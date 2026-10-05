import datetime as dt

import pytest
from sqlalchemy import select

from app.config import settings
from app.delivery.digest import build_digest, render_digest, select_digest_articles
from app.models import Article, Digest, Source, SourceType

UTC = dt.timezone.utc


@pytest.fixture()
def source(db_session):
    src = Source(name="Feed", type=SourceType.RSS, url="https://example.com/f")
    db_session.add(src)
    db_session.commit()
    return src


def add_article(db, source, *, slug, relevance=70, hotness=30, fetched=None, title=None):
    article = Article(
        source_id=source.id,
        url=f"https://example.com/{slug}",
        title=title or f"Article {slug}",
        summary=f"Résumé de {slug}.",
        raw_hash=(slug * 64)[:64],
        relevance_score=relevance,
        hotness_score=hotness,
        fetched_at=fetched or dt.datetime(2026, 7, 5, 8, 0, tzinfo=UTC),
        published_at=dt.datetime(2026, 7, 5, 7, 0, tzinfo=UTC),
    )
    db.add(article)
    db.commit()
    return article


def test_select_filters_on_relevance_threshold_and_window(db_session, source):
    add_article(db_session, source, slug="in", relevance=60)
    add_article(db_session, source, slug="low", relevance=59)
    add_article(db_session, source, slug="old", relevance=90,
                fetched=dt.datetime(2026, 7, 1, 8, 0, tzinfo=UTC))
    since = dt.datetime(2026, 7, 4, 0, 0, tzinfo=UTC)

    picked = select_digest_articles(db_session, since=since)
    assert [a.url for a in picked] == ["https://example.com/in"]


def test_select_orders_by_relevance_then_hotness(db_session, source):
    add_article(db_session, source, slug="b", relevance=70, hotness=90)
    add_article(db_session, source, slug="a", relevance=90, hotness=10)
    add_article(db_session, source, slug="c", relevance=70, hotness=20)
    since = dt.datetime(2026, 7, 4, 0, 0, tzinfo=UTC)

    picked = select_digest_articles(db_session, since=since)
    assert [a.url.rsplit("/", 1)[1] for a in picked] == ["a", "b", "c"]


def test_build_digest_creates_row_with_article_ids(db_session, source):
    a1 = add_article(db_session, source, slug="a")
    a2 = add_article(db_session, source, slug="b")
    digest, articles, created = build_digest(db_session, for_date=dt.date(2026, 7, 5))
    assert created
    assert digest.date == dt.date(2026, 7, 5)
    assert sorted(digest.article_ids) == sorted([a1.id, a2.id])
    assert digest.sent_at is None
    assert {a.id for a in articles} == {a1.id, a2.id}


def test_build_digest_is_idempotent_for_same_date(db_session, source):
    add_article(db_session, source, slug="a")
    build_digest(db_session, for_date=dt.date(2026, 7, 5))
    digest2, _, created2 = build_digest(db_session, for_date=dt.date(2026, 7, 5))
    assert not created2
    assert len(db_session.scalars(select(Digest)).all()) == 1


def test_build_digest_excludes_previous_digest_articles(db_session, source):
    seen = add_article(db_session, source, slug="seen",
                       fetched=dt.datetime(2026, 7, 4, 9, 0, tzinfo=UTC))
    db_session.add(Digest(date=dt.date(2026, 7, 4), article_ids=[seen.id]))
    db_session.commit()
    fresh = add_article(db_session, source, slug="fresh",
                        fetched=dt.datetime(2026, 7, 4, 23, 0, tzinfo=UTC))

    digest, articles, _ = build_digest(db_session, for_date=dt.date(2026, 7, 5))
    assert digest.article_ids == [fresh.id]


def test_build_digest_with_persist_false_writes_nothing(db_session, source):
    add_article(db_session, source, slug="a")
    _, articles, _ = build_digest(db_session, for_date=dt.date(2026, 7, 5), persist=False)
    assert len(articles) == 1
    assert db_session.scalars(select(Digest)).all() == []


def test_build_digest_caps_article_count(db_session, source, monkeypatch):
    monkeypatch.setattr(settings, "digest_max_articles", 2)
    for slug in ("a", "b", "c"):
        add_article(db_session, source, slug=slug)
    _, articles, _ = build_digest(db_session, for_date=dt.date(2026, 7, 5))
    assert len(articles) == 2


def test_render_digest_contains_titles_links_and_hot_section(db_session, source):
    hot = add_article(db_session, source, slug="hot", relevance=90, hotness=85,
                      title="Une légende de la house est morte")
    cold = add_article(db_session, source, slug="cold", relevance=70, hotness=20,
                       title="Nouvel album annoncé")
    html = render_digest(dt.date(2026, 7, 5), [hot, cold])
    assert "Une légende de la house est morte" in html
    assert "Nouvel album annoncé" in html
    assert "https://example.com/hot" in html
    assert "chaud" in html.lower()  # section hot présente
    assert "05/07/2026" in html


def test_digest_excludes_freshly_fetched_but_old_content(db_session, source):
    # Une source nouvellement ajoutée peut back-fill de vieux articles :
    # fetchés aujourd'hui mais publiés il y a longtemps -> pas dans le digest
    old = add_article(db_session, source, slug="vieux", relevance=90)
    old.published_at = dt.datetime(2016, 8, 13, tzinfo=UTC)
    undated = add_article(db_session, source, slug="sansdate", relevance=70)
    undated.published_at = None
    db_session.commit()
    since = dt.datetime(2026, 7, 4, 0, 0, tzinfo=UTC)

    picked = select_digest_articles(db_session, since=since)
    slugs = [a.url.rsplit("/", 1)[1] for a in picked]
    assert "vieux" not in slugs
    assert "sansdate" in slugs  # les flux sans dates restent éligibles


def test_digest_caps_articles_per_source(db_session, source, monkeypatch):
    # Une source prolifique (type Mixmag) ne doit pas monopoliser le digest
    monkeypatch.setattr(settings, "digest_max_per_source", 2)
    other = Source(name="Autre", type=SourceType.RSS, url="https://example.com/o")
    db_session.add(other)
    db_session.commit()
    for i, rel in enumerate((95, 94, 93, 92)):
        add_article(db_session, source, slug=f"m{i}", relevance=rel)
    weaker = Article(
        source_id=other.id, url="https://example.com/autre", title="Autre source",
        summary="x", raw_hash="o" * 64, relevance_score=70,
        fetched_at=dt.datetime(2026, 7, 5, 8, 0, tzinfo=UTC),
        published_at=dt.datetime(2026, 7, 5, 7, 0, tzinfo=UTC),
    )
    db_session.add(weaker)
    db_session.commit()
    since = dt.datetime(2026, 7, 4, 0, 0, tzinfo=UTC)

    picked = select_digest_articles(db_session, since=since)
    by_source = [a.source_id for a in picked]
    assert by_source.count(source.id) == 2  # plafonné
    assert other.id in by_source  # la petite source entre malgré ses 70
    # l'ordre global reste par pertinence décroissante
    assert [a.relevance_score for a in picked] == sorted(
        [a.relevance_score for a in picked], reverse=True)
