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
     `illustration_taille_px`, dans UN SEUL appel -- ~1 400 jetons d'entree ;
  3. le verdict est memorise par URL (`illustration_cache_path`) : une
     recomposition, ou un second creneau sur le meme article, ne repaie rien.

Et quand il n'y a rien a choisir parce que TOUT est une affiche (le carrousel de
line-up d'un festival), `eviter_l_affiche` va chercher une photo sur le compte
qui a publie l'actu -- demande de l'utilisateur du 2026-09-18 sur le post des
Transmusicales. Ce chemin ne coute rien tant que le fond retenu n'est pas une
affiche averee : ni lecture de compte, ni telechargement, ni jeton.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from app.config import settings
from app.content.export import telecharger as telecharger_media
from app.content.urls import url_propre
from app.content.visuels import lire_fichier_cache

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
    """Les verdicts connus, indexes par URL nettoyee."""

    verdicts: dict[str, Verdict]


def _chemin_cache() -> Path:
    return settings.illustration_cache_path


def lire_cache(maintenant: dt.datetime, chemin: Path | None = None) -> Cache:
    """Les verdicts encore valides. Jamais d'exception : un fichier corrompu ou
    absent vaut un cache vide, pas une composition perdue."""
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
        return Cache(verdicts)
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
    (la mieux notee) : le chemin de repli vers une photo d'artiste suivi vit en
    amont, dans `choix_visuels`, et se decide avant la composition -- mieux vaut
    une cover sur une affiche qu'un creneau perdu.
    """
    try:
        return _choisir(candidats, juge, maintenant, chemin_cache)
    except Exception as exc:  # noqa: BLE001 -- jamais d'echec de composition pour un gout
        logger.warning(
            "choix d'illustration abandonne (%s) : premier candidat conserve",
            type(exc).__name__,
        )
        return None


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
    l'appelant."""
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    connus = dict(lire_cache(maintenant, chemin_cache).verdicts)
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
        try:
            images.append(reduire(octets))
        except Exception as exc:  # noqa: BLE001 -- une image illisible est sautee
            logger.info("candidat de fond illisible (%s) : %s", type(exc).__name__, url)
            continue
        juges.append(url)
    if images:
        for url, verdict in zip(juges, juge(images), strict=True):
            connus[url] = verdict
        ecrire_cache(Cache(connus), maintenant, chemin_cache)
    return {url: connus[url] for url in urls if url in connus}


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
            "tous les fonds candidats sont des affiches : on garde la moins mauvaise (%s)",
            retenus[meilleur],
        )
    return retenus[meilleur]


# --- Le dernier recours : une photo du compte qui a publie l'actu ----------


def eviter_l_affiche(fond: str | None, secours=None, *, verdicts=None, **options) -> str | None:
    """Remplacer un fond qui est encore une AFFICHE par une photo du compte.

    Demande de l'utilisateur, 2026-09-18, sur le post des Transmusicales : « pour
    le post des Trans c'est toujours une affiche en background sur la premiere
    slide. Si tu trouves pas, va trouver une belle photo sur le compte Instagram
    de l'info en question. »

    `choisir_illustration` ECARTE une affiche quand il existe une photo a lui
    preferer. Le cas des Trans est celui ou il n'y en a pas : le compte du
    festival a publie un carrousel d'affiches de line-up, les cinq candidats sont
    donc des affiches, et le juge rend la moins mauvaise. C'est ici qu'on va
    chercher ailleurs.

    `secours` est une fonction sans argument qui rend des URL de photos
    RAPATRIEES EN LOCAL (cf. `choix_visuels.photos_du_compte`). Elle n'est
    appelee QUE si le fond est une affiche averee : quand la premiere image est
    deja une bonne photo, rien n'est lu, rien n'est telecharge, rien n'est paye.

    Trois issues, dans l'ordre de preference : la meilleure photo du compte ; a
    defaut le fond d'origine (une affiche d'ailleurs ne vaut pas mieux que
    l'affiche de l'actu) ; et jamais d'exception -- la file continue avec ce
    qu'elle avait.
    """
    if not fond or secours is None:
        return fond
    rendre = verdicts or (lambda urls: verdicts_de(urls, **options))
    try:
        verdict = rendre([fond]).get(fond)
        if verdict is None or not verdict.affiche:
            # Une photo, ou un verdict qu'on n'a pas pu rendre. Ne pas savoir
            # n'est pas savoir que c'est une affiche : on ne depense rien.
            return fond
        # Une place est laissee au fond d'origine dans le lot juge, pour que la
        # comparaison porte sur les memes criteres que le choix initial.
        candidats = [url for url in (secours() or []) if url][
            : max(1, settings.illustration_max_candidats - 1)
        ]
        if not candidats:
            return fond
        connus = rendre([*candidats, fond])
        meilleur = max(
            range(len(candidats)),
            key=lambda i: _rang(i, connus.get(candidats[i], VERDICT_INCONNU)),
        )
        retenu = candidats[meilleur]
        if connus.get(retenu, VERDICT_INCONNU).affiche:
            logger.info("le compte source n'offre que des affiches : fond d'origine conserve")
            return fond
        logger.info("fond de cover remplace par une photo du compte source : %s", retenu)
        return retenu
    except Exception as exc:  # noqa: BLE001 -- jamais d'echec de composition pour un gout
        logger.warning(
            "fond de secours abandonne (%s) : fond d'origine conserve", type(exc).__name__
        )
        return fond
