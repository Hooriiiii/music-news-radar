"""Phase 2A, Task 2, tour de correction 1, point 1 (Critical).

Une migration non jouee doit DEGRADER la fonctionnalite, jamais casser le
pipeline. `events.attacher`/`events.retitrer` sont appeles sans garde depuis
`cluster.lier` et `election.elire_representants`, eux-memes appeles par
`run_pipeline` (`app/pipeline/run.py`) SANS `try/except` autour de `cluster(db)`.
Fusionner ce chantier avant que la migration `0019` ne soit jouee sur Neon
tuerait donc silencieusement le passage horaire entier (dedup, scoring,
illustration, mentions, purge -- et la notification d'erreur elle-meme, posee
en tout dernier par `diagnostiquer`).
"""

import datetime as dt

from app.models import Article, ArticleStatus, Event, EventObservation, Source, SourceType
from app.pipeline import events
from app.pipeline.cluster import link_duplicates
from app.pipeline.comptes_artistes import construire_index
from app.pipeline.election import elire_representants

UTC = dt.timezone.utc


def _src(db, name):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.example/feed")
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, title, hours_ago=1, status=ArticleStatus.NEW):
    art = Article(
        source_id=src.id, url=f"https://{src.name}.example/{n}", title=title,
        raw_hash=f"{n:064d}", published_at=dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago),
        status=status,
    )
    db.add(art)
    db.commit()
    return art


def _sans_tables_evenements(db_session, monkeypatch):
    """Simule une migration `0019` non jouee : les tables existent dans les
    metadonnees SQLAlchemy (Task 1 les a ajoutees a `Base`) mais pas dans CETTE
    base -- exactement l'ecart entre le code fusionne et Neon avant que
    l'utilisateur ne donne son accord pour `alembic upgrade head`."""
    bind = db_session.get_bind()
    EventObservation.__table__.drop(bind, checkfirst=True)  # FK vers events : d'abord
    Event.__table__.drop(bind, checkfirst=True)
    # Le module met le resultat de l'introspection en cache pour tout le
    # process (`_TABLES_PRESENTES`) : sans le reinitialiser, un test precedent
    # qui a deja vu les tables ferait passer celui-ci a tort.
    monkeypatch.setattr(events, "_TABLES_PRESENTES", None)


def test_attacher_ne_leve_pas_quand_les_tables_manquent(db_session, monkeypatch):
    _sans_tables_evenements(db_session, monkeypatch)
    src = _src(db_session, "mixmag")
    article = _art(db_session, src, n=1, title="Une actu quelconque")
    representant = _art(db_session, src, n=2, title="Une autre actu")

    resultat = events.attacher(db_session, article, representant)

    assert resultat is None  # degrade, jamais leve


def test_retitrer_ne_leve_pas_quand_les_tables_manquent(db_session, monkeypatch):
    _sans_tables_evenements(db_session, monkeypatch)
    src = _src(db_session, "mixmag")
    article = _art(db_session, src, n=1, title="Une actu quelconque")

    events.retitrer(db_session, article)  # ne doit lever aucune exception


def test_link_duplicates_tourne_normalement_sans_les_tables_evenements(db_session, monkeypatch):
    """Le scenario du bug critique : un vrai groupe de doublons se forme quand
    meme, avec les MEMES decisions que d'habitude -- seul le rattachement aux
    evenements est absent, silencieusement."""
    _sans_tables_evenements(db_session, monkeypatch)
    mixmag = _src(db_session, "mixmag")
    tsugi = _src(db_session, "tsugi")
    first = _art(db_session, mixmag, n=1, title="Dour Festival unveils its 2027 lineup",
                 hours_ago=5)
    second = _art(db_session, tsugi, n=2, title="Dour Festival devoile son line-up 2027",
                  hours_ago=2)

    stats = link_duplicates(db_session)  # ne doit lever aucune exception

    assert stats.total == 1
    db_session.refresh(first)
    db_session.refresh(second)
    assert first.duplicate_of_id is None
    assert second.duplicate_of_id == first.id  # la dedup tient, sans les evenements


def test_election_tourne_normalement_sans_les_tables_evenements(db_session, monkeypatch):
    _sans_tables_evenements(db_session, monkeypatch)
    mixmag = _src(db_session, "mixmag")
    djmag = _src(db_session, "djmag")
    _art(db_session, mixmag, n=1,
         title="Fred again.. and more teased for GTA 6 soundtrack", hours_ago=3)
    _art(db_session, djmag, n=2,
         title="GTA VI full soundtrack announced featuring Fred again.., more", hours_ago=2)

    link_duplicates(db_session)  # cree le lien sans evenement (couvert ci-dessus)

    # `retitrer` est appele DEPUIS la reelection : doit rester silencieux, lui
    # aussi, sans faire echouer l'election elle-meme.
    stats = elire_representants(
        db_session, construire_index(db_session), maintenant=dt.datetime.now(UTC)
    )

    assert stats.reelus == 1  # la reelection tient, meme sans evenement a retitrer
