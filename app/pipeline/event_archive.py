"""La LECTURE de l'archive d'observations (Phase 2C, Tasks 1 et 2).

`app/pipeline/event_features.py` est PUR : pas de session, pas de reseau, pas
d'horloge implicite. Ce module-ci est l'autre moitie -- celle qui touche la
base -- et il ne calcule aucune note : il produit les VALEURS que les
fonctions pures attendent (`dates_publication`, `similarite_max`,
`profondeur_archive_heures`).

**Le champ de date est `published_at`, jamais `observed_at`.**
`dates_de_publication` est le SEUL endroit du projet ou ce choix se fait, et
il porte son test-sentinelle (`tests/pipeline/test_event_archive.py`). La mesure qui
l'impose est dans le docstring de `event_features.velocity` : l'etalement des
dates d'ingestion mesure la cadence horaire du pipeline, pas la propagation de
l'actu. Basculer ce champ ne ferait echouer aucun calcul -- seulement mentir
la metrique.

**Une ligne d'archive ANCIENNE peut mentir, et on ne peut pas toujours le
savoir.** Jusqu'au tour de correction 1 de la Phase 2C,
`events._moment_fait` ecrivait `article.published_at or article.fetched_at`
dans `EventObservation.published_at` : pour un article sans date de
publication, la colonne portait notre date d'INGESTION sans que rien ne le
trace. Mesure du 2026-09-30 : 79 des 548 observations dont l'article vivait
encore (14 %) etaient dans ce cas. L'ecriture ne replie plus (`events.
_ajouter_observation` ecrit `article.published_at` tel quel, la colonne est
nullable), donc toute ligne NEUVE est honnete ; les anciennes se rattrapent
avec `scripts/reparations/nettoyer_dates_observations.py` TANT QUE leur article vit
encore -- au-dela (purge a deux jours) l'information est perdue pour de bon,
et il vaut mieux le dire que de faire semblant.

Ce module ne LEVE jamais et n'exige pas la migration `0019` : sans les tables
d'evenements, l'archive est vide et la nouveaute vaut `None` -- une
fonctionnalite degradee, jamais un pipeline qui casse (meme philosophie
qu'`events.attacher` et `cluster_resume.charger_archive`).
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EventObservation
from app.pipeline import events
from app.pipeline.cluster import archive_similarity_from_tokens, significant_tokens
from app.pipeline.event_features import NOUVEAUTE_HORIZON_HEURES

# La fenetre de titres chargee pour juger une reprise, en jours. Identique a
# l'horizon de `event_features.NOUVEAUTE_HORIZON_HEURES` : au-dela, la dedup
# elle-meme considere qu'une reprise n'en est plus une
# (`cluster_archive_jours`, 7 jours). Charger plus large couterait des lignes
# pour des verdicts qu'on n'emettrait pas.
FENETRE_JOURS = int(NOUVEAUTE_HORIZON_HEURES // 24)


def en_utc(quand: dt.datetime) -> dt.datetime:
    """Un datetime naif est lu comme de l'UTC ; un datetime aware est
    converti.

    Publique parce que c'est la FRONTIERE : `app/pipeline/event_features.py`
    exige par contrat que ses dates soient du meme monde (il leve sinon, et
    c'est voulu -- une confusion de fuseau ne doit jamais etre avalee), donc
    quelqu'un doit normaliser AVANT lui. Ce quelqu'un est la couche qui lit
    la base, c'est-a-dire ce module et l'API. Voir
    `ArchiveEvenements.profondeur_heures` pour le detail du piege.
    """
    if quand.tzinfo is None:
        return quand.replace(tzinfo=dt.timezone.utc)
    return quand.astimezone(dt.timezone.utc)


def dates_de_publication(observations) -> list[dt.datetime]:
    """Les dates de PUBLICATION datees d'une suite d'observations.

    Le seul point du projet ou le champ est choisi. Une observation sans
    `published_at` est ECARTEE, jamais remplacee par `observed_at` : ce repli
    reintroduirait la cadence du pipeline dans une mesure de propagation, ce
    que toute la Phase 2C existe pour eviter.
    """
    return [o.published_at for o in observations if o.published_at is not None]


@dataclass(frozen=True)
class ArchiveEvenements:
    """Les titres archives, deja tokenises, et la date de debut de l'archive.

    `entrees` : (id d'evenement, date du fait, mots significatifs du titre).
    L'id d'evenement sert a ne jamais comparer un evenement a ses PROPRES
    observations -- elles portent forcement le meme titre.

    `debut` : la plus ancienne date que l'archive porte, tous evenements
    confondus. C'est elle qui dit si l'archive a assez de recul pour qu'une
    ABSENCE de reprise signifie quelque chose.
    """

    entrees: tuple[tuple[int, dt.datetime, frozenset[str]], ...] = ()
    debut: dt.datetime | None = None

    def profondeur_heures(self, instant: dt.datetime) -> float | None:
        """Le temps que l'archive couvre avant `instant`, en heures.

        `None` si l'archive est vide, ou si `instant` precede son debut (un
        evenement plus ancien que l'archive n'a aucun recul derriere lui).

        LES DEUX BORNES SONT RAMENEES EN UTC AWARE avant la soustraction
        (`en_utc`). Sans ca, cette ligne leve un `TypeError` des que les
        deux dates ne viennent pas du meme monde -- et c'est exactement ce
        qui arrive ici : `debut` est lu EN BASE (aware sous Postgres,
        NAIF sous SQLite, ou le type `DateTime(timezone=True)` ne porte
        aucun fuseau), tandis qu'`instant` est fourni par l'appelant, qui
        doit passer une horloge explicite (`dt.datetime.now(dt.timezone.utc)`
        cote API). Une feature qui marche en production et fait tomber
        `/events` sous SQLite n'est pas une feature, c'est un piege --
        et la suite de tests tourne justement sur SQLite.

        La convention appliquee au naif est UTC, parce que c'est ce que la
        base stocke partout dans ce projet.
        """
        if self.debut is None:
            return None
        heures = (en_utc(instant) - en_utc(self.debut)).total_seconds() / 3600.0
        return heures if heures > 0 else None

    def similarite_max(
        self, *, event_id: int | None, titre: str, avant: dt.datetime
    ) -> float | None:
        """La plus forte similarite entre ce titre et un titre archive
        STRICTEMENT anterieur a `avant`, hors observations du meme evenement.

        `None` -- et non `0.0` -- quand le titre n'a aucun mot significatif
        (titres generiques « Post Instagram », titres en ecriture non latine) :
        la comparaison n'a pas eu lieu, elle n'a pas conclu a la difference.
        `0.0` quand la comparaison a bien eu lieu sans rien trouver.
        """
        jetons = significant_tokens(titre)
        if not jetons:
            return None
        # Meme precaution de fuseau que `profondeur_heures` : `quand` vient de
        # la base, `avant` de l'appelant. Une comparaison mixte leve.
        borne = en_utc(avant)
        meilleure = 0.0
        for autre_id, quand, autres_jetons in self.entrees:
            if autre_id == event_id or en_utc(quand) >= borne:
                continue
            score = archive_similarity_from_tokens(jetons, set(autres_jetons))
            if score > meilleure:
                meilleure = score
        return meilleure


def charger_archive(
    db: Session,
    jours: int = FENETRE_JOURS,
    *,
    maintenant: dt.datetime | None = None,
) -> ArchiveEvenements:
    """Lit l'archive des titres d'observations, bornee a `jours`.

    `maintenant` borne la fenetre et se passe EXPLICITEMENT depuis
    l'appelant (l'API le fait) : une horloge implicite rend la lecture non
    reproductible d'un appel a l'autre. Absent, on retombe sur l'heure
    courante -- c'est un confort d'appel a la main, jamais le chemin d'un
    point d'entree.

    Une seule requete pour les titres, une seconde -- scalaire -- pour le
    debut REEL de l'archive, qui n'est pas borne par la fenetre : la
    profondeur doit dire depuis quand on archive, pas depuis quand on vient
    de regarder.
    """
    if not events._tables_disponibles(db):
        return ArchiveEvenements()
    depuis = (maintenant or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=jours)
    lignes = db.execute(
        select(
            EventObservation.event_id,
            EventObservation.title,
            EventObservation.published_at,
        ).where(EventObservation.published_at >= depuis)
    ).all()
    # `published_at` et lui seul, ici aussi : une observation sans date de
    # publication est ecartee plutot que datee de son ingestion -- la meme
    # regle que `dates_de_publication`, appliquee au meme endroit du projet.
    entrees = [
        (event_id, publie_le, frozenset(significant_tokens(titre)))
        for event_id, titre, publie_le in lignes
        if publie_le is not None
    ]
    debut = db.scalar(select(func.min(EventObservation.published_at)))
    return ArchiveEvenements(entrees=tuple(entrees), debut=debut)
