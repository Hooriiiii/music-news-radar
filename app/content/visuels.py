"""Quels visuels Kinda a le droit d'utiliser, et d'ou ils viennent.

Decision de l'utilisateur du 2026-09-17 : l'INFO peut venir d'un compte d'actu,
ses images et videos JAMAIS (watermarks : reposter le visuel d'un media
concurrent est un plagiat visible). Les photos de presse des flux RSS
(og:image, media_content) restent autorisees.
"""

import datetime as dt
import hashlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.config import settings
from app.content.media import CLES, PHOTO
from app.models import SourceCategorie, SourceType

ORIGINE_ARTICLE = "article"
ORIGINE_ARTISTE = "artiste"


def origine_interdite(source) -> bool:
    """Vrai pour un compte Instagram de categorie media, et lui seul. `getattr` :
    des appelants passent des sources partielles (SimpleNamespace sans categorie)."""
    return (
        source is not None
        and getattr(source, "type", None) == SourceType.INSTAGRAM
        and getattr(source, "categorie", None) == SourceCategorie.MEDIA
    )


def visuels_autorises(article) -> list[dict]:
    """Les medias de l'article que Kinda peut montrer : tous, sauf ceux d'un
    compte media Instagram."""
    if article is None or origine_interdite(getattr(article, "source", None)):
        return []
    return list(getattr(article, "media", None) or [])


def urls_interdites(article) -> set[str]:
    """Les URL a ne jamais publier : celles des medias d'un compte media Instagram.
    Sert aux ceintures (slides choisies au dashboard, fond d'un brouillon ancien)."""
    if article is None or not origine_interdite(getattr(article, "source", None)):
        return set()
    return {url for m in article.media or [] for url in (m.get("url"), m.get("preview_url")) if url}


def raison_sans_artiste(article) -> str:
    if origine_interdite(getattr(article, "source", None)):
        return "visuels d'un compte media Instagram interdits, et aucun artiste suivi cite"
    return "aucune image, et aucun artiste suivi cite"


def actif(
    entree_media: dict,
    *,
    origine: str,
    compte: str | None = None,
    permalink: str | None = None,
    article_id: int | None = None,
    publie_le: dt.datetime | None = None,
    fichier: str | None = None,
) -> dict:
    """Une entree media de forme complete, plus d'ou elle vient. C'est ce que
    `posts.assets` garde : le dashboard montre l'origine, la legende en tire le
    credit, le gel lit la copie locale."""
    return {
        **{cle: entree_media.get(cle) for cle in CLES},
        "origine": origine,
        "compte": compte,
        "permalink": permalink,
        "article_id": article_id,
        "publie_le": publie_le.isoformat() if publie_le else None,
        "fichier": fichier,
    }


def image_de(entree_media: dict) -> str | None:
    """L'image fixe d'un media : la copie locale, sinon la photo, sinon la vignette."""
    if entree_media.get("fichier"):
        return entree_media["fichier"]
    if entree_media.get("type") == PHOTO and entree_media.get("url"):
        return entree_media["url"]
    return entree_media.get("preview_url")


def image_de_fond(actifs: list[dict] | None) -> str | None:
    """Le fond de la cover : une VRAIE photo d'abord, la vignette d'une video
    seulement a defaut. Un carrousel ouvre souvent sur une video, et sa vignette
    est une image de secours -- l'ancien `_fond_par_defaut` prenait deja la photo
    en premier."""
    actifs = actifs or []
    photos = [a for a in actifs if a.get("type") == PHOTO]
    return next((image for a in [*photos, *actifs] if (image := image_de(a))), None)


def a_une_image_autorisee(article) -> bool:
    """Vrai si l'article porte au moins une image fixe que Kinda peut publier.

    LE prédicat, unique par construction : la file (`store.a_une_image`) et le
    choix des visuels (`choix_visuels._propres`) doivent voir exactement la meme
    chose. Deux definitions voisines suffisaient a retenir un article dans la
    file (un `gif` ou un `type` absent avec vignette) puis a l'exclure a la
    composition, avec une raison fausse -- verrouille par un test-sentinelle
    parametre (`tests/test_visuels.py`)."""
    return any(image_de(m) for m in visuels_autorises(article))


def credit_des_actifs(actifs: list[dict] | None) -> str | None:
    comptes = dict.fromkeys(a["compte"] for a in actifs or [] if a.get("compte"))
    return " ".join(comptes) or None


def chemin_cache(url: str, extension: str = ".jpg") -> Path:
    """La copie locale d'une image d'artiste : les URL du CDN Instagram sont
    signees et expirent en quelques jours, la composition precede le gel de ~22 h."""
    return settings.visuels_cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()[:32]}{extension}"


def lire_fichier_cache(uri: str | None) -> bytes | None:
    """Le contenu d'un `file://` RESOLU dans le cache, sinon None. Le fond d'une
    cover est editable au dashboard : sans cette borne, il lirait n'importe quel
    fichier de la machine."""
    if not uri or not uri.startswith("file://"):
        return None
    chemin = Path(unquote(urlsplit(uri).path)).resolve()
    if not chemin.is_relative_to(settings.visuels_cache_dir.resolve()) or not chemin.is_file():
        return None
    return chemin.read_bytes()


def purger_cache(maintenant: dt.datetime) -> int:
    """Supprime les copies locales de plus de `visuels_cache_jours` et rend leur
    nombre. Une image ne sert que le temps d'un creneau (composition puis gel) :
    sans menage, le dossier grossit indefiniment. Jamais d'exception -- un
    passage planifie ne doit pas tomber sur un fichier recalcitrant."""
    dossier = settings.visuels_cache_dir
    if not dossier.is_dir():
        return 0
    limite = (maintenant - dt.timedelta(days=settings.visuels_cache_jours)).timestamp()
    supprimes = 0
    for chemin in dossier.iterdir():
        try:
            if chemin.is_file() and chemin.stat().st_mtime < limite:
                chemin.unlink()
                supprimes += 1
        except OSError:
            continue
    return supprimes
