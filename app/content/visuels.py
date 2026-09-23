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
from app.content.urls import url_propre
from app.models import SourceCategorie, SourceType

ORIGINE_ARTICLE = "article"
ORIGINE_ARTISTE = "artiste"
# D'ou vient le FOND de la cover -- les deux ci-dessus, plus ces deux-la, qui
# n'ont de sens que pour un fond : le compte qui a publie l'actu, et le refus.
ORIGINE_COMPTE = "compte"
# Le tout dernier recours avant l'aplat : une image trouvee sur le WEB
# (2026-09-18, « ne saute pas l'actu, cherche une image sur le web en dernier
# recours »). Cf. `app/content/recherche_web.py`.
ORIGINE_WEB = "web"
ORIGINE_KINDA = "kinda"

# Le fond Kinda : l'aplat sombre du gabarit (`app/content/assets/fallback.jpg`),
# celui-la meme sur lequel une cover retombe quand une image est introuvable.
#
# Ce n'est PAS une URL, c'est un CHOIX, et il lui fallait une valeur explicite.
# Un fond absent (`None`) veut dire « ce post n'a pas de visuel » et le gel le
# refuse (`preparation.verifier_fond`) ; `FOND_KINDA` veut dire « on a cherche,
# tout etait une affiche, on assume l'aplat ». Sans cette distinction, la seule
# facon d'ecarter une affiche aurait ete de laisser le fond vide -- et la file
# aurait refuse le post a chaque passage jusqu'a MANQUE.
#
# La regle qu'il porte, posee par l'utilisateur le 2026-09-18 apres trois
# republications du post des Transmusicales (« ya toujours l'affiche en fond !! ») :
# MIEUX VAUT LE FOND KINDA QU'UNE AFFICHE. L'accroche y reste lisible -- c'est le
# fond pour lequel le gabarit a ete dessine.
#
# Le schema `kinda:` n'est ni http(s) ni `file://` : aucune requete ne part,
# aucun fichier n'est lu, et les gardes existantes le voient deja comme non
# telechargeable.
FOND_KINDA = "kinda:fond"


class VisuelDiffere(RuntimeError):
    """Un barreau de l'echelle du fond (reserve compte, artiste ou web) n'a
    PAS PU regarder -- panne de transport (quota Graph, reseau, budget de
    lecture epuise), jamais un jugement sur ce qu'il a trouve.

    Avant cette distinction, une panne de transport dans une reserve etait
    confondue avec « la reserve a regarde et n'a rien trouve », et les deux
    menaient au meme fond Kinda -- une issue SURE tant que l'aplat restait
    publiable. Devenu un SAUT, la confusion faisait sauter un article sur une
    simple coupure reseau, avec une raison fausse (« aucune image... ni sur le
    web » alors que le web n'avait jamais repondu).

    Cette exception reste interne a l'echelle du fond : `illustration.
    eviter_l_affiche` la capture PAR BARREAU et la traduit dans `Fond.differe`
    (jamais en la laissant sortir) -- son propre invariant « ne leve jamais »
    tient donc. `compose.resoudre_fond` lit ce champ et leve
    `choix_visuels.VisuelsDifferes` (un REPORT) au lieu de `VisuelIntrouvable`
    (un SAUT)."""


def origine_du_fond(actifs: list[dict] | None, fond: str | None) -> str | None:
    """L'origine de l'actif dont l'image sert de fond, ou None.

    Le fond est une URL choisie parmi les images des actifs : c'est le seul lien
    qui reste entre lui et l'actif d'ou il vient. On le retrouve pour SAVOIR
    d'ou vient le fond publie (article, artiste), information que le dashboard
    affiche et que le post garde."""
    for entree in actifs or []:
        if fond and image_de(entree) == fond:
            return entree.get("origine")
    return None


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
    # Les deux formes de chaque URL : les lignes posees avant le nettoyage a
    # l'ingestion portent encore `&amp;`, celles d'apres la forme propre -- une
    # slide ou un fond ne doit echapper a l'interdit sous aucune des deux.
    return {
        forme
        for m in article.media or []
        for url in (m.get("url"), m.get("preview_url"))
        if url
        for forme in (url, url_propre(url))
        if forme
    }


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
    """L'image fixe d'un media : la copie locale, sinon la photo, sinon la vignette.

    Les trois passent par `url_propre` : un `str(None)` ou un `&amp;` venu d'une
    ligne posee avant le nettoyage a l'ingestion ne doit pas passer pour un fond
    valable -- le rendu retomberait en silence sur l'image de secours."""
    if entree_media.get("fichier"):
        return url_propre(entree_media["fichier"])
    if entree_media.get("type") == PHOTO and entree_media.get("url"):
        return url_propre(entree_media["url"])
    return url_propre(entree_media.get("preview_url"))


