"""L'exclusion de la file a enfin un retour en arriere.

`articles.file_exclu_raison` refuse l'article PARTOUT (`store._articles_publiables`),
il est ecrit par « 🚫 Sauter », par le gel et par la composition -- et aucun
endpoint ne le remettait a NULL. Le seul recours etait un UPDATE a la main.
"""

import datetime as dt

import pytest

from app.config import settings
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Source, SourceType
from app.pipeline.cluster import RAISON_DOUBLON_PREFIXE
from app.schedule.publication_incertaine import (
    PREFIXE_ECHEC_PUBLICATION,
    RAISON_RECLAMATION,
    publication_incertaine,
)
from app.schedule.runner import RECLAMATION_EN_COURS


def _source(db):
    source = Source(name="Presse", type=SourceType.RSS, url="https://example.com/feed.xml")
    db.add(source)
    db.commit()
    return source


def _article(db, source, *, raison=None, hash_="h1", duplicate_of_id=None):
    article = Article(
        source_id=source.id,
        url=f"https://example.com/{hash_}",
        title="Une actu",
        published_at=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
        relevance_score=70,
        status=ArticleStatus.NEW,
        raw_hash=hash_,
        file_exclu_raison=raison,
        duplicate_of_id=duplicate_of_id,
    )
    db.add(article)
    db.commit()
    return article


def test_la_raison_d_exclusion_est_exposee_par_l_api(client, db_session):
    """Sans ce champ, la grille ne peut pas montrer qu'un article est hors file."""
    source = _source(db_session)
    _article(db_session, source, raison="saute depuis le dashboard")

    (vu,) = client.get("/articles").json()
    assert vu["file_exclu_raison"] == "saute depuis le dashboard"


def test_reintegrer_leve_l_exclusion(client, db_session):
    source = _source(db_session)
    article = _article(db_session, source, raison="gel impossible : image ecartee")

    reponse = client.post(f"/articles/{article.id}/reintegrer")
    assert reponse.status_code == 200
    assert reponse.json()["file_exclu_raison"] is None
    db_session.refresh(article)
    assert article.file_exclu_raison is None


def test_reintegrer_est_idempotent(client, db_session):
    """L'UI peut avoir une grille d'une seconde en retard : deux clics ne sont
    pas une erreur."""
    source = _source(db_session)
    article = _article(db_session, source, raison="saute depuis le dashboard")

    assert client.post(f"/articles/{article.id}/reintegrer").status_code == 200
    assert client.post(f"/articles/{article.id}/reintegrer").status_code == 200


def test_reintegrer_un_article_inconnu_rend_404(client):
    assert client.post("/articles/4242/reintegrer").status_code == 404


def test_une_exclusion_de_dedup_est_refusee_et_nomme_l_outil_qui_tient(client, db_session):
    """Effacer la raison seule ne tient pas : `link_duplicates` relie l'article
    au passage suivant (docs/reference/dedup-et-retention.md). Mentir sur ce point serait pire que refuser."""
    source = _source(db_session)
    representant = _article(db_session, source, hash_="rep")
    article = _article(
        db_session,
        source,
        hash_="dup",
        raison=f"{RAISON_DOUBLON_PREFIXE} (article {representant.id}, similarite 0.85)",
        duplicate_of_id=representant.id,
    )

    reponse = client.post(f"/articles/{article.id}/reintegrer")
    assert reponse.status_code == 409
    assert "marquer_doublon_manuel" in reponse.json()["detail"]
    db_session.refresh(article)
    assert article.file_exclu_raison is not None


def test_une_exclusion_de_gel_sur_un_doublon_se_leve_quand_meme(client, db_session):
    """Ce qui est refuse est la raison POSEE PAR LA DEDUP, pas le fait d'etre un
    doublon : une exclusion de gel se leve, le lien de doublon continue sa vie."""
    source = _source(db_session)
    representant = _article(db_session, source, hash_="rep2")
    article = _article(
        db_session,
        source,
        hash_="dup2",
        raison="gel impossible : le post n'a pas de visuel publiable",
        duplicate_of_id=representant.id,
    )

    assert client.post(f"/articles/{article.id}/reintegrer").status_code == 200
    db_session.refresh(article)
    assert article.file_exclu_raison is None
    assert article.duplicate_of_id == representant.id


# --- « Le post est peut-etre en ligne » : un clic ne suffit plus ---


ECHEC_DELAI = f"{PREFIXE_ECHEC_PUBLICATION}delai depasse"


def _echec(db, article, erreur, publier_a=dt.datetime(2026, 10, 1, 12, tzinfo=dt.timezone.utc)):
    creneau = Creneau(publier_a=publier_a, etat=EtatCreneau.ECHEC, article_id=article.id, erreur=erreur)
    db.add(creneau)
    db.commit()
    return creneau


