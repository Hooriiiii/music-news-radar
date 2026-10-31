"""Le champ d'accroche de la fiche, sur Chromium reel (2026-10-31).

`hl-hint` decrivait un champ qui n'existait pas. Le serveur est simule : ses regles sont
dans tests/publish/test_accroche_gel.py.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.dashboard.test_dashboard_ecritures import ARTICLE, chromium_absent

PAGE = (Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")
PIXEL = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
REFUS = "Trop pres du depart : il faut au moins 20 min."
COVER = {"kind": "cover", "headline": "**Une** accroche", "title": "**Un** titre", "subtitle": "**Un** sous-titre",
         "image_url": "https://e/x.jpg"}
POST = {"id": 7, "article_id": 42, "article_title": "Actu", "status": "draft", "template": "kinda_post",
        "caption": "Legende du serveur.", "hashtags": ["#techno"],
        "slides": [COVER, {"kind": "media", "type": "photo", "url": "https://e/m.jpg"}],
        "variants": {"variantes": [{"headline": "**Autre**"}, {"headline": "**Une** accroche"}], "retenue": 1},
        "assets": None}
REEL = {**POST, "template": "kinda_reel"}


def _jouer(geste, post=POST, *, code=200, overflow=(False, False), html=PAGE, covers_ok=True):
    from playwright.sync_api import sync_playwright

    ecritures, appels_covers = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            chemin = self.path.split("?")[0]
            if chemin == "/":
                return self._repondre(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if chemin in ("/sources", "/posts", "/articles", "/relecture", "/schedule"):
                return self._repondre(200, b"[]", "application/json")
            return self._repondre(404, b"{}", "application/json")

        def do_PATCH(self):  # noqa: N802
            corps = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            ecritures.append(corps)
            if code != 200:
                return self._repondre(code, json.dumps({"detail": REFUS}).encode(), "application/json")
            return self._repondre(200, json.dumps({**post, **corps}).encode(), "application/json")

        def do_POST(self):  # noqa: N802
            appels_covers.append(self.path)
            if not covers_ok:
                return self._repondre(503, b'{"detail":"Chromium absent"}', "application/json")
            rendu = {"covers": [PIXEL, PIXEL], "overflow": list(overflow)}
            return self._repondre(200, json.dumps(rendu).encode(), "application/json")

        def _repondre(self, statut, corps, type_contenu):
            self.send_response(statut)
            self.send_header("Content-Type", type_contenu)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        def log_message(self, *args):
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{serveur.server_address[1]}/#posts")
            page.wait_for_function("typeof renderPostDetail === 'function'")
            page.evaluate("([p, a]) => renderPostDetail(p, a, null)", [post, ARTICLE])
            resultat = geste(page)
            navigateur.close()
        return resultat, ecritures, appels_covers
    finally:
        serveur.shutdown()


NOUVEAU = "**Bien** mieux"


def _ecrire_et_enregistrer(page):
    page.fill("#edit-headline", NOUVEAU)
    page.click("#btn-accroche")
    page.wait_for_function("document.querySelector('#etat-accroche').textContent.match(/[✓✕]/)")
    page.wait_for_timeout(150)


@chromium_absent
def test_le_rappel_des_asterisques_est_sous_le_champ():
    assert PAGE.count('class="hl-hint"') == 1

    def geste(page):
        return page.evaluate(
            "document.querySelector('#edit-headline').compareDocumentPosition("
            "document.querySelector('.hl-hint')) & Node.DOCUMENT_POSITION_FOLLOWING"
        ), page.input_value("#edit-headline")

    (suit, valeur), _, _ = _jouer(geste)
    assert suit
    assert valeur == COVER["headline"]


@chromium_absent
def test_le_gabarit_reel_lit_le_titre_et_le_sous_titre_pas_l_accroche():
    def geste(page):
        return (page.locator("#edit-headline").count(), page.input_value("#edit-title"),
                page.input_value("#edit-subtitle"))

    (compte, titre, sous_titre), _, _ = _jouer(geste, REEL)
    assert (compte, titre, sous_titre) == (0, COVER["title"], COVER["subtitle"])


@chromium_absent
def test_enregistrer_envoie_les_slides_et_rafraichit_l_apercu():
    def geste(page):
        _ecrire_et_enregistrer(page)
        return page.input_value("#edit-headline"), page.text_content("#etat-accroche")

    (valeur, etat), ecritures, covers = _jouer(geste)
    assert valeur == NOUVEAU
    assert "✓" in etat
    assert ecritures == [{"slides": [{**COVER, "headline": NOUVEAU}, POST["slides"][1]]}]
    assert covers == ["/posts/7/covers"]


@chromium_absent
def test_un_debordement_se_voit_sous_le_champ_sans_perdre_la_saisie():
    def geste(page):
        _ecrire_et_enregistrer(page)
        page.wait_for_selector("#accroche-deborde")
        return page.input_value("#edit-headline"), page.text_content("#accroche-deborde")

    (valeur, alerte), _, _ = _jouer(geste, overflow=(False, True))
    assert valeur == NOUVEAU
    assert "déborde" in alerte


@chromium_absent
def test_un_refus_dit_sa_raison_a_cote_du_champ_et_garde_la_saisie():
    def geste(page):
        _ecrire_et_enregistrer(page)
        return page.input_value("#edit-headline"), page.text_content("#etat-accroche"), \
            page.is_visible("#accroche-non-enregistree")

    (valeur, etat, signale), ecritures, covers = _jouer(geste, code=409)
    assert valeur == NOUVEAU
    assert REFUS in etat
    assert signale
    assert len(ecritures) == 1
    assert covers == []


@chromium_absent
def test_un_apercu_en_panne_le_dit_et_garde_l_ecriture():
    def geste(page):
        _ecrire_et_enregistrer(page)
        page.wait_for_function("document.querySelector('#etat-accroche').textContent.includes('aperçu')")
        return page.input_value("#edit-headline"), page.text_content("#etat-accroche")

    (valeur, etat), _, _ = _jouer(geste, covers_ok=False)
    assert valeur == NOUVEAU
    assert "aperçu" in etat


@chromium_absent
def test_rien_de_change_rien_a_envoyer():
    """Un `slides` envoye rouvre le gel, meme identique : le bouton ne l'offre pas."""
    def geste(page):
        return page.is_disabled("#btn-accroche")

    desactive, ecritures, _ = _jouer(geste)
    assert desactive
    assert ecritures == []


