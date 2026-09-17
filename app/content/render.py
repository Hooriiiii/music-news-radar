"""Rendu des slides en PNG par un Chromium headless.

Figma ne peut pas rendre a l'execution : son API REST est en lecture seule sur le
contenu d'un fichier, et l'ecriture passe par l'API Plugin, qui n'existe que dans
l'editeur ouvert. Le template est donc extrait une fois en HTML/CSS (voir
`templates/kinda_post/REFERENCE.md`) et rendu ici.

Ce module ne connait ni la base ni Claude : il recoit une liste de dicts.
"""

import base64
import logging
import mimetypes
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from playwright.sync_api import Browser, sync_playwright

from app.content.highlight import to_html
from app.content.visuels import lire_fichier_cache
from app.content.layout import Ajustement, Gabarit, Mesure, gabarit, hauteur_voile, variables_css

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
ASSETS_DIR = Path(__file__).parent / "assets"

_USER_AGENT = "KindaBot/1.0 (+https://github.com/kinda)"  # ASCII pur, cf. CLAUDE.md


class RenduIndisponible(RuntimeError):
    """Levee quand Chromium Playwright n'est pas installe localement."""


def _signale_chromium_absent(exc: Exception) -> bool:
    """Vrai seulement pour la signature precise de l'executable manquant.

    Isolee pour que `_ouvrir_chromium` reste lisible : un template casse ou une
    erreur Jinja ne matchent pas ce message et remontent donc inchanges (via le
    `raise` nu de son `except`).
    """
    return "Executable doesn't exist" in str(exc) or "playwright install" in str(exc)


def _ouvrir_chromium() -> tuple[Any, Browser]:
    """Demarre Playwright et lance Chromium, ou leve `RenduIndisponible`.

    Deux echecs distincts a garder separes : `contexte.__enter__()` (demarre la
    boucle asyncio et le process driver node) peut echouer independamment de
    `chromium.launch()` (le cas nominal de cette fonction, Chromium absent). Si
    le lancement echoue APRES un `__enter__()` reussi, il faut sortir du
    contexte avant de lever -- sinon le process driver et la boucle asyncio
    qu'il a ouverts ne sont jamais arretes (une fuite par appel echoue).
    Le contexte est ouvert a la main (`__enter__`/`__exit__`) plutot qu'avec
    `with` pour pouvoir le refermer chez l'appelant, une fois le rendu termine.
    L'appel `sync_playwright()` lui-meme est dans le meme essai que
    `__enter__()` : en vrai Playwright il ne fait que construire un objet et
    n'echoue jamais, mais rien ne garantit que cette frontiere reste stable --
    et un test peut la remplacer par un faux qui echoue des l'appel.
    """
    try:
        contexte = sync_playwright()
        playwright = contexte.__enter__()
    except Exception as exc:
        if _signale_chromium_absent(exc):
            raise RenduIndisponible(
                "Chromium Playwright est absent : lancer `uv run playwright install chromium`"
            ) from exc
        raise
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:
        # Le contexte est deja entre : sans cette sortie, le processus driver et
        # la boucle asyncio survivent a l'echec -- une fuite par slide et par
        # consultation, dans un uvicorn qui tourne en permanence.
        contexte.__exit__(None, None, None)
        if _signale_chromium_absent(exc):
            raise RenduIndisponible(
                "Chromium Playwright est absent : lancer `uv run playwright install chromium`"
            ) from exc
        raise
    return contexte, browser


