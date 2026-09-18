"""Le gel : tout ce qu'une publication va envoyer, fige sur disque avant l'heure.

A T-2h, on rend la cover, on telecharge les photos de l'actu et on les met aux
regles de Meta. Deux raisons de le faire en avance plutot qu'a l'heure : une
panne laisse le temps de reagir (remplacer l'article sur une cover qui deborde,
retenter sur un Chromium absent), et l'heure de publication ne depend plus de la lenteur d'un telechargement.

Ordre du carrousel : la cover d'abord, puis jusqu'a 9 medias de l'actu, dans
l'ordre -- photos en JPEG cadrees a 1080x1350 comme la cover (Instagram impose le
ratio du PREMIER element a tous les autres, une photo paysage revenait donc
coupee n'importe comment), VIDEOS en elements video 1080x1350 (meme cadrage sur
fond floute que le reel). Premier post reel (Charlotte de Witte,
article 17839) : 5 videos, et un carrousel parti avec la cover seule.

Six regles :
- JAMAIS un post reduit a sa COVER SEULE : un carrousel porte au moins deux
  elements, sinon le gel est impossible et l'article sort de la file (2026-09-18,
  article 17946 « Noisia » : une image, ecartee, et un post vide -- « la c'est
  n'importe quoi ! »). Sauf si les visuels existent et que seule leur
  RECUPERATION a echoue pour une raison passagere : on differe, on n'exclut pas ;
- la photo qui sert de FOND a la cover n'est pas repetee ensuite (une video dont
  c'est la vignette reste, sauf si elle retombe sur cette vignette) -- SAUF si
  c'est le seul visuel : elle fait alors le fond ET la slide (la cover la montre
  sous l'accroche, la slide la montre nette) ;
- une image que le cadrage 4:5 abimerait (trop petite, ou trop eloignee du
  format : `app.publish.images`) est simplement SAUTEE, jamais une erreur -- la
  raison est journalisee, et rappelee dans le message si le carrousel finit vide ;
- une video inutilisable (trop longue, telechargement ou sonde en echec, sortie
  hors specs) retombe sur sa vignette en photo : un media ne fait jamais echouer
  le gel ;
- les visuels d'un compte Instagram de categorie `media` ne sont jamais repris
  (watermarks, politique dans `app.content.visuels`) : ni en slide, ni en fond de
  cover -- un tel fond rend le gel impossible.

Une AFFICHE, elle, reste une slide honnete : elle n'est refusee qu'en FOND de
cover (`app.content.illustration`), jamais dans le carrousel -- c'est le visuel
de l'actu.
"""

import logging
import secrets
from pathlib import Path

from app.config import settings
from app.content import video as video_mod
from app.content.export import echec_transitoire, telecharger as telecharger_media
from app.content.media import DUREE_MAX_SLIDE_MS, PHOTO, VIDEO
from app.content.urls import url_propre
from app.content.render import render_post
from app.content.teinte import palette_du_post
from app.content.visuels import (
    FOND_KINDA,
    ORIGINE_ARTICLE,
    fond_de_cover,
    lire_fichier_cache,
    origine_interdite,
    urls_interdites,
    visuels_autorises,
)
from app.publish.images import ImageEcartee, ImageInexploitable, cadrer_carrousel

logger = logging.getLogger(__name__)

CARROUSEL_MAX = 10
MO = 1024 * 1024


class PreparationImpossible(RuntimeError):
    """Le post ne peut pas etre fige. Message affichable."""


class PreparationDifferee(RuntimeError):
    """Le gel ne peut pas se faire MAINTENANT, sans que l'article ni la machine
    soient en defaut (pause de yt-dlp, un telechargement par passage deja fait).
    Le runner inscrit la raison sur le creneau, sans exclure ni notifier : le gel
    est retente au passage suivant. Message affichable."""


def _cadrer(octets: bytes) -> bytes:
    """Une image du carrousel au format publie, seuils lus dans la configuration."""
    return cadrer_carrousel(
        octets,
        upscale_max=settings.publication_crop_upscale_max,
        perte_max=settings.publication_crop_perte_max,
    )


def _media_d_actif(actif_: dict) -> dict:
    """Un actif avec copie locale part en PHOTO depuis cette copie (l'URL du CDN
    Instagram aura expire) ; les autres gardent leur forme."""
    if actif_.get("fichier"):
        return {**actif_, "type": PHOTO, "url": actif_["fichier"]}
    return actif_


