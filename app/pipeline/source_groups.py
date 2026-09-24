"""Brancher `scripts/grouper_sources.py` sur le passage horaire (2026-09-24).

Pourquoi : le script pose `sources.source_group_id` a partir d'un mapping
explicite (Mixmag, DJ Mag, Tsugi, Billboard -- un meme media present en RSS et
sur Instagram), mais ne se relance jamais tout seul. Le jour ou l'utilisateur
ajoute un media present sur deux canaux, celui-ci recomptera double EN
SILENCE dans `independent_source_count` (voir le docstring de
`scripts/grouper_sources.py`) tant que personne ne pense a relancer le script
a la main.

Deux regles de cout, parce que les `Event` ne sont JAMAIS purges
(`app/pipeline/retention.py` ne les touche pas) et s'accumulent donc
indefiniment :
- on ne recalcule QUE quand le regroupement vient reellement de poser un
  `source_group_id` (le mapping est stable : zero groupe pose est le cas
  normal, et rend alors zero requete de plus) ;
- on ne recalcule QUE les evenements portes par les sources qui viennent de
  changer de groupe (`EventObservation.source_id`), jamais la base entiere --
  c'est `scripts/rattraper_evenements.py::recalculer_tout` qui fait deja ce
  travail groupe par groupe, avec son parametre `evenements` pour le borner
  a une liste connue au lieu de tout relire.

Cette etape ne doit jamais interrompre le passage horaire : une panne ici
degrade la fonctionnalite (aucun regroupement pose ce passage-ci), jamais le
pipeline. C'est la lecon la plus chere du chantier Phase 2A -- un appel non
garde a une table absente peut faire tomber la TRANSACTION Postgres entiere
en ABORTED, donc plus de dedup, plus de scoring, plus de purge, et meme pas
la notification d'erreur (en fin de run). D'ou le `try/except` qui entoure
tout, `db.rollback()` en filet, et aucune exception qui remonte jamais a
l'appelant.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, EventObservation
from app.pipeline import events as events_module
from scripts.grouper_sources import grouper_sources
from scripts.rattraper_evenements import recalculer_tout

logger = logging.getLogger(__name__)


@dataclass
class GroupementStats:
    """Vide (`sources_regroupees == 0`) dans le cas normal -- le mapping est
    stable d'un passage a l'autre. Non vide seulement le jour ou une source
    rejoint fraichement un groupe (ou qu'un nouveau groupe apparait dans le
    mapping)."""

    sources_regroupees: int = 0
    evenements_recalcules: int = 0
    avertissements: list[str] = field(default_factory=list)
    erreur: str | None = None


def regrouper_sources_et_propager(db: Session) -> GroupementStats:
    """Pose `sources.source_group_id` (mapping explicite, voir
    `scripts/grouper_sources.py` -- un `source_group_id` deja pose a la main
    et different n'est jamais ecrase) puis propage IMMEDIATEMENT aux
    evenements concernes, pour que les compteurs de corroboration ne mentent
    jamais entre le moment ou un groupe est pose et celui ou quelqu'un pense
    a relancer `scripts/rattraper_evenements.py --recalculer` a la main.

    Ne leve jamais (voir le docstring du module)."""
    stats = GroupementStats()
    try:
        plan, avertissements = grouper_sources(db, appliquer=True)
        stats.sources_regroupees = len(plan)
        stats.avertissements = avertissements
        if not plan:
            return stats

        if not events_module._tables_disponibles(db):
            # Migration 0019 non jouee sur cette base : le regroupement des
            # sources reste pose (utile independamment des evenements), mais
            # rien a propager pour l'instant.
            return stats

        source_ids = {ligne.source_id for ligne in plan}
        event_ids = list(
            db.scalars(
                select(EventObservation.event_id)
                .where(EventObservation.source_id.in_(source_ids))
                .distinct()
            )
        )
        if not event_ids:
            return stats

        evenements = list(db.scalars(select(Event).where(Event.id.in_(event_ids))))
        rapport = recalculer_tout(db, appliquer=True, evenements=evenements)
        stats.evenements_recalcules = len(rapport.lignes)
    except Exception as exc:  # noqa: BLE001 -- ne doit jamais interrompre le passage horaire
        logger.exception("Regroupement des sources / propagation aux evenements impossible")
        stats.erreur = str(exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("Rollback impossible apres echec du regroupement des sources")
    return stats
