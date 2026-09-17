"""Le gel d'un reel : tout ce que la publication enverra, fige sur disque a T-2h.

Meme contrat que `preparation.py` pour les carrousels : une faute de l'ARTICLE
leve `PreparationImpossible` (le runner l'exclut), une panne de la MACHINE
remonte telle quelle (rien n'est exclu, le gel est retente au passage suivant),
et une ATTENTE (`PreparationDifferee` : pause ou budget de yt-dlp) laisse le
creneau en l'etat, sans notification.

La video, dans cet ordre (decision du 2026-09-17) :
1. la `media_url` de l'API quand Meta la fournit ;
2. sinon yt-dlp sur le permalink Instagram -- les reels d'artistes, dont Meta
   omet la video (musique protegee). Aussi en repli d'une `media_url` EXPIREE.

Elle est obtenue ICI et jamais a l'ingestion : les liens du CDN Instagram sont
signes et expirent en quelques jours, et yt-dlp ne doit tourner qu'au gel. A
T-2h, une video morte est une exclusion propre, visible au dashboard.

Ordre des fichiers figes : la video, puis la cover (`cover_url` chez Meta).
"""

import re
import secrets
import subprocess
import time
from pathlib import Path

import httpx

from app.config import settings
from app.content.media import VIDEO, est_lien_instagram, video_recuperable, videos_exploitables
from app.content.render import _USER_AGENT, render_post
from app.content.video import FORMAT_REEL, AssemblageIndisponible, assembler_reel, exiger_outils, sonder
from app.publish.images import ImageInexploitable, en_jpeg
from app.publish.preparation import PreparationDifferee, PreparationImpossible

MO = 1024 * 1024
DELAI_TELECHARGEMENT_S = 120.0
# Delai TOTAL : celui d'httpx borne chaque lecture, pas un flux qui goutte.
DELAI_TOTAL_S = 300.0
# Statuts qui disent « plus tard », pas « jamais » : ni exclusion, ni repli yt-dlp.
STATUTS_TRANSITOIRES = (408, 429)
TYPES_BINAIRES = ("application/octet-stream", "binary/octet-stream")
# ffmpeg a court de ressources : la faute est a la machine, pas a la video.
_PANNE_MACHINE = re.compile(r"No space left on device|Cannot allocate memory", re.IGNORECASE)
CODECS_VIDEO = ("h264", "hevc")
CADENCE_MIN, CADENCE_MAX = 23.0, 60.0
ECHANTILLONNAGE_MAX = 48_000
API = "api"
YTDLP = "ytdlp"


class LienExpire(PreparationImpossible):
    """La `media_url` repond 4xx : lien signe expire, ou media retire."""


def source_de_la_video(post, article) -> tuple[str, str] | None:
    for slide in post.slides[1:]:
        if slide.get("kind") == "media" and slide.get("type") == VIDEO and slide.get("url"):
            return API, slide["url"]
    if article is None:
        return None
    videos = videos_exploitables(article.media, int(settings.publication_reel_duree_max_s * 1000))
    if videos:
        return API, videos[0]["url"]
    if video_recuperable(article.media, article.url):
        return YTDLP, article.url
    return None


def _refuser_hors_https(url) -> None:
    if httpx.URL(str(url)).scheme != "https":
        raise PreparationImpossible(f"lien video hors https refuse : {url}")


