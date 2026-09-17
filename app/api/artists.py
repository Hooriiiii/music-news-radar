from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Artist, ArtistMention, Source, SourceCategorie, SourceType
from app.pipeline.artists import rising_artists
from app.schemas import ArtistMentionOut, ArtistOut

router = APIRouter(prefix="/artists", tags=["artists"])


@router.get("", response_model=list[ArtistOut])
def list_rising_artists(
    window_days: int = Query(30, ge=1, le=365),
    min_emergence: int | None = Query(None, ge=0, le=100),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """La shortlist A&R : les artistes les plus cités sur la fenêtre demandée.
    `min_emergence` écarte les artistes déjà installés."""
    return rising_artists(
        db, window_days=window_days, min_emergence=min_emergence, limit=limit
    )


@router.get("/{artist_id}/mentions", response_model=list[ArtistMentionOut])
def list_artist_mentions(
    artist_id: int, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)
):
    """L'historique complet d'un artiste -- il survit à la purge des articles."""
    if db.get(Artist, artist_id) is None:
        raise HTTPException(status_code=404, detail="Artist not found")
    return db.scalars(
        select(ArtistMention)
        .where(ArtistMention.artist_id == artist_id)
        .order_by(ArtistMention.mentioned_at.desc())
        .limit(limit)
    ).all()


class ArtistUpdate(BaseModel):
    tracked: bool | None = None
    source_instagram_id: int | None = None


@router.patch("/{artist_id}", response_model=ArtistOut)
def update_artist_tracking(artist_id: int, payload: ArtistUpdate, db: Session = Depends(get_db)):
    """Suivi A&R manuel, et lien vers le compte Instagram suivi de l'artiste
    (sert a la cascade de scoring et aux visuels quand le nom ne suffit pas)."""
    artist = db.get(Artist, artist_id)
    if artist is None:
        raise HTTPException(status_code=404, detail="Artist not found")
    champs = payload.model_fields_set
    if "tracked" in champs and payload.tracked is not None:
        artist.tracked = payload.tracked
    if "source_instagram_id" in champs:
        source_id = payload.source_instagram_id
        if source_id is not None:
            source = db.get(Source, source_id)
            if source is None or source.type != SourceType.INSTAGRAM or source.categorie != SourceCategorie.ARTISTE:
                raise HTTPException(
                    status_code=422,
                    detail="source_instagram_id doit designer une source Instagram de categorie artiste",
                )
        artist.source_instagram_id = source_id
    db.commit()
    db.refresh(artist)
    return artist
