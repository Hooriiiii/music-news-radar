"""Les quatre defauts qui rompaient la confiance, verrouilles.

Deux etages, pour deux raisons distinctes :

- un etage NAVIGATEUR (Playwright, le Chromium deja requis par le rendu des
  covers), parce que le defaut ① -- une legende tapee detruite par un re-rendu --
  ne se prouve QUE dans un vrai DOM : un test qui ne rejoue pas le re-rendu
  complet passerait au vert sans rien garantir. Il porte donc aussi sa PREUVE PAR
  MUTATION : la meme page, privee de la reinjection, doit perdre le texte ;
- un etage STRUCTUREL (lecture du fichier), qui tient en CI ou Chromium est
  absent.
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


# --- Un serveur minimal, parce que la page fait des `fetch` en chemin relatif :
# servie depuis `about:blank` ou un `data:`, aucune de ses requetes ne part.
def _demarrer_serveur(html: str, etat: dict):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (nom impose par http.server)
            chemin = self.path.split("?")[0]
            if chemin == "/":
                self._repondre(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif chemin == "/articles":
                self._repondre(
                    etat.get("articles_statut", 200),
                    json.dumps(etat.get("articles", [])).encode(),
                    "application/json",
                )
            elif chemin in ("/sources", "/posts"):
                self._repondre(200, b"[]", "application/json")
            elif chemin == "/supervision":
                self._repondre(
                    200,
                    json.dumps({"niveau": "inconnu", "releve_le": None, "voyants": []}).encode(),
                    "application/json",
                )
            else:
                self._repondre(404, b"{}", "application/json")

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


POST = {
    "id": 7,
    "article_id": 42,
    "article_title": "Charlotte de Witte annonce un EP",
    "status": "draft",
    "template": "kinda_post",
    "caption": "Legende du serveur.",
    "hashtags": ["#techno"],
    "slides": [{"kind": "cover", "headline": "**Charlotte** revient", "title": "A", "subtitle": "B"}],
    "variants": {"variantes": [{"headline": "**Charlotte** revient"}], "retenue": 0},
    "assets": None,
}
ARTICLE = {"id": 42, "media": [], "visuels_interdits": False}

TAPE = "Une legende tapee a la main, qui ne doit pas disparaitre."


def _legende_apres_un_re_rendu(html: str) -> str:
    """Tape une legende, puis rejoue le geste editorial : un re-rendu complet de
    la fiche depuis la reponse du serveur (ce que font les six gestes)."""
    from playwright.sync_api import sync_playwright

    etat: dict = {}
    serveur = _demarrer_serveur(html, etat)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("typeof renderPostDetail === 'function'")
            page.evaluate(
                "([post, article]) => renderPostDetail(post, article, null)", [POST, ARTICLE]
            )
            page.fill("#edit-caption", TAPE)
            avertissement_avant = page.is_visible("#texte-non-enregistre")
            # Le re-rendu : exactement l'appel que fait le choix d'une variante.
            page.evaluate(
                "([post, article]) => renderPostDetail(post, article, null)", [POST, ARTICLE]
            )
            legende = page.input_value("#edit-caption")
            navigateur.close()
        assert avertissement_avant, "l'avertissement « non enregistre » doit etre visible"
        return legende
    finally:
        serveur.shutdown()


@chromium_absent
def test_une_legende_tapee_survit_a_un_re_rendu_complet_de_la_fiche():
    assert _legende_apres_un_re_rendu(PAGE) == TAPE


@chromium_absent
def test_preuve_par_mutation_sans_la_reinjection_la_legende_est_perdue():
    """Le meme scenario sur une page privee de la SEULE ligne de reinjection.

    Sans ce test, la sentinelle ci-dessus pourrait etre verte pour une mauvaise
    raison (un `fill` qui survit a autre chose que notre correctif) : ici on
    prouve que c'est bien elle qui sauve le texte.
    """
    cible = "const aReinjecter = textesAReinjecter(brouillonTexte, post);"
    assert PAGE.count(cible) == 1, "la mutation ne s'applique plus : revoir ce test"
    mutant = PAGE.replace(cible, "const aReinjecter = null;")
    assert _legende_apres_un_re_rendu(mutant) == "Legende du serveur."


@chromium_absent
def test_la_grille_en_echec_affiche_sa_raison_et_une_relance_qui_marche():
    """Un `/articles` en echec laissait « Chargement… » a l'ecran pour toujours."""
    from playwright.sync_api import sync_playwright

    etat: dict = {"articles_statut": 500}
    serveur = _demarrer_serveur(PAGE, etat)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_selector("#grid .zone-erreur .relance")
            assert "500" in page.text_content("#grid .zone-erreur .pourquoi")
            # La relance recharge vraiment : le serveur guerit, un clic suffit.
            etat["articles_statut"] = 200
            page.click("#grid .zone-erreur .relance")
            page.wait_for_selector("#grid #empty")
            assert "Rien ici" in page.text_content("#grid #empty")
            navigateur.close()
    finally:
        serveur.shutdown()


@chromium_absent
def test_un_sources_en_echec_n_empeche_plus_la_grille_de_se_charger():
    """`load()` etait la DERNIERE instruction d'un bloc sans rattrapage."""
    from playwright.sync_api import sync_playwright

    etat: dict = {}
    # Le serveur ne connait pas `/sources` quand on le lui retire : on simule
    # l'echec en changeant le chemin demande par la page, pas le serveur, pour
    # que l'echec soit bien celui du `fetch("/sources")` du bas de page.
    html = PAGE.replace('await fetch("/sources")', 'await fetch("/sources-absent")')
    assert html != PAGE
    serveur = _demarrer_serveur(html, etat)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_selector("#grid #empty")
            assert "Rien ici" in page.text_content("#grid #empty")
            assert "illisibles" in page.text_content("#source")
            navigateur.close()
    finally:
        serveur.shutdown()


