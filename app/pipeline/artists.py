"""Persistance des artistes repérés dans le flux — le moteur A&R de Kinda.

Le scoring extrait déjà `mentioned_artists` sur chaque article. Mais l'article vit
14 jours et le radar n'agrège que 7 jours glissants : sans cette couche, Kinda oublie
tout. Or ce qui intéresse un label, ce n'est pas « qui est cité aujourd'hui », c'est
« depuis quand, à quelle fréquence, et est-ce que ça accélère ».

On matérialise donc l'artiste (`Artist`) et chaque citation datée (`ArtistMention`),
avec les champs dénormalisés nécessaires pour rester lisibles après la purge des
articles.
"""

import datetime as dt
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Article, Artist, ArtistMention


def slugify(name: str) -> str:
    """Clé d'unicité d'un artiste : minuscules, sans accents, espaces normalisés."""
    folded = unicodedata.normalize("NFKD", name.strip().lower())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


def record_mentions(db: Session) -> int:
    """Enregistre les citations des articles scorés pas encore traités.

    Renvoie le nombre de citations créées. Idempotent : un article déjà dépouillé
    (présent dans artist_mentions) est ignoré, donc rejouer ne double rien.
    """
    already = select(ArtistMention.article_id).where(ArtistMention.article_id.is_not(None))
    articles = db.scalars(
        select(Article)
        .where(Article.mentioned_artists.is_not(None))
        .where(Article.relevance_score.is_not(None))
        .where(Article.id.not_in(already))
        .order_by(Article.id)
    ).all()

    created = 0
    for article in articles:
        seen_at = article.published_at or article.fetched_at or dt.datetime.now(dt.timezone.utc)
        for raw_name in article.mentioned_artists or []:
            name = raw_name.strip()
            slug = slugify(name)
            if not slug:
                continue
            artist = db.scalar(select(Artist).where(Artist.slug == slug))
            if artist is None:
                artist = Artist(
                    name=name, slug=slug, first_seen_at=seen_at, last_seen_at=seen_at,
                    mentions_count=0, max_emergence=article.emergence_score,
                )
                db.add(artist)
                db.flush()
            artist.mentions_count += 1
            artist.first_seen_at = min(artist.first_seen_at, seen_at)
            artist.last_seen_at = max(artist.last_seen_at, seen_at)
            if article.emergence_score is not None:
                artist.max_emergence = max(artist.max_emergence or 0, article.emergence_score)

            db.add(
                ArtistMention(
                    artist_id=artist.id,
                    article_id=article.id,
                    source_id=article.source_id,
                    mentioned_at=seen_at,
                    relevance_score=article.relevance_score,
                    emergence_score=article.emergence_score,
                    title=article.title,
                    url=article.url,
                )
            )
            created += 1
    db.commit()
    return created


def rising_artists(
    db: Session,
    window_days: int = 30,
    min_emergence: int | None = None,
    limit: int = 20,
) -> list[Artist]:
    """La shortlist A&R : les artistes les plus cités sur la fenêtre récente.

    `min_emergence` filtre sur le meilleur score d'émergence jamais atteint, pour
    ne garder que les jeunes pousses et écarter les stars déjà installées.
    """
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    recent = func.count(ArtistMention.id).label("recent")
    stmt = (
        select(Artist)
        .join(ArtistMention, ArtistMention.artist_id == Artist.id)
        .where(ArtistMention.mentioned_at >= since)
        .group_by(Artist.id)
        .order_by(recent.desc(), Artist.max_emergence.desc().nulls_last(), Artist.id)
        .limit(limit)
    )
    if min_emergence is not None:
        stmt = stmt.where(Artist.max_emergence >= min_emergence)
    return list(db.scalars(stmt))
