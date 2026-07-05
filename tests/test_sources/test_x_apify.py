import datetime as dt

import pytest

from app.config import settings
from app.models import Source, SourceType
from app.sources.x_apify import XApifyAdapter  # noqa: F401 -- alternative testée

UTC = dt.timezone.utc

# Extrait représentatif de la sortie de l'actor apidojo/tweet-scraper
APIFY_ITEMS = [
    {
        "id": "1810000000000000001",
        "url": "https://x.com/residentadvisor/status/1810000000000000001",
        "fullText": "Aphex Twin announces surprise album on Warp Records",
        "createdAt": "Sat Jul 04 10:30:00 +0000 2026",
        "author": {"userName": "residentadvisor", "name": "Resident Advisor"},
        "extendedEntities": {
            "media": [{"media_url_https": "https://pbs.twimg.com/media/xyz.jpg"}]
        },
    },
    {
        # item malformé (l'actor émet parfois des items de contrôle) -> ignoré
        "noResults": True,
    },
]


def make_adapter(url="https://x.com/residentadvisor", state=None):
    source = Source(id=1, name="RA sur X", type=SourceType.X, url=url, state=state)
    return XApifyAdapter(source)


@pytest.fixture()
def apify_config(monkeypatch):
    monkeypatch.setattr(settings, "apify_token", "apify_api_test")
    monkeypatch.setattr(settings, "apify_max_charge_usd", 0.25)
    monkeypatch.setattr(settings, "x_max_items", 50)
    monkeypatch.setattr(settings, "x_min_fetch_interval_hours", 6)


# --- Extraction du handle depuis l'URL de la source ---


def test_handle_from_x_url():
    assert make_adapter("https://x.com/residentadvisor").handle == "residentadvisor"


def test_handle_from_twitter_url_with_slash():
    assert make_adapter("https://twitter.com/MixmagFR/").handle == "MixmagFR"


def test_handle_from_bare_or_at_form():
    assert make_adapter("@boilerroomtv").handle == "boilerroomtv"
    assert make_adapter("boilerroomtv").handle == "boilerroomtv"


# --- Construction de l'input actor et des paramètres de run ---


def test_build_input_contains_handle_and_caps(apify_config):
    payload = make_adapter().build_input()
    assert payload["twitterHandles"] == ["residentadvisor"]
    assert payload["maxItems"] == 50
    assert payload["sort"] == "Latest"
    assert "start" not in payload  # pas d'état -> pas de filtre de date


def test_build_input_uses_since_date_from_state(apify_config):
    adapter = make_adapter(state={"since": "2026-07-04"})
    assert adapter.build_input()["start"] == "2026-07-04"


def test_run_params_include_token_and_charge_cap(apify_config):
    params = make_adapter().build_run_params()
    assert params["token"] == "apify_api_test"
    assert params["maxTotalChargeUsd"] == 0.25


def test_fetch_requires_token(apify_config, monkeypatch):
    monkeypatch.setattr(settings, "apify_token", None)
    with pytest.raises(RuntimeError, match="APIFY_TOKEN"):
        make_adapter().fetch()


# --- Parsing des tweets vers RawItem ---


def test_parse_maps_tweets_and_skips_malformed(apify_config):
    items = make_adapter().parse(APIFY_ITEMS)
    assert len(items) == 1
    tweet = items[0]
    assert tweet.url == "https://x.com/residentadvisor/status/1810000000000000001"
    assert tweet.title.startswith("@residentadvisor")
    assert "Aphex Twin" in tweet.title
    assert tweet.summary == "Aphex Twin announces surprise album on Warp Records"
    assert tweet.published_at == dt.datetime(2026, 7, 4, 10, 30, tzinfo=UTC)
    assert tweet.media_urls == ["https://pbs.twimg.com/media/xyz.jpg"]


# --- Throttle : ne pas payer un run Apify toutes les 30 minutes ---


def test_fetch_skips_when_recently_fetched(apify_config, monkeypatch):
    recent = dt.datetime.now(UTC) - dt.timedelta(hours=1)
    adapter = make_adapter(state={"last_run_at": recent.isoformat()})
    calls = []
    monkeypatch.setattr(adapter, "_run_actor", lambda payload: calls.append(payload) or [])
    assert adapter.fetch() == []
    assert calls == []  # aucun appel payant


def test_fetch_runs_when_stale_and_updates_state(apify_config, monkeypatch):
    stale = dt.datetime.now(UTC) - dt.timedelta(hours=7)
    adapter = make_adapter(state={"last_run_at": stale.isoformat()})
    monkeypatch.setattr(adapter, "_run_actor", lambda payload: APIFY_ITEMS)

    items = adapter.fetch()
    assert len(items) == 1
    assert adapter.source.state["since"] == "2026-07-04"  # date du tweet le plus récent
    assert adapter.source.state["last_run_at"] > stale.isoformat()


# (le registry pointe désormais sur XApiAdapter -- voir test_x_api.py ;
# cet adapter Apify reste testé comme alternative)
