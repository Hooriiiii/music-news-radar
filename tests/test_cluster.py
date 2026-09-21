import datetime as dt

from app.config import settings
from app.models import Article, ArticleStatus, Source, SourceType
from app.pipeline.cluster import link_duplicates, title_similarity

UTC = dt.timezone.utc


def _src(db, name):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.example/feed")
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, title, hours_ago=1, status=ArticleStatus.NEW):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.example/{n}",
        title=title,
        raw_hash=f"{n:064d}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago),
        status=status,
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


# -- archive : une actu deja publiee ne ressort jamais ---------------------


def test_a_used_article_outside_the_window_catches_a_fresh_repeat(db_session):
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    published = _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
                     hours_ago=5 * 24, status=ArticleStatus.USED)
    repeat = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                 hours_ago=1)

    assert link_duplicates(db_session) == 1
    db_session.refresh(published)
    db_session.refresh(repeat)
    assert repeat.duplicate_of_id == published.id


def test_a_used_representative_is_never_marked_a_duplicate_itself(db_session):
    # L'archive ne pretend jamais qu'une actu deja publiee est le doublon d'une
    # autre plus recente -- elle ne sert que de representant possible.
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    published = _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
                     hours_ago=5 * 24, status=ArticleStatus.USED)
    _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027", hours_ago=1)

    link_duplicates(db_session)
    db_session.refresh(published)
    assert published.duplicate_of_id is None


def test_archive_window_bounds_the_comparison(db_session, monkeypatch):
    monkeypatch.setattr(settings, "cluster_archive_jours", 30)
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
         hours_ago=40 * 24, status=ArticleStatus.USED)
    repeat = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                 hours_ago=1)

    assert link_duplicates(db_session) == 0
    db_session.refresh(repeat)
    assert repeat.duplicate_of_id is None


def test_archive_candidates_are_tried_oldest_first(db_session):
    # Tour de correction 1/5 : la requete sur l'archive doit trier par date
    # (comme celle sur les articles frais), sinon le representant retenu
    # depend de l'ordre physique des lignes -- non garanti par SQL, encore
    # moins par Postgres/Neon en production. Insertion dans l'ordre qui
    # echouait : le plus RECENT (4j) prend l'id le plus PETIT, le plus ANCIEN
    # (9j, "vu le plus tot") prend l'id le plus grand.
    src_recent = _src(db_session, "recent-src")
    src_old = _src(db_session, "old-src")
    new_src = _src(db_session, "tsugi")
    _art(db_session, src_recent, n=1, title="Astropolis 2027 devoile son line-up",
         hours_ago=4 * 24, status=ArticleStatus.USED)
    plus_ancien = _art(db_session, src_old, n=2, title="Astropolis 2027 : le line-up est devoile",
                       hours_ago=9 * 24, status=ArticleStatus.USED)
    repeat = _art(db_session, new_src, n=3, title="Astropolis 2027 : line-up devoile aujourd'hui",
                 hours_ago=1)

    link_duplicates(db_session)
    db_session.refresh(repeat)
    assert repeat.duplicate_of_id == plus_ancien.id


def test_archive_matching_is_idempotent(db_session):
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
         hours_ago=5 * 24, status=ArticleStatus.USED)
    _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027", hours_ago=1)

    assert link_duplicates(db_session) == 1
    assert link_duplicates(db_session) == 0


def test_a_used_article_that_is_itself_a_duplicate_is_not_reused_as_a_representative(db_session):
    # Le representant racine du groupe archive doit etre atteint, jamais un
    # membre archive qui pointe deja vers un autre.
    root_src = _src(db_session, "mixmag")
    other_src = _src(db_session, "dj-mag")
    new_src = _src(db_session, "tsugi")
    root = _art(db_session, root_src, n=1, title="Dour Festival unveils its 2027 lineup",
               hours_ago=6 * 24, status=ArticleStatus.USED)
    already_linked = _art(db_session, other_src, n=2, title="Dour Festival line-up 2027 revealed",
                          hours_ago=5 * 24, status=ArticleStatus.USED)
    already_linked.duplicate_of_id = root.id
    db_session.commit()
    repeat = _art(db_session, new_src, n=3, title="Dour Festival devoile son line-up 2027",
                 hours_ago=1)

    link_duplicates(db_session)
    db_session.refresh(repeat)
    assert repeat.duplicate_of_id == root.id
