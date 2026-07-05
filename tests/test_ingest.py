import datetime as dt

from sqlalchemy import select

from app.models import Article, ArticleStatus, Source, SourceType
from app.pipeline.ingest import ingest_source, run_ingest
from app.sources.base import RawItem, SourceAdapter


class FakeAdapter(SourceAdapter):
    def __init__(self, source, items=None, error=None):
        super().__init__(source)
        self._items = items or []
        self._error = error

    def fetch(self):
        if self._error:
            raise self._error
        return self._items


def make_source(db, name="Feed", active=True):
    source = Source(name=name, type=SourceType.RSS, url=f"https://example.com/{name}",
                    active=active)
    db.add(source)
    db.commit()
    return source


def make_item(slug="a", title="Titre"):
    return RawItem(
        url=f"https://example.com/{slug}",
        title=title,
        summary="résumé",
        published_at=dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc),
        media_urls=["https://example.com/img.jpg"],
    )


def test_ingest_inserts_new_articles(db_session):
    source = make_source(db_session)
    adapter = FakeAdapter(source, [make_item("a"), make_item("b", "Autre")])
    stats = ingest_source(db_session, source, adapter=adapter)
    assert (stats.fetched, stats.new, stats.duplicates) == (2, 2, 0)
    articles = db_session.scalars(select(Article)).all()
    assert len(articles) == 2
    assert all(len(a.raw_hash) == 64 for a in articles)
    assert all(a.status == ArticleStatus.NEW for a in articles)
    assert articles[0].media_urls == ["https://example.com/img.jpg"]


def test_ingest_skips_already_ingested(db_session):
    source = make_source(db_session)
    adapter = FakeAdapter(source, [make_item("a")])
    ingest_source(db_session, source, adapter=adapter)
    stats = ingest_source(db_session, source, adapter=adapter)
    assert (stats.new, stats.duplicates) == (0, 1)
    assert len(db_session.scalars(select(Article)).all()) == 1


def test_ingest_dedups_url_variants_within_batch(db_session):
    source = make_source(db_session)
    items = [
        RawItem(url="https://example.com/a?utm_source=rss", title="Titre"),
        RawItem(url="https://example.com/a/", title="titre"),
    ]
    stats = ingest_source(db_session, source, adapter=FakeAdapter(source, items))
    assert (stats.new, stats.duplicates) == (1, 1)


def test_run_ingest_isolates_source_failures(db_session):
    bad = make_source(db_session, "bad")
    good = make_source(db_session, "good")
    adapters = {
        bad.id: FakeAdapter(bad, error=RuntimeError("boom")),
        good.id: FakeAdapter(good, [make_item("ok")]),
    }
    all_stats = run_ingest(db_session, get_adapter=lambda s: adapters[s.id])
    by_name = {s.source_name: s for s in all_stats}
    assert "boom" in by_name["bad"].error
    assert by_name["good"].new == 1
    assert len(db_session.scalars(select(Article)).all()) == 1


def test_run_ingest_skips_inactive_sources(db_session):
    make_source(db_session, "off", active=False)
    all_stats = run_ingest(db_session, get_adapter=lambda s: FakeAdapter(s, [make_item()]))
    assert all_stats == []


def test_run_ingest_order_is_injectable_for_fairness(db_session):
    # Depuis les IP datacenter, Reddit ne laisse passer qu'une requête par run :
    # l'ordre des sources est mélangé à chaque run pour que la même source ne
    # soit pas systématiquement sacrifiée.
    make_source(db_session, "premier")
    make_source(db_session, "second")
    order = []

    def tracking_adapter(source):
        return FakeAdapter(source, [])

    def reverse_shuffle(sources):
        sources.reverse()
        order.extend(s.name for s in sources)

    run_ingest(db_session, get_adapter=tracking_adapter, shuffle=reverse_shuffle)
    assert order == ["second", "premier"]
