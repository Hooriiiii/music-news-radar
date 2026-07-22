import datetime as dt

from app.models import Article, Artist, ArtistMention, Source, SourceType
from app.pipeline.artists import record_mentions, rising_artists
from app.pipeline.retention import purge_old_articles

UTC = dt.timezone.utc


def _src(db, name="Feed", genre="electronic"):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.test/f", genre=genre)
    db.add(src)
    db.commit()
    return src


def _scored(db, src, *, n, artists, emergence=60, relevance=50, days_ago=0):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.test/{n}",
        title=f"Titre {n}",
        raw_hash=f"{n:064d}",
        relevance_score=relevance,
        emergence_score=emergence,
        mentioned_artists=artists,
        published_at=dt.datetime.now(UTC) - dt.timedelta(days=days_ago),
    )
    db.add(art)
    db.commit()
    return art


def test_creates_artist_and_mention(db_session):
    src = _src(db_session)
    article = _scored(db_session, src, n=1, artists=["Peggy Gou"])

    assert record_mentions(db_session) == 1
    artist = db_session.query(Artist).one()
    assert artist.name == "Peggy Gou"
    assert artist.mentions_count == 1
    mention = db_session.query(ArtistMention).one()
    assert mention.artist_id == artist.id
    assert mention.article_id == article.id
    assert mention.emergence_score == 60


def test_same_artist_across_articles_is_one_row(db_session):
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Peggy Gou"], days_ago=5)
    _scored(db_session, src, n=2, artists=["peggy gou"])  # casse différente

    assert record_mentions(db_session) == 2
    artist = db_session.query(Artist).one()
    assert artist.mentions_count == 2
    # la première apparition est conservée, la dernière est mise à jour
    assert artist.first_seen_at < artist.last_seen_at


def test_keeps_the_highest_emergence_seen(db_session):
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Nouveau Venu"], emergence=40)
    _scored(db_session, src, n=2, artists=["Nouveau Venu"], emergence=85)

    record_mentions(db_session)
    assert db_session.query(Artist).one().max_emergence == 85


def test_is_idempotent(db_session):
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Peggy Gou"])

    assert record_mentions(db_session) == 1
    assert record_mentions(db_session) == 0
    assert db_session.query(Artist).one().mentions_count == 1


def test_ignores_unscored_articles(db_session):
    src = _src(db_session)
    art = _scored(db_session, src, n=1, artists=["Peggy Gou"])
    art.relevance_score = None
    art.emergence_score = None
    db_session.commit()

    assert record_mentions(db_session) == 0


def test_mentions_survive_the_purge(db_session):
    """Le point de tout l'exercice : l'article est éphémère, l'historique de
    l'artiste est l'actif du label et ne doit jamais être purgé."""
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Peggy Gou"], days_ago=60)
    record_mentions(db_session)

    purge_old_articles(db_session, retention_days=14)

    assert db_session.query(Article).count() == 0
    assert db_session.query(Artist).one().name == "Peggy Gou"
    assert db_session.query(ArtistMention).count() == 1


def test_rising_artists_ranks_by_recent_mentions(db_session):
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Montant"], days_ago=1)
    _scored(db_session, src, n=2, artists=["Montant"], days_ago=2)
    _scored(db_session, src, n=3, artists=["Discret"], days_ago=1)
    _scored(db_session, src, n=4, artists=["Ancien"], days_ago=90)
    record_mentions(db_session)

    names = [a.name for a in rising_artists(db_session, window_days=30)]
    assert names[0] == "Montant"
    assert "Ancien" not in names  # hors fenêtre


def test_rising_artists_can_filter_on_emergence(db_session):
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Jeune Pousse"], emergence=80)
    _scored(db_session, src, n=2, artists=["Star Etablie"], emergence=5)
    record_mentions(db_session)

    names = [a.name for a in rising_artists(db_session, min_emergence=50)]
    assert names == ["Jeune Pousse"]
