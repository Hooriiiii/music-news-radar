"""Rendre les evenements VISIBLES, sans rien leur faire decider (Phase 2A, Task 4).

Deux endpoints en LECTURE SEULE uniquement -- voir
`docs/superpowers/plans/2026-09-23-phase-2a-evenements.md`, Task 4 :
`GET /events` (liste, triable) et `GET /events/{id}` (avec ses observations).
Aucun endpoint d'ecriture ici, et aucun de ces champs ne doit etre lu par
l'arbitrage de publication -- 2A observe, elle ne decide rien.

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
from app.models import Event, EventObservation
from app.pipeline.event_features import calculer_features
from app.schemas import EventDetailOut, EventOut

router = APIRouter(prefix="/events", tags=["events"])


def _event_out(event: Event, *, maintenant: dt.datetime) -> EventOut:
    features = calculer_features(
        first_seen_at=event.first_seen_at,
        maintenant=maintenant,
        source_count=event.source_count,
        platform_count=event.platform_count,
        independent_source_count=event.independent_source_count,
        event_type=event.event_type,
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
    maintenant = dt.datetime.now(dt.timezone.utc)
    return [_event_out(e, maintenant=maintenant) for e in events]


@router.get("/{event_id}", response_model=EventDetailOut)
def get_event(event_id: int, db: Session = Depends(get_db)):
    """Un evenement et la liste des articles (observations) qui le composent,
    du plus recent au plus ancien."""
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    maintenant = dt.datetime.now(dt.timezone.utc)
    base = _event_out(event, maintenant=maintenant)
    observations = db.scalars(
        select(EventObservation)
        .where(EventObservation.event_id == event_id)
        .order_by(EventObservation.published_at.desc().nullslast(), EventObservation.id.desc())
    ).all()
    return EventDetailOut(**base.model_dump(), observations=observations)
