"""Navigation, filtres et renvois entre panneaux (Task 4).

Trois defauts de COMPOSITION, verifies ici sur un rendu reel plutot que sur la
forme du code :

1. La navigation et le filtrage partageaient une classe, une rangee et une
   apparence. Ils ont deux PORTEES differentes (application / grille), ce qui
   n'est pas une redondance a supprimer mais une composition a ecrire. La
   consequence mesurable de la confusion : les trois `select` restaient actifs
   sous un panneau non-grille, et `load()` -- qui ecrit la grille ET le
   compteur de l'entete -- y etait rappele.
2. `article #123`, `post 45` et `IG 17894…` etaient du texte inerte : chaque
   renvoi d'un panneau a l'autre etait un copier-coller.
3. « (creneau libre) » occupait la position de titre sur six lignes sur dix,
   pendant que la raison d'un echec etait tassee en gris au milieu d'une
   phrase jointe par des « · ».

PREUVE PAR MUTATION sur le premier : c'est une propriete de NON-INTERFERENCE
entre deux portees -- exactement le genre qu'on croit corrigee parce qu'on ne
la voit plus a l'oeil. On rejoue donc la meme mesure sur la page dont la garde
de `load()` est neutralisee, et le compteur de la Programmation doit se faire
ecraser par celui de la grille.
"""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "static" / "dashboard.html"
PAGE = DASHBOARD.read_text(encoding="utf-8")

# La garde de portee, telle qu'elle est ecrite dans `load()`. Si cette ligne
# change de forme, la mutation ci-dessous ne remplacera plus rien et le test
# le dira (assertion explicite), plutot que de passer au vert pour rien.
GARDE = '  if (panneau !== "grille") return;'
# La seconde moitie de la garde : elle est RELUE apres les requetes, pour qu'un
# chargement lent qui se termine trop tard n'ecrase rien.
PORTEE = (
    '  const perime = () => panneau !== "grille" || generation !== generationGrille;'
)

ARTICLES = [
    {
        "id": 19000,
        "source_id": 1,
        "url": "https://example.org/a",
        "title": "Charlotte de Witte annonce un nouvel EP",
        "summary": "",
        "published_at": "2026-10-01T10:00:00+00:00",
        "fetched_at": "2026-10-01T11:00:00+00:00",
        "relevance_score": 75,
        "hotness_score": 82,
        "emergence_score": 40,
        "category": "new_release",
        "imprint": None,
        "genre": None,
        "status": "new",
        "media": [],
        "file_exclu_raison": None,
        "visuel": None,
        "raison_sans_visuel": None,
        "visuels_interdits": False,
    }
]

# Deux creneaux : un MANQUE qui porte une raison (la ligne ou la hierarchie se
# joue) et un LIBRE (la ligne dont le titre etait une non-information).
CRENEAUX = [
    {
        "id": 101,
        "publier_a": "2026-10-02T08:42:00+00:00",
        "format": "carrousel",
        "etat": "manque",
        "epingle": False,
        "article_id": 18951,
        "article_title": "The Weeknd Debuts New Song Sampling Evangelion",
        "article_url": "https://billboard.com/weeknd",
        "relevance": 70,
        "post_id": 134,
        "ig_media_id": "17912345678901234_17841400000000000",
        "erreur": "creneau manque : gele mais jamais publie, plus de 90 min de retard",
    },
    {
        "id": 102,
        "publier_a": "2026-10-02T11:15:00+00:00",
        "format": "reel",
        "etat": "libre",
        "epingle": False,
        "article_id": None,
        "article_title": None,
        "article_url": None,
        "relevance": None,
        "post_id": None,
        "ig_media_id": None,
        "erreur": None,
    },
]

