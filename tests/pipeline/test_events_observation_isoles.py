"""Phase 2B, Task 1 : TOUT article devient l'observation d'un evenement.

Avant cette tache, `events.attacher` n'etait appele que depuis `cluster.lier` :
un article n'entrait dans un evenement que s'il venait d'etre lie comme
doublon (18 % du flux mesure le 2026-09-24). `events.observer_articles` est le
second point d'entree : un article sans evenement en recoit un, meme seul.

Chaque test regle `autoflush = False` EXPLICITEMENT : c'est le reglage de
production (`app/db.py`), et un test en autoflush par defaut a deja masque
deux fois un defaut de production dans ce depot.
"""

import datetime as dt

from sqlalchemy import select

from app.config import settings
from app.models import (
    Article,
    ArticleStatus,
    Event,
    EventObservation,
    Source,
    SourceCategorie,
    SourceRole,
    SourceType,
)
from app.pipeline.cluster import link_duplicates
from app.pipeline.events import observer_articles

UTC = dt.timezone.utc


def _src(db, name, *, type_=SourceType.RSS, categorie=None):
    src = Source(
        name=name, type=type_, url=f"https://{name}.example/feed", categorie=categorie
    )
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


def _events(db):
    return db.scalars(select(Event)).all()


def _observations(db, event_id):
    return db.scalars(
        select(EventObservation).where(EventObservation.event_id == event_id)
    ).all()


# -- l'exigence centrale : un article seul a quand meme son evenement --------


def test_un_article_solitaire_recoit_son_propre_evenement(db_session):
    db_session.autoflush = False
    src = _src(db_session, "tsugi", categorie=SourceCategorie.MEDIA)
    art = _art(db_session, src, n=1, title="Un titre qui ne ressemble a rien d'autre")

    stats = observer_articles(db_session)

    assert stats.evenements_crees == 1
    assert stats.articles_rattaches == 0
    assert stats.erreur is None
    evenements = _events(db_session)
    assert len(evenements) == 1
    assert evenements[0].titre == art.title
    assert evenements[0].source_count == 1
    assert evenements[0].platform_count == 1
    assert evenements[0].independent_source_count == 1


def test_les_champs_denormalises_sont_remplis(db_session):
    """Ce sont eux qui rendent l'archive lisible apres la purge des articles
    (`event_observations.article_id` est `ON DELETE SET NULL`)."""
    db_session.autoflush = False
    src = _src(db_session, "astropolis", type_=SourceType.INSTAGRAM,
               categorie=SourceCategorie.FESTIVAL)
    art = _art(db_session, src, n=1, title="Astropolis devoile sa programmation")

    observer_articles(db_session)

    (observation,) = _observations(db_session, _events(db_session)[0].id)
    assert observation.article_id == art.id
    assert observation.title == art.title
    assert observation.url == art.url
    assert observation.source_name == src.name
    assert observation.source_id == src.id
    assert observation.platform == SourceType.INSTAGRAM
    # Festival = source primaire, exactement comme par le chemin `attacher`.
    assert observation.source_role == SourceRole.PRIMARY
    assert observation.published_at is not None


def test_idempotent_un_article_n_est_jamais_observe_deux_fois(db_session):
    db_session.autoflush = False
    src = _src(db_session, "mixmag")
    _art(db_session, src, n=1, title="Un titre unique")

    premier = observer_articles(db_session)
    second = observer_articles(db_session)

    assert premier.evenements_crees == 1
    assert second.articles_examines == 0
    assert second.evenements_crees == 0
    assert len(_events(db_session)) == 1
    assert len(_observations(db_session, _events(db_session)[0].id)) == 1


def test_un_doublon_ne_cree_jamais_son_propre_evenement(db_session):
    """Il rejoint celui de son representant -- un groupe, un evenement."""
    db_session.autoflush = False
    mixmag = _src(db_session, "mixmag")
    tsugi = _src(db_session, "tsugi")
    representant = _art(db_session, mixmag, n=1,
                        title="Dour Festival unveils its 2027 lineup", hours_ago=5)
    doublon = _art(db_session, tsugi, n=2,
                   title="Dour Festival devoile son line-up 2027", hours_ago=2)
    link_duplicates(db_session)
    db_session.refresh(doublon)
    assert doublon.duplicate_of_id == representant.id
    # `cluster.lier` a deja cree l'evenement du groupe : on efface tout pour
    # prouver que `observer_articles` SEULE ne fabrique pas deux evenements.
    for event in _events(db_session):
        for observation in _observations(db_session, event.id):
            db_session.delete(observation)
        db_session.delete(event)
    db_session.commit()

    stats = observer_articles(db_session)

    # Le doublon passe par `attacher` (le chemin du groupe), jamais par la
    # creation d'un evenement solo ; le representant, pourvu au meme instant,
    # n'en recoit pas un second.
    assert stats.articles_rattaches == 1
    assert stats.evenements_crees == 0
    evenements = _events(db_session)
    assert len(evenements) == 1
    article_ids = {o.article_id for o in _observations(db_session, evenements[0].id)}
    assert article_ids == {representant.id, doublon.id}


