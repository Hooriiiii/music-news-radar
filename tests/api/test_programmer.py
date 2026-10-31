"""« Programmer » remplace « Générer le post » (decision du 2026-10-31).

La console publie seule, l'utilisateur gere le planning et ne genere jamais a la main.
Ce fichier verrouille cote serveur : les lectures d'options (memes regles que
`PATCH /schedule/{id}`, a un seul endroit), la limite de purge, l'erreur qui ne survit
plus a un changement d'article, le creneau mort, et la correction humaine protegee.

Raison et calcul : docs/reference/publication.md, « Programmer ».
"""

import datetime as dt

import pytest
from sqlalchemy import event

from app.config import settings
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau, Post, PostStatus, Source, SourceCategorie, SourceType
from app.pipeline.retention import purge_old_articles
from app.publish import mode as mode_publication
from app.schedule.programmation import moment_de_purge
from app.schedule.relecture import RAISON_RELECTURE
from app.schedule.runner import RAISON_TEXTE_PERIME, tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import appliquer_arbitrage
from tests.schedule.test_file_reperes import COVER_153, LEGENDE_153, Outils
from tests.schedule.test_file_reperes import _article as _article_garrix
from tests.schedule.test_file_reperes import _brouillon as _brouillon_garrix

COVER = {"kind": "cover", "headline": "**A**", "title": "**T**", "subtitle": "**S**",
         "image_url": "https://img/1.jpg"}
ASSETS = [{"type": "photo", "url": "https://img/1.jpg", "origine": "article", "compte": "@tsugi", "article_id": 1}]


@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
    monkeypatch.setattr(settings, "retention_days", 2)
    monkeypatch.setattr(settings, "publication_composition_heures", 24)


def _maintenant():
    return dt.datetime.now(dt.timezone.utc)


def _source(db):
    source = db.query(Source).first()
    if source is None:
        source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
        db.add(source)
        db.commit()
    return source


def _article(db, n=1, *, age_h=1.0, **champs):
    valeurs = dict(relevance_score=80, hotness_score=20, published_at=_maintenant() - dt.timedelta(hours=age_h),
                   media=[{"type": "photo", "url": f"https://img/{n}.jpg"}])
    valeurs.update(champs)
    article = Article(source_id=_source(db).id, url=f"https://t/{n}", title=f"Actu {n}", raw_hash=f"h{n}", **valeurs)
    db.add(article)
    db.commit()
    return article


def _post(db, article=None, *, template="kinda_post", status=PostStatus.DRAFT, assets=ASSETS, slides=None):
    post = Post(article_id=article.id if article else None, article_title=article.title if article else "Actu",
                article_url="https://t/1", caption="Legende", hashtags=["#a"], template=template,
                slides=slides or [COVER], status=status, assets=assets,
                variants={"variantes": [COVER, COVER], "retenue": 0, "palette": "rose"})
    db.add(post)
    db.commit()
    return post


def _creneau(db, article=None, post=None, *, etat=None, heures=5.0, format_=FormatCreneau.CARROUSEL, **champs):
    etat = etat or (EtatCreneau.PROGRAMME if article or post else EtatCreneau.LIBRE)
    creneau = Creneau(publier_a=_maintenant() + dt.timedelta(hours=heures), format=format_,
                      article_id=article.id if article else None, post_id=post.id if post else None,
                      etat=etat, **champs)
    db.add(creneau)
    db.commit()
    return creneau


def _sur_la_grille(db, creneau):
    """Un creneau hors de la grille disparait a l'arbitrage : celui-ci reste."""
    creneau.publier_a = prochains_horaires(_maintenant(), 1)[0]
    db.commit()


def _options(client, article):
    reponse = client.get(f"/articles/{article.id}/creneaux-possibles")
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _option(lecture, creneau):
    return next(o for o in lecture["options"] if o["creneau_id"] == creneau.id)


def _actus(client, creneau):
    reponse = client.get(f"/schedule/{creneau.id}/actus-possibles")
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _candidat(lecture, article):
    return next(c for c in lecture["candidats"] if c["article_id"] == article.id)


class CompteRequetes:
    def __init__(self, db):
        self.engine, self.nombre = db.get_bind(), 0

    def _compter(self, *_):
        self.nombre += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._compter)
        return self

    def __exit__(self, *_):
        event.remove(self.engine, "before_cursor_execute", self._compter)


# --- 3. L'erreur d'un creneau ne survit plus a un changement d'article -----------


def test_le_marqueur_de_texte_perime_ne_saute_plus_le_controle_sur_un_autre_article(client, db_session):
    """REPRODUIT d'abord, sur le chemin reel : le marqueur laisse par l'article A faisait
    sauter a B le controle du gel (`deja_tente`), et « sort ce soir » partait."""
    maintenant = _maintenant()
    ancien = _article(db_session, 1)
    nouveau = _article_garrix(db_session, maintenant)
    perime = _brouillon_garrix(db_session, nouveau, maintenant, cover=COVER_153, legende=LEGENDE_153)
    creneau = _creneau(db_session, ancien, heures=1, erreur=f"{RAISON_TEXTE_PERIME} — composition en echec")

    assert client.patch(f"/schedule/{creneau.id}", json={"article_id": nouveau.id}).status_code == 200
    db_session.refresh(creneau)
    assert creneau.erreur is None
    assert creneau.post_id == perime.id

    outils = Outils()
    outils.tourner(db_session, maintenant)
    db_session.refresh(creneau)
    assert outils.geles == []  # le texte perime n'est PAS gele
    assert creneau.post_id is None and creneau.etat is EtatCreneau.PROGRAMME


