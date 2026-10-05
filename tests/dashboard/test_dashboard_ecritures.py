"""« Je n'arrive pas a enregistrer » -- et c'etait vrai de ce qu'on VOYAIT.

Retour de l'utilisateur le 2026-10-05. Verifie au navigateur : le clic envoyait
bien `PATCH /posts/176`, le serveur repondait **200**, la fiche se rechargeait.
Et RIEN ne le disait : aucun etat « en cours », aucun libelle qui change, aucune
confirmation. **Un succes muet est indistinguable d'un echec muet**, donc
l'utilisateur conclut que ca ne marche pas -- il a raison de le conclure. C'est
le meme defaut que l'ecriture muette de la categorie d'une source, corrigee le
2026-10-02, reste sur CINQ boutons de cette fiche.

La propriete est NEGATIVE et silencieuse a casser : retirer la ligne qui annonce
la reussite ne fait rougir aucun test de structure, aucune exception, aucune
regression visible -- la page reste belle et l'ecriture passe toujours. D'ou la
PREUVE PAR MUTATION ci-dessous, sur Chromium reel.
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


POST = {
    "id": 7,
    "article_id": 42,
    "article_title": "Charlotte de Witte annonce un EP",
    "status": "draft",
    "template": "kinda_post",
    "caption": "Legende du serveur.",
    "hashtags": ["#techno"],
    "slides": [{"kind": "cover", "headline": "**Charlotte** revient", "image_url": "https://e/x.jpg"}],
    "variants": {"variantes": [{"headline": "**Charlotte** revient"}], "retenue": 0},
    "assets": None,
}
ARTICLE = {"id": 42, "url": "https://exemple.test/a", "media": [], "visuels_interdits": False}

# Un reel dont la video n'a PAS d'URL directe : Meta omet `media_url` des reels a
# musique protegee. Le serveur le juge eligible (yt-dlp ira la chercher au gel,
# `store.source_video_reel` rend "ytdlp"), et c'est exactement le post 176.
ARTICLE_REEL = {
    "id": 43,
    "url": "https://www.instagram.com/reel/DeE4jV8NTQD/",
    "media": [{"type": "video", "url": None, "preview_url": "https://e/v.jpg", "duration_ms": None}],
    "visuels_interdits": False,
}


def _demarrer_serveur(html: str, journal: list):
    """Sert la page et accepte le `PATCH /posts/{id}` qu'elle envoie."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            chemin = self.path.split("?")[0]
            if chemin == "/":
                self._repondre(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif chemin in ("/sources", "/posts", "/articles", "/relecture"):
                self._repondre(200, b"[]", "application/json")
            elif chemin == "/supervision":
                self._repondre(
                    200,
                    json.dumps({"niveau": "inconnu", "releve_le": None, "voyants": []}).encode(),
                    "application/json",
                )
            else:
                self._repondre(404, b"{}", "application/json")

        def do_PATCH(self):  # noqa: N802
            taille = int(self.headers.get("Content-Length") or 0)
            corps = json.loads(self.rfile.read(taille) or b"{}")
            journal.append((self.path, corps))
            self._repondre(200, json.dumps({**POST, **corps}).encode(), "application/json")

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


def _enregistrer_et_lire_le_retour(html: str, post: dict = POST, article: dict = ARTICLE) -> dict:
    """Rejoue le geste exact de l'utilisateur : taper, cliquer « Enregistrer »,
    et regarder ce que la fiche en dit."""
    from playwright.sync_api import sync_playwright

    journal: list = []
    serveur = _demarrer_serveur(html, journal)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("typeof renderPostDetail === 'function'")
            page.evaluate("([p, a]) => renderPostDetail(p, a, null)", [post, article])
            page.fill("#edit-caption", "Un texte corrige a la main.")
            page.click("#btn-save")
            page.wait_for_timeout(900)
            retour = {
                "texte": page.text_content("#etat-geste") or "",
                "classe": page.get_attribute("#etat-geste", "class") or "",
                "ecritures": len(journal),
            }
            navigateur.close()
        return retour
    finally:
        serveur.shutdown()


@chromium_absent
def test_un_enregistrement_reussi_le_DIT():
    retour = _enregistrer_et_lire_le_retour(PAGE)
    assert retour["ecritures"] == 1, "le PATCH doit bien partir"
    assert retour["texte"].startswith("✓"), retour
    assert "enregistré" in retour["texte"]
    assert "ok" in retour["classe"].split()


@chromium_absent
def test_preuve_par_mutation_sans_l_annonce_le_succes_redevient_MUET():
    """La meme page, privee de la SEULE ligne qui annonce la reussite.

    C'est l'etat exact du 2026-10-05 : le serveur repond 200, la fiche se
    recharge, et l'ecran ne dit rien. Rien d'autre dans la suite ne rougit --
    c'est pour ca que ce test existe.
    """
    # On retire les deux annonces du chemin qui REUSSIT -- l'etat « en cours » et
    # la confirmation -- et rien d'autre : le chemin d'echec reste en place. C'est
    # mot pour mot la fiche d'avant ce correctif.
    cibles = (
        '  annoncerGeste(postId, "en-cours", `${libelle}…`);\n',
        '    annoncerGeste(postId, "ok", `✓ ${libelle} — enregistré`);\n',
    )
    mutant = PAGE
    for cible in cibles:
        assert mutant.count(cible) == 1, f"la mutation ne s'applique plus : {cible!r}"
        mutant = mutant.replace(cible, "")
    retour = _enregistrer_et_lire_le_retour(mutant)
    assert retour["ecritures"] == 1, "l'ecriture passe toujours : c'est bien l'AFFICHAGE qui manque"
    assert retour["texte"].strip() == "", retour


@chromium_absent
def test_un_echec_reste_a_l_ecran_avec_sa_raison():
    """Un `alert()` se chasse d'un clic et ne dit plus rien ensuite. La raison
    s'imprime a cote du bouton qui l'a provoquee, et y reste."""
    from playwright.sync_api import sync_playwright

    journal: list = []
    serveur = _demarrer_serveur(PAGE, journal)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("typeof renderPostDetail === 'function'")
            page.evaluate("([p, a]) => renderPostDetail(p, a, null)", [POST, ARTICLE])
            # Le serveur refuse : `enregistrerPost` leve.
            page.evaluate(
                "() => { window.enregistrerPost ="
                " async () => { throw new Error('409 trop tard'); }; }"
            )
            page.click("#btn-save")
            page.wait_for_timeout(600)
            texte = page.text_content("#etat-geste") or ""
            classe = page.get_attribute("#etat-geste", "class") or ""
            # Et le bouton redevient cliquable : un echec ne condamne pas le geste.
            rendu = page.is_enabled("#btn-save")
            navigateur.close()
        assert texte.startswith("✕"), texte
        assert "409 trop tard" in texte
        assert "erreur" in classe.split()
        assert rendu
    finally:
        serveur.shutdown()


# --- Etage structurel : ce qui tient meme sans navigateur -------------------


def test_un_double_clic_n_envoie_jamais_deux_ecritures():
    """Aucun des cinq boutons n'avait d'etat « en cours » : un double-clic
    envoyait deux `PATCH`. Le verrou est unique et porte par le seul point de
    passage des ecritures de la fiche."""
    assert "if (gesteEnCours) return undefined;" in PAGE
    assert 'if (bouton) { bouton.disabled = true; bouton.textContent = "…"; }' in PAGE


def test_toute_ecriture_de_la_fiche_passe_par_le_point_unique():
    """Un geste qui ecrirait sans passer par `ecrireDepuisLaFiche` serait muet a
    nouveau, et personne ne le verrait. Les appels directs a `enregistrerPost`
    dans la fiche doivent tous etre des `action:` de ce point de passage."""
    fiche = PAGE[PAGE.index("function renderPostDetail") : PAGE.index("// --- Panneau Événements")]
    for ligne in fiche.splitlines():
        if "enregistrerPost(" not in ligne:
            continue
        assert "action:" in ligne or "action: () =>" in ligne, ligne


def test_la_confirmation_survit_au_re_rendu_de_la_fiche():
    """Tous les gestes rappellent `renderPostDetail` : un message pose dans le
    DOM juste avant serait efface par le rendu qui suit l'ecriture reussie.
    D'ou un etat hors DOM, repeint a chaque rendu -- meme procede que le tampon
    de la legende."""
    assert "let dernierGeste = null;" in PAGE
    assert 'peindreGeste(detailEl.querySelector("#etat-geste"), post.id);' in PAGE
