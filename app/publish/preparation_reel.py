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
from types import SimpleNamespace

import httpx

from app.config import settings
from app.content.media import CLES, VIDEO, lien_de_reel, video_recuperable, videos_exploitables
from app.content.render import _USER_AGENT, render_post
from app.content.teinte import palette_du_post
from app.content.urls import url_propre
from app.content.video import FORMAT_REEL, AssemblageIndisponible, assembler_reel, exiger_outils, sonder
from app.content.visuels import ORIGINE_ARTICLE, origine_interdite, urls_interdites
from app.publish.images import ImageInexploitable, en_jpeg
from app.publish.preparation import PreparationDifferee, PreparationImpossible, verifier_fond

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


def _actif_publiable(choisi: dict, article, interdites: set[str]) -> bool:
    """La policy des visuels est RE-VERIFIEE a la relecture de `post.assets` : un
    actif pose quand la source etait autorisee ne doit pas partir si elle est
    passee en compte media Instagram depuis, ni si son URL est devenue interdite."""
    if choisi.get("origine") == ORIGINE_ARTICLE and origine_interdite(
        getattr(article, "source", None)
    ):
        return False
    return not ({choisi.get("url"), choisi.get("preview_url")} & interdites)


def _porteur_video(post, article):
    """Ce qui porte la video du reel : l'actif video choisi a la composition (le
    post d'un artiste cite), sinon l'article -- jamais un compte media Instagram."""
    interdites = urls_interdites(article)
    for choisi in getattr(post, "assets", None) or []:
        if choisi.get("type") == VIDEO and _actif_publiable(choisi, article, interdites):
            return SimpleNamespace(
                url=choisi.get("permalink"),
                media=[{cle: choisi.get(cle) for cle in CLES}],
            )
    if article is None or origine_interdite(getattr(article, "source", None)):
        return None
    return article


def source_de_la_video(post, article) -> tuple[str, str] | None:
    """(origine, URL) de la video du reel. L'URL est NETTOYEE de ses entites HTML :
    telle quelle, un `&amp;` d'une URL signee du CDN Instagram la fait refuser."""
    interdites = urls_interdites(article)
    for slide in post.slides[1:]:
        if (
            slide.get("kind") == "media"
            and slide.get("type") == VIDEO
            and slide.get("url")
            and slide["url"] not in interdites
        ):
            return API, url_propre(slide["url"])
    porteur = _porteur_video(post, article)
    if porteur is None:
        return None
    videos = videos_exploitables(porteur.media, int(settings.publication_reel_duree_max_s * 1000))
    if videos:
        return API, url_propre(videos[0]["url"])
    if video_recuperable(porteur.media, porteur.url):
        return YTDLP, url_propre(porteur.url)
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


