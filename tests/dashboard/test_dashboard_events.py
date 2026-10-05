"""Sentinelle (Phase 2A, Task 4) : aucun champ d'`EventOut`/`EventObservationOut`
ne doit etre silencieusement absent du panneau Événements du dashboard --
meme philosophie que `test_le_dashboard_propose_la_categorie_ecologie` dans
`tests/api/test_api.py`, qui verifie deja qu'aucune `Category` n'est invisible a
l'usage."""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from app.schemas import EventDetailOut, EventOut

PAGE = pathlib.Path("app/static/dashboard.html").read_text(encoding="utf-8")

# Champs herites (id) ou volontairement geres autrement qu'un acces direct
# `ev.<champ>` / `o.<champ>` dans le JS -- documente ici plutot que devine.
_EVENT_CHAMPS_IGNORES = {"id"}  # affiche via un gabarit ("événement #${ev.id}")
_OBSERVATION_CHAMPS_IGNORES = {
    "id", "event_id", "observed_at",
    # `source_name` (deja affiche) porte l'information editoriale ; `source_id`
    # n'est qu'une cle etrangere technique, sans valeur ajoutee a la lecture.
    "source_id",
}  # techniques, pas montres a l'editorial


def test_le_panneau_evenements_affiche_tous_les_champs_d_event():
    for champ in EventOut.model_fields:
        if champ in _EVENT_CHAMPS_IGNORES:
            continue
        assert f"ev.{champ}" in PAGE, champ


def test_le_panneau_evenements_affiche_tous_les_champs_d_observation():
    champs_observation = set(EventDetailOut.model_fields) - set(EventOut.model_fields)
    assert "observations" in champs_observation
    from app.schemas import EventObservationOut

    for champ in EventObservationOut.model_fields:
        if champ in _OBSERVATION_CHAMPS_IGNORES:
            continue
        assert f"o.{champ}" in PAGE, champ


def test_le_panneau_evenements_existe():
    assert 'id="events-panel"' in PAGE
    # Les panneaux sont une NAVIGATION (`data-panneau`), les filtres de la
    # grille des pastilles (`data-preset`) : deux portees, deux rangees.
    assert 'data-panneau="events"' in PAGE
    assert "loadEventsPanel" in PAGE


# --- Tour de correction 1 : l'ecart corroboration/pertinence doit vraiment
# rendre les deux badges visuellement distincts, pas seulement exister en
# theorie dans le schema. On execute le VRAI extrait de dashboard.html (pas
# une reimplementation en Python qui pourrait se desynchroniser) via Node,
# pour deux evenements synthetiques : un bien corrobore mais peu pertinent
# (le cas Apple/proces/country du plan Phase 2A), un aligne.

_DEBUT_EXTRAIT = "const pertinence = ev.relevance_representant;"
# Phase 2C : le bloc de badges s'est allonge (velocite, acceleration,
# nouveaute, France). La borne suit la DERNIERE ligne du gabarit plutot que
# la corroboration, sinon l'extrait coupe au milieu d'un litteral de gabarit
# et Node refuse de le parser -- c'est ainsi que ce test a rougi a l'ajout.
_FIN_EXTRAIT = 'pertinence_france, 0, "100 = marqueur France'


def _fonctions_auxiliaires() -> str:
    """`badgeFeature` et `escapeHtml`, extraites de la PAGE elle-meme et non
    reecrites ici : une reimplementation en dur se desynchroniserait du code
    reellement servi, ce que ce fichier existe precisement pour empecher."""
    morceaux = []
    for depart in ("function badgeFeature(", "function escapeHtml("):
        debut = PAGE.index(depart)
        # La fonction se termine a la premiere accolade en colonne 0.
        fin = PAGE.index("\n}", debut) + len("\n}")
        morceaux.append(PAGE[debut:fin])
    return "\n".join(morceaux)


def _extrait_calcul_badges() -> str:
    debut = PAGE.index(_DEBUT_EXTRAIT)
    fin = PAGE.index(_FIN_EXTRAIT, debut) + len(_FIN_EXTRAIT)
    # `_FIN_EXTRAIT` s'arrete a l'interieur de la derniere ligne : on rejoint
    # la fin reelle du gabarit pour que l'extrait soit du JS complet.
    fin = PAGE.index("`;", fin) + len("`;")
    extrait = PAGE[debut:fin]
    # Le code de production ecrit dans le DOM (`row.querySelector(...).innerHTML
    # = ...`) ; on ne change que la cible de l'affectation pour recuperer la
    # chaine produite, la logique elle-meme n'est pas touchee.
    extrait = extrait.replace(
        'row.querySelector(".event-metrics").innerHTML = `', "const html = `"
    )
    assert "const html = `" in extrait, "extraction ratee : dashboard.html a change de forme"
    return extrait


def _classes_badges(ev: dict) -> str:
    if shutil.which("node") is None:
        pytest.skip("node indisponible dans cet environnement")
    seuil_match = re.search(r"ECART_CORROBORATION_SEUIL\s*=\s*(\d+)", PAGE)
    assert seuil_match, "constante de seuil introuvable dans dashboard.html"
    script = f"""
    const ECART_CORROBORATION_SEUIL = {seuil_match.group(1)};
    {_fonctions_auxiliaires()}
    const ev = {json.dumps(ev)};
    {_extrait_calcul_badges()}
    console.log(html);
    """
    resultat = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return resultat.stdout


def test_ecart_corroboration_pertinence_rend_les_badges_distincts():
    """Bien corrobore (5 sources indep., K~81) mais peu pertinent (20) --
    doit porter la classe .ecart sur les deux badges, et un titre explicatif."""
    html = _classes_badges(
        {
            "source_count": 5, "independent_source_count": 5, "platform_count": 3,
            "freshness": 80.0, "corroboration": 81.0, "relevance_representant": 20,
        }
    )
    assert 'class="badge corrob ecart"' in html
    assert 'class="badge corrob ecart"' in html.split("pertinence 20")[0]
    assert "hors ligne éditoriale" in html


def test_pas_d_ecart_quand_pertinence_et_corroboration_s_alignent():
    """Meme corroboration forte, mais une pertinence elevee aussi (75) : pas
    de signal d'alerte, les deux grandeurs racontent la meme histoire."""
    html = _classes_badges(
        {
            "source_count": 5, "independent_source_count": 5, "platform_count": 3,
            "freshness": 80.0, "corroboration": 81.0, "relevance_representant": 75,
        }
    )
    assert "ecart" not in html


def test_pas_d_ecart_quand_pertinence_inconnue():
    """Representant purge (pertinence absente) : pas de fausse alerte -- on ne
    sait pas, donc on ne signale rien plutot que de deviner."""
    html = _classes_badges(
        {
            "source_count": 5, "independent_source_count": 5, "platform_count": 3,
            "freshness": 80.0, "corroboration": 81.0, "relevance_representant": None,
        }
    )
    assert "ecart" not in html
    assert "pertinence ?" in html