@pytest.mark.parametrize("raison", [RAISON_RECLAMATION, ECHEC_DELAI])
def test_une_exclusion_de_publication_exige_la_confirmation(client, db_session, raison):
    article = _article(db_session, _source(db_session), raison=raison)

    for corps in ({}, {"confirmation": "oui"}):
        reponse = client.post(f"/articles/{article.id}/reintegrer", json=corps)
        assert reponse.status_code == 409
        assert "compte Instagram" in reponse.json()["detail"]
    assert client.post(f"/articles/{article.id}/reintegrer").status_code == 409

    db_session.refresh(article)
    assert article.file_exclu_raison == raison


@pytest.mark.parametrize("raison", [RAISON_RECLAMATION, ECHEC_DELAI])
def test_la_confirmation_leve_l_exclusion_et_le_doute_du_creneau(client, db_session, raison):
    article = _article(db_session, _source(db_session), raison=raison)
    creneau = _echec(db_session, article, raison)

    reponse = client.post(f"/articles/{article.id}/reintegrer", json={"confirmation": "PAS_PARTI"})

    assert reponse.status_code == 200
    db_session.refresh(article)
    db_session.refresh(creneau)
    assert article.file_exclu_raison is None
    assert creneau.etat == EtatCreneau.ECHEC
    assert not publication_incertaine(creneau.erreur)
    assert raison in creneau.erreur, "l'historique de ce qui s'est passe reste lisible"


def test_la_confirmation_ne_touche_pas_le_creneau_d_un_autre_article(client, db_session):
    source = _source(db_session)
    article = _article(db_session, source, raison=RAISON_RECLAMATION, hash_="a")
    autre = _article(db_session, source, raison=RAISON_RECLAMATION, hash_="b")
    creneau_autre = _echec(db_session, autre, RAISON_RECLAMATION)

    client.post(f"/articles/{article.id}/reintegrer", json={"confirmation": "PAS_PARTI"})

    db_session.refresh(creneau_autre)
    assert creneau_autre.erreur == RAISON_RECLAMATION


def test_la_confirmation_est_refusee_tant_que_le_robot_peut_encore_publier(client, db_session):
    """L'utilisateur regarde Instagram, ne voit rien, et confirme : c'est VRAI
    pendant que le robot attend l'heure dans le tunnel, ou reprend un creneau en
    retard. Le post partirait APRES ce « il n'est pas parti »."""
    article = _article(db_session, _source(db_session), raison=RAISON_RECLAMATION)
    limite = dt.timedelta(minutes=settings.publication_retard_max_minutes) + RECLAMATION_EN_COURS
    maintenant = dt.datetime.now(dt.timezone.utc)
    creneau = _echec(db_session, article, RAISON_RECLAMATION, publier_a=maintenant - limite + dt.timedelta(minutes=5))

    reponse = client.post(f"/articles/{article.id}/reintegrer", json={"confirmation": "PAS_PARTI"})

    assert reponse.status_code == 409
    assert "en cours" in reponse.json()["detail"]
    db_session.refresh(article)
    db_session.refresh(creneau)
    assert article.file_exclu_raison == RAISON_RECLAMATION
    assert creneau.erreur == RAISON_RECLAMATION

    creneau.publier_a = maintenant - limite - dt.timedelta(minutes=5)
    db_session.commit()
    reponse = client.post(f"/articles/{article.id}/reintegrer", json={"confirmation": "PAS_PARTI"})
    assert reponse.status_code == 200


def test_un_doute_de_creneau_sous_une_autre_raison_demande_aussi_la_confirmation(client, db_session):
    """Une autre raison peut recouvrir le doute (dedup, reparation) : la lever d'un
    clic annoncerait un retour en file que la ceinture refuserait."""
    article = _article(db_session, _source(db_session), raison="saute depuis le dashboard")
    _echec(db_session, article, RAISON_RECLAMATION)

    assert client.post(f"/articles/{article.id}/reintegrer").status_code == 409


def test_saute_depuis_le_dashboard_reste_en_un_clic(client, db_session):
    article = _article(db_session, _source(db_session), raison="saute depuis le dashboard")

    assert client.post(f"/articles/{article.id}/reintegrer").status_code == 200
    db_session.refresh(article)
    assert article.file_exclu_raison is None


def test_la_confirmation_ne_contourne_pas_la_dedup_d_archive(client, db_session):
    article = _article(db_session, _source(db_session), raison=f"{RAISON_DOUBLON_PREFIXE} (article 1, x)")

    reponse = client.post(f"/articles/{article.id}/reintegrer", json={"confirmation": "PAS_PARTI"})
    assert reponse.status_code == 409
    assert "marquer_doublon_manuel" in reponse.json()["detail"]


@pytest.mark.parametrize(
    ("raison", "attendu"),
    [
        (RAISON_RECLAMATION, True),
        (ECHEC_DELAI, True),
        ("saute depuis le dashboard", False),
        ("gel impossible : echec de publication : x", False),
        (None, False),
    ],
)
def test_l_api_dit_a_la_grille_quelle_exclusion_demande_une_verification(client, db_session, raison, attendu):
    _article(db_session, _source(db_session), raison=raison)

    (vu,) = client.get("/articles").json()
    assert vu["exclusion_a_verifier"] is attendu
