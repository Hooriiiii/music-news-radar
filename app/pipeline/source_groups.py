"""Regrouper les sources qui sont le MEME media sur plusieurs canaux, et
brancher ce regroupement sur le passage horaire (2026-09-24).

`sources.source_group_id` existe (migration 0019) et n'est rempli nulle part
par defaut : tant qu'elle est vide, `independent_source_count`
(`app/pipeline/events.py::_recalculer`) compte chaque source pour elle-meme,
meme quand deux sources ne sont que deux canaux du meme media -- une depeche
reprise en RSS et sur Instagram par le meme titre de presse compte alors pour
DEUX confirmations independantes au lieu d'une.

Mesure sur les sources actives (2026-09-24) : quatre groupes, tous du meme
motif, un media present en RSS et sur Instagram -- Mixmag, DJ Mag, Tsugi,
Billboard.

Piege a ne jamais reintroduire (`app/pipeline/events.py::_recalculer`) : une
source SANS groupe compte sous la cle `("source", son_id)`, une source AVEC
groupe sous `("groupe", id_du_groupe)`. Si un seul membre d'une paire porte
`source_group_id`, les deux tombent sous des cles DIFFERENTES et le
regroupement ne sert a rien -- TOUS les membres d'un groupe, la reference
comprise, doivent porter la MEME valeur.

Regles de `grouper_sources`, sur le modele de `scripts/categoriser_sources.py` :
- un mapping EXPLICITE fait foi -- pas de detection par similarite de nom
  (une normalisation qui retire des mots communs rapprocherait des sources
  sans rapport sur un corpus de 145 sources) ;
- la valeur du groupe est l'`id` le plus petit parmi les sources trouvees
  pour ce groupe -- deterministe et lisible en base ;
- un `source_group_id` deja pose (par `grouper_sources` ou a la main) et
  DIFFERENT de la cible n'est jamais ecrase -- seules les sources sans groupe
  (NULL) ou deja alignees sur la bonne valeur sont touchees ;
- un groupe de moins de deux sources trouvees en base n'a rien a regrouper ;
- une source du mapping absente de la base est signalee, pas une erreur ;
- idempotent.

**Pourquoi ce module vit dans `app/pipeline/` et non dans `scripts/`** (tour
de correction 1, 2026-09-24) : le regroupement a d'abord ete branche sur le
passage horaire via un `app/pipeline/source_groups.py` qui IMPORTAIT
`scripts.grouper_sources` et `scripts.rattraper_evenements` -- inversant la
dependance documentee dans CLAUDE.md (les scripts consomment `app/`, jamais
l'inverse) et faisant dependre le pipeline de deux modules qui font de
l'`argparse` et du `print`. La logique (mapping, pose des `source_group_id`,
recalcul cible) vit desormais ICI et dans `app/pipeline/events.py`
(`recalculer_tout`, a cote de son seul autre ecrivain, `_recalculer`) ;
`scripts/grouper_sources.py` et `scripts/rattraper_evenements.py` ne sont
plus que des points d'entree en ligne de commande qui RE-EXPORTENT ces
fonctions pour leur CLI et leurs tests existants -- leur comportement
observable n'a pas change d'un caractere.

Le branchement sur le passage horaire (`regrouper_sources_et_propager`,
appele depuis `app/pipeline/run.py` avant `cluster(db)`) obeit a deux regles
de cout, parce que les `Event` ne sont JAMAIS purges
(`app/pipeline/retention.py` ne les touche pas) et s'accumulent donc
indefiniment :
- on ne recalcule QUE quand le regroupement vient reellement de poser un
  `source_group_id` (le mapping est stable : zero groupe pose est le cas
  normal, et rend alors zero requete de plus) ;
- on ne recalcule QUE les evenements portes par les sources qui viennent de
  changer de groupe (`EventObservation.source_id`), jamais la base entiere --
  c'est `app.pipeline.events.recalculer_tout` qui fait deja ce travail, avec
  son parametre `evenements` pour le borner a une liste connue au lieu de
  tout relire.

Cette etape ne doit jamais interrompre le passage horaire : une panne ici
degrade la fonctionnalite (aucun regroupement pose ce passage-ci), jamais le
pipeline. C'est la lecon la plus chere du chantier Phase 2A -- un appel non
garde a une table absente peut faire tomber la TRANSACTION Postgres entiere
en ABORTED, donc plus de dedup, plus de scoring, plus de purge, et meme pas
la notification d'erreur (en fin de run). D'ou le `try/except` qui entoure
tout, `db.rollback()` en filet, et aucune exception qui remonte jamais a
l'appelant -- doublee d'une seconde ceinture dans `run_pipeline` lui-meme,
qui protege le passage quoi qu'il arrive a cette couche.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, EventObservation, Source, SourceType
from app.pipeline import events as events_module

logger = logging.getLogger(__name__)

# Quatre groupes seulement (mesure du 2026-09-24) : un media present en RSS
# et sur Instagram. Cle = (nom exact de la source, type). Ne pas etendre par
# un detecteur automatique -- voir le docstring du module.
GROUPES: tuple[tuple[str, frozenset[tuple[str, SourceType]]], ...] = (
    ("Mixmag", frozenset({("Mixmag", SourceType.RSS), ("Mixmag (IG)", SourceType.INSTAGRAM)})),
    ("DJ Mag", frozenset({("DJ Mag", SourceType.RSS), ("DJ Mag (IG)", SourceType.INSTAGRAM)})),
    ("Tsugi", frozenset({("Tsugi", SourceType.RSS), ("Tsugi (IG)", SourceType.INSTAGRAM)})),
    (
        "Billboard",
        frozenset({("Billboard (RSS)", SourceType.RSS), ("Billboard (IG)", SourceType.INSTAGRAM)}),
    ),
)


@dataclass(frozen=True)
class LignePlan:
    groupe: str
    source_id: int
    nom: str
    avant: int | None
    apres: int


def grouper_sources(
    db: Session, *, appliquer: bool = False
) -> tuple[list[LignePlan], list[str]]:
    """Rend (plan, avertissements) et ecrit si `appliquer`. Idempotent.

    `avertissements` couvre deux cas tolerants, jamais une erreur : un membre
    du mapping absent de la base, et un groupe deja pose differemment sur au
    moins un de ses membres (laisse intact, jamais applique a moitie)."""
    plan: list[LignePlan] = []
    absents: list[str] = []

    for nom_groupe, membres in GROUPES:
        trouvees: list[Source] = []
        for nom, type_ in membres:
            lignes = db.scalars(
                select(Source).where(Source.name == nom, Source.type == type_)
            ).all()
            if not lignes:
                absents.append(f"{nom_groupe} : « {nom} » ({type_.value}) absente de la base")
                continue
            trouvees.extend(lignes)

        if len(trouvees) < 2:
            continue

        cible = min(s.id for s in trouvees)
        conflit = any(
            s.source_group_id is not None and s.source_group_id != cible for s in trouvees
        )
        if conflit:
            # Au moins un membre porte deja un groupe different (pose a la
            # main, ou par un run precedent avec une autre reference) : on ne
            # touche a AUCUN membre. Poser la cible sur les autres membres
            # laisserait le groupe a moitie applique -- exactement le piege
            # que cette fonction existe pour eviter.
            absents.append(
                f"{nom_groupe} : groupe deja pose differemment sur au moins un membre, "
                "laisse en l'etat"
            )
            continue

        for source in trouvees:
            if source.source_group_id == cible:
                continue
            plan.append(
                LignePlan(
                    groupe=nom_groupe, source_id=source.id, nom=source.name,
                    avant=source.source_group_id, apres=cible,
                )
            )
            if appliquer:
                source.source_group_id = cible

    if appliquer:
        db.commit()
    return plan, absents


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
    """Pose `sources.source_group_id` (mapping explicite ci-dessus -- un
    `source_group_id` deja pose a la main et different n'est jamais ecrase)
    puis propage IMMEDIATEMENT aux evenements concernes, pour que les
    compteurs de corroboration ne mentent jamais entre le moment ou un groupe
    est pose et celui ou quelqu'un pense a relancer
    `scripts/rattraper_evenements.py --recalculer` a la main.

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
        rapport = events_module.recalculer_tout(db, appliquer=True, evenements=evenements)
        stats.evenements_recalcules = len(rapport.lignes)
    except Exception as exc:  # noqa: BLE001 -- ne doit jamais interrompre le passage horaire
        logger.exception("Regroupement des sources / propagation aux evenements impossible")
        stats.erreur = str(exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("Rollback impossible apres echec du regroupement des sources")
    return stats
