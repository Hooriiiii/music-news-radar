import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Source
from app.schemas import SourceCreate, SourceOut, SourceUpdate

router = APIRouter(prefix="/sources", tags=["sources"])


def vue(source: Source) -> SourceOut:
    """La source PLUS sa sante, extraite de `sources.state`.

    `state` est un JSONB fourre-tout (il porte aussi le `since_id` de X et
    `dernier_post` d'Instagram) : on n'en expose que les trois cles ecrites par
    `app/pipeline/health.py::marquer_sante`, et jamais le reste -- un etat
    corrompu (pas un dict) vaut « on ne sait pas », pas une 500.
    """
    etat = source.state if isinstance(source.state, dict) else {}
    base = SourceOut.model_validate(source)
    erreur = etat.get("last_error")
    return base.model_copy(
        update={
            "last_error": str(erreur)[:500] if erreur else None,
            "last_error_at": _date_heure(etat.get("last_error_at")),
            "last_ok_at": _date_heure(etat.get("last_ok_at")),
        }
    )


def _date_heure(valeur) -> dt.datetime | None:
    if not isinstance(valeur, str):
        return None
    try:
        return dt.datetime.fromisoformat(valeur)
    except ValueError:
        return None


@router.get("", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)):
    return [vue(source) for source in db.scalars(select(Source).order_by(Source.name))]


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, db: Session = Depends(get_db)):
    source = Source(
        name=payload.name.strip(),
        type=payload.type,
        url=payload.url.strip(),
        genre=(payload.genre or None),
        categorie=payload.categorie,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return vue(source)


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(source_id: int, payload: SourceUpdate, db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    fields = payload.model_dump(exclude_unset=True)
    for key, value in fields.items():
        setattr(source, key, value)
    db.commit()
    db.refresh(source)
    return vue(source)
