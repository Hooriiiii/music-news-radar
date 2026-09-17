"""Publier un dossier fige : l'heberger, puis passer par l'API Instagram.

Le tunnel reste ouvert jusqu'a la FIN : Meta telecharge les images pendant la
creation du conteneur, et `media_publish` doit suivre avant la fermeture.

Deux crochets optionnels pour publier a l'heure affichee : `attendre_jusqu_a`
est appele une fois le conteneur pret, `encore_publier` juste avant
`media_publish` (faux : on rend le conteneur sans publier, le mode a change
pendant l'attente). Sans eux, comportement inchange.
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


def _finir(client, conteneur: str, publier_vraiment: bool, attendre_jusqu_a, encore_publier) -> str:
    # Dans le `with heberger(...)` : le tunnel reste ouvert pendant l'attente.
    if not publier_vraiment:
        return conteneur
    if attendre_jusqu_a is not None:
        attendre_jusqu_a()
    if encore_publier is not None and not encore_publier():
        return conteneur
    return client.publier(conteneur)


def publier_fichiers(
    dossier: Path,
    noms: list[str],
    legende: str,
    publier_vraiment: bool,
    *,
    client=None,
    heberger=None,
    attendre_jusqu_a=None,
    encore_publier=None,
) -> str:
    _verifier_fichiers(dossier, noms)
    client = client or client_par_defaut()
    heberger = heberger or hosting.heberger
    with heberger(dossier, noms[0]) as base:
        elements = [
            {"type": "video", "url": f"{base}/{nom}"} if nom.endswith(".mp4") else f"{base}/{nom}"
            for nom in noms
        ]
        conteneur = client.preparer_carrousel(elements, legende)
        return _finir(client, conteneur, publier_vraiment, attendre_jusqu_a, encore_publier)


def publier_reel(
    dossier: Path,
    noms: list[str],
    legende: str,
    publier_vraiment: bool,
    *,
    client=None,
    heberger=None,
    attendre_jusqu_a=None,
    encore_publier=None,
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
        return _finir(client, conteneur, publier_vraiment, attendre_jusqu_a, encore_publier)
