"""La console publie seule avec verification humaine (decision du 2026-10-30).

Trois proprietes cote serveur :
- les statuts `published` / `approved` / `used` ne se posent plus a la main, et rien
  ne fait sortir un article de USED (ce qui le protege de la purge et d'une
  republication) ;
- un post au programme ne se supprime pas : son retrait est « Ne pas publier » ;
- « Ne pas publier » retire un creneau PROGRAMME ou GELE sans jamais croiser une
  reclamation de l'agent.

Raison et course : docs/reference/relecture-et-controle-editorial.md, « Ne pas publier ».
"""

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import event, update

from app.api import schedule as api_schedule
from app.config import settings
from app.content.compose import CoverVariante, PostDraft
from app.models import (
    Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau, Post, PostStatus, Source, SourceType,
)
from app.schedule.relecture import RAISON_RELECTURE
from app.schedule.runner import RAISON_RECLAMATION, tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import appliquer_arbitrage, _articles_publiables

COVER = {"kind": "cover", "headline": "**A**", "title": "**T**", "subtitle": "**S**",
         "image_url": "https://img/1.jpg"}
EN_COURS = "Publication en cours"


@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)


def _maintenant():
    return dt.datetime.now(dt.timezone.utc)


def _article(db, n=1, **champs):
    source = db.query(Source).first()
    if source is None:
        source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
        db.add(source)
        db.commit()
    article = Article(source_id=source.id, url=f"https://t/{n}", title=f"Actu {n}", raw_hash=f"h{n}",
                      relevance_score=90 - n, hotness_score=20, published_at=_maintenant(),
                      media=[{"type": "photo", "url": f"https://img/{n}.jpg"}], **champs)
    db.add(article)
    db.commit()
    return article


def _post(db, article=None, status=PostStatus.DRAFT):
    post = Post(article_id=article.id if article else None, article_title="Actu", article_url="https://t/1",
                caption="Legende", template="kinda_post", slides=[COVER], status=status,
                variants={"variantes": [COVER]})
    db.add(post)
    db.commit()
    return post


def _creneau(db, article=None, post=None, etat=EtatCreneau.PROGRAMME, minutes=300, **champs):
    creneau = Creneau(publier_a=_maintenant() + dt.timedelta(minutes=minutes), format=FormatCreneau.CARROUSEL,
                      article_id=article.id if article else None, post_id=post.id if post else None,
                      etat=etat, **champs)
    db.add(creneau)
    db.commit()
    return creneau


def _gele(db, minutes=300, **champs):
    article = _article(db)
    post = _post(db, article)
    creneau = _creneau(db, article, post, EtatCreneau.GELE, minutes, fichiers=["p_01.jpg"],
                       elements=[{"kind": "cover", "fichier": "p_01.jpg"}], **champs)
    dossier = Path(settings.publication_dir) / str(creneau.id)
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "p_01.jpg").write_bytes(b"jpeg")
    return article, post, creneau, dossier


def _agent_reclame_en_douce(db, creneau):
    """La reclamation committee par l'autre processus : notre objet reste perime."""
    db.execute(
        update(Creneau).where(Creneau.id == creneau.id)
        .values(etat=EtatCreneau.ECHEC, erreur=RAISON_RECLAMATION)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def _sur_la_grille(db, creneau):
    """Un creneau libre hors de la grille disparait a l'arbitrage : celui-ci reste."""
    creneau.publier_a = prochains_horaires(_maintenant(), 1)[0]
    db.commit()


def _retirer(client, creneau):
    return client.patch(f"/schedule/{creneau.id}", json={"sauter": True, "occupant_attendu": creneau.article_id, "post_attendu": creneau.post_id})


class Agent:
    def __init__(self):
        self.composes, self.publies = [], []

    def composer(self, article):
        self.composes.append(article.id)
        variante = CoverVariante(headline="**A**", title="**T**", subtitle="**S**")
        return PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=["#electro"])

    def preparer(self, post, article, dossier):
        return ["p_01.jpg"]

    def publier(self, dossier, noms, legende, publier_vraiment, **_crochets):
        self.publies.append(noms)
        return "ig42"

    def tourner(self, db):
        maintenant = _maintenant()
        return tourner(db, maintenant, mode="reel", composer=self.composer, preparer=self.preparer,
                       publier=self.publier, notifier=lambda *_: None, horloge=lambda: maintenant)


