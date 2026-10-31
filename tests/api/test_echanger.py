"""Echanger deux creneaux (decision du 2026-10-31) : le geste qui remplace « Decaler ».

Ce fichier verrouille cote serveur : ce que l'echange deplace, ce qu'il refuse, et que la
lecture `echanges-possibles` annonce au mot pres ce que le PATCH fera. La course avec le
gel est dans `tests/schedule/test_course_arbitrage_gel.py`.

Raison et choix sur le format : docs/reference/publication.md, « Echanger ».
"""

import socket
from pathlib import Path

import pytest
from sqlalchemy import event

from app.config import settings
from app.content.media import entree
from app.models import Article, EtatCreneau, FormatCreneau, Post, Source, SourceCategorie, SourceType
from app.schedule.relecture import RAISON_RELECTURE
from tests.api.test_programmer import CompteRequetes, _article, _creneau, _maintenant, _post

KINDA_REEL = "kinda_reel"


@pytest.fixture(autouse=True)
def _reglages(monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
    monkeypatch.setattr(settings, "retention_days", 2)
    monkeypatch.setattr(settings, "publication_composition_heures", 24)


def _echanger(client, ici, la, **champs):
    return client.patch(f"/schedule/{ici.id}", json={"echanger_avec": la.id, **champs})


def _lecture(client, creneau):
    reponse = client.get(f"/schedule/{creneau.id}/echanges-possibles")
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _cible(lecture, creneau):
    return next(c for c in lecture["cibles"] if c["creneau_id"] == creneau.id)


def _ligne(db, creneau):
    db.refresh(creneau)
    return creneau.etat, creneau.article_id, creneau.post_id, creneau.epingle, creneau.erreur


def _article_video(db, n, categorie=SourceCategorie.ARTISTE):
    source = Source(name=f"compte{n}", type=SourceType.INSTAGRAM, url=f"ig:compte{n}", categorie=categorie)
    db.add(source)
    db.commit()
    article = Article(source_id=source.id, url=f"https://www.instagram.com/p/{n}/", title=f"Video {n}",
                      raw_hash=f"v{n}", relevance_score=80, published_at=_maintenant(),
                      media=[entree("video", f"https://cdn/{n}.mp4", preview_url=f"https://cdn/{n}.jpg")])
    db.add(article)
    db.commit()
    return article


# --- 1. Ce que l'echange deplace ---------------------------------------------------


def test_deux_creneaux_quelconques_echangent_article_et_post_et_sont_epingles(client, db_session):
    """Pas seulement le suivant : un creneau entre les deux, et ne bouge pas."""
    a, b, milieu = _article(db_session, 1), _article(db_session, 2), _article(db_session, 3)
    post_a, post_b = _post(db_session, a), _post(db_session, b)
    ici = _creneau(db_session, a, post_a, heures=5)
    entre = _creneau(db_session, milieu, heures=6)
    la = _creneau(db_session, b, post_b, heures=7)

    reponse = _echanger(client, la, ici)

    assert reponse.status_code == 200, reponse.text
    assert (reponse.json()["id"], reponse.json()["article_id"]) == (la.id, a.id)
    assert _ligne(db_session, ici) == (EtatCreneau.PROGRAMME, b.id, post_b.id, True, None)
    assert _ligne(db_session, la) == (EtatCreneau.PROGRAMME, a.id, post_a.id, True, None)
    assert _ligne(db_session, entre)[1] == milieu.id and not entre.epingle


def test_vers_un_creneau_libre_l_actu_s_y_deplace_et_le_vide_n_est_pas_epingle(client, db_session):
    a = _article(db_session, 1)
    post = _post(db_session, a)
    ici = _creneau(db_session, a, post, heures=5, epingle=True)
    libre = _creneau(db_session, heures=7)

    assert _echanger(client, ici, libre).status_code == 200

    assert _ligne(db_session, libre) == (EtatCreneau.PROGRAMME, a.id, post.id, True, None)
    assert _ligne(db_session, ici) == (EtatCreneau.LIBRE, None, None, False, None)


def test_depuis_un_creneau_libre_c_est_le_meme_geste(client, db_session):
    a = _article(db_session, 1)
    occupe = _creneau(db_session, a, heures=7)
    libre = _creneau(db_session, heures=5)

    assert _echanger(client, libre, occupe).status_code == 200

    assert _ligne(db_session, libre)[:2] == (EtatCreneau.PROGRAMME, a.id)
    assert _ligne(db_session, occupe) == (EtatCreneau.LIBRE, None, None, False, None)


def test_deux_creneaux_libres_n_ont_rien_a_echanger(client, db_session):
    ici, la = _creneau(db_session, heures=5), _creneau(db_session, heures=7)

    reponse = _echanger(client, ici, la)

    assert reponse.status_code == 409 and "Rien à échanger" in reponse.json()["detail"]
    assert _ligne(db_session, ici)[0] == EtatCreneau.LIBRE


def test_un_creneau_ne_s_echange_pas_avec_lui_meme(client, db_session):
    ici = _creneau(db_session, _article(db_session, 1), heures=5)
    assert _echanger(client, ici, ici).status_code == 409
    assert not _ligne(db_session, ici)[3]


def test_cible_inconnue(client, db_session):
    ici = _creneau(db_session, _article(db_session, 1), heures=5)
    assert client.patch(f"/schedule/{ici.id}", json={"echanger_avec": 999_999}).status_code == 404


def test_un_post_orphelin_reste_programme_et_epingle_a_l_arrivee(client, db_session):
    """Article purge, le post survit (creneau AUTONOME). `_reprogramme` ne regardait que
    l'article : le post serait arrive sur un creneau LIBRE et non epingle."""
    a = _article(db_session, 1)
    post = _post(db_session, a)
    post.article_id = None
    db_session.commit()
    ici = _creneau(db_session, None, post, heures=5)
    libre = _creneau(db_session, heures=7)

    assert _echanger(client, ici, libre).status_code == 200

    assert _ligne(db_session, libre) == (EtatCreneau.PROGRAMME, None, post.id, True, None)
    assert _ligne(db_session, ici) == (EtatCreneau.LIBRE, None, None, False, None)


def test_le_marqueur_de_relecture_suit_son_post_et_le_reste_s_efface(client, db_session):
    """La regle posee pour `RAISON_RELECTURE` : il protege le post qu'on vient de
    corriger d'une exclusion au gel. Le reste parle du creneau qu'on quitte."""
    a, b = _article(db_session, 1), _article(db_session, 2)
    post_a, post_b = _post(db_session, a), _post(db_session, b)
    ici = _creneau(db_session, a, post_a, heures=5, erreur=RAISON_RELECTURE)
    la = _creneau(db_session, b, post_b, heures=8, erreur="gel refuse : x")

    assert _echanger(client, ici, la).status_code == 200

    assert _ligne(db_session, ici)[1:] == (b.id, post_b.id, True, None)
    assert _ligne(db_session, la)[1:] == (a.id, post_a.id, True, RAISON_RELECTURE)


def test_un_brouillon_existant_est_repris_a_l_arrivee_comme_pour_programmer(client, db_session):
    """Sans lui, la limite de purge levee par ce brouillon mentirait : le creneau partirait
    sans post et l'article serait purge avant sa composition."""
    a = _article(db_session, 1)
    brouillon = _post(db_session, a)
    ici = _creneau(db_session, a, heures=5)
    libre = _creneau(db_session, heures=7)

    assert _echanger(client, ici, libre).status_code == 200

    assert _ligne(db_session, libre)[2] == brouillon.id


# --- 2. Ce qui est refuse, et rien n'est ecrit ---------------------------------------


@pytest.mark.parametrize("etat, attendu", [
    (EtatCreneau.GELE, "gelé"),
    (EtatCreneau.ECHEC, "Publication"),
    (EtatCreneau.PUBLIE, "publié"),
    (EtatCreneau.APERCU, "aperçu"),
    (EtatCreneau.MANQUE, "manqué"),
])
@pytest.mark.parametrize("sens", ["depuis", "vers"])
def test_un_creneau_fige_refuse_dans_les_deux_sens_avec_sa_raison(client, db_session, etat, attendu, sens):
    a, b = _article(db_session, 1), _article(db_session, 2)
    fige = _creneau(db_session, a, _post(db_session, a), etat=etat, heures=5)
    autre = _creneau(db_session, b, heures=7)
    avant = _ligne(db_session, fige), _ligne(db_session, autre)

    reponse = _echanger(client, fige, autre) if sens == "depuis" else _echanger(client, autre, fige)

    assert reponse.status_code == 409 and attendu in reponse.json()["detail"], reponse.text
    assert (_ligne(db_session, fige), _ligne(db_session, autre)) == avant


@pytest.mark.parametrize("sens", ["depuis", "vers"])
def test_un_creneau_passe_refuse(client, db_session, sens):
    a, b = _article(db_session, 1), _article(db_session, 2)
    passe = _creneau(db_session, a, heures=-1)
    autre = _creneau(db_session, b, heures=7)

    reponse = _echanger(client, passe, autre) if sens == "depuis" else _echanger(client, autre, passe)

    assert reponse.status_code == 409 and "passé" in reponse.json()["detail"]
    assert (_ligne(db_session, passe)[1], _ligne(db_session, autre)[1]) == (a.id, b.id)


def test_un_article_exclu_ne_se_deplace_pas(client, db_session):
    """Sinon l'epingle le protegerait de l'arbitrage, qui l'aurait retire."""
    a = _article(db_session, 1, file_exclu_raison="exclu")
    ici = _creneau(db_session, a, heures=5)
    libre = _creneau(db_session, heures=7)

    reponse = _echanger(client, ici, libre)

    assert reponse.status_code == 409 and "exclu" in reponse.json()["detail"]
    assert _ligne(db_session, libre)[1] is None


def test_le_refus_venant_de_l_autre_actu_la_nomme(client, db_session):
    a, b = _article(db_session, 1), _article(db_session, 2, file_exclu_raison="exclu")
    ici, la = _creneau(db_session, a, heures=5), _creneau(db_session, b, heures=7)

    reponse = _echanger(client, ici, la)

    assert reponse.status_code == 409 and "Actu 2" in reponse.json()["detail"]


# --- 3. L'occupant que l'ecran a nomme ----------------------------------------------


@pytest.mark.parametrize("champ", ["occupant_attendu", "occupant_source_attendu"])
def test_un_occupant_change_depuis_l_affichage_rend_409_sans_rien_ecrire(client, db_session, champ):
    """La validation a nomme les deux actus, et l'arbitrage a pu en changer. C'est aussi
    ce qui empeche « Annuler » d'echanger une actu que personne n'a vue."""
    a, b = _article(db_session, 1), _article(db_session, 2)
    ici, la = _creneau(db_session, a, heures=5), _creneau(db_session, b, heures=7)

    reponse = _echanger(client, ici, la, **{champ: 123456})

    assert reponse.status_code == 409 and "changé" in reponse.json()["detail"]
    assert (_ligne(db_session, ici)[1], _ligne(db_session, la)[1]) == (a.id, b.id)


def test_un_creneau_vu_vide_et_rempli_depuis_rend_409(client, db_session):
    a, b = _article(db_session, 1), _article(db_session, 2)
    ici, la = _creneau(db_session, a, heures=5), _creneau(db_session, b, heures=7)
    assert _echanger(client, ici, la, occupant_attendu=None).status_code == 409


def test_les_bons_occupants_passent(client, db_session):
    a, b = _article(db_session, 1), _article(db_session, 2)
    ici, la = _creneau(db_session, a, heures=5), _creneau(db_session, b, heures=7)
    reponse = _echanger(client, ici, la, occupant_attendu=b.id, occupant_source_attendu=a.id)
    assert reponse.status_code == 200, reponse.text


# --- 4. Le format appartient au creneau -----------------------------------------------


def _reel_et_carrousel(db, *, video_au_carrousel=True, post_reel=False, post_carrousel=False):
    reel_article = _article_video(db, 1)
    carrousel_article = _article_video(db, 2) if video_au_carrousel else _article(db, 2)
    post_r = _post(db, reel_article, template=KINDA_REEL) if post_reel else None
    post_c = _post(db, carrousel_article) if post_carrousel else None
    reel = _creneau(db, reel_article, post_r, heures=5, format_=FormatCreneau.REEL)
    carrousel = _creneau(db, carrousel_article, post_c, heures=7, format_=FormatCreneau.CARROUSEL)
    return reel, carrousel, reel_article, carrousel_article


def test_carrousel_et_reel_sans_post_s_echangent_si_chacun_est_eligible(client, db_session):
    reel, carrousel, a, b = _reel_et_carrousel(db_session)

    assert _echanger(client, reel, carrousel).status_code == 200

    assert (_ligne(db_session, reel)[1], reel.format) == (b.id, FormatCreneau.REEL)
    assert (_ligne(db_session, carrousel)[1], carrousel.format) == (a.id, FormatCreneau.CARROUSEL)


def test_une_actu_sans_video_ne_va_pas_sur_un_reel(client, db_session):
    reel, carrousel, a, b = _reel_et_carrousel(db_session, video_au_carrousel=False)

    reponse = _echanger(client, carrousel, reel)

    assert reponse.status_code == 409 and "inutilisable en reel" in reponse.json()["detail"]
    assert (_ligne(db_session, reel)[1], _ligne(db_session, carrousel)[1]) == (a.id, b.id)


def test_une_actu_sans_visuel_ne_va_pas_sur_un_carrousel(client, db_session, monkeypatch):
    from app.schedule import programmation

    reel, carrousel, a, b = _reel_et_carrousel(db_session)
    monkeypatch.setattr(programmation, "raison_sans_visuel", lambda *_: "rien")

    reponse = _echanger(client, reel, carrousel)

    assert reponse.status_code == 409 and "sans visuel autorisé" in reponse.json()["detail"]
    assert _ligne(db_session, reel)[1] == a.id


@pytest.mark.parametrize("cote", ["post_reel", "post_carrousel"])
def test_un_post_compose_ne_change_jamais_de_format(client, db_session, cote):
    """LE CHOIX : refuser. Le gel lacherait le post au mauvais gabarit
    (`runner._geler`, « gel refuse : le post est au gabarit ») et Claude serait repaye ;
    le lacher ici romprait la promesse du geste, qui deplace une paire (actu, post) et
    s'annule par le geste inverse. Le remplacer reste possible, explicitement."""
    reel, carrousel, a, b = _reel_et_carrousel(db_session, **{cote: True})
    avant = _ligne(db_session, reel), _ligne(db_session, carrousel)

    reponse = _echanger(client, reel, carrousel)

    assert reponse.status_code == 409, reponse.text
    assert "déjà composée" in reponse.json()["detail"]
    assert "Choisir une actu" in reponse.json()["detail"]
    assert (_ligne(db_session, reel), _ligne(db_session, carrousel)) == avant


def test_aucun_post_ne_finit_au_mauvais_gabarit_sur_toute_la_matrice(client, db_session):
    """L'invariant, joue pour de vrai : quoi qu'on echange, le post d'un creneau est du
    gabarit du creneau. Un brouillon de l'autre gabarit n'est pas repris non plus."""
    a, b = _article_video(db_session, 1), _article_video(db_session, 2)
    _post(db_session, a)  # un brouillon carrousel de A
    _post(db_session, b, template=KINDA_REEL)  # un brouillon reel de B
    reel = _creneau(db_session, a, heures=5, format_=FormatCreneau.REEL)
    carrousel = _creneau(db_session, b, heures=7, format_=FormatCreneau.CARROUSEL)

    assert _echanger(client, reel, carrousel).status_code == 200

    db_session.refresh(reel)
    db_session.refresh(carrousel)
    assert db_session.get(Post, reel.post_id).template == KINDA_REEL
    assert db_session.get(Post, carrousel.post_id).template == "kinda_post"


# --- 5. La limite de purge, a l'arrivee ----------------------------------------------


def test_la_purge_s_applique_a_l_arrivee(client, db_session):
    """« Decaler » y echappait : l'article etait purge avant sa composition et le creneau
    mourait. Retention 2 j, composition 24 h : a 1 h d'age, plus de 71 h est refuse."""
    a = _article(db_session, 1, age_h=1)
    ici = _creneau(db_session, a, heures=5)
    loin = _creneau(db_session, heures=80)

    reponse = _echanger(client, ici, loin)

    assert reponse.status_code == 409 and "purgée avant sa composition" in reponse.json()["detail"]
    assert (_ligne(db_session, ici)[1], _ligne(db_session, loin)[1]) == (a.id, None)


def test_la_purge_vaut_pour_les_deux_sens(client, db_session):
    a, b = _article(db_session, 1, age_h=1), _article(db_session, 2, age_h=1)
    ici, loin = _creneau(db_session, a, heures=5), _creneau(db_session, b, heures=80)
    # B remonte sans souci, c'est A qui part trop loin : refuse, et B ne remonte pas.
    assert _echanger(client, loin, ici).status_code == 409
    assert (_ligne(db_session, ici)[1], _ligne(db_session, loin)[1]) == (a.id, b.id)


def test_un_post_valide_leve_la_limite_de_purge(client, db_session):
    """Un post survit a son article : meme exception que Programmer."""
    a = _article(db_session, 1, age_h=1)
    post = _post(db_session, a)
    ici = _creneau(db_session, a, post, heures=5)
    loin = _creneau(db_session, heures=80)

    assert _echanger(client, ici, loin).status_code == 200
    assert _ligne(db_session, loin)[1:3] == (a.id, post.id)


# --- 6. La lecture dit ce que le PATCH fera --------------------------------------------


def _matrice(db):
    a, b, c = _article(db, 1), _article(db, 2, age_h=1), _article_video(db, 3)
    gele_article, exclu = _article(db, 4), _article(db, 5, file_exclu_raison="exclu")
    return [
        _creneau(db, a, _post(db, a), heures=5),
        _creneau(db, b, heures=6),
        _creneau(db, heures=7),
        _creneau(db, heures=8),
        _creneau(db, heures=90),
        _creneau(db, c, heures=9, format_=FormatCreneau.REEL),
        _creneau(db, heures=10, format_=FormatCreneau.REEL),
        _creneau(db, gele_article, etat=EtatCreneau.GELE, heures=2),
        _creneau(db, exclu, heures=11),
        _creneau(db, heures=-2),
    ]


def test_la_lecture_et_le_patch_ne_divergent_jamais(client, db_session):
    """Le verrou de la factorisation : sur toute la matrice, la cible dit au mot pres ce
    que le PATCH fera. Le PATCH est joue pour de vrai, puis defait a la main."""
    creneaux = _matrice(db_session)
    permis = refuses = 0
    for ici in creneaux:
        lecture = _lecture(client, ici)
        for la in creneaux:
            if la.id == ici.id:
                continue
            cible = _cible(lecture, la)
            avant = {c.id: _ligne(db_session, c) for c in (ici, la)}
            reponse = _echanger(client, ici, la)
            if cible["possible"]:
                assert reponse.status_code == 200, (ici.id, la.id, reponse.text)
                permis += 1
                for c in (ici, la):
                    db_session.refresh(c)
                    c.etat, c.article_id, c.post_id, c.epingle, c.erreur = avant[c.id]
                db_session.commit()
            else:
                assert reponse.status_code == 409, (ici.id, la.id, reponse.text)
                assert reponse.json()["detail"] == cible["raison"], (ici.id, la.id)
                assert {c.id: _ligne(db_session, c) for c in (ici, la)} == avant
                refuses += 1
    assert permis >= 5 and refuses >= 20


def test_la_lecture_porte_ce_que_l_ecran_a_besoin(client, db_session):
    a, b = _article(db_session, 1), _article(db_session, 2)
    post = _post(db_session, a)
    ici = _creneau(db_session, a, post, heures=5, epingle=True)
    la = _creneau(db_session, b, heures=7)
    fige = _creneau(db_session, _article(db_session, 3), etat=EtatCreneau.GELE, heures=9)
    passe = _creneau(db_session, heures=-3)

    lecture = _lecture(client, ici)

    assert lecture["creneau_id"] == ici.id and lecture["raison_creneau"] is None
    assert lecture["occupant"]["titre"] == "Actu 1"
    assert (lecture["occupant"]["epingle"], lecture["occupant"]["compose"]) == (True, True)
    cible = _cible(lecture, la)
    assert (cible["possible"], cible["raison"], cible["a_venir"]) == (True, None, True)
    assert cible["occupant"]["titre"] == "Actu 2"
    assert _cible(lecture, fige)["possible"] is False and "gelé" in _cible(lecture, fige)["raison"]
    assert _cible(lecture, passe)["a_venir"] is False
    assert all(c["creneau_id"] != ici.id for c in lecture["cibles"])


def test_la_lecture_annonce_un_depart_proche_des_deux_cotes(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "publication_relecture_avance_minutes", 90)
    a, b = _article(db_session, 1), _article(db_session, 2)
    proche = _creneau(db_session, a, heures=1)
    loin = _creneau(db_session, b, heures=7)

    # Le creneau lointain va recevoir A a 7 h : rien. Mais B arrive a 1 h : a dire.
    assert any("sans fenêtre de relecture" in x for x in _cible(_lecture(client, loin), proche)["annonces"])
    assert any("sans fenêtre de relecture" in x for x in _cible(_lecture(client, proche), loin)["annonces"])


def test_la_lecture_d_un_creneau_gele_dit_pourquoi(client, db_session):
    fige = _creneau(db_session, _article(db_session, 1), etat=EtatCreneau.GELE, heures=4)
    autre = _creneau(db_session, heures=7)

    lecture = _lecture(client, fige)

    assert "gelé" in lecture["raison_creneau"]
    assert _cible(lecture, autre)["possible"] is False


def test_lecture_404(client):
    assert client.get("/schedule/999999/echanges-possibles").status_code == 404


def test_la_lecture_n_ecrit_rien_et_n_ouvre_aucune_requete_par_ligne(client, db_session):
    """Dix fois plus de lignes, le meme nombre de requetes, reels compris."""
    def peupler(debut, n):
        for i in range(debut, debut + n):
            article = _article(db_session, i)
            _creneau(db_session, article, _post(db_session, article), heures=5 + i)
            video = _article_video(db_session, 1000 + i)
            _creneau(db_session, video, heures=5.5 + i, format_=FormatCreneau.REEL)

    peupler(1, 2)
    ici = _creneau(db_session, _article(db_session, 900), heures=4)
    _lecture(client, ici)
    with CompteRequetes(db_session) as petit:
        _lecture(client, ici)
    peupler(10, 20)
    ecritures = []

    def noter(_c, _curseur, requete, *_):
        if requete.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            ecritures.append(requete)

    moteur = db_session.get_bind()
    event.listen(moteur, "before_cursor_execute", noter)
    try:
        with CompteRequetes(db_session) as grand:
            _lecture(client, ici)
    finally:
        event.remove(moteur, "before_cursor_execute", noter)
    assert grand.nombre == petit.nombre
    assert ecritures == []


def test_ordre_stable_les_ecritures_vont_par_id_quel_que_soit_le_sens(client, db_session):
    """L'ordre stable qui evite un deadlock Postgres entre deux echanges croises."""
    a, b = _article(db_session, 1), _article(db_session, 2)
    petit, grand = _creneau(db_session, a, heures=5), _creneau(db_session, b, heures=7)
    assert petit.id < grand.id
    vues = []

    def noter(_c, _curseur, requete, _parametres, contexte, _plusieurs):
        if requete.lstrip().upper().startswith("UPDATE CRENEAUX"):
            vues.append(contexte.compiled_parameters[0]["id_1"])

    moteur = db_session.get_bind()
    event.listen(moteur, "before_cursor_execute", noter)
    try:
        assert _echanger(client, petit, grand).status_code == 200
    finally:
        event.remove(moteur, "before_cursor_execute", noter)
    assert vues == [petit.id, grand.id]


# --- 7. « Decaler » est parti, et rien n'appelle le reseau --------------------------------


def test_decaler_n_existe_plus(client, db_session):
    a, b = _article(db_session, 1), _article(db_session, 2)
    ici = _creneau(db_session, a, heures=5)
    _creneau(db_session, b, heures=7)
    assert client.patch(f"/schedule/{ici.id}", json={"decaler": True}).status_code == 422
    assert _ligne(db_session, ici)[1] == a.id


def test_une_seule_action_a_la_fois(client, db_session):
    a = _article(db_session, 1)
    ici, la = _creneau(db_session, a, heures=5), _creneau(db_session, heures=7)
    assert client.patch(f"/schedule/{ici.id}", json={"echanger_avec": la.id, "epingle": True}).status_code == 422


def test_ni_reseau_ni_chemin_vers_media_publish(client, db_session, monkeypatch):
    """Lecture et ecriture sans une connexion sortante : Meta et Claude sont ailleurs."""

    def interdit(*_a, **_k):
        raise AssertionError("connexion reseau pendant un echange")

    monkeypatch.setattr(socket.socket, "connect", interdit)
    a = _article(db_session, 1)
    ici, la = _creneau(db_session, a, heures=5), _creneau(db_session, heures=7)

    assert _lecture(client, ici)["cibles"]
    assert _echanger(client, ici, la).status_code == 200
    racine = Path(__file__).resolve().parents[2] / "app"
    for fichier in ("api/schedule.py", "schedule/programmation.py"):
        imports = [ligne for ligne in (racine / fichier).read_text(encoding="utf-8").splitlines()
                   if ligne.startswith(("import ", "from "))]
        assert not [i for i in imports if "instagram" in i or "publish.publier" in i or "anthropic" in i or "compose" in i]
