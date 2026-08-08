"""Rendu des slides en PNG par un Chromium headless.

Figma ne peut pas rendre a l'execution : son API REST est en lecture seule sur le
contenu d'un fichier, et l'ecriture passe par l'API Plugin, qui n'existe que dans
l'editeur ouvert. Le template est donc extrait une fois en HTML/CSS (voir
`templates/kinda_post/REFERENCE.md`) et rendu ici.

Ce module ne connait ni la base ni Claude : il recoit une liste de dicts.
"""

import base64
import logging
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from playwright.sync_api import sync_playwright

from app.content.highlight import to_html

logger = logging.getLogger(__name__)

SLIDE_WIDTH = 1080
SLIDE_HEIGHT = 1440
# Hauteur allouee au bloc d'accroche, au-dela on reduit la police.
# Mesuree (Chromium headless, echelle 1.0) sur l'accroche de reference du template
# -- « Nico Moreno, Mosimann, Cassius... et **c'est ce week-end** aux **Vieilles
# Charrues** » -- qui doit tomber sur trois lignes : hauteur reelle 276 px (3 x
# 92 px de hauteur de ligne). Une accroche qui deborde sur une quatrieme ligne
# mesure 368 px. 420 px (valeur du brief) ne declenchait donc JAMAIS la reduction,
# meme a quatre lignes : ajuste a 320 px, a mi-chemin entre les deux, pour laisser
# passer trois lignes telles quelles et forcer la reduction des la quatrieme.
MAX_HEADLINE_HEIGHT = 320
# Paliers de reduction. Bornee a trois essais : au-dela le probleme n'est pas la
# taille de police mais l'accroche elle-meme.
SCALE_STEPS = (1.0, 0.9, 0.8)

TEMPLATES_DIR = Path(__file__).parent / "templates"
ASSETS_DIR = Path(__file__).parent / "assets"

_USER_AGENT = "KindaBot/1.0 (+https://github.com/kinda)"  # ASCII pur, cf. CLAUDE.md


def _data_uri(content: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")


def fetch_image_data_uri(url: str | None) -> str:
    """Telecharge l'image et l'inline en base64.

    Inliner et non referencer : une image chargee par URL rend la capture non
    deterministe (elle part parfois avant la fin du chargement). Une photo
    introuvable retombe sur l'image de secours -- un post sans photo vaut mieux
    qu'un post en echec.
    """
    if url:
        try:
            response = httpx.get(
                url, timeout=10.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
            )
            response.raise_for_status()
            mime = response.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            if mime.startswith("image/"):
                return _data_uri(response.content, mime)
            logger.warning("contenu non-image pour %s : %s", url, mime)
        except Exception as exc:  # noqa: BLE001 -- toute erreur retombe sur le fallback
            logger.warning("image non recuperee (%s) : %s", url, exc)
    return _data_uri((ASSETS_DIR / "fallback.jpg").read_bytes(), "image/jpeg")


def _logo_data_uri() -> str:
    return _data_uri((ASSETS_DIR / "logo.png").read_bytes(), "image/png")


def build_document(
    headline: str, image_data_uri: str, scale: float = 1.0, template: str = "kinda_post"
) -> str:
    """Assemble le HTML autonome d'une slide (feuille et images inlinees).

    Sortie par une fonction a part pour etre verifiable sans Chromium : c'est
    ici que se joue l'echappement de la feuille de style, dont un ratage ne se
    voit sur le PNG que si on connait deja la bonne police.
    """
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    # Markup et non str : l'autoescape du gabarit transformerait les guillemets
    # de la feuille en `&#34;`, ce qui casse SILENCIEUSEMENT `font-family:
    # "KindaDisplay"` et `format("woff2")` -- l'@font-face n'est alors pas
    # enregistre du tout et Chromium rend l'accroche dans sa police par defaut
    # (un serif), avec d'autres retours a la ligne que le gabarit Figma.
    css = Markup((TEMPLATES_DIR / template / "style.css").read_text(encoding="utf-8"))
    return env.get_template(f"{template}/slide.html.j2").render(
        css=css,
        logo_data_uri=_logo_data_uri(),
        image_data_uri=image_data_uri,
        headline_html=Markup(to_html(headline)),
        headline_scale=scale,
    )


def _render_one(
    page, template: str, image_uri: str, headline: str, scale_log: list[float] | None = None
) -> bytes:
    """Rend une slide, en reduisant la police tant que l'accroche deborde.

    `scale_log`, si fourni, recoit l'echelle retenue -- utilise par les tests
    pour prouver que la boucle de reduction s'est bien declenchee : la taille
    du PNG produit est fixee par le viewport Playwright et ne temoigne de rien
    sur l'echelle appliquee a l'accroche.
    """
    for scale in SCALE_STEPS:
        page.set_content(build_document(headline, image_uri, scale, template), wait_until="load")
        page.evaluate("document.fonts.ready")
        height = page.evaluate(
            "document.getElementById('headline').getBoundingClientRect().height"
        )
        if height <= MAX_HEADLINE_HEIGHT or scale == SCALE_STEPS[-1]:
            if height > MAX_HEADLINE_HEIGHT:
                logger.warning("accroche encore trop longue apres reduction : %r", headline)
            if scale_log is not None:
                scale_log.append(scale)
            return page.screenshot(type="png")
    raise RuntimeError("boucle de reduction sortie sans capture")  # pragma: no cover


def render_post(
    slides: list[dict], template: str = "kinda_post", scale_log: list[float] | None = None
) -> list[bytes]:
    """Rend chaque slide en PNG 1080x1440. Un seul Chromium pour tout le post."""
    rendus: list[bytes] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT},
                device_scale_factor=1,
            )
            for slide in slides:
                image_uri = fetch_image_data_uri(slide.get("image_url"))
                rendus.append(
                    _render_one(page, template, image_uri, slide["headline"], scale_log)
                )
        finally:
            browser.close()
    return rendus
