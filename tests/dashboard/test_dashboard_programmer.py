"""« Programmer » sur Chromium reel (decision du 2026-10-31).

Le geste sur la carte, sur la ligne de creneau, dans la fiche : sans `prompt`, ni
`confirm`, ni regle recopiee cote navigateur. Le serveur est simule : ses regles sont
verrouillees par tests/api/test_programmer.py.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.dashboard.test_dashboard_verification_humaine import POST, _article, _creneau, _depart, chromium_absent

DASHBOARD = Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html"
PAGE = DASHBOARD.read_text(encoding="utf-8")

REFUS = "Article exclu de la file"
UN = "2099-10-30T14:00:00+00:00"
DEUX = "2099-10-31T14:00:00+00:00"
OCCUPANT = {"article_id": 9, "titre": "L'occupante", "epingle": False, "compose": False, "post_id": None}


def _option(creneau_id=7, publier_a=UN, **champs):
    return {"creneau_id": creneau_id, "publier_a": publier_a, "format": "carrousel", "etat": "libre",
            "occupant": None, "actuel": False, "possible": True, "raison": None, "annonces": [],
            "brouillon": False, "composition_a": "2099-10-29T14:00:00+00:00", "composition_due": False, **champs}


def _lecture(options, **champs):
    prochain = next((o["creneau_id"] for o in options if o["possible"]), None)
    return {"article_id": 42, "titre": "Une actu", "pertinence": 80, "age_heures": 5, "notes": [],
            "raison_article": None, "mode": "reel", "annonce_mode": None,
            "prochain_creneau_id": prochain, "options": options, **champs}


def _actus(candidats, **champs):
    return {"creneau_id": 7, "publier_a": UN, "format": "carrousel", "etat": "programme", "occupant": None,
            "raison_creneau": None, "annonces": [], "mode": "reel", "annonce_mode": None,
            "candidats": candidats, "non_affichees": 0, **champs}


def _candidat(article_id=5, **champs):
    return {"article_id": article_id, "titre": f"Actu {article_id}", "pertinence": 70, "age_heures": 3, "notes": [],
            "actuel": False, "creneau_actuel": None, "possible": True, "raison": None, "brouillon": False,
            "composition_a": "2099-10-29T14:00:00+00:00", "composition_due": False, **champs}


def _jouer(geste, routes=None, *, articles=(), creneaux=(), departs=(), code=200, largeur=1440, html=PAGE):
    from playwright.sync_api import sync_playwright

    ecritures: list = []
    dialogues: list = []
    donnees = {"/articles": list(articles), "/schedule": list(creneaux), "/relecture": list(departs), "/sources": [],
               "/posts": [], "/posts/167": POST, "/articles/42": _article(), **(routes or {})}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            chemin = self.path.split("?")[0]
            if chemin == "/":
                return self._repondre(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if chemin in donnees:
                return self._repondre(200, json.dumps(donnees[chemin]).encode(), "application/json")
            return self._repondre(404, b"{}", "application/json")

        def _ecrire(self):
            taille = int(self.headers.get("Content-Length") or 0)
            ecritures.append((self.command, self.path, json.loads(self.rfile.read(taille)) if taille else None))
            corps = _creneau(post_id=None, composition_a="2099-10-29T14:00:00+00:00") if code == 200 else {"detail": REFUS}
            self._repondre(code, json.dumps(corps).encode(), "application/json")

        do_PATCH = do_POST = _ecrire  # noqa: N815

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
        dialogue.dismiss()

    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page(viewport={"width": largeur, "height": 900})
            page.on("dialog", repondre)
            page.goto(f"http://127.0.0.1:{serveur.server_address[1]}/")
            page.wait_for_function("typeof loadSchedulePanel === 'function'")
            retour = geste(page)
            navigateur.close()
        return retour, ecritures, dialogues
    finally:
        serveur.shutdown()


def _ouvrir_carte(page):
    page.wait_for_selector(".card")
    page.click(".card .programmer")
    page.wait_for_selector(".prog-selecteur .prog-jour, .prog-selecteur .prog-raison, .prog-selecteur .prog-entete")


def _programmation(page):
    page.click('[data-panneau="schedule"]')
    page.wait_for_selector(".slot-actions, #empty", state="attached")
    page.wait_for_timeout(150)


ROUTE = "/articles/42/creneaux-possibles"


# --- 1. Ce qui a disparu ---------------------------------------------------------


@pytest.mark.parametrize("trace", [
    "Générer le post", "genererPost", "gen-post", "✏️ Remplacer", "remplacer-reel", "Identifiant de l'article",
    "raisonPasDeReel", "tpl-post", "tpl-reel", "template-switch", "⬇ Télécharger", "export.zip",
    'method: "POST" });\n    if (!reponse.ok) throw new Error(await raisonDuRefus(reponse));\n    const post',
])
def test_les_gestes_retires_ont_disparu_de_la_page(trace):
    assert trace not in PAGE


# --- 2. La carte du Flux ---------------------------------------------------------


@chromium_absent
def test_programmer_ouvre_le_prochain_creneau_en_premier_puis_les_jours():
    options = [_option(6, possible=False, raison="Créneau gelé ou passé : il ne se modifie plus"),
               _option(7), _option(8, publier_a=DEUX)]

    def geste(page):
        _ouvrir_carte(page)
        return page.eval_on_selector_all(".prog-selecteur > *", "els => els.map(e => e.className)"), \
            page.text_content(".prog-jour")

    (classes, premier_groupe), _, dialogues = _jouer(geste, {ROUTE: _lecture(options)}, articles=[_article()])

    assert premier_groupe == "Prochain créneau compatible"
    assert classes.count("prog-jour") == 3
    assert dialogues == []


@chromium_absent
def test_une_option_impossible_est_desactivee_avec_sa_raison_a_cote():
    raison = "Actu purgée avant sa composition"

    def geste(page):
        _ouvrir_carte(page)
        return page.is_disabled(".prog-option >> nth=1"), page.text_content(".prog-raison")

    (desactivee, texte), _, _ = _jouer(
        geste, {ROUTE: _lecture([_option(7), _option(8, publier_a=DEUX, possible=False, raison=raison)])},
        articles=[_article()])

    assert desactivee and texte == raison


@chromium_absent
def test_un_creneau_libre_se_valide_en_une_etape():
    def geste(page):
        _ouvrir_carte(page)
        page.click(".prog-option")
        page.click(".prog-validation .confirmer")
        page.wait_for_selector(".prog-resultat:not(:empty)")
        return page.text_content(".prog-resultat")

    resultat, ecritures, dialogues = _jouer(geste, {ROUTE: _lecture([_option()])}, articles=[_article()])

    assert ecritures == [("PATCH", "/schedule/7", {"article_id": 42, "occupant_attendu": None})]
    assert resultat.startswith("✓ Programmé") and "composé vers" in resultat
    assert dialogues == []


@chromium_absent
def test_un_occupant_ordinaire_est_nomme_par_la_validation():
    def geste(page):
        _ouvrir_carte(page)
        page.click(".prog-option")
        texte = page.text_content(".prog-validation")
        page.click(".prog-validation .confirmer")
        page.wait_for_selector(".prog-resultat:not(:empty)")
        return texte

    texte, ecritures, _ = _jouer(geste, {ROUTE: _lecture([_option(occupant=OCCUPANT)])}, articles=[_article()])

    assert "« L'occupante » sort de ce créneau" in texte
    assert ecritures == [("PATCH", "/schedule/7", {"article_id": 42, "occupant_attendu": 9})]


@chromium_absent
@pytest.mark.parametrize("champs, mot", [({"epingle": True}, "épinglée"), ({"compose": True, "post_id": 3}, "déjà composée")])
def test_un_occupant_epingle_ou_compose_demande_une_etape_de_plus(champs, mot):
    def geste(page):
        _ouvrir_carte(page)
        page.click(".prog-option")
        page.click(".prog-validation .confirmer")
        page.wait_for_timeout(100)
        return page.text_content(".prog-validation")

    texte, ecritures, _ = _jouer(
        geste, {ROUTE: _lecture([_option(occupant={**OCCUPANT, **champs})])}, articles=[_article()])

    # Le premier clic n'a rien ecrit : la seconde etape nomme ce qui est en jeu.
    assert ecritures == []
    assert mot in texte and "Oui, faire sortir" in texte


@chromium_absent
def test_un_echec_affiche_la_raison_du_serveur_a_cote_du_bouton():
    def geste(page):
        _ouvrir_carte(page)
        page.click(".prog-option")
        page.click(".prog-validation .confirmer")
        page.wait_for_selector(".prog-refus:not(:empty)")
        return page.text_content(".prog-refus"), page.is_enabled(".prog-validation .confirmer")

    (refus, rejouable), _, dialogues = _jouer(geste, {ROUTE: _lecture([_option()])}, articles=[_article()], code=409)

    assert REFUS in refus and rejouable
    assert dialogues == []


@chromium_absent
def test_en_arret_le_selecteur_le_dit_avec_le_texte_du_serveur():
    annonce = "Texte du serveur : rien ne sera composé ni publié."

    def geste(page):
        _ouvrir_carte(page)
        return page.text_content(".prog-mode")

    texte, _, _ = _jouer(geste, {ROUTE: _lecture([_option()], mode="arret", annonce_mode=annonce)}, articles=[_article()])

    assert texte == annonce


@chromium_absent
def test_la_carte_montre_l_etat_reel_et_le_bouton_devient_changer():
    creneau = {"creneau_id": 7, "publier_a": UN, "format": "carrousel", "etat": "programme", "epingle": True,
               "post_id": None, "composition_a": "2099-10-29T14:00:00+00:00", "composition_due": False,
               "composition_suspendue": False}

    def geste(page):
        page.wait_for_selector(".card")
        return page.text_content(".prog-etat"), page.text_content(".card .programmer")

    (etat, bouton), _, _ = _jouer(geste, articles=[_article(creneau=creneau)])

    assert etat.startswith("🗓") and "composé vers" in etat
    assert bouton == "Changer"


@chromium_absent
@pytest.mark.parametrize("champs, reintegrer", [
    ({"status": "used", "raison_non_programmable": "Article déjà publié"}, 0),
    ({"file_exclu_raison": "x", "raison_non_programmable": "Article exclu de la file"}, 1),
])
def test_pas_de_programmer_sur_un_article_publie_ou_exclu(champs, reintegrer):
    def geste(page):
        page.wait_for_selector(".card")
        return page.locator(".card .programmer").count(), page.locator(".reintegrer").count()

    retour, _, _ = _jouer(geste, articles=[_article(**champs)])

    assert retour == (0, reintegrer)


@chromium_absent
def test_une_carte_gelee_ne_propose_rien_et_dit_son_heure():
    creneau = {"creneau_id": 7, "publier_a": UN, "format": "carrousel", "etat": "gele", "epingle": False,
               "post_id": 3, "composition_a": None, "composition_due": False, "composition_suspendue": False}

    def geste(page):
        page.wait_for_selector(".card")
        return page.locator(".card .programmer").count(), page.text_content(".prog-etat")

    (boutons, etat), _, _ = _jouer(
        geste, articles=[_article(creneau=creneau, raison_non_programmable="Article déjà engagé sur un autre créneau")])

    assert boutons == 0 and "gelé" in etat


@chromium_absent
def test_rien_ne_deborde_a_390_px():
    options = [_option(7, occupant={**OCCUPANT, "titre": "Un titre très long " * 8, "epingle": True},
                       annonces=["partira sans fenêtre de relecture (moins de 120 min)"]),
               _option(8, publier_a=DEUX, possible=False, raison="Une raison très longue " * 6)]

    def geste(page):
        _ouvrir_carte(page)
        page.click(".prog-option")
        return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")

    debord, _, _ = _jouer(geste, {ROUTE: _lecture(options)}, articles=[_article()], largeur=390)

    assert debord <= 0


# --- 3. La ligne de Programmation ------------------------------------------------


@chromium_absent
def test_choisir_une_actu_sans_identifiant_a_recopier():
    actus = _actus([_candidat(5, notes=["sous le seuil de 60, choix manuel"]),
                    _candidat(6, possible=False, raison="Article exclu de la file")])

    def geste(page):
        _programmation(page)
        page.click('button:has-text("Choisir une actu")')
        page.wait_for_selector(".prog-option")
        texte = page.text_content(".prog-selecteur")
        page.click(".prog-option")
        page.click(".prog-validation .confirmer")
        page.wait_for_timeout(200)
        return texte

    texte, ecritures, dialogues = _jouer(geste, {"/schedule/7/actus-possibles": actus},
                                         creneaux=[_creneau(article_id=None, article_title=None, post_id=None, etat="libre")])

    assert "Actu 5" in texte and "pertinence 70" in texte and "il y a 3 h" in texte
    assert "sous le seuil de 60, choix manuel" in texte and "Article exclu de la file" in texte
    assert ("PATCH", "/schedule/7", {"article_id": 5, "occupant_attendu": None}) in ecritures
    assert dialogues == []


@chromium_absent
def test_une_ligne_sans_post_dit_quand_elle_sera_composee():
    def geste(page):
        _programmation(page)
        return page.text_content(".post-row .sub")

    sous_titre, _, _ = _jouer(geste, creneaux=[_creneau(post_id=None, composition_a="2099-10-29T14:00:00+00:00")])

    assert "composé vers" in sous_titre


@chromium_absent
def test_desepingler_reste_accessible_sur_un_creneau_mort():
    def geste(page):
        _programmation(page)
        return page.locator('button:has-text("Désépingler")').count()

    retour, _, _ = _jouer(geste, creneaux=[_creneau(article_id=None, article_title=None, post_id=None, epingle=True)])

    assert retour == 1


# --- 5. L'epinglage se dit ----------------------------------------------------------


def _fiche(page):
    page.evaluate("ouvrirPost(167)")
    page.wait_for_selector("#btn-save")
    page.wait_for_timeout(150)


@chromium_absent
def test_la_fiche_dit_l_epinglage_et_propose_desepingler():
    def geste(page):
        _fiche(page)
        texte = page.text_content(".depart-actions")
        page.click(".desepingler")
        page.wait_for_timeout(200)
        return texte

    texte, ecritures, _ = _jouer(geste, departs=[_depart("programme", epingle=True)])

    assert "Épinglé" in texte
    assert ("PATCH", "/schedule/7", {"epingle": False}) in ecritures


@chromium_absent
def test_sans_epinglage_la_fiche_se_tait():
    def geste(page):
        _fiche(page)
        return page.locator(".desepingler").count()

    retour, _, _ = _jouer(geste, departs=[_depart("programme")])

    assert retour == 0


# --- 6. Le format, lu ----------------------------------------------------------------


@chromium_absent
def test_le_format_est_impose_par_le_creneau_en_lecture_seule():
    def geste(page):
        _fiche(page)
        return page.text_content("#format-lu"), page.locator("#format-lu button").count()

    (texte, boutons), _, _ = _jouer(geste, departs=[_depart("programme", format="reel")])

    assert "Reel" in texte and "imposé par le créneau" in texte
    assert boutons == 0


@chromium_absent
def test_un_post_hors_creneau_reste_ouvert_et_editable():
    def geste(page):
        _fiche(page)
        return page.text_content("#format-lu"), page.is_enabled("#btn-save"), page.is_editable("#edit-caption")

    (texte, enregistrer, editable), _, _ = _jouer(geste)

    assert "gabarit de ce brouillon" in texte
    assert enregistrer and editable


@chromium_absent
def test_preuve_par_mutation_sans_stopPropagation_un_clic_dans_le_selecteur_ouvre_la_fiche():
    """Le selecteur vit dans une ligne cliquable : sans la garde, chercher une actu
    ouvrait la fiche par-dessus."""
    mutee = PAGE.replace("zoneChoix.onclick = (evenement) => evenement.stopPropagation();", "")
    assert mutee != PAGE
    actus = _actus([_candidat(5)])

    def geste(page):
        _programmation(page)
        page.click('button:has-text("Choisir une actu")')
        page.wait_for_selector(".prog-entete")
        page.click(".prog-entete")
        page.wait_for_timeout(300)
        return page.locator("#btn-save").count()

    avec, _, _ = _jouer(geste, {"/schedule/7/actus-possibles": actus}, creneaux=[_creneau()])
    sans, _, _ = _jouer(geste, {"/schedule/7/actus-possibles": actus}, creneaux=[_creneau()], html=mutee)

    assert avec == 0 and sans == 1
