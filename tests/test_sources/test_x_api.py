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
            "public_metrics": {"like_count": 340, "reply_count": 25, "retweet_count": 40},
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
        "users": [{"id": "111", "username": "technofan", "verified": False}],
        "media": [{"media_key": "3_aaa", "type": "photo",
                   "url": "https://pbs.twimg.com/media/aaa.jpg"}],
    },
    "meta": {"newest_id": "1900000000000000001", "result_count": 2},
}


def make_search_payload(*, verified, likes, replies=0):
    return {
        "data": [{
            "id": "1900000000000000009",
            "text": "clip de la mainstage #rave",
            "created_at": "2026-07-06T08:00:00.000Z",
            "author_id": "42",
            "attachments": {"media_keys": ["m1"]},
            "public_metrics": {"like_count": likes, "reply_count": replies},
        }],
        "includes": {
            "users": [{"id": "42", "username": "raver", "verified": verified}],
            "media": [{"media_key": "m1", "type": "video",
                       "preview_image_url": "https://pbs.twimg.com/vid_thumb.jpg"}],
        },
        "meta": {"newest_id": "1900000000000000009", "result_count": 1},
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


# --- Sélection par popularité : vérifié OU engagement suffisant ---


def test_search_params_request_metrics_and_relevancy_sort(x_config):
    params = make_adapter("#techno").build_search_params({})
    assert "public_metrics" in params["tweet.fields"]
    assert "verified" in params["user.fields"]
    assert params["sort_order"] == "relevancy"


def test_popularity_keeps_verified_even_without_engagement(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_search_min_likes", 20)
    payload = make_search_payload(verified=True, likes=2)
    assert len(make_adapter("#rave").parse_search(payload)) == 1


def test_popularity_keeps_viral_unverified(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_search_min_likes", 20)
    payload = make_search_payload(verified=False, likes=250)
    items = make_adapter("#rave").parse_search(payload)
    assert len(items) == 1
    # la vignette de la vidéo est bien récupérée comme média
    assert items[0].media_urls == ["https://pbs.twimg.com/vid_thumb.jpg"]


def test_popularity_drops_unverified_low_engagement(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_search_min_likes", 20)
    monkeypatch.setattr(settings, "x_search_min_replies", 5)
    payload = make_search_payload(verified=False, likes=3, replies=1)
    assert make_adapter("#rave").parse_search(payload) == []


def test_metrics_are_exposed_to_scoring_via_summary(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_search_min_likes", 20)
    payload = make_search_payload(verified=False, likes=250, replies=30)
    item = make_adapter("#rave").parse_search(payload)[0]
    assert "250 likes" in item.summary
    assert "30 réponses" in item.summary


# --- Mode UGC : l'authenticité se détecte par la taille du compte ---


def make_ugc_payload(*, followers, likes, verified=False):
    return {
        "data": [{
            "id": "1900000000000000042",
            "text": "GUETTA C'ETAIT FOU HIER SOIR 😭🔥",
            "created_at": "2026-07-06T02:00:00.000Z",
            "author_id": "77",
            "attachments": {"media_keys": ["m7"]},
            "public_metrics": {"like_count": likes, "reply_count": 3},
        }],
        "includes": {
            "users": [{"id": "77", "username": "clubber_33", "verified": verified,
                       "public_metrics": {"followers_count": followers}}],
            "media": [{"media_key": "m7", "type": "video",
                       "preview_image_url": "https://pbs.twimg.com/vid7.jpg"}],
        },
        "meta": {"newest_id": "1900000000000000042", "result_count": 1},
    }


def test_ugc_prefix_detection_and_query_strip(x_config):
    adapter = make_adapter("ugc:(\"david guetta\") has:videos")
    assert adapter.is_search
    assert adapter.is_ugc
    assert not adapter.build_search_query().startswith("ugc:")
    assert not make_adapter("#techno").is_ugc


def test_ugc_keeps_viral_clip_from_small_account(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_search_min_likes", 50)
    monkeypatch.setattr(settings, "x_ugc_max_followers", 25000)
    adapter = make_adapter('ugc:("david guetta") has:videos')
    items = adapter.parse_search(make_ugc_payload(followers=420, likes=800))
    assert len(items) == 1


def test_ugc_rejects_big_media_account_even_if_viral(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_ugc_max_followers", 25000)
    adapter = make_adapter('ugc:("david guetta") has:videos')
    items = adapter.parse_search(make_ugc_payload(followers=300000, likes=5000))
    assert items == []  # agrégateur/média : pas de l'UGC


def test_ugc_rejects_small_account_without_engagement(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_search_min_likes", 50)
    adapter = make_adapter('ugc:("david guetta") has:videos')
    assert adapter.parse_search(make_ugc_payload(followers=420, likes=4)) == []


def test_ugc_verified_small_account_still_passes(x_config, monkeypatch):
    # un fan abonné Premium reste un fan : verified n'exclut pas
    monkeypatch.setattr(settings, "x_search_min_likes", 50)
    monkeypatch.setattr(settings, "x_ugc_max_followers", 25000)
    adapter = make_adapter('ugc:("david guetta") has:videos')
    items = adapter.parse_search(make_ugc_payload(followers=900, likes=200, verified=True))
    assert len(items) == 1


def test_followers_count_exposed_to_scoring(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_search_min_likes", 50)
    adapter = make_adapter('ugc:("david guetta") has:videos')
    item = adapter.parse_search(make_ugc_payload(followers=420, likes=800))[0]
    assert "420 abonnés" in item.summary


# --- Radar maison : source X dynamique pilotée par les artistes du moment ---


def test_radar_detection(x_config):
    adapter = make_adapter("radar:")
    assert adapter.is_radar
    assert adapter.is_search
    assert not make_adapter("#techno").is_radar
    assert not make_adapter("https://x.com/residentadvisor").is_radar


def test_radar_query_built_from_artists(x_config):
    adapter = make_adapter("radar:")
    adapter.radar_artists = ["Fred again..", "Peggy Gou"]
    query = adapter.build_search_query()
    assert '"Fred again.."' in query
    assert '"Peggy Gou"' in query
    assert " OR " in query
    assert "has:videos" in query
    assert "-is:retweet" in query


def test_radar_empty_artists_yields_empty_query(x_config):
    adapter = make_adapter("radar:")
    adapter.radar_artists = []
    assert adapter.build_search_query() == ""


def test_radar_fetch_skips_api_when_cold_start(x_config, monkeypatch):
    adapter = make_adapter("radar:")
    adapter.radar_artists = []
    calls = []
    monkeypatch.setattr(adapter, "_get", lambda path, params: calls.append(path) or {})
    assert adapter.fetch() == []
    assert calls == []  # aucun appel payant au démarrage à froid


def test_radar_default_artists_empty(x_config):
    assert make_adapter("radar:").radar_artists == []


def test_radar_requires_artist_in_text(x_config):
    # Un tweet viral qui matche la requête mais ne mentionne pas l'artiste
    # (spam) doit être écarté, même avec de l'engagement
    adapter = make_adapter("radar:")
    adapter.radar_artists = ["Dom Dolla"]
    payload = {
        "data": [
            {"id": "1", "text": "Dom Dolla incroyable au festival hier", "created_at": "2026-07-06T10:00:00.000Z",
             "author_id": "1", "attachments": {"media_keys": ["m1"]},
             "public_metrics": {"like_count": 200}},
            {"id": "2", "text": "check my onlyfans link in bio 🔥", "created_at": "2026-07-06T10:00:00.000Z",
             "author_id": "2", "attachments": {"media_keys": ["m2"]},
             "public_metrics": {"like_count": 5000}},
        ],
        "includes": {
            "users": [{"id": "1", "username": "fan"}, {"id": "2", "username": "spam"}],
            "media": [{"media_key": "m1", "type": "video", "preview_image_url": "https://p/1.jpg"},
                      {"media_key": "m2", "type": "video", "preview_image_url": "https://p/2.jpg"}],
        },
    }
    items = adapter.parse_search(payload)
    assert len(items) == 1
    assert "Dom Dolla" in items[0].summary


# --- Backfill borné : on ne lit (paie) que les derniers jours au premier fetch ---


def test_timeline_first_fetch_bounded_by_start_time(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_backfill_days", 3)
    params = make_adapter().build_params({})
    assert "start_time" in params
    assert params["start_time"].endswith("Z")
    assert "since_id" not in params


def test_timeline_incremental_uses_since_id_not_start_time(x_config):
    params = make_adapter().build_params({"since_id": "123"})
    assert params["since_id"] == "123"
    assert "start_time" not in params  # incrémental : since_id suffit


def test_search_first_fetch_bounded_by_start_time(x_config, monkeypatch):
    monkeypatch.setattr(settings, "x_backfill_days", 3)
    params = make_adapter("#techno").build_search_params({})
    assert "start_time" in params
    assert "since_id" not in params


def test_search_incremental_uses_since_id_not_start_time(x_config):
    params = make_adapter("#techno").build_search_params({"since_id": "99"})
    assert params["since_id"] == "99"
    assert "start_time" not in params
