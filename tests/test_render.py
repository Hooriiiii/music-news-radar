import struct
from pathlib import Path

import pytest

from app.content.layout import GABARIT_POST
from app.content.render import build_document, fetch_image_data_uri, render_post


def _chromium_playwright_present() -> bool:
    """Le Chromium de Playwright n'est pas sur le PATH : on cherche son cache."""
    caches = [
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    return any(c.exists() and any(c.glob("chromium-*")) for c in caches)


chromium_absent = pytest.mark.skipif(
    not _chromium_playwright_present(),
    reason="Chromium Playwright absent (CI) -- rendu non testable ici",
)


def _png_size(data: bytes) -> tuple[int, int]:
    """Lit largeur/hauteur dans l'entete IHDR d'un PNG."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_fetch_image_data_uri_retombe_sur_le_fallback_si_url_absente():
    uri = fetch_image_data_uri(None)
    assert uri.startswith("data:image/")


def test_fetch_image_data_uri_retombe_sur_le_fallback_si_url_cassee():
    """Une photo introuvable ne doit jamais faire echouer un post."""
    uri = fetch_image_data_uri("https://exemple.invalid/introuvable.jpg")
    assert uri.startswith("data:image/")


def test_le_document_porte_les_variables_du_gabarit():
    """Le CSS n'a plus de valeurs de mise en page : si les variables ne sont pas
    injectees, il rend un gabarit vide sans lever d'erreur."""
    document = build_document(
        GABARIT_POST, {"headline": "Une **accroche**"}, "data:image/jpeg;base64,AAA"
    )

    assert "--headline-font-size: 80px;" in document
    assert "--scale: 1.0;" in document
    assert "<mark>accroche</mark>" in document


def test_le_css_ne_contient_plus_de_valeurs_de_mise_en_page():
    """Sentinelle du refactoring : une valeur qui revient en dur dans la feuille
    est une regle qui echappe a layout.py, donc aux tests."""
    css = Path("app/content/templates/kinda_post/style.css").read_text(encoding="utf-8")
    # On ignore la police embarquee en base64 et les commentaires.
    corps = "\n".join(
        ligne
        for ligne in css.splitlines()
        if "base64" not in ligne and not ligne.strip().startswith(("/*", "*", "--"))
    )

    for valeur in ("80px", "-0.06em", "1.15", "77px", "76px", "253, 139, 195"):
        assert valeur not in corps, f"{valeur} est encore en dur dans style.css"

    # Un bloc `:root` dans la feuille rouvrirait la porte : une valeur en dur
    # derriere un nom de variable reste une valeur en dur, et le filtre de ce
    # test ignore justement les lignes `--`. Toutes les variables viennent de
    # Python, aucune n'est declaree ici.
    assert ":root" not in css


def test_la_feuille_de_style_nest_pas_echappee_par_le_gabarit():
    """L'autoescape de Jinja s'applique aussi a `{{ css }}` dans <style>.

    Il transforme les guillemets en `&#34;`, ce qui rend `font-family:
    "KindaDisplay"` et `format("woff2")` invalides : l'@font-face n'est pas
    enregistre, Chromium rend l'accroche dans son serif par defaut et coupe les
    lignes ailleurs que le gabarit Figma. Aucune erreur, aucun test de taille de
    PNG ne bronche -- d'ou cette verification sur le document lui-meme.
    """
    document = build_document(
        GABARIT_POST, {"headline": "Une **accroche**"}, "data:image/jpeg;base64,AAA"
    )

    assert 'font-family: "KindaDisplay"' in document
    assert 'format("woff2")' in document
    assert "&#34;" not in document


@chromium_absent
def test_la_police_du_gabarit_est_bien_celle_qui_rend_laccroche():
    """Preuve de bout en bout : la police embarquee est chargee ET utilisee.

    Le test precedent lit le HTML ; celui-ci verifie ce que Chromium en fait --
    face enregistree, chargee, et employee pour l'accroche. Un repli silencieux
    sur le serif par defaut donnerait une face absente et une largeur de texte
    differente.
    """
    from playwright.sync_api import sync_playwright

    document = build_document(
        GABARIT_POST, {"headline": "Vieilles Charrues"}, fetch_image_data_uri(None)
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": GABARIT_POST.width, "height": GABARIT_POST.height}
            )
            page.set_content(document, wait_until="load")
            page.evaluate("document.fonts.ready")
            faces = page.evaluate("[...document.fonts].map(f => [f.family, f.status])")
            largeur_kinda = page.evaluate(
                "(() => {"
                "  const r = document.createRange();"
                "  r.selectNodeContents(document.getElementById('headline'));"
                "  return r.getBoundingClientRect().width; })()"
            )
            largeur_repli = page.evaluate(
                "(() => {"
                "  document.getElementById('headline').style.fontFamily = 'serif';"
                "  const r = document.createRange();"
                "  r.selectNodeContents(document.getElementById('headline'));"
                "  return r.getBoundingClientRect().width; })()"
            )
        finally:
            browser.close()

    assert faces == [["KindaDisplay", "loaded"]]
    assert largeur_kinda != pytest.approx(largeur_repli, abs=1.0)


@chromium_absent
def test_le_voile_garde_la_geometrie_de_la_maquette_sans_scrim_height():
    """Un `var()` sans repli n'echoue pas silencieusement -- il INVALIDE toute
    la propriete au calcul, pas seulement le calque qui le porte.

    Rien ne le signale a la lecture du HTML : pas d'erreur, pas de warning, et
    aucune assertion de taille de PNG ne bronche (le viewport impose deja
    1080x1440, quelle que soit la geometrie du voile a l'interieur). Seule la
    valeur CALCULEE (`getComputedStyle`) le revele -- avant le repli, Chromium
    rendait `background-size: auto, auto, auto` et le voile passait en plein
    cadre au lieu des bandes localisees de la maquette. Cette tache (2) ne
    fournit jamais `scrim_height` (cable par la tache 3) : c'est exactement le
    cas que le repli doit couvrir dans le regime actuel.
    """
    from playwright.sync_api import sync_playwright

    document = build_document(
        GABARIT_POST, {"headline": "Vieilles Charrues"}, "data:image/jpeg;base64,AAA"
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": GABARIT_POST.width, "height": GABARIT_POST.height}
            )
            page.set_content(document, wait_until="load")
            taille = page.evaluate(
                "getComputedStyle(document.getElementById('scrim')).backgroundSize"
            )
        finally:
            browser.close()

    assert "auto" not in taille, f"le voile est en plein cadre (repli absent) : {taille!r}"
    assert "817px" in taille


@chromium_absent
def test_render_post_produit_un_png_par_slide():
    slides = [
        {"headline": "**Vieilles Charrues** : le line-up", "image_url": None},
        {"headline": "Rendez-vous **ce week-end**", "image_url": None},
    ]
    rendus = render_post(slides)

    assert len(rendus) == 2
    for rendu in rendus:
        assert _png_size(rendu.png) == (GABARIT_POST.width, GABARIT_POST.height)
        assert len(rendu.png) > 10_000


@chromium_absent
def test_le_comptage_de_lignes_est_exact_sur_l_accroche_de_reference():
    """L'accroche de la maquette Figma tombe sur TROIS lignes.

    Le comptage passe par `Range.getClientRects()`, un rectangle par ligne
    rendue. Diviser la hauteur par l'interlignage serait faux : un accent ou une
    descendante change la hauteur de boite. Et un `<mark>` produit PLUSIEURS
    rectangles sur une meme ligne -- s'ils ne sont pas regroupes par ordonnee, le
    compte explose.
    """
    accroche = (
        "Nico Moreno, Mosimann, Cassius... et **c'est ce week-end** "
        "aux **Vieilles Charrues**"
    )
    (rendu,) = render_post([{"headline": accroche, "image_url": None}])

    assert rendu.lines == {"headline": 3}
    assert rendu.scale == 1.0
    assert rendu.overflow is False


@chromium_absent
def test_une_accroche_trop_longue_declenche_la_reduction():
    """Le regime de secours. La police est reduite, mais le rendu sort."""
    longue = " ".join(["**Nico Moreno** et Mosimann aux Vieilles Charrues"] * 4)
    (rendu,) = render_post([{"headline": longue, "image_url": None}])

    assert rendu.scale < 1.0
    assert rendu.lines["headline"] <= GABARIT_POST.roles["headline"][1].max_lines


@chromium_absent
def test_un_texte_intenable_est_marque_et_non_publie_en_silence():
    """Ce qui remplace le `logger.warning` suivi d'un rendu coupe. Le drapeau
    remonte jusqu'au dashboard."""
    intenable = " ".join(["**Vieilles Charrues** Nico Moreno Mosimann Cassius"] * 20)
    (rendu,) = render_post([{"headline": intenable, "image_url": None}])

    assert rendu.overflow is True
    assert rendu.scale == GABARIT_POST.roles["headline"][1].min_scale
    assert _png_size(rendu.png) == (GABARIT_POST.width, GABARIT_POST.height)


@chromium_absent
def test_la_cover_reel_rend_une_bande_par_role():
    """La STRUCTURE du gabarit : deux roles, une bande chacun, aux dimensions de
    la toile. Les LARGEURS de bande (534 / 753) sont verifiees a la tache 5,
    apres calibration -- les asserter ici ferait echouer cette tache pour une
    raison qui ne la concerne pas."""
    from app.content.layout import GABARIT_REEL

    slide = {
        "title": "**Gregoire Cliquet**",
        "subtitle": "au festival des **vieilles Charrues**",
        "image_url": None,
    }
    (rendu,) = render_post([slide], "kinda_reel")

    assert rendu.lines == {"title": 1, "subtitle": 1}
    assert rendu.overflow is False
    assert _png_size(rendu.png) == (GABARIT_REEL.width, GABARIT_REEL.height)


@chromium_absent
def test_un_titre_de_reel_trop_long_passe_sur_une_deuxieme_bande():
    """Le choix retenu : la police ne se reduit pas, le titre passe sur une
    deuxieme bande rose. L'empilement de bandes EST le langage du gabarit."""
    slide = {
        "title": "**Gregoire Cliquet** et Nico Moreno en cloture",
        "subtitle": "au festival des **vieilles Charrues**",
        "image_url": None,
    }
    (rendu,) = render_post([slide], "kinda_reel")

    assert rendu.lines["title"] == 2
    assert rendu.scale == 1.0
