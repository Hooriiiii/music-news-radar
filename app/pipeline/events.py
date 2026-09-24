"""Rattache les articles regroupés par `cluster.py` à un `Event` (Phase 2A).

Le système raisonnait POST par POST : la dédup (`cluster.py`) rapproche des
articles mais jetait le lien une fois le représentant élu. Ce module rend le
regroupement explicite et mesurable, sans rien changer au comportement de
publication -- voir
`docs/superpowers/plans/2026-09-23-phase-2a-evenements.md`, Task 2.

**Le point d'entrée unique est `attacher`, appelé depuis `cluster.py::lier`**,
lui-même le SEUL point d'écriture de `duplicate_of_id` -- ce module ne réécrit
donc jamais le mécanisme de dédup, il observe ses décisions.

**L'événement suit le GROUPE, pas le représentant.** `elire_representants`
(`election.py`) peut changer qui est le représentant d'un groupe sans jamais
appeler ce module : c'est correct par construction, parce qu'un `Event` est
retrouvé par n'importe quel membre du groupe (`_evenement_de`), jamais par
« le représentant actuel » -- qu'un membre devienne représentant ou cesse de
l'être ne change donc rien à quel événement porte ses observations.

**`attacher` relit le groupe ENTIER, pas seulement les deux articles que
`lier()` vient de toucher** (tour de correction 1, point 2). Un membre lié lors
d'un passage ANTÉRIEUR à l'existence de ce module -- c'est le cas de TOUS les
groupes de production au moment de la mise en service -- n'a jamais eu
l'occasion de devenir une observation : rien dans les arguments d'un appel à
`lier()` ne le mentionne. `attacher` requête donc `Article.duplicate_of_id ==
representant.id` pour retrouver TOUS les membres actuels, au lieu de se fier
aux seuls objets passés en paramètre.

**Fusion de plusieurs événements.** Cette relecture complète peut faire
apparaître PLUSIEURS événements déjà associés à des membres du groupe (`lier()`
aplatit une chaîne P -> D -> R en P -> R et D -> R dans le même appel ; si D
avait déjà ses propres membres avant de rejoindre R, D portait déjà son propre
`Event`). Ils sont tous fusionnés dans celui du plus petit id -- jamais deux
événements pour un seul groupe.

**Une migration non jouée dégrade la fonctionnalité, jamais le pipeline**
(tour de correction 1, point 1 ; confirmé sur un vrai Postgres en tour de
correction 2 : sans cette garde, une table absente laisse la TRANSACTION
ABORTÉE, exactement comme redouté) : `_tables_disponibles` constate l'absence
de `events`/`event_observations` par une requête d'introspection sur LA
CONNEXION DE LA SESSION EN COURS (`db.connection()`, jamais une connexion à
part -- voir son docstring pour le bug reproduit et corrigé, SPÉCIFIQUE aux
pools à connexion unique donc aux tests) et rend `None`/no-op sans lever --
même philosophie que `illustration.py` (« ce module ne lève jamais ») ou la
source Instagram sans jeton.

Tous les compteurs (`source_count`, `platform_count`,
`independent_source_count`, `first_seen_at`, `last_seen_at`) sont RECALCULÉS
depuis les observations à chaque écriture, jamais incrémentés à l'aveugle --
ils doivent rester reconstructibles.
"""

import logging
import weakref
from dataclasses import dataclass, field

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import Article, Event, EventObservation, Source, SourceCategorie, SourceRole

logger = logging.getLogger(__name__)

# Copie volontaire de la logique de `election.CATEGORIES_PRIMAIRES` : importer
# `election.py` depuis ici créerait un cycle (`election` importe déjà
# `cluster`, qui importe ce module pour `attacher`).
_CATEGORIES_PRIMAIRES = frozenset(
    {SourceCategorie.ARTISTE, SourceCategorie.FESTIVAL, SourceCategorie.LABEL, SourceCategorie.CLUB}
)

