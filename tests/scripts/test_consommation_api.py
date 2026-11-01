"""L'agregat de consommation : un cout qu'on ne sait pas calculer se dit INCONNU."""

import datetime as dt
import json

import pytest

from app.config import settings
from scripts.diagnostic import consommation_api as script

MAINTENANT = dt.datetime(2026, 11, 15, 12, tzinfo=dt.timezone.utc)


def _ligne(**champs) -> dict:
    return {
        "le": "2026-11-15T08:00:00+00:00",
        "poste": "notation",
        "modele": "claude-haiku-4-5",
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "web_search_requests": None,
        "stop_reason": "end_turn",
        "request_id": "req",
        **champs,
    }


def _poser(dossier, *lignes, mois="2026-11", brut=""):
    dossier.mkdir(parents=True, exist_ok=True)
    texte = "".join(json.dumps(ligne) + "\n" for ligne in lignes) + brut
    (dossier / f"usage_anthropic-{mois}.jsonl").write_text(texte)


@pytest.fixture()
def dossier():
    return settings.meta_token_path.parent


def _rapport(dossier, jours=7):
    return script.rapport(dossier, jours=jours, maintenant=MAINTENANT)


# -- le cout d'une ligne ------------------------------------------------------


def test_cout_entree_et_sortie():
    assert script.cout_ligne(_ligne()) == pytest.approx(6.0)


def test_cout_du_cache():
    ligne = _ligne(
        input_tokens=0, output_tokens=0,
        cache_read_input_tokens=1_000_000, cache_creation_input_tokens=1_000_000,
    )
    assert script.cout_ligne(ligne) == pytest.approx(0.1 + 1.25)


def test_cout_de_la_recherche_web():
    ligne = _ligne(
        poste="recherche_web", modele="claude-sonnet-5", input_tokens=0, output_tokens=0,
        web_search_requests=3,
    )
    assert script.cout_ligne(ligne) == pytest.approx(0.03)


@pytest.mark.parametrize(
    ("modele", "attendu"),
    [
        ("claude-haiku-4-5", 1 + 5),
        ("claude-haiku-5-5", 0.10 + 0.50),
        ("claude-sonnet-5", 2 + 10),
        ("claude-sonnet-5-5", 2 + 10),
        ("claude-haiku-4-5-20251001", 1 + 5),
    ],
)
def test_table_de_prix(modele, attendu):
    assert script.cout_ligne(_ligne(modele=modele)) == pytest.approx(attendu)


def test_modele_hors_table_cout_inconnu():
    assert script.cout_ligne(_ligne(modele="claude-futur-9")) is None


def test_sans_modele_cout_inconnu():
    assert script.cout_ligne(_ligne(modele=None)) is None


@pytest.mark.parametrize(
    "champ",
    ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"],
)
def test_un_token_null_rend_le_cout_inconnu(champ):
    assert script.cout_ligne(_ligne(**{champ: None})) is None


def test_recherche_null_hors_poste_de_recherche_ne_compte_pas():
    """L'outil n'est demande que par `recherche_web` : ailleurs, absent veut dire aucun."""
    assert script.cout_ligne(_ligne(web_search_requests=None)) is not None


def test_recherche_null_sur_le_poste_de_recherche_cout_inconnu():
    assert script.cout_ligne(_ligne(poste="recherche_web", web_search_requests=None)) is None


# -- le rapport ---------------------------------------------------------------


def test_fichier_absent_le_dit_et_ne_dit_pas_zero(dossier):
    texte = _rapport(dossier)
    assert "aucune donnee" in texte.lower()
    assert "0 $" not in texte
    assert "0.00" not in texte


def test_lecture_seule_le_dossier_absent_n_est_pas_cree(dossier):
    _rapport(dossier)
    assert not dossier.exists()


def test_agrege_par_jour_et_par_poste(dossier):
    _poser(
        dossier,
        _ligne(),
        _ligne(),
        _ligne(poste="arbitrage"),
        _ligne(le="2026-11-14T08:00:00+00:00"),
    )
    texte = _rapport(dossier)
    lignes = texte.splitlines()
    jour_poste = [x for x in lignes if x.startswith("2026-11-15") and "notation" in x]
    assert len(jour_poste) == 1
    assert " 2 " in jour_poste[0]
    assert "2000000" in jour_poste[0]
    assert "$12.0000" in jour_poste[0]
    assert any(x.startswith("2026-11-14") and "notation" in x for x in lignes)
    assert any(x.startswith("2026-11-15") and "arbitrage" in x for x in lignes)
    assert "TOTAL" in texte and "$24.0000" in texte
    assert "exclu" not in texte


def test_ligne_null_affichee_inconnue_et_exclue_du_total(dossier):
    _poser(dossier, _ligne(), _ligne(input_tokens=None, output_tokens=None))
    texte = _rapport(dossier)
    rangee = next(x for x in texte.splitlines() if x.startswith("2026-11-15"))
    assert "INCONNU" in rangee
    assert "1 ligne" in rangee
    total = next(x for x in texte.splitlines() if x.startswith("TOTAL"))
    assert "$6.0000" in total
    assert "1 ligne" in total and "exclu" in total


def test_tout_inconnu_le_total_n_est_pas_zero(dossier):
    _poser(dossier, _ligne(input_tokens=None), _ligne(modele="claude-futur-9"))
    texte = _rapport(dossier)
    total = next(x for x in texte.splitlines() if x.startswith("TOTAL"))
    assert "INCONNU" in total
    assert "2 lignes" in total
    assert "$0" not in texte


def test_les_tokens_null_ne_comptent_pas_pour_zero(dossier):
    _poser(dossier, _ligne(input_tokens=None, output_tokens=None))
    rangee = next(x for x in _rapport(dossier).splitlines() if x.startswith("2026-11-15"))
    assert "INCONNU" in rangee


def test_les_jours_hors_fenetre_sont_ignores(dossier):
    _poser(dossier, _ligne(), _ligne(le="2026-11-01T08:00:00+00:00"))
    texte = _rapport(dossier, jours=3)
    assert "2026-11-01" not in texte
    assert "$6.0000" in texte


def test_rien_dans_la_fenetre_ne_dit_pas_zero(dossier):
    _poser(dossier, _ligne(le="2026-10-01T08:00:00+00:00"), mois="2026-10")
    texte = _rapport(dossier, jours=3)
    assert "aucune donnee" in texte.lower()
    assert "$0" not in texte


def test_une_ligne_corrompue_est_dite_et_exclue(dossier):
    _poser(dossier, _ligne(), brut='{"le": "2026-11\n[1, 2]\n{"le": "pas une date", "poste": "x"}\n')
    texte = _rapport(dossier)
    total = next(x for x in texte.splitlines() if x.startswith("TOTAL"))
    assert "$6.0000" in total
    assert "3 lignes illisibles" in texte


def test_uniquement_des_lignes_corrompues_ne_dit_pas_aucune_donnee(dossier):
    _poser(dossier, brut="pas du json\n")
    texte = _rapport(dossier)
    assert "aucune donnee" not in texte.lower()
    assert "1 ligne illisible" in texte


def test_un_fichier_illisible_est_dit(dossier):
    dossier.mkdir(parents=True)
    (dossier / "usage_anthropic-2026-11.jsonl").mkdir()  # lire un dossier leve OSError
    texte = _rapport(dossier)
    assert "illisible" in texte
    assert "aucune donnee" not in texte.lower()


def test_main_rend_zero_et_imprime(capsys):
    assert script.main(["--jours", "2"]) == 0
    assert "aucune donnee" in capsys.readouterr().out.lower()
