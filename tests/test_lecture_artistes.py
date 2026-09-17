"""La lecture en direct d'un compte d'artiste, a la composition : bornee par
passage et par jour, en cache, en pause sur le quota Graph."""

import datetime as dt

import httpx
import pytest

from app.config import settings
from app.content.lecture_artistes import FICHIER_ETAT, LecteurComptes, LectureDifferee
from app.pipeline.comptes_artistes import CompteArtiste
from app.sources.instagram import QuotaGraphAtteint

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
PAYLOAD = {
    "business_discovery": {
        "followers_count": 10,
        "media": {
            "data": [
                {
                    "id": "1",
                    "caption": "GTA VI soundtrack out now with my track",
                    "media_type": "IMAGE",
                    "media_url": "https://cdn.ig/fred.jpg",
                    "permalink": "https://www.instagram.com/p/FRED1/",
                    "timestamp": "2026-09-17T10:00:00+0000",
                }
            ]
        },
    }
}


@pytest.fixture(autouse=True)
def _jeton(monkeypatch):
    monkeypatch.setattr(settings, "meta_fb_access_token", "EAAsecret")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")


class Lecture:
    def __init__(self, reponse=PAYLOAD, erreur=None):
        self.reponse, self.erreur, self.appels = reponse, erreur, []

    def __call__(self, compte, limite):
        self.appels.append(compte)
        if self.erreur is not None:
            raise self.erreur
        return self.reponse


def _compte(handle="fredagain", dernier_appel=None, source_id=1):
    return CompteArtiste(source_id, handle, "Fred again..", dernier_appel)


def test_une_lecture_rend_les_posts_du_compte():
    lecture = Lecture()
    (post,) = LecteurComptes(lire=lecture)(_compte(), MAINTENANT)
    assert lecture.appels == ["fredagain"]
    assert (post.permalink, post.media[0]["url"], post.article_id) == (
        "https://www.instagram.com/p/FRED1/",
        "https://cdn.ig/fred.jpg",
        None,
    )
    assert post.publie_le == dt.datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
    assert post.compte == _compte()


def test_sans_jeton_aucune_lecture(monkeypatch):
    monkeypatch.setattr(settings, "meta_fb_access_token", None)
    lecture = Lecture()
    assert LecteurComptes(lire=lecture)(_compte(), MAINTENANT) == []
    assert lecture.appels == []


def test_un_compte_lu_recemment_par_le_pipeline_n_est_pas_relu():
    lecture = Lecture()
    frais = _compte(dernier_appel=MAINTENANT - dt.timedelta(hours=1))
    assert LecteurComptes(lire=lecture)(frais, MAINTENANT) == []
    assert lecture.appels == []


def test_le_cache_evite_une_seconde_lecture_pendant_six_heures():
    lecture = Lecture()
    LecteurComptes(lire=lecture)(_compte(), MAINTENANT)
    assert len(LecteurComptes(lire=lecture)(_compte(), MAINTENANT + dt.timedelta(hours=5))) == 1
    assert lecture.appels == ["fredagain"]
    LecteurComptes(lire=lecture)(_compte(), MAINTENANT + dt.timedelta(hours=7))
    assert lecture.appels == ["fredagain", "fredagain"]


def test_deux_lectures_par_passage_au_plus():
    lecture = Lecture()
    lecteur = LecteurComptes(lire=lecture)
    lecteur(_compte("a"), MAINTENANT)
    lecteur(_compte("b"), MAINTENANT)
    with pytest.raises(LectureDifferee, match="passage"):
        lecteur(_compte("c"), MAINTENANT)
    assert lecture.appels == ["a", "b"]


def test_un_budget_par_jour(monkeypatch):
    monkeypatch.setattr(settings, "visuels_lectures_max_par_jour", 1)
    lecture = Lecture()
    LecteurComptes(lire=lecture)(_compte("a"), MAINTENANT)
    with pytest.raises(LectureDifferee, match="jour"):
        LecteurComptes(lire=lecture)(_compte("b"), MAINTENANT)
    LecteurComptes(lire=lecture)(_compte("b"), MAINTENANT + dt.timedelta(days=1))
    assert lecture.appels == ["a", "b"]


def test_le_quota_graph_met_en_pause_une_heure():
    with pytest.raises(LectureDifferee, match="quota"):
        LecteurComptes(lire=Lecture(erreur=QuotaGraphAtteint("code 4")))(_compte("a"), MAINTENANT)
    lecture = Lecture()
    with pytest.raises(LectureDifferee, match="pause"):
        LecteurComptes(lire=lecture)(_compte("b"), MAINTENANT + dt.timedelta(minutes=30))
    assert lecture.appels == []
    LecteurComptes(lire=lecture)(_compte("b"), MAINTENANT + dt.timedelta(minutes=61))
    assert lecture.appels == ["b"]


def test_une_panne_reseau_differe_sans_pause():
    erreur = httpx.ConnectError("boom")
    with pytest.raises(LectureDifferee) as exc:
        LecteurComptes(lire=Lecture(erreur=erreur))(_compte("a"), MAINTENANT)
    assert "EAAsecret" not in str(exc.value)
    lecture = Lecture()
    LecteurComptes(lire=lecture)(_compte("b"), MAINTENANT)
    assert lecture.appels == ["b"]


def test_un_compte_illisible_ne_rend_rien_sans_bloquer():
    lecteur = LecteurComptes(lire=Lecture(erreur=RuntimeError("Meta HTTP 400 (code 110) : Invalid user")))
    assert lecteur(_compte(), MAINTENANT) == []


def test_l_etat_vit_a_cote_du_jeton():
    LecteurComptes(lire=Lecture())(_compte(), MAINTENANT)
    assert (settings.meta_token_path.parent / FICHIER_ETAT).is_file()