EVENEMENT = {
    "id": 658,
    "titre": "Tomorrowland 2026 : la mainstage devoilee",
    "event_type": "event_festival",
    "first_seen_at": "2026-09-30T16:41:00+00:00",
    "last_seen_at": "2026-10-01T16:41:00+00:00",
    "source_count": 3,
    "independent_source_count": 2,
    "platform_count": 2,
    "freshness": 80.0,
    "corroboration": 61.0,
    "relevance_representant": 70,
    "velocity": None,
    "acceleration": None,
    "nouveaute": None,
    "pertinence_france": None,
}
DETAIL = dict(
    EVENEMENT,
    observations=[
        {
            "id": 1,
            "event_id": 658,
            "article_id": 18800,
            "platform": "rss",
            "source_id": 1,
            "source_role": "secondary",
            "published_at": "2026-10-01T10:00:00+00:00",
            "observed_at": "2026-10-01T10:05:00+00:00",
            "title": "Tomorrowland devoile sa mainstage",
            "url": "https://example.org/tml",
            "source_name": "DJ Mag",
            "post_id": 140,
        }
    ],
)

ROUTES = {
    "/reglages": {"retention_days": 2, "pipeline_intervalle_s": 3600},
    "/supervision": {"niveau": "ok", "releve_le": None, "voyants": []},
    "/sources": [],
    "/schedule": CRENEAUX,
    "/schedule/offre-reels": {
        "avec_video": 0,
        "directes": 0,
        "eligibles": 0,
        "fraicheur_jours": 2,
    },
    "/publication/mode": {"mode": "arret", "source": "env"},
    "/publication/reels": {"actifs": False, "ytdlp": False},
    "/posts": [],
    "/events": [EVENEMENT],
    "/events/658": DETAIL,
}


