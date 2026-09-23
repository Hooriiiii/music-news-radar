"""Chercher une image sur le WEB, en tout dernier recours.

Demande de l'utilisateur, 2026-09-18, apres un post publie sur l'aplat Kinda
parce que chaque image candidate etait une affiche de line-up : « ne saute pas
l'actu, cherche une image sur le web en dernier recours. »

Ou ce module se place. Le choix des visuels descend une echelle
(`choix_visuels.py`) : medias de l'article -> compte qui a publie l'actu ->
artistes suivis cites. Ce module est le BARREAU SUIVANT, et le dernier : apres
lui il n'y a que le fond Kinda. Il ne remplace rien -- il n'est ouvert que
lorsque tout le reste a rendu vide, ou n'a rendu que des affiches.

Comment. L'outil de recherche web de l'API Anthropic (`web_search`, un
server tool : Claude cherche, Anthropic execute, on paie la recherche) rend des
PAGES. On ne lit surtout PAS les URL que le modele recopie dans son texte -- la
sonde du 2026-09-18 l'a vu inventer deux liens absents des resultats -- mais les
blocs `web_search_tool_result`, qui portent l'URL et le titre reels. Puis on
extrait l'`og:image` de chaque page avec le meme code que l'illustration des
articles (`app/pipeline/illustrations.py`) : l'image que l'editeur designe
lui-meme pour le partage, deja eprouvee a 97 % de reussite en production.

Pas de filtrage de licence : decision explicite de l'utilisateur (2026-09-18,
« non mais c'est un projet perso donc c'est ok »). Le compte cite en revanche
TOUJOURS le site d'ou vient l'image, par son domaine, dans le credit de la
legende (« 📸 ra.co ») -- jamais coupe, comme le credit d'un compte Instagram.

Trois regles de cout : une seule recherche par requete, memorisee quelques jours
(`visuels_web_cache_path`) ; au plus `visuels_web_max_candidats` pages ouvertes ;
et rien du tout tant qu'un autre barreau de l'echelle a donne.

Et la regle de robustesse de tout ce qui touche aux visuels : ce module ne LEVE
JAMAIS -- SAUF `app.content.visuels.VisuelDiffere` (tour de correction 1/5,
2026-09-21, I-1), quand le RESEAU coupe la recherche elle-meme AVANT qu'elle
ait pu tourner. Cle absente, quota du modele de vision, page illisible ->
liste vide comme avant : ce sont des jugements ou des reglages, jamais des
pannes de transport. La distinction existe parce que le fond Kinda n'est plus
une issue publiable (Tache 5) : une reserve qui n'a pas pu regarder doit faire
REPORTER le creneau, pas conclure a l'absence d'image.
"""

import datetime as dt
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import anthropic

from app.config import settings
from app.content.urls import url_propre
from app.content.visuels import VisuelDiffere
from app.models import SourceCategorie
# Les mots vides FR + EN du rapprochement de titres : meme besoin ici, et une
# seconde liste finirait par diverger de celle-la.
from app.pipeline.cluster import _STOPWORDS as MOTS_VIDES
from app.pipeline.illustrations import image_de_partage

logger = logging.getLogger(__name__)

CACHE_VERSION = 1

# L'outil de recherche web server-side. Variante de base : `visuels_model` est
# Haiku 4.5, qui ne prend pas les variantes a filtrage dynamique.
OUTIL_RECHERCHE = {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}

# Domaines dont l'`og:image` n'est jamais la photo de l'actu : banques d'images
# (filigrane, page de vignettes), reseaux sociaux (mur de connexion, et la regle
# du 2026-09-17 interdit deja les visuels des comptes Instagram media).
DOMAINES_ECARTES = (
    "gettyimages.", "shutterstock.", "alamy.", "istockphoto.", "depositphotos.",
    "dreamstime.", "123rf.", "stock.adobe.com",
    "instagram.com", "facebook.com", "x.com", "twitter.com", "tiktok.com",
    "pinterest.", "youtube.com", "youtu.be", "news.google.com",
)