def medias_du_carrousel(post, article) -> list[dict]:
    """Photos et videos a publier apres la cover : slides, puis actifs du post
    (ou, pour un post anterieur aux actifs, medias autorises de l'article), sans
    doublon, sans le media qui sert de fond a la cover, jamais un visuel interdit."""
    interdites = urls_interdites(article)
    candidats = [
        slide for slide in post.slides[1:]
        if slide.get("kind") == "media" and slide.get("type") in (PHOTO, VIDEO)
        and not ({slide.get("url"), slide.get("preview_url")} & interdites)
    ]
    actifs = getattr(post, "assets", None)
    if actifs is not None:
        # La policy est RE-VERIFIEE a la relecture : un actif pose quand la source
        # etait autorisee ne part pas si elle est passee en compte media depuis,
        # ni si son URL est devenue interdite.
        interdite_maintenant = origine_interdite(getattr(article, "source", None))
        candidats += [
            _media_d_actif(a)
            for a in actifs
            if a.get("type") in (PHOTO, VIDEO)
            and not (a.get("origine") == ORIGINE_ARTICLE and interdite_maintenant)
            and not ({a.get("url"), a.get("preview_url")} & interdites)
        ]
    else:
        candidats += [m for m in visuels_autorises(article) if m.get("type") in (PHOTO, VIDEO)]
    fond = fond_de_cover(post)
    vues: set[str] = set()
    medias = []
    for media in candidats:
        cle = media.get("url") or media.get("preview_url")
        if not cle or cle in vues:
            continue
        vues.add(cle)
        # La photo du fond n'est pas repetee. Une video dont la vignette sert de
        # fond reste : un clip n'est pas le meme asset que son image fixe (elle
        # n'est ecartee que si elle retombe sur cette vignette, au gel).
        if fond and media.get("type") == PHOTO and media.get("url") == fond:
            continue
        medias.append(media)
    return medias


def verifier_fond(post, article) -> str:
    """Le fond de la cover, verifie, ou une erreur explicite. Commun aux deux
    gabarits : la cover d'un reel est rendue par Chromium exactement comme celle
    d'un carrousel, donc les trois memes fautes la guettent.

    Un `str(None)` ou un vide sont lus comme absents (`fond_de_cover`) : sinon la
    garde passe et le rendu retombe EN SILENCE sur l'image de secours du gabarit.
    """
    fond = fond_de_cover(post)
    if fond == FOND_KINDA:
        # L'aplat Kinda est un fond VALABLE, choisi a la composition parce que
        # tout le reste etait une affiche (2026-09-18). Le distinguer d'un fond
        # absent est tout l'interet de la sentinelle : sans ce cas, le gel
        # refuserait le post a chaque passage jusqu'a MANQUE.
        return fond
    if fond and fond in urls_interdites(article):
        # Brouillon compose avant la regle du 2026-09-17 : jamais publie.
        raise PreparationImpossible("fond de cover tire d'un compte media Instagram : visuel interdit")
    if not fond:
        # Sans fond, la cover part sur l'image de secours du gabarit : lisible a
        # l'ecran d'un editeur, pas publiable telle quelle. Une file automatique
        # ne doit jamais publier ca -- c'est le signe d'un post compose sans
        # visuel autorise.
        raise PreparationImpossible("aucun fond de cover : le post n'a pas de visuel")
    if fond.startswith("file://") and lire_fichier_cache(fond) is None:
        # La copie locale a ete purgee (cache vide, dossier applicatif efface) :
        # le rendu retomberait en silence sur l'image de secours du gabarit. Un
        # REPORT, jamais une exclusion -- l'article n'y est pour rien, et rien ne
        # part sur le fond de secours.
        raise PreparationDifferee("la copie locale du fond de cover a disparu du cache")
    return fond


def _octets_image(url: str, telecharger) -> tuple[bytes | None, str | None]:
    """Le contenu de l'image, et la raison de l'echec le cas echeant.

    Une copie locale est lue dans le cache, jamais telechargee ; un `file://`
    hors du cache n'est jamais lu. L'URL est nettoyee de ses entites HTML : une
    ligne posee avant le nettoyage a l'ingestion porte encore `&amp;`, que le CDN
    Instagram refuse.

    La raison est rendue parce qu'un carrousel vide ne se traite pas pareil selon
    qu'elle est PASSAGERE (on retente) ou definitive (on exclut)."""
    url = url_propre(url) or url
    octets = lire_fichier_cache(url)
    if octets is not None or url.startswith("file://"):
        return octets, None if octets is not None else "copie locale absente"
    return telecharger(url)


def _video_de_carrousel(media: dict, telecharger_video, sonder, transcoder) -> bytes | None:
    """Le MP4 d'un element video, ou None (l'appelant prend alors la vignette)."""
    url, duree_ms = url_propre(media.get("url")), media.get("duration_ms")
    if not url or (duree_ms and duree_ms > DUREE_MAX_SLIDE_MS):
        return None
    duree_max_s = DUREE_MAX_SLIDE_MS / 1000
    try:
        source = telecharger_video(url, settings.publication_reel_source_max_mo * MO)
        duree = sonder(source)["duration"]
        if not settings.publication_reel_duree_min_s <= duree <= duree_max_s:
            logger.info("video de carrousel ecartee (%.1f s) : %s", duree, url)
            return None
        sortie = transcoder(source)
        infos = sonder(sortie)
    except Exception as exc:  # noqa: BLE001 -- un media ne fait jamais echouer le gel
        logger.warning("video de carrousel inutilisable, vignette a la place : %s", type(exc).__name__)
        return None
    conforme = (
        (infos["width"], infos["height"]) == video_mod.FORMAT_CARROUSEL
        and infos.get("video_codec") == "h264"
        and infos.get("audio_codec") == "aac"
        and len(sortie) <= settings.publication_reel_max_mo * MO
    )
    return sortie if conforme else None