# --- 1. Les statuts ne se posent plus a la main --------------------------------


@pytest.mark.parametrize("statut", ["published", "approved", "draft"])
def test_patch_post_ne_pose_aucun_statut(client, db_session, statut):
    article = _article(db_session)
    post = _post(db_session, article)

    reponse = client.patch(f"/posts/{post.id}", json={"status": statut})

    assert reponse.status_code == 409
    assert "robot" in reponse.json()["detail"]
    db_session.refresh(post)
    db_session.refresh(article)
    assert (post.status, post.published_at, article.status) == (PostStatus.DRAFT, None, ArticleStatus.NEW)


def test_un_post_publie_ne_redevient_pas_brouillon(client, db_session):
    """Redevenu brouillon, il redeviendrait supprimable : l'archive editoriale y passerait."""
    post = _post(db_session, _article(db_session), status=PostStatus.PUBLISHED)

    assert client.patch(f"/posts/{post.id}", json={"status": "draft"}).status_code == 409
    db_session.refresh(post)
    assert post.status is PostStatus.PUBLISHED


def test_un_statut_refuse_n_applique_rien_et_ne_rouvre_rien(client, db_session):
    _, post, creneau, dossier = _gele(db_session)

    reponse = client.patch(f"/posts/{post.id}", json={"status": "published", "caption": "Autre",
                                                      "slides": [COVER]})

    assert reponse.status_code == 409
    db_session.refresh(post)
    db_session.refresh(creneau)
    assert post.caption == "Legende"
    assert creneau.etat == EtatCreneau.GELE and dossier.exists()


def test_le_robot_ne_lit_jamais_approved():
    racine = Path(__file__).resolve().parents[2] / "app"
    lecteurs = [p.relative_to(racine).as_posix() for p in racine.rglob("*.py")
                if "PostStatus.APPROVED" in p.read_text(encoding="utf-8")]
    assert lecteurs == []


def test_patch_article_ne_pose_pas_used(client, db_session):
    article = _article(db_session)

    reponse = client.patch(f"/articles/{article.id}", json={"status": "used"})

    assert reponse.status_code == 409
    assert "robot" in reponse.json()["detail"]
    db_session.refresh(article)
    assert article.status == ArticleStatus.NEW


@pytest.mark.parametrize("statut", ["new", "reviewed", "used"])
def test_un_article_used_ne_sort_jamais_de_used(client, db_session, statut):
    article = _article(db_session, status=ArticleStatus.USED)

    reponse = client.patch(f"/articles/{article.id}", json={"status": statut})

    assert reponse.status_code == 409
    assert "purge" in reponse.json()["detail"]
    db_session.refresh(article)
    assert article.status == ArticleStatus.USED


def test_une_publication_pendant_le_clic_garde_l_article_used(client, db_session):
    """Le robot pose USED entre notre lecture et notre ecriture : l'objet en memoire
    dit encore « new », seule la condition de l'UPDATE tient."""
    article = _article(db_session)
    db_session.execute(
        update(Article).where(Article.id == article.id).values(status=ArticleStatus.USED)
        .execution_options(synchronize_session=False)
    )
    db_session.commit()
    assert article.status == ArticleStatus.NEW, "l'objet en memoire doit rester perime"

    assert client.patch(f"/articles/{article.id}", json={"status": "reviewed"}).status_code == 409
    db_session.refresh(article)
    assert article.status == ArticleStatus.USED


@pytest.mark.parametrize("avant, apres", [
    (ArticleStatus.NEW, "reviewed"), (ArticleStatus.REVIEWED, "new"),
])
def test_vu_et_a_traiter_restent_possibles(client, db_session, avant, apres):
    article = _article(db_session, status=avant)

    reponse = client.patch(f"/articles/{article.id}", json={"status": apres})

    assert reponse.status_code == 200 and reponse.json()["status"] == apres


# --- 3. DELETE /posts/{id} -----------------------------------------------------