# Mots qui disent la discipline. Le premier qui matche gagne : un article de
# festival parle souvent aussi de clubs, l'inverse est rare.
_DISCIPLINES = (
    ("festival", ("festival", "open air", "plage", "edition")),
    ("dj set", ("dj set", "b2b", "club", "warehouse", "rave", "boiler room", "techno",
                "set", "residence")),
)
DISCIPLINE_DEFAUT = "concert"

# Articles de tete qu'on garde dans le nom d'un evenement (« Les Transmusicales »)
# mais qui ne suffisent jamais a le constituer.
_ARTICLES = frozenset({"le", "la", "les", "l", "un", "une", "des", "du", "de", "d"})
_MOT = re.compile(r"[\w'’.\-]+", re.UNICODE)
# Le canal, toujours en suffixe entre parentheses : « Transmusicales (IG) ».
_PARENTHESES = re.compile(r"\([^)]*\)")
# Combien de mots du titre au plus dans le tout dernier repli. Au-dela, la
# requete redevient une phrase, et une phrase ne ramene que des reprises.
MOTS_DE_TITRE = 6

# Les categories de source qui NOMMENT leur sujet : un compte de festival, de
# label, de club ou d'artiste parle de lui-meme. Un media parle des autres.
CATEGORIES_QUI_NOMMENT = (
    SourceCategorie.FESTIVAL,
    SourceCategorie.LABEL,
    SourceCategorie.CLUB,
    SourceCategorie.ARTISTE,
)


@dataclass(frozen=True)
class Candidat:
    """Une image trouvee sur le web, et la page qui la publie.

    `page` et `domaine` ne sont pas decoratifs : le domaine EST le credit publie
    sous le post (« 📸 ra.co »), et la page est ce qui permet de le verifier.
    """

    page: str
    titre: str
    image: str
    domaine: str


@dataclass(frozen=True)
class Attribution:
    """De quoi crediter une copie locale : le domaine, et la page d'origine."""

    domaine: str
    page: str


def domaine_de(url: str) -> str:
    """Le domaine d'une URL, sans `www.` -- c'est lui qui est publie en credit."""
    hote = (urlsplit(url or "").hostname or "").lower()
    return hote[4:] if hote.startswith("www.") else hote


def _ecarte(url: str) -> bool:
    hote = domaine_de(url)
    return not hote or any(motif in hote for motif in DOMAINES_ECARTES)


# --- La requete ------------------------------------------------------------


def indice_discipline(article) -> str:
    """« festival », « dj set » ou « concert » : sans cet indice, une recherche
    sur un nom d'artiste ramene surtout des pochettes et des portraits presse."""
    # La CATEGORIE de la source dit la discipline quand le texte ne la dit pas :
    # « Les Transmusicales devoilent leur programmation » ne porte pas le mot
    # festival, et c'en est pourtant un.
    source = getattr(article, "source", None)
    if getattr(source, "categorie", None) == SourceCategorie.FESTIVAL:
        return "festival"
    texte = f"{getattr(article, 'title', '') or ''} {getattr(article, 'summary', '') or ''}".lower()
    for discipline, mots in _DISCIPLINES:
        if any(mot in texte for mot in mots):
            return discipline
    return DISCIPLINE_DEFAUT


def _nom_d_evenement(titre: str) -> str | None:
    """Le debut du titre tant qu'il est en capitales : « Les Transmusicales » et
    non « Les Transmusicales devoilent leur programmation 2026 ».

    Une phrase entiere en requete ne ramene que des REPRISES de l'article, quand
    un nom propre ramene des photos de l'evenement lui-meme.
    """
    mots = _MOT.findall(titre or "")
    tete: list[str] = []
    for mot in mots:
        if not mot[:1].isupper() and mot.lower() not in _ARTICLES:
            break
        tete.append(mot)
        if len(tete) >= 5:
            break
    propres = [mot for mot in tete if mot.lower() not in _ARTICLES]
    if not propres:
        return None
    return " ".join(tete)


