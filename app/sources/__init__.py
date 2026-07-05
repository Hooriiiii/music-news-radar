from app.models import Source, SourceType
from app.sources.base import RawItem, SourceAdapter
from app.sources.reddit_rss import RedditRssAdapter
from app.sources.rss import RssAdapter
from app.sources.x_apify import XApifyAdapter

# Registry : le champ sources.type en base pointe vers la classe adapter.
# Ajouter une source d'un type déjà supporté = un simple INSERT en base.
ADAPTERS: dict[SourceType, type[SourceAdapter]] = {
    SourceType.RSS: RssAdapter,
    SourceType.REDDIT_RSS: RedditRssAdapter,
    SourceType.X: XApifyAdapter,
}


def get_adapter(source: Source) -> SourceAdapter:
    adapter_cls = ADAPTERS.get(source.type)
    if adapter_cls is None:
        raise NotImplementedError(f"Pas d'adapter pour le type de source {source.type!r}")
    return adapter_cls(source)


__all__ = [
    "ADAPTERS",
    "RawItem",
    "RedditRssAdapter",
    "RssAdapter",
    "SourceAdapter",
    "XApifyAdapter",
    "get_adapter",
]
