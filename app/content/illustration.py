"""Le fond d'une cover doit etre une ILLUSTRATION, jamais une affiche.

Demande de l'utilisateur, 2026-09-18 : « pour la cover background pour la
premiere slide avec le titre, je veux une image d'illustration de l'evenement, ou
de l'artiste mais pas une affiche ».

Le probleme est qu'une affiche de line-up est EXACTEMENT ce que les comptes de
festivals publient en premier, et que le fond etait jusqu'ici la premiere image
exploitable. Superposer l'accroche Kinda a une affiche deja couverte de texte
donne une cover illisible, et fait doublon : l'info est dans l'accroche.

Pourquoi un modele de VISION et pas une heuristique. Une affiche ne se reconnait
ni a son ratio (souvent 4:5, comme une photo de concert), ni a son URL, ni a ses
metadonnees -- elle se reconnait a ce qu'on y VOIT : des blocs de texte, une
grille de noms, un logo centre. Une detection de texte par OCR dirait « il y a du
texte » sur une photo de scene portant un ecran LED. Le seul juge fiable est
celui qui regarde l'image.

Trois regles de cout, dans cet ordre :
  1. on ne juge QUE s'il y a un choix a faire (au moins deux candidats) ;
  2. au plus `illustration_max_candidats` images, reduites a
     `illustration_taille_px`, dans UN SEUL appel -- ~2 300 jetons d'entree. Ce
     lot est juge d'avance par `prejuger` et sert DEUX decisions : quelle image
     fait le fond, et quelles cartes d'annonce le carrousel garde
     (`plafonner_affiches`). Un lot elargi, jamais un second appel ;
  3. le verdict est memorise par URL (`illustration_cache_path`) : une
     recomposition, ou un second creneau sur le meme article, ne repaie rien.

Et quand il n'y a rien a choisir parce que TOUT est une affiche (le carrousel de
line-up d'un festival), `eviter_l_affiche` ouvre des RESERVES : le compte qui a
publie l'actu, puis les artistes suivis cites. Si aucune ne donne de vraie photo,
la cover part sur le fond Kinda -- mieux vaut le fond Kinda qu'une affiche
(utilisateur, 2026-09-18 : « ya toujours l'affiche en fond !! »). Ce chemin ne
coute rien tant que le fond retenu n'est pas une affiche averee : ni lecture de
compte, ni telechargement, ni jeton.

Et une regle de robustesse qui prime sur tout : ce module ne LEVE JAMAIS. Pas de
cle API, modele en panne, reseau coupe, JSON hors schema, image illisible -> il
rend None et l'appelant garde le comportement d'avant (la premiere image). Une
composition ne se perd pas pour une question de gout.
"""

import base64
import datetime as dt
import io
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from app.config import settings
from app.content.export import telecharger as telecharger_media
from app.content.media import PHOTO
from app.content.urls import url_propre
from app.content.visuels import (
    FOND_KINDA,
    ORIGINE_ARTICLE,
    ORIGINE_KINDA,
    candidats_de_fond,
    image_de,
    lire_fichier_cache,
)

logger = logging.getLogger(__name__)

SUJETS = ("scene", "portrait", "autre")

# L'ordre de preference entre sujets, a note egale. Une scene (concert, foule,
# club) et un portrait sont tous deux de bons fonds ; « autre » (un plan
# d'objet, un visuel abstrait) passe apres.
RANG_SUJET = {"scene": 2, "portrait": 2, "autre": 1}

CACHE_VERSION = 1


class Verdict(BaseModel):
    """Ce que le juge dit d'UNE image."""

    affiche: bool
    sujet: Literal["scene", "portrait", "autre"]
    note: int = Field(ge=0, le=100)


class Verdicts(BaseModel):
    """Un verdict par image fournie, DANS L'ORDRE. L'ordre est le seul lien entre
    une image et son verdict : le modele ne voit pas les URL."""

    verdicts: list[Verdict]


# Verdict pose sur un candidat qu'on n'a pas pu juger (telechargement en echec,
# juge muet). Neutre expres : il passe DEVANT une affiche averee -- ne pas savoir
# vaut mieux que savoir que c'est une affiche -- et DERRIERE une photo reconnue.
VERDICT_INCONNU = Verdict(affiche=False, sujet="autre", note=50)