def telecharger_video(
    url: str, plafond: int, *, client: httpx.Client | None = None, horloge=time.monotonic
) -> bytes:
    _refuser_hors_https(url)
    debut = horloge()
    propre = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=DELAI_TELECHARGEMENT_S)
    try:
        with client.stream("GET", url, headers={"User-Agent": _USER_AGENT}) as reponse:
            # Une redirection vers http:// ne passe pas, meme si elle aboutit.
            for etape in [*reponse.history, reponse]:
                _refuser_hors_https(etape.url)
            if reponse.status_code in STATUTS_TRANSITOIRES:
                raise PreparationDifferee(
                    f"video momentanement indisponible (HTTP {reponse.status_code}), retentee au passage suivant"
                )
            if 400 <= reponse.status_code < 500:
                raise LienExpire(
                    f"video indisponible (HTTP {reponse.status_code}) : lien expire ou retire"
                )
            # 5xx : HTTPStatusError remonte, panne systemique retentee.
            reponse.raise_for_status()
            type_ = reponse.headers.get("content-type", "").split(";")[0].strip().lower()
            if type_ == "text/html":
                # Portail captif (Wi-Fi d'hotel) ou page d'erreur d'un proxy : le
                # reseau de la machine, pas l'article.
                raise PreparationDifferee(f"le lien n'est pas une video ({type_}), retente au passage suivant")
            if type_ and not type_.startswith("video/") and type_ not in TYPES_BINAIRES:
                raise PreparationImpossible(f"le lien n'est pas une video ({type_})")
            octets = bytearray()
            for morceau in reponse.iter_bytes():
                octets.extend(morceau)
                # En cours de lecture : Content-Length peut manquer ou mentir.
                if len(octets) > plafond:
                    raise PreparationImpossible(f"video source trop lourde : plus de {plafond / MO:.0f} Mo")
                if horloge() - debut > DELAI_TOTAL_S:
                    raise PreparationDifferee(
                        f"telechargement abandonne : delai de {DELAI_TOTAL_S:.0f} s depasse"
                    )
            return bytes(octets)
    finally:
        if propre:
            client.close()


def verifier_source(infos: dict) -> None:
    duree = infos["duration"]
    if duree < settings.publication_reel_duree_min_s:
        raise PreparationImpossible(
            f"video trop courte : {duree:.1f} s, minimum {settings.publication_reel_duree_min_s:.0f} s"
        )
    if duree > settings.publication_reel_duree_max_s:
        # Refusee, jamais coupee : couper le contenu d'un artiste en trahirait le sens.
        raise PreparationImpossible(
            f"video trop longue : {duree:.0f} s, maximum {settings.publication_reel_duree_max_s:.0f} s"
        )


def verifier_sortie(infos: dict, poids: int) -> None:
    """Les specs Meta du 2026-09-17, controlees AVANT la reclamation : un refus
    decouvert chez Meta ferait passer le creneau en ECHEC sans retour."""
    problemes = []
    if (infos["width"], infos["height"]) != FORMAT_REEL:
        problemes.append(f"dimensions {infos['width']}x{infos['height']}")
    if infos.get("video_codec") not in CODECS_VIDEO:
        problemes.append(f"codec video {infos.get('video_codec')}")
    if infos.get("audio_codec") != "aac":
        problemes.append(f"codec audio {infos.get('audio_codec')}")
    if (infos.get("sample_rate") or 0) > ECHANTILLONNAGE_MAX:
        problemes.append(f"echantillonnage {infos.get('sample_rate')} Hz")
    if not CADENCE_MIN <= infos.get("fps", 0.0) <= CADENCE_MAX:
        problemes.append(f"cadence {infos.get('fps')} i/s")
    if infos["duration"] < settings.publication_reel_duree_min_s:
        problemes.append(f"duree {infos['duration']:.1f} s")
    if poids > settings.publication_reel_max_mo * MO:
        problemes.append(f"poids {poids / MO:.0f} Mo")
    if problemes:
        raise PreparationImpossible("reel hors specifications Meta : " + ", ".join(problemes))


def _panne_machine(exc: AssemblageIndisponible) -> bool:
    code = getattr(exc, "returncode", None)
    if code is not None and code < 0:
        return True
    return bool(_PANNE_MACHINE.search(f"{getattr(exc, 'stderr', '')}\n{exc}"))


