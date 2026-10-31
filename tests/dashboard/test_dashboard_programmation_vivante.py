"""La Programmation dit ce qu'elle a lu, et quand (2026-10-31).

Sur Chromium reel, horloge de page controlee : une liste figee depuis le matin faisait
retirer une autre actu que celle affichee. Le serveur refuse de son cote
(tests/schedule/test_verification_humaine.py).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.dashboard.test_dashboard_verification_humaine import POST, _article, _creneau, _depart, chromium_absent

PAGE = (Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")
CHANGE = "L'occupant de ce créneau a changé depuis l'affichage : rien n'a été changé, recharge le choix."


class Monde:
    """Un faux serveur dont on change l'etat en cours de test."""

    def __init__(self, creneaux=(), departs=()):
        self.creneaux = list(creneaux)
        self.departs = list(departs)
        self.ecritures: list = []
        self.lectures_schedule = 0
        self.panne = False
        self.code = 200
        self.detail = "refus"


def _jouer(monde, geste):
    from playwright.sync_api import sync_playwright

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            chemin = self.path.split("?")[0]
            if chemin == "/":
                return self._repondre(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            if chemin == "/schedule":
                monde.lectures_schedule += 1
                if monde.panne:
                    return self._repondre(503, b'{"detail":"base injoignable"}', "application/json")
            donnees = {
                "/articles": [], "/schedule": monde.creneaux, "/relecture": monde.departs, "/sources": [],
                "/posts": [], "/posts/167": POST, "/articles/42": _article(),
            }
            if chemin in donnees:
                return self._repondre(200, json.dumps(donnees[chemin]).encode(), "application/json")
            return self._repondre(404, b"{}", "application/json")

        def do_PATCH(self):  # noqa: N802
            taille = int(self.headers.get("Content-Length") or 0)
            monde.ecritures.append((self.path, json.loads(self.rfile.read(taille))))
            corps = {"detail": monde.detail} if monde.code != 200 else {}
            self._repondre(monde.code, json.dumps(corps).encode(), "application/json")

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
            page = navigateur.new_page()
            page.clock.install()
            page.on("dialog", lambda d: d.accept())
            page.goto(f"http://127.0.0.1:{serveur.server_address[1]}/#programmation")
            page.wait_for_function("typeof loadSchedulePanel === 'function'")
            page.clock.run_for(100)
            page.wait_for_selector(".slot-actions, #empty", state="attached")
            page.wait_for_timeout(200)
            retour = geste(page)
            navigateur.close()
        return retour
    finally:
        serveur.shutdown()


def _une_minute(page):
    page.clock.run_for(60_000)
    page.wait_for_timeout(300)


def _occupant_b():
    return _creneau(article_id=43, article_title="Une autre actu", post_id=168)


# --- Ce que les gestes envoient ----------------------------------------------------------


@chromium_absent
def test_le_retrait_de_la_programmation_envoie_ce_qui_est_affiche():
    monde = Monde([_creneau()])
    _jouer(monde, lambda page: (page.click(".ne-pas-publier"), page.wait_for_timeout(200)))
    assert monde.ecritures == [("/schedule/7", {"sauter": True, "occupant_attendu": 42, "post_attendu": 167})]


@chromium_absent
def test_le_retrait_d_a_relire_envoie_ce_qui_est_affiche():
    monde = Monde(departs=[_depart(article_id=42)])

    def geste(page):
        page.click('[data-panneau="posts"]')
        page.wait_for_selector(".relecture-row")
        page.wait_for_timeout(100)
        page.click(".relecture-row .ne-pas-publier")
        page.wait_for_timeout(200)

    _jouer(monde, geste)
    assert monde.ecritures == [("/schedule/7", {"sauter": True, "occupant_attendu": 42, "post_attendu": 167})]


@chromium_absent
@pytest.mark.parametrize("selecteur, corps", [
    (".depart-actions .ne-pas-publier", {"sauter": True}),
    (".depart-actions .desepingler", {"epingle": False}),
])
def test_la_fiche_envoie_ce_qui_est_affiche(selecteur, corps):
    monde = Monde(departs=[_depart("programme", article_id=42, epingle=True)])

    def geste(page):
        page.evaluate("ouvrirPost(167)")
        page.wait_for_selector("#btn-save")
        page.wait_for_timeout(100)
        page.click(selecteur)
        page.wait_for_timeout(200)

    _jouer(monde, geste)
    assert monde.ecritures == [("/schedule/7", {**corps, "occupant_attendu": 42, "post_attendu": 167})]


@chromium_absent
def test_epingler_envoie_ce_qui_est_affiche():
    monde = Monde([_creneau()])
    _jouer(monde, lambda page: (page.click('.slot-actions button:has-text("Épingler")'), page.wait_for_timeout(200)))
    assert monde.ecritures == [("/schedule/7", {"epingle": True, "occupant_attendu": 42, "post_attendu": 167})]


# --- Le 409 ------------------------------------------------------------------------------


@chromium_absent
def test_un_409_recharge_et_dit_ce_qui_a_change():
    monde = Monde([_creneau()])
    monde.code, monde.detail = 409, CHANGE

    def geste(page):
        monde.creneaux[:] = [_occupant_b()]
        page.click(".ne-pas-publier")
        page.wait_for_timeout(300)
        return page.text_content("#retrait-programmation"), page.text_content("#schedule-list")

    resultat, liste = _jouer(monde, geste)
    assert "Une actu" in resultat and "Une autre actu" in resultat and "ne partira pas" not in resultat
    assert "Une autre actu" in liste


@chromium_absent
def test_un_409_sur_l_epingle_recharge_et_dit_ce_qui_a_change():
    monde = Monde([_creneau()])
    monde.code, monde.detail = 409, CHANGE

    def geste(page):
        monde.creneaux[:] = [_occupant_b()]
        page.click('.slot-actions button:has-text("Épingler")')
        page.wait_for_timeout(300)
        return page.text_content("#retrait-programmation")

    assert "Une autre actu" in _jouer(monde, geste)


# --- Le rechargement ---------------------------------------------------------------------


@chromium_absent
def test_la_liste_se_recharge_toutes_les_60_secondes_et_se_date():
    monde = Monde([_creneau()])

    def geste(page):
        avant = monde.lectures_schedule
        lue = page.text_content("#schedule-lue")
        monde.creneaux[:] = [_occupant_b()]
        _une_minute(page)
        return avant, lue, page.text_content("#schedule-list")

    avant, lue, liste = _jouer(monde, geste)
    assert monde.lectures_schedule == avant + 1
    assert "liste lue à" in lue
    assert "Une autre actu" in liste


@chromium_absent
def test_le_retour_sur_l_onglet_recharge():
    monde = Monde([_creneau()])

    def geste(page):
        avant = monde.lectures_schedule
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_timeout(300)
        return avant

    avant = _jouer(monde, geste)
    assert monde.lectures_schedule == avant + 1


@chromium_absent
def test_un_autre_panneau_ne_recharge_pas_la_programmation():
    monde = Monde([_creneau()])

    def geste(page):
        page.click('[data-panneau="grille"]')
        page.wait_for_timeout(100)
        avant = monde.lectures_schedule
        _une_minute(page)
        return avant

    avant = _jouer(monde, geste)
    assert monde.lectures_schedule == avant


@chromium_absent
def test_une_lecture_en_echec_le_dit_et_garde_la_liste():
    monde = Monde([_creneau()])

    def geste(page):
        monde.panne = True
        _une_minute(page)
        return page.text_content("#schedule-lue"), page.text_content("#schedule-list")

    lue, liste = _jouer(monde, geste)
    assert "⚠" in lue and "503" in lue and "liste lue à" in lue
    assert "Une actu" in liste


@chromium_absent
def test_un_selecteur_ouvert_suspend_le_rechargement():
    monde = Monde([_creneau()])

    def geste(page):
        page.evaluate("""
            const z = document.querySelector('#schedule-list .prog-zone');
            z.appendChild(nouvelElement('div', 'prog-selecteur', 'ouvert'));
        """)
        avant = monde.lectures_schedule
        _une_minute(page)
        pendant = monde.lectures_schedule
        page.evaluate("document.querySelector('#schedule-list .prog-selecteur').remove()")
        _une_minute(page)
        return avant, pendant

    avant, pendant = _jouer(monde, geste)
    assert pendant == avant
    assert monde.lectures_schedule == avant + 1


@chromium_absent
def test_un_glissement_reporte_le_rechargement():
    monde = Monde([_creneau()])

    def geste(page):
        page.evaluate("glissement = { c: {}, faux: true }")
        avant = monde.lectures_schedule
        _une_minute(page)
        return avant, page.evaluate("rechargementDiffere")

    avant, differe = _jouer(monde, geste)
    assert monde.lectures_schedule == avant
    assert differe is True


@chromium_absent
def test_le_rechargement_garde_le_focus_de_la_ligne():
    monde = Monde([_creneau()])

    def geste(page):
        page.focus(".ne-pas-publier")
        _une_minute(page)
        return page.evaluate("document.activeElement.className")

    assert "ne-pas-publier" in _jouer(monde, geste)


@chromium_absent
def test_le_rechargement_ne_perd_ni_la_frappe_ni_le_focus_de_la_fiche():
    monde = Monde([_creneau()], [_depart("programme", article_id=42)])

    def geste(page):
        page.evaluate("ouvrirPost(167)")
        page.wait_for_selector("#btn-save")
        page.wait_for_timeout(100)
        page.fill("#edit-headline", "Je suis en train d'ecrire")
        avant = monde.lectures_schedule
        _une_minute(page)
        return avant, page.input_value("#edit-headline"), page.evaluate("document.activeElement.id")

    avant, texte, focus = _jouer(monde, geste)
    assert monde.lectures_schedule == avant + 1
    assert texte == "Je suis en train d'ecrire" and focus == "edit-headline"


# --- Le marqueur de relecture ne crie pas au loup ---------------------------------------


@chromium_absent
def test_le_marqueur_seul_ne_s_affiche_pas_en_alerte():
    """Une correction a T-20 h le pose : un ⚠ rouge pendant vingt heures sur un post sain
    apprendrait a ignorer l'alerte."""
    monde = Monde([_creneau(erreur="rouvert pour relecture")])
    assert _jouer(monde, lambda page: page.is_hidden(".slot-alerte"))


@chromium_absent
def test_une_vraie_erreur_a_cote_du_marqueur_s_affiche():
    monde = Monde([_creneau(erreur="rouvert pour relecture — gel differe : yt-dlp en pause")])
    assert "yt-dlp" in _jouer(monde, lambda page: page.text_content(".slot-alerte"))