SYSTEM_PROMPT = """\
Tu tries des images pour en faire le FOND d'une cover Instagram. Un titre en \
gros caracteres sera superpose par-dessus.

Pour CHAQUE image fournie, dans l'ordre, rends un verdict :

- affiche (true/false) : true si l'image est une AFFICHE, un flyer, une \
tracklist, une pochette ou un visuel graphique charge de TEXTE — line-up de \
festival, grille de noms d'artistes, dates et lieux composes, logo centre sur \
un aplat, artwork de sortie portant le titre. true aussi pour une capture \
d'ecran, un post texte ou une story mise en page. false pour une PHOTOGRAPHIE \
— concert, foule, club, materiel, portrait, paysage — meme si un logo, un \
ecran LED ou une banderole y porte quelques mots : ce qui compte est de savoir \
si l'image EST un support graphique ou une photo prise sur le vif.
- sujet : "scene" pour un moment vivant (concert, DJ set, foule, club, \
festival), "portrait" pour une ou plusieurs personnes posant ou photographiees \
de pres, "autre" pour tout le reste (lieu vide, objet, paysage, abstrait).
- note (0-100) : la qualite de cette image COMME FOND derriere un gros titre. \
Monte pour une image nette, contrastee, avec une zone calme ou du texte peut se \
poser. Descends pour une image floue, minuscule, tres chargee, presque noire ou \
presque blanche. Une affiche ne merite jamais plus de 20.

Rends exactement autant de verdicts que d'images, dans le meme ordre.\
"""


def _octets(url: str) -> bytes | None:
    """Le contenu d'un candidat, qu'il soit une copie locale ou une URL reseau.

    Les copies locales (`file://`) sont les photos d'artistes deja rapatriees par
    `choix_visuels` : les relire sur disque evite de re-solliciter un CDN dont
    l'URL signee a pu expirer.
    """
    locale = lire_fichier_cache(url)
    if locale is not None:
        return locale
    if url.startswith("file://"):
        return None
    octets, _ = telecharger_media(url)
    return octets


def reduire(octets: bytes, taille: int | None = None) -> bytes:
    """Une vignette JPEG carree-max de `taille` px. Leve si l'image est illisible.

    Reduire n'est pas une optimisation accessoire : le cout d'une image en jetons
    est proportionnel a sa SURFACE (largeur x hauteur / 750). Une photo de presse
    en 2000x1333 coute ~3 550 jetons, sa reduction a 512 px ~230 — et la question
    posee (affiche ou photo ?) se tranche parfaitement a cette taille.
    """
    from PIL import Image

    taille = taille or settings.illustration_taille_px
    image = Image.open(io.BytesIO(octets))
    image = image.convert("RGB")
    image.thumbnail((taille, taille))
    tampon = io.BytesIO()
    image.save(tampon, format="JPEG", quality=70)
    return tampon.getvalue()


def make_claude_juge(client: anthropic.Anthropic | None = None):
    """Juge par defaut : UN appel vision pour toutes les images d'un coup."""
    if client is None:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def juger(images: list[bytes]) -> list[Verdict]:
        contenu: list[dict] = []
        for rang, jpeg in enumerate(images, start=1):
            contenu.append({"type": "text", "text": f"Image {rang} :"})
            contenu.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(jpeg).decode("ascii"),
                    },
                }
            )
        reponse = client.messages.parse(
            model=settings.visuels_model,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": contenu}],
            output_format=Verdicts,
        )
        sortie = reponse.parsed_output
        if sortie is None or len(sortie.verdicts) != len(images):
            # Un verdict de moins et tout l'appariement glisse d'un rang : une
            # affiche prendrait la note d'une photo. On prefere ne rien savoir.
            raise ValueError(
                f"{len(sortie.verdicts) if sortie else 0} verdicts pour {len(images)} images"
            )
        return sortie.verdicts

    return juger


# --- Memoire des verdicts -------------------------------------------------


@dataclass(frozen=True)
class Cache:
    """Les verdicts connus, indexes par URL nettoyee, et les TAILLES REELES
    (largeur, hauteur) mesurees au meme moment que le juge decode l'image pour
    la reduire (`reduire`). Un champ separe et non un ajout a `Verdict` : ce
    dernier est aussi le SCHEMA envoye a Claude (`output_format=Verdicts`), et
    lui ajouter largeur/hauteur ferait deviner ces valeurs au modele au lieu de
    les mesurer nous-memes."""

    verdicts: dict[str, Verdict]
    tailles: dict[str, tuple[int, int]] = field(default_factory=dict)


def _chemin_cache() -> Path:
    return settings.illustration_cache_path


