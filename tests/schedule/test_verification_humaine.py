"""Ce que l'humain a vu, corrige ou retire est bien ce qui part (2026-10-31).

Les cinq defauts du bilan de la console, convertis depuis les reproductions du bilan.
Memes deux sessions que `test_course_arbitrage_gel.py`, dont les fixtures sont reprises :
rejouable sur Postgres par `KINDA_TEST_POSTGRES_URL`, jamais sur Neon.
"""

import datetime as dt
import json

import pytest

import app.api.posts as posts_api
import app.api.schedule as schedule_api
import app.schedule.runner as runner
from app.api.schedule import OCCUPANT_CHANGE
from app.config import settings
from app.content.choix_visuels import ChoixVisuels
from app.content.compose import CoverVariante, PostDraft
from app.content.media import entree
from app.content.visuels import actif
from app.models import Article, Creneau, EtatCreneau, Post
from app.publish.preparation import PreparationDifferee, PreparationImpossible
from app.schedule import relecture
from app.schedule.pause_visuels import articles_en_pause
from app.schedule.store import appliquer_arbitrage
from tests.schedule.test_course_arbitrage_gel import (  # noqa: F401 -- fixtures
    BASE,
    _deux_carrousels,
    _ligne,
    _publier_a_l_heure,
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


# --- 2. Une correction humaine n'est jamais remplacee par le robot -----------------------


LUNDI = BASE - dt.timedelta(days=2)
HIER = BASE - dt.timedelta(days=1)


class Passages:
    """Le vrai `tourner`, avec des outils factices : compte Sonnet, le rendu, les notifications."""

    def __init__(self, fabrique):  # noqa: F811
        self.fabrique = fabrique
        self.composes, self.rendus, self.notes = [], [], []
        self.preparer = lambda post, article, dossier: ["p_01.jpg"]

    def _composer(self, article):
        self.composes.append(article.id)
        variante = CoverVariante(headline="**Robot** ecrit ca", title="**Robot**", subtitle="ecrit **ca**")
        return PostDraft(variantes=[variante] * 4, caption="Legende du robot 🎧 ✨", hashtags=["#electro"])

    def tourner(self, moment):
        def preparer(post, article, dossier):
            self.rendus.append(post.slides[0]["headline"])
            return self.preparer(post, article, dossier)

        choix = actif(entree("photo", "https://cdn.ig/x.jpg"), origine="article")
        with self.fabrique() as db:
            return runner.tourner(
                db, moment, mode="apercu", composer=self._composer, preparer=preparer,
                publier=lambda *_a, **_k: "ig42", notifier=lambda _t, m: self.notes.append(m),
                horloge=lambda: moment, dormir=lambda _s: None,
                choisir=lambda _db, _a, _f, _m: ChoixVisuels([choix], None),
            )


@pytest.fixture()
def passages(fabrique):  # noqa: F811
    return Passages(fabrique)


@pytest.fixture()
def horloge_des_posts(monkeypatch):
    """L'heure de l'edition humaine, que l'API lit sur l'horloge du serveur."""
    def regler(moment):
        monkeypatch.setattr(posts_api, "_maintenant", lambda: moment, raising=False)
    regler(BASE)
    return regler


def _poser(fabrique, post_id, **champs):  # noqa: F811
    with fabrique() as db:
        post = db.get(Post, post_id)
        for cle, valeur in champs.items():
            setattr(post, cle, valeur)
        db.commit()


def _post(fabrique, post_id):  # noqa: F811
    with fabrique() as db:
        return db.get(Post, post_id)


def _creneau(fabrique, creneau_id):  # noqa: F811
    with fabrique() as db:
        return db.get(Creneau, creneau_id)


def _cover(**textes):
    return {"kind": "cover", "headline": "**A**", "title": "**T**", "subtitle": "**S**",
            "image_url": "https://img/A.jpg", **textes}


def _une_heure_avant(file_):  # noqa: F811
    return file_["h0"] - dt.timedelta(hours=1)


@pytest.mark.parametrize("corps", [
    {"caption": "Correction : ca commence ce soir a Brest."},
    {"slides": [_cover(headline="**Ce soir** a Brest")]},
])
def test_une_correction_du_jour_part_telle_quelle(client_web, fabrique, file_, passages, horloge_des_posts, corps):  # noqa: F811
    """La reproduction de Jo et Tom : post compose la veille, phrase juste ecrite le jour
    meme. Avant : le gel lachait le post, et la composition suivante repayait Sonnet."""
    _poser(fabrique, file_["post_a"], created_at=HIER)
    assert client_web.patch(f"/posts/{file_['post_a']}", json=corps).status_code == 200

    passages.tourner(_une_heure_avant(file_))
    passages.tourner(_une_heure_avant(file_) + dt.timedelta(minutes=10))

    creneau = _creneau(fabrique, file_["tot"])
    assert (creneau.etat, creneau.post_id, creneau.erreur) == (EtatCreneau.GELE, file_["post_a"], None)
    assert passages.composes == []


def test_un_repere_humain_vraiment_perime_part_avec_un_avertissement(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    """Ecrit lundi « ce soir », parti plus tard : le texte humain n'est pas perdu, rien
    n'est repaye, et cela se dit, sur la ligne et par la notification."""
    _poser(fabrique, file_["post_a"], created_at=LUNDI)
    horloge_des_posts(LUNDI)
    texte = "Correction de lundi : ca commence ce soir a Brest."
    assert client_web.patch(f"/posts/{file_['post_a']}", json={"caption": texte}).status_code == 200

    passages.tourner(_une_heure_avant(file_))

    creneau = _creneau(fabrique, file_["tot"])
    assert (creneau.etat, creneau.post_id) == (EtatCreneau.GELE, file_["post_a"])
    assert "ce soir" in creneau.erreur
    assert any("ce soir" in note for note in passages.notes)
    assert _post(fabrique, file_["post_a"]).caption == texte
    assert passages.composes == []


def test_le_robot_perime_a_cote_d_une_correction_ne_la_remplace_pas(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    _poser(fabrique, file_["post_a"], created_at=HIER, slides=[_cover(headline="**Sort** ce soir")])
    assert client_web.patch(f"/posts/{file_['post_a']}", json={"caption": "Corrigee a la main"}).status_code == 200

    passages.tourner(_une_heure_avant(file_))

    creneau = _creneau(fabrique, file_["tot"])
    assert (creneau.etat, creneau.post_id) == (EtatCreneau.GELE, file_["post_a"])
    assert "ce soir" in creneau.erreur
    assert passages.composes == []


def test_un_texte_du_robot_jamais_touche_reste_controle(fabrique, file_, passages):  # noqa: F811
    """Le controle reste entier : le defaut du post 153 ne revient pas."""
    _poser(fabrique, file_["post_a"], created_at=HIER, caption="Sort ce soir a minuit")
    with fabrique() as db:
        db.get(Creneau, file_["tot"]).epingle = True
        db.commit()
    passages.tourner(_une_heure_avant(file_))
    creneau = _creneau(fabrique, file_["tot"])
    assert creneau.post_id is None and "ce soir" in creneau.erreur.lower()

    passages.tourner(_une_heure_avant(file_) + dt.timedelta(minutes=10))
    assert passages.composes == [file_["a"]]


def test_choisir_une_variante_du_robot_ne_vaut_pas_correction(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    variantes = [{"headline": "**A**", "title": "**T**", "subtitle": "**S**"},
                 {"headline": "**Sort** ce soir", "title": "**T**", "subtitle": "**S**"}]
    _poser(fabrique, file_["post_a"], created_at=HIER, variants={"variantes": variantes, "palette": "rose"})
    assert client_web.patch(f"/posts/{file_['post_a']}", json={"variante_retenue": 1}).status_code == 200

    passages.tourner(_une_heure_avant(file_))

    creneau = _creneau(fabrique, file_["tot"])
    assert creneau.post_id is None and "ce soir" in creneau.erreur


def test_retomber_sur_le_texte_du_robot_rend_le_controle(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    """L'horodatage vaut pour le texte ecrit, pas pour le champ."""
    variantes = [{"headline": "**A**", "title": "**T**", "subtitle": "**S**"},
                 {"headline": "**Sort** ce soir", "title": "**T**", "subtitle": "**S**"}]
    _poser(fabrique, file_["post_a"], created_at=HIER, variants={"variantes": variantes})
    corrige = [_cover(headline="**Corrigee** ce soir")]
    assert client_web.patch(f"/posts/{file_['post_a']}", json={"slides": corrige}).status_code == 200
    assert client_web.patch(f"/posts/{file_['post_a']}", json={"variante_retenue": 1}).status_code == 200

    with fabrique() as db:
        post = db.get(Post, file_["post_a"])
        assert runner._reperes_perimes(post, _une_heure_avant(file_)) is not None
        assert relecture.porte_une_correction(post) is False


def test_la_composition_reprend_un_brouillon_corrige_sans_repayer(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    """L'autre lecteur du controle : `_composer`, quand le creneau a lache le post."""
    _poser(fabrique, file_["post_a"], created_at=LUNDI)
    horloge_des_posts(LUNDI)
    assert client_web.patch(f"/posts/{file_['post_a']}", json={"caption": "Lundi : ce soir a Brest"}).status_code == 200
    with fabrique() as db:
        creneau = db.get(Creneau, file_["tot"])
        creneau.post_id, creneau.epingle = None, True
        db.commit()

    passages.tourner(_une_heure_avant(file_))

    assert _creneau(fabrique, file_["tot"]).post_id == file_["post_a"]
    assert passages.composes == []


def test_noter_l_edition_fusionne_et_ne_note_que_ce_qui_change(client_web, fabrique, file_, horloge_des_posts):  # noqa: F811
    """`variants` en FUSION, jamais en bloc ; « Enregistrer » sans rien changer ne note rien."""
    _poser(fabrique, file_["post_a"], variants={"palette": "rose", "fond": {"origine": "article"}}, caption="Telle quelle")
    assert client_web.patch(f"/posts/{file_['post_a']}", json={"caption": "Telle quelle"}).status_code == 200
    assert relecture.CLE_EDITION not in _post(fabrique, file_["post_a"]).variants

    assert client_web.patch(f"/posts/{file_['post_a']}", json={"caption": "Autre"}).status_code == 200
    variants = _post(fabrique, file_["post_a"]).variants
    assert (variants["palette"], variants["fond"]) == ("rose", {"origine": "article"})
    assert variants[relecture.CLE_EDITION]["textes"]["caption"]["texte"] == "Autre"


# --- 3. Allonger une accroche sur un PROGRAMME ne brule pas l'actu -----------------------


def _impossible(post, article, dossier):
    raise PreparationImpossible("accroche trop longue : la cover deborde")


@pytest.mark.parametrize("corps", [
    {"slides": [_cover(headline="**Beaucoup** trop long")]},
    {"slides": [_cover(image_url="https://img/autre.jpg")]},
    {"variante_retenue": 1},
    {"carrousel_explicite": True},
])
def test_une_edition_du_fige_pose_le_marqueur(client_web, fabrique, file_, horloge_des_posts, corps):  # noqa: F811
    variantes = [{"headline": "**A**"}, {"headline": "**Autre**"}]
    _poser(fabrique, file_["post_a"], variants={"variantes": variantes})
    assert client_web.patch(f"/posts/{file_['post_a']}", json=corps).status_code == 200
    assert _creneau(fabrique, file_["tot"]).erreur == relecture.RAISON_RELECTURE


def test_la_legende_ne_pose_pas_le_marqueur(client_web, fabrique, file_, horloge_des_posts):  # noqa: F811
    """Le fige n'en depend pas : un gel impossible resterait la faute de l'article."""
    assert client_web.patch(f"/posts/{file_['post_a']}", json={"caption": "Autre"}).status_code == 200
    assert _creneau(fabrique, file_["tot"]).erreur is None


def test_le_marqueur_n_ecrase_pas_une_erreur_existante(client_web, fabrique, file_, horloge_des_posts):  # noqa: F811
    with fabrique() as db:
        db.get(Creneau, file_["tot"]).erreur = "composition differee : quota"
        db.commit()
    corps = {"slides": [_cover(headline="**Autre**")]}
    assert client_web.patch(f"/posts/{file_['post_a']}", json=corps).status_code == 200
    assert client_web.patch(f"/posts/{file_['post_a']}", json={"slides": [_cover(headline="**Encore**")]}).status_code == 200
    erreur = _creneau(fabrique, file_["tot"]).erreur
    # En tete : `supervision` lit `composition%`.
    assert erreur.startswith("composition differee : quota")
    assert erreur.count(relecture.RAISON_RELECTURE) == 1


def test_un_gel_impossible_apres_edition_libere_sans_exclure(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    """La reproduction de Karim et Jo. Avant : article exclu a vie, sans notification."""
    corps = {"slides": [_cover(headline="**Beaucoup** trop long")]}
    assert client_web.patch(f"/posts/{file_['post_a']}", json=corps).status_code == 200
    passages.preparer = _impossible

    passages.tourner(_une_heure_avant(file_))

    assert _raison(fabrique, file_["a"]) is None
    assert _post(fabrique, file_["post_a"]).slides[0]["headline"] == "**Beaucoup** trop long"
    assert any("deborde" in note for note in passages.notes)
    assert file_["a"] in articles_en_pause(_une_heure_avant(file_))


def test_le_creneau_libere_prend_une_autre_actu(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    """Sans mise a l'ecart, la meme actu reviendrait a chaque passage jusqu'a MANQUE."""
    corps = {"slides": [_cover(headline="**Beaucoup** trop long")]}
    client_web.patch(f"/posts/{file_['post_a']}", json=corps)
    passages.preparer = _impossible
    passages.tourner(_une_heure_avant(file_))
    assert _creneau(fabrique, file_["tot"]).article_id != file_["a"]


def test_la_protection_survit_a_la_reprise_du_post(client_web, fabrique, file_, passages, horloge_des_posts, monkeypatch):  # noqa: F811
    """Liberee, l'actu revient apres sa pause : l'arbitrage reprend le post et efface
    `erreur`, donc le marqueur. L'edition notee sur le post prend le relais."""
    monkeypatch.setattr(settings, "visuel_saute_pause_minutes", 1)
    client_web.patch(f"/posts/{file_['post_a']}", json={"slides": [_cover(headline="**Beaucoup** trop long")]})
    passages.preparer = _impossible
    for minutes in (0, 10, 20):
        passages.tourner(_une_heure_avant(file_) + dt.timedelta(minutes=minutes))
        assert _raison(fabrique, file_["a"]) is None
    assert len(passages.rendus) >= 2


@pytest.mark.parametrize("panne", [RuntimeError("chromium absent"), PreparationDifferee("yt-dlp en pause")])
def test_le_marqueur_survit_a_une_panne_du_gel(fabrique, file_, passages, panne):  # noqa: F811
    """Un `rouvrir` sans edition notee : seul le marqueur protege."""
    with fabrique() as db:
        creneau = db.get(Creneau, file_["tot"])
        creneau.erreur, creneau.epingle = relecture.RAISON_RELECTURE, True
        db.commit()

    def en_panne(post, article, dossier):
        raise panne

    passages.preparer = en_panne
    passages.tourner(_une_heure_avant(file_))
    assert relecture.RAISON_RELECTURE in _creneau(fabrique, file_["tot"]).erreur

    passages.preparer = _impossible
    passages.tourner(_une_heure_avant(file_) + dt.timedelta(minutes=10))
    assert _raison(fabrique, file_["a"]) is None


def test_le_marqueur_n_empeche_pas_un_post_sain_de_partir(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    client_web.patch(f"/posts/{file_['post_a']}", json={"slides": [_cover(headline="**Corrigee**")]})
    passages.tourner(_une_heure_avant(file_))
    creneau = _creneau(fabrique, file_["tot"])
    assert (creneau.etat, creneau.erreur) == (EtatCreneau.GELE, None)
    assert _publier_a_l_heure(fabrique, file_["h0"]) == [(["p_01.jpg"], "Legende de A")]


def test_sans_edition_un_gel_impossible_exclut_toujours(fabrique, file_, passages):  # noqa: F811
    with fabrique() as db:
        db.get(Creneau, file_["tot"]).epingle = True
        db.commit()
    passages.preparer = _impossible
    passages.tourner(_une_heure_avant(file_))
    assert _raison(fabrique, file_["a"]) is not None


@pytest.mark.parametrize("vide", [None, "", "None"])
def test_le_fond_vide_est_refuse_avant_de_rien_toucher(client_web, fabrique, file_, vide):  # noqa: F811
    """`|| null` au dashboard : le brouillon devenait perime et repayait Sonnet."""
    with fabrique() as db:
        creneau = db.get(Creneau, file_["tot"])
        creneau.etat = EtatCreneau.GELE
        creneau.fichiers = ["p_01.jpg"]
        creneau.publier_a = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
        db.commit()
    reponse = client_web.patch(f"/posts/{file_['post_a']}", json={"slides": [_cover(image_url=vide)]})
    assert reponse.status_code == 422
    assert "fond" in reponse.json()["detail"]
    assert _post(fabrique, file_["post_a"]).slides[0]["image_url"] == "https://img/A.jpg"
    assert _creneau(fabrique, file_["tot"]).etat is EtatCreneau.GELE


# --- 4. Une edition pendant le rendu n'est pas figee avec l'ancienne cover ---------------


NOUVELLE = [_cover(headline="**Corrigee** pendant le gel")]


def _epingler(fabrique, file_):  # noqa: F811
    """Sinon l'arbitrage du passage met B a la place de A avant le rendu."""
    with fabrique() as db:
        db.get(Creneau, file_["tot"]).epingle = True
        db.commit()


def _pendant_le_rendu(client_web, file_, corps):  # noqa: F811
    """Le PATCH tombe au milieu du rendu, sur l'autre session, creneau encore PROGRAMME."""
    deja = []

    def preparer(post, article, dossier):
        if not deja:
            deja.append(client_web.patch(f"/posts/{file_['post_a']}", json=corps).status_code)
        return ["p_01.jpg"]

    return preparer, deja


def test_une_accroche_enregistree_pendant_le_gel_n_est_pas_perdue(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    """La reproduction de Karim. Avant : PATCH 200, puis GELE avec l'ancienne cover."""
    _epingler(fabrique, file_)
    passages.preparer, statuts = _pendant_le_rendu(client_web, file_, {"slides": NOUVELLE})

    passages.tourner(_une_heure_avant(file_))

    assert statuts == [200]
    creneau = _creneau(fabrique, file_["tot"])
    assert (creneau.etat, creneau.fichiers) == (EtatCreneau.PROGRAMME, None)
    assert "pendant le rendu" in creneau.erreur
    assert _raison(fabrique, file_["a"]) is None

    # Le passage suivant refait, et ne recommence pas : aucune boucle sans nouvelle edition.
    passages.tourner(_une_heure_avant(file_) + dt.timedelta(minutes=10))

    creneau = _creneau(fabrique, file_["tot"])
    assert (creneau.etat, creneau.fichiers, creneau.erreur) == (EtatCreneau.GELE, ["p_01.jpg"], None)
    assert passages.rendus == ["**A**", "**Corrigee** pendant le gel"]
    assert passages.composes == []


def test_une_legende_pendant_le_gel_ne_coute_pas_un_gel(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    """Elle est relue a la publication : les fichiers figes n'en dependent pas."""
    _epingler(fabrique, file_)
    passages.preparer, _ = _pendant_le_rendu(client_web, file_, {"caption": "Corrigee pendant le gel"})
    passages.tourner(_une_heure_avant(file_))
    assert _creneau(fabrique, file_["tot"]).etat is EtatCreneau.GELE
    assert _post(fabrique, file_["post_a"]).caption == "Corrigee pendant le gel"


def test_l_agrandissement_du_fond_ne_passe_pas_pour_une_edition(fabrique, file_, passages):  # noqa: F811
    """`preparation` reassigne `post.slides` en memoire : ce n'est pas un humain."""
    _epingler(fabrique, file_)

    def agrandit(post, article, dossier):
        post.slides = [{**post.slides[0], "image_url": "file:///cache/agrandi.jpg"}]
        return ["p_01.jpg"]

    passages.preparer = agrandit
    passages.tourner(_une_heure_avant(file_))
    assert _creneau(fabrique, file_["tot"]).etat is EtatCreneau.GELE


def test_l_agrandissement_n_ecrase_pas_l_edition_en_base(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    """L'objet en memoire de l'agent porte l'ancien texte : son `commit` l'aurait reecrit."""
    _epingler(fabrique, file_)
    def agrandit(post, article, dossier):
        post.slides = [{**post.slides[0], "image_url": "file:///cache/agrandi.jpg"}]
        client_web.patch(f"/posts/{file_['post_a']}", json={"slides": NOUVELLE})
        return ["p_01.jpg"]

    passages.preparer = agrandit
    passages.tourner(_une_heure_avant(file_))
    assert _post(fabrique, file_["post_a"]).slides[0]["headline"] == "**Corrigee** pendant le gel"


def test_un_gel_impossible_sur_l_ancien_texte_n_exclut_pas(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    """Le refus parle d'un texte qui n'existe plus : le passage suivant juge le nouveau."""
    _epingler(fabrique, file_)
    def impossible(post, article, dossier):
        client_web.patch(f"/posts/{file_['post_a']}", json={"slides": NOUVELLE})
        raise PreparationImpossible("la cover deborde")

    passages.preparer = impossible
    passages.tourner(_une_heure_avant(file_))
    creneau = _creneau(fabrique, file_["tot"])
    assert (creneau.etat, creneau.post_id) == (EtatCreneau.PROGRAMME, file_["post_a"])
    assert _raison(fabrique, file_["a"]) is None


def test_pres_du_depart_cela_se_dit_une_fois(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    """A T-10 min, le passage suivant tombe apres l'heure : l'humain doit le savoir."""
    _epingler(fabrique, file_)
    moment = file_["h0"] - dt.timedelta(minutes=15)
    numero = []

    def edite_a_chaque_fois(post, article, dossier):
        numero.append(1)
        corps = {"slides": [_cover(headline=f"**Version** {len(numero)}")]}
        client_web.patch(f"/posts/{file_['post_a']}", json=corps)
        return ["p_01.jpg"]

    passages.preparer = edite_a_chaque_fois
    passages.tourner(moment)
    passages.tourner(moment + dt.timedelta(minutes=1))
    assert len(numero) == 2
    annonces = [note for note in passages.notes if "pendant le rendu" in note]
    assert len(annonces) == 1
    assert str(settings.publication_retard_max_minutes) in annonces[0]


def test_loin_du_depart_cela_ne_notifie_pas(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    _epingler(fabrique, file_)
    passages.preparer, _ = _pendant_le_rendu(client_web, file_, {"slides": NOUVELLE})
    passages.tourner(_une_heure_avant(file_))
    assert not [note for note in passages.notes if "pendant le rendu" in note]


def test_au_dela_de_la_limite_le_manque_dit_pourquoi(client_web, fabrique, file_, passages, horloge_des_posts):  # noqa: F811
    _epingler(fabrique, file_)
    passages.preparer, _ = _pendant_le_rendu(client_web, file_, {"slides": NOUVELLE})
    passages.tourner(_une_heure_avant(file_))

    passages.tourner(file_["h0"] + dt.timedelta(minutes=settings.publication_retard_max_minutes + 1))

    creneau = _creneau(fabrique, file_["tot"])
    assert creneau.etat is EtatCreneau.MANQUE
    assert "pendant le rendu" in creneau.erreur


# --- 5. Une annonce par post sur un creneau ----------------------------------------------


def _fichier_annonces():
    return settings.meta_token_path.parent / "relecture.json"


def _annonces(passages):
    return [note for note in passages.notes if note.startswith("relecture")]


def test_le_remplacant_est_annonce_et_chacun_une_seule_fois(fabrique, file_, passages):  # noqa: F811
    """La reproduction du bilan : un post retire, son remplacant partait sans notification."""
    moment = _une_heure_avant(file_)
    _epingler(fabrique, file_)
    passages.tourner(moment)
    passages.tourner(moment + dt.timedelta(minutes=10))
    assert len(_annonces(passages)) == 1

    with fabrique() as db:
        creneau = db.get(Creneau, file_["tot"])
        creneau.article_id, creneau.post_id = file_["b"], file_["post_b"]
        creneau.etat, creneau.fichiers = EtatCreneau.PROGRAMME, None
        tard = db.get(Creneau, file_["tard"])
        tard.article_id, tard.post_id, tard.etat = None, None, EtatCreneau.LIBRE
        db.commit()

    passages.tourner(moment + dt.timedelta(minutes=20))
    passages.tourner(moment + dt.timedelta(minutes=30))

    assert len(_annonces(passages)) == 2


def test_un_creneau_repris_pres_du_depart_est_annonce_avant_de_partir(fabrique, file_, passages):  # noqa: F811
    """Composition, gel et publication s'enchainent dans le MEME passage a moins de 11 min :
    l'annonce du debut du passage ne voyait pas encore ce post."""
    with fabrique() as db:
        creneau = db.get(Creneau, file_["tot"])
        creneau.post_id, creneau.epingle = None, True
        db.delete(db.get(Post, file_["post_a"]))
        db.commit()
    passages.tourner(file_["h0"] - dt.timedelta(minutes=5))
    assert passages.composes == [file_["a"]]
    annonces = _annonces(passages)
    assert len(annonces) == 1 and "Robot" in annonces[0]


def test_l_ancien_format_n_annonce_pas_deux_fois_au_deploiement():
    _fichier_annonces().parent.mkdir(parents=True, exist_ok=True)
    _fichier_annonces().write_text(json.dumps({"annonces": {"12": BASE.isoformat()}}))

    assert relecture.deja_annonce(12, 99, BASE)
    assert not relecture.deja_annonce(13, 99, BASE)

    relecture.marquer_annonce(13, 99, BASE)
    assert relecture.deja_annonce(12, 99, BASE), "la fusion garde l'ancienne entree"
    assert relecture.deja_annonce(13, 99, BASE)
    assert not relecture.deja_annonce(13, 100, BASE)
    # Un jour apres : purgee comme les autres, le remplacant redevient annonce.
    assert not relecture.deja_annonce(12, 99, BASE + dt.timedelta(days=2))


def test_les_annonces_de_deux_processus_se_fusionnent():
    relecture.marquer_annonce(1, 10, BASE)
    relecture.marquer_annonce(1, 11, BASE)
    relecture.marquer_annonce(2, 20, BASE)
    assert all(relecture.deja_annonce(c, p, BASE) for c, p in [(1, 10), (1, 11), (2, 20)])


def test_lire_les_annonces_n_ecrit_rien():
    assert not relecture.deja_annonce(1, 10, BASE)
    assert not _fichier_annonces().exists()
