"""Le gel : tout ce qu'une publication va envoyer, fige sur disque avant l'heure.

A T-2h, on rend la cover, on telecharge les photos de l'actu et on les met aux
regles de Meta. Deux raisons de le faire en avance plutot qu'a l'heure : une
panne laisse le temps de reagir (remplacer l'article sur une cover qui deborde,
retenter sur un Chromium absent), et l'heure de publication ne depend plus de la lenteur d'un telechargement.

Ordre du carrousel : la cover d'abord, puis les photos de l'actu. Pas de video :
la publication ne fait pas de reel.
"""

import secrets
from pathlib import Path

from app.config import settings
from app.content.export import telecharger as telecharger_media
from app.content.render import render_post
from app.publish.images import ImageInexploitable, normaliser

CARROUSEL_MAX = 10


class PreparationImpossible(RuntimeError):
    """Le post ne peut pas etre fige. Message affichable."""


def urls_des_photos(post, article) -> list[str]:
    urls = [
        slide["url"]
        for slide in post.slides[1:]
        if slide.get("kind") == "media" and slide.get("type") == "photo" and slide.get("url")
    ]
    if article is not None:
        urls += [m["url"] for m in article.media or [] if m.get("type") == "photo" and m.get("url")]
    vues: set[str] = set()
    return [url for url in urls if not (url in vues or vues.add(url))]


def preparer_fichiers(
    post,
    article,
    dossier: Path,
    *,
    rendre=render_post,
    telecharger=telecharger_media,
    ratio_min: float | None = None,
) -> list[str]:
    ratio = settings.publication_ratio_min if ratio_min is None else ratio_min
    dossier.mkdir(parents=True, exist_ok=True)
    for ancien in dossier.glob("*.jpg"):
        ancien.unlink()

    # RenduIndisponible (Chromium absent) remonte telle quelle : c'est une panne
    # systemique, pas un defaut de l'article -- le runner ne doit pas l'exclure.
    rendus = rendre(post.slides, "kinda_post")
    if not rendus:
        raise PreparationImpossible("la cover n'a pas pu etre rendue")
    if rendus[0].overflow:
        raise PreparationImpossible("accroche trop longue : la cover deborde")
    try:
        images = [normaliser(rendus[0].png, ratio)]
    except ImageInexploitable as exc:
        raise PreparationImpossible(f"cover inexploitable : {exc}") from exc

    for url in urls_des_photos(post, article):
        if len(images) >= CARROUSEL_MAX:
            break
        octets, _ = telecharger(url)
        if octets is None:
            continue
        try:
            images.append(normaliser(octets, ratio))
        except ImageInexploitable:
            continue

    # Prefixe aleatoire : les fichiers seront publics quelques minutes, leurs noms
    # ne doivent pas se deviner.
    prefixe = secrets.token_urlsafe(12)
    noms = []
    for rang, jpeg in enumerate(images, start=1):
        nom = f"{prefixe}_{rang:02d}.jpg"
        (dossier / nom).write_bytes(jpeg)
        noms.append(nom)
    return noms
