import datetime as dt

import pytest

from app.config import settings
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Source, SourceType


@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    # Ces tests decrivent la file carrousel seule ; les reels ont leurs propres fichiers.
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)


def _maintenant():
    return dt.datetime.now(dt.timezone.utc)


def _articles(db, *pertinences):
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db.add(source)
    db.commit()
    articles = []
    for n, relevance in enumerate(pertinences):
        article = Article(source_id=source.id, url=f"https://t/{n}", title=f"Actu {n}", raw_hash=f"h{n}",
                          relevance_score=relevance, published_at=_maintenant(),
                          media=[{"type": "photo", "url": f"https://img/{n}.jpg"}])
        db.add(article)
        articles.append(article)
    db.commit()
    return articles


def _creneau(db, heures, article=None, etat=None):
    creneau = Creneau(
        publier_a=_maintenant() + dt.timedelta(hours=heures),
        article_id=article.id if article else None,
        etat=etat or (EtatCreneau.PROGRAMME if article else EtatCreneau.LIBRE),
    )
    db.add(creneau)
    db.commit()
    return creneau


def test_la_file_montre_l_article_de_chaque_creneau(client, db_session):
    (article,) = _articles(db_session, 80)
    _creneau(db_session, 5, article)
    (vue,) = client.get("/schedule").json()
    assert (vue["article_title"], vue["relevance"], vue["etat"]) == ("Actu 0", 80, "programme")
    # L'URL de l'actu, pour que « article #123 » soit un LIEN au dashboard et
    # non un identifiant a recopier a la main. Zero requete de plus : l'article
    # est deja charge pour en tirer titre et pertinence.
    assert vue["article_url"] == "https://t/0"


def test_un_creneau_sans_article_n_annonce_aucune_url(client, db_session):
    """Le creneau est AUTONOME de son article (2026-09-30) : purge, il n'a plus
    d'URL, et le renvoi doit redevenir du texte inerte plutot qu'un lien mort."""
    _creneau(db_session, 5)
    (vue,) = client.get("/schedule").json()
    assert vue["article_id"] is None and vue["article_url"] is None


def test_epingler(client, db_session):
    (article,) = _articles(db_session, 80)
    creneau = _creneau(db_session, 5, article)
    assert client.patch(f"/schedule/{creneau.id}", json={"epingle": True}).json()["epingle"] is True


def test_remplacer_epingle_et_libere_l_ancien_creneau_de_l_article(client, db_session):
    a, b = _articles(db_session, 80, 70)
    premier, second = _creneau(db_session, 5, a), _creneau(db_session, 17, b)
    vue = client.patch(f"/schedule/{second.id}", json={"article_id": a.id}).json()
    assert (vue["article_id"], vue["epingle"]) == (a.id, True)
    db_session.refresh(premier)
    assert (premier.article_id, premier.etat) == (None, EtatCreneau.LIBRE)


def test_sauter_exclut_l_article_de_la_file(client, db_session):
    (article,) = _articles(db_session, 80)
    creneau = _creneau(db_session, 5, article)
    vue = client.patch(f"/schedule/{creneau.id}", json={"sauter": True}).json()
    assert vue["article_id"] is None
    db_session.refresh(article)
    assert article.file_exclu_raison == "saute depuis le dashboard"


def test_decaler_echange_avec_le_creneau_suivant_et_epingle(client, db_session):
    a, b = _articles(db_session, 80, 70)
    premier, second = _creneau(db_session, 5, a), _creneau(db_session, 17, b)
    vue = client.patch(f"/schedule/{premier.id}", json={"decaler": True}).json()
    assert (vue["article_id"], vue["epingle"]) == (b.id, True)
    db_session.refresh(second)
    assert (second.article_id, second.epingle) == (a.id, True)


def test_un_creneau_gele_ne_se_modifie_plus(client, db_session):
    (article,) = _articles(db_session, 80)
    creneau = _creneau(db_session, 1, article, etat=EtatCreneau.GELE)
    assert client.patch(f"/schedule/{creneau.id}", json={"epingle": True}).status_code == 409


def test_une_seule_action_a_la_fois(client, db_session):
    (article,) = _articles(db_session, 80)
    creneau = _creneau(db_session, 5, article)
    reponse = client.patch(f"/schedule/{creneau.id}", json={"epingle": True, "sauter": True})
    assert reponse.status_code == 422


def test_decaler_le_dernier_creneau_est_refuse(client, db_session):
    (article,) = _articles(db_session, 80)
    creneau = _creneau(db_session, 5, article)
    assert client.patch(f"/schedule/{creneau.id}", json={"decaler": True}).status_code == 409