def lire_cache(maintenant: dt.datetime, chemin: Path | None = None) -> Cache:
    """Les verdicts et tailles encore valides. Jamais d'exception : un fichier
    corrompu ou absent vaut un cache vide, pas une composition perdue."""
    chemin = chemin or _chemin_cache()
    try:
        brut = json.loads(chemin.read_text(encoding="utf-8"))
        if brut.get("version") != CACHE_VERSION:
            return Cache({})
        limite = maintenant - dt.timedelta(days=settings.illustration_cache_jours)
        verdicts = {}
        for url, ligne in (brut.get("verdicts") or {}).items():
            pose = dt.datetime.fromisoformat(ligne["le"])
            if pose >= limite:
                verdicts[url] = Verdict.model_validate(
                    {cle: ligne[cle] for cle in ("affiche", "sujet", "note")}
                )
        tailles = {}
        for url, ligne in (brut.get("tailles") or {}).items():
            pose = dt.datetime.fromisoformat(ligne["le"])
            if pose >= limite:
                tailles[url] = (int(ligne["largeur"]), int(ligne["hauteur"]))
        return Cache(verdicts, tailles)
    except Exception as exc:  # noqa: BLE001 -- un cache illisible n'est qu'un cache vide
        logger.info("cache des verdicts d'illustration illisible (%s)", type(exc).__name__)
        return Cache({})


def ecrire_cache(cache: Cache, maintenant: dt.datetime, chemin: Path | None = None) -> None:
    """Ecriture ATOMIQUE (fichier temporaire + os.replace), comme le mode de
    publication : un passage interrompu ne laisse pas un JSON tronque que le
    passage suivant jetterait."""
    chemin = chemin or _chemin_cache()
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        contenu = {
            "version": CACHE_VERSION,
            "verdicts": {
                url: {**verdict.model_dump(), "le": maintenant.isoformat()}
                for url, verdict in cache.verdicts.items()
            },
            "tailles": {
                url: {"largeur": largeur, "hauteur": hauteur, "le": maintenant.isoformat()}
                for url, (largeur, hauteur) in cache.tailles.items()
            },
        }
        temporaire = chemin.with_suffix(chemin.suffix + ".tmp")
        temporaire.write_text(json.dumps(contenu, ensure_ascii=False), encoding="utf-8")
        os.replace(temporaire, chemin)
    except Exception as exc:  # noqa: BLE001 -- ne jamais faire echouer une composition
        logger.warning(
            "verdicts d'illustration non memorises (%s) : le prochain passage rejugera",
            type(exc).__name__,
        )


# --- Le choix -------------------------------------------------------------


def _rang(index: int, verdict: Verdict) -> tuple:
    """La cle de tri. Plus c'est grand, mieux c'est -- sauf `-index`, qui garde
    l'ordre historique comme dernier departage (le premier candidat gagne)."""
    return (not verdict.affiche, RANG_SUJET.get(verdict.sujet, 0), verdict.note, -index)


def choisir_illustration(
    candidats: list[str],
    *,
    juge=None,
    maintenant: dt.datetime | None = None,
    chemin_cache: Path | None = None,
) -> str | None:
    """Le candidat qui ressemble le moins a une affiche, ou None.

    None veut dire « je n'ai rien a dire » : l'appelant garde le premier
    candidat. C'est le cas quand il n'y a pas de choix a faire (0 ou 1
    candidat), quand la vision est coupee, et quand tout echoue.

    Si TOUS les candidats sont des affiches, on rend quand meme la moins mauvaise
    (la mieux notee). Ce n'est PAS le dernier mot : `eviter_l_affiche`, en aval,
    reprend ce fond, constate qu'il est une affiche, ouvre les reserves et, a
    defaut, part sur le fond Kinda. Ici on ne fait que classer.
    """
    try:
        return _choisir(candidats, juge, maintenant, chemin_cache)
    except Exception as exc:  # noqa: BLE001 -- jamais d'echec de composition pour un gout
        logger.warning(
            "choix d'illustration abandonne (%s) : premier candidat conserve",
            type(exc).__name__,
        )
        return None


def verdicts_memorises(
    urls: list[str],
    *,
    maintenant: dt.datetime | None = None,
    chemin_cache: Path | None = None,
) -> dict[str, Verdict]:
    """Ce que le juge a DEJA dit, lu dans le cache seul -- jamais un appel de plus.

    `verdicts_de` paie pour ce qu'il ne connait pas ; ici on ne paie rien, et une
    URL jamais jugee est simplement absente. C'est ce qu'il faut a
    `plafonner_affiches`, qui s'ajoute APRES le juge paye une fois par
    composition (`prejuger`) : lui donner `verdicts_de` ajouterait un second
    appel pour des images que la borne du lot avait ecartees.

    Ce que ca coute : ce qui tombe hors du lot (au-dela de
    `illustration_max_candidats`, ou juge lors d'une composition d'il y a plus de
    `illustration_cache_jours`) reste inconnu -- et une slide inconnue est gardee.
    """
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    connus = lire_cache(maintenant, chemin_cache).verdicts
    return {url: connus[url] for url in urls if url in connus}