@chromium_absent
def test_un_post_avec_des_visuels_retenus_s_ouvre_vraiment():
    """Defaut PRE-EXISTANT trouve en verifiant ① dans le vrai dashboard : la
    section « Visuels retenus » produisait elle aussi des `.media-choice`, mais
    sans case a cocher. Le `querySelectorAll(".media-choice")` portait sur TOUTE
    la fiche, donc `.media-check.onchange` levait un TypeError au milieu de
    `renderPostDetail` -- avant `detailEl.hidden = false` et avant le branchement
    de « Enregistrer ». Tout post portant des `assets` (tous ceux composes depuis
    la migration 0018) ne s'ouvrait donc pas du tout : le clic ne faisait rien.

    La FUSION des trois listes en une seule (2026-10-05) a retire la cause -- il
    n'y a plus qu'une liste de visuels dans la fiche, donc plus deux formes sous
    un meme selecteur. Le test reste : ce qu'il verifie n'est pas le selecteur,
    c'est qu'un post porteur d'`assets` s'OUVRE, sans exception, avec sa liste
    branchee. C'est la propriete qui avait coute 126 posts sur 157.
    """
    from playwright.sync_api import sync_playwright

    post = dict(
        POST,
        assets=[{"origine": "artiste", "compte": "@charli_xcx", "type": "photo",
                 "url": "https://example.com/a.jpg", "publie_le": None, "permalink": None}],
    )
    article = {"id": 42, "media": [{"type": "photo", "url": "https://example.com/m.jpg"}],
               "visuels_interdits": False}
    etat: dict = {}
    serveur = _demarrer_serveur(PAGE, etat)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            fautes: list[str] = []
            page.on("pageerror", lambda e: fautes.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("typeof renderPostDetail === 'function'")
            page.evaluate("([p, a]) => renderPostDetail(p, a, null)", [post, article])
            assert not fautes, fautes
            assert page.is_visible("#post-detail .sheet")
            # Et la LISTE UNIQUE est bien branchee : l'actif du post et le media
            # de l'actu y sont tous deux proposables, et le geste de retrait de
            # la ligne existe.
            assert page.evaluate(
                "document.querySelectorAll('#el-ajouter option').length"
            ) == 3  # l'entete + l'actif + le media de l'actu
            assert page.evaluate(
                "!!document.querySelector('#elements-liste .el-retirer')"
            ) is False  # ce post n'a aucune slide media : rien a retirer
            assert page.evaluate(
                "document.querySelectorAll('#elements-liste .element-ligne').length"
            ) == 1  # la cover, et elle seule
            navigateur.close()
    finally:
        serveur.shutdown()


# --- Etage structurel : ce qui tient meme sans navigateur ------------------


def test_un_seul_point_de_capture_du_texte_et_un_seul_de_lecture():
    """Six gestes re-rendent la fiche : un tampon rempli a la main dans chacun
    serait a refaire au septieme."""
    assert "captionEl.oninput = capturerTextes;" in PAGE
    assert "hashtagsEl.oninput = capturerTextes;" in PAGE
    assert "function textesAReinjecter(brouillon, post)" in PAGE


def test_la_fermeture_de_la_fiche_garde_les_modifications_non_enregistrees():
    assert "const fermerDetail = () => {" in PAGE
    assert "La légende n'est pas enregistrée." in PAGE
    assert 'detailEl.querySelector(".close-btn").onclick = fermerDetail;' in PAGE
    # Et plus aucune fermeture qui vide la fiche sans passer par la garde.
    assert 'onclick = () => { detailEl.hidden = true; detailEl.innerHTML = ""; }' not in PAGE


def test_les_quatre_panneaux_offrent_une_relance_et_pas_seulement_un_message():
    assert PAGE.count("afficherErreur(") >= 5  # la definition plus quatre appels
    for relance in ("load", "loadSchedulePanel", "loadPostsPanel", "loadEventsPanel"):
        assert f", {relance});" in PAGE, relance
    # Aucun panneau ne doit plus poser un message d'echec sans relance.
    assert "Échec du chargement de la file.</div>" not in PAGE
    assert "Échec du chargement des posts.</div>" not in PAGE
    assert "Échec du chargement des événements.</div>" not in PAGE


def test_la_categorie_d_une_source_n_est_plus_une_ecriture_muette():
    bloc = PAGE.split("catSel.onchange", 1)[1].split("row.querySelector(\".toggle\")", 1)[0]
    assert "if (!reponse.ok)" in bloc
    assert "catSel.value = precedente;" in bloc, "un refus doit revenir en arriere"
    assert "Catégorie non enregistrée" in bloc


def test_ne_pas_publier_demande_une_confirmation_qui_nomme_l_article():
    # Le comportement reel, sur Chromium : tests/dashboard/test_dashboard_verification_humaine.py.
    bloc = PAGE.split("function boutonNePasPublier", 1)[1].split("\nasync function ", 1)[0]
    assert "confirm(" in bloc
    assert "${nom}" in bloc and "${heure}" in bloc
    assert "Remettre dans la file" in bloc


def test_la_grille_montre_l_exclusion_et_propose_de_la_lever():
    assert "a.file_exclu_raison" in PAGE
    assert "⛔ hors file" in PAGE
    assert "/reintegrer`, options)" in PAGE
    assert "envoyer(btnReintegrer, { method: \"POST\" })" in PAGE
