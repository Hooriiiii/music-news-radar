"""Phase 2A, Task 2, tour de correction 1 -- points 4, 5, 6 (Minor).

Les points 1 (migration absente), 2 (membre deja lie) et 3 (autoflush) ont
leurs propres tests (`test_events_migration_absente.py`,
`test_events_rattachement.py`). Ce fichier couvre les trois finitions
restantes : une source disparue ne doit jamais fusionner a tort avec un vrai
groupe, `first_seen_at`/`last_seen_at` n'ont plus qu'un seul repli, et
`retitrer` ne doit plus sortir en silence quand il ne trouve rien.
"""

import datetime as dt
import logging

from sqlalchemy import select

from app.models import Article, ArticleStatus, Event, EventObservation, Source, SourceType
from app.pipeline import events
from app.pipeline.cluster import link_duplicates

UTC = dt.timezone.utc


def _src(db, name, *, source_group_id=None):
    src = Source(
        name=name, type=SourceType.RSS, url=f"https://{name}.example/feed",
        source_group_id=source_group_id,
    )
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, title, hours_ago=1, published_at=...):
    if published_at is ...:
        published_at = dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago)
    art = Article(
        source_id=src.id, url=f"https://{src.name}.example/{n}", title=title,
        raw_hash=f"{n:064d}", published_at=published_at, status=ArticleStatus.NEW,
    )
    db.add(art)
    db.commit()
    return art


def _events(db):
    return db.scalars(select(Event)).all()


# -- point 4 : une source disparue ne fusionne jamais avec un vrai groupe -----


def test_disappeared_source_never_merges_with_a_real_group_of_the_same_id(db_session):
    """Deux sources partagent le groupe 1234 (un id choisi arbitrairement
    grand pour coincider avec une source qui n'existe PAS). Une observation
    porte `source_id=1234` sans qu'aucune ligne `sources` ne porte cet id --
    exactement l'etat d'une source disparue (jamais purgee en production,
    mais le defaut doit rester correct si ca arrivait quand meme). Le bug
    corrige rangeait cette observation sous `("groupe", 1234)`, la fusionnant
    A TORT avec le vrai groupe 1234 -- `independent_source_count` valait 1 au
    lieu de 2."""
    db_session.autoflush = False
    groupe_a = _src(db_session, "site-un", source_group_id=1234)
    groupe_b = _src(db_session, "site-deux", source_group_id=1234)

    _art(db_session, groupe_a, n=1, title="Nouveau festival techno annonce a Rennes", hours_ago=5)
    _art(db_session, groupe_b, n=2, title="Nouveau festival techno annonce a Rennes cet ete",
         hours_ago=4)
    link_duplicates(db_session)
    (event,) = _events(db_session)
    assert event.source_count == 2
    assert event.independent_source_count == 1  # {groupe 1234} : point de depart

    # Observation dont `source_id` (1234) COINCIDE numeriquement avec le vrai
    # groupe, mais sans aucune ligne `sources` d'id 1234 -- SQLite n'applique
    # pas les cles etrangeres par defaut, l'insertion passe donc bien.
    db_session.add(
        EventObservation(
            event_id=event.id,
            article_id=None,
            platform=SourceType.RSS,
            source_id=1234,
            source_role=event.observations[0].source_role,
            published_at=dt.datetime.now(UTC) - dt.timedelta(hours=1),
            title="Une reprise dont la source a disparu",
            url="https://source-disparue.example/1",
            source_name="Source disparue",
        )
    )
    db_session.commit()

    events._recalculer(db_session, event)
    db_session.commit()

    assert event.source_count == 3
    # {("groupe", 1234), ("source", 1234)} -- jamais fusionnes malgre l'id partage.
    assert event.independent_source_count == 2


# -- point 5 : un seul repli pour dater un fait -------------------------------


def test_first_seen_at_uses_the_same_fallback_at_creation_and_recompute(db_session):
    """Un article SANS date de publication : `_creer_evenement` et
    `_recalculer` doivent s'accorder sur le MEME repli (`fetched_at`), jamais
    l'heure du rattachement (`observed_at`) -- sinon `first_seen_at` cesse
    d'etre reconstructible depuis les articles (Task 3 s'appuiera dessus)."""
    db_session.autoflush = False
    src = _src(db_session, "mixmag")
    article = _art(db_session, src, n=1, title="Une actu sans date de publication connue",
                   published_at=None)
    # `fetched_at` a un `server_default=func.now()` : sur SQLite via l'ORM (pas
    # de vrai serveur), il faut le poser explicitement pour le test.
    article.fetched_at = dt.datetime.now(UTC) - dt.timedelta(hours=10)
    db_session.commit()

    event, _ = events._creer_evenement(db_session, article)
    db_session.commit()

    assert event.first_seen_at.replace(tzinfo=None) == article.fetched_at.replace(tzinfo=None)

    # Un recalcul (comme apres l'ajout d'un membre) doit retomber sur EXACTEMENT
    # la meme valeur -- pas sur `observed_at`, l'heure du rattachement.
    events._recalculer(db_session, event)
    db_session.commit()
    assert event.first_seen_at.replace(tzinfo=None) == article.fetched_at.replace(tzinfo=None)


# -- point 6 : retitrer ne sort plus en silence -------------------------------


def test_retitrer_logs_a_warning_when_no_event_is_found(db_session, caplog):
    db_session.autoflush = False
    src = _src(db_session, "mixmag")
    article = _art(db_session, src, n=1, title="Un representant sans evenement")

    with caplog.at_level(logging.WARNING, logger="app.pipeline.events"):
        events.retitrer(db_session, article)

    assert any("aucun evenement" in message.lower() for message in caplog.messages)