def preparer_fichiers(
    post,
    article,
    dossier: Path,
    *,
    rendre=render_post,
    telecharger=telecharger_media,
    telecharger_video=None,
    sonder=None,
    transcoder=None,
) -> list[str]:
    if telecharger_video is None:
        # Import tardif : preparation_reel importe ce module.
        from app.publish.preparation_reel import telecharger_video
    sonder = sonder or video_mod.sonder
    transcoder = transcoder or video_mod.transcoder_carrousel
    dossier.mkdir(parents=True, exist_ok=True)
    for ancien in [*dossier.glob("*.jpg"), *dossier.glob("*.mp4")]:
        ancien.unlink()

    fond = verifier_fond(post, article)
    panne_transport = False
    # Les raisons d'ecartement, gardees pour le message d'exclusion : sinon un
    # article sortirait de la file sur un « aucun visuel publiable » muet.
    ecartees: list[str] = []

    # RenduIndisponible (Chromium absent) remonte telle quelle : c'est une panne
    # systemique, pas un defaut de l'article -- le runner ne doit pas l'exclure.
    rendus = rendre(post.slides, "kinda_post", palette_du_post(post))
    if not rendus:
        raise PreparationImpossible("la cover n'a pas pu etre rendue")
    if rendus[0].overflow:
        raise PreparationImpossible("accroche trop longue : la cover deborde")
    try:
        elements = [("jpg", _cadrer(rendus[0].png))]
    except ImageInexploitable as exc:
        raise PreparationImpossible(f"cover inexploitable : {exc}") from exc

    for media in medias_du_carrousel(post, article):
        if len(elements) >= CARROUSEL_MAX:
            break
        if media.get("type") == VIDEO:
            mp4 = _video_de_carrousel(media, telecharger_video, sonder, transcoder)
            if mp4 is not None:
                elements.append(("mp4", mp4))
                continue
            url = media.get("preview_url")
            if url and url_propre(url) == fond:
                continue
        else:
            url = media.get("url")
        if not url:
            continue
        octets, raison = _octets_image(url, telecharger)
        if octets is None:
            panne_transport = panne_transport or echec_transitoire(raison)
            continue
        try:
            elements.append(("jpg", _cadrer(octets)))
        except ImageEcartee as exc:
            ecartees.append(str(exc))
            logger.info("visuel ecarte du carrousel (%s) : %s", exc, url)
            continue
        except ImageInexploitable as exc:
            logger.info("visuel inexploitable (%s) : %s", exc, url)
            continue

    if len(elements) == 1 and fond != FOND_KINDA:
        # UNE SEULE IMAGE, et c'est celle de la cover : la regle « ne pas repeter
        # l'image du fond » vaut quand il existe un AUTRE visuel -- seule, elle
        # vaut mieux que rien. La cover la montre sous l'accroche, la slide la
        # montre nette. (2026-09-18, post Noisia : « il n'y a pas de slide ».)
        octets, raison = _octets_image(fond, telecharger)
        if octets is None:
            panne_transport = panne_transport or echec_transitoire(raison)
        else:
            try:
                elements.append(("jpg", _cadrer(octets)))
            except ImageEcartee as exc:
                ecartees.append(str(exc))
                logger.info("fond de cover ecarte du carrousel (%s) : %s", exc, fond)
            except ImageInexploitable as exc:
                logger.info("fond de cover inexploitable (%s) : %s", exc, fond)

    if len(elements) < 2 and panne_transport:
        # Coupure, 429, 5xx : les visuels EXISTENT, seule leur recuperation a
        # echoue. Un article exclu ne revient jamais dans la file -- on retente
        # au passage suivant, comme le choix des visuels le fait deja.
        raise PreparationDifferee(
            "visuels du carrousel inaccessibles (reseau) : nouvelle tentative au prochain passage"
        )
    if len(elements) < 2:
        # JAMAIS un post reduit a sa cover (2026-09-18, article 17946 « Noisia
        # Reunite After Seven-Year Hiatus » : une image, ecartee, et un post
        # vide -- « la c'est n'importe quoi ! »). Une exclusion, donc : le
        # creneau passe a l'article suivant, ce qui vaut mieux qu'un carrousel
        # d'une slide.
        detail = f" ({' ; '.join(dict.fromkeys(ecartees))})" if ecartees else ""
        raise PreparationImpossible(
            f"carrousel reduit a la cover seule : aucun visuel publiable apres elle{detail}"
        )

    # Prefixe aleatoire : les fichiers seront publics quelques minutes, leurs noms
    # ne doivent pas se deviner.
    prefixe = secrets.token_urlsafe(12)
    noms = []
    for rang, (extension, octets) in enumerate(elements, start=1):
        nom = f"{prefixe}_{rang:02d}.{extension}"
        (dossier / nom).write_bytes(octets)
        noms.append(nom)
    return noms