def nom_de_source(nom: str | None) -> str | None:
    """Le nom d'une source sans son canal entre parentheses, ou None s'il ne
    reste rien.

    Toutes les sources du projet portent leur canal en suffixe --
    « Transmusicales (IG) », « Tsugi (RSS) », « Festivals Bretagne (Bing News) ».
    Le garder chercherait « Transmusicales IG photo ».
    """
    propre = re.sub(r"\s+", " ", _PARENTHESES.sub(" ", nom or "")).strip()
    return propre or None


def _mots_du_titre(titre: str) -> str | None:
    """Les premiers mots PORTEURS du titre -- le tout dernier repli.

    Mieux vaut une requete imparfaite qu'aucune requete : sans elle l'article est
    saute, et c'est precisement ce que l'utilisateur a refuse. Mots vides et mots
    courts otes, ordre du titre garde.
    """
    mots = [
        mot
        for mot in _MOT.findall(titre or "")
        if len(mot) >= 4 and mot.lower() not in _ARTICLES and mot.lower() not in MOTS_VIDES
    ]
    # Les NOMS PROPRES du titre des qu'il y en a assez pour faire un sujet :
    # « Rencontres Trans Musicales » vaut infiniment mieux que « voile leve
    # programme 48es Rencontres Trans », qui n'est que la phrase raccourcie.
    propres = [mot for mot in mots if mot[:1].isupper()]
    retenus = propres if len(propres) >= 2 else mots
    return " ".join(retenus[:MOTS_DE_TITRE]) or None


def requete_pour(article) -> str | None:
    """La requete envoyee a la recherche web, ou None si l'article ne dit RIEN.

    Quatre reprises, dans l'ordre -- et ce qui compte est qu'on ne renonce qu'a
    la toute fin. La premiere version s'arretait a (2) et rendait None pour
    l'article 18260 (« Le voile se leve sur le programme des 48es Rencontres
    Trans Musicales », `mentioned_artists` vide) : le dernier recours ne partait
    jamais, sur le cas meme pour lequel il a ete ecrit.

    1. les ARTISTES CITES : un nom d'artiste ramene des photos de lui, ce qui est
       exactement ce qu'un fond de cover demande ;
    2. le nom propre en TETE DE TITRE (« Les Transmusicales devoilent... ») ;
    3. le NOM DE LA SOURCE, nettoye -- mais seulement d'un compte qui NOMME son
       sujet (`CATEGORIES_QUI_NOMMENT`) : « Tsugi festival photo » ramenerait le
       logo de Tsugi, jamais l'actu ;
    4. les mots porteurs du titre.

    Deterministe expres : c'est la cle du cache.
    """
    noms = [
        nom.strip()
        for nom in (getattr(article, "mentioned_artists", None) or [])
        if nom and nom.strip()
    ]
    titre = getattr(article, "title", "") or ""
    source = getattr(article, "source", None)
    nomme_son_sujet = getattr(source, "categorie", None) in CATEGORIES_QUI_NOMMENT
    sujet = (
        (" ".join(noms[:2]) if noms else None)
        or _nom_d_evenement(titre)
        or (nom_de_source(getattr(source, "name", None)) if nomme_son_sujet else None)
        or _mots_du_titre(titre)
    )
    if not sujet:
        return None
    return f"{sujet} {indice_discipline(article)} photo"


# --- Le chercheur ----------------------------------------------------------