def test_supprimer_refuse_une_publication_en_vol(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    _creneau(db_session, article, post, EtatCreneau.ECHEC, erreur=RAISON_RECLAMATION)

    reponse = client.delete(f"/posts/{post.id}")

    assert reponse.status_code == 409
    assert reponse.json()["detail"] == client.patch(f"/posts/{post.id}", json={"caption": "x"}).json()["detail"]
    assert db_session.get(Post, post.id) is not None


@pytest.mark.parametrize("etat", [EtatCreneau.LIBRE, EtatCreneau.PROGRAMME, EtatCreneau.GELE])
def test_supprimer_refuse_un_post_au_programme(client, db_session, etat):
    article = _article(db_session)
    post = _post(db_session, article)
    creneau = _creneau(db_session, article, post, etat)

    reponse = client.delete(f"/posts/{post.id}")

    assert reponse.status_code == 409
    assert "Ne pas publier" in reponse.json()["detail"]
    assert db_session.get(Post, post.id) is not None
    db_session.refresh(creneau)
    assert creneau.post_id == post.id


def test_supprimer_refuse_un_post_peut_etre_en_ligne(client, db_session):
    """Reclamation ancienne : plus « en vol » pour l'affichage, mais le post est
    peut-etre sur le compte, et ce brouillon est tout ce qu'on en garde."""
    article = _article(db_session)
    post = _post(db_session, article)
    _creneau(db_session, article, post, EtatCreneau.ECHEC, minutes=-600, erreur=RAISON_RECLAMATION)

    reponse = client.delete(f"/posts/{post.id}")

    assert reponse.status_code == 409
    assert "peut-être en ligne" in reponse.json()["detail"]


def test_un_creneau_termine_ne_retient_pas_le_brouillon(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    _creneau(db_session, article, post, EtatCreneau.MANQUE, minutes=-600)

    assert client.delete(f"/posts/{post.id}").status_code == 204


def test_apres_ne_pas_publier_le_brouillon_se_supprime(client, db_session):
    _, post, creneau, _ = _gele(db_session)

    assert client.delete(f"/posts/{post.id}").status_code == 409
    assert _retirer(client, creneau).status_code == 200
    assert client.delete(f"/posts/{post.id}").status_code == 204


# --- 4. Ne pas publier ---------------------------------------------------------


def test_ne_pas_publier_un_creneau_gele_rend_le_creneau_et_ecarte_l_article(client, db_session):
    article, post, creneau, dossier = _gele(db_session, epingle=True, erreur=RAISON_RELECTURE)

    reponse = _retirer(client, creneau)

    assert reponse.status_code == 200
    vue = reponse.json()
    assert (vue["etat"], vue["epingle"], vue["article_id"], vue["post_id"], vue["erreur"]) == (
        "libre", False, None, None, None)
    db_session.refresh(article)
    db_session.refresh(post)
    assert article.file_exclu_raison is not None
    assert "Ne pas publier" in article.file_exclu_raison
    assert post.status is PostStatus.DRAFT and post.caption == "Legende", "le brouillon reste en base"
    assert not dossier.exists(), "des fichiers orphelins seraient republies si l'etat revenait a GELE"


def test_ne_pas_publier_efface_fichiers_et_manifeste(db_session):
    from app.schedule.reouverture import ne_pas_publier

    _, _, creneau, _ = _gele(db_session)
    ne_pas_publier(db_session, creneau, (creneau.article_id, creneau.post_id))
    db_session.refresh(creneau)
    assert (creneau.etat, creneau.fichiers, creneau.elements) == (EtatCreneau.LIBRE, None, None)


def test_ne_pas_publier_un_creneau_programme(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    creneau = _creneau(db_session, article, post, epingle=True)

    assert _retirer(client, creneau).json()["post_id"] is None
    db_session.refresh(article)
    assert article.file_exclu_raison is not None


def test_ne_pas_publier_n_a_pas_de_plancher_de_vingt_minutes(client, db_session):
    """Le plancher protege un RE-GEL ; ici rien n'est refait."""
    article, _, creneau, _ = _gele(db_session, minutes=2)

    assert _retirer(client, creneau).status_code == 200
    db_session.refresh(article)
    assert article.file_exclu_raison is not None


def test_ne_pas_publier_vaut_aussi_apres_l_heure_tant_que_le_robot_peut_partir(client, db_session):
    """Mac reveille en retard : un GELE passe de 10 min part encore. C'est justement
    la que le retrait compte, et un creneau passe n'est jamais rempli a nouveau."""
    autre = _article(db_session, n=2)
    article = _article(db_session, n=3)
    post = _post(db_session, article)
    creneau = _creneau(db_session, article, post, EtatCreneau.GELE, minutes=-10, fichiers=["p_01.jpg"])

    assert _retirer(client, creneau).status_code == 200
    db_session.refresh(creneau)
    assert (creneau.etat, creneau.article_id) == (EtatCreneau.LIBRE, None)
    assert autre.id != article.id


def test_une_publication_en_vol_refuse_le_retrait(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    creneau = _creneau(db_session, article, post, EtatCreneau.ECHEC, erreur=RAISON_RECLAMATION)

    reponse = _retirer(client, creneau)

    assert reponse.status_code == 409
    assert EN_COURS in reponse.json()["detail"]
    db_session.refresh(creneau)
    db_session.refresh(article)
    assert (creneau.etat, creneau.post_id) == (EtatCreneau.ECHEC, post.id)
    assert article.file_exclu_raison is None


def test_une_reclamation_concurrente_fait_echouer_l_ecriture(client, db_session):
    """PREUVE PAR MUTATION dans la reference : retirer la condition d'etat de
    l'UPDATE ferait redevenir LIBRE un creneau dont le conteneur part chez Meta."""
    article, post, creneau, dossier = _gele(db_session)
    _agent_reclame_en_douce(db_session, creneau)
    assert creneau.etat == EtatCreneau.GELE, "l'objet en memoire doit rester perime"

    reponse = _retirer(client, creneau)

    assert reponse.status_code == 409
    assert EN_COURS in reponse.json()["detail"]
    db_session.refresh(creneau)
    db_session.refresh(article)
    assert (creneau.etat, creneau.post_id, creneau.fichiers) == (EtatCreneau.ECHEC, post.id, ["p_01.jpg"])
    assert article.file_exclu_raison is None, "un retrait refuse n'exclut rien"
    assert dossier.exists()


def test_un_post_change_entre_temps_fait_echouer_l_ecriture(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    creneau = _creneau(db_session, article, None)
    db_session.execute(
        update(Creneau).where(Creneau.id == creneau.id).values(post_id=post.id)
        .execution_options(synchronize_session=False)
    )
    db_session.commit()
    assert creneau.post_id is None

    assert _retirer(client, creneau).status_code == 409
    db_session.refresh(article)
    assert article.file_exclu_raison is None


@pytest.mark.parametrize("etat", [EtatCreneau.PUBLIE, EtatCreneau.APERCU, EtatCreneau.MANQUE])
def test_un_creneau_termine_ne_se_retire_pas(client, db_session, etat):
    article = _article(db_session)
    creneau = _creneau(db_session, article, None, etat)

    assert _retirer(client, creneau).status_code == 409
    db_session.refresh(creneau)
    assert creneau.etat == etat


def test_l_exclusion_et_le_creneau_partent_ensemble_ou_pas_du_tout(client, db_session):
    """Atomique : si l'exclusion ne s'ecrit pas, le creneau reste GELE."""
    from app.schedule.reouverture import ne_pas_publier

    article, _, creneau, dossier = _gele(db_session)

    def casser(session, *_):
        raise RuntimeError("panne pendant l'exclusion")

    event.listen(db_session, "before_flush", casser)
    try:
        with pytest.raises(RuntimeError):
            ne_pas_publier(db_session, creneau, (creneau.article_id, creneau.post_id))
    finally:
        event.remove(db_session, "before_flush", casser)
    db_session.rollback()
    db_session.refresh(creneau)
    db_session.refresh(article)
    assert creneau.etat == EtatCreneau.GELE and creneau.fichiers == ["p_01.jpg"]
    assert article.file_exclu_raison is None
    assert dossier.exists()


def test_une_exclusion_existante_garde_sa_raison(client, db_session):
    """Une raison de dedup dit ce que `reintegrer` doit refuser : ne pas l'ecraser."""
    article = _article(db_session, file_exclu_raison="doublon d'archive : deja publie")
    creneau = _creneau(db_session, article, None)

    assert _retirer(client, creneau).status_code == 200
    db_session.refresh(article)
    assert article.file_exclu_raison == "doublon d'archive : deja publie"


def test_un_post_orphelin_se_retire_et_ne_revient_pas(client, db_session):
    """Article purge : `porte_un_post_orphelin` protegeait ce creneau de l'arbitrage,
    le lacher doit donc vraiment le lacher."""
    post = _post(db_session)
    creneau = _creneau(db_session, None, post, minutes=120)
    _sur_la_grille(db_session, creneau)

    assert _retirer(client, creneau).status_code == 200
    db_session.refresh(creneau)
    assert (creneau.post_id, creneau.etat) == (None, EtatCreneau.LIBRE)
    assert db_session.get(Post, post.id) is not None


def test_un_arbitrage_en_panne_ne_fait_pas_mentir_le_retrait(client, db_session, monkeypatch):
    """Le retrait est committe : un 500 dirait qu'il a echoue."""
    _, _, creneau, _ = _gele(db_session)

    def panne(*_):
        raise RuntimeError("fenetres invalides")

    monkeypatch.setattr(api_schedule, "appliquer_arbitrage", panne)

    reponse = _retirer(client, creneau)

    assert reponse.status_code == 200 and reponse.json()["etat"] == "libre"


def test_les_autres_gestes_restent_refuses_sur_un_creneau_gele(client, db_session):
    _, _, creneau, _ = _gele(db_session)

    assert client.patch(f"/schedule/{creneau.id}", json={"epingle": True, "occupant_attendu": creneau.article_id, "post_attendu": creneau.post_id}).status_code == 409
    libre = Creneau(publier_a=creneau.publier_a + dt.timedelta(hours=6), etat=EtatCreneau.LIBRE)
    db_session.add(libre)
    db_session.commit()
    assert client.patch(f"/schedule/{creneau.id}", json={"echanger_avec": libre.id}).status_code == 409
    assert client.patch(f"/schedule/{libre.id}", json={"echanger_avec": creneau.id}).status_code == 409
    assert client.patch(f"/schedule/{creneau.id}", json={"sauter": True, "epingle": True}).status_code == 422
    db_session.refresh(creneau)
    assert creneau.etat == EtatCreneau.GELE


def test_temoin_sans_retrait_le_robot_publie_ce_creneau(db_session, tmp_path, monkeypatch):
    """Le temoin du test suivant : sans lui, « rien n'est parti » ne prouverait rien."""
    _, _, creneau, _ = _gele(db_session, minutes=-5)
    agent = Agent()

    agent.tourner(db_session)

    assert agent.publies == [["p_01.jpg"]]
    db_session.refresh(creneau)
    assert creneau.etat == EtatCreneau.PUBLIE


def test_un_passage_du_robot_apres_le_retrait_ne_publie_rien(client, db_session):
    article, _, creneau, _ = _gele(db_session, minutes=-5)
    agent = Agent()

    assert _retirer(client, creneau).status_code == 200
    agent.tourner(db_session)

    assert agent.publies == []
    assert article.id not in agent.composes, "ni composition repayee"
    db_session.refresh(creneau)
    assert creneau.etat != EtatCreneau.PUBLIE and creneau.article_id is None


def test_le_creneau_libere_est_repris_mais_jamais_par_l_article_ecarte(client, db_session):
    article, post, creneau, _ = _gele(db_session)
    _sur_la_grille(db_session, creneau)
    autre = _article(db_session, n=2)

    assert _retirer(client, creneau).status_code == 200

    db_session.refresh(creneau)
    assert creneau.article_id == autre.id, "le creneau libere se remplit (normal)"
    assert creneau.post_id != post.id
    maintenant = _maintenant()
    assert article not in _articles_publiables(db_session, maintenant, 50)
    appliquer_arbitrage(db_session, maintenant)
    assert article.id not in {c.article_id for c in db_session.query(Creneau).all()}


def test_le_retrait_se_defait_par_remettre_dans_la_file_sans_repayer_claude(client, db_session):
    article, post, creneau, _ = _gele(db_session)
    assert _retirer(client, creneau).status_code == 200

    reintegre = client.post(f"/articles/{article.id}/reintegrer")

    assert reintegre.status_code == 200, "un retrait se defait en un clic, sans verification du compte"
    appliquer_arbitrage(db_session, _maintenant())
    portes = [c for c in db_session.query(Creneau).all() if c.article_id == article.id]
    assert [c.post_id for c in portes] == [post.id], "le meme brouillon, aucun Claude de plus"
