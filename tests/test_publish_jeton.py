"""Le jeton IG expire a 60 jours : il se rafraichit, et il previent avant de mourir."""

import datetime as dt
import json

import httpx
import pytest

from app.config import settings
from app.publish.jeton import (
    EtatJeton,
    JetonIndisponible,
    alerte_expiration,
    charger,
    enregistrer,
    rafraichir_si_necessaire,
)

MAINTENANT = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.timezone.utc)


class FauxHttp:
    def __init__(self, reponse=None):
        self.reponse = reponse
        self.appels = []

    def get(self, url, params=None, timeout=None):
        self.appels.append((url, params))
        if self.reponse is None:
            raise AssertionError("aucun appel reseau n'etait attendu")
        return self.reponse


def test_sans_fichier_le_jeton_vient_du_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", " IGAAinitial ")
    assert charger(tmp_path / "jeton.json").access_token == "IGAAinitial"


def test_sans_fichier_ni_env_l_erreur_dit_quoi_faire(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", None)
    with pytest.raises(JetonIndisponible, match="META_ACCESS_TOKEN"):
        charger(tmp_path / "jeton.json")


def test_aller_retour_disque_et_droits_restreints(tmp_path):
    chemin = tmp_path / "sous" / "jeton.json"
    etat = EtatJeton("IGAAx", rafraichi_le=MAINTENANT, expire_le=MAINTENANT + dt.timedelta(days=60))
    enregistrer(chemin, etat)
    assert charger(chemin) == etat
    assert chemin.stat().st_mode & 0o777 == 0o600


def test_un_jeton_rafraichi_recemment_ne_declenche_aucun_appel(tmp_path):
    chemin = tmp_path / "jeton.json"
    enregistrer(chemin, EtatJeton("IGAAx", rafraichi_le=MAINTENANT - dt.timedelta(days=2)))
    etat, erreur = rafraichir_si_necessaire(chemin, MAINTENANT, http=FauxHttp())
    assert (etat.access_token, erreur) == ("IGAAx", None)


def test_un_jeton_jamais_rafraichi_est_rafraichi_et_enregistre(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "IGAAancien")
    chemin = tmp_path / "jeton.json"
    http = FauxHttp(httpx.Response(200, json={
        "access_token": "IGAAneuf", "token_type": "bearer", "expires_in": 5184000,
    }))

    etat, erreur = rafraichir_si_necessaire(chemin, MAINTENANT, http=http)

    assert erreur is None
    assert etat.access_token == "IGAAneuf"
    assert etat.expire_le == MAINTENANT + dt.timedelta(days=60)
    url, params = http.appels[0]
    assert url == "https://graph.instagram.com/refresh_access_token"
    assert params == {"grant_type": "ig_refresh_token", "access_token": "IGAAancien"}
    assert json.loads(chemin.read_text())["access_token"] == "IGAAneuf"


def test_un_refus_garde_l_ancien_jeton_et_rend_le_message(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "IGAAancien")
    chemin = tmp_path / "jeton.json"
    http = FauxHttp(httpx.Response(400, json={"error": {"message": "Session has expired"}}))

    etat, erreur = rafraichir_si_necessaire(chemin, MAINTENANT, http=http)

    assert etat.access_token == "IGAAancien"
    assert "Session has expired" in erreur
    assert charger(chemin).echec_le == MAINTENANT


def test_apres_un_echec_on_ne_reessaie_pas_avant_six_heures(tmp_path):
    """L'agent tourne toutes les 10 minutes : sans ce delai, un jeton mort
    produirait un appel refuse toutes les 10 minutes."""
    chemin = tmp_path / "jeton.json"
    enregistrer(chemin, EtatJeton("IGAAx", echec_le=MAINTENANT - dt.timedelta(hours=1)))
    etat, erreur = rafraichir_si_necessaire(chemin, MAINTENANT, http=FauxHttp())
    assert etat.access_token == "IGAAx"
    assert erreur is None


def test_l_alerte_se_declenche_a_sept_jours_de_l_expiration():
    loin = EtatJeton("x", expire_le=MAINTENANT + dt.timedelta(days=10))
    proche = EtatJeton("x", expire_le=MAINTENANT + dt.timedelta(days=5))
    assert alerte_expiration(loin, MAINTENANT) is None
    assert "5 jour" in alerte_expiration(proche, MAINTENANT)
    assert alerte_expiration(EtatJeton("x"), MAINTENANT) is None
