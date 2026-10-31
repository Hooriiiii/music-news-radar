"""« Remettre dans la file » quand le post est peut-etre en ligne.

Le serveur refuse deja sans confirmation (tests/api/test_articles_reintegrer.py).
Ici, ce que voit l'utilisateur, sur Chromium reel : la carte lui dit de verifier le
compte AVANT d'offrir le geste, et le refus du serveur reste affiche a cote du
bouton. Scenario et raison : docs/reference/publication.md, « Exclusion ».
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html"
PAGE = DASHBOARD.read_text(encoding="utf-8")


def _chromium_playwright_present() -> bool:
    caches = [
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    return any(c.exists() and any(c.glob("chromium-*")) for c in caches)


chromium_absent = pytest.mark.skipif(
    not _chromium_playwright_present(),
    reason="Chromium Playwright absent (CI) -- le rendu reel n'est pas testable ici",
)

ARTICLE = {
    "id": 42, "source_id": 1, "url": "https://exemple.test/a", "title": "Une actu", "summary": "",
    "published_at": "2026-10-01T12:00:00Z", "relevance_score": 80, "hotness_score": 20,
    "status": "new", "media": [], "visuels_interdits": False,
}
A_VERIFIER = {
    **ARTICLE,
    "file_exclu_raison": "publication interrompue avant confirmation",
    "exclusion_a_verifier": True,
}
SAUTE = {**ARTICLE, "file_exclu_raison": "saute depuis le dashboard", "exclusion_a_verifier": False}
REFUS = "Publication peut-être encore en cours"


def _serveur(html: str, article: dict, code_post: int, journal: list):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            chemin = self.path.split("?")[0]
            if chemin == "/":
                self._repondre(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif chemin == "/articles":
                self._repondre(200, json.dumps([article]).encode(), "application/json")
            elif chemin in ("/sources", "/posts", "/relecture"):
                self._repondre(200, b"[]", "application/json")
            else:
                self._repondre(404, b"{}", "application/json")

        def do_POST(self):  # noqa: N802
            taille = int(self.headers.get("Content-Length") or 0)
            journal.append((self.path, json.loads(self.rfile.read(taille)) if taille else None))
            corps = {"detail": REFUS} if code_post != 200 else article
            self._repondre(code_post, json.dumps(corps).encode(), "application/json")

        def _repondre(self, code: int, corps: bytes, type_contenu: str):
            self.send_response(code)
            self.send_header("Content-Type", type_contenu)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        def log_message(self, *args):  # silence
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    return serveur


def _jouer(article: dict, geste, code_post: int = 200, html: str = PAGE):
    """Ouvre la grille sur cette carte, joue `geste(page)`, rend (retour, journal, dialogues)."""
    from playwright.sync_api import sync_playwright

    journal: list = []
    dialogues: list = []
    serveur = _serveur(html, article, code_post, journal)
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.on("dialog", lambda d: (dialogues.append(d.message), d.dismiss()))
            page.goto(f"http://127.0.0.1:{serveur.server_address[1]}/")
            page.wait_for_selector(".reintegrer", state="attached")
            retour = geste(page)
            navigateur.close()
        return retour, journal, dialogues
    finally:
        serveur.shutdown()


def _ouvrir(page):
    page.click(".reintegrer")
    page.wait_for_timeout(200)
    return {
        "zone": page.is_visible(".verif-publication"),
        "texte": page.text_content(".verif-publication"),
        "bouton": page.is_visible(".pas-parti"),
    }


@chromium_absent
def test_le_premier_clic_n_envoie_rien_et_dit_de_verifier_le_compte():
    retour, journal, dialogues = _jouer(A_VERIFIER, _ouvrir)

    assert journal == []
    assert retour["zone"] and retour["bouton"]
    assert "Le post est peut-être en ligne. Vérifie le compte" in retour["texte"]
    assert "Vérifié, il n'est pas parti" in retour["texte"]
    assert dialogues == []


@chromium_absent
def test_la_verification_envoie_la_confirmation():
    def geste(page):
        _ouvrir(page)
        page.click(".pas-parti")
        page.wait_for_timeout(300)

    _, journal, _ = _jouer(A_VERIFIER, geste)

    assert journal == [("/articles/42/reintegrer", {"confirmation": "PAS_PARTI"})]


@chromium_absent
def test_le_refus_du_serveur_s_affiche_a_cote_du_bouton():
    def geste(page):
        _ouvrir(page)
        page.click(".pas-parti")
        page.wait_for_timeout(300)
        return {
            "refus": page.text_content(".refus-reintegrer"),
            "refus_visible": page.is_visible(".refus-reintegrer"),
            "rejouable": page.is_enabled(".pas-parti"),
        }

    retour, journal, dialogues = _jouer(A_VERIFIER, geste, code_post=409)

    assert len(journal) == 1
    assert REFUS in retour["refus"] and retour["refus_visible"]
    assert retour["rejouable"], "un refus ne condamne pas le geste"
    assert dialogues == []


@chromium_absent
def test_une_exclusion_ordinaire_reste_en_un_clic():
    def geste(page):
        page.click(".reintegrer")
        page.wait_for_timeout(300)
        return page.locator(".verif-publication").count()

    zones, journal, _ = _jouer(SAUTE, geste)

    assert zones == 0
    assert journal == [("/articles/42/reintegrer", None)]


@chromium_absent
def test_preuve_par_mutation_sans_le_champ_de_l_api_le_clic_envoie_tout_de_suite():
    """Ce fichier voit bien la propriete : la page qui ignore `exclusion_a_verifier`
    retombe sur l'envoi en un clic, et le test du premier clic le verrait."""
    mutee = PAGE.replace("a.exclusion_a_verifier ?", "false ?")
    assert mutee != PAGE

    def geste(page):
        page.click(".reintegrer")
        page.wait_for_timeout(300)

    _, journal, _ = _jouer(A_VERIFIER, geste, html=mutee)

    assert journal == [("/articles/42/reintegrer", None)]
