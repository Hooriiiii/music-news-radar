"""En mode `arret`, `tourner` sort avant `_marquer_manques` : le constat des creneaux
depasses n'accuse pas l'agent. Mode illisible : on ne sait pas, on le dit."""

import datetime as dt

import pytest

from app.config import settings
from app.models import EtatCreneau
from app.supervision import ATTENTION, INCONNU, PANNE, voyant_creneaux
from tests.socle.test_supervision import LONGTEMPS, MAINTENANT, _creneau, _ecrire, _reveil

VIEUX = dt.timedelta(hours=30)


def test_en_arret_un_creneau_depasse_n_est_pas_une_panne(db_session, monkeypatch):
    _reveil(monkeypatch, LONGTEMPS)
    _ecrire("publication.json", {"mode": "arret"})
    _creneau(db_session, VIEUX, EtatCreneau.PROGRAMME)

    voyant = voyant_creneaux(db_session, MAINTENANT)

    assert voyant.niveau == ATTENTION, "un fait, ni rouge ni vert"
    assert "mode Arrêt" in voyant.raison and "pas une panne" in voyant.raison
    assert "ne les traite plus" not in voyant.raison


def test_en_arret_par_le_env_le_constat_reste_neutre(db_session, monkeypatch):
    _reveil(monkeypatch, LONGTEMPS)
    monkeypatch.setattr(settings, "publication_mode", "arret")
    _creneau(db_session, VIEUX, EtatCreneau.PROGRAMME)
    assert voyant_creneaux(db_session, MAINTENANT).niveau == ATTENTION


@pytest.mark.parametrize("mode", ["apercu", "reel"])
def test_hors_arret_l_agent_reste_accuse(db_session, monkeypatch, mode):
    _reveil(monkeypatch, LONGTEMPS)
    _ecrire("publication.json", {"mode": mode})
    _creneau(db_session, VIEUX, EtatCreneau.PROGRAMME)
    assert voyant_creneaux(db_session, MAINTENANT).niveau == PANNE


@pytest.mark.parametrize("contenu", ["{tronque", '{"mode": "n_importe"}'])
def test_un_mode_illisible_reste_inconnu_devant_un_creneau_depasse(db_session, monkeypatch, contenu):
    _reveil(monkeypatch, LONGTEMPS)
    settings.publication_mode_path.parent.mkdir(parents=True, exist_ok=True)
    settings.publication_mode_path.write_text(contenu)
    _creneau(db_session, VIEUX, EtatCreneau.PROGRAMME)

    voyant = voyant_creneaux(db_session, MAINTENANT)

    assert voyant.niveau == INCONNU and "mode" in voyant.raison