# Mémorisé PAR ENGINE, une fois vrai (les tables ne disparaissent jamais après
# une migration jouée sur CETTE base) ; retenté tant que ce n'est pas le cas,
# pour qu'un `alembic upgrade head` appliqué en cours de vie d'un process
# providentiellement long (le worker du dashboard) soit repris sans redémarrage.
# `WeakKeyDictionary` plutôt qu'un booléen unique (tour de correction 2, point 2) :
# un booléen de process suppose une seule base par process -- vrai en
# production, mais un test qui ouvre plusieurs engines SQLite dans la même
# suite obtiendrait sinon un `True` périmé de l'un pour l'autre, avec une
# erreur illisible à la clé. Une clé par `Engine` (faible : un engine de test
# jeté à la fin du test ne fuit pas ici) règle ça sans qu'aucun test n'ait
# besoin de réinitialiser quoi que ce soit à la main.
_TABLES_PRESENTES: "weakref.WeakKeyDictionary[Engine, bool]" = weakref.WeakKeyDictionary()


def _tables_disponibles(db: Session) -> bool:
    """Vrai si `events` et `event_observations` existent.

    Introspection sur la connexion DE LA SESSION (`db.connection()`), jamais
    sur une connexion à part ouverte via `db.get_bind()` : mesuré en re-revue,
    `sa_inspect(db.get_bind())` fait checkouter à l'`Engine` une connexion
    SÉPARÉE de celle de la transaction en cours. **Spécifique aux pools à
    connexion unique, donc aux tests** (tour de correction 2 : vérifié sur un
    vrai Postgres, où le pool rend une connexion réellement distincte et ne
    perd rien) : sous `StaticPool` (celui des tests), cette connexion séparée
    est la MÊME connexion DBAPI physique, et la connexion Core que
    l'inspecteur ouvre dessus émet un ROLLBACK implicite en se refermant, qui
    annule alors AUSSI tout ce que la session avait flush2 mais pas commit --
    exactement l'assignation de `duplicate_of_id` que `cluster.lier` vient de
    poser. Reproduit et vérifié : avec `db.get_bind()`, un article fraîchement
    lié se retrouve délié après un simple `db.commit(); db.refresh(...)`.
    `db.connection()` réutilise la connexion (et la transaction) DÉJÀ ouverte
    par la session : aucun risque de ce genre, sur SQLite comme sur Postgres,
    avec ou sans les tables présentes -- et c'est ce qui rend les tests
    honnêtes plutôt qu'un correctif qui ne change rien en pratique."""
    bind = db.get_bind()
    if isinstance(bind, Engine) and _TABLES_PRESENTES.get(bind):
        return True
    try:
        inspecteur = sa_inspect(db.connection())
        resultat = inspecteur.has_table("events") and inspecteur.has_table("event_observations")
    except Exception:  # noqa: BLE001 -- une introspection qui echoue vaut « pas encore prete »
        logger.debug(
            "events._tables_disponibles : introspection en echec, evenements desactives "
            "pour ce passage",
            exc_info=True,
        )
        resultat = False
    if isinstance(bind, Engine):
        _TABLES_PRESENTES[bind] = resultat
    return resultat


def _role(source: Source | None) -> SourceRole:
    if source is None or source.categorie is None:
        return SourceRole.UNKNOWN
    if source.categorie in _CATEGORIES_PRIMAIRES:
        return SourceRole.PRIMARY
    if source.categorie == SourceCategorie.MEDIA:
        return SourceRole.SECONDARY
    return SourceRole.UNKNOWN


def _evenement_de(db: Session, article_id: int) -> Event | None:
    """L'événement qui porte déjà une observation pour cet article, s'il existe."""
    event_id = db.scalar(
        select(EventObservation.event_id).where(EventObservation.article_id == article_id)
    )
    return db.get(Event, event_id) if event_id is not None else None


def _observation_existe(db: Session, event_id: int, article_id: int) -> bool:
    return (
        db.scalar(
            select(EventObservation.id).where(
                EventObservation.event_id == event_id, EventObservation.article_id == article_id
            )
        )
        is not None
    )


def _moment_fait(article: Article):
    """Le repli UNIQUE utilisé pour dater un fait (tour de correction 1,
    point 5) : la date de publication, sinon celle d'ingestion -- jamais celle
    du rattachement. Résolu ICI, une seule fois, et écrit tel quel dans
    `EventObservation.published_at` : `_recalculer` n'a alors plus à choisir
    entre deux replis différents pour la même grandeur (`freshness`, Task 3,
    se calculera dessus)."""
    return article.published_at or article.fetched_at


