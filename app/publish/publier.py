"""Publier un dossier fige : l'heberger, puis passer par l'API Instagram.

Le tunnel reste ouvert jusqu'a la FIN : Meta telecharge les images pendant la
creation du conteneur, et `media_publish` doit suivre avant la fermeture.
"""

from pathlib import Path

from app.config import settings
from app.publish import hosting
from app.publish.instagram import ClientInstagram, ErreurInstagram
from app.publish.jeton import charger


def client_par_defaut() -> ClientInstagram:
    if not settings.meta_ig_user_id:
        raise ErreurInstagram("META_IG_USER_ID manquant dans .env")
    return ClientInstagram(charger(settings.meta_token_path).access_token, settings.meta_ig_user_id)


def _verifier_fichiers(dossier: Path, noms: list[str]) -> None:
    if not noms:
        raise ErreurInstagram("rien a publier : aucun fichier fige")
    manquants = [nom for nom in noms if not (dossier / nom).exists()]
    if manquants:
        raise ErreurInstagram(f"fichiers figes introuvables : {', '.join(manquants)}")


def publier_fichiers(
    dossier: Path,
    noms: list[str],
    legende: str,
    publier_vraiment: bool,
    *,
    client=None,
    heberger=None,
) -> str:
    _verifier_fichiers(dossier, noms)
    client = client or client_par_defaut()
    heberger = heberger or hosting.heberger
    with heberger(dossier, noms[0]) as base:
        conteneur = client.preparer_carrousel([f"{base}/{nom}" for nom in noms], legende)
        if not publier_vraiment:
            return conteneur
        return client.publier(conteneur)


def publier_reel(
    dossier: Path,
    noms: list[str],
    legende: str,
    publier_vraiment: bool,
    *,
    client=None,
    heberger=None,
) -> str:
    """Un reel fige : la video puis la cover, dans cet ordre (preparation_reel)."""
    if len(noms) != 2:
        raise ErreurInstagram(f"un reel fige compte deux fichiers (video, cover), pas {len(noms)}")
    _verifier_fichiers(dossier, noms)
    video, cover = noms
    client = client or client_par_defaut()
    heberger = heberger or hosting.heberger
    # Temoin = la cover : `attendre_joignable` la telecharge en entier pour
    # verifier le tunnel, inutile d'y faire transiter la video.
    with heberger(dossier, cover) as base:
        conteneur = client.preparer_reel(
            f"{base}/{video}",
            f"{base}/{cover}",
            legende,
            share_to_feed=settings.publication_reel_share_to_feed,
        )
        if not publier_vraiment:
            return conteneur
        return client.publier(conteneur)
