import datetime as dt

from app.models import Article, Source, SourceType
from app.pipeline.cluster import link_duplicates, title_similarity

UTC = dt.timezone.utc


def _src(db, name):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.example/feed")
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, title, hours_ago=1):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.example/{n}",
        title=title,
        raw_hash=f"{n:064d}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago),
    )
    db.add(art)
    db.commit()
    return art


# -- similarité de titres -------------------------------------------------


def test_same_story_different_wording_is_similar():
    assert title_similarity(
        "Charlotte de Witte announces new EP on KNTXT",
        "Charlotte de Witte annonce un nouvel EP sur KNTXT",
    ) >= 0.6


def test_same_story_with_source_suffix_is_similar():
    assert title_similarity(
        "Astropolis 2027 devoile sa premiere vague d'artistes",
        "Astropolis 2027 : premiere vague d'artistes devoilee - Tsugi",
    ) >= 0.6


def test_different_stories_about_same_artist_are_not_similar():
    assert title_similarity(
        "Charlotte de Witte announces new EP on KNTXT",
        "Charlotte de Witte joue a Fort Boyard cet ete",
    ) < 0.6


def test_unrelated_titles_are_not_similar():
    assert title_similarity(
        "Les meilleurs festivals techno de l'ete 2026",
        "Aphex Twin ressort un album de 1994",
    ) < 0.6


# -- liaison en base ------------------------------------------------------


def test_links_duplicate_across_sources_to_the_earliest(db_session):
    mixmag = _src(db_session, "mixmag")
    tsugi = _src(db_session, "tsugi")
    first = _art(db_session, mixmag, n=1, title="Dour Festival unveils its 2027 lineup",
                 hours_ago=5)
    second = _art(db_session, tsugi, n=2, title="Dour Festival devoile son line-up 2027",
                  hours_ago=2)

    assert link_duplicates(db_session) == 1
    db_session.refresh(first)
    db_session.refresh(second)
    assert first.duplicate_of_id is None  # le plus ancien reste le representant
    assert second.duplicate_of_id == first.id


def test_leaves_distinct_stories_alone(db_session):
    src = _src(db_session, "feed")
    a = _art(db_session, src, n=1, title="Boris Brejcha annonce une date aux Arenes de Frejus")
    b = _art(db_session, src, n=2, title="Les sorties electro de la semaine")

    assert link_duplicates(db_session) == 0
    db_session.refresh(a)
    db_session.refresh(b)
    assert a.duplicate_of_id is None and b.duplicate_of_id is None


def test_is_idempotent(db_session):
    mixmag = _src(db_session, "mixmag")
    tsugi = _src(db_session, "tsugi")
    _art(db_session, mixmag, n=1, title="Dour Festival unveils its 2027 lineup", hours_ago=5)
    _art(db_session, tsugi, n=2, title="Dour Festival devoile son line-up 2027", hours_ago=2)

    assert link_duplicates(db_session) == 1
    assert link_duplicates(db_session) == 0  # deja lie -> plus rien a faire


def test_third_copy_points_to_the_same_representative(db_session):
    a_src, b_src, c_src = (_src(db_session, n) for n in ("a", "b", "c"))
    head = _art(db_session, a_src, n=1, title="Dour Festival unveils its 2027 lineup",
                hours_ago=6)
    second = _art(db_session, b_src, n=2, title="Dour Festival devoile son line-up 2027",
                  hours_ago=4)
    third = _art(db_session, c_src, n=3, title="Dour Festival : le line-up 2027 est tombe",
                 hours_ago=2)

    assert link_duplicates(db_session) == 2
    db_session.refresh(second)
    db_session.refresh(third)
    # pas de chaine : tout pointe vers le representant, jamais vers un doublon
    assert second.duplicate_of_id == head.id
    assert third.duplicate_of_id == head.id


def test_ignores_articles_outside_the_window(db_session):
    a_src = _src(db_session, "a")
    b_src = _src(db_session, "b")
    _art(db_session, a_src, n=1, title="Dour Festival unveils its 2027 lineup", hours_ago=200)
    _art(db_session, b_src, n=2, title="Dour Festival devoile son line-up 2027", hours_ago=1)

    assert link_duplicates(db_session, window_hours=48) == 0
