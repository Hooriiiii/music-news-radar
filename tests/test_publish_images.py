"""Les images que Meta acceptera : JPEG, 4:5 a 1,91:1, 1440 px de large au plus."""

import io

import pytest
from PIL import Image

from app.publish.images import ImageInexploitable, en_jpeg, normaliser


def _image(largeur, hauteur, mode="RGB", format_="PNG"):
    couleur = (255, 0, 128, 0) if mode == "RGBA" else (255, 0, 128)
    tampon = io.BytesIO()
    Image.new(mode, (largeur, hauteur), couleur).save(tampon, format_)
    return tampon.getvalue()


def _dimensions(octets):
    image = Image.open(io.BytesIO(octets))
    return image.format, image.size, image.mode


def test_la_cover_du_post_traverse_la_normalisation_intacte():
    """Depuis le 2026-09-18, `kinda_post` est rendu NATIVEMENT en 1080x1350.

    C'est le test qui ferme le defaut signale par l'utilisateur : tant que la
    cover etait dessinee en 3:4, la normalisation lui prenait 45 px en haut et
    45 px en bas, donc un tiers de la marge du logo et de celle de l'accroche.
    Un recadrage ici serait la reapparition du meme defaut."""
    assert _dimensions(normaliser(_image(1080, 1350))) == ("JPEG", (1080, 1350), "RGB")


def test_une_image_3_4_reste_recadree_en_4_5():
    """La regle Meta, elle, n'a pas bouge : une PHOTO d'actu en 3:4 (les slides
    du carrousel, qui ne passent pas par le gabarit) est toujours recadree."""
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


def test_une_banniere_trop_etroite_apres_recadrage_est_refusee():
    """Une banniere 728x90 (8.09:1) est recadree a 171x90, sous les 320 px."""
    with pytest.raises(ImageInexploitable):
        normaliser(_image(728, 90))


def test_la_cover_d_un_reel_garde_son_9_16():
    """Meta prend lui-meme le 9:16 central pour l'onglet Reels : pas de recadrage."""
    assert _dimensions(en_jpeg(_image(1080, 1920))) == ("JPEG", (1080, 1920), "RGB")


def test_en_jpeg_aplatit_la_transparence():
    format_, _, mode = _dimensions(en_jpeg(_image(1080, 1920, mode="RGBA")))
    assert (format_, mode) == ("JPEG", "RGB")


def test_en_jpeg_refuse_une_image_illisible():
    with pytest.raises(ImageInexploitable):
        en_jpeg(b"pas une image")
