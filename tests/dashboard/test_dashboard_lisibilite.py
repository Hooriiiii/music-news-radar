"""Ce qui ne s'affichait pas : la LARGEUR RENDUE, mesuree dans un navigateur.

Le defaut corrige ici ne se voit pas dans le DOM et ne se voit pas dans la
feuille de style lue a l'oeil : `.event-head` etait une rangee flex ou le titre
portait `flex: 1` (donc `flex-basis: 0`) pendant que les onze badges de
metriques revendiquaient leur largeur INTRINSEQUE. Le titre perdait l'arbitrage
et tombait a zero de large -- on n'en voyait qu'un fragment de glyphe --, et les
metadonnees tombaient un mot par ligne. Tous les champs etaient pourtant bien
presents dans le HTML, et `tests/dashboard/test_dashboard_events.py` etait au vert.

C'est exactement pourquoi un test structurel ne suffit pas : il faut MESURER.
Chromium calcule la mise en page, Python juge. Trois largeurs, parce que le
defaut empirait en se resserrant : a 820 px le titre disparaissait tout a fait.

Preuve par mutation (voir `test_la_mesure_rougit_si_le_titre_redevient_un_pair`):
on rejoue la MEME mesure sur la page dont le CSS est remis dans son etat fautif,
et la mesure doit rougir. Sans cette preuve, rien ne dirait que le test regarde
la bonne chose.
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html"
PAGE = DASHBOARD.read_text(encoding="utf-8")

# Les largeurs verifiees. 1280 = le poste de travail ; 820 = la largeur ou le
# titre disparaissait completement ; 390 = un portable.
LARGEURS = (1280, 820, 390)

TITRE = "Tomorrowland 2026: live updates from the mainstage"

EVENEMENT = {
    "id": 658,
    "titre": TITRE,
    "event_type": "event_festival",
    "first_seen_at": "2026-09-17T16:41:00+00:00",
    "last_seen_at": "2026-09-17T16:41:00+00:00",
    "source_count": 3,
    "independent_source_count": 2,
    "platform_count": 2,
    "freshness": 80.0,
    "corroboration": 61.0,
    "relevance_representant": 70,
    "velocity": 1.25,
    "acceleration": 0.5,
    "nouveaute": 100,
    "pertinence_france": 70,
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


def _demarrer_serveur(html: str):
    """La page `fetch` en chemin relatif : servie depuis un `data:`, aucune de
    ses requetes ne partirait."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (nom impose par http.server)
            chemin = self.path.split("?")[0]
            if chemin == "/":
                return self._repondre(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if chemin == "/events":
                return self._repondre(200, json.dumps([EVENEMENT]).encode(), "application/json")
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
            if chemin in ("/sources", "/posts", "/articles"):
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


def _mesurer_le_titre(html: str) -> dict[int, dict]:
    """Ouvre le panneau Événements et mesure, a chaque largeur, la boite du
    titre et celle de la ligne qui le contient."""
    from playwright.sync_api import sync_playwright

    serveur = _demarrer_serveur(html)
    port = serveur.server_address[1]
    mesures: dict[int, dict] = {}
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/#flux")
            page.wait_for_function("typeof loadEventsPanel === 'function'")
            page.click('[data-panneau="events"]')
            # `state="attached"` et non « visible » : dans la version fautive,
            # Chromium lui-meme considere ce titre comme INVISIBLE (largeur
            # nulle), et attendre sa visibilite ferait expirer la mutation au
            # lieu de la mesurer.
            page.wait_for_selector(".event-row .hl", state="attached")
            for largeur in LARGEURS:
                page.set_viewport_size({"width": largeur, "height": 900})
                mesures[largeur] = page.evaluate(
                    """() => {
                      const row = document.querySelector(".event-row");
                      const hl = row.querySelector(".hl");
                      const sub = row.querySelector(".sub");
                      return {
                        titre: hl.getBoundingClientRect().width,
                        sous_titre: sub.getBoundingClientRect().width,
                        hauteur_sous_titre: sub.getBoundingClientRect().height,
                        ligne: row.clientWidth
                          - parseFloat(getComputedStyle(row).paddingLeft)
                          - parseFloat(getComputedStyle(row).paddingRight),
                        texte: hl.textContent,
                      };
                    }"""
                )
            navigateur.close()
    finally:
        serveur.shutdown()
    return mesures


@chromium_absent
def test_le_titre_d_un_evenement_occupe_sa_ligne_a_toute_largeur():
    mesures = _mesurer_le_titre(PAGE)
    for largeur, m in mesures.items():
        assert m["texte"] == TITRE, largeur
        # Le titre a SA ligne : il occupe donc la largeur utile du bloc, aux
        # arrondis pres. Le seuil est a 95 % et non a 100 % pour tolerer une
        # bordure ou un demi-pixel, jamais un partage avec les badges (qui
        # laissait, mesure, moins de 1 % au titre).
        assert m["titre"] >= 0.95 * m["ligne"], (largeur, m)
        # Les metadonnees aussi : un mot par ligne venait d'une colonne de
        # ~40 px. Deux lignes de haut au plus a 1280 px suffisent a le dire.
        assert m["sous_titre"] >= 0.95 * m["ligne"], (largeur, m)
    assert mesures[1280]["hauteur_sous_titre"] <= 40, mesures[1280]


@chromium_absent
def test_la_mesure_rougit_si_le_titre_redevient_un_pair():
    """PREUVE PAR MUTATION. On remet la disposition fautive -- le titre et les
    badges dans la meme rangee flex -- et la mesure doit echouer. Un test de
    largeur qui passerait aussi sur la version cassee ne garantirait rien."""
    mutee = PAGE.replace(
        "  .event-head { display: block; cursor: pointer; }",
        "  .event-head { display: flex; align-items: center; gap: 12px; cursor: pointer; }\n"
        "  .event-head .hl, .event-head .sub { flex: 1; min-width: 0; }",
    )
    assert mutee != PAGE, "la mutation n'a rien remplace : le CSS a change de forme"
    mesures = _mesurer_le_titre(mutee)
    # A 820 px, la largeur ou le titre disparaissait : la mutation doit le
    # ramener loin sous sa ligne.
    m = mesures[820]
    assert m["titre"] < 0.95 * m["ligne"], m


# --- Etage STRUCTUREL : ce qui tient meme sans Chromium (CI). -----------------

# Les assertions NEGATIVES ci-dessous (« ce texte ne doit plus apparaitre »)
# portent sur ce qui est RENDU, pas sur les commentaires -- lesquels citent
# justement les anciennes formulations pour expliquer pourquoi elles etaient
# fausses. D'ou cette version de la page debarrassee de ses commentaires.
_SANS_COMMENTAIRES = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
_SANS_COMMENTAIRES = "\n".join(
    ligne for ligne in _SANS_COMMENTAIRES.splitlines() if not ligne.lstrip().startswith("//")
)


def test_la_page_porte_des_media_queries():
    """Il n'en existait AUCUNE : a 820 px la barre de l'entete eclatait en
    quatre rangees et le titre d'un evenement disparaissait."""
    assert "@media (max-width: 900px)" in PAGE
    assert "@media (max-width: 560px)" in PAGE


def test_les_gestes_d_un_creneau_portent_un_libelle_visible():
    """« 📌 » et « 📍 » sont deux actions OPPOSEES que seule l'inclinaison de
    l'icone distinguait, et un `title` ne se lit ni d'un coup d'oeil ni au
    lecteur d'ecran."""
    assert '"📍 Désépingler" : "📌 Épingler"' in PAGE
    assert '"Choisir une actu"' in PAGE
    assert '"⏬ Décaler"' in PAGE
    assert '"🚫 Ne pas publier"' in PAGE
    assert 'b.setAttribute("aria-label", titre);' in PAGE
    # Et sur les cartes d'article, « ↩ » seul ne disait pas ou l'on revient.
    assert "↩ À traiter</button>" in PAGE


def test_les_textes_qui_annoncent_un_reglage_le_derivent_de_la_config():
    """Deux d'entre eux mentaient : « toutes les 2 h » (l'agent est horaire) et
    « 14 jours de retention » (elle est a 2 depuis le 2026-09-21)."""
    assert 'fetch("/reglages")' in PAGE
    assert "toutes les 2 h" not in _SANS_COMMENTAIRES
    assert "14 jours de rétention" not in _SANS_COMMENTAIRES
    assert "cadencePipeline()" in PAGE and "retentionTexte()" in PAGE


def test_un_reglage_illisible_ne_retombe_pas_sur_une_valeur_inventee():
    """Meme regle que le bandeau : inconnu, jamais une valeur en dur."""
    bloc = PAGE.split("function cadencePipeline()", 1)[1].split("// ---", 1)[0]
    assert 'if (!s) return "";' in bloc
    assert "durée de rétention inconnue" in bloc


def test_le_fond_kinda_ne_decrit_plus_un_comportement_retire():
    """Depuis la Tache 5 du chantier visuels, une composition neuve SAUTE le
    creneau au lieu de publier l'aplat ; et le credit du site web a ete retire
    le meme jour."""
    assert "mieux vaut l'aplat sombre" not in _SANS_COMMENTAIRES
    assert "sauterait le créneau" in PAGE
    assert "le site n'est pas crédité" in PAGE


def test_une_ancre_n_atterrit_pas_sous_l_entete_sticky():
    assert "scroll-padding-top: calc(var(--entete-h" in PAGE
    assert "function mesurerEntete()" in PAGE
    assert 'entete.offsetHeight + "px"' in PAGE