def verdicts_de(
    urls: list[str],
    *,
    juge=None,
    maintenant: dt.datetime | None = None,
    chemin_cache: Path | None = None,
) -> dict[str, Verdict]:
    """Ce qu'on sait de chaque URL : le cache d'abord, un appel de vision pour
    les inconnues. Les URL qu'on n'a pas pu juger sont simplement ABSENTES du
    dictionnaire -- « je ne sais pas » ne se confond jamais avec un verdict.

    Extrait de `_choisir` pour servir aussi le fond de secours
    (`eviter_l_affiche`), qui a besoin du verdict lui-meme et pas seulement du
    classement. Un verdict deja memorise ne repaie donc rien, quel que soit
    l'appelant.

    Mesure au passage les dimensions REELLES de chaque image telechargee
    (`_dimensions`) et les memorise dans le meme cache (`Cache.tailles`) : c'est
    exactement le decodage que `reduire` fait deja pour la vignette envoyee au
    juge, donc le plancher de resolution du fond (`resolution_suffisante`) ne
    coute pas une requete de plus."""
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    cache = lire_cache(maintenant, chemin_cache)
    connus = dict(cache.verdicts)
    tailles = dict(cache.tailles)
    inconnus = [url for url in urls if url not in connus]
    if not inconnus:
        return {url: connus[url] for url in urls if url in connus}

    if juge is None:
        if not settings.illustration_vision_enabled or not settings.anthropic_api_key:
            logger.info("juge visuel indisponible : verdicts inconnus")
            return {url: connus[url] for url in urls if url in connus}
        juge = make_claude_juge()
    images, juges = [], []
    for url in inconnus:
        octets = _octets(url)
        if octets is None:
            logger.info("candidat de fond inaccessible : %s", url)
            continue
        taille = _dimensions(octets)
        if taille is not None:
            tailles[url] = taille
        try:
            images.append(reduire(octets))
        except Exception as exc:  # noqa: BLE001 -- une image illisible est sautee
            logger.info("candidat de fond illisible (%s) : %s", type(exc).__name__, url)
            continue
        juges.append(url)
    if images:
        for url, verdict in zip(juges, juge(images), strict=True):
            connus[url] = verdict
        ecrire_cache(Cache(connus, tailles), maintenant, chemin_cache)
    elif tailles != cache.tailles:
        # Aucune image n'a pu etre jugee (toutes illisibles) mais au moins une
        # taille a ete mesuree avant l'echec du decodage complet -- la garder
        # evite de retelecharger cette URL au prochain passage.
        ecrire_cache(Cache(connus, tailles), maintenant, chemin_cache)
    return {url: connus[url] for url in urls if url in connus}


def _dimensions(octets: bytes) -> tuple[int, int] | None:
    """Les dimensions REELLES (largeur, hauteur) d'une image telechargee, ou
    None si elle est illisible. Mesurees sur les octets bruts, AVANT la
    reduction a `illustration_taille_px` que `reduire` applique pour le juge --
    la vignette n'a plus la taille d'origine."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(octets)) as image:
            return image.size
    except Exception:  # noqa: BLE001 -- une taille non mesurable vaut "inconnue"
        return None


def tailles_de(
    urls: list[str],
    *,
    maintenant: dt.datetime | None = None,
    chemin_cache: Path | None = None,
) -> dict[str, tuple[int, int]]:
    """Les tailles DEJA mesurees, lues du cache seul -- jamais de requete ni de
    decodage de plus. Miroir de `verdicts_memorises` : sert au plancher de
    resolution du fond (`eviter_l_affiche`), qui lit une mesure prise par le
    meme telechargement que le juge d'affiches, jamais un second."""
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    connues = lire_cache(maintenant, chemin_cache).tailles
    return {url: connues[url] for url in urls if url in connues}


