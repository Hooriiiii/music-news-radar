"""Le retrait humain vaut pour l'ACTU, pas pour une ligne.

Une feuille : `election`, `cluster`, `retention`, `scoring` et `reouverture` la lisent,
et `reouverture` importe le runner.

La regle tient en un invariant : **le representant d'un groupe porte le retrait d'un
de ses membres**. `store._articles_publiables` ecarte deja un article exclu et tout
doublon : il n'y a donc ni verrou a poser ni a defaire, et la carte du representant
offre « Remettre dans la file ». Raison, mesures et limites :
docs/reference/dedup-et-retention.md, « Le retrait humain vaut pour l'actu ».
"""

import logging

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Article, ArticleStatus

logger = logging.getLogger(__name__)

# Affichee sur la carte (« ⛔ hors file : ... ») : le mot du bouton qui l'a posee.
RAISON_NE_PAS_PUBLIER = "« Ne pas publier » depuis le dashboard"
# Lignes ecrites par l'ancien bouton « Sauter », encore en base.
RAISON_SAUTE_ANCIENNE = "saute depuis le dashboard"
RAISONS_HUMAINES = (RAISON_NE_PAS_PUBLIER, RAISON_SAUTE_ANCIENNE)


def retrait_humain(raison: str | None) -> bool:
    """Liste fermee, jamais un motif : une exclusion machine ne verrouille jamais un
    groupe (une panne n'exclut pas, et ce n'est pas a elle d'enterrer une actu)."""
    return raison in RAISONS_HUMAINES


def reporter_sur_le_representant(representant: Article, raison: str | None) -> None:
    """Non committe. Ne recouvre aucune raison deja posee, et un article publie n'a plus
    de file d'ou sortir."""
    if (
        retrait_humain(raison)
        and representant.file_exclu_raison is None
        and representant.status != ArticleStatus.USED
    ):
        representant.file_exclu_raison = raison
        logger.warning("retrait humain reporte sur le representant %s (%s)", representant.id, representant.title)


def membres_du_groupe(db: Session, article: Article) -> list[Article]:
    tete = article.duplicate_of_id if article.duplicate_of_id is not None else article.id
    return list(db.scalars(select(Article).where(or_(Article.id == tete, Article.duplicate_of_id == tete))))


def lever_les_retraits_du_groupe(membres: list[Article]) -> None:
    """Non committe. Seules les raisons humaines : « gel impossible » reste sur son
    article, comme la trace d'un doublon d'archive."""
    for membre in membres:
        if retrait_humain(membre.file_exclu_raison):
            membre.file_exclu_raison = None
