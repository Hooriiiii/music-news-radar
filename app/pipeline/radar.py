import datetime as dt
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, Source


def top_artists(
    db: Session,
    window_days: int,
    min_relevance: int,
    limit: int,
    allowed_genres: list[str] | None = None,
) -> list[str]:
    """Les artistes du moment : les plus mentionnés dans les articles récents à
    forte pertinence. C'est le palmarès maison dérivé du flux scoré, qui pilote
    la recherche X du radar.

    Gate par genre de source : seules les sources pop/électro contribuent, pour
    que les sources festivals multi-genres n'injectent pas d'artistes hors ligne
    (le modèle ne filtre pas le genre de façon fiable)."""
    if allowed_genres is None:
        allowed_genres = [g.strip() for g in settings.radar_source_genres.split(",") if g.strip()]
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    rows = db.scalars(
        select(Article.mentioned_artists)
        .join(Source, Source.id == Article.source_id)
        .where(Article.relevance_score >= min_relevance)
        .where(Article.fetched_at >= since)
        .where(Article.mentioned_artists.is_not(None))
        .where(Source.genre.in_(allowed_genres))
    )
    counter: Counter[str] = Counter()
    for artists in rows:
        for name in artists or []:
            cleaned = name.strip()
            if cleaned:
                counter[cleaned] += 1
    return [name for name, _ in counter.most_common(limit)]
