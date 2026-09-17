import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.content.visuels import origine_interdite
from app.db import get_db
from app.models import Article, ArticleStatus, Source, SourceType
from app.pipeline.comptes_artistes import IndexComptes, construire_index
from app.schedule.store import etiquette_visuel, raison_sans_visuel
from app.schemas import ArticleOut

router = APIRouter(prefix="/articles", tags=["articles"])


def _avec_visuel(article: Article, index: IndexComptes) -> ArticleOut:
    sortie = ArticleOut.model_validate(article)
    sortie.visuel = etiquette_visuel(article, index)
    sortie.raison_sans_visuel = raison_sans_visuel(article, index)
    sortie.visuels_interdits = origine_interdite(article.source)
    return sortie


@router.get("", response_model=list[ArticleOut])
def list_articles(
    status: ArticleStatus | None = None,
    min_relevance: int | None = Query(None, ge=0, le=100),
    min_hotness: int | None = Query(None, ge=0, le=100),
    min_emergence: int | None = Query(None, ge=0, le=100),
    source_id: int | None = None,
    source_type: SourceType | None = None,
    category: str | None = None,
    has_media: bool | None = None,
    since: dt.datetime | None = None,
    # Par défaut on ne montre que le représentant de chaque actu : les reprises
    # de la même info par d'autres sources sont masquées (cf. pipeline/cluster.py)
    include_duplicates: bool = False,
    sort: Literal["date", "relevance", "emergence"] = "date",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Article).options(selectinload(Article.source))
    if status is not None:
        stmt = stmt.where(Article.status == status)
    if min_relevance is not None:
        stmt = stmt.where(Article.relevance_score >= min_relevance)
    if min_hotness is not None:
        stmt = stmt.where(Article.hotness_score >= min_hotness)
    if min_emergence is not None:
        stmt = stmt.where(Article.emergence_score >= min_emergence)
    if source_id is not None:
        stmt = stmt.where(Article.source_id == source_id)
    if source_type is not None:
        stmt = stmt.join(Source, Article.source_id == Source.id).where(Source.type == source_type)
    if category is not None:
        stmt = stmt.where(Article.category == category)
    if has_media is not None:
        stmt = stmt.where(
            Article.media.is_not(None) if has_media else Article.media.is_(None)
        )
    if since is not None:
        stmt = stmt.where(Article.published_at >= since)
    if not include_duplicates:
        stmt = stmt.where(Article.duplicate_of_id.is_(None))
    if sort == "emergence":
        # La vue A&R : les jeunes artistes à suivre, indépendamment de l'actu
        stmt = stmt.order_by(Article.emergence_score.desc().nulls_last(), Article.id.desc())
    elif sort == "relevance":
        stmt = stmt.order_by(
            Article.relevance_score.desc().nulls_last(),
            Article.hotness_score.desc().nulls_last(),
            Article.id.desc(),
        )
    else:
        stmt = stmt.order_by(Article.published_at.desc().nulls_last(), Article.id.desc())
    articles = db.scalars(stmt.limit(limit).offset(offset)).all()
    # Un index pour toute la page : les comptes suivis ne changent pas d'un article a l'autre.
    index = construire_index(db)
    return [_avec_visuel(article, index) for article in articles]


@router.get("/{article_id}", response_model=ArticleOut)
def get_article(article_id: int, db: Session = Depends(get_db)):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return _avec_visuel(article, construire_index(db))


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