def make_chercheur_claude(client: anthropic.Anthropic | None = None):
    """Chercheur par defaut : UN appel avec l'outil `web_search`.

    Rend les (url, titre) des blocs `web_search_tool_result`, JAMAIS les URL que
    le modele recopie dans sa reponse : la sonde du 2026-09-18 l'a vu ajouter
    deux liens (instagram.com, facebook.com) qui ne figuraient dans aucun
    resultat. Le texte du modele est une paraphrase, les blocs sont des faits.
    """
    if client is None:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def chercher(requete: str) -> list[tuple[str, str]]:
        reponse = client.messages.create(
            model=settings.visuels_model,
            max_tokens=500,
            tools=[OUTIL_RECHERCHE],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Cherche des pages recentes qui PUBLIENT une photographie de : "
                        f"{requete}. Des pages de presse musicale, de festival ou de "
                        "salle, pas des banques d'images ni des reseaux sociaux. "
                        "Reponds en une phrase."
                    ),
                }
            ],
        )
        pages: list[tuple[str, str]] = []
        for bloc in reponse.content:
            if getattr(bloc, "type", None) != "web_search_tool_result":
                continue
            contenu = getattr(bloc, "content", None)
            if not isinstance(contenu, list):
                # Un ERREUR de l'outil rend un objet, pas une liste (quota,
                # max_uses) : ce n'est pas une absence de resultat, mais ca se
                # traite pareil -- on n'a rien a proposer.
                logger.info("recherche web en erreur : %s", getattr(contenu, "error_code", contenu))
                continue
            for resultat in contenu:
                url = getattr(resultat, "url", None)
                if url:
                    pages.append((url, getattr(resultat, "title", "") or ""))
        return pages

    return chercher


# --- Le cache par requete --------------------------------------------------


def _chemin_cache() -> Path:
    return settings.visuels_web_cache_path


def _lire(maintenant: dt.datetime, chemin: Path) -> dict[str, tuple[str, list[Candidat]]]:
    """Les requetes encore valides, chacune avec SA date de pose -- une reecriture
    ne doit pas rajeunir les autres lignes. Jamais d'exception : un fichier
    corrompu ou absent vaut un cache vide, pas une composition perdue."""
    try:
        brut = json.loads(chemin.read_text(encoding="utf-8"))
        if brut.get("version") != CACHE_VERSION:
            return {}
        limite = maintenant - dt.timedelta(days=settings.visuels_web_cache_jours)
        connues = {}
        for requete, ligne in (brut.get("requetes") or {}).items():
            if dt.datetime.fromisoformat(ligne["le"]) >= limite:
                connues[requete] = (
                    ligne["le"],
                    [Candidat(**candidat) for candidat in ligne.get("candidats") or []],
                )
        return connues
    except Exception as exc:  # noqa: BLE001 -- un cache illisible n'est qu'un cache vide
        logger.info("cache de recherche web illisible (%s)", type(exc).__name__)
        return {}


def _ecrire(connues: dict[str, tuple[str, list[Candidat]]], chemin: Path) -> None:
    """Ecriture ATOMIQUE (temporaire + os.replace), comme les autres etats du
    projet : un passage interrompu ne laisse pas un JSON tronque."""
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        contenu = {
            "version": CACHE_VERSION,
            "requetes": {
                requete: {"le": pose, "candidats": [vars(c) for c in candidats]}
                for requete, (pose, candidats) in connues.items()
            },
        }
        temporaire = chemin.with_suffix(chemin.suffix + ".tmp")
        temporaire.write_text(json.dumps(contenu, ensure_ascii=False), encoding="utf-8")
        os.replace(temporaire, chemin)
    except Exception as exc:  # noqa: BLE001 -- ne jamais faire echouer une composition
        logger.warning("recherche web non memorisee (%s)", type(exc).__name__)


# --- Le choix --------------------------------------------------------------


def candidats_du_web(
    article,
    *,
    maintenant: dt.datetime,
    chercheur=None,
    extraire=None,
    chemin_cache: Path | None = None,
) -> list[Candidat]:
    """Des images du web pour cet article, au plus `visuels_web_max_candidats`.

    `maintenant` est OBLIGATOIRE et nomme : c'est l'horloge du cache (TTL des
    requetes), et tout le choix des visuels fait deja circuler la sienne -- une
    horloge implicite ferait diverger le TTL d'un passage a l'autre.

    Ne leve jamais, SAUF `app.content.visuels.VisuelDiffere` (tour de
    correction 1/5, 2026-09-21, I-1) : quand la recherche elle-meme n'a pas pu
    etre TENTEE (reseau, quota), ce n'est pas la meme chose qu'une recherche
    qui a tourne et n'a rien trouve -- confondre les deux faisait sauter un
    article sur une simple coupure reseau, avec une raison fausse. Cle absente,
    requete introuvable, page illisible : toujours une liste vide, jamais cette
    exception -- ce sont des conditions PERMANENTES ou des jugements, pas des
    pannes de transport.
    """
    if not settings.visuels_web_enabled:
        return []
    try:
        # `image_de_partage` est resolu ICI et non en valeur par defaut : liee a
        # la definition, elle rendrait le module intestable sans reseau.
        return _candidats(article, maintenant, chercheur, extraire or image_de_partage,
                          chemin_cache or _chemin_cache())
    except VisuelDiffere:
        raise
    except Exception as exc:  # noqa: BLE001 -- jamais d'echec de composition pour une image
        logger.warning("recherche web abandonnee (%s)", type(exc).__name__)
        return []


