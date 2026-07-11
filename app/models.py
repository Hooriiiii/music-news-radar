import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# JSONB en prod (Postgres), JSON générique pour les tests SQLite.
# none_as_null : sans lui, un None Python devient la VALEUR json 'null'
# (pas un NULL SQL) et les filtres IS NOT NULL ne filtrent plus rien
JSONVariant = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class SourceType(str, enum.Enum):
    RSS = "rss"
    REDDIT_RSS = "reddit_rss"
    BLUESKY = "bluesky"
    X = "x"


class ArticleStatus(str, enum.Enum):
    NEW = "new"
    REVIEWED = "reviewed"
    USED = "used"


def _values(enum_cls):
    return [m.value for m in enum_cls]


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type", native_enum=False, length=20, values_callable=_values)
    )
    url: Mapped[str] = mapped_column(String(1000))
    genre: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # État propre à l'adapter (ex. X/Apify : since, last_run_at) -- réassigner un
    # dict complet pour que SQLAlchemy détecte le changement, pas de mutation in place
    state: Mapped[dict | None] = mapped_column(JSONVariant)

    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    url: Mapped[str] = mapped_column(String(2000))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    relevance_score: Mapped[int | None] = mapped_column(Integer)
    hotness_score: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(String(100))
    imprint: Mapped[str | None] = mapped_column(String(200))
    genre: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[ArticleStatus] = mapped_column(
        Enum(
            ArticleStatus, name="article_status", native_enum=False, length=20,
            values_callable=_values,
        ),
        default=ArticleStatus.NEW,
        server_default="new",
        index=True,
    )
    raw_hash: Mapped[str] = mapped_column(String(64), unique=True)
    media_urls: Mapped[list | None] = mapped_column(JSONVariant)
    # Horodatage de l'alerte Discord envoyée (null = jamais alerté) -- garantit
    # l'idempotence des runs fréquents de détection du hot
    alerted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[Source] = relationship(back_populates="articles")


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, unique=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    article_ids: Mapped[list] = mapped_column(JSONVariant, default=list)