def test_le_creneau_quitte_par_l_article_perd_aussi_son_erreur(client, db_session):
    article = _article(db_session)
    ancien = _creneau(db_session, article, heures=5, erreur="gel refuse : x")
    cible = _creneau(db_session, heures=8)

    assert client.patch(f"/schedule/{cible.id}", json={"article_id": article.id}).status_code == 200

    db_session.refresh(ancien)
    assert (ancien.erreur, ancien.article_id) == (None, None)


def test_decaler_efface_l_erreur_mais_le_marqueur_de_relecture_suit_son_post(client, db_session):
    """Le marqueur protege un post qu'on vient de corriger : il part avec lui, sinon un
    gel impossible apres decalage exclurait l'article a vie."""
    premier = _article(db_session, 1)
    second = _article(db_session, 2)
    post = _post(db_session, premier)
    ici = _creneau(db_session, premier, post, heures=5, erreur=RAISON_RELECTURE)
    apres = _creneau(db_session, second, heures=8, erreur="gel refuse : x")

    assert client.patch(f"/schedule/{ici.id}", json={"decaler": True}).status_code == 200

    db_session.refresh(ici)
    db_session.refresh(apres)
    assert (ici.article_id, ici.erreur) == (second.id, None)
    assert (apres.article_id, apres.post_id, apres.erreur) == (premier.id, post.id, RAISON_RELECTURE)


# --- 4. Le creneau mort ----------------------------------------------------------


def _creneau_mort(db):
    """Le profil reproduit : epingle a la main, article purge avant sa composition."""
    article = _article(db, 1, age_h=24 * 3)
    creneau = _creneau(db, article, epingle=True)
    _sur_la_grille(db, creneau)
    assert purge_old_articles(db, settings.retention_days) == 1
    # `ON DELETE SET NULL` en production ; SQLite de test n'applique pas les FK.
    creneau.article_id = None
    db.commit()
    assert (creneau.article_id, creneau.post_id, creneau.etat, creneau.epingle) == (
        None, None, EtatCreneau.PROGRAMME, True)
    return creneau


def test_un_creneau_mort_reproduit_est_desepingle_et_rearbitre(db_session):
    creneau = _creneau_mort(db_session)
    candidat = _article(db_session, 2)

    appliquer_arbitrage(db_session, _maintenant())

    db_session.refresh(creneau)
    assert creneau.epingle is False
    assert creneau.article_id == candidat.id
    assert creneau.etat is EtatCreneau.PROGRAMME


def test_un_creneau_mort_sans_candidat_redevient_libre(db_session):
    creneau = _creneau_mort(db_session)

    appliquer_arbitrage(db_session, _maintenant())

    db_session.refresh(creneau)
    assert (creneau.epingle, creneau.etat) == (False, EtatCreneau.LIBRE)


def test_desepingler_reste_possible_sur_un_creneau_mort(client, db_session):
    creneau = _creneau_mort(db_session)

    assert client.patch(f"/schedule/{creneau.id}", json={"epingle": False}).status_code == 200

    db_session.refresh(creneau)
    assert creneau.epingle is False


def test_un_post_orphelin_epingle_n_est_pas_un_creneau_mort(db_session):
    """Temoin : un post pret survit a son article, le creneau le garde jusqu'au gel."""
    post = _post(db_session)
    creneau = _creneau(db_session, post=post, epingle=True)
    _sur_la_grille(db_session, creneau)

    appliquer_arbitrage(db_session, _maintenant())

    db_session.refresh(creneau)
    assert (creneau.epingle, creneau.post_id, creneau.etat) == (True, post.id, EtatCreneau.PROGRAMME)


# --- 5. Une correction humaine est protegee --------------------------------------


@pytest.mark.parametrize("corps", [
    {"caption": "Corrigee"},
    {"hashtags": ["#b"]},
    {"slides": [COVER]},
    {"slides": [COVER, {"kind": "media", "type": "photo", "url": "https://img/2.jpg"}], "carrousel_explicite": True},
    {"variante_retenue": 1},
])
def test_toute_correction_epingle_le_creneau_programme(client, db_session, corps):
    article = _article(db_session)
    post = _post(db_session, article, slides=[{**COVER, "image_url": "https://img/autre.jpg"}])
    creneau = _creneau(db_session, article, post)
    _sur_la_grille(db_session, creneau)

    assert client.patch(f"/posts/{post.id}", json=corps).status_code == 200

    db_session.refresh(creneau)
    assert creneau.epingle is True
    # Et l'arbitrage, qui a un meilleur candidat, n'y touche plus.
    _article(db_session, 2, relevance_score=99)
    appliquer_arbitrage(db_session, _maintenant())
    db_session.refresh(creneau)
    assert (creneau.article_id, creneau.post_id) == (article.id, post.id)


