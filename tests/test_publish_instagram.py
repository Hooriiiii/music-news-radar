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


def test_un_reel_donne_un_conteneur_reels_avec_cover_explicite():
    http = FauxHttp()
    conteneur = _client(http).preparer_reel("https://t/r.mp4", "https://t/c.jpg", "Legende")
    assert conteneur == "c1"
    methode, chemin, params = http.appels[0]
    assert (methode, chemin) == ("POST", "17841/media")
    assert params["media_type"] == "REELS"
    assert (params["video_url"], params["cover_url"]) == ("https://t/r.mp4", "https://t/c.jpg")
    assert (params["caption"], params["share_to_feed"]) == ("Legende", "true")
    assert "thumb_offset" not in params
    assert not any(c.endswith("media_publish") for _, c, _ in http.appels), "preparer ne publie pas"


def test_share_to_feed_peut_etre_coupe():
    http = FauxHttp()
    _client(http).preparer_reel("https://t/r.mp4", "https://t/c.jpg", "L", share_to_feed=False)
    assert http.appels[0][2]["share_to_feed"] == "false"


def test_un_reel_se_sonde_toutes_les_15_secondes():
    """L'encodage d'une video est plus long que celui d'une image."""
    pauses = []
    http = FauxHttp(statuts=("IN_PROGRESS", "IN_PROGRESS", "FINISHED"))
    ClientInstagram("j", "17841", http=http, version="v23.0", dormir=pauses.append).preparer_reel(
        "https://t/r.mp4", "https://t/c.jpg", "L"
    )
    assert pauses == [15.0, 15.0]


def test_un_reel_qui_ne_finit_jamais_leve_apres_le_delai_reel(monkeypatch):
    monkeypatch.setattr(mod, "ATTENTE_REEL_MAX_S", 30.0)
    pauses = []
    client = ClientInstagram("j", "17841", http=FauxHttp(statuts=("IN_PROGRESS",)), version="v23.0",
                             dormir=pauses.append)
    with pytest.raises(ErreurInstagram, match="IN_PROGRESS"):
        client.preparer_reel("https://t/r.mp4", "https://t/c.jpg", "L")
    assert len(pauses) == 2


# --- Carrousel avec videos ------------------------------------------------------------


def test_un_carrousel_mixte_cree_des_enfants_video_attendus_avant_le_parent():
    http = FauxHttp()
    conteneur = _client(http).preparer_carrousel(
        ["https://t/cover.jpg", {"type": "video", "url": "https://t/v.mp4"}, {"type": "photo", "url": "https://t/p.jpg"}],
        "L",
    )
    posts = [(c, p) for m, c, p in http.appels if m == "POST"]
    assert posts[0][1] == {"image_url": "https://t/cover.jpg", "is_carousel_item": "true", "access_token": "IGAAjeton"}
    assert posts[1][1] == {
        "media_type": "VIDEO", "video_url": "https://t/v.mp4", "is_carousel_item": "true", "access_token": "IGAAjeton",
    }
    assert posts[2][1]["image_url"] == "https://t/p.jpg"
    assert posts[3][1]["children"] == "c1,c2,c3"
    # L'enfant video est sonde AVANT la creation du parent.
    ordre = [(m, c) for m, c, _ in http.appels]
    assert ordre.index(("GET", "c2")) < ordre.index(("POST", "17841/media"), 3)
    assert conteneur == "c4"


def test_un_enfant_video_est_sonde_au_rythme_des_reels():
    pauses = []
    http = FauxHttp(statuts=("IN_PROGRESS", "FINISHED"))
    ClientInstagram("j", "17841", http=http, version="v23.0", dormir=pauses.append).preparer_carrousel(
        ["https://t/cover.jpg", {"type": "video", "url": "https://t/v.mp4"}], "L"
    )
    assert pauses[0] == mod.ATTENTE_REEL_PAS_S


class HttpParConteneur(FauxHttp):
    """Statut par identifiant de conteneur, FINISHED par defaut."""

    def __init__(self, statuts_par_id):
        super().__init__()
        self.statuts_par_id = statuts_par_id

    def request(self, methode, url, params=None):
        chemin = url.removeprefix(BASE + "/")
        if methode == "GET":
            self.appels.append((methode, chemin, dict(params)))
            return httpx.Response(200, json={"status_code": self.statuts_par_id.get(chemin, "FINISHED")})
        return super().request(methode, url, params)


def test_un_enfant_video_en_erreur_est_retire_du_carrousel():
    http = HttpParConteneur({"c2": "ERROR"})
    conteneur = _client(http).preparer_carrousel(
        ["https://t/cover.jpg", {"type": "video", "url": "https://t/v.mp4"}, "https://t/p.jpg"], "L"
    )
    parent = [p for m, c, p in http.appels if m == "POST" and p.get("media_type") == "CAROUSEL"]
    assert parent[0]["children"] == "c1,c3"
    assert conteneur == "c4"


def test_sans_deux_elements_restants_la_cover_part_seule():
    http = HttpParConteneur({"c2": "EXPIRED"})
    conteneur = _client(http).preparer_carrousel(
        ["https://t/cover.jpg", {"type": "video", "url": "https://t/v.mp4"}], "L"
    )
    posts = [p for m, c, p in http.appels if m == "POST"]
    assert posts[-1] == {"image_url": "https://t/cover.jpg", "caption": "L", "access_token": "IGAAjeton"}
    assert not any(p.get("media_type") == "CAROUSEL" for p in posts)
    assert conteneur == "c3"


def test_une_video_seule_n_est_pas_un_carrousel():
    with pytest.raises(ErreurInstagram, match="video seule"):
        _client(FauxHttp()).preparer_carrousel([{"type": "video", "url": "https://t/v.mp4"}], "L")