def _video_source(origine, url, article, dossier, plafond, telecharger, recuperer_ytdlp) -> bytes:
    if origine == API:
        try:
            return telecharger(url, plafond)
        except LienExpire:
            # Repli sur la page du post : yt-dlp sait souvent recuperer ce que le
            # lien signe ne donne plus. Hors Instagram, ou sans yt-dlp, la faute reste.
            permalink = getattr(article, "url", None)
            if not est_lien_instagram(permalink):
                raise
            if recuperer_ytdlp is None:
                # Le repli existe, yt-dlp est seulement coupe (.env) : on attend.
                raise PreparationDifferee(
                    "lien video expire, repli yt-dlp desactive (PUBLICATION_REEL_YTDLP)"
                ) from None
            url = permalink
    elif recuperer_ytdlp is None:
        # Pas une faute de l'article : yt-dlp a ete coupe (.env) apres la
        # programmation, il peut etre reactive.
        raise PreparationDifferee("video a recuperer par yt-dlp, desactive (PUBLICATION_REEL_YTDLP)")
    # PreparationImpossible (video supprimee), PreparationDifferee (pause, budget)
    # et YtdlpIndisponible (panne) remontent telles quelles.
    return recuperer_ytdlp(url, dossier, plafond)


def preparer_reel(
    post,
    article,
    dossier: Path,
    *,
    rendre=render_post,
    telecharger=telecharger_video,
    recuperer_ytdlp=None,
    sonder=sonder,
    assembler=assembler_reel,
    verifier_outils=exiger_outils,
) -> list[str]:
    if post.template != "kinda_reel":
        raise PreparationImpossible(f"le post est au gabarit {post.template}, pas kinda_reel")
    if not getattr(post, "credit", None):
        raise PreparationImpossible("credit du compte source manquant : un reel ne part jamais sans")
    origine = source_de_la_video(post, article)
    if origine is None:
        raise PreparationImpossible("aucune video exploitable")
    # AssemblageIndisponible ICI = ffmpeg ou ffprobe absent : panne de la
    # machine, elle remonte telle quelle et n'exclut rien.
    verifier_outils()

    dossier.mkdir(parents=True, exist_ok=True)
    for ancien in [*dossier.glob("*.mp4"), *dossier.glob("*.jpg")]:
        ancien.unlink()

    # La cover AVANT la video : une cover fautive ne doit pas consommer le
    # telechargement yt-dlp du passage. RenduIndisponible (Chromium absent) remonte.
    rendus = rendre(post.slides, "kinda_reel")
    if not rendus:
        raise PreparationImpossible("la cover n'a pas pu etre rendue")
    if rendus[0].overflow:
        raise PreparationImpossible("accroche trop longue : la cover deborde")

    plafond = settings.publication_reel_source_max_mo * MO
    source = _video_source(*origine, article, dossier, plafond, telecharger, recuperer_ytdlp)
    try:
        try:
            infos_source = sonder(source)
            verifier_source(infos_source)
        except (KeyError, ValueError) as exc:
            # Un flux que ffprobe decrit sans duree, ou avec une valeur absurde :
            # propre a cette video. L'exclure evite de rebruler le budget yt-dlp.
            raise PreparationImpossible(f"video source inexploitable a la sonde : {exc!r}") from exc
        # Reencodage complet (libx264 + aac) : une source VP9 de yt-dlp en sort en H.264.
        reel = assembler(rendus[0].png, source)
        infos = sonder(reel)
    except AssemblageIndisponible as exc:
        # Les outils sont presents (verifie plus haut). Un binaire tue par un
        # signal, ou a court de disque ou de memoire : panne de la machine, qui
        # remonte telle quelle. Tout autre echec vient de l'entree.
        if _panne_machine(exc):
            raise
        raise PreparationImpossible(f"video inexploitable : {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        # Le plus souvent une machine chargee : retente au passage suivant.
        raise PreparationDifferee(f"assemblage abandonne : delai de {exc.timeout:.0f} s depasse") from exc
    verifier_sortie(infos, len(reel))

    try:
        cover = en_jpeg(rendus[0].png)
    except ImageInexploitable as exc:
        raise PreparationImpossible(f"cover inexploitable : {exc}") from exc

    # Prefixe aleatoire : les fichiers seront publics quelques minutes.
    prefixe = secrets.token_urlsafe(12)
    noms = [f"{prefixe}_reel.mp4", f"{prefixe}_cover.jpg"]
    (dossier / noms[0]).write_bytes(reel)
    (dossier / noms[1]).write_bytes(cover)
    return noms
