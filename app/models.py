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


class ScoredBy(str, enum.Enum):
    """Quel étage de la cascade a produit les notes d'un article.

    Sans cette trace, impossible de savoir a posteriori quels articles n'ont eu
    que le traitement au rabais du modèle local — et le dashboard ne peut pas les
    distinguer de ceux jugés par Claude.
    """

    CLAUDE = "claude"
    LOCAL = "local"


class PostStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"


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
    # Axe A&R : intérêt "jeune artiste à suivre", indépendant de relevance
    emergence_score: Mapped[int | None] = mapped_column(Integer, index=True)
    # Étage de la cascade qui a produit ces notes : "claude" (le juge) ou
    # "local" (article écarté par le pré-filtre, jamais soumis à Claude).
    # Null = pas encore scoré.
    scored_by: Mapped[ScoredBy | None] = mapped_column(
        Enum(
            ScoredBy, name="scored_by", native_enum=False, length=20,
            values_callable=_values,
        )
    )
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
    # Medias TYPES (photo / video / gif) avec URL exploitable, duree et dimensions.
    # Remplace l'ancienne `media_urls: list[str]`, dans laquelle un tweet video
    # stockait sa VIGNETTE -- indistinguable d'une photo, donc le format REELS
    # etait indecidable. Reassigner la liste ENTIERE, jamais de mutation in place.
    media: Mapped[list | None] = mapped_column(JSONVariant)
    # Horodatage de l'alerte Discord envoyée (null = jamais alerté) -- garantit
    # l'idempotence des runs fréquents de détection du hot
    alerted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Artistes mentionnés, extraits par le scoring -- alimentent le radar maison
    mentioned_artists: Mapped[list | None] = mapped_column(JSONVariant)
    # Même actu déjà couverte par une autre source : pointe vers le représentant
    # (l'article vu le plus tôt). Null = c'est lui, le représentant. Jamais de
    # chaîne : un doublon pointe toujours vers un article dont ce champ est null.
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )

    source: Mapped[Source] = relationship(back_populates="articles")


class Artist(Base):
    """Un artiste repéré dans le flux. C'est l'ACTIF DURABLE de Kinda : les articles
    sont purgés à 14 jours, les artistes et leurs mentions ne le sont jamais. La
    courbe de citation d'un artiste dans le temps est le deal-flow du label."""

    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Forme normalisée (minuscules, sans accents) : la clé d'unicité, pour que
    # "Peggy Gou" et "peggy gou" ne fassent pas deux fiches
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    mentions_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Meilleur score d'émergence jamais vu : un artiste repéré une fois comme très
    # prometteur le reste, même si les mentions suivantes sont anodines
    max_emergence: Mapped[int | None] = mapped_column(Integer, index=True)
    # Suivi manuel : l'artiste est entré dans le radar A&R de Kinda
    tracked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    mentions: Mapped[list["ArtistMention"]] = relationship(back_populates="artist")


class ArtistMention(Base):
    """Une citation d'un artiste dans un article, à une date donnée.

    Les champs sont DÉNORMALISÉS (titre, url, scores) exprès : l'article sera purgé,
    la mention doit rester lisible sans lui.
    """

    __tablename__ = "artist_mentions"

    id: Mapped[int] = mapped_column(primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id"), index=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    mentioned_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    relevance_score: Mapped[int | None] = mapped_column(Integer)
    emergence_score: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(2000))

    artist: Mapped[Artist] = relationship(back_populates="mentions")


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, unique=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    article_ids: Mapped[list] = mapped_column(JSONVariant, default=list)


class Post(Base):
    """Un post Instagram genere a partir d'un article.

    Table separee et non colonnes sur `articles` pour deux raisons : un article
    peut donner plusieurs tentatives de post, et surtout les articles sont purges
    a 14 jours alors qu'un post publie est une archive permanente. Meme motif que
    `artist_mentions` : FK en SET NULL et champs DENORMALISES, pour que le post
    reste lisible apres la purge de son article.
    """

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    # Denormalise : survit a la purge de l'article
    article_title: Mapped[str] = mapped_column(Text)
    article_url: Mapped[str] = mapped_column(String(2000))
    source_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[PostStatus] = mapped_column(
        Enum(
            PostStatus, name="post_status", native_enum=False, length=20,
            values_callable=_values,
        ),
        default=PostStatus.DRAFT,
        server_default="draft",
        index=True,
    )
    template: Mapped[str] = mapped_column(
        String(100), default="kinda_post", server_default="kinda_post"
    )
    caption: Mapped[str] = mapped_column(Text)
    hashtags: Mapped[list | None] = mapped_column(JSONVariant)
    # Liste ordonnee : [{"kind": "cover", "headline": ..., "title": ..., "subtitle":
    # ..., "image_url": ...}, ...]. "cover" est rendue par Chromium ; "media" (a
    # venir) passe telle quelle a l'export -- c'est une vraie photo/video de
    # l'actu, pas du texte genere. Reassigner la liste ENTIERE, jamais de mutation
    # in place.
    slides: Mapped[list] = mapped_column(JSONVariant, default=list)
    # Les quatre propositions et l'indice de celle que l'utilisateur retient
    # (`retenue: null` tant qu'il n'a pas choisi). Les non retenues sont GARDEES :
    # choisir entre quatre formulations du meme sujet est du signal sur ce qui
    # plait, gratuit a collecter, et exploitable par le tri local a venir.
    # Reassigner le dict ENTIER, jamais de mutation in place.
    variants: Mapped[dict | None] = mapped_column(JSONVariant)
    # Rapport du dernier rendu : par slide, l'echelle retenue, le nombre de lignes
    # et un drapeau de debordement. Nullable : un post jamais rendu n'en a pas.
    # C'est ce qui permet au dashboard de signaler un post en defaut au lieu de
    # laisser publier une image coupee.
    # Reassigner le dict ENTIER, jamais de mutation in place.
    render_report: Mapped[dict | None] = mapped_column(JSONVariant)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
