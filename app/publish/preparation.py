"""Le gel : tout ce qu'une publication va envoyer, fige sur disque avant l'heure.

A T-2h, on rend la cover, on telecharge les photos de l'actu et on les met aux
regles de Meta. Deux raisons de le faire en avance plutot qu'a l'heure : une
panne laisse le temps de reagir (remplacer l'article sur une cover qui deborde,
retenter sur un Chromium absent), et l'heure de publication ne depend plus de la lenteur d'un telechargement.

Ordre du carrousel : la cover d'abord, puis jusqu'a 9 medias de l'actu, dans
l'ordre -- photos en JPEG normalises, VIDEOS en elements video 1080x1350 (meme
cadrage sur fond floute que le reel). Premier post reel (Charlotte de Witte,
article 17839) : 5 videos, et un carrousel parti avec la cover seule.

Trois regles :
- la photo qui sert de FOND a la cover n'est pas repetee ensuite (une video dont
  c'est la vignette reste, sauf si elle retombe sur cette vignette) ;
- une video inutilisable (trop longue, telechargement ou sonde en echec, sortie
  hors specs) retombe sur sa vignette en photo : un media ne fait jamais echouer
  le gel ;
- les visuels d'un compte Instagram de categorie `media` ne sont jamais repris
  (watermarks) : cover seule.
"""

import logging
import secrets
from pathlib import Path

from app.config import settings
from app.content import video as video_mod
from app.content.export import telecharger as telecharger_media
from app.content.media import DUREE_MAX_SLIDE_MS, PHOTO, VIDEO
from app.content.render import render_post
from app.models import SourceCategorie, SourceType
from app.publish.images import ImageInexploitable, normaliser

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


def visuels_interdits(article) -> bool:
    """Compte d'actu Instagram : l'info se reecrit, ses visuels (watermarks) jamais."""
    source = getattr(article, "source", None)
    return (
        source is not None
        and getattr(source, "type", None) == SourceType.INSTAGRAM
        and getattr(source, "categorie", None) == SourceCategorie.MEDIA
    )


def medias_du_carrousel(post, article) -> list[dict]:
    """Photos et videos a publier apres la cover : slides, puis article, sans
    doublon, sans le media qui sert de fond a la cover."""
    if visuels_interdits(article):
        return []
    candidats = [
        slide for slide in post.slides[1:]
        if slide.get("kind") == "media" and slide.get("type") in (PHOTO, VIDEO)
    ]
    if article is not None:
        candidats += [m for m in article.media or [] if m.get("type") in (PHOTO, VIDEO)]
    fond = post.slides[0].get("image_url") if post.slides else None
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


def _video_de_carrousel(media: dict, telecharger_video, sonder, transcoder) -> bytes | None:
    """Le MP4 d'un element video, ou None (l'appelant prend alors la vignette)."""
    url, duree_ms = media.get("url"), media.get("duration_ms")
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
    ratio_min: float | None = None,
    telecharger_video=None,
    sonder=None,
    transcoder=None,
) -> list[str]:
    ratio = settings.publication_ratio_min if ratio_min is None else ratio_min
    if telecharger_video is None:
        # Import tardif : preparation_reel importe ce module.
        from app.publish.preparation_reel import telecharger_video
    sonder = sonder or video_mod.sonder
    transcoder = transcoder or video_mod.transcoder_carrousel
    dossier.mkdir(parents=True, exist_ok=True)
    for ancien in [*dossier.glob("*.jpg"), *dossier.glob("*.mp4")]:
        ancien.unlink()

    # RenduIndisponible (Chromium absent) remonte telle quelle : c'est une panne
    # systemique, pas un defaut de l'article -- le runner ne doit pas l'exclure.
    rendus = rendre(post.slides, "kinda_post")
    if not rendus:
        raise PreparationImpossible("la cover n'a pas pu etre rendue")
    if rendus[0].overflow:
        raise PreparationImpossible("accroche trop longue : la cover deborde")
    try:
        elements = [("jpg", normaliser(rendus[0].png, ratio))]
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
            if url and url == post.slides[0].get("image_url"):
                continue
        else:
            url = media.get("url")
        if not url:
            continue
        octets, _ = telecharger(url)
        if octets is None:
            continue
        try:
            elements.append(("jpg", normaliser(octets, ratio)))
        except ImageInexploitable:
            continue

    # Prefixe aleatoire : les fichiers seront publics quelques minutes, leurs noms
    # ne doivent pas se deviner.
    prefixe = secrets.token_urlsafe(12)
    noms = []
    for rang, (extension, octets) in enumerate(elements, start=1):
        nom = f"{prefixe}_{rang:02d}.{extension}"
        (dossier / nom).write_bytes(octets)
        noms.append(nom)
    return noms
