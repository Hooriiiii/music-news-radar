"""Quels visuels Kinda a le droit d'utiliser, et d'ou ils viennent.

Decision de l'utilisateur du 2026-09-17 : l'INFO peut venir d'un compte d'actu,
ses images et videos JAMAIS (watermarks : reposter le visuel d'un media
concurrent est un plagiat visible). Les photos de presse des flux RSS
(og:image, media_content) restent autorisees.
"""

from app.models import SourceCategorie, SourceType


def origine_interdite(source) -> bool:
    """Vrai pour un compte Instagram de categorie media, et lui seul. `getattr` :
    des appelants passent des sources partielles (SimpleNamespace sans categorie)."""
    return (
        source is not None
        and getattr(source, "type", None) == SourceType.INSTAGRAM
        and getattr(source, "categorie", None) == SourceCategorie.MEDIA
    )
