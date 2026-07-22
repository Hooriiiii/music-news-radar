import struct
from pathlib import Path

import pytest

import app.content.render as render_module
from app.content.render import SLIDE_HEIGHT, SLIDE_WIDTH, fetch_image_data_uri, render_post


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


@chromium_absent
def test_render_post_produit_un_png_par_slide():
    slides = [
        {"headline": "**Vieilles Charrues** : le line-up", "image_url": None},
        {"headline": "Rendez-vous **ce week-end**", "image_url": None},
    ]
    pngs = render_post(slides)

    assert len(pngs) == 2
    for png in pngs:
        assert _png_size(png) == (SLIDE_WIDTH, SLIDE_HEIGHT)
        assert len(png) > 10_000


@chromium_absent
def test_render_post_reduit_la_police_sur_une_accroche_maximale(monkeypatch):
    """La borne de 110 caracteres ne garantit pas l'absence de debordement :
    une accroche longue doit sortir un rendu, pas un post illisible.

    Les dimensions du PNG sont imposees par le viewport Playwright, quelle que
    soit l'echelle de police retenue -- les comparer ne prouve donc rien sur la
    boucle de reduction elle-meme (ce test resterait vert meme si la
    comparaison `height <= MAX_HEADLINE_HEIGHT` etait inversee). On rend la
    MEME accroche deux fois en monkeypatchant `MAX_HEADLINE_HEIGHT` -- une
    fois tres haut (aucune reduction possible, l'echelle retenue doit rester
    1.0), une fois tres bas (reduction forcee, l'echelle doit descendre sous
    1.0) -- et on lit l'echelle reellement appliquee via le parametre
    `scale_log` de `render_post`. C'est la preuve directe que la boucle de
    reduction s'est declenchee dans le bon sens, pas un indice indirect.
    """
    longue = "**Nico Moreno**, Mosimann, Cassius et le collectif PACT ce week-end aux Vieilles Charrues"
    slide = [{"headline": longue, "image_url": None}]

    monkeypatch.setattr(render_module, "MAX_HEADLINE_HEIGHT", 100_000)
    scales_sans_reduction: list[float] = []
    (png_sans_reduction,) = render_post(slide, scale_log=scales_sans_reduction)

    monkeypatch.setattr(render_module, "MAX_HEADLINE_HEIGHT", 1)
    scales_avec_reduction: list[float] = []
    (png_avec_reduction,) = render_post(slide, scale_log=scales_avec_reduction)

    for png in (png_sans_reduction, png_avec_reduction):
        assert _png_size(png) == (SLIDE_WIDTH, SLIDE_HEIGHT)
    assert scales_sans_reduction == [1.0]
    assert scales_avec_reduction == [render_module.SCALE_STEPS[-1]]
    assert scales_avec_reduction[0] < scales_sans_reduction[0]
    assert png_sans_reduction != png_avec_reduction
