"""Les images que Meta acceptera : JPEG, 4:5 a 1,91:1, 1440 px de large au plus."""

import io

import pytest
from PIL import Image

from app.publish.images import ImageInexploitable, normaliser


def _image(largeur, hauteur, mode="RGB", format_="PNG"):
    couleur = (255, 0, 128, 0) if mode == "RGBA" else (255, 0, 128)
    tampon = io.BytesIO()
    Image.new(mode, (largeur, hauteur), couleur).save(tampon, format_)
    return tampon.getvalue()


def _dimensions(octets):
    image = Image.open(io.BytesIO(octets))
    return image.format, image.size, image.mode


def test_la_cover_3_4_est_recadree_en_4_5():
    """La cover kinda_post fait 1080x1440 (3:4), hors des proportions Meta."""
    assert _dimensions(normaliser(_image(1080, 1440))) == ("JPEG", (1080, 1350), "RGB")


def test_une_image_carree_reste_intacte():
    assert _dimensions(normaliser(_image(1080, 1080)))[1] == (1080, 1080)


def test_un_panoramique_est_recadre_a_1_91():
    assert _dimensions(normaliser(_image(2000, 500)))[1] == (955, 500)


def test_une_image_trop_large_est_reduite_a_1440():
    assert _dimensions(normaliser(_image(3000, 2000)))[1] == (1440, 960)


def test_un_png_transparent_devient_un_jpeg_rgb():
    format_, _, mode = _dimensions(normaliser(_image(1080, 1080, mode="RGBA")))
    assert (format_, mode) == ("JPEG", "RGB")


def test_le_seuil_de_proportion_est_reglable():
    """Si Meta accepte un jour le 3:4, publication_ratio_min passe a 0.75."""
    assert _dimensions(normaliser(_image(1080, 1440), ratio_min=0.75))[1] == (1080, 1440)


def test_des_octets_qui_ne_sont_pas_une_image_sont_refuses():
    with pytest.raises(ImageInexploitable):
        normaliser(b"<html>pas une image</html>")


def test_une_image_trop_petite_est_refusee():
    with pytest.raises(ImageInexploitable):
        normaliser(_image(100, 100))
