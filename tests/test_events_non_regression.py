"""Phase 2A, Task 2 -- Global Constraints : « Rien ne change dans le
comportement de publication ».

Preuve directe : sur un jeu de donnees identique, le pipeline de dedup/election
prend EXACTEMENT les memes decisions (duplicate_of_id, file_exclu_raison,
status, reelections) que `events.attacher`/`events.retitrer` soient actifs ou
neutralises. Les deux bases sont independantes (memes graines, memes horaires
figes) ; seule la couche d'observation Phase 2A differe entre les deux.
"""

import datetime as dt

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Article, ArticleStatus, Event, Source, SourceCategorie, SourceType
from app.pipeline import events
from app.pipeline.cluster import link_duplicates
from app.pipeline.comptes_artistes import construire_index
from app.pipeline.election import elire_representants

UTC = dt.timezone.utc


def _nouvelle_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # autoflush=False : le reglage de production (`app/db.py`), une Global
    # Constraint explicite du plan Task 2.
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return TestingSession()


def _semer(db, now: dt.datetime) -> None:
    """Un jeu de donnees qui exerce les trois chemins de decision : reprise a
    trois sources (declenche la remise a noter par election), lien d'archive,
    et une actu isolee (aucun lien)."""

    def source(name, categorie=None):
        s = Source(name=name, type=SourceType.RSS, url=f"https://{name}.example/feed",
                   categorie=categorie)
        db.add(s)
        db.commit()
        return s

    def article(src, n, title, hours_ago, status=ArticleStatus.NEW, scored_by=None,
                relevance=None):
        a = Article(
            source_id=src.id, url=f"https://{src.name}.example/{n}", title=title,
            raw_hash=f"{n:064d}", published_at=now - dt.timedelta(hours=hours_ago),
            status=status, scored_by=scored_by, relevance_score=relevance,
        )
        db.add(a)
        db.commit()
        return a

    mixmag = source("mixmag", SourceCategorie.MEDIA)
    djmag = source("djmag", SourceCategorie.MEDIA)
    tsugi = source("tsugi", SourceCategorie.MEDIA)
    reddit = source("reddit")
    festival = source("festival", SourceCategorie.FESTIVAL)

    # Groupe a 3 sources : le representant local sera remis a noter par
    # `elire_representants` (voir `CLAUDE.md`, « Dedup a deux etages »).
    from app.models import ScoredBy

    article(mixmag, 1, "Fred again.. and more teased for GTA 6 soundtrack", 6,
            scored_by=ScoredBy.LOCAL, relevance=40)
    article(djmag, 2, "GTA VI full soundtrack announced featuring Fred again.., more", 5)
    article(tsugi, 3, "La bande son de GTA VI devoilee avec Fred again..", 4)

    # Lien d'archive vers une actu deja publiee.
    article(festival, 10, "Dour Festival annonce son line-up 2027", 5 * 24,
            status=ArticleStatus.USED)
    article(reddit, 11, "Dour Festival devoile son line-up 2027", 2)

    # Actu isolee : aucun lien attendu.
    article(mixmag, 20, "Les sorties electro de la semaine", 1)


def _decisions(db) -> list[tuple]:
    """Les champs qui pilotent la publication, pour chaque article, avec le
    representant traduit en URL (identifiant stable entre les deux bases,
    independant de l'ordre d'attribution des ids)."""
    par_id = {a.id: a.url for a in db.scalars(select(Article))}
    lignes = db.execute(
        select(
            Article.url, Article.duplicate_of_id, Article.file_exclu_raison,
            Article.status, Article.scored_by,
        )
    ).all()
    return sorted(
        (url, par_id.get(dup), raison, status, scored_by)
        for url, dup, raison, status, scored_by in lignes
    )


def test_pipeline_decisions_are_identical_with_events_neutralized(monkeypatch):
    now = dt.datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    db_avec_evenements = _nouvelle_session()
    db_sans_evenements = _nouvelle_session()
    _semer(db_avec_evenements, now)
    _semer(db_sans_evenements, now)

    # Passage NORMAL : events.attacher/retitrer actifs (comportement reel).
    stats_avec = link_duplicates(db_avec_evenements)
    election_avec = elire_representants(
        db_avec_evenements, construire_index(db_avec_evenements), maintenant=now
    )

    # Passage NEUTRALISE : la couche Phase 2A est coupee, comme si Task 2
    # n'avait jamais ete branchee -- meme jeu de donnees, meme horaire fige.
    monkeypatch.setattr(events, "attacher", lambda *a, **k: None)
    monkeypatch.setattr(events, "retitrer", lambda *a, **k: None)
    stats_sans = link_duplicates(db_sans_evenements)
    election_sans = elire_representants(
        db_sans_evenements, construire_index(db_sans_evenements), maintenant=now
    )

    assert _decisions(db_avec_evenements) == _decisions(db_sans_evenements)
    assert (stats_avec.frais, stats_avec.archive, stats_avec.total) == (
        stats_sans.frais, stats_sans.archive, stats_sans.total,
    )
    assert (election_avec.reelus, election_avec.a_rescorer, election_avec.verrouilles) == (
        election_sans.reelus, election_sans.a_rescorer, election_sans.verrouilles,
    )
    # Preuve que la comparaison ci-dessus n'est pas vide de sens : le passage
    # normal a bien cree des evenements, celui neutralise n'en a cree aucun.
    assert db_avec_evenements.scalar(select(func.count()).select_from(Event)) > 0
    assert db_sans_evenements.scalar(select(func.count()).select_from(Event)) == 0