def _candidats(article, maintenant, chercheur, extraire, chemin) -> list[Candidat]:
    requete = requete_pour(article)
    if not requete:
        logger.info("recherche web : rien a chercher pour l'article %s", getattr(article, "id", "?"))
        return []
    connues = _lire(maintenant, chemin)
    if requete in connues:
        logger.info("recherche web deja faite pour « %s » : %s candidat(s)",
                    requete, len(connues[requete][1]))
        return connues[requete][1]

    if chercheur is None:
        if not settings.anthropic_api_key:
            return []
        chercheur = make_chercheur_claude()
    logger.info("recherche web (dernier recours) : « %s »", requete)
    try:
        pages = chercheur(requete)
    except Exception as exc:  # noqa: BLE001
        # La recherche n'a PAS PU etre tentee (reseau, quota de l'outil) : un
        # signal DIFFERENT d'une recherche qui tourne et ne trouve rien (I-1).
        logger.warning(
            "recherche web en panne (%s) : nouvelle tentative au prochain passage",
            type(exc).__name__,
        )
        raise VisuelDiffere(f"recherche web impossible ({type(exc).__name__})") from exc

    trouves: list[Candidat] = []
    vues: set[str] = set()
    for url, titre in pages:
        if len(trouves) >= settings.visuels_web_max_candidats:
            break
        if _ecarte(url) or url in vues:
            continue
        vues.add(url)
        try:
            media = extraire(url)
        except Exception as exc:  # noqa: BLE001 -- une page qui casse est sautee
            logger.info("page sans image exploitable (%s) : %s", type(exc).__name__, url)
            continue
        image = url_propre((media or {}).get("url"))
        if not image:
            continue
        trouves.append(Candidat(page=url, titre=titre, image=image, domaine=domaine_de(url)))

    connues[requete] = (maintenant.isoformat(), trouves)
    _ecrire(connues, chemin)
    return trouves


# --- Le credit -------------------------------------------------------------


def _cle(url: str) -> str:
    """La meme cle que `visuels.chemin_cache` : le nom du fichier local EST le
    hachage de l'URL d'origine. C'est ce qui permet de retrouver l'attribution
    d'une copie locale sans rien persister de plus."""
    return hashlib.sha256((url_propre(url) or url).encode()).hexdigest()[:32]


def attribution_de(
    uri: str | None, *, maintenant: dt.datetime, chemin_cache: Path | None = None
) -> Attribution | None:
    """D'ou vient une copie locale, ou None si elle ne vient pas du web.

    Le fond publie n'est qu'un `file://` : le credit doit pouvoir etre
    reconstitue a partir de lui seul, y compris apres un redemarrage -- d'ou la
    relecture du cache des requetes plutot qu'un index en memoire.
    """
    if not uri:
        return None
    cle = Path(urlsplit(uri).path).stem
    if not cle:
        return None
    for _, candidats in _lire(maintenant, chemin_cache or _chemin_cache()).values():
        for candidat in candidats:
            if _cle(candidat.image) == cle:
                return Attribution(candidat.domaine, candidat.page)
    return None


def credit_de(attribution: Attribution | None) -> str | None:
    """Le credit publie sous le post : « ra.co ». Le pictogramme (📸) est pose
    par `app/content/legende.py`, comme pour un compte Instagram."""
    return attribution.domaine if attribution and attribution.domaine else None