def resolution_suffisante(
    taille: tuple[int, int] | None, cible: tuple[int, int], seuil: float | None = None
) -> bool:
    """Vrai si une taille CONNUE suffit a remplir le gabarit `cible` sans depasser
    `seuil` (`cover_fond_upscale_max`) d'agrandissement.

    Mesure du 2026-09-21 (CLAUDE.md, 6 derniers posts publies) : nets a 1,05x et
    0,89x d'agrandissement, flous a partir de 2,13x. Le seuil retenu (2,2x)
    n'est PAS au milieu de cet ecart : il laisse volontairement passer
    l'og:image standard 1200x630 (2,14x), de loin l'illustration la plus
    frequente du pipeline -- voir le commentaire de `cover_fond_upscale_max`
    dans `app/config.py` pour l'arbitrage complet.

    Taille inconnue -> True : meme regle du doute que le juge d'affiches, une
    image ecartee par erreur appauvrit le post, et l'alternative ici est soit
    une reserve, soit le fond Kinda, jamais une meilleure information."""
    if taille is None:
        return True
    largeur, hauteur = taille
    if largeur <= 0 or hauteur <= 0:
        # Decodage aberrant : ne pas rejeter sur une valeur qui ne peut pas etre
        # vraie plutot que de la traiter comme un verdict fiable.
        return True
    cible_largeur, cible_hauteur = cible
    seuil = settings.cover_fond_upscale_max if seuil is None else seuil
    agrandissement = max(cible_largeur / largeur, cible_hauteur / hauteur)
    return agrandissement <= seuil


def _choisir(candidats, juge, maintenant, chemin_cache) -> str | None:
    retenus = [url for url in (url_propre(u) for u in candidats or []) if url][
        : settings.illustration_max_candidats
    ]
    if len(retenus) < 2:
        # Aucun choix a faire : ne jamais payer un appel pour confirmer l'unique
        # image dont on dispose, qu'elle soit une affiche ou non. Le fond de
        # secours (`eviter_l_affiche`) juge cette image-la separement, mais
        # seulement quand il a vraiment une photo a lui proposer.
        return None

    connus = verdicts_de(retenus, juge=juge, maintenant=maintenant, chemin_cache=chemin_cache)
    if not connus:
        # Aucun verdict du tout : vision coupee, images inaccessibles, juge muet.
        # « Je n'ai rien a dire », et l'appelant garde le premier candidat -- ce
        # n'est pas la meme chose que de les declarer tous equivalents.
        return None
    verdicts = [connus.get(url, VERDICT_INCONNU) for url in retenus]
    meilleur = max(range(len(retenus)), key=lambda i: _rang(i, verdicts[i]))
    if all(v.affiche for v in verdicts):
        logger.info(
            "tous les fonds candidats sont des affiches : %s passe la main a "
            "`eviter_l_affiche`",
            retenus[meilleur],
        )
    return retenus[meilleur]


# --- Le dernier recours : chercher ailleurs, sinon l'aplat Kinda ----------


@dataclass(frozen=True)
class Lot:
    """Une RESERVE de photos de remplacement, et d'ou elle vient.

    `photos` est une fonction sans argument qui rend des URL de photos
    RAPATRIEES EN LOCAL (cf. `choix_visuels.photos_du_compte` et
    `photos_des_artistes_cites`). Une fonction et non une liste : un lot ne doit
    rien couter -- ni lecture Graph, ni telechargement -- tant que le fond retenu
    n'est pas une affiche averee.
    """

    origine: str
    photos: Callable[[], list[str]]


@dataclass(frozen=True)
class Fond:
    """Le fond retenu, et d'ou il vient. L'origine est POSEE et non deduite : une
    fois le fond ecrit dans `slides[0].image_url`, plus rien ne dirait s'il vient
    de l'actu, du compte, d'un artiste ou de l'aplat Kinda -- et c'est
    precisement ce que le dashboard doit montrer."""

    url: str | None
    origine: str
    # Les autres VRAIES photos du lot retenu, la meilleure en tete (`url` y
    # figure en premier). Elles sont deja payees -- lecture du compte,
    # telechargement, jetons du juge -- et servent de SLIDES au carrousel : un
    # post qui n'aurait que l'affiche de l'actu ne montre jamais l'artiste
    # (post Noisia, 2026-09-18). Hors comparaison : deux fonds sont le meme
    # choix s'ils ont la meme URL et la meme origine, le reste est du bagage.
    photos: tuple[str, ...] = field(default=(), compare=False)