def _ajouter_observation(db: Session, event: Event, article: Article) -> None:
    """Idempotent : n'ajoute rien si l'article a déjà sa preuve sur cet événement --
    c'est CE contrôle, pas la contrainte unique, qui garantit l'idempotence
    (« ne provoque pas l'erreur »)."""
    if _observation_existe(db, event.id, article.id):
        return
    source = article.source
    db.add(
        EventObservation(
            event_id=event.id,
            article_id=article.id,
            platform=source.type,
            source_id=article.source_id,
            source_role=_role(source),
            published_at=_moment_fait(article),
            title=article.title,
            url=article.url,
            source_name=source.name,
        )
    )
    db.flush()


def _creer_evenement(db: Session, article: Article) -> Event:
    vu = _moment_fait(article)
    event = Event(titre=article.title, first_seen_at=vu, last_seen_at=vu)
    db.add(event)
    db.flush()
    _ajouter_observation(db, event, article)
    return event


def _fusionner(db: Session, source_event: Event, cible: Event) -> None:
    """Déplace toutes les observations de `source_event` vers `cible`, puis
    supprime `source_event` (qui n'a plus aucune raison d'exister sans son
    dernier membre). Les deux ne peuvent jamais porter le même groupe : après
    cet appel, `source_event.id` ne doit plus être référencé nulle part."""
    if source_event.id == cible.id:
        return
    deja_dans_cible = set(
        db.scalars(
            select(EventObservation.article_id).where(
                EventObservation.event_id == cible.id, EventObservation.article_id.is_not(None)
            )
        )
    )
    observations = list(
        db.scalars(select(EventObservation).where(EventObservation.event_id == source_event.id))
    )
    for observation in observations:
        if observation.article_id is not None and observation.article_id in deja_dans_cible:
            # Doublon défensif : ne devrait pas arriver (un article n'a qu'un
            # seul événement à la fois), mais la contrainte unique interdirait
            # sinon la réaffectation.
            db.delete(observation)
            continue
        observation.event_id = cible.id
        if observation.article_id is not None:
            deja_dans_cible.add(observation.article_id)
    db.flush()
    db.delete(source_event)
    db.flush()


def _recalculer(db: Session, event: Event) -> None:
    """Recompte tout depuis les `EventObservation` -- jamais une valeur devinée
    ou incrémentée. Seule fonction qui écrit `first_seen_at`, `last_seen_at`,
    `source_count`, `platform_count`, `independent_source_count`."""
    observations = list(
        db.scalars(select(EventObservation).where(EventObservation.event_id == event.id))
    )
    if not observations:
        return
    # `o.published_at` porte déjà le repli résolu par `_moment_fait` à
    # l'écriture ; `o.observed_at` n'est qu'un FILET pour une ligne écrite
    # autrement (avant ce module, ou directement en base).
    moments = [o.published_at or o.observed_at for o in observations]
    event.first_seen_at = min(moments)
    event.last_seen_at = max(moments)

    event.platform_count = len({o.platform for o in observations})

    ids_source = {o.source_id for o in observations if o.source_id is not None}
    noms_sans_source = {o.source_name for o in observations if o.source_id is None}
    event.source_count = len(ids_source) + len(noms_sans_source)

    groupes: dict[int, int | None] = {}
    if ids_source:
        for source_id, groupe_id in db.execute(
            select(Source.id, Source.source_group_id).where(Source.id.in_(ids_source))
        ):
            groupes[source_id] = groupe_id
    cles_independantes: set[tuple] = set()
    for source_id in ids_source:
        # `.get` rend None aussi bien pour une source SANS groupe (colonne
        # NULL) que pour une source disparue entre-temps (ne devrait pas
        # arriver, aucune purge ne cible `sources`) -- les deux cas doivent
        # compter la source pour ELLE-MÊME, jamais sous la clé "groupe" (tour
        # de correction 1, point 4 : l'ancienne version y rangeait une source
        # disparue, ce qui la fusionnait à tort avec un groupe qui porterait
        # le même id numérique).
        groupe_id = groupes.get(source_id)
        cle = ("groupe", groupe_id) if groupe_id is not None else ("source", source_id)
        cles_independantes.add(cle)
    cles_independantes.update(("nom", nom) for nom in noms_sans_source)
    event.independent_source_count = len(cles_independantes)