@chromium_absent
def test_enregistrer_la_legende_ne_rouvre_pas_le_gel_pour_une_accroche_en_cours():
    def geste(page):
        page.fill("#edit-headline", NOUVEAU)
        page.fill("#edit-caption", "Nouvelle legende")
        page.click("#btn-save")
        page.wait_for_function("document.querySelector('#etat-geste').textContent.includes('✓')")
        return page.input_value("#edit-headline")

    valeur, ecritures, _ = _jouer(geste)
    assert valeur == NOUVEAU, "la saisie survit au re-rendu de la legende"
    assert "slides" not in ecritures[0]


TAMPON = "if (aReinjecterAccroche) {"


@chromium_absent
@pytest.mark.parametrize("mute", [False, True])
def test_la_saisie_survit_a_un_re_rendu_preuve_par_mutation(mute):
    assert TAMPON in PAGE
    html = PAGE.replace(TAMPON, "if (false) {") if mute else PAGE

    def geste(page):
        page.fill("#edit-headline", NOUVEAU)
        page.evaluate("([p, a]) => renderPostDetail(p, a, null)", [POST, ARTICLE])
        return page.input_value("#edit-headline")

    valeur, _, _ = _jouer(geste, html=html)
    assert valeur == (COVER["headline"] if mute else NOUVEAU)


@chromium_absent
def test_fermer_avec_une_accroche_en_cours_demande_confirmation():
    def geste(page):
        vus = []
        page.on("dialog", lambda d: (vus.append(d.message), d.dismiss()))
        page.fill("#edit-headline", NOUVEAU)
        page.click(".close-btn")
        return vus, page.input_value("#edit-headline")

    (vus, valeur), _, _ = _jouer(geste)
    assert vus and "accroche" in vus[0].lower()
    assert valeur == NOUVEAU