def eviter_l_affiche(
    fond: str | None,
    *lots: Lot | None,
    origine: str = ORIGINE_ARTICLE,
    verdicts=None,
    cible: tuple[int, int] | None = None,
    tailles=None,
    **options,
) -> Fond:
    """JAMAIS une affiche en fond de cover, ni un fond trop petit pour le gabarit.
    Chercher ailleurs, sinon l'aplat Kinda.

    Demande de l'utilisateur, 2026-09-18, sur le post des Transmusicales : « pour
    le post des Trans c'est toujours une affiche en background sur la premiere
    slide. Si tu trouves pas, va trouver une belle photo sur le compte Instagram
    de l'info en question. » Puis, apres une troisieme republication du meme
    post : « ya toujours l'affiche en fond !! ».

    La premiere version gardait « la moins mauvaise des affiches » quand aucune
    reserve ne donnait mieux. C'etait le defaut : une affiche moins chargee reste
    une affiche, illisible sous l'accroche et redondante avec elle. La regle est
    donc devenue absolue -- MIEUX VAUT LE FOND KINDA QU'UNE AFFICHE.

    `choisir_illustration` ECARTE deja une affiche quand il existe une photo a
    lui preferer parmi les medias de l'actu. On n'arrive ici que lorsqu'il n'y en
    a pas : le compte du festival a publie un carrousel d'affiches de line-up.

    **Le plancher de resolution (2026-09-21)** recoit exactement le meme
    traitement qu'une affiche : mesure sur les 6 derniers posts publies
    (CLAUDE.md), un post dont le fond n'avait qu'une og:image de 564x345 est
    parti agrandi 3,91x, flou a l'oeil nu. `cible` (la taille du gabarit --
    1080x1350 en post, 1080x1920 en reel) est fourni par l'appelant, qui seul
    connait le format publie ; a `None` (defaut, compositions anterieures a
    cette regle), aucune verification de resolution n'a lieu.

    Les `lots` sont ouverts DANS L'ORDRE, et seulement tant qu'aucun n'a donne :
    le compte de l'actu d'abord (c'est son actu), les artistes suivis cites
    ensuite. On s'arrete au premier lot qui offre une photo NI affiche NI trop
    petite.

    Quatre issues : le fond d'origine (ni affiche ni trop petit, ou l'un des
    deux n'a pas pu etre etabli -- ne pas savoir n'est pas savoir) ; la
    meilleure photo assez grande du premier lot qui en offre une ; le fond
    Kinda ; et jamais d'exception -- une panne du juge laisse le fond
    d'origine, une panne d'un lot laisse le fond Kinda, puisque le defaut du
    fond, lui, est avere.
    """
    if not fond:
        return Fond(fond, origine)
    rendre = verdicts or (lambda urls: verdicts_de(urls, **options))
    mesurer = tailles or (lambda urls: tailles_de(urls, **options))
    try:
        verdict = rendre([fond]).get(fond)
    except Exception as exc:  # noqa: BLE001 -- jamais d'echec de composition pour un gout
        logger.warning("fond non juge (%s) : fond d'origine conserve", type(exc).__name__)
        return Fond(fond, origine)
    est_affiche = verdict is not None and verdict.affiche
    trop_petit = cible is not None and not _assez_grand(fond, cible, mesurer)
    if not est_affiche and not trop_petit:
        # Une photo assez grande, ou un defaut qu'on n'a pas pu etablir. Ne pas
        # savoir n'est pas savoir que le fond est mauvais : on ne depense rien
        # et le fond reste. C'est aussi le comportement quand la vision est
        # coupee ou que `cible` n'est pas fourni.
        return Fond(fond, origine)
    for lot in lots:
        candidat = _meilleur_du_lot(lot, rendre, cible, mesurer)
        if candidat is not None:
            image, photos = candidat
            logger.info("fond de cover remplace par une photo (%s) : %s", lot.origine, image)
            return Fond(image, lot.origine, photos)
    raison = "une affiche" if est_affiche else "trop petit pour le gabarit"
    logger.info("aucune photo trouvee, le fond retenu est %s : cover sur le fond Kinda", raison)
    return Fond(FOND_KINDA, ORIGINE_KINDA)


def _assez_grand(url: str, cible: tuple[int, int], mesurer) -> bool:
    """`resolution_suffisante`, mais qui ne leve jamais : une panne de mesure
    (fonction injectee cassee) vaut « on ne sait pas », jamais un rejet."""
    try:
        taille = mesurer([url]).get(url)
    except Exception:  # noqa: BLE001 -- ne pas savoir vaut mieux que rejeter a tort
        return True
    return resolution_suffisante(taille, cible)


def _meilleur_du_lot(lot, rendre, cible, mesurer) -> tuple[str, tuple[str, ...]] | None:
    """La photo du lot a la fois NI affiche NI trop petite, ou None. Les autres
    photos du lot (`Fond.photos`, pour les slides) restent dans leur ordre de
    classement, la retenue simplement placee en tete -- l'invariant de `Fond`
    (« `url` y figure en premier »)."""
    photos = _photos_du_lot(lot, rendre)
    if not photos:
        return None
    if cible is None:
        return photos[0], photos
    try:
        connues = mesurer(list(photos))
    except Exception:  # noqa: BLE001 -- ne pas savoir vaut mieux que rejeter a tort
        connues = {}
    for candidat in photos:
        if resolution_suffisante(connues.get(candidat), cible):
            reste = tuple(p for p in photos if p != candidat)
            return candidat, (candidat, *reste)
    return None


