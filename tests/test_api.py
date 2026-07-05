import datetime as dt

from app.models import Article, ArticleStatus, Source, SourceType


def make_source(db):
    source = Source(name="Test Feed", type=SourceType.RSS, url="https://example.com/feed.xml")
    db.add(source)
    db.commit()
    return source


def make_article(db, source, *, title, raw_hash, relevance=None, hotness=None,
                 status=ArticleStatus.NEW):
    article = Article(
        source_id=source.id,
        url=f"https://example.com/{raw_hash}",
        title=title,
        published_at=dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc),
        relevance_score=relevance,
        hotness_score=hotness,
        status=status,
        raw_hash=raw_hash,
        media_urls=["https://example.com/img.jpg"],
    )
    db.add(article)
    db.commit()
    return article


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_articles_empty(client):
    response = client.get("/articles")
    assert response.status_code == 200
    assert response.json() == []


def test_list_articles_filters(client, db_session):
    source = make_source(db_session)
    make_article(db_session, source, title="Low score", raw_hash="hash1", relevance=30)
    make_article(db_session, source, title="High score", raw_hash="hash2", relevance=85,
                 hotness=90)
    make_article(db_session, source, title="Used one", raw_hash="hash3", relevance=70,
                 status=ArticleStatus.USED)

    response = client.get("/articles")
    assert len(response.json()) == 3

    response = client.get("/articles", params={"min_relevance": 60})
    titles = [a["title"] for a in response.json()]
    assert titles == ["High score", "Used one"] or set(titles) == {"High score", "Used one"}

    response = client.get("/articles", params={"status": "new"})
    assert {a["title"] for a in response.json()} == {"Low score", "High score"}

    response = client.get("/articles", params={"min_hotness": 80})
    assert [a["title"] for a in response.json()] == ["High score"]


def test_get_digest_404(client):
    response = client.get("/digests/999")
    assert response.status_code == 404


def test_list_digests_empty(client):
    response = client.get("/digests")
    assert response.status_code == 200
    assert response.json() == []