class ReelInadapte(PreparationImpossible):
    """La VIDEO ne convient pas a un reel -- l'article, lui, reste publiable.

    Le 2026-09-21, l'article 18951 (Billboard, « The Weeknd Debuts New Song
    Sampling "Neon Genesis Evangelion" Theme ») a ete exclu DEFINITIVEMENT de la
    file parce qu'une video de 190 s depassait la limite. Cet article n'avait
    qu'une photo : la video etait empruntee a un compte d'artiste suivi. On a
    donc brule une actu a cause d'un visuel pret.

    D'ou une exception distincte, sous-classe de `PreparationImpossible` pour
    que tout ce qui l'attrape deja continue de la traiter comme un refus de gel
    (meme motif que `ImageEcartee` sous `ImageInexploitable`). Le gel, lui, la
    reconnait et LIBERE le creneau au lieu d'exclure.

    Elle porte la duree MESUREE et la video concernee : c'est l'exclusion qui
    empechait jusqu'ici de recomposer le meme reel a chaque creneau (Claude
    paye a chaque fois). En ecrivant ce qu'on vient d'apprendre dans
    `articles.media`, `videos_exploitables` l'ecarte des reels pour toujours --
    sans fermer les carrousels a l'article.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        duree_ms: int | None = None,
        article_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.duree_ms = duree_ms
        self.article_id = article_id


def verifier_source(infos: dict, *, url: str | None = None, article_id: int | None = None) -> None:
    duree = infos["duration"]
    hors_bornes = (
        f"video trop courte : {duree:.1f} s, minimum {settings.publication_reel_duree_min_s:.0f} s"
        if duree < settings.publication_reel_duree_min_s
        # Refusee, jamais coupee : couper le contenu d'un artiste en trahirait le sens.
        else f"video trop longue : {duree:.0f} s, maximum {settings.publication_reel_duree_max_s:.0f} s"
        if duree > settings.publication_reel_duree_max_s
        else None
    )
    if hors_bornes is not None:
        raise ReelInadapte(hors_bornes, url=url, duree_ms=int(duree * 1000), article_id=article_id)


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


def _repli_ytdlp_possible(article, permalink) -> bool:
    """yt-dlp saurait-il retrouver la video dont le lien signe a expire ? Un
    permalink de reel, ou un post dont le SEUL media est une video (son URL
    expiree ignoree : c'est la meme question que `video_recuperable`). Un
    carrousel `/p/` a plusieurs medias, non : yt-dlp rendrait une liste."""
    if lien_de_reel(permalink):
        return True
    medias = [{**m, "url": None} for m in getattr(article, "media", None) or []]
    return video_recuperable(medias, permalink)


def _video_source(origine, url, article, dossier, plafond, telecharger, recuperer_ytdlp) -> bytes:
    if origine == API:
        try:
            return telecharger(url, plafond)
        except LienExpire:
            # Repli sur la page du post : yt-dlp sait souvent recuperer ce que le
            # lien signe ne donne plus. Hors d'un reel ou d'un post a video seule
            # (carrousel /p/, autre site), aucun repli : la faute reste.
            permalink = getattr(article, "url", None)
            if not _repli_ytdlp_possible(article, permalink):
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
    telecharger_image=None,
    agrandir=None,
    binaire_disponible=None,
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
    if origine[0] == YTDLP and recuperer_ytdlp is not None:
        # Pause ou budget deja connus : inutile d'allumer Chromium pour la cover.
        attente = getattr(recuperer_ytdlp, "attente", None)
        attente = attente() if attente is not None else None
        if attente is not None:
            raise attente

    dossier.mkdir(parents=True, exist_ok=True)
    for ancien in [*dossier.glob("*.mp4"), *dossier.glob("*.jpg")]:
        ancien.unlink()

    # Le fond AVANT le rendu : la cover d'un reel est une image rendue par
    # Chromium comme celle d'un carrousel. Sans fond verifie, elle partirait sur
    # l'image de secours du gabarit -- et c'est elle que Meta affiche en
    # couverture du reel (`cover_url`). `telecharger` ici est celui de la
    # VIDEO (signature `(url, plafond)`), jamais celui du fond -- donc les
    # trois crochets de la Tache 6 sont PROPRES a `preparer_reel`, passes
    # seulement quand injectes (les valeurs par defaut de `verifier_fond`
    # sinon).
    kwargs_fond = {
        cle: valeur
        for cle, valeur in (
            ("telecharger", telecharger_image),
            ("agrandir", agrandir),
            ("binaire_disponible", binaire_disponible),
        )
        if valeur is not None
    }
    verifier_fond(post, article, **kwargs_fond)

    # La cover AVANT la video : une cover fautive ne doit pas consommer le
    # telechargement yt-dlp du passage. RenduIndisponible (Chromium absent) remonte.
    rendus = rendre(post.slides, "kinda_reel", palette_du_post(post))
    if not rendus:
        raise PreparationImpossible("la cover n'a pas pu etre rendue")
    if rendus[0].overflow:
        raise PreparationImpossible("accroche trop longue : la cover deborde")

    plafond = settings.publication_reel_source_max_mo * MO
    # Le repli yt-dlp d'un lien expire vise le permalink du PORTEUR : le post de
    # l'artiste quand la video vient de lui, pas la page de l'article.
    porteur = _porteur_video(post, article) or article
    source = _video_source(*origine, porteur, dossier, plafond, telecharger, recuperer_ytdlp)
    try:
        try:
            infos_source = sonder(source)
            # L'URL de `origine` est celle du MEDIA quand elle vient de l'API ;
            # pour un repli yt-dlp c'est un permalink, qui ne correspond a aucune
            # entree de `media` -- le gel s'en apercoit et exclut alors, comme avant.
            verifier_source(
                infos_source, url=origine[1], article_id=getattr(porteur, "id", None)
            )
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