def test_preuve_sans_epinglage_l_arbitrage_remplace_le_post(db_session):
    """Temoin du defaut : le meme creneau, non epingle, perd son post au passage suivant."""
    article = _article(db_session)
    post = _post(db_session, article)
    creneau = _creneau(db_session, article, post)
    _sur_la_grille(db_session, creneau)
    meilleur = _article(db_session, 2, relevance_score=99)

    appliquer_arbitrage(db_session, _maintenant())

    db_session.refresh(creneau)
    assert creneau.article_id == meilleur.id


def test_enregistrer_sans_rien_changer_n_epingle_pas(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    creneau = _creneau(db_session, article, post)

    assert client.patch(f"/posts/{post.id}", json={"caption": post.caption, "hashtags": post.hashtags}).status_code == 200

    db_session.refresh(creneau)
    assert creneau.epingle is False


def test_une_correction_refusee_n_epingle_pas(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    creneau = _creneau(db_session, article, post)

    assert client.patch(f"/posts/{post.id}", json={"variante_retenue": 99}).status_code == 422

    db_session.refresh(creneau)
    assert creneau.epingle is False


def test_corriger_la_legende_d_un_gele_ne_refait_rien(client, db_session):
    """Gratuit, sans re-gel : l'epinglage ne vise que PROGRAMME, le gel garde ses fichiers."""
    article = _article(db_session)
    post = _post(db_session, article)
    creneau = _creneau(db_session, article, post, etat=EtatCreneau.GELE, fichiers=["p_01.jpg"])

    assert client.patch(f"/posts/{post.id}", json={"caption": "Corrigee"}).status_code == 200

    db_session.refresh(creneau)
    assert (creneau.etat, creneau.fichiers, creneau.epingle) == (EtatCreneau.GELE, ["p_01.jpg"], False)


def test_l_epinglage_ecrit_conditionnellement_ne_touche_pas_un_creneau_reclame(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    creneau = _creneau(db_session, article, post, etat=EtatCreneau.MANQUE)

    assert client.patch(f"/posts/{post.id}", json={"caption": "Corrigee"}).status_code == 200

    db_session.refresh(creneau)
    assert creneau.epingle is False


def test_le_depart_dit_l_epinglage_a_la_fiche(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    _creneau(db_session, article, post)

    client.patch(f"/posts/{post.id}", json={"caption": "Corrigee"})

    assert client.get("/relecture").json()[0]["epingle"] is True


# --- 1. Les lectures d'options ---------------------------------------------------


def test_les_options_d_un_article_portent_heure_format_et_occupant(client, db_session):
    article = _article(db_session, 1)
    occupant = _article(db_session, 2)
    post = _post(db_session, occupant)
    libre = _creneau(db_session, heures=5)
    occupe = _creneau(db_session, occupant, post, heures=8, epingle=True)

    lecture = _options(client, article)

    assert [o["creneau_id"] for o in lecture["options"]] == [libre.id, occupe.id]
    assert lecture["prochain_creneau_id"] == libre.id
    assert _option(lecture, libre)["occupant"] is None
    option = _option(lecture, occupe)
    assert option["possible"] is True and option["raison"] is None
    assert option["format"] == "carrousel" and option["publier_a"]
    assert option["occupant"] == {"article_id": occupant.id, "titre": "Actu 2", "epingle": True,
                                  "compose": True, "post_id": post.id}


def test_le_titre_d_un_occupant_purge_vient_de_son_post(client, db_session):
    article = _article(db_session)
    post = _post(db_session)
    creneau = _creneau(db_session, post=post)

    occupant = _option(_options(client, article), creneau)["occupant"]

    assert (occupant["article_id"], occupant["titre"], occupant["compose"]) == (None, "Actu", True)


@pytest.mark.parametrize("champs, attendu", [
    ({"status": ArticleStatus.USED}, "déjà publié"),
    ({"file_exclu_raison": "« Ne pas publier » depuis le dashboard"}, "exclu"),
])
def test_un_article_refuse_le_dit_sur_chaque_option(client, db_session, champs, attendu):
    article = _article(db_session, **champs)
    creneau = _creneau(db_session)

    lecture = _options(client, article)

    assert attendu in lecture["raison_article"]
    assert attendu in _option(lecture, creneau)["raison"]
    assert lecture["prochain_creneau_id"] is None


def test_un_doublon_est_refuse(client, db_session):
    original = _article(db_session, 1)
    doublon = _article(db_session, 2, duplicate_of_id=original.id)

    assert "dupliqué" in _options(client, doublon)["raison_article"]


def test_un_article_sans_visuel_est_refuse_sur_le_carrousel(client, db_session):
    article = _article(db_session, media=None)
    article.title = ""
    db_session.commit()
    creneau = _creneau(db_session)

    assert "sans visuel" in _option(_options(client, article), creneau)["raison"]


def test_un_article_sans_video_est_refuse_sur_le_reel_mais_pas_sur_le_carrousel(client, db_session):
    article = _article(db_session)
    carrousel = _creneau(db_session, heures=5)
    reel = _creneau(db_session, heures=6, format_=FormatCreneau.REEL)

    lecture = _options(client, article)

    assert _option(lecture, carrousel)["possible"] is True
    assert "reel" in _option(lecture, reel)["raison"]


@pytest.mark.parametrize("etat", [EtatCreneau.GELE, EtatCreneau.ECHEC, EtatCreneau.PUBLIE])
def test_un_creneau_verrouille_n_est_pas_une_option(client, db_session, etat):
    article = _article(db_session, 1)
    autre = _article(db_session, 2)
    creneau = _creneau(db_session, autre, etat=etat)

    assert "ne se modifie plus" in _option(_options(client, article), creneau)["raison"]


def test_un_article_engage_ailleurs_est_refuse(client, db_session):
    article = _article(db_session)
    _creneau(db_session, article, etat=EtatCreneau.GELE, heures=1)
    libre = _creneau(db_session, heures=5)

    assert "engagé" in _option(_options(client, article), libre)["raison"]


def test_le_creneau_actuel_est_nomme_et_n_est_pas_un_choix(client, db_session):
    article = _article(db_session)
    actuel = _creneau(db_session, article, heures=5)
    autre = _creneau(db_session, heures=8)

    lecture = _options(client, article)

    assert _option(lecture, actuel)["actuel"] is True
    assert _option(lecture, actuel)["possible"] is False
    assert lecture["prochain_creneau_id"] == autre.id


def test_sous_le_seuil_et_vieux_restent_permis_avec_leur_mention(client, db_session):
    """L'humain prime : le seuil de 60 et la fraicheur n'interdisent rien."""
    article = _article(db_session, relevance_score=settings.publication_min_relevance - 1, age_h=20)
    creneau = _creneau(db_session, heures=5)

    lecture = _options(client, article)

    assert _option(lecture, creneau)["possible"] is True
    assert lecture["age_heures"] == 20
    assert any("sous le seuil" in note and "choix manuel" in note for note in lecture["notes"])
    assert client.patch(f"/schedule/{creneau.id}", json={"article_id": article.id}).status_code == 200


def test_au_dessus_du_seuil_aucune_mention(client, db_session):
    assert _options(client, _article(db_session, relevance_score=60))["notes"] == []


def test_une_note_absente_n_est_pas_sous_le_seuil(client, db_session):
    """Une valeur non lue n'est ni verte ni « 0 » : elle se dit inconnue."""
    lecture = _options(client, _article(db_session, relevance_score=None))

    assert lecture["pertinence"] is None
    assert not any("sous le seuil" in note for note in lecture["notes"])
    assert any("pas encore notée" in note for note in lecture["notes"])


def test_moins_de_deux_heures_est_permis_et_annonce(client, db_session):
    article = _article(db_session)
    proche = _creneau(db_session, heures=1)
    loin = _creneau(db_session, heures=5)

    lecture = _options(client, article)

    assert _option(lecture, proche)["possible"] is True
    assert any("partira sans fenêtre de relecture" in a for a in _option(lecture, proche)["annonces"])
    assert _option(lecture, loin)["annonces"] == []


def test_sous_le_seuil_de_regel_on_le_dit(client, db_session):
    article = _article(db_session)
    tres_proche = _creneau(db_session, heures=(settings.publication_relecture_regel_minutes - 5) / 60)

    annonces = _option(_options(client, article), tres_proche)["annonces"]

    assert len(annonces) == 2
    assert f"{settings.publication_relecture_regel_minutes} min" in annonces[1]


# --- La limite de purge ----------------------------------------------------------


def test_purge_avant_composition_grise_l_option_et_dit_pourquoi(client, db_session):
    """Publie il y a 1 h, purge a +47 h ; un creneau a +72 h se compose a +48 h : trop tard."""
    article = _article(db_session, age_h=1)
    trop_loin = _creneau(db_session, heures=72)
    a_temps = _creneau(db_session, heures=70)

    lecture = _options(client, article)

    assert _option(lecture, a_temps)["possible"] is True
    option = _option(lecture, trop_loin)
    assert option["possible"] is False
    assert "purgé" in option["raison"]
    refus = client.patch(f"/schedule/{trop_loin.id}", json={"article_id": article.id})
    assert refus.status_code == 409 and "purgé" in refus.json()["detail"]


def test_deja_hors_retention_sans_attendre_la_purge(client, db_session):
    """Purge horaire pas encore passee : l'article est la, mais condamne. Sa composition
    tomberait au prochain passage de l'agent, deja au-dela de la limite."""
    article = _article(db_session, age_h=49)
    creneau = _creneau(db_session, heures=5)

    assert "purgé" in _option(_options(client, article), creneau)["raison"]


def test_la_limite_derive_de_la_configuration(client, db_session, monkeypatch):
    article = _article(db_session, age_h=1)
    creneau = _creneau(db_session, heures=72)
    assert _option(_options(client, article), creneau)["possible"] is False

    monkeypatch.setattr(settings, "retention_days", 3)
    assert _option(_options(client, article), creneau)["possible"] is True

    monkeypatch.setattr(settings, "retention_days", 2)
    monkeypatch.setattr(settings, "publication_composition_heures", 30)
    assert _option(_options(client, article), creneau)["possible"] is True


def test_un_brouillon_valide_leve_la_limite_de_purge(client, db_session):
    article = _article(db_session, age_h=1)
    post = _post(db_session, article)
    trop_loin = _creneau(db_session, heures=72)

    option = _option(_options(client, article), trop_loin)

    assert option["possible"] is True and option["brouillon"] is True and option["composition_a"] is None
    assert client.patch(f"/schedule/{trop_loin.id}", json={"article_id": article.id}).status_code == 200
    db_session.refresh(trop_loin)
    assert trop_loin.post_id == post.id


def test_un_brouillon_perime_ne_leve_pas_la_limite(client, db_session):
    """Sans fond, le gel le lacherait, et l'article purge laisserait un creneau mort."""
    article = _article(db_session, age_h=1)
    _post(db_session, article, slides=[{**COVER, "image_url": None}])
    trop_loin = _creneau(db_session, heures=72)

    option = _option(_options(client, article), trop_loin)

    assert option["possible"] is False and option["brouillon"] is False


def test_un_brouillon_de_l_autre_gabarit_ne_leve_pas_la_limite(client, db_session):
    article = _article(db_session, age_h=1)
    _post(db_session, article, template="kinda_reel")
    trop_loin = _creneau(db_session, heures=72)

    assert _option(_options(client, article), trop_loin)["possible"] is False


def test_un_article_jamais_note_garde_son_sursis_de_purge(client, db_session):
    """Meme predicat que `_predicat_purgeable` : un non-juge beneficie du sursis."""
    article = _article(db_session, age_h=1, relevance_score=None)
    creneau = _creneau(db_session, heures=72)

    assert _option(_options(client, article), creneau)["possible"] is True


def test_sans_date_de_publication_la_purge_compte_depuis_l_ajout(client, db_session):
    article = _article(db_session, published_at=None, fetched_at=_maintenant() - dt.timedelta(hours=1))
    trop_loin = _creneau(db_session, heures=72)

    assert _option(_options(client, article), trop_loin)["possible"] is False


def test_composition_prevue_sur_l_option(client, db_session):
    article = _article(db_session, age_h=1)
    loin = _creneau(db_session, heures=30)
    proche = _creneau(db_session, heures=5)

    lecture = _options(client, article)

    assert _option(lecture, loin)["composition_due"] is False
    ecart = dt.datetime.fromisoformat(_option(lecture, loin)["composition_a"]) - _maintenant()
    assert dt.timedelta(hours=5.9) < ecart < dt.timedelta(hours=6.1)
    assert _option(lecture, proche)["composition_due"] is True


@pytest.mark.parametrize("champs", [
    {"age_h": 47.5},
    {"age_h": 48.5},
    {"age_h": 24 * 6.9, "relevance_score": None},
    {"age_h": 24 * 7.1, "relevance_score": None},
    {"published_at": None, "fetched_at": _maintenant() - dt.timedelta(hours=47.5)},
    {"published_at": None, "fetched_at": _maintenant() - dt.timedelta(hours=48.5)},
])
def test_le_calcul_de_purge_dit_ce_que_fait_la_purge(db_session, champs):
    """Le calcul lit la regle a l'envers : il ne doit jamais s'en ecarter."""
    article = _article(db_session, **champs)
    annonce = moment_de_purge(article) <= _maintenant()

    purges = purge_old_articles(db_session, settings.retention_days)

    assert (purges == 1) is annonce


# --- Le sens inverse : les actus d'un creneau ------------------------------------


def test_les_actus_d_un_creneau_portent_titre_pertinence_et_age(client, db_session):
    forte = _article(db_session, 1, relevance_score=90, age_h=3)
    faible = _article(db_session, 2, relevance_score=40, age_h=10)
    creneau = _creneau(db_session, heures=5)

    lecture = _actus(client, creneau)

    assert [c["article_id"] for c in lecture["candidats"]][:2] == [forte.id, faible.id]
    assert _candidat(lecture, forte)["titre"] == "Actu 1"
    assert _candidat(lecture, forte)["pertinence"] == 90
    assert _candidat(lecture, forte)["age_heures"] == 3
    assert _candidat(lecture, forte)["notes"] == []
    assert any("sous le seuil" in n for n in _candidat(lecture, faible)["notes"])
    assert _candidat(lecture, faible)["possible"] is True


def test_les_actus_possibles_passent_avant_les_impossibles(client, db_session):
    exclue = _article(db_session, 1, relevance_score=99, file_exclu_raison="x")
    permise = _article(db_session, 2, relevance_score=40)
    creneau = _creneau(db_session, heures=5)

    lecture = _actus(client, creneau)

    assert [c["article_id"] for c in lecture["candidats"]] == [permise.id, exclue.id]
    assert "exclu" in _candidat(lecture, exclue)["raison"]


def test_publies_et_doublons_ne_sont_pas_proposes(client, db_session):
    original = _article(db_session, 1)
    _article(db_session, 2, duplicate_of_id=original.id)
    _article(db_session, 3, status=ArticleStatus.USED)
    creneau = _creneau(db_session)

    assert [c["article_id"] for c in _actus(client, creneau)["candidats"]] == [original.id]


def test_les_actus_d_un_creneau_gele_le_disent(client, db_session):
    occupant = _article(db_session, 1)
    creneau = _creneau(db_session, occupant, etat=EtatCreneau.GELE)
    _article(db_session, 2)

    lecture = _actus(client, creneau)

    assert "ne se modifie plus" in lecture["raison_creneau"]
    assert all(c["possible"] is False for c in lecture["candidats"])


def test_l_actu_vue_du_creneau_nomme_le_creneau_qu_elle_quitte(client, db_session):
    article = _article(db_session)
    ailleurs = _creneau(db_session, article, heures=5)
    creneau = _creneau(db_session, heures=8)

    candidat = _candidat(_actus(client, creneau), article)

    assert dt.datetime.fromisoformat(candidat["creneau_actuel"]).replace(tzinfo=dt.timezone.utc) \
        == ailleurs.publier_a.replace(tzinfo=dt.timezone.utc)


def test_404_sur_les_lectures(client):
    assert client.get("/articles/999/creneaux-possibles").status_code == 404
    assert client.get("/schedule/999/actus-possibles").status_code == 404


# --- La lecture et le PATCH disent la meme chose ---------------------------------


def _matrice(db):
    original = _article(db, 1)
    articles = [
        original,
        _article(db, 2, status=ArticleStatus.USED),
        _article(db, 3, duplicate_of_id=original.id),
        _article(db, 4, file_exclu_raison="x"),
        _article(db, 5, media=None),
        _article(db, 6, relevance_score=10),
        _article(db, 7, age_h=40),
    ]
    articles[4].title = ""
    db.commit()
    creneaux = [
        _creneau(db, heures=1),
        _creneau(db, heures=72),
        _creneau(db, heures=5, format_=FormatCreneau.REEL),
        _creneau(db, _article(db, 8), etat=EtatCreneau.GELE, heures=3),
        _creneau(db, _article(db, 9), etat=EtatCreneau.PUBLIE, heures=4),
    ]
    return articles, creneaux


def test_les_lectures_et_le_patch_ne_divergent_jamais(client, db_session):
    """Le verrou de la factorisation : sur toute la matrice, l'option dit ce que le PATCH
    fera, dans les deux sens de lecture. Le PATCH est joue pour de vrai, puis defait."""
    articles, creneaux = _matrice(db_session)
    verifies = 0
    for article in articles:
        lecture = _options(client, article)
        for creneau in creneaux:
            option = _option(lecture, creneau)
            candidats = {c["article_id"]: c for c in _actus(client, creneau)["candidats"]}
            if article.id in candidats:
                assert candidats[article.id]["raison"] == option["raison"], (article.id, creneau.id)
            avant = (creneau.article_id, creneau.post_id, creneau.etat, creneau.epingle)
            reponse = client.patch(f"/schedule/{creneau.id}", json={"article_id": article.id})
            if option["possible"]:
                assert reponse.status_code == 200, (article.id, creneau.id, reponse.text)
            else:
                assert reponse.status_code == 409, (article.id, creneau.id, reponse.text)
                assert reponse.json()["detail"] == option["raison"]
            creneau.article_id, creneau.post_id, creneau.etat, creneau.epingle = avant
            db_session.commit()
            verifies += 1
    assert verifies == len(articles) * len(creneaux)


def test_les_lectures_n_ecrivent_rien(client, db_session):
    articles, creneaux = _matrice(db_session)
    ecritures = []

    def noter(_c, _curseur, requete, *_):
        if requete.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            ecritures.append(requete)

    moteur = db_session.get_bind()
    event.listen(moteur, "before_cursor_execute", noter)
    try:
        _options(client, articles[0])
        _actus(client, creneaux[0])
        client.get("/articles")
    finally:
        event.remove(moteur, "before_cursor_execute", noter)

    assert ecritures == []


def test_les_lectures_n_ouvrent_aucune_requete_par_ligne(client, db_session):
    """Dix fois plus de lignes, le meme nombre de requetes, brouillons compris."""

    def peupler(debut, n):
        for i in range(debut, debut + n):
            article = _article(db_session, i)
            _post(db_session, article)
            _creneau(db_session, article, _post(db_session, article), heures=5 + i)

    peupler(1, 2)
    creneau = _creneau(db_session, heures=72)
    cible = _article(db_session, 900)
    # Une premiere lecture : les chargements paresseux d'une session neuve ne comptent pas.
    _options(client, cible)
    _actus(client, creneau)
    with CompteRequetes(db_session) as petit_options:
        _options(client, cible)
    with CompteRequetes(db_session) as petit_actus:
        _actus(client, creneau)
    with CompteRequetes(db_session) as petit_liste:
        client.get("/articles")

    peupler(10, 20)
    with CompteRequetes(db_session) as grand_options:
        _options(client, cible)
    with CompteRequetes(db_session) as grand_actus:
        _actus(client, creneau)
    with CompteRequetes(db_session) as grand_liste:
        client.get("/articles")

    assert grand_options.nombre == petit_options.nombre
    assert grand_actus.nombre == petit_actus.nombre
    assert grand_liste.nombre == petit_liste.nombre


def test_les_actus_d_un_reel_n_ouvrent_aucune_requete_par_ligne(client, db_session):
    """Le cout connu de l'arbitrage reel (`posts_en_base` par article) n'est pas repris."""
    _source(db_session)  # la source RSS des articles : un compte ne se cite pas lui-meme
    compte = Source(name="Fred Again", type=SourceType.INSTAGRAM, url="ig:fredagain", categorie=SourceCategorie.ARTISTE)
    db_session.add(compte)
    db_session.commit()
    for i in range(1, 3):
        _article(db_session, i, mentioned_artists=["Fred Again"])
    creneau = _creneau(db_session, heures=5, format_=FormatCreneau.REEL)
    with CompteRequetes(db_session) as petit:
        _actus(client, creneau)
    for i in range(3, 20):
        _article(db_session, i, mentioned_artists=["Fred Again"])
    with CompteRequetes(db_session) as grand:
        _actus(client, creneau)

    assert grand.nombre == petit.nombre


# --- Le mode, lu et jamais recopie ------------------------------------------------


@pytest.mark.parametrize("mode, attendu", [("arret", "rien ne sera composé ni publié"), ("apercu", "aperçu")])
def test_le_mode_est_dit_par_le_serveur(client, db_session, monkeypatch, mode, attendu):
    monkeypatch.setattr("app.schedule.programmation.mode_effectif", lambda: mode)
    article = _article(db_session)
    creneau = _creneau(db_session)

    for lecture in (_options(client, article), _actus(client, creneau)):
        assert lecture["mode"] == mode
        assert attendu in lecture["annonce_mode"].lower()


def test_en_mode_reel_aucune_annonce(client, db_session, monkeypatch):
    monkeypatch.setattr("app.schedule.programmation.mode_effectif", lambda: "reel")

    assert _options(client, _article(db_session))["annonce_mode"] is None


def test_le_module_de_mode_reste_importable():
    assert mode_publication.mode_effectif() in {"arret", "apercu", "reel"}


# --- Le PATCH et la validation qui nomme ce qui sort ------------------------------


def test_le_patch_refuse_si_l_occupant_nomme_n_est_plus_le_bon(client, db_session):
    """La validation nomme ce qui sort : l'arbitrage a pu changer d'occupant entre-temps."""
    prevu = _article(db_session, 1)
    arrive = _article(db_session, 2)
    voulu = _article(db_session, 3)
    creneau = _creneau(db_session, arrive)

    reponse = client.patch(f"/schedule/{creneau.id}", json={"article_id": voulu.id, "occupant_attendu": prevu.id})

    assert reponse.status_code == 409
    db_session.refresh(creneau)
    assert creneau.article_id == arrive.id


def test_occupant_attendu_vide_vaut_creneau_libre(client, db_session):
    voulu = _article(db_session, 1)
    arrive = _article(db_session, 2)
    creneau = _creneau(db_session, arrive)

    assert client.patch(f"/schedule/{creneau.id}", json={"article_id": voulu.id, "occupant_attendu": None}).status_code == 409
    assert client.patch(f"/schedule/{creneau.id}", json={"article_id": voulu.id, "occupant_attendu": arrive.id}).status_code == 200
    db_session.refresh(creneau)
    assert creneau.article_id == voulu.id and creneau.epingle is True


def test_le_geste_n_appelle_ni_meta_ni_claude(client, db_session, monkeypatch):
    """Aucun chemin vers `media_publish`, aucune composition au clic : le geste n'ecrit
    que le creneau, et l'agent compose ensuite."""
    import socket

    from app.content import compose

    def interdit(*_a, **_k):
        raise AssertionError("appel interdit au clic")

    monkeypatch.setattr(compose, "appeler_compositeur", interdit)
    monkeypatch.setattr(compose, "make_claude_composer", interdit)
    monkeypatch.setattr(socket.socket, "connect", interdit)
    article = _article(db_session)
    creneau = _creneau(db_session, heures=5)

    assert client.patch(f"/schedule/{creneau.id}", json={"article_id": article.id}).status_code == 200

    db_session.refresh(creneau)
    assert creneau.post_id is None and creneau.etat is EtatCreneau.PROGRAMME


def test_le_creneau_sans_post_dit_quand_il_sera_compose(client, db_session):
    article = _article(db_session)
    loin = _creneau(db_session, article, heures=30)
    proche = _creneau(db_session, _article(db_session, 2), heures=5)
    libre = _creneau(db_session, heures=7)
    compose_ = _creneau(db_session, _article(db_session, 3), _post(db_session), heures=9)

    vues = {c["id"]: c for c in client.get("/schedule").json()}

    assert vues[loin.id]["composition_due"] is False and vues[loin.id]["composition_a"]
    assert vues[proche.id]["composition_due"] is True
    assert vues[libre.id]["composition_a"] is None
    assert vues[compose_.id]["composition_a"] is None


def test_en_arret_la_composition_est_dite_suspendue(client, db_session, monkeypatch):
    monkeypatch.setattr("app.schedule.programmation.mode_effectif", lambda: "arret")
    creneau = _creneau(db_session, _article(db_session), heures=30)

    vue = next(c for c in client.get("/schedule").json() if c["id"] == creneau.id)

    assert vue["composition_suspendue"] is True
    assert _option(_options(client, _article(db_session, 2)), creneau)["composition_suspendue"] is True


# --- ArticleOut : l'etat reel sur la carte ----------------------------------------


def test_la_carte_recoit_son_creneau(client, db_session):
    article = _article(db_session, 1)
    creneau = _creneau(db_session, article, heures=30)

    vue = next(a for a in client.get("/articles").json() if a["id"] == article.id)

    assert vue["creneau"]["creneau_id"] == creneau.id
    assert vue["creneau"]["etat"] == "programme"
    assert vue["creneau"]["composition_a"]
    assert vue["raison_non_programmable"] is None


def test_sans_creneau_la_carte_le_sait(client, db_session):
    article = _article(db_session)

    vue = client.get(f"/articles/{article.id}").json()

    assert vue["creneau"] is None and vue["raison_non_programmable"] is None


@pytest.mark.parametrize("champs, attendu", [
    ({"status": ArticleStatus.USED}, "déjà publié"),
    ({"file_exclu_raison": "x"}, "exclu"),
])
def test_une_carte_non_programmable_recoit_sa_raison(client, db_session, champs, attendu):
    article = _article(db_session, **champs)

    assert attendu in client.get(f"/articles/{article.id}").json()["raison_non_programmable"]


def test_une_carte_gelee_montre_le_gel_et_n_est_pas_programmable(client, db_session):
    article = _article(db_session)
    _creneau(db_session, article, etat=EtatCreneau.GELE, heures=1)

    vue = client.get(f"/articles/{article.id}").json()

    assert vue["creneau"]["etat"] == "gele"
    assert "engagé" in vue["raison_non_programmable"]


# --- 6. Les retraits --------------------------------------------------------------


@pytest.mark.parametrize("identifiant", [1, 999])
def test_generer_a_la_main_n_existe_plus(client, db_session, identifiant):
    _article(db_session)

    assert client.post(f"/articles/{identifiant}/posts").status_code in (404, 405)


def test_un_post_hors_creneau_reste_consultable_et_editable(client, db_session):
    post = _post(db_session, _article(db_session))

    assert client.get(f"/posts/{post.id}").status_code == 200
    assert client.patch(f"/posts/{post.id}", json={"caption": "Corrigee"}).status_code == 200
    assert client.get(f"/posts/{post.id}").json()["caption"] == "Corrigee"


def test_create_post_reste_au_robot():
    from app.content.compose import create_post
    from app.schedule import runner

    assert runner.create_post is create_post


def test_la_representante_d_un_evenement_est_marquee(client, db_session):
    from app.models import Event, EventObservation, SourceRole

    representant = _article(db_session, 1)
    doublon = _article(db_session, 2, duplicate_of_id=representant.id)
    moment = _maintenant()
    evenement = Event(titre="Actu", first_seen_at=moment, last_seen_at=moment)
    db_session.add(evenement)
    db_session.commit()
    for article in (representant, doublon):
        db_session.add(EventObservation(
            event_id=evenement.id, article_id=article.id, platform=SourceType.RSS, source_id=article.source_id,
            source_role=SourceRole.SECONDARY, observed_at=moment, title=article.title, url=article.url,
            source_name="Tsugi"))
    db_session.commit()

    observations = {o["article_id"]: o for o in client.get(f"/events/{evenement.id}").json()["observations"]}

    assert observations[representant.id]["representant"] is True
    assert observations[doublon.id]["representant"] is False


def test_un_agent_peut_composer_un_creneau_programme_a_la_main(client, db_session):
    """Bout en bout : Programmer, l'agent compose et gele sans autre geste."""
    article = _article(db_session)
    creneau = _creneau(db_session, heures=1)
    assert client.patch(f"/schedule/{creneau.id}", json={"article_id": article.id}).status_code == 200

    composes, prepares = [], []

    def composer(a):
        from app.content.compose import CoverVariante, PostDraft

        composes.append(a.id)
        variante = CoverVariante(headline="**A**", title="**T**", subtitle="**S**")
        return PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=["#electro"])

    def preparer(post, a, dossier):
        prepares.append(post.id)
        return ["p_01.jpg"]

    maintenant = _maintenant()
    tourner(db_session, maintenant, mode="apercu", composer=composer, preparer=preparer,
            publier=lambda *a, **k: "ig", notifier=lambda *_: None, horloge=lambda: maintenant)

    assert composes == [article.id]
    assert len(prepares) == 1
    db_session.refresh(creneau)
    assert creneau.post_id is not None and creneau.article_id == article.id
