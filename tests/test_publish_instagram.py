"""Le client Instagram, sur un faux HTTP : aucun appel ne sort."""

import httpx
import pytest

import app.publish.instagram as mod
from app.publish.instagram import ClientInstagram, ErreurInstagram

BASE = "https://graph.instagram.com/v23.0"


class FauxHttp:
    """Rejoue des reponses selon (methode, chemin), et consigne les appels."""

    def __init__(self, statuts=("FINISHED",)):
        self.appels = []
        self.statuts = list(statuts)
        self.compteur = 0

    def request(self, methode, url, params=None):
        self.appels.append((methode, url.removeprefix(BASE + "/"), dict(params)))
        chemin = url.removeprefix(BASE + "/")
        if methode == "POST" and chemin.endswith("/media"):
            self.compteur += 1
            return httpx.Response(200, json={"id": f"c{self.compteur}"})
        if methode == "POST" and chemin.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "media999"})
        if methode == "GET":
            statut = self.statuts.pop(0) if len(self.statuts) > 1 else self.statuts[0]
            return httpx.Response(200, json={"status_code": statut, "status": "detail"})
        raise AssertionError(f"appel inattendu {methode} {url}")


def _client(http, **kwargs):
    return ClientInstagram("IGAAjeton", "17841", http=http, version="v23.0", dormir=lambda s: None, **kwargs)


def test_une_seule_image_donne_un_conteneur_image_avec_legende():
    http = FauxHttp()
    assert _client(http).preparer_carrousel(["https://t/a.jpg"], "Legende") == "c1"
    methode, chemin, params = http.appels[0]
    assert (methode, chemin) == ("POST", "17841/media")
    assert params["image_url"] == "https://t/a.jpg"
    assert params["caption"] == "Legende"
    assert params["access_token"] == "IGAAjeton"
    assert not any(c.endswith("media_publish") for _, c, _ in http.appels), "preparer ne publie pas"


def test_plusieurs_images_donnent_un_carrousel():
    http = FauxHttp()
    conteneur = _client(http).preparer_carrousel(["https://t/1.jpg", "https://t/2.jpg", "https://t/3.jpg"], "L")
    posts = [p for m, c, p in http.appels if m == "POST"]
    assert all(p.get("is_carousel_item") == "true" for p in posts[:3])
    assert posts[3]["media_type"] == "CAROUSEL"
    assert posts[3]["children"] == "c1,c2,c3"
    assert posts[3]["caption"] == "L"
    assert conteneur == "c4"


def test_au_dela_de_dix_images_on_tronque():
    http = FauxHttp()
    _client(http).preparer_carrousel([f"https://t/{i}.jpg" for i in range(12)], "L")
    enfants = [p for m, c, p in http.appels if m == "POST" and p.get("is_carousel_item")]
    assert len(enfants) == 10


def test_on_attend_finished_avant_de_rendre_la_main():
    """Publier un conteneur IN_PROGRESS echoue : le sondage n'est pas optionnel."""
    pauses = []
    http = FauxHttp(statuts=("IN_PROGRESS", "IN_PROGRESS", "FINISHED"))
    ClientInstagram("j", "17841", http=http, version="v23.0", dormir=pauses.append).preparer_carrousel(
        ["https://t/a.jpg"], "L"
    )
    assert len(pauses) == 2


def test_un_conteneur_en_erreur_leve():
    with pytest.raises(ErreurInstagram, match="ERROR"):
        _client(FauxHttp(statuts=("ERROR",))).preparer_carrousel(["https://t/a.jpg"], "L")


def test_un_conteneur_qui_ne_finit_jamais_leve(monkeypatch):
    monkeypatch.setattr(mod, "ATTENTE_MAX_S", 6.0)
    with pytest.raises(ErreurInstagram, match="IN_PROGRESS"):
        _client(FauxHttp(statuts=("IN_PROGRESS",))).preparer_carrousel(["https://t/a.jpg"], "L")


def test_le_message_d_erreur_de_meta_remonte():
    class HttpRefus(FauxHttp):
        def request(self, methode, url, params=None):
            return httpx.Response(400, json={"error": {
                "message": "Invalid parameter", "error_user_msg": "Format d'image non pris en charge",
            }})

    with pytest.raises(ErreurInstagram, match="Format d'image non pris en charge"):
        _client(HttpRefus()).preparer_carrousel(["https://t/a.png"], "L")


def test_publier_appelle_media_publish():
    http = FauxHttp()
    assert _client(http).publier("c4") == "media999"
    assert http.appels[-1] == ("POST", "17841/media_publish", {"creation_id": "c4", "access_token": "IGAAjeton"})


def test_aucune_image_est_une_erreur():
    with pytest.raises(ErreurInstagram, match="aucune image"):
        _client(FauxHttp()).preparer_carrousel([], "L")
