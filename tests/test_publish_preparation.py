"""Le gel : ce qui partira chez Meta, fige sur disque avant l'heure."""

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.content.render import RenduIndisponible, RenduSlide
from app.publish.preparation import PreparationImpossible, preparer_fichiers, urls_des_photos


def _png(largeur, hauteur):
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (10, 20, 30)).save(tampon, "PNG")
    return tampon.getvalue()


def _rendre(overflow=False):
    def rendre(slides, template):
        rendre.appels.append((slides, template))
        return [RenduSlide(png=_png(1080, 1440), scale=1.0, lines={}, overflow=overflow)]

    rendre.appels = []
    return rendre


def _post(*medias):
    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": "https://img/fond.jpg"}
    return SimpleNamespace(slides=[cover, *medias])


def _article(*urls):
    return SimpleNamespace(media=[{"type": "photo", "url": url} for url in urls])


def _telecharger(echecs=()):
    def telecharger(url):
        if url in echecs:
            return None, "HTTP 404"
        return _png(1200, 800), ".png"

    return telecharger


def test_la_cover_puis_les_photos_en_jpeg(tmp_path):
    rendre = _rendre()
    noms = preparer_fichiers(
        _post(), _article("https://img/1.jpg", "https://img/2.jpg"), tmp_path,
        rendre=rendre, telecharger=_telecharger(),
    )
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg", "03.jpg"]
    for nom in noms:
        assert (tmp_path / nom).read_bytes()[:2] == b"\xff\xd8"
    assert Image.open(tmp_path / noms[0]).size == (1080, 1350)
    assert rendre.appels[0][1] == "kinda_post"


def test_une_photo_qui_ne_se_telecharge_pas_est_ignoree(tmp_path):
    noms = preparer_fichiers(
        _post(), _article("https://img/1.jpg", "https://img/2.jpg"), tmp_path,
        rendre=_rendre(), telecharger=_telecharger(echecs=("https://img/1.jpg",)),
    )
    assert len(noms) == 2


def test_une_cover_qui_deborde_n_est_jamais_publiee(tmp_path):
    with pytest.raises(PreparationImpossible, match="deborde"):
        preparer_fichiers(_post(), None, tmp_path, rendre=_rendre(overflow=True), telecharger=_telecharger())


def test_chromium_absent_n_est_pas_une_preparation_impossible(tmp_path):
    # Panne systemique : elle doit remonter telle quelle, sinon le runner
    # exclurait les articles un par un pour une faute qui n'est pas la leur.
    def rendre(slides, template):
        raise RenduIndisponible("Chromium Playwright est absent")

    with pytest.raises(RenduIndisponible, match="Chromium"):
        preparer_fichiers(_post(), None, tmp_path, rendre=rendre, telecharger=_telecharger())


def test_dix_images_au_plus(tmp_path):
    urls = [f"https://img/{i}.jpg" for i in range(15)]
    noms = preparer_fichiers(_post(), _article(*urls), tmp_path, rendre=_rendre(), telecharger=_telecharger())
    assert len(noms) == 10


def test_un_nouveau_gel_efface_le_precedent(tmp_path):
    (tmp_path / "ancien_01.jpg").write_bytes(b"x")
    preparer_fichiers(_post(), None, tmp_path, rendre=_rendre(), telecharger=_telecharger())
    assert not (tmp_path / "ancien_01.jpg").exists()


def test_les_photos_viennent_des_slides_puis_de_l_article_sans_doublon():
    post = _post({"kind": "media", "type": "photo", "url": "https://img/a.jpg"},
                 {"kind": "media", "type": "video", "url": "https://img/v.mp4"})
    assert urls_des_photos(post, _article("https://img/a.jpg", "https://img/b.jpg")) == [
        "https://img/a.jpg", "https://img/b.jpg",
    ]
    assert urls_des_photos(post, None) == ["https://img/a.jpg"]
