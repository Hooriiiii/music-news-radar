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


def _flux(items):
    """Un flux RSS minimal, construit a partir de dicts."""
    corps = "".join(
        f"<item><title>{i['title']}</title><link>{i['link'].replace('&', '&amp;')}</link></item>"
        for i in items
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>t</title>{corps}</channel></rss>"
    ).encode()


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


def test_parse_extracts_media():
    items = make_adapter().parse(RSS_FIXTURE)
    # Les six cles du contrat sont toujours presentes, meme absentes de la source
    assert items[0].media == [{
        "type": "photo", "url": "https://example.com/img/aphex.jpg",
        "preview_url": None, "duration_ms": None, "width": None, "height": None,
    }]


def test_parse_handles_missing_optional_fields():
    second = make_adapter().parse(RSS_FIXTURE)[1]
    assert second.summary is None
    assert second.published_at is None
    assert second.media == []


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


# --- Images dans la description HTML ---


def test_media_lit_les_img_de_la_description():
    """Bandcamp Daily et XLR8R ne fournissent NI media_content NI enclosure :
    leur seule illustration est un <img> dans le HTML de la description. Mesure
    sur 12 flux reels : 12 % d'articles illustres en plus, sans requete reseau."""
    adapter = make_adapter()
    entry = {
        "summary": '<p>Texte</p><img src="https://cdn.fr/photo.jpg" alt="x"><p>suite</p>',
    }
    assert adapter._media(entry) == [
        {"type": "photo", "url": "https://cdn.fr/photo.jpg",
         "preview_url": None, "duration_ms": None, "width": None, "height": None}
    ]


def test_les_media_declares_priment_sur_les_img_du_texte():
    """Un media_content est choisi par l'editeur ; un <img> peut etre un logo,
    un pixel de suivi ou une pub."""
    adapter = make_adapter()
    entry = {
        "media_content": [{"url": "https://cdn.fr/officielle.jpg"}],
        "summary": '<img src="https://cdn.fr/logo.gif">',
    }
    urls = [e["url"] for e in adapter._media(entry)]
    assert urls[0] == "https://cdn.fr/officielle.jpg"


def test_les_pixels_de_suivi_sont_ecartes():
    """Un <img> de 1 pixel est un mouchard, pas une illustration -- et il
    passerait pour un media valide dans tout le reste du pipeline."""
    adapter = make_adapter()
    entry = {"summary": '<img src="https://stats.fr/p.gif?id=1" width="1" height="1">'}
    assert adapter._media(entry) == []


def test_media_lit_aussi_le_champ_content():
    adapter = make_adapter()
    entry = {"content": [{"value": '<img src="https://cdn.fr/dans-content.jpg">'}]}
    assert [e["url"] for e in adapter._media(entry)] == ["https://cdn.fr/dans-content.jpg"]


# --- Redirecteurs : l'URL stockee doit etre celle de l'EDITEUR ---


def test_un_lien_bing_est_decode_vers_l_article_reel():
    """Les recherches d'actualite passent par un redirecteur. Bing porte l'URL
    reelle dans son parametre `url=` -- contrairement a Google News, dont le lien
    est opaque et mene a une page de consentement.

    Sans ce decodage, `articles.url` pointerait vers bing.com : illustration
    impossible (pas d'og:image), et un post citerait Bing au lieu d'Ouest-France.
    """
    adapter = make_adapter()
    lien = (
        "http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&tid=abc"
        "&url=https%3A%2F%2Fwww.ouest-france.fr%2Ffestivals%2Farticle.html&c=1"
    )
    items = adapter.parse(_flux([{"link": lien, "title": "Festival"}]))
    assert items[0].url == "https://www.ouest-france.fr/festivals/article.html"


def test_une_url_normale_n_est_pas_touchee():
    adapter = make_adapter()
    items = adapter.parse(_flux([{"link": "https://tsugi.fr/actu/1", "title": "t"}]))
    assert items[0].url == "https://tsugi.fr/actu/1"


def test_un_redirecteur_sans_url_exploitable_reste_tel_quel():
    """Mieux vaut un lien de redirection qu'aucun lien : l'article existe, il
    sera simplement mal illustre."""
    adapter = make_adapter()
    lien = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&tid=abc"
    items = adapter.parse(_flux([{"link": lien, "title": "t"}]))
    assert items[0].url == lien


def test_un_parametre_url_non_http_est_refuse():
    """Un `url=` qui ne commence pas par http est soit une relative, soit une
    tentative d'injection : on garde le lien d'origine."""
    adapter = make_adapter()
    lien = "http://www.bing.com/news/apiclick.aspx?url=javascript%3Aalert(1)"
    items = adapter.parse(_flux([{"link": lien, "title": "t"}]))
    assert items[0].url == lien
