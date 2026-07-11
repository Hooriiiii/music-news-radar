import datetime as dt

from sqlalchemy import func, select

from app.models import Article, Source, SourceType
from app.pipeline.retention import purge_old_articles

UTC = dt.timezone.utc


def _src(db):
    src = Source(name="Feed", type=SourceType.RSS, url="https://example.com/f")
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, published_days_ago=None, fetched_days_ago=0):
    now = dt.datetime.now(UTC)
    db.add(Article(
        source_id=src.id, url=f"https://example.com/{n}", title=f"T{n}",
        raw_hash=f"{n:064d}",
        published_at=None if published_days_ago is None
        else now - dt.timedelta(days=published_days_ago),
        fetched_at=now - dt.timedelta(days=fetched_days_ago),
    ))
    db.commit()


def _remaining(db):
    return db.scalar(select(func.count(Article.id)))


def test_purge_deletes_by_post_date(db_session):
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=20)  # vieux -> supprimé
    _art(db_session, src, n=2, published_days_ago=5)   # récent -> gardé
    assert purge_old_articles(db_session, retention_days=14) == 1
    assert _remaining(db_session) == 1


def test_purge_ignores_fetch_date_for_dated_articles(db_session):
    src = _src(db_session)
    # publié il y a 20j mais ajouté aujourd'hui -> supprimé (la date de post prime)
    _art(db_session, src, n=1, published_days_ago=20, fetched_days_ago=0)
    assert purge_old_articles(db_session, retention_days=14) == 1
    assert _remaining(db_session) == 0


def test_purge_null_published_falls_back_to_fetch_date(db_session):
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=None, fetched_days_ago=20)  # supprimé
    _art(db_session, src, n=2, published_days_ago=None, fetched_days_ago=2)   # gardé
    assert purge_old_articles(db_session, retention_days=14) == 1
    assert _remaining(db_session) == 1


def test_purge_returns_zero_when_all_recent(db_session):
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=1)
    assert purge_old_articles(db_session, retention_days=14) == 0
    assert _remaining(db_session) == 1