def _chromium_playwright_present() -> bool:
    caches = [
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    return any(c.exists() and any(c.glob("chromium-*")) for c in caches)


chromium_absent = pytest.mark.skipif(
    not _chromium_playwright_present(),
    reason="Chromium Playwright absent (CI) -- le rendu n'est pas mesurable ici",
)


def _demarrer_serveur(html: str, lenteur_articles: float = 0.0):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (nom impose par http.server)
            chemin, _, requete = self.path.partition("?")
            if chemin == "/":
                return self._repondre(html.encode("utf-8"), "text/html; charset=utf-8")
            if chemin == "/articles":
                # Une seule page : la boucle de pagination s'arrete sous 200.
                if lenteur_articles:
                    time.sleep(lenteur_articles)
                premiere = "offset=0" in requete or "offset=" not in requete
                return self._json(ARTICLES if premiere else [])
            if chemin in ROUTES:
                return self._json(ROUTES[chemin])
            self.send_response(404)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def _json(self, charge):
            self._repondre(json.dumps(charge).encode(), "application/json")

        def _repondre(self, corps: bytes, type_contenu: str):
            self.send_response(200)
            self.send_header("Content-Type", type_contenu)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        def log_message(self, *args):  # silence
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    return serveur


def _mesurer(html: str, largeurs=(1440, 900, 390)) -> dict:
    """Ouvre la page, passe sur la Programmation, agit sur le tri, et rend ce
    qu'on voit a chaque largeur."""
    from playwright.sync_api import sync_playwright

    serveur = _demarrer_serveur(html)
    port = serveur.server_address[1]
    mesures: dict = {}
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("typeof afficherPanneau === 'function'")
            page.wait_for_selector("#grid .card", state="attached")
            for largeur in largeurs:
                page.set_viewport_size({"width": largeur, "height": 950})
                page.click('[data-panneau="schedule"]')
                page.wait_for_selector("#schedule-list .post-row", state="attached")
                avant = page.evaluate("document.getElementById('count').textContent")
                # Le geste de FILTRAGE, sous un panneau qui n'est pas la grille.
                page.evaluate(
                    "const s = document.getElementById('sort');"
                    "s.value = 'date';"
                    "s.dispatchEvent(new Event('change'));"
                )
                page.wait_for_timeout(500)
                mesures[largeur] = page.evaluate(
                    """(avant) => {
                      const bar = document.getElementById("barre-filtres");
                      const lignes = [...document.querySelectorAll("#schedule-list .post-row")];
                      const porte = lignes[0];
                      const libre = lignes[1];
                      const cs = (el) => getComputedStyle(el);
                      const heure = porte.querySelector(".slot-heure");
                      const titre = porte.querySelector(".slot-titre");
                      const alerte = porte.querySelector(".slot-alerte");
                      const vide = libre.querySelector(".slot-titre");
                      return {
                        compteur_avant: avant,
                        compteur_apres: document.getElementById("count").textContent,
                        filtres_visibles: !!bar.offsetParent,
                        entete: document.querySelector("header").getBoundingClientRect().height,
                        heure: {
                          texte: heure.textContent,
                          taille: parseFloat(cs(heure).fontSize),
                          poids: Number(cs(heure).fontWeight),
                        },
                        titre: {
                          texte: titre.textContent,
                          taille: parseFloat(cs(titre).fontSize),
                          poids: Number(cs(titre).fontWeight),
                        },
                        vide: {
                          texte: vide.textContent,
                          taille: parseFloat(cs(vide).fontSize),
                          poids: Number(cs(vide).fontWeight),
                          italique: cs(vide).fontStyle,
                        },
                        alerte: {
                          visible: !alerte.hidden && alerte.getBoundingClientRect().height > 0,
                          texte: alerte.textContent,
                          couleur: cs(alerte).color,
                          largeur: alerte.getBoundingClientRect().width,
                          ligne: porte.querySelector(".grow").getBoundingClientRect().width,
                        },
                        renvois: [...porte.querySelectorAll(".sub .ref")].map(
                          (el) => ({ texte: el.textContent, href: el.getAttribute("href") })),
                      };
                    }""",
                    avant,
                )
                page.click('[data-panneau="grille"]')
                page.wait_for_selector("#grid .card", state="attached")
            navigateur.close()
    finally:
        serveur.shutdown()
    return mesures


# --- 1) Non-interference entre les deux portees -----------------------------


@chromium_absent
def test_un_geste_de_filtrage_ne_touche_pas_un_panneau_qui_n_est_pas_la_grille():
    for largeur, m in _mesurer(PAGE).items():
        assert m["compteur_avant"] == "2 créneaux", (largeur, m)
        # LE point : le compteur de la Programmation n'a pas bouge.
        assert m["compteur_apres"] == m["compteur_avant"], (largeur, m)
        # Et les filtres ne sont meme pas a l'ecran : ce qui n'est pas affiche
        # ne peut pas etre actionne par megarde.
        assert m["filtres_visibles"] is False, (largeur, m)


@chromium_absent
def test_preuve_par_mutation_sans_la_garde_le_compteur_est_reecrit():
    """On neutralise la garde de portee de `load()` et rien d'autre. La grille
    CACHEE se reconstruit alors, et son compteur ecrase celui de la
    Programmation -- « 2 creneaux » devient « 1 article » sans cause visible,
    le defaut exact que cette tache ferme."""
    mutee = PAGE.replace(GARDE, "  if (false) return;").replace(
        PORTEE, "  const perime = () => generation !== generationGrille;")
    assert mutee.count("panneau !== \"grille\"") < PAGE.count("panneau !== \"grille\""), (
        "la garde de portee a change de forme : la mutation ne mesure plus rien")
    m = _mesurer(mutee, largeurs=(1440,))[1440]
    assert m["compteur_avant"] == "2 créneaux", m
    assert m["compteur_apres"] == "1 article", m


@chromium_absent
def test_les_filtres_reviennent_sur_la_grille():
    """La bretelle ne doit pas confisquer les filtres a leur propre panneau."""
    from playwright.sync_api import sync_playwright

    serveur = _demarrer_serveur(PAGE)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_selector("#grid .card", state="attached")
            assert page.evaluate("!!document.getElementById('barre-filtres').offsetParent")
            page.click('[data-panneau="posts"]')
            page.wait_for_timeout(300)
            assert not page.evaluate("!!document.getElementById('barre-filtres').offsetParent")
            page.click('[data-panneau="grille"]')
            page.wait_for_selector("#grid .card", state="attached")
            assert page.evaluate("!!document.getElementById('barre-filtres').offsetParent")
            # Et un filtre y refait son travail : le compteur redevient celui
            # de la grille.
            page.evaluate(
                "const s = document.getElementById('sort');"
                "s.value = 'date'; s.dispatchEvent(new Event('change'));"
            )
            page.wait_for_timeout(400)
            assert page.evaluate("document.getElementById('count').textContent") == "1 article"
            navigateur.close()
    finally:
        serveur.shutdown()


# --- 3) La hierarchie d'une ligne de creneau --------------------------------


@chromium_absent
def test_la_ligne_de_creneau_hierarchise_heure_titre_et_panne():
    for largeur, m in _mesurer(PAGE).items():
        # L'heure prend la tete : plus grande ET plus grasse que le titre.
        assert m["heure"]["taille"] > m["titre"]["taille"], (largeur, m["heure"], m["titre"])
        assert m["heure"]["poids"] >= m["titre"]["poids"], (largeur, m)
        # Un creneau libre ne porte plus un titre : le texte le plus visible de
        # la ligne n'est plus une non-information.
        assert m["vide"]["poids"] < m["titre"]["poids"], (largeur, m["vide"])
        assert m["vide"]["italique"] == "italic", (largeur, m["vide"])
        # La PANNE est plus visible qu'avant, jamais moins : sa propre ligne,
        # pleine largeur, dans le rouge d'alerte deja en service.
        assert m["alerte"]["visible"], (largeur, m["alerte"])
        assert "90 min de retard" in m["alerte"]["texte"], (largeur, m["alerte"])
        assert m["alerte"]["couleur"] == "rgb(255, 138, 138)", (largeur, m["alerte"])
        assert m["alerte"]["largeur"] >= 0.95 * m["alerte"]["ligne"], (largeur, m["alerte"])


# --- 2) Les renvois entre panneaux ------------------------------------------


@chromium_absent
def test_les_identifiants_d_un_creneau_sont_des_renvois_cliquables():
    m = _mesurer(PAGE, largeurs=(1440,))[1440]
    renvois = {r["texte"]: r["href"] for r in m["renvois"]}
    assert renvois["article #18951"] == "https://billboard.com/weeknd", renvois
    # Le post ouvre sa fiche : c'est un bouton, pas un lien (donc pas de href).
    assert "post 134" in renvois and renvois["post 134"] is None, renvois
    lien_ig = next(t for t in renvois if t.startswith("IG "))
    assert renvois[lien_ig].startswith("https://www.instagram.com/p/"), renvois


# --- Etage STRUCTUREL : ce qui tient meme sans Chromium (CI) ----------------

_SANS_COMMENTAIRES = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
_SANS_COMMENTAIRES = "\n".join(
    ligne for ligne in _SANS_COMMENTAIRES.splitlines() if not ligne.lstrip().startswith("//")
)


def test_la_navigation_et_les_filtres_sont_deux_rangees_distinctes():
    """Pas une fusion, pas une suppression : une COMPOSITION. Les panneaux
    portent `data-panneau` et la classe `.onglet` ; les filtres gardent
    `data-preset` et `.chip`."""
    assert '<nav class="nav"' in PAGE
    for panneau in ("grille", "schedule", "posts", "events", "sources"):
        assert f'data-panneau="{panneau}"' in PAGE
    # Aucun bouton ne porte les deux portees a la fois.
    assert not re.search(r'data-preset="[^"]*"[^>]*data-panneau=', PAGE)
    assert not re.search(r'data-panneau="[^"]*"[^>]*data-preset=', PAGE)
    # Les anciennes pastilles de panneau ont disparu de la barre de filtres.
    for disparu in ("schedule", "posts", "events", "sources"):
        assert f'data-preset="{disparu}"' not in PAGE
    # Chaque rangee est titree.
    assert '<span class="rangee-titre">Vue</span>' in PAGE
    assert '<span class="rangee-titre">Filtres</span>' in PAGE


def test_la_garde_de_portee_est_le_point_unique_qui_tient_la_non_interference():
    assert GARDE in PAGE
    # Elle est la PREMIERE instruction de `load()` : un appel venu d'ailleurs
    # ne doit pas d'abord faire une requete pour etre ensuite refuse.
    corps = PAGE.split("async function load() {", 1)[1]
    avant_garde = corps.split(GARDE, 1)[0]
    assert "fetch(" not in avant_garde and "textContent" not in avant_garde


def test_le_lien_instagram_est_deduit_sans_aucun_appel_reseau():
    """La supervision ne doit RIEN couter : aucun permalien n'est demande a
    Graph. Le shortcode se calcule depuis l'identifiant media."""
    assert "function permalienInstagram(" in PAGE
    assert "https://www.instagram.com/p/" in PAGE
    bloc = PAGE.split("function permalienInstagram(", 1)[1].split("\n}", 1)[0]
    assert "fetch(" not in bloc


def test_article_url_n_est_plus_un_champ_inutilise():
    """Il etait dans la reponse de `/posts/{id}` et lu NULLE PART, pendant que
    le titre de l'actu s'affichait en clair."""
    assert "post.article_url" in PAGE
    assert "c.article_url" in PAGE
    assert "p.article_url" in PAGE


def test_un_renvoi_n_entraine_pas_le_geste_de_sa_ligne():
    """Une ligne de post s'ouvre au clic sur la ligne entiere."""
    assert "evenement.stopPropagation()" in PAGE


def test_un_article_purge_rend_un_renvoi_inerte_jamais_un_lien_mort():
    bloc = PAGE.split("function renvoiArticle(", 1)[1].split("\n}", 1)[0]
    assert "if (!url) return texte;" in bloc


@chromium_absent
def test_une_grille_lente_qui_arrive_en_retard_n_ecrase_pas_le_panneau_affiche():
    """Le defaut que la GARDE D'ENTREE seule ne voyait pas, trouve en ouvrant
    la page sur les donnees reelles (2026-10-02, 201 articles sur Neon) : la
    pagination lancee depuis la grille se terminait APRES le passage a la
    Programmation, et son ecriture ecrasait le compteur -- « 24 creneaux »
    redevenait « 201 articles » tout seul, sans aucun geste de filtrage.

    Ici le serveur retarde `/articles` d'une seconde, on change de panneau
    pendant ce temps, et le compteur doit rester celui du panneau affiche."""
    from playwright.sync_api import sync_playwright

    serveur = _demarrer_serveur(PAGE, lenteur_articles=1.0)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("typeof afficherPanneau === 'function'")
            # La grille est en train de charger : on part sur la Programmation.
            page.click('[data-panneau="schedule"]')
            page.wait_for_selector("#schedule-list .post-row", state="attached")
            assert page.evaluate("document.getElementById('count').textContent") == "2 créneaux"
            # Largement de quoi laisser la pagination se terminer.
            page.wait_for_timeout(2500)
            assert page.evaluate("document.getElementById('count').textContent") == "2 créneaux"
            navigateur.close()
    finally:
        serveur.shutdown()


@chromium_absent
def test_preuve_par_mutation_sans_le_jeton_la_grille_en_retard_ecrase_le_compteur():
    """Meme mutation d'esprit que la precedente, sur l'autre moitie de la
    garde : on neutralise la peremption et le defaut mesure en production
    revient."""
    mutee = PAGE.replace(
        "  const perime = () => panneau !== \"grille\" || generation !== generationGrille;",
        "  const perime = () => false;",
    )
    assert mutee != PAGE, "la peremption a change de forme : la mutation ne mesure plus rien"
    from playwright.sync_api import sync_playwright

    serveur = _demarrer_serveur(mutee, lenteur_articles=1.0)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("typeof afficherPanneau === 'function'")
            page.click('[data-panneau="schedule"]')
            page.wait_for_selector("#schedule-list .post-row", state="attached")
            page.wait_for_timeout(2500)
            assert page.evaluate("document.getElementById('count').textContent") == "1 article"
            navigateur.close()
    finally:
        serveur.shutdown()
