"""Une RE-INGESTION ne cree pas un evenement de plus (Phase 2C, tour 1).

Mesure du 2026-09-30 sur la production : 48 paires (titre, source) se
retrouvaient dans plusieurs evenements, soit 147 evenements sur 907 -- 16 %
de la couche -- avec des ecarts de 34 a 59 h, juste au-dela de la retention
de 48 h. L'article est purge, le flux le liste encore, il revient comme neuf.
`nouveaute` mesurait donc en grande partie notre propre cadence.

`autoflush = False` explicitement dans chaque test : le reglage de production
(`app/db.py`), qui a deja masque deux defauts dans ce depot.
"""

import datetime as dt

from sqlalchemy import select

from app.models import Article, Event, EventObservation, Source, SourceType
from app.pipeline.events import observer_articles

UTC = dt.timezone.utc


def _src(db, name):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.example/feed")
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, title, url=None, heures=1):
    """Un article ingere il y a `heures` heures. `fetched_at` est pose
    explicitement : c'est l'horloge que la detection de re-ingestion lit."""
    quand = dt.datetime.now(UTC) - dt.timedelta(hours=heures)
    art = Article(
        source_id=src.id,
        url=url or f"https://{src.name}.example/{n}",
        title=title,
        raw_hash=f"{n:064d}",
        published_at=quand,
        fetched_at=quand,
    )
    db.add(art)
    db.commit()
    return art


def _events(db):
    return db.scalars(select(Event)).all()


def _observer_puis_purger(db, article):
    """Observer l'article, puis le PURGER : c'est la sequence reelle
    (retention a 2 jours) qui rendait la dedup par hash aveugle.

    `article_id` est mis a NULL A LA MAIN parce que SQLite (les tests)
    n'applique pas le `ON DELETE SET NULL` de la FK, que Postgres (la
    production) applique. Sans ca, l'observation garde un `article_id`
    fantome -- et SQLite REUTILISE les rowid liberes, si bien que l'article
    suivant heriterait de l'observation du precedent. Un artefact de banc
    d'essai qui ferait passer ce test pour de mauvaises raisons.
    """
    observer_articles(db)
    _purger(db, article)


def _purger(db, article):
    for obs in db.scalars(
        select(EventObservation).where(EventObservation.article_id == article.id)
    ):
        obs.article_id = None
    db.delete(article)
    db.commit()


def test_le_meme_titre_de_la_meme_source_ne_refait_pas_un_evenement(db_session):
    db_session.autoflush = False
    src = _src(db_session, "Mixmag")
    premier = _art(db_session, src, n=1, title="Vector Lovers has died", heures=40)
    _observer_puis_purger(db_session, premier)
    assert len(_events(db_session)) == 1
    avant = _events(db_session)[0].last_seen_at

    # Le flux le reliste 40 h plus tard : meme titre, meme source, url neuve.
    _art(db_session, src, n=2, title="Vector Lovers has died", heures=0)
    observer_articles(db_session)

    evenements = _events(db_session)
    assert len(evenements) == 1, "une re-ingestion a refait un evenement"
    # Aucune corroboration fabriquee : c'est la meme source.
    assert evenements[0].source_count == 1
    assert evenements[0].platform_count == 1
    assert evenements[0].independent_source_count == 1
    # Mais `last_seen_at` avance : « cette actu est encore republiee ».
    assert evenements[0].last_seen_at > avant
    assert db_session.scalar(select(EventObservation.event_id).limit(1)) is not None
    assert len(db_session.scalars(select(EventObservation)).all()) == 2


def test_la_meme_url_suffit_meme_si_le_titre_a_ete_reecrit(db_session):
    db_session.autoflush = False
    src = _src(db_session, "DJMag")
    url = "https://djmag.example/une-actu"
    premier = _art(db_session, src, n=1, title="Un titre", url=url, heures=40)
    _observer_puis_purger(db_session, premier)

    _art(db_session, src, n=2, title="Un titre legerement reecrit", url=url, heures=0)
    observer_articles(db_session)

    assert len(_events(db_session)) == 1


def test_le_meme_titre_chez_DEUX_sources_reste_deux_evenements(db_session):
    """LA precaution a ne jamais perdre : deux sources qui publient la meme
    actu sont une vraie corroboration editoriale, pas un artefact. C'est la
    dedup par titre qui les relie ensuite -- pas cette detection-ci, qui
    exige la MEME source."""
    db_session.autoflush = False
    mixmag = _src(db_session, "Mixmag")
    djmag = _src(db_session, "DJMag")
    _art(db_session, mixmag, n=1, title="Boys Noize annonce un album", heures=3)
    _art(db_session, djmag, n=2, title="Boys Noize annonce un album", heures=1)
    observer_articles(db_session)

    assert len(_events(db_session)) == 2


def test_au_dela_de_la_borne_c_est_une_actu_neuve(db_session):
    """Un marronnier republie bien plus tard n'est pas la meme actu : la
    borne est `cluster_archive_jours` (7 j), le precedent du depot."""
    db_session.autoflush = False
    src = _src(db_session, "Mixmag")
    ancien = _art(db_session, src, n=1, title="Le festival revient", heures=1)
    observer_articles(db_session)
    # L'observation herite de l'heure d'ingestion courante ; on la recule a la
    # main pour simuler un mois d'archive -- l'ARTICLE, lui, doit rester dans
    # la fenetre de retention, sinon `observer_articles` ne le regarde meme
    # pas et le test ne prouverait rien.
    obs = db_session.scalars(select(EventObservation)).one()
    obs.observed_at = dt.datetime.now(UTC) - dt.timedelta(days=30)
    db_session.commit()
    _purger(db_session, ancien)

    _art(db_session, src, n=2, title="Le festival revient", heures=0)
    observer_articles(db_session)

    assert len(_events(db_session)) == 2


def test_un_titre_sans_mot_significatif_ne_rattache_rien_par_le_titre(db_session):
    """« Post Instagram » et consorts se normalisent a vide : les rattacher
    entre eux collerait des actus sans rapport. Seule l'url peut alors
    trancher."""
    db_session.autoflush = False
    src = _src(db_session, "ComptePro")
    _art(db_session, src, n=1, title="...", heures=3)
    _art(db_session, src, n=2, title="...", heures=1)
    observer_articles(db_session)

    assert len(_events(db_session)) == 2