_CHAMPS_RECALCULES = (
    "source_count", "platform_count", "independent_source_count",
    "first_seen_at", "last_seen_at",
)


@dataclass
class LigneRecalcul:
    event_id: int
    titre: str
    diffs: list[str]


@dataclass
class RapportRecalcul:
    tables_absentes: bool = False
    total_evenements: int = 0
    lignes: list[LigneRecalcul] = field(default_factory=list)


def _valeurs(event: Event) -> dict:
    return {champ: getattr(event, champ) for champ in _CHAMPS_RECALCULES}


def recalculer_tout(
    db: Session,
    *,
    appliquer: bool = False,
    evenements: list[Event] | None = None,
    finaliser: bool = True,
) -> RapportRecalcul:
    """Relit des `Event` existants et leur applique `_recalculer` -- la seule
    fonction qui écrit `source_count`, `platform_count`,
    `independent_source_count`, `first_seen_at`, `last_seen_at`. Ne crée ni ne
    supprime aucune `EventObservation`, ne touche aucun `Article`. Rend le
    rapport dans tous les cas ; écrit réellement seulement si `appliquer`.

    `evenements` restreint le recalcul à une liste déjà connue de l'appelant
    (utilisé par `app.pipeline.source_groups`, qui ne veut recalculer QUE les
    événements touchés par un regroupement de sources tout juste posé, jamais
    la base entière à chaque passage horaire) ; par défaut (`None`, le chemin
    de `scripts/rattraper_evenements.py --recalculer`), TOUS les événements
    sont relus, comportement inchangé.

    `finaliser` (tour de correction 2, 2026-09-24) : par défaut (`True`), la
    fonction clôt elle-même la transaction (`commit` si `appliquer`, sinon
    `rollback`) -- comportement inchangé pour `scripts/rattraper_evenements.py`.
    `app.pipeline.source_groups.regrouper_sources_et_propager` passe `False` :
    poser `sources.source_group_id` et recalculer les compteurs doivent être
    une SEULE transaction (tout ou rien), pour qu'une panne pendant le recalcul
    ne laisse jamais les groupes déjà committés avec des compteurs faux que
    plus rien ne retente. C'est alors à cet appelant de committer ou d'annuler.

    Point d'écriture unique de ces compteurs (avec `_recalculer` lui-même) :
    déplacé depuis `scripts/rattraper_evenements.py` (tour de correction 1,
    2026-09-24) pour que `app/` cesse de dépendre d'un module qui fait de
    l'`argparse` -- le script en reste un simple appelant, ré-exportant cette
    fonction pour sa ligne de commande et ses tests existants."""
    rapport = RapportRecalcul()

    if not _tables_disponibles(db):
        rapport.tables_absentes = True
        if finaliser:
            db.rollback()
        return rapport

    tous = list(db.scalars(select(Event))) if evenements is None else evenements
    rapport.total_evenements = len(tous)

    for event in tous:
        avant = _valeurs(event)
        _recalculer(db, event)
        apres = _valeurs(event)
        diffs = [
            f"{champ} : {avant[champ]!r} -> {apres[champ]!r}"
            for champ in _CHAMPS_RECALCULES
            if avant[champ] != apres[champ]
        ]
        if diffs:
            rapport.lignes.append(LigneRecalcul(event.id, event.titre, diffs))

    if finaliser:
        if appliquer:
            db.commit()
        else:
            db.rollback()
    return rapport


