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
from app.content.layout import Gabarit, gabarit, variables_css

logger = logging.getLogger(__name__)

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
    gabarit: Gabarit,
    textes: dict[str, str],
    image_data_uri: str,
    scale: float = 1.0,
    scrim_height: float | None = None,
) -> str:
    """Assemble le HTML autonome d'une slide (feuille, police et images inlinees).

    Sortie par une fonction a part pour etre verifiable sans Chromium : c'est ici
    que se joue l'echappement de la feuille de style, dont un ratage ne se voit
    sur le PNG que si on connait deja la bonne police.
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
    css = Markup((TEMPLATES_DIR / gabarit.name / "style.css").read_text(encoding="utf-8"))
    return env.get_template(f"{gabarit.name}/slide.html.j2").render(
        css=css,
        variables=Markup(variables_css(gabarit, scale, scrim_height)),
        logo_data_uri=_logo_data_uri(),
        image_data_uri=image_data_uri,
        textes={role: Markup(to_html(textes[role])) for role in gabarit.roles},
    )


def _render_one(page, gabarit_: Gabarit, textes: dict[str, str], image_uri: str) -> bytes:
    """Rend une slide a l'echelle de la maquette.

    Pas de mesure ni de reduction ici : cette fonction ne fait qu'assembler le
    document et prendre la capture -- la boucle de mesure/ajustement Chromium
    (`layout.Ajustement`, hauteur du voile dynamique) est la tache suivante.
    """
    page.set_content(build_document(gabarit_, textes, image_uri), wait_until="load")
    page.evaluate("document.fonts.ready")
    return page.screenshot(type="png")


def render_post(slides: list[dict], template: str = "kinda_post") -> list[bytes]:
    """Rend chaque slide en PNG. Un seul Chromium pour tout le post."""
    gabarit_ = gabarit(template)
    rendus: list[bytes] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": gabarit_.width, "height": gabarit_.height},
                device_scale_factor=1,
            )
            for slide in slides:
                image_uri = fetch_image_data_uri(slide.get("image_url"))
                textes = {role: slide[role] for role in gabarit_.roles}
                rendus.append(_render_one(page, gabarit_, textes, image_uri))
        finally:
            browser.close()
    return rendus
