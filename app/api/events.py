"""Rendre les evenements VISIBLES, sans rien leur faire decider (Phase 2A, Task 4).

Deux endpoints en LECTURE SEULE uniquement -- voir
`docs/superpowers/plans/2026-09-23-phase-2a-evenements.md`, Task 4 :
`GET /events` (liste, triable) et `GET /events/{id}` (avec ses observations).
Aucun endpoint d'ecriture ici, et aucun de ces champs ne doit etre lu par
l'arbitrage de publication -- 2A observe, elle ne decide rien.

Phase 2C : `velocity`, `acceleration`, `nouveaute` et `pertinence_france`
rejoignent `freshness` et `corroboration`. Elles restent EN LECTURE SEULE et
aucune n'est lue par l'arbitrage de publication -- 2C calcule et expose, c'est
2D qui classera (`tests/test_events_non_regression.py`).

`freshness` et `corroboration` sont calcules A LA LECTURE
(`app/pipeline/event_features.py`, module pur deja teste) plutot que stockes :
ce sont des fonctions de l'instant present (la fraicheur decroit avec le temps
qui passe, meme si aucune observation nouvelle n'arrive), les persister les
figerait au moment du dernier rattachement.
"""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Article, Event, EventObservation, Post, Source, SourceRole
from app.pipeline.event_archive import (
    ArchiveEvenements,
    charger_archive,
    dates_de_publication,
    en_utc,
)
from app.pipeline.event_features import calculer_features
from app.schemas import EventDetailOut, EventObservationOut, EventOut

router = APIRouter(prefix="/events", tags=["events"])


def _relevances_representants(db: Session, event_ids: list[int]) -> dict[int, int]:
    """La pertinence (`relevance_score`) de l'article REPRESENTANT du groupe
    derriere cet evenement -- Tour de correction 1 : sans elle, la
    corroboration (le bavardage de la presse) n'a aucun chiffre editorial en
    face, et un evenement tres corrobore mais hors ligne editoriale (une salle
    Apple, un proces de marque, du country -- voir le plan Phase 2A) ressort
    identique a une vraie priorite.

    Le representant est l'article du groupe dont `duplicate_of_id IS NULL`
    (seul point d'ecriture : `cluster.py`, voir son docstring) -- c'est lui,
    et lui seul, dont le titre alimente `Event.titre`. Nullable par
    construction : l'article peut avoir ete purge (retention 14 j) sans que
    l'evenement ni ses observations ne disparaissent, exactement comme pour
    `Event.titre` qui reste alors fige sur le dernier representant connu.

    UNE requete pour toute la page, jamais une par evenement (tour de
    correction 2) : mesure avant correction, `/events?limit=200` passait
    **20,4 s sur 22,7 s** dans cette seule fonction, et le dashboard, qui
    demande 100 evenements, mettait ~10 s a ouvrir son panneau. C'etait
    herite de la Phase 2A, et c'est devenu le seul cout de la page une fois
    le contexte de la Phase 2C groupe. Meme motif que `_contexte`.

    Rend un dictionnaire des pertinences CONNUES : un evenement absent du
    resultat n'a pas de representant note (purge, ou note jamais posee), et
    l'appelant lit alors `None`. Le premier representant rencontre gagne,
    comme le faisait le `LIMIT 1` d'avant."""
    if not event_ids:
        return {}
    trouvees: dict[int, int] = {}
    for event_id, note in db.execute(
        select(EventObservation.event_id, Article.relevance_score)
        .join(Article, EventObservation.article_id == Article.id)
        .where(
            EventObservation.event_id.in_(event_ids),
            Article.duplicate_of_id.is_(None),
            Article.relevance_score.is_not(None),
        )
        .order_by(EventObservation.id)
    ):
        trouvees.setdefault(event_id, note)
    return trouvees


def _contexte(db: Session, event_ids: list[int]) -> dict[int, tuple[list, list[str]]]:
    """Pour chaque evenement : ses dates de PUBLICATION et les noms de ses
    sources PRIMAIRES.

    UNE seule requete pour toute la liste, jamais une par evenement : la route
    `/events` en rend jusqu'a 200, et un N+1 sur Neon se paie en secondes.

    Les sources primaires seules alimentent la pertinence France : le nom
    d'un MEDIA ne dit rien du pays de son sujet (Tsugi est francais et couvre
    l'international) -- voir `event_features.pertinence_france`.
    """
    if not event_ids:
        return {}
    lignes = db.execute(
        select(
            EventObservation.event_id,
            EventObservation.published_at,
            EventObservation.source_role,
            Source.name,
        )
        .join(Source, Source.id == EventObservation.source_id, isouter=True)
        .where(EventObservation.event_id.in_(event_ids))
    ).all()
    contexte: dict[int, tuple[list, list[str]]] = {i: ([], []) for i in event_ids}
    for event_id, publie_le, role, nom in lignes:
        dates, primaires = contexte.setdefault(event_id, ([], []))
        dates.append(publie_le)
        if role == SourceRole.PRIMARY and nom:
            primaires.append(nom)
    return contexte


class _Observation:
    """Le strict minimum que `dates_de_publication` lit -- pour que le choix
    du champ reste fait a UN SEUL endroit du projet (son test-sentinelle),
    meme quand les dates arrivent d'une requete a plat plutot que d'objets
    ORM."""

    __slots__ = ("published_at",)

    def __init__(self, published_at):
        self.published_at = published_at


