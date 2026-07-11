import datetime as dt

from pydantic import BaseModel, ConfigDict

from app.models import ArticleStatus, SourceType


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: SourceType
    url: str
    genre: str | None
    active: bool


class SourceCreate(BaseModel):
    name: str
    type: SourceType
    url: str
    genre: str | None = None


class SourceUpdate(BaseModel):
    name: str | None = None
    genre: str | None = None
    active: bool | None = None


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    url: str
    title: str
    summary: str | None
    published_at: dt.datetime | None
    fetched_at: dt.datetime
    relevance_score: int | None
    hotness_score: int | None
    category: str | None
    imprint: str | None
    genre: str | None
    status: ArticleStatus
    media_urls: list[str] | None


class DigestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    sent_at: dt.datetime | None
    article_ids: list[int]
