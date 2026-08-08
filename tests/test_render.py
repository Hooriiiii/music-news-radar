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
def test_render_post_produit_un_png_par_slide():
    slides = [
        {"headline": "**Vieilles Charrues** : le line-up", "image_url": None},
        {"headline": "Rendez-vous **ce week-end**", "image_url": None},
    ]
    pngs = render_post(slides)

    assert len(pngs) == 2
    for png in pngs:
        assert _png_size(png) == (GABARIT_POST.width, GABARIT_POST.height)
        assert len(png) > 10_000


# La reduction de police sur une accroche trop longue (`MAX_HEADLINE_HEIGHT`,
# `SCALE_STEPS`) n'existe plus dans ce module : elle est remplacee par la
# dichotomie de `layout.Ajustement`, cablee dans `_render_one` par la tache
# suivante. Le test qui la couvrait est donc retire ici plutot que laisse
# rouge -- il sera reecrit sur `layout.Ajustement` / `RenduSlide`.