def _event_out(
    db: Session,
    event: Event,
    *,
    maintenant: dt.datetime,
    archive: ArchiveEvenements,
    contexte: dict[int, tuple[list, list[str]]],
    relevances: dict[int, int],
) -> EventOut:
    dates, primaires = contexte.get(event.id, ([], []))
    # NORMALISATION DE FUSEAU, ici et pas ailleurs. Sous Postgres les
    # datetimes relus sont AWARE, sous SQLite ils sont NAIFS (la suite de
    # tests tourne sur SQLite) ; `event_features` leve deliberement si on lui
    # melange les deux. C'est donc a la couche qui lit la base de ramener
    # tout en UTC aware avant de l'appeler -- sinon `/events` rend 500 dans
    # un des deux mondes seulement, c'est-a-dire jamais celui ou on teste.
    premiere_preuve = en_utc(event.first_seen_at)
    features = calculer_features(
        first_seen_at=premiere_preuve,
        maintenant=maintenant,
        source_count=event.source_count,
        platform_count=event.platform_count,
        independent_source_count=event.independent_source_count,
        event_type=event.event_type,
        dates_publication=[
            en_utc(d) for d in dates_de_publication(_Observation(d) for d in dates)
        ],
        similarite_max=archive.similarite_max(
            event_id=event.id, titre=event.titre, avant=premiere_preuve
        ),
        profondeur_archive_heures=archive.profondeur_heures(premiere_preuve),
        titre=event.titre,
        noms_sources_primaires=primaires,
    )
    return EventOut(
        id=event.id,
        titre=event.titre,
        event_type=event.event_type,
        first_seen_at=event.first_seen_at,
        last_seen_at=event.last_seen_at,
        source_count=event.source_count,
        platform_count=event.platform_count,
        independent_source_count=event.independent_source_count,
        freshness=features.freshness,
        corroboration=features.corroboration,
        velocity=features.velocity,
        acceleration=features.acceleration,
        nouveaute=features.nouveaute,
        pertinence_france=features.pertinence_france,
        relevance_representant=relevances.get(event.id),
    )


@router.get("", response_model=list[EventOut])
def list_events(
    sort: str = Query("last_seen_at", pattern="^(last_seen_at|source_count)$"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Les evenements recents -- tri par fraicheur d'arrivee (`last_seen_at`,
    defaut) ou par corroboration brute (`source_count`)."""
    colonne = Event.source_count if sort == "source_count" else Event.last_seen_at
    events = db.scalars(select(Event).order_by(colonne.desc()).limit(limit)).all()
    # L'horloge est prise UNE FOIS et passee explicitement partout (features,
    # fenetre d'archive) : deux appels a `now()` dans la meme reponse
    # donneraient deux fraicheurs incoherentes, et `event_features` refuse
    # par contrat toute horloge implicite.
    maintenant = dt.datetime.now(dt.timezone.utc)
    archive = charger_archive(db, maintenant=maintenant)
    ids = [e.id for e in events]
    contexte = _contexte(db, ids)
    relevances = _relevances_representants(db, ids)
    return [
        _event_out(
            db, e, maintenant=maintenant, archive=archive,
            contexte=contexte, relevances=relevances,
        )
        for e in events
    ]


@router.get("/{event_id}", response_model=EventDetailOut)
def get_event(event_id: int, db: Session = Depends(get_db)):
    """Un evenement et la liste des articles (observations) qui le composent,
    du plus recent au plus ancien."""
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    maintenant = dt.datetime.now(dt.timezone.utc)
    archive = charger_archive(db, maintenant=maintenant)
    base = _event_out(
        db,
        event,
        maintenant=maintenant,
        archive=archive,
        contexte=_contexte(db, [event.id]),
        relevances=_relevances_representants(db, [event.id]),
    )
    observations = db.scalars(
        select(EventObservation)
        .where(EventObservation.event_id == event_id)
        .order_by(EventObservation.published_at.desc().nullslast(), EventObservation.id.desc())
    ).all()
    return EventDetailOut(
        **base.model_dump(), observations=_vues_observations(db, observations)
    )


def _vues_observations(db: Session, observations) -> list[EventObservationOut]:
    """Les observations, plus le post Kinda tire de chaque article quand il y
    en a un.

    UNE requete pour toute la liste, jamais une par observation : meme regle
    que `_contexte` et `_relevances_representants`, ou un N+1 sur Neon se
    payait en secondes.

    Un article peut porter PLUSIEURS posts (un brouillon jete puis refait) :
    le premier par identifiant gagne, comme `setdefault` le fait deja pour la
    pertinence du representant. C'est un renvoi de navigation, pas un choix
    editorial -- la fiche ouverte dit le reste."""
    article_ids = [o.article_id for o in observations if o.article_id is not None]
    posts_par_article: dict[int, int] = {}
    if article_ids:
        for article_id, post_id in db.execute(
            select(Post.article_id, Post.id)
            .where(Post.article_id.in_(article_ids))
            .order_by(Post.id)
        ):
            posts_par_article.setdefault(article_id, post_id)
    return [
        EventObservationOut.model_validate(o).model_copy(
            update={"post_id": posts_par_article.get(o.article_id)}
        )
        for o in observations
    ]
