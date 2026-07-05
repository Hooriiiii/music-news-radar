import pytest

from app.models import Source, SourceType
from app.sources import get_adapter
from app.sources.reddit_rss import RedditRssAdapter
from app.sources.rss import RssAdapter


def make_source(type_):
    return Source(id=1, name="x", type=type_, url="https://example.com/x")


def test_registry_maps_rss_to_generic_adapter():
    adapter = get_adapter(make_source(SourceType.RSS))
    assert type(adapter) is RssAdapter


def test_registry_maps_reddit_rss_to_reddit_adapter():
    adapter = get_adapter(make_source(SourceType.REDDIT_RSS))
    assert type(adapter) is RedditRssAdapter


def test_registry_rejects_unimplemented_type():
    with pytest.raises(NotImplementedError):
        get_adapter(make_source(SourceType.BLUESKY))
