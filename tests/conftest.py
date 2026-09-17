import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app


@pytest.fixture(autouse=True)
def _pas_de_prefiltre_ambiant(monkeypatch):
    """Neutralise le pré-filtre local pour TOUTE la suite.

    Sans ça, une machine dont le `.env` porte `LOCAL_SCORING_ENABLED=true` fait
    appeler LM Studio par `score_pending` dès qu'un test ne passe pas de
    pré-filtre explicite : la suite sort sur le réseau, devient lente et dépend
    d'un serveur local. Les tests qui veulent une cascade en injectent une
    eux-mêmes via le paramètre `prefilter`.
    """
    monkeypatch.setattr(settings, "local_scoring_enabled", False)


@pytest.fixture(autouse=True)
def _pas_de_fichiers_applicatifs_reels(monkeypatch, tmp_path):
    """Aucun test ne lit ni n'ecrit dans ~/Library/Application Support/Kinda.

    Sans ca, le `publication.json` pose depuis le vrai dashboard (mode `apercu`)
    primait sur le mode force par un test, qui echouait selon l'etat de la
    machine -- et un test pouvait ecraser le jeton ou le mode reels.
    """
    applicatif = tmp_path / "application-support-kinda"
    monkeypatch.setattr(settings, "meta_token_path", applicatif / "instagram_token.json")
    monkeypatch.setattr(settings, "publication_mode_path", applicatif / "publication.json")
    monkeypatch.setattr(settings, "publication_dir", applicatif / "publication")


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    with TestingSession() as session:
        yield session


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
