"""Le panneau Artistes : la moitie DETECTION de Kinda, enfin visible.

`docs/reference/memoire-ar.md` appelle la courbe des citations « l'actif de
Kinda » et `CLAUDE.md` le flux score « le deal-flow du label ». En production : 4 752 artistes, 13 533 citations
archivees depuis des semaines -- et jusqu'au 2026-10-04, AUCUNE interface ne les
montrait. `GET /artists` existait, teste, sans un seul consommateur.

Ce fichier verrouille deux choses, et une seule des deux est structurelle :

1. **Aucun champ de l'API ne disparait en silence du panneau** (meme sentinelle
   que `test_dashboard_events.py`).

2. **Une valeur qu'on n'a pas pu mesurer ne s'affiche JAMAIS comme un zero** --
   la regle la plus facile a casser par un `|| 0` bien intentionne, et la plus
   couteuse : `velocite` vaut `None` sous deux citations datees, `acceleration`
   sous trois, et 17 % des citations de la base n'ont aucune note d'emergence.
   Une acceleration inconnue affichee « 0 » se lit « cet artiste stagne », c'est
   a dire une affirmation FAUSSE sur la seule grandeur qui separe une
   decouverte d'une star. Elle est donc prouvee PAR MUTATION, sur un rendu
   Chromium reel et non sur la forme du code.

Le reste se mesure a l'ecran, aux trois largeurs : c'est en allant voir qu'on a
decouvert que 80 % des posts ne s'ouvraient pas.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.schemas import ArtistMentionOut, ArtistOut, SignalArtisteOut

DASHBOARD = pathlib.Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html"
PAGE = DASHBOARD.read_text(encoding="utf-8")


# --- 1) Sentinelle de schema ------------------------------------------------
# Champs volontairement geres autrement qu'un acces direct, documentes ici
# plutot que devines.
_SIGNAL_IGNORES: set[str] = set()
_ARTIST_IGNORES: set[str] = set()
_MENTION_IGNORES = {
    "id",        # aucune valeur a la lecture editoriale
    "artist_id",  # c'est la ligne qui le porte deja
}


def test_le_panneau_artistes_existe_et_est_une_navigation():
    assert 'id="artistes-panel"' in PAGE
    # Les panneaux sont une NAVIGATION (`data-panneau`), les filtres de la
    # grille des PASTILLES (`data-preset`) : deux portees, deux formes. Les
    # reglages propres au panneau sont des `select` dans sa barre, comme ceux
    # des panneaux Posts / Programmation / Evenements -- jamais des `.chip`.
    assert 'data-panneau="artistes"' in PAGE
    assert "loadArtistesPanel" in PAGE
    assert 'class="chip" data-preset="artistes"' not in PAGE
    for identifiant in ("art-fenetre", "art-citations-min", "art-min-emergence",
                        "art-limite", "art-inclure-suivis"):
        assert f'id="{identifiant}"' in PAGE, identifiant


def test_le_panneau_affiche_tous_les_champs_du_signal():
    for champ in SignalArtisteOut.model_fields:
        if champ in _SIGNAL_IGNORES:
            continue
        assert f"s.{champ}" in PAGE, champ


def test_le_panneau_affiche_tous_les_champs_de_la_fiche_artiste():
    for champ in ArtistOut.model_fields:
        if champ in _ARTIST_IGNORES:
            continue
        assert f"s.artist.{champ}" in PAGE, champ


def test_le_panneau_affiche_tous_les_champs_d_une_citation():
    for champ in ArtistMentionOut.model_fields:
        if champ in _MENTION_IGNORES:
            continue
        assert f"m.{champ}" in PAGE, champ


def test_la_fiche_artiste_ne_porte_plus_de_suivi_manuel():
    """`tracked` est tombee avec la migration 0021 : le panneau ne doit ni
    l'afficher ni l'envoyer, et `source_instagram_id` reste le geste utile."""
    assert "tracked" not in ArtistOut.model_fields
    # Hors commentaires : le mot reste ECRIT dans les notes de conception (c'est
    # la trace de ce qui a ete retire et pourquoi), il ne doit plus etre LU ni
    # ENVOYE par une ligne de code.
    sans_commentaires = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
    sans_commentaires = re.sub(r"^\s*//.*$", "", sans_commentaires, flags=re.M)
    assert "tracked" not in sans_commentaires
    assert "source_instagram_id" in sans_commentaires


# --- 2) Mesure a l'ecran ----------------------------------------------------

def _chromium_playwright_present() -> bool:
    caches = [
        pathlib.Path.home() / "Library" / "Caches" / "ms-playwright",
        pathlib.Path.home() / ".cache" / "ms-playwright",
    ]
    return any(c.exists() and any(c.glob("chromium-*")) for c in caches)


chromium_absent = pytest.mark.skipif(
    not _chromium_playwright_present(),
    reason="Chromium Playwright absent (CI) -- le rendu n'est pas mesurable ici",
)


# Les fixtures sont RELATIVES A MAINTENANT : deux dates figees ont pourri des
# tests de ce depot ce mois-ci.
def _iso(jours: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=jours)).isoformat()


def _artiste(identifiant: int, nom: str, *, source_instagram_id=None, max_emergence=None):
    return {
        "id": identifiant,
        "name": nom,
        "slug": nom.lower(),
        "first_seen_at": _iso(12),
        "last_seen_at": _iso(0.2),
        "mentions_count": 17,
        "max_emergence": max_emergence,
        "source_instagram_id": source_instagram_id,
    }


# Trois lignes, et chacune porte un cas qu'on veut voir a l'ecran :
# 1. une DECOUVERTE qui monte, tout mesure ;
# 2. un artiste dont RIEN n'est mesurable -- le cas de la preuve par mutation ;
# 3. un compte DEJA SUIVI, la question A&R deja repondue.
SIGNAUX = [
    {
        "artist": _artiste(1, "Ahadadream", max_emergence=75),
        "citations": 17,
        "citations_principales": 14,
        "emergence_moyenne": 52.4,
        "emergence_max_principale": 75,
        "velocite": 0.12,
        "acceleration": 0.65,
        "deja_suivi": False,
    },
    {
        "artist": _artiste(2, "Surusinghe"),
        "citations": 3,
        "citations_principales": 1,
        # TOUT est inconnu : pas de note d'emergence sur la fenetre, etalement
        # trop court pour une velocite, moins de trois citations datees pour une
        # acceleration. C'est le cas reel de la majorite des fiches.
        "emergence_moyenne": None,
        "emergence_max_principale": None,
        "velocite": None,
        "acceleration": None,
        "deja_suivi": False,
    },
    {
        "artist": _artiste(3, "Skrillex", source_instagram_id=42, max_emergence=85),
        "citations": 9,
        "citations_principales": 9,
        "emergence_moyenne": 85.0,
        "emergence_max_principale": 85,
        "velocite": 0.4,
        "acceleration": -0.5,
        "deja_suivi": True,
    },
]

CITATIONS = [
    {
        "id": 1, "artist_id": 1, "article_id": 18800, "source_id": 7,
        "mentioned_at": _iso(0.3), "relevance_score": 70, "emergence_score": 75,
        "title": "On debut album P K I, Ahadadream maps a new lineage",
        "url": "https://example.org/ahada", "principal": True,
    },
    {
        # Article purge (ON DELETE SET NULL) et rang perdu (NULL = inconnu, lu
        # comme PRINCIPAL : la regle du doute). Deux « inconnu » dans une ligne.
        "id": 2, "artist_id": 1, "article_id": None, "source_id": 7,
        "mentioned_at": _iso(9), "relevance_score": None, "emergence_score": None,
        "title": "Une citation dont l'article a ete purge",
        "url": "https://example.org/archive", "principal": None,
    },
    {
        "id": 3, "artist_id": 1, "article_id": 18700, "source_id": 7,
        "mentioned_at": _iso(10), "relevance_score": 45, "emergence_score": 85,
        "title": "Dance Up-And-Comer of the Month: Tatyana Jane",
        "url": "https://example.org/parrain", "principal": False,
    },
]

SOURCES = [
    {"id": 7, "name": "Mixmag", "url": "https://mixmag.net/rss.xml", "type": "rss",
     "genre": None, "categorie": "media", "active": True,
     "last_error": None, "last_error_at": None, "last_ok_at": _iso(0.05)},
    {"id": 42, "name": "Skrillex (IG)", "url": "ig:skrillex", "type": "instagram",
     "genre": None, "categorie": "artiste", "active": True,
     "last_error": None, "last_error_at": None, "last_ok_at": _iso(0.05)},
    {"id": 43, "name": "Fred again.. (IG)", "url": "ig:fredagain", "type": "instagram",
     "genre": None, "categorie": "artiste", "active": True,
     "last_error": None, "last_error_at": None, "last_ok_at": _iso(0.05)},
]

ROUTES = {
    "/reglages": {"retention_days": 2, "pipeline_intervalle_s": 3600},
    "/supervision": {"niveau": "ok", "releve_le": None, "voyants": []},
    "/sources": SOURCES,
    "/artists": SIGNAUX,
    "/artists/1/mentions": CITATIONS,
    "/articles": [],
}


def _demarrer_serveur(html: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (nom impose par http.server)
            chemin, _, _requete = self.path.partition("?")
            if chemin == "/":
                return self._repondre(html.encode("utf-8"), "text/html; charset=utf-8")
            if chemin in ROUTES:
                return self._json(ROUTES[chemin])
            return self._json([])

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


_RELEVE = """() => {
  const lignes = [...document.querySelectorAll("#artistes-list .artiste-row")];
  const cs = (el) => getComputedStyle(el);
  const texte = (el) => (el ? el.textContent.trim() : null);
  return {
    compteur: document.getElementById("count").textContent,
    filtres_grille_visibles: !!document.getElementById("barre-filtres").offsetParent,
    entete: document.querySelector("header").getBoundingClientRect().height,
    entete_sticky: cs(document.querySelector("header")).position,
    debordement: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    lignes: lignes.map((ligne) => {
      const nom = ligne.querySelector(".nom");
      const badges = [...ligne.querySelectorAll(".artiste-metrics .badge")];
      const tendance = ligne.querySelector(".badge.tendance");
      const suivi = ligne.querySelector(".badge.suivi, .badge.a-decouvrir");
      const sel = ligne.querySelector(".lier-compte select");
      return {
        nom: texte(nom),
        // La LARGEUR RENDUE du nom, pas une propriete CSS : c'est ainsi que le
        // titre ecrase a zero du panneau Evenements a ete trouve.
        largeur_nom: nom.getBoundingClientRect().width,
        ligne_largeur: ligne.getBoundingClientRect().width,
        badges: badges.map((b) => b.textContent.trim()),
        inconnus: badges.filter((b) => b.classList.contains("inconnu"))
                        .map((b) => b.textContent.trim()),
        tendance: { texte: texte(tendance), classes: [...tendance.classList] },
        suivi: { texte: texte(suivi), classes: [...suivi.classList] },
        liaison: sel ? { desactive: sel.disabled, valeur: sel.value,
                         options: [...sel.options].map((o) => o.textContent) } : null,
        citations_ouvertes: !ligne.querySelector(".artiste-citations").hidden,
      };
    }),
  };
}"""


def _mesurer(html: str, largeurs=(1440, 900, 390)) -> dict:
    from playwright.sync_api import sync_playwright

    serveur = _demarrer_serveur(html)
    port = serveur.server_address[1]
    mesures: dict = {}
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/#flux")
            page.wait_for_function("typeof afficherPanneau === 'function'")
            for largeur in largeurs:
                page.set_viewport_size({"width": largeur, "height": 950})
                page.click('[data-panneau="artistes"]')
                page.wait_for_selector("#artistes-list .artiste-row", state="attached")
                mesures[largeur] = page.evaluate(_RELEVE)
                # Les citations : juger sur pieces. On ouvre la premiere ligne.
                page.click("#artistes-list .artiste-row:first-child .artiste-head")
                page.wait_for_selector(
                    "#artistes-list .artiste-row:first-child .cit-row", state="attached")
                mesures[largeur]["citations"] = page.evaluate(
                    """() => [...document.querySelectorAll(
                         "#artistes-list .artiste-row:first-child .cit-row")].map((l) => ({
                       tete: l.querySelector(".tete").textContent.trim(),
                       titre: l.querySelector(".titre a").textContent.trim(),
                       href: l.querySelector(".titre a").getAttribute("href"),
                       apres: l.querySelector(".apres").textContent.trim(),
                       renvois: l.querySelector(".renvois").textContent.trim(),
                     }))""")
                page.click('[data-panneau="grille"]')
            navigateur.close()
    finally:
        serveur.shutdown()
    return mesures


@pytest.fixture(scope="module")
def mesures():
    return _mesurer(PAGE)


@chromium_absent
def test_le_panneau_s_affiche_aux_trois_largeurs(mesures):
    for largeur, m in mesures.items():
        assert m["compteur"] == "3 artistes", (largeur, m["compteur"])
        # Les filtres de la GRILLE n'ont rien a faire ici (regle 3 de la refonte).
        assert m["filtres_grille_visibles"] is False, largeur
        assert len(m["lignes"]) == 3, largeur
        # Aucun debordement horizontal : la lecon du panneau Evenements, dont
        # l'entete faisait 705 px sur un telephone.
        assert m["debordement"] <= 0, (largeur, m["debordement"])
        for ligne in m["lignes"]:
            # Le nom ne doit JAMAIS etre ecrase a zero de large.
            assert ligne["largeur_nom"] > 20, (largeur, ligne["nom"], ligne["largeur_nom"])
            assert ligne["ligne_largeur"] <= largeur, largeur


@chromium_absent
def test_l_entete_cesse_d_etre_collante_sur_un_telephone(mesures):
    assert mesures[1440]["entete_sticky"] == "sticky"
    assert mesures[390]["entete_sticky"] == "static"


@chromium_absent
def test_une_valeur_non_mesuree_s_affiche_inconnue_jamais_zero(mesures):
    """La regle la plus facile a casser par un `|| 0`, et la plus couteuse."""
    for largeur, m in mesures.items():
        inconnu = m["lignes"][1]  # Surusinghe : rien n'est mesurable
        assert inconnu["tendance"]["texte"] == "tendance inconnue", largeur
        assert "inconnu" in inconnu["tendance"]["classes"], largeur
        # Cinq mesures absentes (tendance, moyenne, pic fenetre, pic
        # historique, velocite), cinq badges « inconnue » -- et surtout aucun
        # « 0 », « 0.00 » ni « 0.0 » nulle part sur la ligne.
        assert len(inconnu["inconnus"]) == 5, (largeur, inconnu["badges"])
        for badge in inconnu["badges"]:
            assert "inconnue" in badge or not badge.strip().endswith(("0", "0.0", "0.00")), (
                largeur, badge)
        joints = " | ".join(inconnu["badges"])
        for faux_zero in ("moyenne 0", "vélocité 0", "pic fenêtre 0", "pic historique 0"):
            assert faux_zero not in joints, (largeur, joints)


@chromium_absent
def test_preuve_par_mutation_un_zero_bien_intentionne_est_attrape():
    """On remplace le garde-fou de `mesure()` par le `|| 0` exact qu'un
    contributeur presse ecrirait. Rien ne casse : la page reste belle, les
    tests de structure restent verts, et une acceleration inconnue se lit
    desormais « cet artiste stagne ». La mesure doit la refuser."""
    garde = "  return valeur == null ? null : Number(valeur).toFixed(decimales);"
    assert garde in PAGE, "`mesure()` a change de forme : la mutation ne mesure plus rien"
    mutee = PAGE.replace(garde, "  return Number(valeur || 0).toFixed(decimales);")
    m = _mesurer(mutee, largeurs=(1440,))[1440]
    inconnu = m["lignes"][1]
    # Le degat, mot pour mot : « regulier 0.00 » la ou rien n'a ete mesure.
    assert inconnu["tendance"]["texte"] == "→ régulier 0.00", inconnu["tendance"]
    assert inconnu["inconnus"] == [], inconnu["badges"]
    # Et la mesure du test precedent, rejouee sur la page mutee, doit echouer.
    with pytest.raises(AssertionError):
        test_une_valeur_non_mesuree_s_affiche_inconnue_jamais_zero({1440: m})


@chromium_absent
def test_la_tendance_distingue_ce_qui_monte_de_ce_qui_retombe(mesures):
    """« On commence a en parler » contre « on en parle beaucoup » : c'est LA
    lecture que ce panneau existe pour rendre immediate."""
    for largeur, m in mesures.items():
        monte, inconnu, retombe = m["lignes"]
        assert monte["tendance"]["texte"] == "↗ on commence à en parler 0.65", largeur
        assert "monte" in monte["tendance"]["classes"], largeur
        assert retombe["tendance"]["texte"] == "↘ ça retombe -0.50", largeur
        assert "retombe" in retombe["tendance"]["classes"], largeur
        # Les trois etats portent trois classes DISTINCTES : l'inconnu n'emprunte
        # la couleur d'aucun des deux verdicts.
        assert "monte" not in inconnu["tendance"]["classes"], largeur
        assert "retombe" not in inconnu["tendance"]["classes"], largeur


@chromium_absent
def test_le_seuil_de_montee_passe_au_dessus_du_biais_structurel():
    """`acceleration` penche de +1/n pour un nombre IMPAIR de citations
    regulierement espacees : trois citations equidistantes valent +0,33 et non 0,
    et trois est le plancher par defaut. Un seuil sous 0,33 qualifierait donc de
    montee la regularite la plus plate du cas le plus frequent."""
    valeur = re.search(r"const ACCELERATION_MONTE = ([\d.]+);", PAGE)
    assert valeur, "le seuil a change de nom"
    assert float(valeur.group(1)) > 1 / 3


@chromium_absent
def test_deja_suivi_se_lit_comme_une_reponse_pas_comme_une_alerte(mesures):
    for largeur, m in mesures.items():
        decouverte, _, suivi = m["lignes"]
        assert "à découvrir" in decouverte["suivi"]["texte"], largeur
        assert "a-decouvrir" in decouverte["suivi"]["classes"], largeur
        # Le nom du compte, pas seulement une coche : on veut savoir LEQUEL.
        assert "déjà suivi" in suivi["suivi"]["texte"], largeur
        assert "Skrillex (IG)" in suivi["suivi"]["texte"], largeur
        assert "suivi" in suivi["suivi"]["classes"], largeur


@chromium_absent
def test_le_selecteur_de_liaison_montre_les_comptes_suivis(mesures):
    """Le seul geste d'ecriture du panneau, et la raison pour laquelle
    `PATCH /artists/{id}` garde `source_instagram_id` apres la chute de
    `tracked` : rattacher une fiche a son compte quand le nom ne suffit pas."""
    for largeur, m in mesures.items():
        decouverte, _, suivi = m["lignes"]
        assert decouverte["liaison"]["desactive"] is False, largeur
        # Seules les sources Instagram ACTIVES de categorie artiste : Mixmag
        # (media, RSS) n'a rien a faire dans cette liste.
        assert decouverte["liaison"]["options"] == [
            "— aucun —", "Fred again.. (IG)", "Skrillex (IG)"], largeur
        assert decouverte["liaison"]["valeur"] == "", largeur
        # La fiche deja liee montre SON compte, pas « aucun ».
        assert suivi["liaison"]["valeur"] == "42", largeur


@chromium_absent
def test_un_controle_desactive_imprime_sa_raison():
    """Regle de la refonte : jamais un selecteur gris et muet. Sans compte
    suivi lisible, le panneau DIT pourquoi la liaison est impossible."""
    from playwright.sync_api import sync_playwright

    # Aucune source Instagram artiste en base : le cas d'un depot neuf.
    routes_sauvees = ROUTES["/sources"]
    ROUTES["/sources"] = [routes_sauvees[0]]
    serveur = _demarrer_serveur(PAGE)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/#flux")
            page.wait_for_function("typeof afficherPanneau === 'function'")
            page.click('[data-panneau="artistes"]')
            page.wait_for_selector("#artistes-list .artiste-row", state="attached")
            releve = page.evaluate(
                """() => {
                  const z = document.querySelector("#artistes-list .lier-compte");
                  return { desactive: z.querySelector("select").disabled,
                           raison: z.querySelector(".pourquoi-desactive").textContent.trim(),
                           visible: !!z.querySelector(".pourquoi-desactive").offsetParent };
                }""")
            navigateur.close()
    finally:
        serveur.shutdown()
        ROUTES["/sources"] = routes_sauvees
    assert releve["desactive"] is True
    assert releve["visible"] is True, "une raison en infobulle n'est pas une raison"
    assert "aucune source Instagram active de catégorie artiste" in releve["raison"]


@chromium_absent
def test_les_citations_se_lisent_sur_pieces(mesures):
    """« Juger sur pieces plutot que sur un score » : titre, source, date, et le
    renvoi vers l'actu -- ou « archivee » quand l'article a ete purge."""
    for largeur, m in mesures.items():
        citations = m["citations"]
        assert len(citations) == 3, largeur
        principale, archivee, passage = citations
        assert principale["tete"] == "principale", largeur
        assert principale["titre"].startswith("On debut album"), largeur
        assert principale["href"] == "https://example.org/ahada", largeur
        assert "Mixmag" in principale["apres"], largeur
        assert "article #18800" in principale["renvois"], largeur
        # Rang perdu (NULL) : lu comme principale, mais sans pretendre l'avoir
        # mesure -- et l'article purge ne peut pas porter de renvoi.
        assert archivee["tete"] == "principale ?", largeur
        assert archivee["renvois"] == "archivée", largeur
        # Les notes absentes de cette citation ne valent pas zero non plus.
        assert "pertinence inconnue" in archivee["apres"], largeur
        assert "émergence inconnue" in archivee["apres"], largeur
        # Un parrain cite en passant : sa note ne compte pas pour lui.
        assert passage["tete"] == "de passage", largeur


@chromium_absent
def test_un_echec_de_chargement_est_terminal_et_relancable():
    """Meme exigence que les quatre autres panneaux : une panne porte sa raison
    et un bouton, jamais un « Chargement… » eternel."""
    from playwright.sync_api import sync_playwright

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            chemin, _, _r = self.path.partition("?")
            if chemin == "/":
                corps = PAGE.encode("utf-8")
                type_contenu = "text/html; charset=utf-8"
            elif chemin == "/artists":
                self.send_response(500)
                self.send_header("Content-Length", "5")
                self.end_headers()
                self.wfile.write(b"boom!")
                return
            else:
                corps = json.dumps(ROUTES.get(chemin, [])).encode()
                type_contenu = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", type_contenu)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        def log_message(self, *args):
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/#flux")
            page.wait_for_function("typeof afficherPanneau === 'function'")
            page.click('[data-panneau="artistes"]')
            page.wait_for_selector("#artistes-list .zone-erreur", state="attached")
            releve = page.evaluate(
                """() => ({
                  raison: document.querySelector("#artistes-list .pourquoi").textContent,
                  relance: !!document.querySelector("#artistes-list .relance"),
                  compteur: document.getElementById("count").textContent,
                })""")
            navigateur.close()
    finally:
        serveur.shutdown()
    assert "Échec du chargement des artistes" in releve["raison"]
    assert "500" in releve["raison"]
    assert releve["relance"] is True
    assert releve["compteur"] == ""
