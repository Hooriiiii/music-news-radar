"""Rendu des slides en PNG par un Chromium headless.

Figma ne peut pas rendre a l'execution : son API REST est en lecture seule sur le
contenu d'un fichier, et l'ecriture passe par l'API Plugin, qui n'existe que dans
l'editeur ouvert. Le template est donc extrait une fois en HTML/CSS (voir
`templates/kinda_post/REFERENCE.md`) et rendu ici.

Ce module ne connait ni la base ni Claude : il recoit une liste de dicts.
"""

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from playwright.sync_api import sync_playwright

from app.content.highlight import to_html
from app.content.layout import Ajustement, Gabarit, Mesure, gabarit, hauteur_voile, variables_css

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
    gabarit_: Gabarit,
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
    css = Markup((TEMPLATES_DIR / gabarit_.name / "style.css").read_text(encoding="utf-8"))
    return env.get_template(f"{gabarit_.name}/slide.html.j2").render(
        css=css,
        variables=Markup(variables_css(gabarit_, scale, scrim_height)),
        logo_data_uri=_logo_data_uri(),
        image_data_uri=image_data_uri,
        textes={role: Markup(to_html(textes[role])) for role in gabarit_.roles},
    )


# Regroupement par ordonnee et non un rectangle = une ligne : un `<mark>` coupe la
# ligne en PLUSIEURS rectangles (avant, dans, apres le surlignage). Sans
# regroupement, une accroche de 3 lignes avec deux segments surlignes compterait
# 7 lignes et declencherait une reduction injustifiee.
# La tolerance de 4 px absorbe les ecarts d'ordonnee entre fragments d'une meme
# ligne (accents, `background-position` du mark).
_MESURE_JS = """
(role) => {
  const el = document.getElementById(role);
  const style = getComputedStyle(el);
  // Un role a bande de fond est un element EN LIGNE : ses propres rectangles
  // incluent le padding et le fond, donc ils SONT les bandes -- c'est ce qu'on
  // veut mesurer. Un role en bloc n'a qu'un rectangle : il faut passer par un
  // Range sur son contenu pour obtenir une ligne par ligne rendue.
  const rects = style.display === 'inline'
    ? [...el.getClientRects()]
    : (() => { const r = document.createRange(); r.selectNodeContents(el);
               return [...r.getClientRects()]; })();

  const lignes = [];
  for (const rect of rects.filter(r => r.width > 0 && r.height > 0)
                          .sort((a, b) => a.top - b.top)) {
    const derniere = lignes[lignes.length - 1];
    if (derniere && Math.abs(rect.top - derniere.top) < 4) {
      derniere.gauche = Math.min(derniere.gauche, rect.left);
      derniere.droite = Math.max(derniere.droite, rect.right);
      derniere.bas = Math.max(derniere.bas, rect.bottom);
    } else {
      lignes.push({top: rect.top, bas: rect.bottom, gauche: rect.left, droite: rect.right});
    }
  }
  if (!lignes.length) return {widths: [], height: 0};
  return {
    widths: lignes.map(l => l.droite - l.gauche),
    height: lignes[lignes.length - 1].bas - lignes[0].top,
  };
}
"""


def mesurer_role(page, role: str) -> Mesure:
    """Mesure un role : une largeur par ligne rendue, plus la hauteur du bloc."""
    brut = page.evaluate(_MESURE_JS, role)
    return Mesure(line_widths=tuple(brut["widths"]), height=float(brut["height"]))


@dataclass(frozen=True)
class RenduSlide:
    """Le PNG et le rapport de ce qui s'est passe pour le produire.

    Le rapport n'est pas un detail de test : `overflow` est ce qui permet au
    dashboard de signaler un post en defaut plutot que de laisser publier une
    image coupee.
    """

    png: bytes
    scale: float
    lines: dict[str, int]
    overflow: bool


def _render_one(page, gabarit_: Gabarit, textes: dict[str, str], image_uri: str) -> RenduSlide:
    """Rend une slide : mesure, ajuste, puis capture.

    Deux passes AU MINIMUM, et c'est structurel : la hauteur du voile depend de la
    hauteur mesuree du bloc, qu'on ne connait qu'apres un premier rendu.
    """
    ajustement = Ajustement(gabarit_)
    mesures: dict[str, Mesure] = {}
    while (echelle := ajustement.prochaine_echelle()) is not None:
        page.set_content(build_document(gabarit_, textes, image_uri, echelle), wait_until="load")
        page.evaluate("document.fonts.ready")
        mesures = {role: mesurer_role(page, role) for role in gabarit_.roles}
        ajustement.enregistrer(mesures)

    resultat = ajustement.resultat
    if resultat.overflow:
        logger.warning(
            "texte intenable meme a l'echelle %.2f, slide marquee en defaut : %r",
            resultat.scale,
            textes,
        )

    # Passe finale : l'echelle retenue, et le voile cale sur la hauteur du bloc.
    role_principal = next(iter(gabarit_.roles))
    voile = (
        hauteur_voile(gabarit_, role_principal, mesures[role_principal])
        if role_principal in mesures
        else None
    )
    page.set_content(
        build_document(gabarit_, textes, image_uri, resultat.scale, voile), wait_until="load"
    )
    page.evaluate("document.fonts.ready")
    return RenduSlide(
        png=page.screenshot(type="png"),
        scale=resultat.scale,
        lines=resultat.lines,
        overflow=resultat.overflow,
    )


def render_post(slides: list[dict], template: str = "kinda_post") -> list[RenduSlide]:
    """Rend chaque slide du post. Un seul Chromium pour tout le post."""
    gabarit_ = gabarit(template)
    rendus: list[RenduSlide] = []
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