def _data_uri(content: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")


def fetch_image_data_uri(url: str | None) -> str:
    """Telecharge l'image et l'inline en base64.

    Inliner et non referencer : une image chargee par URL rend la capture non
    deterministe (elle part parfois avant la fin du chargement). Une photo
    introuvable retombe sur l'image de secours -- un post sans photo vaut mieux
    qu'un post en echec.

    Une copie locale (`file://`) est lue dans le cache des visuels seulement ;
    hors du cache ou disparue, elle n'est jamais lue et le secours la remplace.
    """
    locale = lire_fichier_cache(url)
    if locale is not None:
        return _data_uri(locale, mimetypes.guess_type(url)[0] or "image/jpeg")
    if url and url.startswith("file://"):
        logger.warning("copie locale introuvable ou hors cache : %s", url)
        url = None
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
    # La cover reel n'a pas de voile (gabarit_.voile = False) : son contraste
    # vient des bandes, pas d'un degrade calcule sur la hauteur du texte.
    voile = None
    if gabarit_.voile:
        role_principal = next(iter(gabarit_.roles))
        if role_principal in mesures:
            voile = hauteur_voile(gabarit_, role_principal, mesures[role_principal])
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


def mesurer_bandes(slide: dict, template: str = "kinda_reel") -> dict[str, list[float]]:
    """Les largeurs de bande reellement rendues, role par role.

    Sert a la calibration : les corps et paddings exacts ne se deduisent pas par
    inversion des mesures de la maquette, ils se calent en rendant puis en
    comparant.
    """
    gabarit_ = gabarit(template)
    image_uri = fetch_image_data_uri(slide.get("image_url"))
    textes = {role: slide[role] for role in gabarit_.roles}
    contexte, browser = _ouvrir_chromium()
    # ExitStack et non deux `finally` empiles a la main : si `browser.close()`
    # leve (navigateur deja crashe), la sortie du contexte Playwright doit quand
    # meme avoir lieu -- sinon meme fuite que dans `_ouvrir_chromium`, sur un
    # chemin plus rare mais pas moins reel.
    with ExitStack() as pile:
        pile.callback(contexte.__exit__, None, None, None)
        pile.callback(browser.close)
        page = browser.new_page(
            viewport={"width": gabarit_.width, "height": gabarit_.height},
            device_scale_factor=1,
        )
        page.set_content(build_document(gabarit_, textes, image_uri), wait_until="load")
        page.evaluate("document.fonts.ready")
        return {role: list(mesurer_role(page, role).line_widths) for role in gabarit_.roles}


def render_post(slides: list[dict], template: str = "kinda_post") -> list[RenduSlide]:
    """Rend chaque slide du post. Un seul Chromium pour tout le post."""
    gabarit_ = gabarit(template)
    # Un `kind: "media"` est une photo ou une video de l'actu : elle part telle
    # quelle dans l'export, sans passer par le gabarit. Le defaut sur "cover"
    # est une ceinture pour une ligne que la migration aurait manquee -- a la
    # frontiere API le tag discriminant refuse deja un `kind` absent.
    covers = [s for s in slides if s.get("kind", "cover") == "cover"]
    rendus: list[RenduSlide] = []
    contexte, browser = _ouvrir_chromium()
    with ExitStack() as pile:
        pile.callback(contexte.__exit__, None, None, None)
        pile.callback(browser.close)
        page = browser.new_page(
            viewport={"width": gabarit_.width, "height": gabarit_.height},
            device_scale_factor=1,
        )
        for slide in covers:
            image_uri = fetch_image_data_uri(slide.get("image_url"))
            textes = {role: slide[role] for role in gabarit_.roles}
            rendus.append(_render_one(page, gabarit_, textes, image_uri))
    return rendus


def render_covers(
    variantes: list[dict], image_url: str | None, template: str = "kinda_post"
) -> list[RenduSlide]:
    """Rend les covers de plusieurs variantes sur le MEME fond.

    Un seul Chromium et une seule recuperation d'image pour les quatre : c'est
    le lancement du navigateur et le telechargement qui coutent du temps a
    l'usage du soir, pas la capture elle-meme. Rendre les quatre separement
    multiplierait par quatre le seul cout qui se voit.

    `gabarit_.roles` differe entre gabarits ({"headline": ...} pour le post,
    {"title": ..., "subtitle": ...} pour le reel) : la meme variante sert donc
    les deux sans condition, en ne prenant que les roles que le gabarit attend.
    """
    gabarit_ = gabarit(template)
    image_uri = fetch_image_data_uri(image_url)
    rendus: list[RenduSlide] = []
    contexte, browser = _ouvrir_chromium()
    with ExitStack() as pile:
        pile.callback(contexte.__exit__, None, None, None)
        pile.callback(browser.close)
        page = browser.new_page(
            viewport={"width": gabarit_.width, "height": gabarit_.height},
            device_scale_factor=1,
        )
        for variante in variantes:
            textes = {role: variante[role] for role in gabarit_.roles}
            rendus.append(_render_one(page, gabarit_, textes, image_uri))
    return rendus
