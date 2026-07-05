import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, Source
from app.pipeline.dedup import compute_raw_hash
from app.sources import get_adapter as default_get_adapter
from app.sources.base import SourceAdapter

logger = logging.getLogger(__name__)


@dataclass
class IngestStats:
    source_id: int
    source_name: str
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    error: str | None = None


def ingest_source(db: Session, source: Source, adapter: SourceAdapter | None = None) -> IngestStats:
    """Fetch une source, déduplique par raw_hash et insère les nouveaux articles."""
    adapter = adapter or default_get_adapter(source)
    stats = IngestStats(source_id=source.id, source_name=source.name)
    items = adapter.fetch()
    stats.fetched = len(items)
    if not items:
        return stats

    hashes = [compute_raw_hash(item.url, item.title) for item in items]
    seen = set(db.scalars(select(Article.raw_hash).where(Article.raw_hash.in_(hashes))))
    for item, raw_hash in zip(items, hashes):
        if raw_hash in seen:
            stats.duplicates += 1
            continue
        seen.add(raw_hash)
        db.add(
            Article(
                source_id=source.id,
                url=item.url[:2000],
                title=item.title,
                summary=item.summary,
                published_at=item.published_at,
                raw_hash=raw_hash,
                media_urls=item.media_urls or None,
            )
        )
        stats.new += 1
    db.commit()
    return stats


def run_ingest(db: Session, get_adapter=default_get_adapter) -> list[IngestStats]:
    """Ingère toutes les sources actives. Une source qui casse n'arrête pas le run."""
    sources = db.scalars(select(Source).where(Source.active)).all()
    results = []
    for source in sources:
        try:
            results.append(ingest_source(db, source, adapter=get_adapter(source)))
        except Exception as exc:
            logger.exception("Ingestion échouée pour la source %s", source.name)
            db.rollback()
            results.append(
                IngestStats(source_id=source.id, source_name=source.name, error=str(exc))
            )
    return results
