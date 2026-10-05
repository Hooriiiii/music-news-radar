"""Un visuel depose depuis le disque : la seule porte par ou un fichier entre.

POURQUOI. L'editeur de relecture (2026-10-04) sait supprimer, reordonner et
REMPLACER un element de la liste publiee -- mais il ne propose que ce que le
pipeline a deja trouve (`preparation.candidats_editables` : medias de l'actu, de
son groupe de doublons, actifs d'artistes, candidats web). Utile quand la bonne
image est la, inutile quand elle n'y est pas. Choix de l'utilisateur : deposer un
fichier depuis son Mac.

CE QUE CE MODULE PRODUIT, ET RIEN DE PLUS : un ACTIF de plus dans `posts.assets`,
donc UNE ENTREE DE PLUS dans `candidats`. Tout le reste de l'editeur le traite
alors sans une ligne de changement -- l'ajouter a la liste passe par
`PATCH /posts/{id}` (`slides` + `carrousel_explicite`), qui rouvre deja le
creneau tout seul. Aucun second chemin d'ecriture, aucun appel a Meta, et la
reclamation (GELE -> ECHEC committe AVANT l'appel a Meta) n'est pas touchee.

LE FICHIER EST COPIE EN LOCAL, comme toute image qui doit survivre entre la
composition et le gel : `visuels_cache_dir`, nom derive du SHA-256, relu par un
`file://` RESOLU dans ce dossier (`visuels.lire_fichier_cache`), menage a
`visuels_cache_jours` (7 j). La purge supprime sur le `mtime` : un fichier
depose a l'instant ne peut pas disparaitre avant son gel (T-2h), et un depot
IDEMPOTENT (meme octets, donc meme nom) reecrit le fichier, donc rafraichit son
`mtime` -- un visuel redepose ne se fait pas purger sous son creneau.

UN REFUS DIT POURQUOI, ET AVANT LE GEL. Un media inexploitable est normalement
ECARTE au gel, avec sa raison, et c'est le bon comportement pour une image que
le pipeline a trouvee tout seul. Pour un fichier que l'utilisateur vient de
choisir, attendre T-2h pour apprendre qu'il ne passe pas est absurde. Le verdict
est donc rendu au depot, par LE MEME code que le gel
(`images.cadrer_carrousel`, seuils lus dans la configuration) : meme seuils,
memes messages, aucune seconde definition qui finirait par diverger.

CE QUI EST REPARABLE N'EST PAS REFUSE. Le pipeline sait recadrer -- vertical
ancre EN HAUT, horizontal centre -- et convertir en JPEG. Un PNG, une image
transparente, une photo de telephone couchee dans son EXIF, un 16:9 de presse :
tout cela passe. Ne sont refuses que les deux cas ou le cadrage abimerait plus
qu'il n'arrange, ceux-la memes qu'`ImageEcartee` ecarte au gel -- trop petite
(`publication_crop_upscale_max`) ou trop loin du 4:5
(`publication_crop_perte_max`) -- et le message dit LEQUEL a mordu.

LES BORNES DE META SONT ALORS SATISFAITES PAR CONSTRUCTION, et c'est le seul
endroit ou le dire : ce qui est fige au gel est la sortie de `cadrer_carrousel`,
exactement 1080x1350 (donc largeur entre 320 et 1440, proportion 4:5 pile) en
JPEG borne a 8 Mo par l'echelle de qualites. Le depot ne verifie donc pas ces
trois regles, il verifie qu'on peut les ATTEINDRE.

PAS DE VIDEO, et ce n'est pas une paresse : le gel ne sait telecharger une video
que depuis une URL http(s) (`preparation_reel.telecharger_video` refuse tout
autre schema, pour ne jamais lire un fichier de la machine sur une URL de base).
Une video deposee serait donc acceptee ici puis ECARTEE au gel, sans rien
montrer -- exactement le defaut que ce chantier corrige. Elle est refusee au
depot, avec cette raison.

SURFACE DE SECURITE -- c'est le premier endroit du depot ou un fichier entre
depuis l'exterieur. Le dashboard n'ecoute que sur 127.0.0.1 et n'est jamais
expose (le tunnel ne sert que le dossier d'un post), mais le fichier est traite
comme non fiable :
- LE TYPE REEL, jamais l'extension ni le `Content-Type` : l'image est DECODEE,
  et son format doit etre dans `FORMATS_ACCEPTES`. Un SVG, un PDF, une archive,
  un HEIC sont nommes dans le refus plutot que renvoyes en « illisible » ;
- UNE BORNE DE TAILLE AVANT la lecture en memoire : `TAILLE_MAX_OCTETS` est
  verifie sur le flux de la requete, morceau par morceau (`app/api/posts.py`),
  et un `Content-Length` annonce trop grand est refuse sans lire un octet ;
- UNE BORNE DE PIXELS, lue dans l'EN-TETE sans decoder : 25 Mo de PNG peuvent
  decoder en plusieurs gigaoctets. `PIXELS_MAX` tranche avant `Image.load()` ;
- AUCUN NOM DE FICHIER N'ARRIVE JAMAIS AU SERVEUR. Ce n'est pas un nom assaini,
  c'est un nom ABSENT : le navigateur poste les octets bruts et rien d'autre,
  cette fonction ne prend qu'un `bytes`, et le nom sur disque se derive du
  CONTENU. Un nom ne peut donc pas composer un chemin, par construction et non
  par vigilance -- la seule propriete d'ici dont la violation serait silencieuse
  ET grave (test de mutation dans `tests/publish/test_televersement.py`).
"""

