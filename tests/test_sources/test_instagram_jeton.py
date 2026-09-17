import datetime as dt
import json

import pytest

from app.config import settings
from app.sources import instagram_jeton as module
from app.sources.instagram_jeton import (
    FICHIER_ALERTE,
    JetonSourcingInvalide,
    alerte_jeton_sourcing,
    expiration_jeton_sourcing,
    surveiller_jeton_sourcing,
)

MAINTENANT = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.UTC)
SECRET = "EAAsecret-sourcing-123"


class FauxHttp:
    def __init__(self, statut=200, corps=None, exc=None):
        self.statut, self.corps, self.exc = statut, corps, exc
        self.appels = []

    def get(self, url, params=None, timeout=None):
        self.appels.append((url, params))
        if self.exc:
            raise self.exc
        return FausseReponse(self.statut, self.corps)


class FausseReponse:
    def __init__(self, statut, corps):
        self.status_code, self._corps = statut, corps

    def json(self):
        return self._corps


def _debug(expires_at, valide=True):
    return {"data": {"is_valid": valide, "expires_at": expires_at, "data_access_expires_at": 0}}


@pytest.fixture()
def jeton(monkeypatch):
    monkeypatch.setattr(settings, "meta_fb_access_token", SECRET)
    monkeypatch.setattr(settings, "meta_api_version", "v23.0")


# --- expiration_jeton_sourcing ---

def test_expiration_lit_expires_at_de_debug_token(jeton):
    http = FauxHttp(corps=_debug(1794787200))  # 2026-11-16T00:00:00Z
    assert expiration_jeton_sourcing(http=http) == dt.datetime(2026, 11, 16, tzinfo=dt.UTC)
    url, params = http.appels[0]
    assert url == "https://graph.facebook.com/v23.0/debug_token"
    assert params["input_token"] == SECRET and params["access_token"] == SECRET


def test_expires_at_zero_veut_dire_jamais(jeton):
    assert expiration_jeton_sourcing(http=FauxHttp(corps=_debug(0))) is None


def test_un_jeton_expire_est_signale_comme_invalide(jeton):
    corps = {"error": {"code": 190, "message": "Session has expired", "type": "OAuthException"}}
    with pytest.raises(JetonSourcingInvalide):
        expiration_jeton_sourcing(http=FauxHttp(statut=400, corps=corps))


def test_is_valid_faux_est_invalide(jeton):
    with pytest.raises(JetonSourcingInvalide):
        expiration_jeton_sourcing(http=FauxHttp(corps=_debug(1794787200, valide=False)))


def test_une_autre_erreur_ne_contient_pas_le_jeton(jeton):
    corps = {"error": {"code": 1, "message": "An unknown error occurred"}}
    with pytest.raises(RuntimeError) as exc:
        expiration_jeton_sourcing(http=FauxHttp(statut=500, corps=corps))
    assert SECRET not in str(exc.value)
    assert not isinstance(exc.value, JetonSourcingInvalide)


# --- alerte_jeton_sourcing ---

def test_pas_d_alerte_loin_de_l_expiration():
    assert alerte_jeton_sourcing(MAINTENANT + dt.timedelta(days=8), MAINTENANT) is None


def test_pas_d_alerte_pour_un_jeton_sans_expiration():
    assert alerte_jeton_sourcing(None, MAINTENANT) is None


def test_alerte_a_sept_jours_explique_le_renouvellement():
    message = alerte_jeton_sourcing(MAINTENANT + dt.timedelta(days=7), MAINTENANT)
    assert message is not None
    assert "7 jour" in message
    assert "developers.facebook.com/tools/explorer" in message
    assert "Étendre le jeton d'accès" in message
    assert "META_FB_ACCESS_TOKEN" in message


def test_alerte_quand_le_jeton_est_deja_expire():
    message = alerte_jeton_sourcing(MAINTENANT - dt.timedelta(hours=1), MAINTENANT)
    assert message is not None and "expiré" in message
    assert "Étendre le jeton d'accès" in message


# --- surveiller_jeton_sourcing ---

def _surveiller(http, notifications, maintenant=MAINTENANT):
    return surveiller_jeton_sourcing(
        maintenant, notifier=lambda titre, message: notifications.append(message), http=http
    )


def test_sans_jeton_rien_ne_part(monkeypatch):
    monkeypatch.setattr(settings, "meta_fb_access_token", None)
    http, notifications = FauxHttp(exc=AssertionError("aucun appel")), []
    assert _surveiller(http, notifications) is None
    assert http.appels == [] and notifications == []


def test_une_seule_notification_par_jour(jeton):
    expire = int((MAINTENANT + dt.timedelta(days=3)).timestamp())
    notifications = []
    for heure in range(3):
        _surveiller(FauxHttp(corps=_debug(expire)), notifications, MAINTENANT + dt.timedelta(hours=heure))
    assert len(notifications) == 1
    chemin = settings.meta_token_path.parent / FICHIER_ALERTE
    assert json.loads(chemin.read_text())["alerte_envoyee_le"] == MAINTENANT.date().isoformat()

    _surveiller(FauxHttp(corps=_debug(expire)), notifications, MAINTENANT + dt.timedelta(days=1))
    assert len(notifications) == 2


def test_un_jeton_sain_ne_notifie_pas(jeton):
    notifications = []
    expire = int((MAINTENANT + dt.timedelta(days=30)).timestamp())
    assert _surveiller(FauxHttp(corps=_debug(expire)), notifications) is None
    assert notifications == []


def test_un_jeton_deja_invalide_notifie_l_expiration(jeton):
    corps = {"error": {"code": 190, "message": "Session has expired"}}
    notifications = []
    _surveiller(FauxHttp(statut=400, corps=corps), notifications)
    assert len(notifications) == 1 and "expiré" in notifications[0]


def test_une_panne_reseau_ne_casse_rien_et_ne_notifie_pas(jeton, caplog):
    notifications = []
    http = FauxHttp(exc=ConnectionError(f"https://graph.facebook.com/?access_token={SECRET}"))
    assert _surveiller(http, notifications) is None
    assert notifications == []
    assert SECRET not in caplog.text
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_un_notificateur_en_panne_ne_casse_rien(jeton):
    expire = int((MAINTENANT + dt.timedelta(days=1)).timestamp())

    def casse(titre, message):
        raise OSError("osascript absent")

    surveiller_jeton_sourcing(MAINTENANT, notifier=casse, http=FauxHttp(corps=_debug(expire)))


def test_un_fichier_d_alerte_illisible_ne_bloque_pas(jeton):
    chemin = settings.meta_token_path.parent / FICHIER_ALERTE
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("{pas du json")
    expire = int((MAINTENANT + dt.timedelta(days=1)).timestamp())
    notifications = []
    _surveiller(FauxHttp(corps=_debug(expire)), notifications)
    assert len(notifications) == 1


def test_le_module_n_utilise_jamais_le_jeton_de_publication(jeton, monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "IGAA-publication")
    http = FauxHttp(corps=_debug(0))
    module.expiration_jeton_sourcing(http=http)
    assert "IGAA-publication" not in json.dumps(http.appels)
