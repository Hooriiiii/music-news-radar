"""Le compte a crediter sous un post repris d'Instagram.

Reposter la video ou la photo d'un artiste sans le nommer serait s'en attribuer
le contenu. La mention `@compte` est le minimum de bonne foi -- et elle notifie
le compte cite, ce qui est voulu. Obligatoire pour un reel (eligibilite, gel).
"""

from app.content.visuels import origine_interdite
from app.models import SourceType
from app.sources.instagram import compte_depuis_url


def credit_video(article) -> str | None:
    source = getattr(article, "source", None)
    # Un compte media ne fournit jamais le visuel : il n'y a rien a lui crediter.
    if source is None or source.type != SourceType.INSTAGRAM or origine_interdite(source):
        return None
    compte = compte_depuis_url(source.url)
    return f"@{compte}" if compte else None
