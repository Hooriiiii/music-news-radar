"""Illustrer un article avec l'image que son éditeur désigne pour le partage.

57 % des articles arrivaient sans aucun média, et le déficit était concentré sur
les flux RSS — 19 % d'illustration seulement, alors que ce sont eux qui portent
la matière éditoriale (Tsugi, DJ Mag, Mixmag, festivals). Or ces sites publient
tous une balise `og:image` : celle que Facebook, X et Instagram affichent quand
on partage le lien. Mesuré sur 30 articles de 4 sources : **100 % de couverture**.

C'est pour cela qu'on ne fait PAS de recherche web. `og:image` est l'image que
l'éditeur a lui-même choisie pour illustrer CET article ; une recherche ramènerait
au mieux la même, au pire celle d'un autre événement.

Le module ne lève jamais : illustrer est un confort, et un site injoignable ne
doit pas casser le pipeline qui l'appelle.
"""

import datetime as dt
import io
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.media import PHOTO, entree
from app.models import Article

logger = logging.getLogger(__name__)

# ASCII pur : httpx refuse un en-tête accentué AVANT même d'émettre la requête
# (contrainte relevée sur le terrain, cf. CLAUDE.md).
ENTETES = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) KindaBot/1.0"
    )
}

# Deux ordres d'attributs : `property` avant `content` et l'inverse. Une regex
# rigide raterait la moitié des sites -- l'ordre n'est contraint par aucune norme.
_MOTIFS = tuple(
    re.compile(motif % {"cle": cle}, re.IGNORECASE)
    for cle in ("og:image", "twitter:image")
    for motif in (
        r'<meta[^>]+(?:property|name)=["\']%(cle)s["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']%(cle)s["\']',
    )
)

# Au-delà, la page n'est pas une page d'article -- inutile de la charger en
# entier pour y chercher une balise qui vit dans le <head>.
TAILLE_MAX = 2_000_000


# Ces liens ne menent PAS a l'article : Google News redirige vers sa page de
# consentement, qui n'a pas d'og:image. Verifie en vrai sur les 4 sources
# concernees. Une requete y est certaine d'echouer -- autant ne pas la faire, et
# compter le cas a part pour qu'il reste VISIBLE au lieu de se fondre dans les
# echecs reseau.
DOMAINES_SANS_ARTICLE = ("news.google.com",)


@dataclass
class IllustrationStats:
    illustres: int = 0
    echecs: int = 0
    ignores: int = 0


def image_de_partage(url: str, get=httpx.get) -> dict | None:
    """L'entrée média construite depuis `og:image`, ou None.

    Rend une entrée à la forme COMPLÈTE de `app/content/media.py` : les six clés
    toujours présentes, pour qu'une photo trouvée ici se lise exactement comme
    une photo venue de X.
    """
    try:
        reponse = get(
            url, timeout=settings.illustration_timeout,
            follow_redirects=True, headers=ENTETES,
        )
    except Exception as exc:
        logger.debug("Illustration : %s injoignable (%s)", url, type(exc).__name__)
        return None

    if reponse.status_code >= 400:
        return None
    # Ne pas passer une regex sur un PDF ou une image de 8 Mo.
    if "html" not in reponse.headers.get("content-type", ""):
        return None

    for motif in _MOTIFS:
        trouve = motif.search(reponse.text[:TAILLE_MAX])
        if trouve:
            # Résolue sur la page : beaucoup de sites servent une og:image
            # relative, inexploitable telle quelle.
            resolue = urljoin(url, trouve.group(1).strip())
            return entree(PHOTO, _plus_grande_variante(resolue, get=get))
    return None


# --- Variante pleine resolution (2026-09-21) -------------------------------

# Parametres de redimensionnement CDN releves chez les editeurs -- pas une
# liste exhaustive, mais suffisante pour le cas mesure (WordPress `?w=564`).
PARAMETRES_TAILLE = ("w", "width", "h", "height", "resize", "fit", "s")

CACHE_VARIANTES_VERSION = 1


