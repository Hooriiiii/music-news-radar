"""La liste de relecture, MESUREE dans un vrai Chromium a trois largeurs.

Meme methode et meme motif que `tests/dashboard/test_dashboard_lisibilite.py` : le defaut
que la refonte du 2026-10-02 a trouve -- un titre ecrase a zero de large par ses
voisins dans une rangee flex -- ne se voyait ni dans le DOM ni a la lecture du
CSS, et aucun test structurel ne l'attrapait. La ligne de relecture a exactement
la meme forme (une heure a largeur intrinseque, un titre qui doit prendre le
reste) : elle merite la meme mesure.

Et une PREUVE PAR MUTATION, parce qu'un test de largeur qui passerait aussi sur
la version cassee ne garantirait rien.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html"
PAGE = DASHBOARD.read_text(encoding="utf-8")

LARGEURS = (1440, 900, 390)

ACCROCHE = "5 dates sold out en octobre pour Awakenings a Amsterdam"

DEPART = {
    "creneau_id": 120,
    "publier_a": "2026-10-04T13:00:00+00:00",
    "format": "carrousel",
    "etat": "gele",
    "post_id": 167,
    "accroche": ACCROCHE,
    "article_title": "October belongs to Awakenings",
    "article_url": "https://example.test/a",
    "legende_publiee": "Awakenings n'a plus besoin de convaincre personne.\n\n📸 @awakenings\n\n#awakenings",
    "legende_longueur": 616,
    "legende_maximum": 2150,
    "hashtags_publies": ["#awakenings", "#amsterdam"],
    "hashtags_maximum": 30,
    "credit": "@awakenings",
    "fond": "https://img.test/fond.jpg",
    "fond_origine": "article",
    "elements_figes": 2,
    "editable": False,
    "raison_non_editable": "gelé : légende et hashtags modifiables, la cover demande de rouvrir",
    "reouvrable": True,
    "raison_non_reouvrable": None,
    "minutes_restantes": 113,
}


def _chromium_playwright_present() -> bool:
    caches = [
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    return any(c.exists() and any(c.glob("chromium-*")) for c in caches)


chromium_absent = pytest.mark.skipif(
    not _chromium_playwright_present(),
    reason="Chromium Playwright absent (CI) -- la largeur rendue n'est pas mesurable ici",
)


def _demarrer_serveur(html: str, departs):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (nom impose par http.server)
            chemin = self.path.split("?")[0]
            if chemin == "/":
                return self._repondre(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if chemin == "/relecture":
                return self._repondre(200, json.dumps(departs).encode(), "application/json")
            if chemin == "/reglages":
                return self._repondre(
                    200,
                    json.dumps({"retention_days": 2, "pipeline_intervalle_s": 3600}).encode(),
                    "application/json",
                )
            if chemin == "/supervision":
                return self._repondre(
                    200,
                    json.dumps({"niveau": "inconnu", "releve_le": None, "voyants": []}).encode(),
                    "application/json",
                )
            if chemin in ("/sources", "/posts", "/articles", "/events"):
                return self._repondre(200, b"[]", "application/json")
            return self._repondre(404, b"{}", "application/json")

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


def _mesurer(html: str, departs=(DEPART,)) -> dict[int, dict]:
    from playwright.sync_api import sync_playwright

    serveur = _demarrer_serveur(html, list(departs))
    port = serveur.server_address[1]
    mesures: dict[int, dict] = {}
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("typeof loadRelectureList === 'function'")
            page.click('[data-panneau="posts"]')
            # « attached » et non « visible » : dans la version mutee, Chromium
            # compte lui-meme ce titre comme invisible (largeur nulle), et
            # attendre sa visibilite ferait expirer la mutation au lieu de la
            # mesurer -- exactement la lecon de `test_dashboard_lisibilite`.
            page.wait_for_selector(".relecture-row .hl", state="attached")
            for largeur in LARGEURS:
                page.set_viewport_size({"width": largeur, "height": 900})
                mesures[largeur] = page.evaluate(
                    """() => {
                      const row = document.querySelector(".relecture-row");
                      const hl = row.querySelector(".hl");
                      const style = getComputedStyle(row);
                      return {
                        titre: hl.getBoundingClientRect().width,
                        quand: row.querySelector(".quand").getBoundingClientRect().width,
                        ligne: row.clientWidth
                          - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
                        texte: hl.textContent,
                        sous_titre: row.querySelector(".sub").textContent,
                        deborde_page: document.documentElement.scrollWidth > window.innerWidth + 1,
                      };
                    }"""
                )
            navigateur.close()
    finally:
        serveur.shutdown()
    return mesures


@chromium_absent
def test_la_ligne_de_relecture_tient_a_1440_900_et_390():
    mesures = _mesurer(PAGE)
    for largeur, m in mesures.items():
        assert m["texte"] == ACCROCHE, largeur
        # Pas de scroll horizontal, y compris a 390 px : la regle de la refonte
        # du 2026-10-02 (un gutter de 16 px, jamais de debordement lateral).
        assert not m["deborde_page"], (largeur, m)
        # L'heure garde sa largeur intrinseque (`white-space: nowrap`), donc elle
        # ne tombe jamais sur deux lignes...
        assert m["quand"] > 60, (largeur, m)
        # ... et le titre prend TOUT le reste. C'est l'assertion qui aurait
        # attrape le defaut du panneau Evenements.
        assert m["titre"] >= 0.5 * m["ligne"], (largeur, m)
        assert m["titre"] > 100, (largeur, m)


@chromium_absent
def test_la_mesure_rougit_si_le_titre_cesse_d_etre_tronque():
    """PREUVE PAR MUTATION, sur le garde-fou qui porte REELLEMENT le risque.

    La premiere mutation essayee -- retirer le `flex: 1` de la colonne du titre,
    la forme exacte du defaut du panneau Evenements -- NE casse pas cette ligne :
    mesure a 390 px, le titre garde 199 px sur 338, parce qu'il n'a qu'un seul
    voisin a largeur intrinseque (l'heure) et non onze badges. La disposition est
    donc genuinement plus robuste ici, et il aurait ete malhonnete de garder une
    mutation qui passe pour une preuve sans rien prouver.

    Ce qui casse vraiment, a 390 px, c'est la TRONCATURE : sans elle une accroche
    de cinquante caracteres pousse la rangee plus large que l'ecran, et toute la
    page prend un scroll horizontal -- ce que la refonte du 2026-10-02 interdit
    explicitement. C'est exactement l'assertion `deborde_page` ci-dessus.
    """
    mutee = PAGE.replace(
        "  .relecture-row .hl { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis;\n"
        "    white-space: nowrap; }",
        "  .relecture-row .hl { font-size: 13.5px; white-space: nowrap; }",
    )
    assert mutee != PAGE, "la mutation n'a rien remplace : le CSS a change de forme"
    m = _mesurer(mutee)[390]
    assert m["deborde_page"], m


@chromium_absent
def test_la_liste_n_affirme_rien_sur_une_cover_qu_elle_n_a_pas_mesuree():
    """L'intention d'origine tient : ne jamais affirmer que ca tient sans l'avoir
    mesure. Depuis le 2026-10-30 la liste ne parle plus du tout de debordement :
    `render_report` n'est ecrit par aucun bouton, et le gel refuse une cover qui
    deborde. La mesure vivante est `/covers`, a l'ouverture de la fiche."""
    ligne = _mesurer(PAGE, [DEPART])[1440]
    assert "déborde" not in ligne["sous_titre"]
    assert "tient" not in ligne["sous_titre"], "ne jamais affirmer que ca tient sans l'avoir mesure"


def test_la_fiche_ne_montre_plus_le_signal_mort_et_garde_la_mesure_vivante():
    """Les deux ecrans se contredisaient : « inconnu » dans la fiche, vert au
    bandeau, sur une mesure que personne ne produit."""
    assert "débordement de la cover inconnu" not in PAGE
    assert "partira ainsi" not in PAGE
    assert "depart.deborde" not in PAGE
    # L'avertissement qui reste : il lit `/covers`, rendu a l'ouverture de la fiche.
    assert "covers.overflow[indexActif]" in PAGE
    assert "Texte trop long même à la taille de police minimale" in PAGE


@chromium_absent
def test_une_liste_injoignable_ne_se_lit_pas_comme_rien_a_relire():
    """La panne la plus silencieuse possible : si `/relecture` tombe, afficher
    « aucun post en attente » ferait croire que la file est vide."""
    from playwright.sync_api import sync_playwright

    serveur = _demarrer_serveur(PAGE, [])
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.route("**/relecture", lambda route: route.fulfill(status=500, body="{}"))
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("typeof loadRelectureList === 'function'")
            page.click('[data-panneau="posts"]')
            page.wait_for_selector("#relecture-list .media-empty", state="attached")
            texte = page.inner_text("#relecture-list")
            navigateur.close()
    finally:
        serveur.shutdown()
    assert "injoignable" in texte
    assert "on ne sait pas" in texte
