"""Ce que la Programmation et la carte montrent, sur Chromium reel.

Une ligne de creneau : le titre est l'information qu'on vient verifier, les boutons
le tronquaient (« Charlotte de Witte ann… ») a 1440 px. La carte : l'issue d'un article
retenu par la ceinture, et « Changer » sur ce qui porte un creneau modifiable.
"""

import pytest

from tests.dashboard.test_dashboard_programmer import (
    UN,
    _article,
    _creneau,
    _jouer,
    _programmation,
    chromium_absent,
)

TITRE = "Charlotte de Witte annonce une tournée mondiale et un nouvel EP pour le printemps prochain en Europe"
A_VERIFIER = "Publication peut-être en ligne : vérifie le compte, puis confirme qu'elle n'est pas partie"
VUE_CRENEAU = {
    "creneau_id": 7, "publier_a": UN, "format": "carrousel", "etat": "programme", "epingle": False,
    "post_id": None, "composition_a": None, "composition_due": False, "composition_suspendue": False,
}


@chromium_absent
@pytest.mark.parametrize("largeur", [1440, 390])
def test_le_titre_entier_se_lit_et_les_actions_passent_dessous(largeur):
    def geste(page):
        _programmation(page)
        return page.evaluate(
            """() => {
              const ligne = document.querySelector("#schedule-list .post-row");
              const titre = ligne.querySelector(".slot-titre");
              const grow = ligne.querySelector(".grow").getBoundingClientRect();
              const actions = ligne.querySelector(".slot-actions").getBoundingClientRect();
              return {
                tronque: titre.scrollWidth > titre.clientWidth + 1,
                texte: titre.textContent,
                grow: grow.width,
                ligne: ligne.clientWidth,
                bas_titre: grow.bottom,
                haut_actions: actions.top,
                deborde: document.documentElement.scrollWidth - document.documentElement.clientWidth,
              };
            }"""
        )

    creneau = _creneau(article_id=42, article_title=TITRE, post_id=None, epingle=True)
    mesure, _, _ = _jouer(geste, creneaux=[creneau], largeur=largeur)

    assert mesure["texte"] == TITRE and not mesure["tronque"], mesure
    assert mesure["grow"] >= mesure["ligne"] * 0.9, mesure
    assert mesure["haut_actions"] >= mesure["bas_titre"], mesure
    assert mesure["deborde"] <= 1, mesure


@chromium_absent
def test_une_carte_retenue_par_la_ceinture_propose_la_verification():
    def geste(page):
        page.wait_for_selector(".card")
        page.click(".card .reintegrer")
        page.wait_for_selector(".verif-publication:not([hidden])")
        page.click(".pas-parti")
        page.wait_for_timeout(150)
        return page.text_content(".card .note-file")

    retenue = _article(file_exclu_raison=None, exclusion_a_verifier=True, raison_non_programmable=A_VERIFIER)
    note, ecritures, _ = _jouer(geste, articles=[retenue])

    assert note == A_VERIFIER
    assert ("POST", "/articles/42/reintegrer", {"confirmation": "PAS_PARTI"}) in ecritures


@chromium_absent
def test_une_carte_sur_un_creneau_modifiable_montre_changer_meme_verrouillee():
    def geste(page):
        page.wait_for_selector(".card")
        return page.text_content(".card .programmer")

    carte = _article(creneau=VUE_CRENEAU, raison_non_programmable="Article déjà gelé sur un autre créneau")
    texte, _, _ = _jouer(geste, articles=[carte])

    assert "Changer" in texte