import hashlib
import io
import logging
from pathlib import Path

from PIL import Image

from app.config import settings
from app.content.media import PHOTO
from app.content.visuels import ORIGINE_DEPOT, actif, chemin_cache
from app.publish.images import ImageEcartee, ImageInexploitable, cadrer_carrousel, en_jpeg

logger = logging.getLogger(__name__)

# Borne d'INGESTION, pas une regle de Meta : le fichier est reencode avant d'etre
# garde, donc un PNG de 20 Mo est parfaitement legitime et finira sous les 8 Mo
# que Meta accepte. 25 Mo couvre large une photo d'appareil, et borne ce qu'un
# depot peut faire tenir en memoire.
TAILLE_MAX_OCTETS = 25 * 1024 * 1024
# Bombe de decompression : lue dans l'en-tete, avant tout decodage. 50 Mpx, soit
# deux fois et demie un capteur de 20 Mpx.
PIXELS_MAX = 50_000_000

# Les formats matriciels que Pillow decode et qu'un recadrage a du sens. Le nom
# est celui que Pillow rend (`Image.format`), donc le type REEL du contenu --
# jamais l'extension du fichier, jamais l'en-tete `Content-Type`.
FORMATS_ACCEPTES = frozenset({"JPEG", "MPO", "PNG", "WEBP", "GIF", "BMP", "TIFF"})

# Ce qu'un refus doit NOMMER plutot que de renvoyer « image illisible ». Un HEIC
# est le premier candidat sur un Mac (c'est le format par defaut de l'iPhone) et
# Pillow ne le lit pas sans greffon ; un SVG et un PDF sont des fichiers qu'on
# depose de bonne foi en croyant deposer une image.
# LES VIDEOS NE SONT PAS ACCEPTEES, et la raison n'est pas « pas encore » : le
# gel ne sait telecharger une video que depuis une URL http(s)
# (`preparation_reel.telecharger_video` refuse tout autre schema, pour ne jamais
# lire un fichier de la machine sur une URL de base). Une video deposee serait
# donc acceptee ici puis ECARTEE au gel, sans rien montrer -- exactement le
# defaut que ce chantier corrige. On le dit au depot.
_REFUS_VIDEO = (
    "une vidéo ne peut pas être déposée depuis le disque : le gel ne sait "
    "télécharger une vidéo que depuis une URL http(s). Déposez une image fixe."
)

