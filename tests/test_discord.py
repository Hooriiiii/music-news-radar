import datetime as dt

from app.delivery.discord import build_alert_payload, send_hot_alerts
from app.models import Article, Source, SourceType

UTC = dt.timezone.utc


def add_article(db, *, slug, hotness=85, relevance=70, alerted_at=None):
    source = db.get(Source, 1)
    if source is None:
        source = Source(id=1, name="Feed", type=SourceType.RSS, url="https://example.com/f")
        db.add(source)
        db.commit()
    article = Article(
        source_id=source.id,
        url=f"https://example.com/{slug}",
        title=f"Breaking {slug}",
        summary=f"Résumé {slug}.",
        raw_hash=(slug * 64)[:64],
        relevance_score=relevance,
        hotness_score=hotness,
        alerted_at=alerted_at,
    )
    db.add(article)
    db.commit()
    return article


def test_build_alert_payload_contains_essentials(db_session):
    article = add_article(db_session, slug="a")
    payload = build_alert_payload(article)
    embed = payload["embeds"][0]
    assert embed["title"] == "Breaking a"
    assert embed["url"] == "https://example.com/a"
    assert "Résumé a." in embed["description"]


def test_send_hot_alerts_posts_and_marks(db_session):
    article = add_article(db_session, slug="a")
    posted = []
    stats = send_hot_alerts(db_session, poster=lambda a: posted.append(a.id))
    assert stats.sent == 1
    assert posted == [article.id]
    db_session.refresh(article)
    assert article.alerted_at is not None


def test_send_hot_alerts_skips_already_alerted_and_cold(db_session):
    add_article(db_session, slug="done", alerted_at=dt.datetime(2026, 7, 5, tzinfo=UTC))
    add_article(db_session, slug="cold", hotness=79)
    add_article(db_session, slug="unscored", hotness=None)
    stats = send_hot_alerts(db_session, poster=lambda a: None)
    assert stats.sent == 0


def test_send_hot_alerts_isolates_errors_and_retries_later(db_session):
    bad = add_article(db_session, slug="bad")
    good = add_article(db_session, slug="good")

    def poster(article):
        if article.id == bad.id:
            raise RuntimeError("webhook 500")

    stats = send_hot_alerts(db_session, poster=poster)
    assert stats.sent == 1
    assert stats.errors == 1
    db_session.refresh(bad)
    db_session.refresh(good)
    assert bad.alerted_at is None  # sera retenté au prochain run
    assert good.alerted_at is not None