@dataclass(frozen=True)
class _CacheVariantes:
    """La variante retenue par URL d'origine -- l'URL elle-meme quand elle n'a
    pas de parametre connu, ou quand la variante sans parametre n'etait pas
    reellement plus grande."""

    retenues: dict[str, str]


def _sans_parametres_taille(url: str) -> str | None:
    """`url` sans ses parametres de redimensionnement CONNUS, ou None si aucun
    n'y figure -- rien a gagner a retenter dans ce cas."""
    decoupe = urlsplit(url)
    if not decoupe.query:
        return None
    query = parse_qsl(decoupe.query, keep_blank_values=True)
    filtree = [(cle, valeur) for cle, valeur in query if cle.lower() not in PARAMETRES_TAILLE]
    if len(filtree) == len(query):
        return None
    return urlunsplit(
        (decoupe.scheme, decoupe.netloc, decoupe.path, urlencode(filtree), decoupe.fragment)
    )


def _valeurs_declarees(url: str) -> tuple[int | None, int | None]:
    """Les valeurs NUMERIQUES de largeur/hauteur portees par l'URL, si l'editeur
    les y a mises (`?w=564`). Un parametre sans valeur numerique exploitable
    (`?fit=crop`) rend None : rien a comparer, donc rien a affirmer."""
    query = dict(parse_qsl(urlsplit(url).query))

    def _valeur(cles: tuple[str, ...]) -> int | None:
        for cle in cles:
            valeur = query.get(cle)
            if valeur and valeur.isdigit():
                return int(valeur)
        return None

    return _valeur(("w", "width")), _valeur(("h", "height"))


def _chemin_cache_variantes() -> Path:
    return settings.illustration_variante_cache_path


def _lire_cache_variantes(maintenant: dt.datetime, chemin: Path | None = None) -> _CacheVariantes:
    chemin = chemin or _chemin_cache_variantes()
    try:
        brut = json.loads(chemin.read_text(encoding="utf-8"))
        if brut.get("version") != CACHE_VARIANTES_VERSION:
            return _CacheVariantes({})
        limite = maintenant - dt.timedelta(days=settings.illustration_variante_cache_jours)
        retenues = {}
        for url, ligne in (brut.get("retenues") or {}).items():
            pose = dt.datetime.fromisoformat(ligne["le"])
            if pose >= limite:
                retenues[url] = ligne["url"]
        return _CacheVariantes(retenues)
    except Exception as exc:  # noqa: BLE001 -- un cache illisible n'est qu'un cache vide
        logger.info("cache des variantes d'illustration illisible (%s)", type(exc).__name__)
        return _CacheVariantes({})


def _ecrire_cache_variantes(
    cache: _CacheVariantes, maintenant: dt.datetime, chemin: Path | None = None
) -> None:
    """Ecriture ATOMIQUE (fichier temporaire + os.replace), meme mecanique que
    `app/content/illustration.py::ecrire_cache`."""
    chemin = chemin or _chemin_cache_variantes()
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        contenu = {
            "version": CACHE_VARIANTES_VERSION,
            "retenues": {
                url: {"url": retenue, "le": maintenant.isoformat()}
                for url, retenue in cache.retenues.items()
            },
        }
        temporaire = chemin.with_suffix(chemin.suffix + ".tmp")
        temporaire.write_text(json.dumps(contenu, ensure_ascii=False), encoding="utf-8")
        os.replace(temporaire, chemin)
    except Exception as exc:  # noqa: BLE001 -- ne jamais faire echouer une illustration
        logger.warning(
            "variante d'illustration non memorisee (%s) : le prochain passage retentera",
            type(exc).__name__,
        )


