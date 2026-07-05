from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Digest
from app.schemas import DigestOut

router = APIRouter(prefix="/digests", tags=["digests"])


@router.get("", response_model=list[DigestOut])
def list_digests(db: Session = Depends(get_db)):
    return db.scalars(select(Digest).order_by(Digest.date.desc())).all()


@router.get("/{digest_id}", response_model=DigestOut)
def get_digest(digest_id: int, db: Session = Depends(get_db)):
    digest = db.get(Digest, digest_id)
    if digest is None:
        raise HTTPException(status_code=404, detail="Digest not found")
    return digest