def candidats_de_fond(actifs: list[dict] | None) -> list[str]:
    """Les images fixes exploitables comme fond, de la plus a la moins probable.

    Meme ordre que le premier choix historique : une VRAIE photo d'abord, la
    vignette d'une video seulement a defaut. Extrait en liste pour qu'un
    selecteur puisse CLASSER ces candidats au lieu de subir le premier.
    """
    actifs = actifs or []
    photos = [a for a in actifs if a.get("type") == PHOTO]
    vues: set[str] = set()
    urls: list[str] = []
    for entree in [*photos, *actifs]:
        image = image_de(entree)
        if image and image not in vues:
            vues.add(image)
            urls.append(image)
    return urls


def image_de_fond(actifs: list[dict] | None, selecteur=None) -> str | None:
    """Le fond de la cover : une VRAIE photo d'abord, la vignette d'une video
    seulement a defaut. Un carrousel ouvre souvent sur une video, et sa vignette
    est une image de secours -- l'ancien `_fond_par_defaut` prenait deja la photo
    en premier.

    Sans `selecteur`, la fonction reste PURE (ni reseau, ni API) : la file et le
    gel l'appellent a chaque passage juste pour savoir si un fond existe.

    Avec `selecteur` (`app/content/illustration.py`, un appel Claude vision) : le
    candidat qui ressemble le moins a une affiche. Il ne peut que REORDONNER --
    une reponse hors liste, ou None, retombe sur le premier candidat, pour qu'un
    juge en panne ne fasse jamais perdre un fond valable.
    """
    candidats = candidats_de_fond(actifs)
    if not candidats:
        return None
    if selecteur is None:
        return candidats[0]
    choisi = selecteur(candidats)
    return choisi if choisi in candidats else candidats[0]


def fond_de_cover(post) -> str | None:
    """Le fond de la cover d'un post, ou None s'il n'en a pas d'exploitable.

    LE point de lecture unique de `slides[0].image_url` : le champ est editable au
    dashboard et des lignes de production portent le `str(None)` d'un ancien
    defaut. Chaque lecteur (file, gel, rendu) doit voir la meme chose -- sans quoi
    l'un refuse un post que l'autre publie sur l'image de secours du gabarit."""
    slides = getattr(post, "slides", None) or []
    return url_propre(slides[0].get("image_url")) if slides else None


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
    """Les comptes distincts a crediter, ou None. Un `compte` qui ne porte pas
    de `@` est un DOMAINE (`actif_du_web`, « ra.co ») : l'utilisateur a retire
    cette citation le 2026-09-21 (« retirer aussi le credit web »), le credit
    d'un compte reste seul obligatoire."""
    comptes = dict.fromkeys(
        a["compte"] for a in actifs or [] if a.get("compte") and a["compte"].startswith("@")
    )
    return " ".join(comptes) or None


def chemin_cache(url: str, extension: str = ".jpg") -> Path:
    """La copie locale d'une image d'artiste : les URL du CDN Instagram sont
    signees et expirent en quelques jours, la composition precede le gel de ~22 h."""
    # Cle sur l'URL NETTOYEE : sans cela, la meme image occuperait deux copies
    # selon qu'on vient d'une ligne echappee ou propre, et la copie posee depuis
    # l'une ne serait jamais retrouvee depuis l'autre.
    propre = url_propre(url) or url
    return settings.visuels_cache_dir / f"{hashlib.sha256(propre.encode()).hexdigest()[:32]}{extension}"


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
