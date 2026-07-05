import datetime as dt

import pytest

from app.config import settings
from app.models import Source, SourceType
from app.sources.x_api import XApiAdapter

UTC = dt.timezone.utc

# Forme réelle de la réponse GET /2/users/:id/tweets de l'API X v2
API_PAYLOAD = {
    "data": [
        {
            "id": "1810000000000000001",
            "text": "Aphex Twin announces new album on Warp\nhttps://t.co/abc",
            "created_at": "2026-07-05T10:30:00.000Z",
            "attachments": {"media_keys": ["3_123"]},
        },
        {
            "id": "1810000000000000002",
            "text": "Line-up announcement soon",
            "created_at": "2026-07-05T09:00:00.000Z",
        },
    ],
    "includes": {
        "media": [
            {"media_key": "3_123", "type": "photo",
             "url": "https://pbs.twimg.com/media/xyz.jpg"}
        ]
    },
    "meta": {"newest_id": "1810000000000000001", "result_count": 2},
}

EMPTY_PAYLOAD = {"meta": {"result_count": 0}}


def make_adapter(url="https://x.com/residentadvisor", state=None):
    source = Source(id=1, name="RA (X)", type=SourceType.X, url=url, state=state)
    return XApiAdapter(source)


@pytest.fixture()
def x_config(monkeypatch):
    monkeypatch.setattr(settings, "x_bearer_token", "AAAA-test")
    monkeypatch.setattr(settings, "x_max_items", 50)


# --- Paramètres de requête ---


def test_build_params_defaults(x_config):
    params = make_adapter().build_params({})
    assert params["max_results"] == 50
    assert params["exclude"] == "retweets,replies"
    assert "since_id" not in params


def test_build_params_uses_since_id_from_state(x_config):
    params = make_adapter().build_params({"since_id": "1809"})
    assert params["since_id"] == "1809"


def test_fetch_requires_token(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_bearer_token", None)
    with pytest.raises(RuntimeError, match="X_BEARER_TOKEN"):
        make_adapter().fetch()


# --- Parsing ---


def test_parse_maps_tweets(x_config):
    items = make_adapter().parse(API_PAYLOAD)
    assert len(items) == 2
    first = items[0]
    assert first.url == "https://x.com/residentadvisor/status/1810000000000000001"
    assert first.title.startswith("@residentadvisor: Aphex Twin")
    assert "\n" not in first.title  # titre sur une seule ligne
    assert "Warp" in first.summary
    assert first.published_at == dt.datetime(2026, 7, 5, 10, 30, tzinfo=UTC)
    assert first.media_urls == ["https://pbs.twimg.com/media/xyz.jpg"]
    assert items[1].media_urls == []


def test_parse_empty_response(x_config):
    assert make_adapter().parse(EMPTY_PAYLOAD) == []


# --- fetch : user_id caché, since_id avancé ---


def test_fetch_caches_user_id_and_advances_since_id(x_config, monkeypatch):
    adapter = make_adapter(state={"user_id": "12345", "since_id": "1700"})
    requests = []

    def fake_get(path, params):
        requests.append((path, params))
        return API_PAYLOAD

    monkeypatch.setattr(adapter, "_get", fake_get)
    items = adapter.fetch()

    assert len(items) == 2
    assert requests[0][0] == "/users/12345/tweets"  # pas de re-lookup du user
    assert requests[0][1]["since_id"] == "1700"
    assert adapter.source.state["since_id"] == "1810000000000000001"
    assert adapter.source.state["user_id"] == "12345"


def test_fetch_resolves_user_id_once_when_unknown(x_config, monkeypatch):
    adapter = make_adapter(state=None)
    monkeypatch.setattr(adapter, "_lookup_user_id", lambda: "99")
    monkeypatch.setattr(adapter, "_get", lambda path, params: EMPTY_PAYLOAD)

    assert adapter.fetch() == []
    assert adapter.source.state["user_id"] == "99"
    assert "since_id" not in adapter.source.state  # pas de newest_id -> pas d'avancée


# --- Registry : le type x pointe désormais sur l'API officielle ---


def test_registry_maps_x_to_official_api_adapter():
    from app.sources import get_adapter

    source = Source(id=1, name="x", type=SourceType.X, url="https://x.com/user")
    assert type(get_adapter(source)) is XApiAdapter


# --- Mode recherche (hashtags) : média obligatoire, throttle, multi-auteurs ---

SEARCH_PAYLOAD = {
    "data": [
        {
            "id": "1900000000000000001",
            "text": "Nouveau set techno au Berghain #techno",
            "created_at": "2026-07-06T08:00:00.000Z",
            "author_id": "111",
            "attachments": {"media_keys": ["3_aaa"]},
        },
        {
            # pas de média -> exclu en mode recherche (a priori impossible avec
            # has:media, ceinture et bretelles)
            "id": "1900000000000000002",
            "text": "tweet texte #techno",
            "created_at": "2026-07-06T07:00:00.000Z",
            "author_id": "222",
        },
    ],
    "includes": {
        "users": [{"id": "111", "username": "technofan"}],
        "media": [{"media_key": "3_aaa", "type": "photo",
                   "url": "https://pbs.twimg.com/media/aaa.jpg"}],
    },
    "meta": {"newest_id": "1900000000000000001", "result_count": 2},
}


def test_search_mode_detection(x_config):
    assert make_adapter("#techno OR #housemusic").is_search
    assert make_adapter("search: berghain lineup").is_search
    assert not make_adapter("https://x.com/residentadvisor").is_search
    assert not make_adapter("@boilerroomtv").is_search
    assert not make_adapter("boilerroomtv").is_search


def test_search_query_enforces_media_and_excludes_noise(x_config):
    query = make_adapter("#techno OR #rave").build_search_query()
    assert "#techno OR #rave" in query
    assert "has:media" in query
    assert "-is:retweet" in query


def test_search_query_does_not_duplicate_operators(x_config):
    query = make_adapter("#techno has:media -is:retweet").build_search_query()
    assert query.count("has:media") == 1


def test_search_params_use_dedicated_cap_and_since_id(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_search_max_results", 10)
    params = make_adapter("#techno").build_search_params({"since_id": "42"})
    assert params["max_results"] == 10
    assert params["since_id"] == "42"
    assert "author_id" in params["expansions"]


def test_parse_search_requires_media_and_resolves_authors(x_config):
    items = make_adapter("#techno").parse_search(SEARCH_PAYLOAD)
    assert len(items) == 1  # le tweet sans média est écarté
    tweet = items[0]
    assert tweet.url == "https://x.com/technofan/status/1900000000000000001"
    assert tweet.title.startswith("@technofan:")
    assert tweet.media_urls == ["https://pbs.twimg.com/media/aaa.jpg"]


def test_search_fetch_is_throttled(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_min_fetch_interval_hours", 6)
    recent = dt.datetime.now(UTC) - dt.timedelta(hours=1)
    adapter = make_adapter("#techno", state={"last_run_at": recent.isoformat()})
    calls = []
    monkeypatch.setattr(adapter, "_get", lambda path, params: calls.append(path))
    assert adapter.fetch() == []
    assert calls == []  # aucune lecture payante pendant la fenêtre de throttle
