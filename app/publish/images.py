"""Mettre une image aux regles de l'API de publication Instagram.

Regles citees de la documentation Meta le 2026-09-16 : JPEG uniquement, 8 Mo au
plus, largeur de 320 a 1440 px, proportions entre 4:5 et 1,91:1. Une image hors
de ces bornes fait echouer le conteneur -- et seulement au moment de publier,
c'est-a-dire trop tard. D'ou une mise au format systematique au gel.

Mais respecter les bornes de Meta NE SUFFIT PAS. Instagram recadre chaque
element d'un carrousel au ratio du PREMIER, ici la cover `kinda_post` rendue en
1080x1350 (4:5) : une photo de presse 16:9 laissee en paysage arrivait donc
rognee par Instagram, sans qu'on maitrise ce qui est coupe (2026-09-18, « sinon
les images rendent bizarre »). D'ou `cadrer_carrousel`, qui produit TOUS les
elements du carrousel exactement en 1080x1350 : agrandissement/reduction pour
REMPLIR le cadre, puis recadrage. Meme convention que les videos, que
`video.py::transcoder_carrousel` sort deja en 1080x1350.

Le recadrage n'est CENTRE que dans un sens. Une source plus LARGE que la
cible (paysage) garde sa bande centrale : la largeur ne dit rien sur ou est le
sujet, couper autant a gauche qu'a droite est le seul choix neutre. Une source
plus HAUTE que la cible (portrait) garde au contraire sa bande du HAUT --
jamais le centre. Constate en production le 2026-09-18 (post publie a 17h33,
slide 2, instagram.com/p/Ddb-UMFjVSW/) : un centrage vertical a coupe la tete
de Travis Scott sur une photo de concert plus haute que 4:5. Un photographe
cadre deja son sujet en haut du portrait et laisse la marge perdue en bas
(pieds, sol, scene) ; sur un portrait 2:3 ramene au 4:5 on ne retire que ~17 %
de la hauteur, et retirer cette marge par le bas ne coute rien alors que la
retirer par le haut decapite.

Deux cas ou le recadrage abimerait plus qu'il n'arrange, et ou l'image est alors
ECARTEE (`ImageEcartee`) plutot que publiee : elle n'est simplement pas dans le
carrousel -- jamais une erreur, jamais une exclusion d'article.

1. TROP PETITE (`UPSCALE_MAX`, 2,2) : la zone conservee ne remplit 1080x1350
   qu'au prix d'un agrandissement superieur au facteur. 2,2 laisse passer
   l'`og:image` standard de 1200x630 (facteur 2,14), qui est de loin
   l'illustration la plus frequente du pipeline -- un seuil plus severe (1,3 par
   exemple) l'ecarterait, donc viderait la plupart des carrousels et finirait par
   exclure les articles faute de second visuel. Au-dela de 2,2, on est sur des
   vignettes et des avatars (300-400 px) : l'agrandissement Lanczos s'y voit.
2. TROP ELOIGNEE DU 4:5 (`PERTE_MAX`, 0,60) : le recadrage jetterait plus de
   60 % des pixels. 0,60 garde le 16:9 (55 % de perte) et le 1,91:1 (58 %), les
   deux formats de presse courants, et ecarte les panoramiques (>= 2,35:1) et
   les bandeaux verticaux -- ceux dont il ne resterait qu'une tranche sans sujet.

`en_jpeg` sert la cover d'un reel, qui echappe aux regles de proportions du fil.
"""

import io

from PIL import Image, ImageOps

FORMAT_CARROUSEL = (1080, 1350)
LARGEUR_MIN = 320
LARGEUR_MAX = 1440
POIDS_MAX = 8 * 1024 * 1024
# Agrandissement maximal tolere pour remplir le cadre, et part maximale de
# l'image que le recadrage a le droit de jeter. Voir le docstring du module.
UPSCALE_MAX = 2.2
PERTE_MAX = 0.60
# Qualites essayees dans l'ordre, jusqu'a passer sous les 8 Mo.
QUALITES = (92, 85, 75, 65)
# Fond des images transparentes : le noir de la charte Kinda.
FOND = (0x1D, 0x1D, 0x1D)


class ImageInexploitable(ValueError):
    """L'image ne peut pas etre mise aux regles de Meta. Message affichable."""


class ImageEcartee(ImageInexploitable):
    """L'image est lisible, mais le cadrage 4:5 l'abimerait : on ne la publie pas.

    Sous-classe d'`ImageInexploitable` a dessein : le gel attrape deja celle-ci
    pour sauter un media, et une image ecartee doit etre sautee de la meme
    facon -- ce n'est ni une panne ni un defaut de l'article."""


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


def cadrer_carrousel(
    octets: bytes,
    format_: tuple[int, int] = FORMAT_CARROUSEL,
    *,
    upscale_max: float = UPSCALE_MAX,
    perte_max: float = PERTE_MAX,
) -> bytes:
    """Une image du carrousel, exactement au format demande (1080x1350), ou
    `ImageEcartee` si le recadrage l'abimerait trop.

    On recadre AVANT de redimensionner : le resultat est le meme et Pillow ne
    travaille jamais sur des pixels destines a partir."""
    image = _ouvrir_rgb(octets)
    largeur, hauteur = image.size
    cible_l, cible_h = format_
    ratio_cible = cible_l / cible_h

    # La zone du source qui a exactement le ratio de la cible.
    if largeur / hauteur > ratio_cible:
        zone_l, zone_h = round(hauteur * ratio_cible), hauteur
    else:
        zone_l, zone_h = largeur, round(largeur / ratio_cible)
    zone_l, zone_h = min(zone_l, largeur), min(zone_h, hauteur)

    perte = 1 - (zone_l * zone_h) / (largeur * hauteur)
    if perte > perte_max:
        raise ImageEcartee(
            f"proportion trop eloignee du {cible_l}x{cible_h} : "
            f"{perte:.0%} de l'image partirait au recadrage (maximum {perte_max:.0%})"
        )
    facteur = cible_l / zone_l
    if facteur > upscale_max:
        raise ImageEcartee(
            f"trop petite pour le cadre {cible_l}x{cible_h} : il faudrait l'agrandir "
            f"{facteur:.1f} fois (maximum {upscale_max:.1f})"
        )

    # Recadrage horizontal centre (couper autant a gauche qu'a droite) ;
    # recadrage vertical ancre en HAUT -- voir le docstring du module pour la
    # raison des deux regles.
    gauche = (largeur - zone_l) // 2
    recadrage_vertical = zone_l == largeur
    haut = 0 if recadrage_vertical else (hauteur - zone_h) // 2
    image = image.crop((gauche, haut, gauche + zone_l, haut + zone_h))
    if image.size != format_:
        image = image.resize(format_, Image.LANCZOS)
    return _jpeg_borne(image, POIDS_MAX)


def en_jpeg(octets: bytes, poids_max: int = POIDS_MAX) -> bytes:
    """La cover d'un reel : JPEG RGB de 8 Mo au plus, SANS recadrage.

    Les regles 4:5 a 1,91:1 ne valent que pour les images du fil. Pour un reel,
    Meta prend lui-meme le rectangle 9:16 central (onglet Reels) et le carre
    central (fil) : la cover kinda_reel, deja en 1080x1920, part telle quelle.
    """
    return _jpeg_borne(_ouvrir_rgb(octets), poids_max)