_SIGNATURES = (
    (b"%PDF-", "un PDF n'est pas une image : exportez la page en JPEG ou en PNG"),
    (b"PK\x03\x04", "une archive (zip, docx, …) n'est pas une image"),
    (b"<svg", "un SVG est un dessin vectoriel, pas une image matricielle : exportez-le en PNG"),
    (b"<?xml", "ce fichier est du XML (un SVG ?), pas une image matricielle"),
)


class DepotRefuse(ValueError):
    """Le fichier depose ne peut pas etre publie, et la raison est affichable.

    Toujours une phrase que l'utilisateur peut lire et sur laquelle il peut
    agir : c'est tout l'interet de trancher au depot plutot qu'au gel."""


def _diagnostic_connu(octets: bytes) -> str | None:
    """Un refus NOMME quand les premiers octets le permettent, sinon None."""
    debut = octets[:64]
    # Un conteneur ISO-BMFF annonce `ftyp` au 5e octet, puis sa marque : `heic`,
    # `heix`, `mif1`, `heim`... On cherche la famille, pas la liste exhaustive.
    if debut[4:8] == b"ftyp":
        marques = debut[8:24]
        if any(m in marques for m in (b"heic", b"heix", b"heif", b"mif1", b"msf1", b"hevc")):
            return (
                "les photos HEIC de l'iPhone ne sont pas lisibles ici : "
                "exportez-les en JPEG (Aperçu › Exporter) avant de les déposer"
            )
        return _REFUS_VIDEO
    # Matroska/WebM, et AVI. Le `RIFF` d'un WebP commence pareil : on exige la
    # marque `AVI `, sans quoi on refuserait une image parfaitement publiable.
    if debut[:4] == b"\x1aE\xdf\xa3" or (debut[:4] == b"RIFF" and debut[8:12] == b"AVI "):
        return _REFUS_VIDEO
    tete = debut.lstrip()
    for signature, raison in _SIGNATURES:
        if tete.startswith(signature):
            return raison
    return None


def _en_tete(octets: bytes) -> tuple[str, tuple[int, int]]:
    """Le format REEL et les dimensions, lus dans l'en-tete SANS decoder.

    `Image.open` ne lit que l'en-tete ; c'est `load()` qui decode -- d'ou la
    borne de pixels posee ici, avant que `en_jpeg` ne decode quoi que ce soit.
    """
    nomme = _diagnostic_connu(octets)
    if nomme:
        raise DepotRefuse(nomme)
    try:
        with Image.open(io.BytesIO(octets)) as image:
            format_, taille = image.format or "?", image.size
    except Exception as exc:  # noqa: BLE001 -- Pillow leve des types tres varies
        raise DepotRefuse(f"fichier illisible comme image : {exc}") from exc
    if format_ not in FORMATS_ACCEPTES:
        raise DepotRefuse(
            f"format {format_} non accepté (acceptés : {', '.join(sorted(FORMATS_ACCEPTES))})"
        )
    largeur, hauteur = taille
    if largeur * hauteur > PIXELS_MAX:
        raise DepotRefuse(
            f"image trop grande à décoder : {largeur}×{hauteur} px "
            f"(maximum {PIXELS_MAX // 1_000_000} Mpx)"
        )
    return format_, taille


def normaliser(octets: bytes) -> bytes:
    """Les octets gardes en cache : un JPEG RGB a la resolution D'ORIGINE.

    Pas le recadrage : le gel recadre lui-meme, sur les seuils du moment, et une
    copie deja rognee ne pourrait plus etre recadree autrement. Juste la
    conversion que tout le pipeline applique de toute facon -- RGB (un PNG
    transparent est aplati sur le noir de la charte), orientation EXIF redressee
    (sinon une photo de telephone partirait couchee), poids borne.
    """
    _en_tete(octets)
    try:
        return en_jpeg(octets)
    except ImageInexploitable as exc:
        raise DepotRefuse(str(exc)) from exc