def classer_photos(candidats: list[str], rendre, *, garder_inconnues: bool = False) -> tuple[str, ...]:
    """Les VRAIES photos d'une liste, la meilleure en tete, les affiches otees.

    `garder_inconnues` dit quoi faire d'un candidat que le juge n'a pas su noter,
    et la reponse depend de CE QU'ON A EN FACE. Pour une reserve de fond
    (`_photos_du_lot`), l'alternative est le fond Kinda, qui est sur : on ne lui
    prefere qu'une image dont le juge a dit qu'elle n'est pas une affiche.
    Pour le dernier recours du web, l'alternative est de SAUTER L'ACTU : une
    image non jugee (vision coupee, quota) vaut alors mieux que rien.
    """
    if not candidats:
        return ()
    connus = rendre(candidats)
    retenues = [
        url
        for url in candidats
        if (url in connus and not connus[url].affiche) or (garder_inconnues and url not in connus)
    ]
    if not retenues:
        return ()
    classees = sorted(
        enumerate(retenues),
        key=lambda paire: _rang(paire[0], connus.get(paire[1], VERDICT_INCONNU)),
        reverse=True,
    )
    return tuple(url for _, url in classees)


def meilleures_photos(
    candidats, *, verdicts=None, garder_inconnues: bool = False, **options
) -> tuple[str, ...]:
    """`classer_photos`, avec le juge par defaut et la garantie de ne jamais
    lever -- l'entree publique pour qui n'a pas de `Lot` a ouvrir."""
    candidats = [url for url in (candidats or []) if url]
    rendre = verdicts or (lambda urls: verdicts_de(urls, **options))
    try:
        return classer_photos(candidats, rendre, garder_inconnues=garder_inconnues)
    except Exception as exc:  # noqa: BLE001 -- jamais d'echec de composition pour un gout
        logger.warning("classement des photos abandonne (%s)", type(exc).__name__)
        return tuple(candidats) if garder_inconnues else ()


def _photos_du_lot(lot: Lot | None, rendre) -> tuple[str, ...]:
    """Les VRAIES photos d'un lot, la meilleure en tete -- vide si le lot est
    absent, vide, tout en affiches, ou en panne (un lot qui casse prive d'une
    alternative ; il ne rend pas l'affiche publiable pour autant).

    Toutes et pas seulement la meilleure : la premiere fait le fond, les
    suivantes font des slides (cf. `Fond.photos`). Elles sont deja rapatriees et
    deja jugees -- les rendre ne coute plus rien.

    Un candidat qu'on n'a pas su juger n'est JAMAIS retenu : c'est la difference
    avec le classement de `choisir_illustration`, ou l'inconnu passe devant une
    affiche averee. Ici l'alternative est le fond Kinda, qui est sur -- on ne lui
    prefere qu'une image dont le juge a dit qu'elle n'est pas une affiche."""
    if lot is None:
        return ()
    try:
        candidats = [url for url in (lot.photos() or []) if url][
            : max(1, settings.fond_secours_max_photos)
        ]
        photos = classer_photos(candidats, rendre)
        if candidats and not photos:
            logger.info("la reserve « %s » n'offre que des affiches", lot.origine)
        return photos
    except Exception as exc:  # noqa: BLE001 -- jamais d'echec de composition pour un gout
        logger.warning(
            "reserve « %s » abandonnee (%s)",
            getattr(lot, "origine", "?"),
            type(exc).__name__,
        )
        return ()


# --- Une carte d'annonce dans le carrousel, jamais sept --------------------

# Combien d'affiches un carrousel a le droit de porter. Un (utilisateur,
# 2026-09-19, post du 18/09 13h10 sur le programme des Trans Musicales) : les
# slides 2 a 8 etaient la carte d'annonce du festival, declinee sept fois dans SA
# charte graphique. Deux defauts en un : le post devient le carrousel du
# festival avec une cover Kinda posee devant -- l'intermediaire dont le projet
# veut sortir --, et sept slides d'affilee ne ressemblent plus a Kinda.
# L'alternative etait de les refuser toutes ; l'utilisateur a tranche pour une :
# un visuel d'annonce officielle a sa place dans le post, sept n'en ont pas.
AFFICHES_MAX = 1


