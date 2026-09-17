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
        media=[{"type": "photo", "url": "https://example.com/img.jpg"}],
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


def test_get_article(client, db_session):
    source = make_source(db_session)
    article = make_article(db_session, source, title="Un festival annonce son line-up",
                            raw_hash="get1", relevance=70)

    response = client.get(f"/articles/{article.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == article.id
    assert body["title"] == "Un festival annonce son line-up"
    # Meme schema que la liste : le dashboard s'appuie sur `media` pour la liste
    # de medias cochables du post -- une regression de serialisation ici
    # casserait silencieusement le detail d'un post.
    assert body["media"] == [{"type": "photo", "url": "https://example.com/img.jpg"}]


def test_get_article_404(client):
    assert client.get("/articles/99999").status_code == 404


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
    without.media = None
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


# --- Gestion des sources depuis le dashboard ---


def test_create_source(client, db_session):
    from sqlalchemy import select

    from app.models import Source

    response = client.post("/sources", json={
        "name": "Fred Again — fan", "type": "x",
        "url": "https://x.com/fredagainfan", "genre": "electronic"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Fred Again — fan"
    assert body["active"] is True
    assert db_session.scalar(select(Source).where(Source.id == body["id"])) is not None


def test_create_source_rejects_invalid_type(client):
    response = client.post("/sources", json={
        "name": "x", "type": "tiktok", "url": "https://tiktok.com/x"})
    assert response.status_code == 422


def test_create_source_requires_name_and_url(client):
    assert client.post("/sources", json={"type": "rss"}).status_code == 422


def test_toggle_source_active(client, db_session):
    source = make_source(db_session)
    response = client.patch(f"/sources/{source.id}", json={"active": False})
    assert response.status_code == 200
    assert response.json()["active"] is False
    db_session.refresh(source)
    assert source.active is False


def test_patch_source_404(client):
    assert client.patch("/sources/9999", json={"active": False}).status_code == 404


# --- Endpoints A&R : la shortlist artistes ---


def _seed_artist(db, *, name, mentions=1, emergence=70, days_ago=1):
    import datetime as dt

    from app.models import Artist, ArtistMention

    now = dt.datetime.now(dt.timezone.utc)
    artist = Artist(
        name=name, slug=name.lower(), first_seen_at=now - dt.timedelta(days=days_ago),
        last_seen_at=now, mentions_count=mentions, max_emergence=emergence,
    )
    db.add(artist)
    db.commit()
    for i in range(mentions):
        db.add(ArtistMention(
            artist_id=artist.id, article_id=None, source_id=None,
            mentioned_at=now - dt.timedelta(days=days_ago),
            relevance_score=50, emergence_score=emergence,
            title=f"Mention {i}", url=f"https://example.com/{name}/{i}",
        ))
    db.commit()
    return artist


def test_list_rising_artists(client, db_session):
    _seed_artist(db_session, name="Montant", mentions=3)
    _seed_artist(db_session, name="Discret", mentions=1)
    body = client.get("/artists").json()
    assert [a["name"] for a in body] == ["Montant", "Discret"]


def test_list_rising_artists_filters_on_emergence(client, db_session):
    _seed_artist(db_session, name="Jeune", emergence=80)
    _seed_artist(db_session, name="Star", emergence=10)
    body = client.get("/artists", params={"min_emergence": 50}).json()
    assert [a["name"] for a in body] == ["Jeune"]


def test_artist_mentions_history(client, db_session):
    artist = _seed_artist(db_session, name="Suivi", mentions=2)
    body = client.get(f"/artists/{artist.id}/mentions").json()
    assert len(body) == 2


def test_artist_mentions_404(client):
    assert client.get("/artists/999/mentions").status_code == 404


def test_track_artist(client, db_session):
    artist = _seed_artist(db_session, name="Pepite")
    body = client.patch(f"/artists/{artist.id}", json={"tracked": True}).json()
    assert body["tracked"] is True


def test_le_dashboard_porte_le_panneau_de_programmation(client):
    html = client.get("/").text
    assert 'data-preset="schedule"' in html
    assert 'id="schedule-panel"' in html
    assert 'fetch("/schedule")' in html


# --- Catégorie de source (artiste / média / ...) ---


def test_source_expose_sa_categorie(client, db_session):
    make_source(db_session)
    assert client.get("/sources").json()[0]["categorie"] is None


def test_create_source_avec_categorie(client):
    response = client.post("/sources", json={
        "name": "Tsugi", "type": "instagram", "url": "ig:tsugimag", "categorie": "media"})
    assert response.status_code == 201
    assert response.json()["categorie"] == "media"


def test_patch_source_categorie(client, db_session):
    source = make_source(db_session)
    response = client.patch(f"/sources/{source.id}", json={"categorie": "artiste"})
    assert response.status_code == 200
    assert response.json()["categorie"] == "artiste"


def test_categorie_invalide_rejetee(client, db_session):
    source = make_source(db_session)
    assert client.patch(f"/sources/{source.id}", json={"categorie": "blog"}).status_code == 422


def test_patch_source_categorie_remise_a_inconnu(client, db_session):
    source = make_source(db_session)
    client.patch(f"/sources/{source.id}", json={"categorie": "media"})
    response = client.patch(f"/sources/{source.id}", json={"categorie": None})
    assert response.json()["categorie"] is None


def test_dashboard_permet_de_classer_une_source(client):
    html = client.get("/").text
    assert 'id="s-categorie"' in html
    assert 'class="categorie"' in html
