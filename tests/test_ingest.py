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
    # date relative récente : sinon les tests vieilliraient hors fenêtre de rétention
    return RawItem(
        url=f"https://example.com/{slug}",
        title=title,
        summary="résumé",
        published_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),
        media=[{"type": "photo", "url": "https://example.com/img.jpg"}],
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
    assert articles[0].media == [{"type": "photo", "url": "https://example.com/img.jpg"}]


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


def test_ingest_skips_articles_older_than_retention(db_session):
    source = make_source(db_session)
    now = dt.datetime.now(dt.timezone.utc)
    items = [
        RawItem(url="https://example.com/vieux", title="Vieux",
                published_at=now - dt.timedelta(days=20)),   # hors fenêtre -> ignoré
        RawItem(url="https://example.com/recent", title="Récent",
                published_at=now - dt.timedelta(days=2)),    # dans la fenêtre -> inséré
        RawItem(url="https://example.com/sansdate", title="Sans date",
                published_at=None),                          # non daté -> inséré
    ]
    stats = ingest_source(db_session, source, adapter=FakeAdapter(source, items))
    assert stats.new == 2
    titles = {a.title for a in db_session.scalars(select(Article)).all()}
    assert titles == {"Récent", "Sans date"}


# --- Coupe-circuit de quota Graph (panne du 2026-09-19) --------------------
#
# Un appel refusé pour quota consomme quand même du quota : le passage horaire
# attaquait les 123 comptes Instagram, encaissait 115 refus, et ces refus
# alimentaient le dépassement de l'heure suivante -- 751 refus dans le log,
# jusqu'à « 116 source(s) en erreur » par run, 80 % des comptes plus lus du
# tout. Ces tests tiennent les quatre promesses du coupe-circuit : on s'arrête
# au premier code, le RSS continue, la pause survit au processus, et une source
# non appelée n'est ni une source en erreur ni une source dont on oublie la
# santé.

QUOTA = "Meta HTTP 403 (code 4) : (#4) Application request limit reached"


def _source_ig(db, nom):
    source = Source(name=nom, type=SourceType.INSTAGRAM, url=f"ig:{nom}")
    db.add(source)
    db.commit()
    return source


def _sans_melange(sources):
    """L'ordre de la base : ces tests ont besoin de savoir qui est appelé après
    qui, ce que `random.shuffle` rend indécidable."""


def test_un_code_de_quota_arrete_les_sources_instagram_suivantes(db_session):
    from app.sources.instagram import QuotaGraphAtteint

    for n in range(5):
        _source_ig(db_session, f"ig{n}")
    make_source(db_session, "mixmag")
    appeles = []

    def adapter(source):
        appeles.append(source.name)
        if source.type == SourceType.INSTAGRAM and source.name == "ig2":
            return FakeAdapter(source, error=QuotaGraphAtteint(QUOTA))
        return FakeAdapter(source, [make_item(source.name)])

    stats = run_ingest(
        db_session, get_adapter=adapter, shuffle=_sans_melange, notify=lambda *_: True
    )

    # Les deux premiers comptes sont lus, le troisième est refusé, les suivants
    # ne sont PAS appelés : c'est ce qui entretenait le dépassement.
    assert appeles == ["ig0", "ig1", "ig2", "mixmag"]
    par_nom = {s.source_name: s for s in stats}
    assert par_nom["mixmag"].new == 1, "un dépassement Meta n'empêche pas d'ingérer la presse"
    assert [s.source_name for s in stats if s.reportee] == ["ig2", "ig3", "ig4"]
    assert all(s.error is None for s in stats)


def test_la_pause_survit_au_passage_suivant(db_session):
    """Le passage horaire est un processus neuf à chaque fois : sans état
    persisté, le run suivant retaperait dans le mur une heure plus tard."""
    from app.sources.instagram import QuotaGraphAtteint

    _source_ig(db_session, "ig0")
    appeles = []

    def adapter(source):
        appeles.append(source.name)
        return FakeAdapter(source, error=QuotaGraphAtteint(QUOTA))

    run_ingest(db_session, get_adapter=adapter, shuffle=_sans_melange, notify=lambda *_: True)
    assert appeles == ["ig0"]

    stats = run_ingest(
        db_session, get_adapter=adapter, shuffle=_sans_melange, notify=lambda *_: True
    )

    assert appeles == ["ig0"], "aucun appel de plus pendant la pause"
    assert stats[0].reportee and stats[0].error is None


def test_la_pause_ne_notifie_qu_une_fois(db_session):
    from app.sources.instagram import QuotaGraphAtteint

    _source_ig(db_session, "ig0")
    _source_ig(db_session, "ig1")
    notifications = []

    def adapter(source):
        return FakeAdapter(source, error=QuotaGraphAtteint(QUOTA))

    for _ in range(3):
        run_ingest(
            db_session,
            get_adapter=adapter,
            shuffle=_sans_melange,
            notify=lambda *args: notifications.append(args),
        )

    assert len(notifications) == 1


def test_une_source_non_appelee_garde_sa_sante_et_n_est_pas_en_erreur(db_session):
    """« 116 source(s) en erreur » était un mensonge de supervision : ces
    comptes n'étaient pas cassés, on ne les avait pas appelés."""
    from app.pipeline.health import diagnostiquer
    from app.sources.instagram import QuotaGraphAtteint

    en_panne = _source_ig(db_session, "ig0")
    jamais_appelee = _source_ig(db_session, "ig1")
    hier = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    jamais_appelee.state = {"last_ok_at": hier}
    db_session.commit()

    def adapter(source):
        if source.name == "ig0":
            return FakeAdapter(source, error=QuotaGraphAtteint(QUOTA))
        return FakeAdapter(source, [])

    stats = run_ingest(
        db_session, get_adapter=adapter, shuffle=_sans_melange, notify=lambda *_: True
    )

    assert diagnostiquer(db_session, stats, None) == []
    # Ni erreur inscrite sur la source refusée, ni succès effacé sur la suivante.
    assert "last_error" not in (en_panne.state or {})
    assert jamais_appelee.state["last_ok_at"] == hier
