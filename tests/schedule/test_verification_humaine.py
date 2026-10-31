"""Ce que l'humain a vu, corrige ou retire est bien ce qui part (2026-10-31).

Les cinq defauts du bilan de la console, convertis depuis les reproductions du bilan.
Memes deux sessions que `test_course_arbitrage_gel.py`, dont les fixtures sont reprises :
rejouable sur Postgres par `KINDA_TEST_POSTGRES_URL`, jamais sur Neon.
"""

import datetime as dt

import pytest

import app.api.schedule as schedule_api
from app.models import Article, Creneau, EtatCreneau
from app.api.schedule import OCCUPANT_CHANGE
from app.schedule.store import appliquer_arbitrage
from tests.schedule.test_course_arbitrage_gel import (  # noqa: F401 -- fixtures
    BASE,
    _deux_carrousels,
    _ligne,
    client_web,
    fabrique,
    file_,
)


@pytest.fixture()
def a_l_heure_de_la_file(monkeypatch):
    monkeypatch.setattr(schedule_api, "_maintenant", lambda: BASE)


def _raison(fabrique, article_id):  # noqa: F811
    with fabrique() as db:
        return db.get(Article, article_id).file_exclu_raison


def _vu(file_, nom):  # noqa: F811
    return {"occupant_attendu": file_[nom], "post_attendu": file_[f"post_{nom}"]}


# --- 1. « Ne pas publier » et « Epingler » visent ce que l'humain a vu -------------------


@pytest.mark.usefixtures("a_l_heure_de_la_file")
def test_retirer_une_ligne_perimee_ne_touche_a_rien(client_web, fabrique, file_):  # noqa: F811
    """La reproduction de Karim : l'ecran montre A, l'arbitrage a mis B. Avant : 200, B
    exclue, A intacte, et le message disait « A ne partira pas »."""
    with fabrique() as web:
        appliquer_arbitrage(web, BASE)
    assert _ligne(fabrique, file_["tot"])[1] == file_["b"]

    reponse = client_web.patch(f"/schedule/{file_['tot']}", json={"sauter": True, **_vu(file_, "a")})

    assert reponse.status_code == 409
    assert reponse.json()["detail"] == OCCUPANT_CHANGE
    assert _raison(fabrique, file_["a"]) is None and _raison(fabrique, file_["b"]) is None
    assert _ligne(fabrique, file_["tot"])[:3] == (EtatCreneau.PROGRAMME, file_["b"], file_["post_b"])


@pytest.mark.usefixtures("a_l_heure_de_la_file")
def test_retirer_ce_qui_est_affiche_retire_bien(client_web, fabrique, file_):  # noqa: F811
    reponse = client_web.patch(f"/schedule/{file_['tot']}", json={"sauter": True, **_vu(file_, "a")})
    assert reponse.status_code == 200
    assert _raison(fabrique, file_["a"]) is not None and _raison(fabrique, file_["b"]) is None


@pytest.mark.usefixtures("a_l_heure_de_la_file")
@pytest.mark.parametrize("geste", [{"sauter": True}, {"epingle": True}, {"epingle": False}])
@pytest.mark.parametrize("present", [{}, {"occupant_attendu": 1}, {"post_attendu": 1}])
def test_le_vu_est_obligatoire(client_web, fabrique, file_, geste, present):  # noqa: F811
    avant = _ligne(fabrique, file_["tot"])
    reponse = client_web.patch(f"/schedule/{file_['tot']}", json={**geste, **present})
    assert reponse.status_code == 422
    assert "occupant_attendu" in reponse.json()["detail"]
    assert _ligne(fabrique, file_["tot"]) == avant
    assert _raison(fabrique, file_["a"]) is None


@pytest.mark.usefixtures("a_l_heure_de_la_file")
def test_le_post_compte_autant_que_l_article(client_web, fabrique, file_):  # noqa: F811
    """Meme actu, autre post (recomposition) : l'humain n'a pas lu ce texte-la."""
    vu = {"occupant_attendu": file_["a"], "post_attendu": file_["post_b"]}
    reponse = client_web.patch(f"/schedule/{file_['tot']}", json={"sauter": True, **vu})
    assert reponse.status_code == 409
    assert reponse.json()["detail"] == OCCUPANT_CHANGE
    assert _raison(fabrique, file_["a"]) is None


@pytest.mark.parametrize("geste", [{"sauter": True}, {"epingle": True}])
def test_l_occupant_qui_change_entre_la_lecture_et_l_ecriture(client_web, fabrique, file_, monkeypatch, geste):  # noqa: F811
    """La comparaison vit dans l'ecriture conditionnelle, pas dans un `if` avant elle :
    `_maintenant` tombe apres la lecture du creneau, l'objet en memoire dit encore A."""
    futur = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)
    with fabrique() as db:
        db.get(Creneau, file_["tot"]).publier_a = futur
        db.get(Creneau, file_["tard"]).publier_a = futur + dt.timedelta(hours=6)
        db.commit()
    vrai = schedule_api._maintenant
    deja = []

    def maintenant():
        if not deja:
            deja.append(True)
            with fabrique() as agent:
                creneau = agent.get(Creneau, file_["tot"])
                creneau.article_id, creneau.post_id = file_["b"], file_["post_b"]
                agent.commit()
        return vrai()

    monkeypatch.setattr(schedule_api, "_maintenant", maintenant)
    reponse = client_web.patch(f"/schedule/{file_['tot']}", json={**geste, **_vu(file_, "a")})

    assert reponse.status_code == 409
    assert reponse.json()["detail"] == OCCUPANT_CHANGE
    assert _raison(fabrique, file_["a"]) is None and _raison(fabrique, file_["b"]) is None
    with fabrique() as db:
        creneau = db.get(Creneau, file_["tot"])
        assert (creneau.article_id, creneau.epingle) == (file_["b"], False)


@pytest.mark.usefixtures("a_l_heure_de_la_file")
def test_epingler_une_ligne_perimee_ne_touche_a_rien(client_web, fabrique, file_, monkeypatch):  # noqa: F811
    futur = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)
    with fabrique() as db:
        creneau = db.get(Creneau, file_["tot"])
        creneau.publier_a = futur
        creneau.article_id, creneau.post_id = file_["b"], file_["post_b"]
        db.commit()
    monkeypatch.setattr(schedule_api, "_maintenant", lambda: futur - dt.timedelta(hours=5))
    reponse = client_web.patch(f"/schedule/{file_['tot']}", json={"epingle": True, **_vu(file_, "a")})
    assert reponse.status_code == 409 and reponse.json()["detail"] == OCCUPANT_CHANGE
    with fabrique() as db:
        assert db.get(Creneau, file_["tot"]).epingle is False
