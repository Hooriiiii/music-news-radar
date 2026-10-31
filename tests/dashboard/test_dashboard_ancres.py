"""La console s'ouvre sur la Programmation, et chaque panneau a son ancre (2026-10-31).

L'utilisateur gere le planning : « l'onglet Flux d'actus ne me sert plus ». Une ancre
survit au rechargement, ce qui se casse sans bruit : d'ou la preuve par mutation.
"""

from pathlib import Path

import pytest

from tests.dashboard.test_dashboard_ecritures import _demarrer_serveur, chromium_absent

PAGE = (Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")
ANCRES = {"programmation": "schedule", "flux": "grille", "posts": "posts", "evenements": "events",
          "artistes": "artistes", "sources": "sources"}
SECTIONS = {"schedule": "schedule-panel", "grille": "grid", "posts": "posts-panel", "events": "events-panel",
            "artistes": "artistes-panel", "sources": "sources-panel"}


def _jouer(geste, html=PAGE, ancre=""):
    from playwright.sync_api import sync_playwright

    serveur = _demarrer_serveur(html, [])
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{serveur.server_address[1]}/{ancre}")
            page.wait_for_function("typeof afficherPanneau === 'function' && document.readyState === 'complete'")
            resultat = geste(page)
            navigateur.close()
        return resultat
    finally:
        serveur.shutdown()


def _visibles(page):
    return [nom for nom, id_ in SECTIONS.items() if page.evaluate(f"!document.getElementById('{id_}').hidden")]


def _onglet_actif(page):
    return page.evaluate("document.querySelector('.onglet.on').dataset.panneau")


@chromium_absent
def test_sans_ancre_la_console_s_ouvre_sur_la_programmation():
    def geste(page):
        return (_visibles(page), _onglet_actif(page),
                page.evaluate("document.querySelector('.onglet').dataset.panneau"),
                page.evaluate("document.getElementById('barre-filtres').hidden"))

    assert _jouer(geste) == (["schedule"], "schedule", "schedule", True)


@chromium_absent
@pytest.mark.parametrize(("ancre", "panneau"), ANCRES.items())
def test_chaque_ancre_ouvre_son_panneau_et_survit_au_rechargement(ancre, panneau):
    def geste(page):
        premier = (_visibles(page), _onglet_actif(page))
        page.reload()
        page.wait_for_function("document.readyState === 'complete'")
        return premier, (_visibles(page), _onglet_actif(page)), page.evaluate("location.hash")

    premier, second, lu = _jouer(geste, ancre=f"#{ancre}")
    assert premier == ([panneau], panneau)
    assert second == premier
    assert lu == f"#{ancre}"


@chromium_absent
def test_cliquer_un_onglet_ecrit_son_ancre_et_le_rechargement_y_revient():
    def geste(page):
        page.click('[data-panneau="posts"]')
        ancre = page.evaluate("location.hash")
        page.reload()
        page.wait_for_function("document.readyState === 'complete'")
        return ancre, _visibles(page)

    assert _jouer(geste) == ("#posts", ["posts"])


@chromium_absent
def test_changer_l_ancre_a_la_main_change_le_panneau():
    def geste(page):
        page.evaluate("location.hash = '#artistes'")
        page.wait_for_function("!document.getElementById('artistes-panel').hidden")
        return _visibles(page), _onglet_actif(page)

    assert _jouer(geste) == (["artistes"], "artistes")


@chromium_absent
def test_une_ancre_inconnue_retombe_sur_la_programmation():
    assert _jouer(_visibles, ancre="#nimporte-quoi") == ["schedule"]


@chromium_absent
def test_le_flux_reste_accessible_avec_ses_filtres():
    def geste(page):
        page.click('[data-panneau="grille"]')
        return _visibles(page), page.evaluate("document.getElementById('barre-filtres').hidden")

    assert _jouer(geste) == (["grille"], False)


# --- Preuve negative, par mutation ---------------------------------------------


@chromium_absent
def test_une_ancre_ignoree_au_chargement_rougit_sous_mutation():
    """Sans cette lecture, le rechargement revient en silence au panneau par defaut."""
    cible = "afficherPanneau(panneauDeLAncre(location.hash));\n  // Le chargement"
    assert cible in PAGE
    mutee = PAGE.replace(cible, "afficherPanneau(PANNEAU_PAR_DEFAUT);\n  // Le chargement")
    assert _jouer(_visibles, html=mutee, ancre="#posts") == ["schedule"]


@chromium_absent
def test_un_clic_qui_n_ecrit_pas_l_ancre_rougit_sous_mutation():
    cible = 'history.replaceState(null, "", attendu);'
    assert cible in PAGE
    mutee = PAGE.replace(cible, "")

    def geste(page):
        page.click('[data-panneau="posts"]')
        return page.evaluate("location.hash")

    assert _jouer(geste, html=mutee) != "#posts"