def attacher(db: Session, article: Article, representant: Article) -> Event | None:
    """Rattache `article` (qui vient de recevoir `duplicate_of_id = representant.id`
    dans `cluster.lier`) à l'événement du groupe -- crée l'événement s'il
    n'existe pas encore, fusionne les événements déjà associés à un membre du
    groupe s'il y en a plusieurs.

    Relit le groupe ENTIER (`Article.duplicate_of_id == representant.id`)
    plutôt que les seuls `article`/`representant` de cet appel : un membre lié
    par un passage antérieur doit devenir une observation lui aussi, sinon
    `source_count` sous-compte le groupe dès la mise en service (tour de
    correction 1, point 2).

    `db.flush()` en tête est OBLIGATOIRE : `cluster.lier` vient de modifier
    `duplicate_of_id` sur `article` ET sur les articles aplatis (« repointés »)
    dans la MÊME transaction, sans les flusher lui-même avant d'appeler cette
    fonction -- `SessionLocal` tourne en `autoflush=False` en production,
    donc sans ce flush la requête ci-dessous ne verrait ni l'un ni les autres.

    Appelée pour un lien FRAIS comme pour un lien D'ARCHIVE : un article relié
    à un post déjà publié est une observation de plus sur le même événement,
    même si l'article d'origine sera purgé -- c'est le cas le plus utile pour
    mesurer la propagation dans le temps.

    Rend `None` sans rien écrire si la migration `0019` n'est pas encore
    appliquée : une fonctionnalité dégradée, jamais un pipeline qui casse.
    """
    if not _tables_disponibles(db):
        return None

    db.flush()
    membres = list(
        db.scalars(select(Article).where(Article.duplicate_of_id == representant.id))
    )
    tous: dict[int, Article] = {representant.id: representant, article.id: article}
    for membre in membres:
        tous[membre.id] = membre

    evenements_existants: dict[int, Event] = {
        ev.id: ev
        for ev in db.scalars(
            select(Event)
            .join(EventObservation, EventObservation.event_id == Event.id)
            .where(EventObservation.article_id.in_(tous.keys()))
            .distinct()
        )
    }

    if not evenements_existants:
        cible = _creer_evenement(db, representant)
    else:
        # Le plus petit id sert de cible stable ; les autres (rare : deux
        # groupes préexistants qui se rejoignent dans le même appel) sont
        # fusionnés dedans, jamais deux événements pour un seul groupe.
        cible = min(evenements_existants.values(), key=lambda e: e.id)
        for autre in evenements_existants.values():
            if autre.id != cible.id:
                _fusionner(db, autre, cible)

    # `representant` EST le représentant courant du groupe (celui que
    # `cluster.lier` vient d'élire pour cet appel) : le titre le suit
    # TOUJOURS, y compris quand `cible` est un événement RÉUTILISÉ ou issu
    # d'une fusion -- sans cette ligne, une fusion garde le titre de
    # l'événement CIBLE (le plus petit id, donc souvent le PREMIER membre
    # historique du groupe), jamais celui du représentant final. Mesuré en
    # tour de correction 2 : un événement affichait le titre de « Dour
    # Festival unveils its 2027 lineup » alors que son représentant final
    # était un tout autre article -- cosmétique tant que rien ne l'affiche,
    # plus du tout une fois la Task 4 branchée sur ce champ.
    cible.titre = representant.title

    for membre in tous.values():
        _ajouter_observation(db, cible, membre)

    _recalculer(db, cible)
    db.flush()
    return cible


def retitrer(db: Session, nouveau_representant: Article) -> None:
    """Le titre d'un événement suit celui de son représentant élu, qui peut
    changer (`election.elire_representants`). Appelée seulement quand une
    réélection a effectivement eu lieu -- ne crée ni ne fusionne jamais
    d'événement, elle suppose que `attacher` a déjà fait ce travail.

    Rien à faire si la migration n'est pas jouée -- même dégradation que
    `attacher`, pour la même raison."""
    if not _tables_disponibles(db):
        return
    event = _evenement_de(db, nouveau_representant.id)
    if event is None:
        # Ne devrait arriver que si l'événement du groupe n'a jamais été créé
        # (migration appliquée en cours de route, ou bug) : l'affichage (Task
        # 4) garderait alors le titre de l'ancien représentant sans que rien
        # ne le signale ailleurs -- tour de correction 1, point 6.
        logger.warning(
            "retitrer : aucun evenement trouve pour le nouveau representant %s (%r) -- "
            "le titre affiche restera celui de l'ancien representant",
            nouveau_representant.id, nouveau_representant.title,
        )
        return
    event.titre = nouveau_representant.title
