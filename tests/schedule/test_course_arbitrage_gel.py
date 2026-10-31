"""La course entre le processus web (arbitrage, PATCH) et l'agent de publication.

DEUX SESSIONS SUR DEUX CONNEXIONS, contrairement au `db_session` de `conftest.py`
(`StaticPool`, une seule connexion : un commit de l'« agent » y committerait aussi
le « web »). `autoflush=False` comme en production.

SQLite par defaut. Pour rejouer les memes tests sur Postgres en Read Committed, la
semantique de la production :
    KINDA_TEST_POSTGRES_URL=postgresql+psycopg://... uv run pytest tests/schedule/test_course_arbitrage_gel.py
Ce fichier CREE ET DETRUIT toutes les tables de cette base : un cluster jetable,
jamais Neon.

Le « pendant » est pose a l'endroit ou il tombe vraiment : apres la lecture des
creneaux, avant les ecritures. Rien n'est simule au-dela de cet ordre.
"""

import datetime as dt
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url
from sqlalchemy.orm import sessionmaker

import app.api.schedule as schedule_api
import app.schedule.runner as runner
import app.schedule.store as store
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Article, Creneau, EtatCreneau, FormatCreneau, Post, Source, SourceType
from app.schedule.runner import RapportPublication
from app.schedule.slots import prochains_horaires
from app.schedule.store import appliquer_arbitrage

BASE = dt.datetime(2026, 9, 20, 4, 0, tzinfo=dt.timezone.utc)
FICHIERS_DE_A = ["a_01.jpg", "a_02.jpg"]


