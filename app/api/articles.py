import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Article, ArticleStatus
from app.schemas import ArticleOut

router = APIRouter(prefix="/articles", tags=["articles"])


@router.get("", response_model=list[ArticleOut])
def list_articles(
    status: ArticleStatus | None = None,
    min_relevance: int | None = Query(None, ge=0, le=100),
    min_hotness: int | None = Query(None, ge=0, le=100),
    source_id: int | None = None,
    since: dt.datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Article)
    if status is not None:
        stmt = stmt.where(Article.status == status)
    if min_relevance is not None:
        stmt = stmt.where(Article.relevance_score >= min_relevance)
    if min_hotness is not None:
        stmt = stmt.where(Article.hotness_score >= min_hotness)
    if source_id is not None:
        stmt = stmt.where(Article.source_id == source_id)
    if since is not None:
        stmt = stmt.where(Article.published_at >= since)
    stmt = (
        stmt.order_by(Article.published_at.desc().nulls_last(), Article.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return db.scalars(stmt).all()
