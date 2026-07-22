import datetime as dt

from pydantic import BaseModel, ConfigDict

from app.models import ArticleStatus, PostStatus, SourceType


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
    emergence_score: int | None
    category: str | None
    imprint: str | None
    genre: str | None
    status: ArticleStatus
    media_urls: list[str] | None


class ArtistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    mentions_count: int
    max_emergence: int | None
    tracked: bool


class ArtistMentionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    artist_id: int
    article_id: int | None
    source_id: int | None
    mentioned_at: dt.datetime
    relevance_score: int | None
    emergence_score: int | None
    title: str
    url: str


class DigestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    sent_at: dt.datetime | None
    article_ids: list[int]


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int | None
    article_title: str
    article_url: str
    source_name: str | None
    status: PostStatus
    template: str
    caption: str
    hashtags: list[str] | None
    slides: list[dict]
    created_at: dt.datetime
    published_at: dt.datetime | None


class PostUpdate(BaseModel):
    caption: str | None = None
    hashtags: list[str] | None = None
    slides: list[dict] | None = None
    status: PostStatus | None = None
