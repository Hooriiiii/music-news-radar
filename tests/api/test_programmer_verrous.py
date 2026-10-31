"""Ce qui verrouille la main, et le message qui dit pourquoi.

Raison : docs/reference/publication.md, « Programmer ». MANQUE ne suit jamais un
`media_publish` (`runner._marquer_manques` ne lit que LIBRE/PROGRAMME/GELE, et le MANQUE
apres reclamation exige que `encore_publier` ait refuse) : rien n'est parti.
"""

import datetime as dt

import pytest
from sqlalchemy import update

from app.models import Creneau, EtatCreneau
from app.schedule.publication_incertaine import RAISON_RECLAMATION
from tests.api.test_programmer import _article, _carrousels_seuls, _creneau, _maintenant, _option, _options  # noqa: F401


def _passe(db, article, etat, erreur=None):
    creneau = Creneau(publier_a=_maintenant() - dt.timedelta(hours=8), article_id=article.id, etat=etat, erreur=erreur)
    db.add(creneau)
    db.commit()
    return creneau


def test_un_ancien_manque_ne_verrouille_plus_la_main(client, db_session):
    """Le robot le reprenait et la main se voyait dire « deja engage » : rien n'est parti."""
    article = _article(db_session)
    _passe(db_session, article, EtatCreneau.MANQUE, "Mac en veille")
    libre = _creneau(db_session)

    assert client.get(f"/articles/{article.id}").json()["raison_non_programmable"] is None
    assert _option(_options(client, article), libre)["possible"] is True
    assert client.patch(f"/schedule/{libre.id}", json={"article_id": article.id}).status_code == 200


def test_une_carte_programmee_par_le_robot_reste_modifiable_malgre_un_ancien_manque(client, db_session):
    article = _article(db_session)
    _passe(db_session, article, EtatCreneau.MANQUE, "Mac en veille")
    actuel = _creneau(db_session, article)

    vue = client.get(f"/articles/{article.id}").json()

    assert vue["raison_non_programmable"] is None and vue["creneau"]["creneau_id"] == actuel.id


@pytest.mark.parametrize("etat, erreur, attendu", [
    (EtatCreneau.PUBLIE, None, "déjà publié"),
    (EtatCreneau.APERCU, None, "aperçu"),
    (EtatCreneau.GELE, None, "gelé"),
    (EtatCreneau.ECHEC, RAISON_RECLAMATION, "peut-être en ligne"),
    (EtatCreneau.ECHEC, "echec de publication : delai", "peut-être en ligne"),
])
def test_ce_qui_verrouille_encore_dit_sa_vraie_raison_partout(client, db_session, etat, erreur, attendu):
    article = _article(db_session)
    _passe(db_session, article, etat, erreur)
    libre = _creneau(db_session)

    carte = client.get(f"/articles/{article.id}").json()["raison_non_programmable"]
    option = _option(_options(client, article), libre)["raison"]
    refus = client.patch(f"/schedule/{libre.id}", json={"article_id": article.id})

    assert attendu in carte and carte == option == refus.json()["detail"]
    assert refus.status_code == 409


def test_un_echec_verifie_ne_verrouille_plus(client, db_session):
    article = _article(db_session, file_exclu_raison=RAISON_RECLAMATION)
    _passe(db_session, article, EtatCreneau.ECHEC, RAISON_RECLAMATION)
    libre = _creneau(db_session)

    assert client.post(f"/articles/{article.id}/reintegrer", json={"confirmation": "PAS_PARTI"}).status_code == 200

    assert client.get(f"/articles/{article.id}").json()["raison_non_programmable"] is None
    assert client.patch(f"/schedule/{libre.id}", json={"article_id": article.id}).status_code == 200


@pytest.mark.parametrize("erreur", ["post supprime apres le gel", "publication refusee : le post ne correspond pas"])
def test_un_echec_ou_rien_n_a_pu_partir_ne_verrouille_pas(client, db_session, erreur):
    """Meme regle que le robot : seule une trace de doute ecarte l'article."""
    article = _article(db_session)
    _passe(db_session, article, EtatCreneau.ECHEC, erreur)
    assert client.get(f"/articles/{article.id}").json()["raison_non_programmable"] is None


# --- Remis en file avant la fusion : la carte offre la verification ------------------


def test_la_ceinture_se_lit_sur_la_carte(client, db_session):
    article = _article(db_session)
    _passe(db_session, article, EtatCreneau.ECHEC, RAISON_RECLAMATION)

    vue = client.get(f"/articles/{article.id}").json()

    assert vue["file_exclu_raison"] is None
    assert vue["exclusion_a_verifier"] is True
    assert "peut-être en ligne" in vue["raison_non_programmable"]


def test_sans_trace_de_doute_rien_n_est_a_verifier(client, db_session):
    article = _article(db_session)
    _passe(db_session, article, EtatCreneau.ECHEC, "post supprime apres le gel")
    assert client.get(f"/articles/{article.id}").json()["exclusion_a_verifier"] is False


def test_apres_verification_plus_rien_n_est_a_verifier(client, db_session):
    article = _article(db_session)
    _passe(db_session, article, EtatCreneau.ECHEC, RAISON_RECLAMATION)
    reponse = client.post(f"/articles/{article.id}/reintegrer", json={"confirmation": "PAS_PARTI"})
    assert reponse.json()["exclusion_a_verifier"] is False


def test_retirer_un_vieil_echec_dit_la_raison_exacte(client, db_session):
    article = _article(db_session)
    vieux = _passe(db_session, article, EtatCreneau.ECHEC, RAISON_RECLAMATION)

    reponse = client.patch(f"/schedule/{vieux.id}", json={"sauter": True, "occupant_attendu": vieux.article_id, "post_attendu": vieux.post_id})

    assert reponse.status_code == 409
    assert "Publication en cours" not in reponse.json()["detail"]
    assert "rien à retirer" in reponse.json()["detail"]


def test_si_le_creneau_bouge_pendant_la_verification_rien_n_est_leve(client, db_session, monkeypatch):
    """Le robot enregistre entre la lecture et l'ecriture : zero ligne, on le dit."""
    article = _article(db_session, file_exclu_raison=RAISON_RECLAMATION)
    creneau = _passe(db_session, article, EtatCreneau.ECHEC, RAISON_RECLAMATION)
    identifiant = creneau.id

    def le_robot_passe(*_):
        db_session.execute(
            update(Creneau).where(Creneau.id == identifiant).values(etat=EtatCreneau.PUBLIE, erreur=None)
            .execution_options(synchronize_session=False)
        )
        db_session.commit()
        return False

    monkeypatch.setattr("app.api.articles._robot_peut_encore_publier", le_robot_passe)

    reponse = client.post(f"/articles/{article.id}/reintegrer", json={"confirmation": "PAS_PARTI"})

    assert reponse.status_code == 409
    db_session.expire_all()
    assert article.file_exclu_raison == RAISON_RECLAMATION
