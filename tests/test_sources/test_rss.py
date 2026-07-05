import datetime as dt

from app.models import Source, SourceType
from app.sources.rss import RssAdapter

RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Test Music Feed</title>
    <item>
      <title>Aphex Twin annonce un nouvel album</title>
      <link>https://example.com/news/aphex-twin-album?utm_source=rss</link>
      <description><![CDATA[<p>Le producteur &amp; DJ annonce un album.</p>]]></description>
      <pubDate>Wed, 01 Jul 2026 10:30:00 GMT</pubDate>
      <media:content url="https://example.com/img/aphex.jpg" type="image/jpeg"/>
    </item>
    <item>
      <title>Sans date ni image</title>
      <link>https://example.com/news/no-date</link>
    </item>
  </channel>
</rss>
"""


def make_adapter():
    source = Source(id=1, name="Test", type=SourceType.RSS, url="https://example.com/feed.xml")
    return RssAdapter(source)


def test_parse_maps_entries_to_raw_items():
    items = make_adapter().parse(RSS_FIXTURE)
    assert len(items) == 2
    assert items[0].title == "Aphex Twin annonce un nouvel album"
    assert items[0].url == "https://example.com/news/aphex-twin-album?utm_source=rss"


def test_parse_strips_html_from_summary():
    items = make_adapter().parse(RSS_FIXTURE)
    assert items[0].summary == "Le producteur & DJ annonce un album."


def test_parse_extracts_published_date_as_utc():
    items = make_adapter().parse(RSS_FIXTURE)
    assert items[0].published_at == dt.datetime(2026, 7, 1, 10, 30, tzinfo=dt.timezone.utc)


def test_parse_extracts_media_urls():
    items = make_adapter().parse(RSS_FIXTURE)
    assert items[0].media_urls == ["https://example.com/img/aphex.jpg"]


def test_parse_handles_missing_optional_fields():
    second = make_adapter().parse(RSS_FIXTURE)[1]
    assert second.summary is None
    assert second.published_at is None
    assert second.media_urls == []


def test_parse_skips_entries_without_link():
    fixture = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Pas de lien</title></item>
    </channel></rss>"""
    assert make_adapter().parse(fixture) == []


def test_feed_url_is_source_url():
    assert make_adapter().feed_url == "https://example.com/feed.xml"


def test_user_agent_is_ascii_encodable():
    # Les headers HTTP n'acceptent que l'ASCII -- un accent dans le UA fait
    # planter httpx avant même l'envoi de la requête.
    make_adapter().user_agent.encode("ascii")


def test_generic_rss_has_no_politeness_delay():
    assert make_adapter().request_delay == 0
