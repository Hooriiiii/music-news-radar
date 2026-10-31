"""La console publie seule avec verification humaine (decision du 2026-10-30).

Sur Chromium reel : les gestes herites du travail manuel ont disparu, ceux qui
restent disent un refus a cote du bouton, et « Ne pas publier » nomme ce qu'il retire.
Le serveur refuse de son cote (tests/api/test_console_verification.py).
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

REFUS = "Publication en cours : le conteneur est cree chez Meta"


def _article(statut="new", **champs):
    return {"id": 42, "source_id": 1, "url": "https://exemple.test/a", "title": "Une actu", "summary": "",
            "published_at": "2026-10-01T12:00:00Z", "relevance_score": 80, "hotness_score": 20,
            "status": statut, "media": [], "visuels_interdits": False, **champs}


def _creneau(etat="programme", **champs):
    return {"id": 7, "publier_a": "2099-10-30T14:00:00+00:00", "format": "carrousel", "etat": etat,
            "epingle": False, "article_id": 42, "article_title": "Une actu", "article_url": "https://exemple.test/a",
            "relevance": 80, "post_id": 167, "ig_media_id": None, "erreur": None, **champs}


def _depart(etat="gele", **champs):
    return {"creneau_id": 7, "publier_a": "2099-10-30T14:00:00+00:00", "format": "carrousel", "etat": etat,
            "post_id": 167, "accroche": "Accroche", "article_title": "Une actu",
            "article_url": "https://exemple.test/a", "legende_publiee": "Legende", "legende_longueur": 7,
            "legende_maximum": 2150, "hashtags_publies": [], "hashtags_maximum": 30, "credit": None,
            "fond": None, "fond_origine": None, "elements_figes": 1, "elements": [], "candidats": [],
            "liste_arretee": False, "editable": etat != "echec",
            "raison_non_editable": "publication en cours" if etat == "echec" else None,
            "reouvrable": True, "raison_non_reouvrable": None, "minutes_restantes": 90, **champs}


POST = {"id": 167, "article_id": 42, "article_title": "Une actu", "article_url": "https://exemple.test/a",
        "caption": "Legende", "hashtags": [], "slides": [{"kind": "cover", "headline": "Accroche"}],
        "assets": [], "variants": {}, "template": "kinda_post", "status": "draft", "credit": None,
        "created_at": "2026-10-30T10:00:00Z", "published_at": None, "render_report": None}


def _jouer(geste, *, articles=(), creneaux=(), departs=(), code=200, generation=200, confirme=False, html=PAGE):
    """Sert la page sur un faux serveur, joue `geste(page)`, rend (retour, ecritures, dialogues)."""
    from playwright.sync_api import sync_playwright

    ecritures: list = []
    dialogues: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            chemin = self.path.split("?")[0]
            if chemin == "/":
                return self._repondre(200, html.encode("utf-8"), "text/html; charset=utf-8")
            donnees = {
                "/articles": list(articles), "/schedule": list(creneaux), "/relecture": list(departs),
                "/sources": [], "/posts": [], "/posts/167": POST, "/articles/42": _article(),
            }
            if chemin in donnees:
                return self._repondre(200, json.dumps(donnees[chemin]).encode(), "application/json")
            return self._repondre(404, b"{}", "application/json")

        def _ecrire(self):
            taille = int(self.headers.get("Content-Length") or 0)
            ecritures.append((self.command, self.path, json.loads(self.rfile.read(taille)) if taille else None))
            statut = generation if self.path.endswith("/posts") else code
            corps = {"detail": REFUS} if statut != 200 else {}
            self._repondre(statut, json.dumps(corps).encode(), "application/json")

        do_PATCH = do_POST = do_DELETE = _ecrire  # noqa: N815

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

    def repondre(dialogue):
        dialogues.append(dialogue.message)
        dialogue.accept() if confirme else dialogue.dismiss()

    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.on("dialog", repondre)
            page.goto(f"http://127.0.0.1:{serveur.server_address[1]}/")
            page.wait_for_function("typeof loadSchedulePanel === 'function'")
            retour = geste(page)
            navigateur.close()
        return retour, ecritures, dialogues
    finally:
        serveur.shutdown()


def _programmation(page):
    page.click('[data-panneau="schedule"]')
    page.wait_for_selector(".slot-actions, #empty", state="attached")
    page.wait_for_timeout(150)


def _a_relire(page):
    page.click('[data-panneau="posts"]')
    page.wait_for_selector(".relecture-row, .media-empty", state="attached")
    page.wait_for_timeout(150)


def _fiche(page):
    page.evaluate("ouvrirPost(167)")
    page.wait_for_selector("#btn-save")
    page.wait_for_timeout(150)


# --- 1. Ce qui a disparu --------------------------------------------------------


@pytest.mark.parametrize("trace", [
    "btn-approve", "btn-publish", "Valider le statut", "Marquer comme publié", "Générer le post",
    '<option value="approved">', 'data-status="used"', "📸 Posté</button>", "🚫 Sauter",
])
def test_les_gestes_hérités_ont_disparu_de_la_page(trace):
    assert trace not in PAGE


@chromium_absent
def test_le_filtre_garde_brouillon_et_publie():
    options, _, _ = _jouer(
        lambda page: page.eval_on_selector_all("#post-status option", "os => os.map(o => o.value)"))
    assert options == ["", "draft", "published"]


# --- 2. Les gestes de carte -----------------------------------------------------


@chromium_absent
@pytest.mark.parametrize("statut, vu, a_traiter", [
    ("new", 1, 0),
    ("reviewed", 0, 1),
    ("used", 0, 0),
])
def test_un_geste_n_apparait_que_s_il_a_un_effet(statut, vu, a_traiter):
    def geste(page):
        page.wait_for_selector(".card")
        return (page.locator('[data-status="reviewed"]').count(), page.locator('[data-status="new"]').count())

    retour, _, _ = _jouer(geste, articles=[_article(statut)])

    assert retour == (vu, a_traiter)


@chromium_absent
def test_un_refus_de_statut_s_affiche_a_cote_du_bouton():
    def geste(page):
        page.wait_for_selector(".card")
        page.click('[data-status="reviewed"]')
        page.wait_for_timeout(250)
        return page.text_content(".refus-statut"), page.is_enabled('[data-status="reviewed"]')

    (refus, rejouable), ecritures, dialogues = _jouer(geste, articles=[_article()], code=409)

    assert ecritures == [("PATCH", "/articles/42", {"status": "reviewed"})]
    assert REFUS in refus and rejouable
    assert dialogues == []


@chromium_absent
def test_preuve_par_mutation_sans_lecture_de_la_reponse_le_refus_est_muet():
    mutee = PAGE.replace("const reponse = await fetch(`/articles/${a.id}`, {", "await fetch(`/articles/${a.id}`, {")
    assert mutee != PAGE

    def geste(page):
        page.wait_for_selector(".card")
        page.click('[data-status="reviewed"]')
        page.wait_for_timeout(250)
        return page.text_content(".refus-statut") or ""

    refus, _, _ = _jouer(geste, articles=[_article()], code=409, html=mutee)

    assert REFUS not in refus


# --- 3. Supprimer ---------------------------------------------------------------


@chromium_absent
@pytest.mark.parametrize("departs, visible", [([], 1), ([_depart("programme")], 0), ([_depart("gele")], 0)])
def test_la_fiche_masque_supprimer_tant_que_le_post_est_au_programme(departs, visible):
    def geste(page):
        _fiche(page)
        return page.locator("#btn-delete").count()

    retour, _, _ = _jouer(geste, departs=departs)

    assert retour == visible


@chromium_absent
def test_preuve_par_mutation_le_masque_lit_bien_le_depart():
    mutee = PAGE.replace('post.status === "draft" && departsParPost && !depart', 'post.status === "draft"')
    assert mutee != PAGE

    def geste(page):
        _fiche(page)
        return page.locator("#btn-delete").count()

    retour, _, _ = _jouer(geste, departs=[_depart("gele")], html=mutee)

    assert retour == 1


# --- 4. Ne pas publier ----------------------------------------------------------


@chromium_absent
@pytest.mark.parametrize("creneau, present", [
    (_creneau("programme"), 1),
    (_creneau("gele"), 1),
    (_creneau("echec", erreur="publication interrompue avant confirmation"), 0),
    (_creneau("libre", article_id=None, article_title=None, post_id=None), 0),
    (_creneau("publie"), 0),
])
def test_le_bouton_de_la_programmation(creneau, present):
    def geste(page):
        _programmation(page)
        return page.locator(".ne-pas-publier").count()

    retour, _, _ = _jouer(geste, creneaux=[creneau])

    assert retour == present


@chromium_absent
def test_la_confirmation_nomme_l_article_l_heure_et_le_retour():
    def geste(page):
        _programmation(page)
        attendu = page.evaluate("fmtDate('2099-10-30T14:00:00+00:00')")
        page.click(".ne-pas-publier")
        page.wait_for_timeout(150)
        return attendu

    heure, ecritures, dialogues = _jouer(geste, creneaux=[_creneau("gele")], confirme=False)

    assert ecritures == [], "annuler n'envoie rien"
    assert "Une actu" in dialogues[0] and heure in dialogues[0]
    assert "Remettre dans la file" in dialogues[0]


@chromium_absent
def test_un_article_purge_ne_promet_pas_un_retour_impossible():
    def geste(page):
        _programmation(page)
        page.click(".ne-pas-publier")

    _, _, dialogues = _jouer(geste, creneaux=[_creneau("programme", article_id=None, article_title=None)])

    assert "Remettre dans la file" not in dialogues[0]
    assert "post 167" in dialogues[0]


@chromium_absent
def test_le_retrait_part_et_dit_son_resultat():
    def geste(page):
        _programmation(page)
        page.click(".ne-pas-publier")
        page.wait_for_timeout(300)
        return page.text_content("#retrait-programmation")

    resultat, ecritures, _ = _jouer(geste, creneaux=[_creneau("gele")], confirme=True)

    assert ecritures == [("PATCH", "/schedule/7", {"sauter": True})]
    assert "Une actu" in resultat and "ne partira pas" in resultat


@chromium_absent
def test_le_refus_du_retrait_s_affiche_a_cote_du_bouton():
    def geste(page):
        _programmation(page)
        page.click(".ne-pas-publier")
        page.wait_for_timeout(300)
        return page.text_content(".refus-retrait"), page.is_enabled(".ne-pas-publier")

    (refus, rejouable), _, dialogues = _jouer(geste, creneaux=[_creneau("gele")], code=409, confirme=True)

    assert REFUS in refus and rejouable
    assert len(dialogues) == 1, "la confirmation, et aucune alerte a chasser"


@chromium_absent
def test_preuve_par_mutation_sans_la_lecture_du_refus_le_retrait_dit_reussi():
    mutee = PAGE.replace("if (!reponse.ok) throw new Error(await raisonDuRefus(reponse));\n  return \"retrait\";",
                         'return "retrait";')
    assert mutee != PAGE

    def geste(page):
        _programmation(page)
        page.click(".ne-pas-publier")
        page.wait_for_timeout(300)
        return page.text_content("#retrait-programmation")

    resultat, _, _ = _jouer(geste, creneaux=[_creneau("gele")], code=409, confirme=True, html=mutee)

    assert "ne partira pas" in resultat


@chromium_absent
@pytest.mark.parametrize("depart, present", [
    (_depart("programme"), 1), (_depart("gele"), 1), (_depart("echec"), 0),
])
def test_le_bouton_de_a_relire(depart, present):
    def geste(page):
        _a_relire(page)
        return page.locator(".relecture-row .ne-pas-publier").count()

    retour, _, _ = _jouer(geste, departs=[depart])

    assert retour == present


@chromium_absent
def test_le_bouton_de_a_relire_n_ouvre_pas_la_fiche():
    def geste(page):
        _a_relire(page)
        page.click(".relecture-row .ne-pas-publier")
        page.wait_for_timeout(300)
        return page.locator("#btn-save").count(), page.text_content("#retrait-relecture")

    (fiches, resultat), ecritures, dialogues = _jouer(geste, departs=[_depart("gele")], confirme=True)

    assert fiches == 0
    assert ecritures == [("PATCH", "/schedule/7", {"sauter": True})]
    assert "Une actu" in dialogues[0] and "ne partira pas" in resultat


@chromium_absent
@pytest.mark.parametrize("depart, present", [
    (_depart("programme"), 1), (_depart("gele"), 1), (_depart("echec"), 0),
])
def test_le_bouton_de_la_fiche(depart, present):
    def geste(page):
        _fiche(page)
        return page.locator(".depart-actions .ne-pas-publier").count()

    retour, _, _ = _jouer(geste, departs=[depart])

    assert retour == present


@chromium_absent
def test_la_fiche_dit_le_resultat_et_son_refus():
    def geste(page):
        _fiche(page)
        page.click(".depart-actions .ne-pas-publier")
        page.wait_for_timeout(300)
        return page.text_content("#etat-geste"), page.text_content(".refus-retrait")

    (etat, _), toutes, _ = _jouer(geste, departs=[_depart("gele")], confirme=True)
    ecritures = [e for e in toutes if e[1].startswith("/schedule")]
    assert ecritures == [("PATCH", "/schedule/7", {"sauter": True})]
    assert "ne partira pas" in etat

    (_, refus), _, _ = _jouer(geste, departs=[_depart("gele")], confirme=True, code=409)
    assert REFUS in refus