def test_remplacer_par_un_article_deja_publie_est_refuse(client, db_session):
    (article,) = _articles(db_session, 80)
    article.status = ArticleStatus.USED
    db_session.commit()
    creneau = _creneau(db_session, 5)
    assert client.patch(f"/schedule/{creneau.id}", json={"article_id": article.id}).status_code == 409


def test_recalculer_cree_la_file(client, db_session):
    reponse = client.post("/schedule/rebuild")
    assert reponse.status_code == 200
    assert len(reponse.json()) == 9


def test_epingler_un_creneau_vide_est_refuse(client, db_session):
    creneau = _creneau(db_session, 5)
    assert client.patch(f"/schedule/{creneau.id}", json={"epingle": True}).status_code == 409


def test_remplacer_par_un_article_deja_sur_un_creneau_gele_est_refuse(client, db_session):
    (article,) = _articles(db_session, 80)
    _creneau(db_session, 1, article, etat=EtatCreneau.GELE)
    cible = _creneau(db_session, 5)
    assert client.patch(f"/schedule/{cible.id}", json={"article_id": article.id}).status_code == 409


def test_remplacer_par_un_article_deja_sur_un_creneau_apercu_est_refuse(client, db_session):
    (article,) = _articles(db_session, 80)
    _creneau(db_session, 1, article, etat=EtatCreneau.APERCU)
    cible = _creneau(db_session, 5)
    assert client.patch(f"/schedule/{cible.id}", json={"article_id": article.id}).status_code == 409


def test_remplacer_par_un_article_duplique_est_refuse(client, db_session):
    original, article = _articles(db_session, 80, 75)
    article.duplicate_of_id = original.id
    db_session.commit()
    cible = _creneau(db_session, 5)
    assert client.patch(f"/schedule/{cible.id}", json={"article_id": article.id}).status_code == 409


def test_remplacer_par_un_article_exclu_est_refuse(client, db_session):
    (article,) = _articles(db_session, 80)
    article.file_exclu_raison = "saute depuis le dashboard"
    db_session.commit()
    cible = _creneau(db_session, 5)
    assert client.patch(f"/schedule/{cible.id}", json={"article_id": article.id}).status_code == 409


def test_remplacer_libere_un_ancien_creneau_programme_passe(client, db_session):
    (article,) = _articles(db_session, 80)
    ancien = _creneau(db_session, -3, article)
    cible = _creneau(db_session, 5)
    vue = client.patch(f"/schedule/{cible.id}", json={"article_id": article.id}).json()
    assert vue["article_id"] == article.id
    db_session.refresh(ancien)
    assert (ancien.article_id, ancien.etat) == (None, EtatCreneau.LIBRE)


# --- Mode de publication -----------------------------------------------------


def _fichier_mode(tmp_path, monkeypatch):
    chemin = tmp_path / "publication.json"
    monkeypatch.setattr(settings, "publication_mode_path", chemin)
    return chemin


def test_get_mode_sans_fichier_rend_le_repli_env(client, tmp_path, monkeypatch):
    _fichier_mode(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "publication_mode", "arret")
    assert client.get("/publication/mode").json() == {"mode": "arret", "source": "env"}


def test_put_mode_apercu_change_la_source_en_dashboard(client, tmp_path, monkeypatch):
    _fichier_mode(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "publication_mode", "arret")
    reponse = client.put("/publication/mode", json={"mode": "apercu"})
    assert reponse.status_code == 200
    assert reponse.json() == {"mode": "apercu", "source": "dashboard"}
    assert client.get("/publication/mode").json() == {"mode": "apercu", "source": "dashboard"}


def test_put_mode_reel_sans_confirmation_est_refuse(client, tmp_path, monkeypatch):
    _fichier_mode(tmp_path, monkeypatch)
    reponse = client.put("/publication/mode", json={"mode": "reel"})
    assert reponse.status_code == 409
    assert client.get("/publication/mode").json()["mode"] != "reel"


def test_put_mode_reel_avec_mauvaise_confirmation_est_refuse(client, tmp_path, monkeypatch):
    _fichier_mode(tmp_path, monkeypatch)
    reponse = client.put("/publication/mode", json={"mode": "reel", "confirmation": "publier"})
    assert reponse.status_code == 409


def test_put_mode_reel_avec_confirmation_est_accepte(client, tmp_path, monkeypatch):
    _fichier_mode(tmp_path, monkeypatch)
    reponse = client.put("/publication/mode", json={"mode": "reel", "confirmation": "PUBLIER"})
    assert reponse.status_code == 200
    assert reponse.json() == {"mode": "reel", "source": "dashboard"}


def test_put_mode_inconnu_est_refuse_en_422(client, tmp_path, monkeypatch):
    _fichier_mode(tmp_path, monkeypatch)
    reponse = client.put("/publication/mode", json={"mode": "pause"})
    assert reponse.status_code == 422
