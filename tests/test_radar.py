import datetime as dt

from app.models import Article, Source, SourceType
from app.pipeline.radar import top_artists

UTC = dt.timezone.utc


def _source(db, genre="electronic"):
    src = Source(name=f"Feed {genre}", type=SourceType.RSS,
                 url=f"https://example.com/{genre}", genre=genre)
    db.add(src)
    db.commit()
    return src


def _article(db, source, *, artists, relevance, days_ago=0, n):
    db.add(Article(
        source_id=source.id, url=f"https://example.com/{n}", title=f"T{n}",
        raw_hash=f"{n:064d}", relevance_score=relevance, mentioned_artists=artists,
        fetched_at=dt.datetime.now(UTC) - dt.timedelta(days=days_ago),
    ))
    db.commit()


def test_top_artists_ranks_by_frequency(db_session):
    src = _source(db_session)
    _article(db_session, src, artists=["Fred again..", "Skrillex"], relevance=80, n=1)
    _article(db_session, src, artists=["Fred again.."], relevance=70, n=2)
    _article(db_session, src, artists=["Fred again..", "Peggy Gou"], relevance=65, n=3)
    _article(db_session, src, artists=["Peggy Gou"], relevance=60, n=4)

    ranked = top_artists(db_session, window_days=7, min_relevance=60, limit=10)
    assert ranked[0] == "Fred again.."      # 3 mentions
    assert ranked[1] == "Peggy Gou"          # 2 mentions
    assert "Skrillex" in ranked              # 1 mention


def test_top_artists_excludes_low_relevance(db_session):
    src = _source(db_session)
    _article(db_session, src, artists=["Faible"], relevance=59, n=1)
    _article(db_session, src, artists=["Fort"], relevance=60, n=2)
    assert top_artists(db_session, window_days=7, min_relevance=60, limit=10) == ["Fort"]


def test_top_artists_excludes_old(db_session):
    src = _source(db_session)
    _article(db_session, src, artists=["Vieux"], relevance=90, days_ago=10, n=1)
    _article(db_session, src, artists=["Recent"], relevance=70, days_ago=1, n=2)
    assert top_artists(db_session, window_days=7, min_relevance=60, limit=10) == ["Recent"]


def test_top_artists_respects_limit(db_session):
    src = _source(db_session)
    for i, name in enumerate(["A", "B", "C"]):
        _article(db_session, src, artists=[name], relevance=80, n=i)
    assert len(top_artists(db_session, window_days=7, min_relevance=60, limit=2)) == 2


def test_top_artists_empty_when_no_data(db_session):
    assert top_artists(db_session, window_days=7, min_relevance=60, limit=10) == []


def test_top_artists_excludes_offgenre_sources(db_session):
    # Une source festival multi-genres ne doit pas injecter d'artistes dans le radar
    electro = _source(db_session, genre="electronic")
    festival = _source(db_session, genre="festival")
    _article(db_session, electro, artists=["Dom Dolla"], relevance=80, n=1)
    _article(db_session, festival, artists=["Ultra Vomit"], relevance=80, n=2)

    ranked = top_artists(db_session, window_days=7, min_relevance=60, limit=10)
    assert ranked == ["Dom Dolla"]
    assert "Ultra Vomit" not in ranked
