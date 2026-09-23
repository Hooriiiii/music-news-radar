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

**Fusion de deux événements.** `lier()` peut aplatir une chaîne (P -> D -> R
devient P -> R et D -> R dans le même appel) : si D avait déjà ses propres
membres avant de rejoindre R, D portait déjà son propre `Event`. Le rejoindre
à celui de R exige de FUSIONNER les deux -- déplacer toutes les observations
de l'un vers l'autre, jamais créer un second événement pour le même groupe.

Tous les compteurs (`source_count`, `platform_count`,
`independent_source_count`, `first_seen_at`, `last_seen_at`) sont RECALCULÉS
depuis les observations à chaque écriture, jamais incrémentés à l'aveugle --
ils doivent rester reconstructibles.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, Event, EventObservation, Source, SourceCategorie, SourceRole

# Copie volontaire de la logique de `election.CATEGORIES_PRIMAIRES` : importer
# `election.py` depuis ici créerait un cycle (`election` importe déjà
# `cluster`, qui importe ce module pour `attacher`).
_CATEGORIES_PRIMAIRES = frozenset(
    {SourceCategorie.ARTISTE, SourceCategorie.FESTIVAL, SourceCategorie.LABEL, SourceCategorie.CLUB}
)


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
            published_at=article.published_at,
            title=article.title,
            url=article.url,
            source_name=source.name,
        )
    )
    db.flush()


def _creer_evenement(db: Session, article: Article) -> Event:
    vu = article.published_at or article.fetched_at
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
        # Source disparue entre-temps (ne devrait pas arriver, aucune purge ne
        # cible `sources`) : compte pour elle-même, jamais fusionnée à tort.
        groupe_id = groupes.get(source_id, None) if source_id in groupes else source_id
        cle = ("groupe", groupe_id) if groupe_id is not None else ("source", source_id)
        cles_independantes.add(cle)
    cles_independantes.update(("nom", nom) for nom in noms_sans_source)
    event.independent_source_count = len(cles_independantes)


def attacher(db: Session, article: Article, representant: Article, repointes: list[Article]) -> Event:
    """Rattache `article` (qui vient de recevoir `duplicate_of_id = representant.id`
    dans `cluster.lier`) à l'événement du groupe, et `repointes` avec lui (les
    articles aplatis dans le même appel). Crée l'événement s'il n'existe pas
    encore ; fusionne deux événements existants si `article` en portait déjà un.

    Appelée pour un lien FRAIS comme pour un lien D'ARCHIVE : un article relié
    à un post déjà publié est une observation de plus sur le même événement,
    même si l'article d'origine sera purgé -- c'est le cas le plus utile pour
    mesurer la propagation dans le temps.
    """
    evenement_article = _evenement_de(db, article.id)
    evenement_representant = _evenement_de(db, representant.id)

    if evenement_representant is not None:
        cible = evenement_representant
        if evenement_article is not None and evenement_article.id != cible.id:
            _fusionner(db, evenement_article, cible)
    elif evenement_article is not None:
        # Le représentant n'a pas encore d'événement, mais l'article qui le
        # rejoint en avait déjà un (il avait lui-même des membres avant de
        # rejoindre le représentant) : c'est ce groupe-là qui continue.
        cible = evenement_article
    else:
        cible = _creer_evenement(db, representant)

    _ajouter_observation(db, cible, representant)
    _ajouter_observation(db, cible, article)
    for pointeur in repointes:
        _ajouter_observation(db, cible, pointeur)

    _recalculer(db, cible)
    db.flush()
    return cible


def retitrer(db: Session, nouveau_representant: Article) -> None:
    """Le titre d'un événement suit celui de son représentant élu, qui peut
    changer (`election.elire_representants`). Appelée seulement quand une
    réélection a effectivement eu lieu -- ne crée ni ne fusionne jamais
    d'événement, elle suppose que `attacher` a déjà fait ce travail."""
    event = _evenement_de(db, nouveau_representant.id)
    if event is not None:
        event.titre = nouveau_representant.title
