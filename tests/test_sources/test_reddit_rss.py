import datetime as dt

from app.models import Source, SourceType
from app.sources.reddit_rss import RedditRssAdapter

ATOM_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <title>r/electronicmusic</title>
  <entry>
    <title>New Daft Punk documentary announced</title>
    <link href="https://www.reddit.com/r/electronicmusic/comments/abc123/new_daft_punk/"/>
    <updated>2026-07-02T08:00:00+00:00</updated>
    <content type="html">&lt;div&gt;submitted by u/someone&lt;/div&gt;</content>
    <media:thumbnail url="https://b.thumbs.redditmedia.com/xyz.jpg"/>
  </entry>
</feed>
"""


def make_adapter(url="https://www.reddit.com/r/electronicmusic"):
    source = Source(id=1, name="r/electronicmusic", type=SourceType.REDDIT_RSS, url=url)
    return RedditRssAdapter(source)


def test_feed_url_appends_rss_suffix():
    assert make_adapter().feed_url == "https://www.reddit.com/r/electronicmusic.rss"


def test_feed_url_handles_trailing_slash():
    adapter = make_adapter("https://www.reddit.com/r/techno/")
    assert adapter.feed_url == "https://www.reddit.com/r/techno.rss"


def test_feed_url_keeps_existing_rss_suffix():
    adapter = make_adapter("https://www.reddit.com/r/techno.rss")
    assert adapter.feed_url == "https://www.reddit.com/r/techno.rss"


def test_reddit_uses_descriptive_user_agent():
    # Reddit renvoie des 429 sur les User-Agents génériques
    assert "music-news-radar" in make_adapter().user_agent


def test_parse_atom_entry():
    items = make_adapter().parse(ATOM_FIXTURE)
    assert len(items) == 1
    item = items[0]
    assert item.title == "New Daft Punk documentary announced"
    assert item.url == "https://www.reddit.com/r/electronicmusic/comments/abc123/new_daft_punk/"
    assert item.published_at == dt.datetime(2026, 7, 2, 8, 0, tzinfo=dt.timezone.utc)
    assert item.media_urls == ["https://b.thumbs.redditmedia.com/xyz.jpg"]
    assert "submitted by" in item.summary


def test_user_agent_is_ascii_encodable():
    make_adapter().user_agent.encode("ascii")


def test_reddit_has_politeness_delay():
    # Deux fetchs Reddit coup sur coup déclenchent un 429 qui persiste plusieurs
    # minutes -- chaque requête Reddit doit être espacée.
    assert make_adapter().request_delay >= 2
