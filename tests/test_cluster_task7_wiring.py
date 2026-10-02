"""Cablage de l'arbitrage Task 7 : seul le passage horaire (`run_pipeline`)
peut arbitrer, jamais `run_veille` (toutes les 5 minutes) -- distinction par
PARAMETRE, pas en devinant qui appelle."""

import datetime as dt

from app.config import settings
from app.models import Article, ArticleStatus, Source, SourceCategorie, SourceType
from app.pipeline.run import dedupliquer_avec_arbitrage
from app.pipeline.veille import run_veille
from app.sources.base import RawItem

UTC = dt.timezone.utc
# Une heure RONDE et RÉCENTE, jamais une date figée : `retention_days` vaut 2,
# donc un article daté d'un 18 septembre en dur sort de la fenêtre dès que le
# calendrier avance — l'item d'essai est alors sauté à l'ingestion, `nouveaux`
# tombe à 0 et le test vire au rouge tout seul, sans qu'une ligne de code ait
# bougé. C'est arrivé DEUX fois sur ce dépôt (2026-09-30 sur le test de
# non-régression des événements, 2026-10-02 ici et sur le verrou de `run_veille`).
T0 = dt.datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)


class _FauxAdapter:
    """Un item a ingerer, pour que `run_veille` ait de quoi appeler `cluster`
    (elle ne le fait que si `rapport.nouveaux` est vrai -- 288 passages/jour,
    un passage a vide ne doit rien couter)."""

    def a_lire(self, maintenant):
        return True

    def fetch(self, maintenant=None):
        return [
            RawItem(
                url="https://www.instagram.com/p/1/",
                title="Un post sans rapport",
                summary="rien a voir",
                published_at=T0,
            )
        ]


def test_run_veille_n_arbitre_jamais_meme_active_et_avec_une_cle(db_session, monkeypatch):
    """Tour de correction 1/5 (M4) : un test de COMPORTEMENT, pas une simple
    inspection de signature (qui resterait vert si `run_veille` appelait
    l'arbitre depuis son CORPS plutot que via un defaut de parametre). On
    active l'arbitrage et on pose une cle -- exactement les conditions ou
    `run_pipeline` construirait un arbitre -- et on fait planter la fabrique
    si elle est appelee : la preuve que `run_veille` ne la touche jamais est
    qu'aucune exception ne remonte ET que la paire douteuse reste non liee.

    Corrige au tour de correction 2/5, apres une re-revue qui a prouve que ce
    test ne prouvait rien : `T0` etait fige au 2026-09-18 alors que
    `link_duplicates` lit l'heure REELLE (`dt.datetime.now`), donc la reprise
    tombait hors de la fenetre de 48h et la bande douteuse n'etait jamais
    meme examinee -- l'arbitre n'avait aucune occasion d'etre appele, mutation
    ou pas. Les dates des articles sont donc calees sur `dt.datetime.now`,
    pas sur `T0` (qui ne sert plus qu'a l'horloge interne de `run_veille`,
    sans rapport avec la fenetre de `link_duplicates`). Le monkeypatch visait
    aussi `app.pipeline.cluster_arbitrage.make_claude_arbitre` : sans effet
    sur `dedupliquer_avec_arbitrage`, qui importe ce nom AU CHARGEMENT dans
    l'espace de noms de `app.pipeline.run` -- corrige en visant CE nom-la.

    Mutation faite et defaite pour prouver que ce test est utile (voir le
    rapport de la tache) : `run_veille` branche sur `dedupliquer_avec_arbitrage`
    au lieu de `link_duplicates` fait bien passer ce test au ROUGE."""
    monkeypatch.setattr(settings, "cluster_arbitrage_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(settings, "meta_fb_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")

    def _fabrique_qui_leve(*a, **k):
        raise AssertionError("run_veille ne doit jamais construire d'arbitre")

    monkeypatch.setattr("app.pipeline.run.make_claude_arbitre", _fabrique_qui_leve)
    monkeypatch.setattr(
        "app.pipeline.cluster_arbitrage.make_claude_arbitre", _fabrique_qui_leve
    )

    # Une paire reelle dans la bande douteuse (archive < 0.8, recouvrement >= 0.65
    # -- memes titres que `tests/test_cluster.py::_paire_douteuse`), datee sur
    # l'HEURE REELLE : `link_duplicates` ne lit jamais `T0`.
    maintenant = dt.datetime.now(UTC)
    src_publie = Source(name="media-publie", type=SourceType.RSS, url="https://a/feed")
    src_reprise = Source(name="media-reprise", type=SourceType.RSS, url="https://b/feed")
    src_veille = Source(
        name="stmpdrcrds", type=SourceType.INSTAGRAM, url="ig:stmpdrcrds",
        categorie=SourceCategorie.ARTISTE,
    )
    db_session.add_all([src_publie, src_reprise, src_veille])
    db_session.commit()
    publie = Article(
        source_id=src_publie.id, url="https://a/1",
        title="Boris Brejcha annonce son premier album",
        raw_hash="1", published_at=maintenant - dt.timedelta(days=5), status=ArticleStatus.USED,
    )
    reprise = Article(
        source_id=src_reprise.id, url="https://b/1",
        title="Boris Brejcha devoile une tournee mondiale autour de son premier album",
        raw_hash="2", published_at=maintenant - dt.timedelta(hours=1),
    )
    db_session.add_all([publie, reprise])
    db_session.commit()
    monkeypatch.setattr(settings, "veille_comptes", "stmpdrcrds")

    rapport = run_veille(
        db_session, T0, get_adapter=lambda source: _FauxAdapter(),
        score=lambda db, limit=None: None, record=lambda db: 0,
        notify=lambda *a: True,
    )

    assert rapport.nouveaux == 1  # confirme que `cluster` a bien ete appele
    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None


def test_dedupliquer_avec_arbitrage_ne_construit_rien_sans_cle(monkeypatch):
    monkeypatch.setattr(settings, "cluster_arbitrage_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    appels = []
    monkeypatch.setattr(
        "app.pipeline.run.make_claude_arbitre", lambda *a, **k: appels.append(1) or None
    )

    from app.db import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        dedupliquer_avec_arbitrage(db)

    assert appels == []


def test_dedupliquer_avec_arbitrage_ne_construit_rien_si_coupe(monkeypatch):
    monkeypatch.setattr(settings, "cluster_arbitrage_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")

    appels = []
    monkeypatch.setattr(
        "app.pipeline.run.make_claude_arbitre", lambda *a, **k: appels.append(1) or None
    )

    from app.db import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        dedupliquer_avec_arbitrage(db)

    assert appels == []


def test_dedupliquer_avec_arbitrage_construit_un_arbitre_quand_active(monkeypatch):
    monkeypatch.setattr(settings, "cluster_arbitrage_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")

    appels = []

    def _faux_arbitre():
        appels.append(1)
        return lambda a, b: None

    monkeypatch.setattr("app.pipeline.run.make_claude_arbitre", _faux_arbitre)

    from app.db import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        dedupliquer_avec_arbitrage(db)

    assert appels == [1]