def prejuger(actifs: list[dict] | None, *, verdicts=None, **options) -> dict[str, Verdict]:
    """Juge D'UN COUP tout ce que le post peut montrer, et memorise les verdicts.

    C'est l'appel paye de la composition, et il porte desormais sur TOUS les
    visuels du post -- pas seulement sur les candidats du fond. La premiere
    version du plafond ne s'appuyait que sur le lot du fond : sur les sept cartes
    d'annonce des Trans Musicales, trois etaient jugees, quatre inconnues donc
    gardees -- cinq cartes publiees la ou l'utilisateur en voulait une.

    Un appel de plus n'est pas paye pour autant : les verdicts sont ecrits dans
    le cache par URL, et `choisir_illustration` comme `eviter_l_affiche` les y
    retrouvent gratuitement juste apres. On elargit le LOT juge, on n'ajoute
    jamais un second appel -- c'est la regle de cout du module.

    Borne : `illustration_max_candidats` (10, la taille d'un carrousel Meta).
    Au-dela, on paierait pour juger des images que le post ne publiera jamais ;
    ce qui tombe hors du lot reste inconnu, donc garde en slide.

    Ne leve jamais : sans verdict, tout garde le comportement d'avant.
    """
    candidats = candidats_de_fond(actifs)[: settings.illustration_max_candidats]
    if not candidats:
        return {}
    rendre = verdicts or (lambda urls: verdicts_de(urls, **options))
    try:
        return rendre(candidats)
    except Exception as exc:  # noqa: BLE001 -- jamais d'echec de composition pour un gout
        logger.warning(
            "visuels non juges (%s) : aucun verdict pour cette composition",
            type(exc).__name__,
        )
        return {}


def lecture_memorisee(connus: dict[str, Verdict], **options):
    """Une source de verdicts qui ne paie RIEN : ce qui vient d'etre juge, puis le
    cache. Le frais prime -- il reste disponible meme quand le cache n'a pas pu
    s'ecrire, et c'est ce qui rend le plafond independant d'un disque en panne."""

    def rendre(urls: list[str]) -> dict[str, Verdict]:
        urls = list(urls)
        return {
            **verdicts_memorises(urls, **options),
            **{url: connus[url] for url in urls if url in connus},
        }

    return rendre


def plafonner_affiches(actifs: list[dict] | None, *, verdicts=None, **options) -> list[dict]:
    """Les actifs, avec au plus `AFFICHES_MAX` affiche averee parmi eux.

    On garde la PREMIERE dans l'ordre des candidats. C'est une heuristique
    D'ORDRE et non un jugement sur le contenu : sur un carrousel d'annonce, la
    carte de tete porte le message principal (ici « TOUTE LA PROG REVELEE »),
    les suivantes le detaillent. Quand elle se trompe, le post garde une carte
    secondaire (un chiffre, une date) au lieu de l'annonce elle-meme -- on perd
    la meilleure des sept, jamais la seule slide du post.

    Ce qui n'est PAS une affiche ne bouge pas : les photos, les videos (une
    video n'est jamais une carte de texte, seule sa vignette en aurait l'air), et
    tout ce dont on n'a pas de verdict. Dans le doute on garde : une photo jetee
    par erreur appauvrit le post, une carte de trop ne fait que l'alourdir.

    Les verdicts sont ceux DEJA obtenus (`verdicts_memorises` par defaut, qui ne
    lit que le cache) : ce plafond ne paie aucun juge. `verdicts` n'existe que
    pour en injecter un dans les tests, comme pour `eviter_l_affiche`.

    Jamais d'exception -- une panne rend les actifs tels quels, donc le
    comportement d'avant.
    """
    actifs = list(actifs or [])
    if not actifs:
        return actifs
    rendre = verdicts or (lambda urls: verdicts_memorises(urls, **options))
    try:
        connus = rendre([url for url in (image_de(a) for a in actifs) if url])
    except Exception as exc:  # noqa: BLE001 -- jamais d'echec de composition pour un gout
        logger.warning("plafond des affiches abandonne (%s) : actifs conserves", type(exc).__name__)
        return actifs
    gardes: list[dict] = []
    affiches = 0
    for actif_ in actifs:
        image = image_de(actif_) if actif_.get("type") == PHOTO else None
        verdict = connus.get(image) if image else None
        if verdict is not None and verdict.affiche:
            affiches += 1
            if affiches > AFFICHES_MAX:
                logger.info("affiche en trop retiree du carrousel : %s", image)
                continue
        gardes.append(actif_)
    return gardes