def verifier_cadrage(jpeg: bytes) -> None:
    """Le verdict du GEL, rendu au depot -- meme fonction, memes seuils.

    `cadrer_carrousel` est appele pour son VERDICT et non pour sa sortie : ce qui
    sera fige est produit au gel, sur le fichier en cache. L'appeler ici coute un
    recadrage pour rien et achete la seule chose qui compte, savoir maintenant ce
    qu'on ne saurait sinon qu'a T-2h.
    """
    try:
        cadrer_carrousel(
            jpeg,
            upscale_max=settings.publication_crop_upscale_max,
            perte_max=settings.publication_crop_perte_max,
        )
    except ImageEcartee as exc:
        # Le message d'`ImageEcartee` dit DEJA lequel des deux seuils a mordu, en
        # chiffres : ne pas le reformuler, il est le meme que celui qui
        # apparaitrait en pointille dans la liste si le gel l'ecartait.
        raise DepotRefuse(str(exc)) from exc
    except ImageInexploitable as exc:
        raise DepotRefuse(str(exc)) from exc


def chemin_depose(jpeg: bytes) -> Path:
    """Ou le fichier atterrit : `visuels_cache_dir`, nom derive du CONTENU.

    Content-adressé, donc idempotent : redeposer la meme image reecrit le meme
    fichier (et rafraichit son `mtime`, ce qui la met a l'abri de la purge).
    Le prefixe `depot:` separe cet espace de nom de celui des copies
    telechargees, dont la cle est l'URL -- deux choses differentes ne doivent pas
    pouvoir se recouvrir par hasard.

    AUCUN nom fourni par l'appelant n'entre ici : il n'y en a pas. C'est ce qui
    rend la traversee de chemin impossible par construction.
    """
    return chemin_cache("depot:" + hashlib.sha256(jpeg).hexdigest())


def accepter(octets: bytes) -> dict:
    """Le fichier verifie, copie dans le cache, et rendu comme un ACTIF.

    Leve `DepotRefuse` -- jamais autre chose -- quand le fichier ne peut pas
    etre publie. L'actif rendu est de la meme forme que ceux de la composition
    (`visuels.actif`), avec sa copie locale dans `fichier` : `candidats_editables`
    le proposera donc comme n'importe quel autre visuel, et le gel lira la copie
    par `_octets_image`, qui resout un `file://` dans ce cache et nulle part
    ailleurs.
    """
    if not octets:
        raise DepotRefuse("fichier vide")
    if len(octets) > TAILLE_MAX_OCTETS:
        # Ceinture : l'endpoint borne deja le flux avant de lire. Deux gardes,
        # parce que celle-ci protege aussi les appelants non HTTP (scripts, tests).
        raise DepotRefuse(
            f"fichier trop lourd : {len(octets) / 1024 / 1024:.1f} Mo "
            f"(maximum {TAILLE_MAX_OCTETS // 1024 // 1024} Mo)"
        )
    jpeg = normaliser(octets)
    verifier_cadrage(jpeg)
    chemin = chemin_depose(jpeg)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(jpeg)
    uri = chemin.resolve().as_uri()
    largeur, hauteur = _en_tete(jpeg)[1]
    logger.info("visuel depose depuis le disque : %s (%dx%d)", chemin.name, largeur, hauteur)
    return actif(
        {"type": PHOTO, "url": uri, "preview_url": None,
         "duration_ms": None, "width": largeur, "height": hauteur},
        origine=ORIGINE_DEPOT,
        fichier=uri,
    )


__all__ = [
    "FORMATS_ACCEPTES",
    "PIXELS_MAX",
    "TAILLE_MAX_OCTETS",
    "DepotRefuse",
    "accepter",
    "chemin_depose",
    "normaliser",
    "verifier_cadrage",
]