def _plus_grande_variante(
    url: str, get, *, maintenant: dt.datetime | None = None, chemin_cache: Path | None = None
) -> str:
    """L'URL de l'image, remplacee par sa variante PLEINE RESOLUTION quand le CDN
    en sert une plus petite par parametre ET que la variante sans ce parametre
    est REELLEMENT plus grande une fois telechargee et decodee.

    Mesure du 2026-09-21 (CLAUDE.md) : l'og:image d'un WordPress porte souvent
    `?w=564` (564x345, 50 Ko) alors que la meme image sans parametre pese
    386 Ko en 2156x1320 -- exactement la cause d'un fond de cover flou (post
    71, agrandi 3,91x pour remplir le gabarit). On ne le SUPPOSE pas : un
    editeur peut ignorer le parametre et servir la meme image quel que soit
    `w`, d'ou le telechargement et la comparaison des dimensions reellement
    decodees aux valeurs que l'URL declare.

    Memorisee par URL (meme mecanique que `illustration_cache_path`) : tant que
    l'og:image d'un article ne change pas, un passage ulterieur ne repaie pas
    la requete de verification. Le cout normal reste celui annonce par le
    plan : au plus UNE requete de plus, et seulement quand l'URL porte un
    parametre connu.
    """
    sans_params = _sans_parametres_taille(url)
    if sans_params is None:
        return url
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    cache = _lire_cache_variantes(maintenant, chemin_cache)
    if url in cache.retenues:
        return cache.retenues[url]
    retenue = _tenter_variante(url, sans_params, get)
    nouveau = {**cache.retenues, url: retenue}
    _ecrire_cache_variantes(_CacheVariantes(nouveau), maintenant, chemin_cache)
    return retenue


def _tenter_variante(url: str, sans_params: str, get) -> str:
    """Un echec (404, timeout, image illisible) garde l'URL d'origine, sans
    exception qui remonte : demander la pleine resolution est un confort."""
    try:
        reponse = get(
            sans_params, timeout=settings.illustration_timeout,
            follow_redirects=True, headers=ENTETES,
        )
        if reponse.status_code >= 400:
            return url
        from PIL import Image

        with Image.open(io.BytesIO(reponse.content)) as image:
            largeur, hauteur = image.size
    except Exception as exc:  # noqa: BLE001 -- confort, jamais une exception qui remonte
        logger.debug(
            "variante pleine resolution de %s injoignable (%s)", url, type(exc).__name__
        )
        return url
    declaree_largeur, declaree_hauteur = _valeurs_declarees(url)
    if declaree_largeur is None and declaree_hauteur is None:
        # Un parametre sans valeur numerique exploitable (`fit=crop`) : rien a
        # comparer. Mieux vaut garder l'original prouve que supposer plus grand.
        return url
    if declaree_largeur is not None and largeur <= declaree_largeur:
        return url
    if declaree_hauteur is not None and hauteur <= declaree_hauteur:
        return url
    return sans_params


def enrichir_articles(
    db: Session, get=httpx.get, limite: int | None = None
) -> IllustrationStats:
    """Illustre les articles PERTINENTS qui n'ont aucun média.

    Le filtre sur la pertinence n'est pas une optimisation cosmétique : un
    article de bruit ne sera jamais posté, lui chercher une image revient à payer
    une requête réseau pour un contenu destiné à la purge. Le seuil est celui du
    digest — la même barre que « mérite d'être vu ».
    """
    stmt = (
        select(Article)
        .where(Article.media.is_(None))
        .where(Article.relevance_score.is_not(None))
        .where(Article.relevance_score >= settings.illustration_min_relevance)
        .where(Article.duplicate_of_id.is_(None))
        .order_by(Article.relevance_score.desc())
    )
    plafond = limite if limite is not None else settings.illustration_max_par_run
    if plafond:
        stmt = stmt.limit(plafond)

    stats = IllustrationStats()
    for article in db.scalars(stmt).all():
        if any(d in article.url for d in DOMAINES_SANS_ARTICLE):
            stats.ignores += 1
            continue
        image = image_de_partage(article.url, get=get)
        if image is None:
            stats.echecs += 1
            continue
        article.media = [image]
        db.commit()
        stats.illustres += 1
    if stats.illustres or stats.echecs or stats.ignores:
        logger.info(
            "Illustration : %s illustrés, %s échecs, %s ignorés (lien sans article)",
            stats.illustres, stats.echecs, stats.ignores,
        )
    return stats
