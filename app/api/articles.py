import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
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
    category: str | None = None,
    has_media: bool | None = None,
    since: dt.datetime | None = None,
    sort: Literal["date", "relevance"] = "date",
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
    if category is not None:
        stmt = stmt.where(Article.category == category)
    if has_media is not None:
        stmt = stmt.where(
            Article.media_urls.is_not(None) if has_media else Article.media_urls.is_(None)
        )
    if since is not None:
        stmt = stmt.where(Article.published_at >= since)
    if sort == "relevance":
        stmt = stmt.order_by(
            Article.relevance_score.desc().nulls_last(),
            Article.hotness_score.desc().nulls_last(),
            Article.id.desc(),
        )
    else:
        stmt = stmt.order_by(Article.published_at.desc().nulls_last(), Article.id.desc())
    return db.scalars(stmt.limit(limit).offset(offset)).all()


class ArticleStatusUpdate(BaseModel):
    status: ArticleStatus


@router.patch("/{article_id}", response_model=ArticleOut)
def update_article_status(
    article_id: int, payload: ArticleStatusUpdate, db: Session = Depends(get_db)
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    article.status = payload.status
    db.commit()
    db.refresh(article)
    return article
