"""Cablage de l'arbitrage Task 7 : seul le passage horaire (`run_pipeline`)
peut arbitrer, jamais `run_veille` (toutes les 5 minutes) -- distinction par
PARAMETRE, pas en devinant qui appelle."""

import inspect

from app.config import settings
from app.pipeline.cluster import link_duplicates
from app.pipeline.run import dedupliquer_avec_arbitrage
from app.pipeline.veille import run_veille


def test_run_veille_utilise_link_duplicates_brut_sans_arbitre():
    """Le defaut de `run_veille` doit etre EXACTEMENT `link_duplicates`, jamais
    un wrapper qui construirait un arbitre -- ainsi aucune modification future
    de `run_veille` ne peut, par megarde, se remettre a arbitrer."""
    parametre = inspect.signature(run_veille).parameters["cluster"]
    assert parametre.default is link_duplicates


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
