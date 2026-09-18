"""Les images que Meta acceptera : JPEG, 1080x1350 exactement, 8 Mo au plus."""

import io

import pytest
from PIL import Image

from app.publish.images import (
    FORMAT_CARROUSEL,
    ImageEcartee,
    ImageInexploitable,
    cadrer_carrousel,
    en_jpeg,
)


def _image(largeur, hauteur, mode="RGB", format_="PNG"):
    couleur = (255, 0, 128, 0) if mode == "RGBA" else (255, 0, 128)
    tampon = io.BytesIO()
    Image.new(mode, (largeur, hauteur), couleur).save(tampon, format_)
    return tampon.getvalue()


def _dimensions(octets):
    image = Image.open(io.BytesIO(octets))
    return image.format, image.size, image.mode


def test_la_cover_du_post_traverse_le_cadrage_intacte():
    """Depuis le 2026-09-18, `kinda_post` est rendu NATIVEMENT en 1080x1350.

    Elle est deja au format du carrousel : ni agrandissement, ni recadrage."""
    assert _dimensions(cadrer_carrousel(_image(1080, 1350))) == ("JPEG", (1080, 1350), "RGB")


def test_une_photo_paysage_est_recadree_au_format_du_carrousel():
    """Instagram recadre chaque element au ratio du PREMIER : une photo de presse
    16:9 laissee en paysage revenait coupee n'importe comment. On cadre nous-memes."""
    assert _dimensions(cadrer_carrousel(_image(3200, 1800)))[1] == FORMAT_CARROUSEL


def test_une_photo_carree_est_recadree_au_format_du_carrousel():
    assert _dimensions(cadrer_carrousel(_image(2000, 2000)))[1] == FORMAT_CARROUSEL


def test_une_photo_portrait_9_16_est_recadree_au_format_du_carrousel():
    assert _dimensions(cadrer_carrousel(_image(1080, 1920)))[1] == FORMAT_CARROUSEL


def test_le_recadrage_est_centre():
    """Le sujet d'une photo est au centre : c'est la bande centrale qu'on garde."""
    source = Image.new("RGB", (2400, 1350), (0, 0, 0))
    source.paste(Image.new("RGB", (200, 1350), (255, 255, 255)), (1100, 0))
    tampon = io.BytesIO()
    source.save(tampon, "PNG")
    cadree = Image.open(io.BytesIO(cadrer_carrousel(tampon.getvalue())))
    assert cadree.getpixel((540, 675))[0] > 200  # la bande blanche est restee au centre
    assert cadree.getpixel((10, 675))[0] < 60  # et le noir aux bords


def test_un_panoramique_est_ecarte_plutot_que_massacre():
    """2000x500 (4:1) : un cadrage 4:5 en jetterait 80 %. Mieux vaut pas de slide."""
    with pytest.raises(ImageEcartee, match="trop eloignee"):
        cadrer_carrousel(_image(2000, 500))


def test_une_banniere_verticale_est_ecartee():
    """400x2000 (1:5) : 75 % de l'image partirait au recadrage."""
    with pytest.raises(ImageEcartee, match="trop eloignee"):
        cadrer_carrousel(_image(400, 2000))


def test_un_16_9_reste_publiable():
    """55 % de perte, juste sous la borne : c'est le format de presse le plus
    courant, l'ecarter viderait les carrousels."""
    assert _dimensions(cadrer_carrousel(_image(2400, 1350)))[1] == FORMAT_CARROUSEL


def test_une_vignette_trop_petite_est_ecartee():
    """400x300 : il faudrait l'agrandir 4,5 fois pour remplir le cadre."""
    with pytest.raises(ImageEcartee, match="trop petite"):
        cadrer_carrousel(_image(400, 300))


def test_une_image_a_peine_reduite_passe():
    """1200x1500 : facteur 0,9, aucun agrandissement."""
    assert _dimensions(cadrer_carrousel(_image(1200, 1500)))[1] == FORMAT_CARROUSEL


def test_les_deux_seuils_sont_reglables():
    petite = _image(400, 500)
    with pytest.raises(ImageEcartee):
        cadrer_carrousel(petite)
    assert _dimensions(cadrer_carrousel(petite, upscale_max=3.0))[1] == FORMAT_CARROUSEL

    panoramique = _image(3000, 750)
    with pytest.raises(ImageEcartee):
        cadrer_carrousel(panoramique)
    assert _dimensions(cadrer_carrousel(panoramique, perte_max=0.9))[1] == FORMAT_CARROUSEL


def test_une_image_ecartee_est_une_image_inexploitable():
    """Le gel attrape `ImageInexploitable` : une image ecartee doit y entrer,
    sinon elle ferait echouer la preparation au lieu d'etre simplement sautee."""
    assert issubclass(ImageEcartee, ImageInexploitable)


def test_un_png_transparent_devient_un_jpeg_rgb():
    format_, _, mode = _dimensions(cadrer_carrousel(_image(2000, 2000, mode="RGBA")))
    assert (format_, mode) == ("JPEG", "RGB")


def test_des_octets_qui_ne_sont_pas_une_image_sont_refuses():
    with pytest.raises(ImageInexploitable):
        cadrer_carrousel(b"<html>pas une image</html>")


def test_la_cover_d_un_reel_garde_son_9_16():
    """Meta prend lui-meme le 9:16 central pour l'onglet Reels : pas de recadrage."""
    assert _dimensions(en_jpeg(_image(1080, 1920))) == ("JPEG", (1080, 1920), "RGB")


def test_en_jpeg_aplatit_la_transparence():
    format_, _, mode = _dimensions(en_jpeg(_image(1080, 1920, mode="RGBA")))
    assert (format_, mode) == ("JPEG", "RGB")


def test_en_jpeg_refuse_une_image_illisible():
    with pytest.raises(ImageInexploitable):
        en_jpeg(b"pas une image")
