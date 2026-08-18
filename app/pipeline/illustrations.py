"""Illustrer un article avec l'image que son éditeur désigne pour le partage.

57 % des articles arrivaient sans aucun média, et le déficit était concentré sur
les flux RSS — 19 % d'illustration seulement, alors que ce sont eux qui portent
la matière éditoriale (Tsugi, DJ Mag, Mixmag, festivals). Or ces sites publient
tous une balise `og:image` : celle que Facebook, X et Instagram affichent quand
on partage le lien. Mesuré sur 30 articles de 4 sources : **100 % de couverture**.

C'est pour cela qu'on ne fait PAS de recherche web. `og:image` est l'image que
l'éditeur a lui-même choisie pour illustrer CET article ; une recherche ramènerait
au mieux la même, au pire celle d'un autre événement.

Le module ne lève jamais : illustrer est un confort, et un site injoignable ne
doit pas casser le pipeline qui l'appelle.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.media import PHOTO, entree
from app.models import Article

logger = logging.getLogger(__name__)

# ASCII pur : httpx refuse un en-tête accentué AVANT même d'émettre la requête
# (contrainte relevée sur le terrain, cf. CLAUDE.md).
ENTETES = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) KindaBot/1.0"
    )
}

# Deux ordres d'attributs : `property` avant `content` et l'inverse. Une regex
# rigide raterait la moitié des sites -- l'ordre n'est contraint par aucune norme.
_MOTIFS = tuple(
    re.compile(motif % {"cle": cle}, re.IGNORECASE)
    for cle in ("og:image", "twitter:image")
    for motif in (
        r'<meta[^>]+(?:property|name)=["\']%(cle)s["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']%(cle)s["\']',
    )
)

# Au-delà, la page n'est pas une page d'article -- inutile de la charger en
# entier pour y chercher une balise qui vit dans le <head>.
TAILLE_MAX = 2_000_000


# Ces liens ne menent PAS a l'article : Google News redirige vers sa page de
# consentement, qui n'a pas d'og:image. Verifie en vrai sur les 4 sources
# concernees. Une requete y est certaine d'echouer -- autant ne pas la faire, et
# compter le cas a part pour qu'il reste VISIBLE au lieu de se fondre dans les
# echecs reseau.
DOMAINES_SANS_ARTICLE = ("news.google.com",)


@dataclass
class IllustrationStats:
    illustres: int = 0
    echecs: int = 0
    ignores: int = 0


def image_de_partage(url: str, get=httpx.get) -> dict | None:
    """L'entrée média construite depuis `og:image`, ou None.

    Rend une entrée à la forme COMPLÈTE de `app/content/media.py` : les six clés
    toujours présentes, pour qu'une photo trouvée ici se lise exactement comme
    une photo venue de X.
    """
    try:
        reponse = get(
            url, timeout=settings.illustration_timeout,
            follow_redirects=True, headers=ENTETES,
        )
    except Exception as exc:
        logger.debug("Illustration : %s injoignable (%s)", url, type(exc).__name__)
        return None

    if reponse.status_code >= 400:
        return None
    # Ne pas passer une regex sur un PDF ou une image de 8 Mo.
    if "html" not in reponse.headers.get("content-type", ""):
        return None

    for motif in _MOTIFS:
        trouve = motif.search(reponse.text[:TAILLE_MAX])
        if trouve:
            # Résolue sur la page : beaucoup de sites servent une og:image
            # relative, inexploitable telle quelle.
            return entree(PHOTO, urljoin(url, trouve.group(1).strip()))
    return None


def enrichir_articles(
    db: Session, get=httpx.get, limite: int | None = None
) -> IllustrationStats:
    """Illustre les articles PERTINENTS qui n'ont aucun média.

    Le filtre sur la pertinence n'est pas une optimisation cosmétique : un
    article de bruit ne sera jamais posté, lui chercher une image revient à payer
    une requête réseau pour un contenu destiné à la purge. Le seuil est celui du
    digest — la même barre que « mérite d'être vu ».
    """
    stmt = (
        select(Article)
        .where(Article.media.is_(None))
        .where(Article.relevance_score.is_not(None))
        .where(Article.relevance_score >= settings.illustration_min_relevance)
        .where(Article.duplicate_of_id.is_(None))
        .order_by(Article.relevance_score.desc())
    )
    plafond = limite if limite is not None else settings.illustration_max_par_run
    if plafond:
        stmt = stmt.limit(plafond)

    stats = IllustrationStats()
    for article in db.scalars(stmt).all():
        if any(d in article.url for d in DOMAINES_SANS_ARTICLE):
            stats.ignores += 1
            continue
        image = image_de_partage(article.url, get=get)
        if image is None:
            stats.echecs += 1
            continue
        article.media = [image]
        db.commit()
        stats.illustres += 1
    if stats.illustres or stats.echecs or stats.ignores:
        logger.info(
            "Illustration : %s illustrés, %s échecs, %s ignorés (lien sans article)",
            stats.illustres, stats.echecs, stats.ignores,
        )
    return stats
