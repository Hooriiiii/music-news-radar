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


# --- Extensions pour le dashboard ---


def test_patch_article_status(client, db_session):
    source = make_source(db_session)
    article = make_article(db_session, source, title="À traiter", raw_hash="patch1")

    response = client.patch(f"/articles/{article.id}", json={"status": "used"})
    assert response.status_code == 200
    assert response.json()["status"] == "used"
    db_session.refresh(article)
    assert article.status == ArticleStatus.USED


def test_patch_article_rejects_unknown_status(client, db_session):
    source = make_source(db_session)
    article = make_article(db_session, source, title="X", raw_hash="patch2")
    response = client.patch(f"/articles/{article.id}", json={"status": "publié"})
    assert response.status_code == 422


def test_patch_article_404(client):
    assert client.patch("/articles/99999", json={"status": "used"}).status_code == 404


def test_filter_articles_with_media_only(client, db_session):
    source = make_source(db_session)
    make_article(db_session, source, title="Avec image", raw_hash="med1")
    without = make_article(db_session, source, title="Sans image", raw_hash="med2")
    without.media_urls = None
    db_session.commit()

    titles = [a["title"] for a in client.get("/articles", params={"has_media": True}).json()]
    assert titles == ["Avec image"]


def test_filter_articles_by_category(client, db_session):
    source = make_source(db_session)
    a = make_article(db_session, source, title="Release", raw_hash="cat1")
    a.category = "new_release"
    b = make_article(db_session, source, title="Drame", raw_hash="cat2")
    b.category = "drama"
    db_session.commit()

    titles = [x["title"] for x in client.get("/articles", params={"category": "drama"}).json()]
    assert titles == ["Drame"]


def test_sort_articles_by_relevance(client, db_session):
    source = make_source(db_session)
    make_article(db_session, source, title="Moyen", raw_hash="s1", relevance=50)
    make_article(db_session, source, title="Fort", raw_hash="s2", relevance=90)
    make_article(db_session, source, title="Faible", raw_hash="s3", relevance=10)

    titles = [a["title"] for a in client.get("/articles", params={"sort": "relevance"}).json()]
    assert titles == ["Fort", "Moyen", "Faible"]


def test_list_sources(client, db_session):
    make_source(db_session)
    response = client.get("/sources")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "Test Feed"


def test_dashboard_served_at_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Music News Radar" in response.text