# -- la propriete centrale : la fusion existe deja, rien a reecrire ----------


def test_deux_evenements_solos_fusionnent_quand_la_dedup_les_rapproche(db_session):
    """PREUVE de ce qui rend la Task 1 courte : `attacher` sait deja fusionner
    (`_fusionner`). Deux articles nes chacun avec leur evenement solo, puis
    rapproches par la dedup, ne doivent laisser qu'UN evenement portant les
    deux observations -- sans une seconde mecanique de fusion ecrite a cote.
    """
    db_session.autoflush = False
    mixmag = _src(db_session, "mixmag")
    tsugi = _src(db_session, "tsugi")
    premier = _art(db_session, mixmag, n=1,
                   title="Dour Festival unveils its 2027 lineup", hours_ago=5)

    # Passage 1 : le premier article est seul, il nait avec son evenement.
    observer_articles(db_session)
    assert len(_events(db_session)) == 1

    # Passage 2 : le second arrive et nait lui aussi avec le sien (la dedup
    # n'a pas encore tourne).
    second = _art(db_session, tsugi, n=2,
                  title="Dour Festival devoile son line-up 2027", hours_ago=2)
    observer_articles(db_session)
    assert len(_events(db_session)) == 2

    # Passage 3 : la dedup les rapproche -> `cluster.lier` appelle `attacher`,
    # qui FUSIONNE les deux evenements existants.
    link_duplicates(db_session)
    db_session.commit()
    db_session.refresh(second)
    assert second.duplicate_of_id == premier.id

    evenements = _events(db_session)
    assert len(evenements) == 1, "les deux evenements solos auraient du fusionner"
    observations = _observations(db_session, evenements[0].id)
    assert {o.article_id for o in observations} == {premier.id, second.id}
    assert evenements[0].source_count == 2
    assert evenements[0].independent_source_count == 2

    # Et un passage de plus ne recree rien : l'article fusionne a deja son
    # observation, il n'est meme plus candidat.
    apres = observer_articles(db_session)
    assert apres.articles_examines == 0
    assert len(_events(db_session)) == 1


# -- bornes de cout et robustesse -------------------------------------------


def test_le_plafond_par_passage_borne_les_ecritures(db_session, monkeypatch):
    db_session.autoflush = False
    monkeypatch.setattr(settings, "events_observation_max_par_passage", 2)
    src = _src(db_session, "mixmag")
    for n in range(5):
        _art(db_session, src, n=n, title=f"Actu numero {n} sans rapport avec les autres")

    stats = observer_articles(db_session)

    assert stats.articles_examines == 2
    assert stats.evenements_crees == 2
    assert stats.plafond_atteint is True
    assert len(_events(db_session)) == 2
    # Le passage suivant reprend l'arriere la ou il s'etait arrete.
    assert observer_articles(db_session).evenements_crees == 2
    assert len(_events(db_session)) == 4


def test_un_article_deja_hors_fenetre_de_retention_est_ignore(db_session, monkeypatch):
    """Il sera purge a la fin de CE MEME passage : lui creer un evenement ne
    laisserait qu'une observation orpheline sur un evenement injugeable."""
    db_session.autoflush = False
    monkeypatch.setattr(settings, "retention_days", 2)
    src = _src(db_session, "mixmag")
    _art(db_session, src, n=1, title="Une vieille actu", hours_ago=24 * 5)
    _art(db_session, src, n=2, title="Une actu fraiche", hours_ago=1)

    stats = observer_articles(db_session)

    assert stats.evenements_crees == 1
    assert _events(db_session)[0].titre == "Une actu fraiche"


