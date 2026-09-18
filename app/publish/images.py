"""Mettre une image aux regles de l'API de publication Instagram.

Regles citees de la documentation Meta le 2026-09-16 : JPEG uniquement, 8 Mo au
plus, largeur de 320 a 1440 px, proportions entre 4:5 et 1,91:1. Une image hors
de ces bornes fait echouer le conteneur -- et seulement au moment de publier,
c'est-a-dire trop tard. D'ou une normalisation systematique au gel.

La cover `kinda_post` ne subit PLUS aucun recadrage : elle est rendue nativement
en 1080x1350, c'est-a-dire exactement au 4:5 minimal, et traverse donc cette
fonction intacte. Jusqu'au 2026-09-18 elle etait dessinee en 1080x1440 (3:4) puis
recadree ici au centre : 45 px partaient en haut et en bas, soit la moitie de la
marge du logo (87 -> 42) et 60 % de celle de l'accroche (76 -> 31). Les marges
publiees n'etaient plus celles du design, et cela se voyait. La correction vit
dans `app/content/layout.py` (`GABARIT_POST.height`), pas ici : cette fonction
applique la regle de Meta et ne connait aucun gabarit.

Les autres images d'un carrousel (les photos de l'actu) restent, elles, recadrees
au besoin : elles arrivent dans n'importe quel format.

`en_jpeg` sert la cover d'un reel, qui echappe aux regles de proportions du fil.
"""

import io

from PIL import Image, ImageOps

RATIO_MIN = 4 / 5
RATIO_MAX = 1.91
LARGEUR_MIN = 320
LARGEUR_MAX = 1440
POIDS_MAX = 8 * 1024 * 1024
# Qualites essayees dans l'ordre, jusqu'a passer sous les 8 Mo.
QUALITES = (92, 85, 75, 65)
# Fond des images transparentes : le noir de la charte Kinda.
FOND = (0x1D, 0x1D, 0x1D)


class ImageInexploitable(ValueError):
    """L'image ne peut pas etre mise aux regles de Meta. Message affichable."""


def _ouvrir_rgb(octets: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(octets))
        image.load()
    except Exception as exc:  # noqa: BLE001 -- Pillow leve des types tres varies
        raise ImageInexploitable(f"image illisible : {exc}") from exc
    # Une photo de telephone porte souvent son orientation en EXIF : sans ca,
    # elle partirait couchee.
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        rgba = image.convert("RGBA")
        fond = Image.new("RGB", rgba.size, FOND)
        fond.paste(rgba, mask=rgba.getchannel("A"))
        image = fond
    return image


def _jpeg_borne(image: Image.Image, poids_max: int) -> bytes:
    for qualite in QUALITES:
        tampon = io.BytesIO()
        image.save(tampon, "JPEG", quality=qualite, optimize=True)
        if tampon.tell() <= poids_max:
            return tampon.getvalue()
    raise ImageInexploitable(f"plus de {poids_max // (1024 * 1024)} Mo meme compressee")


def normaliser(octets: bytes, ratio_min: float = RATIO_MIN) -> bytes:
    image = _ouvrir_rgb(octets)
    largeur, hauteur = image.size

    ratio = largeur / hauteur
    if ratio < ratio_min:
        # int() et pas round() : arrondir vers le haut donnerait une hauteur a
        # peine trop grande, donc un ratio a peine sous le minimum.
        nouvelle_hauteur = int(largeur / ratio_min)
        haut = (hauteur - nouvelle_hauteur) // 2
        image = image.crop((0, haut, largeur, haut + nouvelle_hauteur))
    elif ratio > RATIO_MAX:
        nouvelle_largeur = int(hauteur * RATIO_MAX)
        gauche = (largeur - nouvelle_largeur) // 2
        image = image.crop((gauche, 0, gauche + nouvelle_largeur, hauteur))

    # Verifier la largeur minimale APRES le recadrage de ratio, qui peut
    # l'avoir diminuee.
    if image.width < LARGEUR_MIN:
        raise ImageInexploitable(f"trop petite apres recadrage : {image.width} px de large, minimum {LARGEUR_MIN}")

    if image.width > LARGEUR_MAX:
        nouvelle_hauteur = round(image.height * LARGEUR_MAX / image.width)
        image = image.resize((LARGEUR_MAX, nouvelle_hauteur), Image.LANCZOS)

    return _jpeg_borne(image, POIDS_MAX)


def en_jpeg(octets: bytes, poids_max: int = POIDS_MAX) -> bytes:
    """La cover d'un reel : JPEG RGB de 8 Mo au plus, SANS recadrage.

    Les regles 4:5 a 1,91:1 ne valent que pour les images du fil. Pour un reel,
    Meta prend lui-meme le rectangle 9:16 central (onglet Reels) et le carre
    central (fil) : la cover kinda_reel, deja en 1080x1920, part telle quelle.
    """
    return _jpeg_borne(_ouvrir_rgb(octets), poids_max)
