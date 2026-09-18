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
    monkeypatch.setattr(settings, "visuels_cache_dir", applicatif / "visuels")
    monkeypatch.setattr(settings, "illustration_cache_path", applicatif / "illustrations.json")
    monkeypatch.setattr(settings, "visuels_web_cache_path", applicatif / "visuels_web.json")


@pytest.fixture()
def reels_allumes(_pas_de_fichiers_applicatifs_reels):
    """Les reels sont en opt-in (coupes par defaut) : les tests qui exercent la
    chaine reel les allument explicitement, comme le ferait le dashboard."""
    import datetime as dt

    from app.publish.mode import enregistrer_reels

    enregistrer_reels(True, dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc))


@pytest.fixture(autouse=True)
def _pas_de_jeton_sourcing_ambiant(monkeypatch):
    """Le `.env` de l'utilisateur porte un vrai META_FB_ACCESS_TOKEN : sans ca,
    chaque `run_pipeline` de test appellerait debug_token chez Meta."""
    monkeypatch.setattr(settings, "meta_fb_access_token", None)


@pytest.fixture(autouse=True)
def _pas_de_juge_visuel_ambiant(monkeypatch):
    """Le `.env` de l'utilisateur porte une vraie ANTHROPIC_API_KEY : sans ca,
    chaque `create_post` de test enverrait des images a Claude vision. Les tests
    du juge injectent le leur (`juge=`), qui ne passe jamais par ce reglage."""
    monkeypatch.setattr(settings, "illustration_vision_enabled", False)


@pytest.fixture(autouse=True)
def _pas_de_recherche_web_ambiante(monkeypatch):
    """Meme motif : sans ca, chaque choix de visuels sans issue lancerait une
    vraie recherche web facturee. Les tests du dernier recours injectent leur
    chercheur (`chercheur=`) ou rallument le reglage explicitement."""
    monkeypatch.setattr(settings, "visuels_web_enabled", False)


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
