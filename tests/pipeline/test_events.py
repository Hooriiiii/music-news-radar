"""Schema evenements Phase 2A (Task 1) : `events`, `event_observations`,
`sources.source_group_id`.

Rien n'est encore alimente par le pipeline (Task 2 branchera la dedup) : ces
tests verifient seulement que le schema tient sa promesse -- la meme que
`artist_mentions` -- une preuve survit a la purge de son article, avec ses
champs denormalises intacts.
"""

import datetime as dt

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Article, Event, EventObservation, Source, SourceRole, SourceType
from app.pipeline.retention import purge_old_articles

UTC = dt.timezone.utc


def _src(db, name="Feed", categorie=None):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.test/f", categorie=categorie)
    db.add(src)
    db.commit()
    return src


def _article(db, src, *, n, days_ago=0):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.test/{n}",
        title=f"Titre {n}",
        raw_hash=f"{n:064d}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(days=days_ago),
    )
    db.add(art)
    db.commit()
    return art


def _event(db, *, titre="Un evenement", first_seen_at=None, last_seen_at=None):
    now = dt.datetime.now(UTC)
    ev = Event(
        titre=titre,
        first_seen_at=first_seen_at or now,
        last_seen_at=last_seen_at or now,
    )
    db.add(ev)
    db.commit()
    return ev


def _observation(db, event, article, src, *, platform=SourceType.RSS, role=SourceRole.PRIMARY):
    obs = EventObservation(
        event_id=event.id,
        article_id=article.id if article else None,
        platform=platform,
        source_id=src.id if src else None,
        source_role=role,
        published_at=article.published_at if article else None,
        title=article.title if article else "Titre archive",
        url=article.url if article else "https://archive.test/1",
        source_name=src.name if src else "Source disparue",
    )
    db.add(obs)
    db.commit()
    return obs


def test_creation_evenement_et_observation(db_session):
    src = _src(db_session)
    article = _article(db_session, src, n=1)
    event = _event(db_session, titre="Fred again.. annonce un nouvel album")
    obs = _observation(db_session, event, article, src)

    assert db_session.query(Event).one().titre == "Fred again.. annonce un nouvel album"
    relu = db_session.query(EventObservation).one()
    assert relu.event_id == event.id
    assert relu.article_id == article.id
    assert relu.source_id == src.id
    assert relu.platform == SourceType.RSS
    assert relu.source_role == SourceRole.PRIMARY
    assert relu.title == article.title
    assert relu.url == article.url
    assert obs in event.observations


def test_observation_survit_a_la_purge_de_son_article(db_session):
    """Le point de tout l'exercice, comme pour `artist_mentions` : l'article est
    ephemere, la preuve (titre, url, nom de source) doit rester lisible sans
    lui apres la purge a 2 jours."""
    # Chemin de base (delete() cote base, sans passer par l'ORM) : le reglage
    # de production est `autoflush=False` (app/db.py), et un test en autoflush
    # par defaut a deja masque un defaut de production dans ce depot.
    db_session.autoflush = False

    src = _src(db_session, name="Mixmag")
    article = _article(db_session, src, n=1, days_ago=60)
    event = _event(db_session, titre="Une actu vieille de deux mois")
    obs = _observation(db_session, event, article, src)
    article_id = article.id
    obs_id = obs.id

    purge_old_articles(db_session, retention_days=14)

    assert db_session.query(Article).filter(Article.id == article_id).count() == 0
    # L'evenement et l'observation survivent tels quels, jamais purges.
    relu_event = db_session.query(Event).one()
    assert relu_event.titre == "Une actu vieille de deux mois"
    relu_obs = db_session.query(EventObservation).filter(EventObservation.id == obs_id).one()
    assert relu_obs.title == "Titre 1"
    assert relu_obs.url == "https://Mixmag.test/1"
    assert relu_obs.source_name == "Mixmag"


def test_couple_event_id_article_id_unique(db_session):
    """Idempotence du futur rattachement (Task 2) : deux observations du meme
    article sur le meme evenement doivent etre refusees par la base."""
    src = _src(db_session)
    article = _article(db_session, src, n=1)
    event = _event(db_session)
    _observation(db_session, event, article, src)

    with pytest.raises(IntegrityError):
        _observation(db_session, event, article, src)
    db_session.rollback()


def test_sources_source_group_id_est_nullable_et_regroupe(db_session):
    """Deux sources partageant un `source_group_id` (ex. la meme depeche AFP
    reprise par deux sites) : la colonne existe et n'est remplie par personne
    encore -- seule la structure est verifiee ici (Task 3 calculera dessus)."""
    isolee = _src(db_session, name="Isolee")
    assert isolee.source_group_id is None

    a = Source(name="Site A", type=SourceType.RSS, url="https://a.test/f", source_group_id=42)
    b = Source(name="Site B", type=SourceType.RSS, url="https://b.test/f", source_group_id=42)
    db_session.add_all([a, b])
    db_session.commit()

    memes_groupe = (
        db_session.query(Source).filter(Source.source_group_id == 42).count()
    )
    assert memes_groupe == 2
