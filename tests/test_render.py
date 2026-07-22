import struct
from pathlib import Path

import pytest

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
def test_render_post_reduit_la_police_sur_une_accroche_maximale():
    """La borne de 110 caracteres ne garantit pas l'absence de debordement :
    une accroche longue doit sortir un rendu, pas un post illisible."""
    longue = "**Nico Moreno**, Mosimann, Cassius et le collectif PACT ce week-end aux Vieilles Charrues"
    pngs = render_post([{"headline": longue, "image_url": None}])
    assert _png_size(pngs[0]) == (SLIDE_WIDTH, SLIDE_HEIGHT)