@pytest.fixture(autouse=True)
def _deux_carrousels(monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
    monkeypatch.setattr(settings, "publication_creneaux", 2)


@pytest.fixture()
def fabrique(tmp_path):
    url = os.environ.get("KINDA_TEST_POSTGRES_URL") or f"sqlite:///{tmp_path / 'course.db'}"
    # Garde-fou : ce fixture detruit les tables. Jamais une base distante.
    assert make_url(url).host in (None, "localhost", "127.0.0.1")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _article(db, source, nom, relevance):
    article = Article(
        source_id=source.id, url=f"https://t/{nom}", title=f"Actu {nom}", raw_hash=f"h{nom}",
        relevance_score=relevance, hotness_score=20, published_at=BASE - dt.timedelta(hours=2),
        media=[{"type": "photo", "url": f"https://img/{nom}.jpg"}],
    )
    db.add(article)
    db.flush()
    post = Post(
        article_id=article.id, article_title=article.title, article_url=article.url,
        caption=f"Legende de {nom}", template="kinda_post",
        slides=[{"kind": "cover", "headline": "**A**", "title": "**T**", "subtitle": "**S**",
                 "image_url": f"https://img/{nom}.jpg"}],
    )
    db.add(post)
    db.flush()
    return article.id, post.id


@pytest.fixture()
def file_(fabrique):
    """A tient H0 et B tient H1, tous deux composes. B vaut mieux que A : le vrai
    arbitrage veut les ECHANGER -- une grosse actu qui decale la precedente, et le
    brouillon de B existe deja, puisque B occupait deja une place."""
    h0, h1 = prochains_horaires(BASE, 2)
    with fabrique() as db:
        source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
        db.add(source)
        db.flush()
        a, post_a = _article(db, source, "A", 70)
        b, post_b = _article(db, source, "B", 95)
        tot = Creneau(publier_a=h0, article_id=a, post_id=post_a, etat=EtatCreneau.PROGRAMME)
        tard = Creneau(publier_a=h1, article_id=b, post_id=post_b, etat=EtatCreneau.PROGRAMME)
        db.add_all([tot, tard])
        db.commit()
        return {"a": a, "post_a": post_a, "b": b, "post_b": post_b, "tot": tot.id, "tard": tard.id, "h0": h0}


def _geler_comme_l_agent(fabrique, creneau_id):
    """La derniere ecriture de `runner._geler`, sur sa propre connexion."""
    with fabrique() as db:
        creneau = db.get(Creneau, creneau_id)
        assert runner._ecrire_si(
            db, creneau, runner._attendu(creneau),
            fichiers=FICHIERS_DE_A, elements=None, etat=EtatCreneau.GELE, gele_le=BASE, erreur=None,
        )


def _pendant_l_arbitrage(monkeypatch, geste):
    """`geste` part une fois, entre la lecture des creneaux et les ecritures."""
    vrai = store.arbitrer
    deja = []

    def arbitrer(places, candidats, maintenant):
        if places and not deja:
            deja.append(True)
            geste()
        return vrai(places, candidats, maintenant)

    monkeypatch.setattr(store, "arbitrer", arbitrer)


def _ligne(fabrique, creneau_id):
    with fabrique() as db:
        c = db.get(Creneau, creneau_id)
        return c.etat, c.article_id, c.post_id, c.fichiers


def _publier_a_l_heure(fabrique, heure):
    """Le vrai `_publier`, celui de l'agent, avec un publieur factice."""
    recus = []

    def publier(_dossier, noms, legende, _vraiment, **_crochets):
        recus.append((noms, legende))
        return "ig42"

    moment = heure + dt.timedelta(minutes=1)
    with fabrique() as db:
        runner._publier(
            db, moment, lambda: "reel", {FormatCreneau.CARROUSEL: publier}, lambda _m: None,
            RapportPublication(), lambda: moment, lambda _s: None, set(),
        )
    return recus


def test_l_arbitrage_ne_change_pas_l_article_d_un_creneau_gele_pendant_sa_lecture(fabrique, file_, monkeypatch):
    """PREUVE PAR MUTATION, et c'est la garantie contre le FAUX POST.

    Sans la condition sur l'etat, l'arbitrage ecrit B sur sa lecture perimee.
    `creneau.etat = PROGRAMME` n'emettait rien (valeur inchangee aux yeux de
    SQLAlchemy) : le creneau restait GELE, avec l'article et le post de B et les
    fichiers de A. Le post de B porte bien l'article du creneau, donc le controle
    de `_publier` passe, et les visuels de A partent avec la legende de B.
    """
    _pendant_l_arbitrage(monkeypatch, lambda: _geler_comme_l_agent(fabrique, file_["tot"]))

    with fabrique() as web:
        appliquer_arbitrage(web, BASE)

    assert _ligne(fabrique, file_["tot"]) == (EtatCreneau.GELE, file_["a"], file_["post_a"], FICHIERS_DE_A)
    assert _publier_a_l_heure(fabrique, file_["h0"]) == [(FICHIERS_DE_A, "Legende de A")]


def test_un_creneau_saute_ne_met_pas_son_article_sur_deux_creneaux(fabrique, file_, monkeypatch):
    """L'arbitrage voulait A en H1 parce qu'il croyait A sorti de H0. Sauter H0 seul
    et ecrire H1 poserait A sur deux creneaux : le lot du format est donc abandonne,
    et le passage suivant reprend avec la verite."""
    _pendant_l_arbitrage(monkeypatch, lambda: _geler_comme_l_agent(fabrique, file_["tot"]))

    with fabrique() as web:
        appliquer_arbitrage(web, BASE)

    assert _ligne(fabrique, file_["tard"])[:3] == (EtatCreneau.PROGRAMME, file_["b"], file_["post_b"])

    # L'arbitrage suivant ne croise rien : H0 est verrouille, B reste en H1.
    with fabrique() as web:
        creneaux = appliquer_arbitrage(web, BASE)
    assert {c.id: c.article_id for c in creneaux} == {file_["tot"]: file_["a"], file_["tard"]: file_["b"]}


def test_sans_course_l_arbitrage_echange_toujours_les_articles(fabrique, file_):
    """Le temoin : la condition ne change pas ce que l'arbitrage choisit."""
    with fabrique() as web:
        creneaux = appliquer_arbitrage(web, BASE)
        vus = {c.id: (c.etat, c.article_id, c.post_id) for c in creneaux}

    attendu = {
        file_["tot"]: (EtatCreneau.PROGRAMME, file_["b"], file_["post_b"]),
        file_["tard"]: (EtatCreneau.PROGRAMME, file_["a"], file_["post_a"]),
    }
    assert vus == attendu, "ce que rend l'arbitrage"
    assert _ligne(fabrique, file_["tot"])[:3] == attendu[file_["tot"]], "ce qui est en base"


def test_l_arbitrage_ne_libere_pas_un_creneau_gele_pendant_sa_lecture(fabrique, file_, monkeypatch):
    """Quand il n'a plus personne a y mettre, l'arbitrage ecrivait LIBRE : la, l'etat
    change a ses yeux et l'ecriture part, ce qui jetait un gel en silence."""
    with fabrique() as db:
        for article_id in (file_["a"], file_["b"]):
            db.get(Article, article_id).file_exclu_raison = "exclu"
        db.commit()
    _pendant_l_arbitrage(monkeypatch, lambda: _geler_comme_l_agent(fabrique, file_["tot"]))

    with fabrique() as web:
        appliquer_arbitrage(web, BASE)

    assert _ligne(fabrique, file_["tot"]) == (EtatCreneau.GELE, file_["a"], file_["post_a"], FICHIERS_DE_A)


@pytest.mark.parametrize("champ", ["article_id", "post_id"])
def test_l_arbitrage_ne_reprend_pas_un_creneau_qui_a_change_sans_changer_d_etat(fabrique, file_, monkeypatch, champ):
    """PREUVE PAR MUTATION des deux autres conditions : un PATCH ou une composition
    passe entre la lecture et l'ecriture, le creneau reste PROGRAMME."""
    autre = {"article_id": file_["b"], "post_id": file_["post_b"]}[champ]

    def geste():
        with fabrique() as db:
            creneau = db.get(Creneau, file_["tot"])
            assert runner._ecrire_si(db, creneau, runner._attendu(creneau), **{champ: autre})

    _pendant_l_arbitrage(monkeypatch, geste)

    with fabrique() as web:
        appliquer_arbitrage(web, BASE)

    avant = {"article_id": file_["a"], "post_id": file_["post_a"], champ: autre}
    assert _ligne(fabrique, file_["tot"])[1:3] == (avant["article_id"], avant["post_id"])


@pytest.fixture()
def client_web(fabrique):
    def session():
        with fabrique() as db:
            yield db

    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize("action", ["article_id", "decaler", "epingle"])
def test_le_patch_rend_409_si_l_agent_a_gele_pendant_sa_lecture(client_web, fabrique, file_, monkeypatch, action):
    """Meme course, meme faux post pour `article_id` et `decaler` : `_maintenant` est
    appele juste apres la lecture du creneau, le gel tombe donc apres le controle
    d'etat qui, lui, ne lit que l'objet en memoire."""
    vrai = schedule_api._maintenant
    deja = []

    def maintenant():
        if not deja:
            deja.append(True)
            _geler_comme_l_agent(fabrique, file_["tot"])
        return vrai()

    monkeypatch.setattr(schedule_api, "_maintenant", maintenant)
    # Les creneaux doivent etre encore a venir pour l'horloge reelle de l'API.
    futur = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)
    with fabrique() as db:
        db.get(Creneau, file_["tot"]).publier_a = futur
        db.get(Creneau, file_["tard"]).publier_a = futur + dt.timedelta(hours=6)
        db.get(Article, file_["a"]).published_at = futur
        db.commit()
    corps = {"article_id": {"article_id": file_["b"]}, "decaler": {"decaler": True}, "epingle": {"epingle": True}}[action]

    reponse = client_web.patch(f"/schedule/{file_['tot']}", json=corps)

    assert reponse.status_code == 409
    assert "entre-temps" in reponse.json()["detail"]
    assert _ligne(fabrique, file_["tot"]) == (EtatCreneau.GELE, file_["a"], file_["post_a"], FICHIERS_DE_A)
    assert _ligne(fabrique, file_["tard"])[:3] == (EtatCreneau.PROGRAMME, file_["b"], file_["post_b"])