def test_une_panne_ne_leve_jamais_et_remonte_dans_les_stats(db_session, monkeypatch):
    db_session.autoflush = False
    src = _src(db_session, "mixmag")
    _art(db_session, src, n=1, title="Une actu quelconque")

    from app.pipeline import events as events_module

    def _boum(*args, **kwargs):
        raise RuntimeError("connexion perdue")

    monkeypatch.setattr(events_module, "_creer_evenement", _boum)

    stats = observer_articles(db_session)

    assert stats.erreur == "connexion perdue"
    assert stats.evenements_crees == 0
    assert _events(db_session) == []


def test_sans_les_tables_la_fonctionnalite_est_dormante_pas_en_panne(db_session, monkeypatch):
    db_session.autoflush = False
    from app.pipeline import events as events_module

    monkeypatch.setattr(events_module, "_tables_disponibles", lambda db: False)
    src = _src(db_session, "mixmag")
    _art(db_session, src, n=1, title="Une actu quelconque")

    stats = observer_articles(db_session)

    assert stats.tables_absentes is True
    assert stats.erreur is None
    assert stats.evenements_crees == 0


# -- branchement sur le passage horaire --------------------------------------


def _pipeline_muet(monkeypatch):
    from app.pipeline.cluster import ClusterStats
    from app.pipeline.election import ElectionStats
    from app.pipeline.scoring import ScoringStats

    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "discord_webhook_url", None)
    return ClusterStats, ElectionStats, ScoringStats


def test_l_etape_est_injectable_et_tourne_apres_le_scoring_avant_les_mentions(
    db_session, monkeypatch
):
    from app.pipeline.run import run_pipeline

    ClusterStats, ElectionStats, ScoringStats = _pipeline_muet(monkeypatch)
    monkeypatch.setattr(settings, "anthropic_api_key", "cle-de-test")
    vus = []

    from app.pipeline.events import ObservationStats

    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        cluster=lambda db: vus.append("cluster") or ClusterStats(),
        elire=lambda db: ElectionStats(),
        score=lambda db: vus.append("score") or ScoringStats(),
        record=lambda db: vus.append("record") or 0,
        illustrer=lambda db: type("S", (), {"illustres": 0})(),
        purge=lambda db, days: 0,
        grouper_sources=lambda db: None,
        observer=lambda db: vus.append("observer") or ObservationStats(evenements_crees=7),
    )

    assert vus == ["cluster", "score", "observer", "record"]
    assert report.observation_evenements.evenements_crees == 7


def test_run_pipeline_survit_a_une_panne_du_rattachement_aux_evenements(
    db_session, monkeypatch
):
    from app.pipeline.run import run_pipeline

    ClusterStats, ElectionStats, ScoringStats = _pipeline_muet(monkeypatch)
    calls = []

    def observer_qui_leve(db):
        raise RuntimeError("boom")

    report = run_pipeline(
        db_session,
        ingest=lambda db: calls.append("ingest") or [],
        cluster=lambda db: calls.append("cluster") or ClusterStats(),
        elire=lambda db: calls.append("elire") or ElectionStats(),
        score=lambda db: calls.append("score") or ScoringStats(),
        record=lambda db: calls.append("record") or 0,
        illustrer=lambda db: calls.append("illustrer") or type("S", (), {"illustres": 0})(),
        purge=lambda db, days: calls.append("purge") or 3,
        grouper_sources=lambda db: None,
        observer=observer_qui_leve,
    )

    assert calls == ["ingest", "cluster", "elire", "illustrer", "record", "purge"]
    assert report.purged == 3


def test_une_panne_de_rattachement_est_notifiee(db_session, monkeypatch):
    """Un compteur qui n'est affiche nulle part est un compteur mort."""
    from app.pipeline.events import ObservationStats
    from app.pipeline.run import run_pipeline

    ClusterStats, ElectionStats, ScoringStats = _pipeline_muet(monkeypatch)
    notifications = []

    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        cluster=lambda db: ClusterStats(),
        elire=lambda db: ElectionStats(),
        score=lambda db: ScoringStats(),
        record=lambda db: 0,
        illustrer=lambda db: type("S", (), {"illustres": 0})(),
        purge=lambda db, days: 0,
        grouper_sources=lambda db: None,
        observer=lambda db: ObservationStats(erreur="connexion perdue"),
        notify=lambda titre, message: notifications.append(message),
    )

    assert any("rattachement aux evenements" in p for p in report.problemes)
    assert notifications and "connexion perdue" in notifications[0]
