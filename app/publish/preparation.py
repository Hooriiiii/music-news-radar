"""Le gel : tout ce qu'une publication va envoyer, fige sur disque avant l'heure.

A T-2h, on rend la cover, on telecharge les photos de l'actu et on les met aux
regles de Meta. Deux raisons de le faire en avance plutot qu'a l'heure : une
panne laisse le temps de reagir (remplacer l'article sur une cover qui deborde,
retenter sur un Chromium absent), et l'heure de publication ne depend plus de la lenteur d'un telechargement.

Ordre du carrousel : la cover d'abord, puis jusqu'a 9 medias de l'actu, dans
l'ordre -- photos en JPEG cadrees a 1080x1350 comme la cover (Instagram impose le
ratio du PREMIER element a tous les autres, une photo paysage revenait donc
coupee n'importe comment), VIDEOS en elements video 1080x1350 (meme cadrage sur
fond floute que le reel). Premier post reel (Charlotte de Witte,
article 17839) : 5 videos, et un carrousel parti avec la cover seule.

Six regles :
- JAMAIS un post qui ne MONTRE RIEN de l'actu. Ce qui etait interdit n'est pas
  « un seul element », c'est une cover sur l'aplat Kinda sans rien derriere
  (2026-09-18, article 17946 « Noisia » : l'unique image jugee affiche, ecartee
  du fond, absente des slides -- « la c'est n'importe quoi ! »). Une cover qui
  porte une VRAIE photo se suffit a elle-meme : le dossier part alors avec un
  seul fichier, et Meta publie un post IMAGE simple (`instagram.preparer_carrousel`).
  Sauf si les visuels existent et que seule leur RECUPERATION a echoue pour une
  raison passagere : on differe, on n'exclut pas ;
- la photo qui sert de FOND a la cover n'est JAMAIS republiee ensuite, meme si
  c'est le seul visuel (2026-09-19, post « Fred again.. au Palacio » : slide 2
  etait la photo de la cover en version nette -- « si c'est pour remettre la meme
  image en deuxieme slide, ca vaut pas le coup »). Une video dont c'est la
  vignette reste, elle : un clip n'est pas le meme asset que son image fixe ;
- une image que le cadrage 4:5 abimerait (trop petite, ou trop eloignee du
  format : `app.publish.images`) est simplement SAUTEE, jamais une erreur -- la
  raison est journalisee, et rappelee dans le message si le carrousel finit vide ;
- une video inutilisable (trop longue, telechargement ou sonde en echec, sortie
  hors specs) retombe sur sa vignette en photo : un media ne fait jamais echouer
  le gel ;
- les visuels d'un compte Instagram de categorie `media` ne sont jamais repris
  (watermarks, politique dans `app.content.visuels`) : ni en slide, ni en fond de
  cover -- un tel fond rend le gel impossible.

Une AFFICHE, elle, reste une slide honnete : elle n'est refusee qu'en FOND de
cover (`app.content.illustration`), jamais dans le carrousel -- c'est le visuel
de l'actu.
"""

import logging
import secrets
from pathlib import Path

from app.config import settings
from app.content import upscale as upscale_mod
from app.content import video as video_mod
from app.content.export import echec_transitoire, telecharger as telecharger_media
from app.content.compose import cible_gabarit
from app.content.illustration import dimensions_de, resolution_suffisante
from app.content.media import DUREE_MAX_SLIDE_MS, PHOTO, VIDEO
from app.content.urls import url_propre
from app.content.render import render_post
from app.content.teinte import palette_du_post
from app.content.visuels import (
    FOND_KINDA,
    ORIGINE_ARTICLE,
    chemin_cache,
    fond_de_cover,
    lire_fichier_cache,
    origine_interdite,
    urls_interdites,
    visuels_autorises,
)
from app.publish import manifeste as manifeste_mod
from app.publish.images import ImageEcartee, ImageInexploitable, cadrer_carrousel
from app.publish.manifeste import Figement

logger = logging.getLogger(__name__)

CARROUSEL_MAX = 10
MO = 1024 * 1024


class PreparationImpossible(RuntimeError):
    """Le post ne peut pas etre fige. Message affichable."""


class PreparationDifferee(RuntimeError):
    """Le gel ne peut pas se faire MAINTENANT, sans que l'article ni la machine
    soient en defaut (pause de yt-dlp, un telechargement par passage deja fait).
    Le runner inscrit la raison sur le creneau, sans exclure ni notifier : le gel
    est retente au passage suivant. Message affichable."""


class PreparationSautee(RuntimeError):
    """Tache 6 (2026-09-23) : le fond marque `a_agrandir` a la composition ne
    passe TOUJOURS PAS le plancher de nettete au gel -- soit l'outil de
    super-resolution est indisponible (binaire ou modeles absents, panne
    MACHINE), soit le resultat agrandi echoue encore
    `cover_fond_upscale_max`. C'est un SAUT, exactement comme
    `choix_visuels.VisuelIntrouvable` a la composition (Tache 5) : PAS une
    exclusion. Le runner doit liberer le creneau et poser la mise a l'ecart
    (`app.schedule.pause_visuels.marquer_saute`), jamais `file_exclu_raison`
    -- l'article peut avoir un meilleur candidat au prochain passage (compte
    source qui publie, cache web qui se renouvelle). Message affichable."""


def _cadrer(octets: bytes) -> bytes:
    """Une image du carrousel au format publie, seuils lus dans la configuration."""
    return cadrer_carrousel(
        octets,
        upscale_max=settings.publication_crop_upscale_max,
        perte_max=settings.publication_crop_perte_max,
    )


def _media_d_actif(actif_: dict) -> dict:
    """Un actif avec copie locale part en PHOTO depuis cette copie (l'URL du CDN
    Instagram aura expire) ; les autres gardent leur forme."""
    if actif_.get("fichier"):
        return {**actif_, "type": PHOTO, "url": actif_["fichier"]}
    return actif_


def liste_explicite(post) -> bool:
    """L'utilisateur a-t-il ARRETE la liste depuis l'editeur de relecture ?

    Marqueur pose dans `posts.variants["carrousel"]["explicite"]` par
    `PATCH /posts/{id}` (`carrousel_explicite`). Sur le POST et non sur le
    creneau, pour la raison qui gouverne toutes ses colonnes denormalisees :
    l'article est purge a deux jours et le post doit rester publiable sans lui.

    Ce qu'il change, et rien d'autre : les candidats ne viennent plus que des
    SLIDES. Sans lui, retirer un element de la liste ne servirait a rien -- le gel
    suivant le reprendrait depuis `posts.assets` ou depuis les medias de
    l'article, et l'edition serait silencieusement annulee. Tous les autres
    filtres (visuels interdits, fond jamais republie, doublons) restent
    appliques : ce sont des REGLES du projet, pas une source de candidats.
    """
    return bool(((getattr(post, "variants", None) or {}).get("carrousel") or {}).get("explicite"))


def medias_du_carrousel(post, article) -> list[dict]:
    """Photos et videos a publier apres la cover : slides, puis actifs du post
    (ou, pour un post anterieur aux actifs, medias autorises de l'article), sans
    doublon, sans le media qui sert de fond a la cover, jamais un visuel interdit.

    Liste ARRETEE par l'utilisateur (`liste_explicite`) : les slides SEULES, sans
    reprise des actifs ni des medias de l'article -- `_media_d_actif` leur est
    quand meme applique, une slide ecrite par l'editeur portant la copie locale
    d'un actif dans `fichier` (l'URL du CDN Instagram, elle, aura expire).
    """
    interdites = urls_interdites(article)
    candidats = [
        _media_d_actif(slide) for slide in post.slides[1:]
        if slide.get("kind") == "media" and slide.get("type") in (PHOTO, VIDEO)
        and not ({slide.get("url"), slide.get("preview_url")} & interdites)
    ]
    actifs = getattr(post, "assets", None)
    if liste_explicite(post):
        # Les deux reprises ci-dessous sont fermees d'un coup : c'est tout ce que
        # « liste arretee par l'utilisateur » veut dire.
        pass
    elif actifs is not None:
        # La policy est RE-VERIFIEE a la relecture : un actif pose quand la source
        # etait autorisee ne part pas si elle est passee en compte media depuis,
        # ni si son URL est devenue interdite. Ici, seule la source du
        # REPRESENTANT est connue (pas de session) ; celle de chaque MEMBRE du
        # groupe de doublons dont un actif est venu est relue par
        # `store.actifs_interdits`, que le gel appelle juste avant ce module
        # (`runner._brouillon_perime`) -- revue du 2026-09-21.
        interdite_maintenant = origine_interdite(getattr(article, "source", None))
        candidats += [
            _media_d_actif(a)
            for a in actifs
            if a.get("type") in (PHOTO, VIDEO)
            and not (a.get("origine") == ORIGINE_ARTICLE and interdite_maintenant)
            and not ({a.get("url"), a.get("preview_url")} & interdites)
        ]
    else:
        candidats += [m for m in visuels_autorises(article) if m.get("type") in (PHOTO, VIDEO)]
    fond = fond_de_cover(post)
    vues: set[str] = set()
    medias = []
    for media in candidats:
        cle = media.get("url") or media.get("preview_url")
        if not cle or cle in vues:
            continue
        vues.add(cle)
        # La photo du fond n'est pas repetee. Une video dont la vignette sert de
        # fond reste : un clip n'est pas le meme asset que son image fixe (elle
        # n'est ecartee que si elle retombe sur cette vignette, au gel).
        if fond and media.get("type") == PHOTO and media.get("url") == fond:
            continue
        medias.append(media)
    return medias


def candidats_editables(post, article) -> list[dict]:
    """Tout visuel AUTORISE qu'on peut proposer en ajout ou en remplacement.

    C'est la reserve de l'editeur de relecture, pas la liste publiee : ni le
    plafond de dix, ni la regle « le fond n'est jamais republie », ni la liste
    arretee ne s'y appliquent -- ce sont des regles du GEL, et l'utilisateur doit
    pouvoir remettre dans la liste un element que le gel avait ecarte.

    Ce qui s'y applique en revanche, et sans exception : la regle du 2026-09-17,
    jamais le visuel d'un compte Instagram de categorie `media`. Une reserve qui
    proposerait un visuel interdit le ferait rentrer par la porte de l'editeur.
    """
    interdites = urls_interdites(article)
    pool: list[dict] = []
    for actif in getattr(post, "assets", None) or []:
        if actif.get("type") in (PHOTO, VIDEO) and not (
            {actif.get("url"), actif.get("preview_url")} & interdites
        ):
            pool.append(_media_d_actif(actif))
    if article is not None and not origine_interdite(getattr(article, "source", None)):
        pool += [m for m in visuels_autorises(article) if m.get("type") in (PHOTO, VIDEO)]
    vus: set[str] = set()
    reserve = []
    for media in pool:
        cle = media.get("url") or media.get("preview_url")
        if not cle or cle in vus:
            continue
        vus.add(cle)
        reserve.append(
            {
                "kind": "media",
                "type": media.get("type"),
                "url": media.get("url"),
                "preview_url": media.get("preview_url"),
                "duration_ms": media.get("duration_ms"),
                "width": media.get("width"),
                "height": media.get("height"),
                "origine": media.get("origine") or ORIGINE_ARTICLE,
                "compte": media.get("compte"),
            }
        )
    return reserve


def _agrandir_fond_si_marque(
    post, fond: str, *, telecharger, agrandir, binaire_disponible
) -> str:
    """Tache 6 : agrandit `fond` et reecrit `post.slides` sur la copie
    agrandie s'il repasse le plancher -- rend `fond` INCHANGE si le
    brouillon n'est pas marque `a_agrandir` (immense majorite des posts) ou
    si le fond agrandi a deja disparu du cache local entre-temps (le gel
    suivant retentera).

    Leve `PreparationSautee` (jamais une exclusion) quand l'outil est
    indisponible ou que le resultat ne suffit toujours pas -- c'est le SAUT
    de la Tache 5, deplace ici parce que c'est ici, et seulement ici, qu'on
    sait si l'agrandissement a reussi. Leve `PreparationDifferee` (retente
    au passage suivant) sur un echec de TELECHARGEMENT du fond d'origine --
    une panne de reseau n'est pas une panne de l'outil de super-resolution,
    et ce n'est pas non plus une raison de sauter un candidat qui reste par
    ailleurs valable.
    """
    # `getattr` : les tests (et un brouillon compose a la main) passent
    # parfois un `Post`/objet partiel sans `variants` du tout -- absent vaut
    # « non marque », comme pour tout brouillon anterieur a la Tache 6.
    marque = bool((getattr(post, "variants", None) or {}).get("fond", {}).get("a_agrandir"))
    if not marque:
        return fond
    cible = cible_gabarit(post.template)
    if cible is None:
        # Gabarit inconnu : le plancher est desactive partout ailleurs pour
        # la meme raison (`compose.cible_gabarit`), donc rien a agrandir.
        return fond
    octets, raison = _octets_image(fond, telecharger)
    if octets is None:
        raise PreparationDifferee(
            f"impossible de relire le fond a agrandir ({raison}) : nouvelle tentative au prochain passage"
        )
    if resolution_suffisante(dimensions_de(octets), cible):
        # Le candidat a change entre la composition et le gel (recadrage
        # d'un editeur, cache renouvele) : plus besoin d'agrandir.
        return fond
    if not binaire_disponible():
        # PANNE MACHINE, jamais une exclusion ni un saut silencieux (decision
        # de l'utilisateur, 2026-09-23) : on retombe sur le comportement
        # d'avant la Tache 6, c'est-a-dire le saut de la Tache 5 -- le
        # candidat echouait deja le plancher, agrandir etait la seule
        # alternative au saut.
        raise PreparationSautee(
            "agrandissement indisponible (upscayl-bin ou ses modeles absents) : "
            "fond trop petit pour le gabarit"
        )
    try:
        agrandi = agrandir(octets, sortie_max_px=max(cible))
    except upscale_mod.UpscaleIndisponible as exc:
        # Meme famille que le binaire absent : la machine a echoue, pas
        # l'article.
        raise PreparationSautee(f"agrandissement en echec : {exc}") from None
    if not resolution_suffisante(dimensions_de(agrandi), cible):
        # Le plancher n'est jamais contourne : un resultat encore trop petit
        # (source minuscule proche du seuil bas, ratio extreme) fait sauter
        # exactement comme un echec de l'outil.
        raise PreparationSautee(
            "fond agrandi toujours sous le plancher de nettete pour ce gabarit"
        )
    chemin = chemin_cache("agrandi:" + fond, ".jpg")
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(agrandi)
    nouveau = chemin.resolve().as_uri()
    # Reassignation, jamais une mutation en place (une colonne JSON ne
    # persisterait pas l'ecriture) : `render_post(post.slides, ...)`, appele
    # juste apres par l'appelant, doit voir cette copie nette.
    post.slides = [
        {**post.slides[0], "image_url": nouveau},
        *post.slides[1:],
    ]
    logger.info("fond de cover agrandi par super-resolution : %s -> %s", fond, nouveau)
    return nouveau


def verifier_fond(
    post,
    article,
    *,
    telecharger=telecharger_media,
    agrandir=upscale_mod.agrandir_fond,
    binaire_disponible=upscale_mod.binaire_disponible,
) -> str:
    """Le fond de la cover, verifie, ou une erreur explicite. Commun aux deux
    gabarits : la cover d'un reel est rendue par Chromium exactement comme celle
    d'un carrousel, donc les trois memes fautes la guettent.

    Un `str(None)` ou un vide sont lus comme absents (`fond_de_cover`) : sinon la
    garde passe et le rendu retombe EN SILENCE sur l'image de secours du gabarit.
    """
    fond = fond_de_cover(post)
    if fond == FOND_KINDA:
        # L'aplat Kinda est un fond VALABLE, choisi a la composition parce que
        # tout le reste etait une affiche (2026-09-18). Le distinguer d'un fond
        # absent est tout l'interet de la sentinelle : sans ce cas, le gel
        # refuserait le post a chaque passage jusqu'a MANQUE.
        return fond
    if fond and fond in urls_interdites(article):
        # Brouillon compose avant la regle du 2026-09-17 : jamais publie.
        raise PreparationImpossible("fond de cover tire d'un compte media Instagram : visuel interdit")
    if not fond:
        # Sans fond, la cover part sur l'image de secours du gabarit : lisible a
        # l'ecran d'un editeur, pas publiable telle quelle. Une file automatique
        # ne doit jamais publier ca -- c'est le signe d'un post compose sans
        # visuel autorise.
        raise PreparationImpossible("aucun fond de cover : le post n'a pas de visuel")
    if fond.startswith("file://") and lire_fichier_cache(fond) is None:
        # La copie locale a ete purgee (cache vide, dossier applicatif efface) :
        # le rendu retomberait en silence sur l'image de secours du gabarit. Un
        # REPORT, jamais une exclusion -- l'article n'y est pour rien, et rien ne
        # part sur le fond de secours.
        raise PreparationDifferee("la copie locale du fond de cover a disparu du cache")
    return _agrandir_fond_si_marque(
        post, fond,
        telecharger=telecharger, agrandir=agrandir, binaire_disponible=binaire_disponible,
    )


def _octets_image(url: str, telecharger) -> tuple[bytes | None, str | None]:
    """Le contenu de l'image, et la raison de l'echec le cas echeant.

    Une copie locale est lue dans le cache, jamais telechargee ; un `file://`
    hors du cache n'est jamais lu. L'URL est nettoyee de ses entites HTML : une
    ligne posee avant le nettoyage a l'ingestion porte encore `&amp;`, que le CDN
    Instagram refuse.

    La raison est rendue parce qu'un carrousel vide ne se traite pas pareil selon
    qu'elle est PASSAGERE (on retente) ou definitive (on exclut)."""
    url = url_propre(url) or url
    octets = lire_fichier_cache(url)
    if octets is not None or url.startswith("file://"):
        return octets, None if octets is not None else "copie locale absente"
    return telecharger(url)


def _video_de_carrousel(media: dict, telecharger_video, sonder, transcoder) -> bytes | None:
    """Le MP4 d'un element video, ou None (l'appelant prend alors la vignette)."""
    url, duree_ms = url_propre(media.get("url")), media.get("duration_ms")
    if not url or (duree_ms and duree_ms > DUREE_MAX_SLIDE_MS):
        return None
    duree_max_s = DUREE_MAX_SLIDE_MS / 1000
    try:
        source = telecharger_video(url, settings.publication_reel_source_max_mo * MO)
        duree = sonder(source)["duration"]
        if not settings.publication_reel_duree_min_s <= duree <= duree_max_s:
            logger.info("video de carrousel ecartee (%.1f s) : %s", duree, url)
            return None
        sortie = transcoder(source)
        infos = sonder(sortie)
    except Exception as exc:  # noqa: BLE001 -- un media ne fait jamais echouer le gel
        logger.warning("video de carrousel inutilisable, vignette a la place : %s", type(exc).__name__)
        return None
    conforme = (
        (infos["width"], infos["height"]) == video_mod.FORMAT_CARROUSEL
        and infos.get("video_codec") == "h264"
        and infos.get("audio_codec") == "aac"
        and len(sortie) <= settings.publication_reel_max_mo * MO
    )
    return sortie if conforme else None


def preparer_fichiers(
    post,
    article,
    dossier: Path,
    *,
    rendre=render_post,
    telecharger=telecharger_media,
    telecharger_video=None,
    sonder=None,
    transcoder=None,
    agrandir=upscale_mod.agrandir_fond,
    binaire_disponible=upscale_mod.binaire_disponible,
) -> Figement:
    """Les fichiers figes ET le manifeste de ce qui part (`app.publish.manifeste`).

    Le manifeste n'est pas un journal a cote : il est construit ICI, element par
    element, au moment ou chacun est ecrit ou ecarte. C'est la seule facon qu'il
    soit vrai -- un recalcul plus tard ne retombe pas dessus (mesure du
    2026-10-04 : 27 des 48 carrousels geles recalculables donnent un autre
    nombre), et il ne le pourra jamais, l'article etant purge a deux jours.
    """
    if telecharger_video is None:
        # Import tardif : preparation_reel importe ce module.
        from app.publish.preparation_reel import telecharger_video
    sonder = sonder or video_mod.sonder
    transcoder = transcoder or video_mod.transcoder_carrousel
    dossier.mkdir(parents=True, exist_ok=True)
    for ancien in [*dossier.glob("*.jpg"), *dossier.glob("*.mp4")]:
        ancien.unlink()

    # `telecharger` est le MEME injecte pour le reste du carrousel (tests,
    # DI) : le fond a agrandir ne doit pas repartir sur le telechargeur reel
    # pendant qu'un faux sert tout le reste.
    fond = verifier_fond(
        post, article, telecharger=telecharger, agrandir=agrandir, binaire_disponible=binaire_disponible
    )
    panne_transport = False
    # Les raisons d'ecartement, gardees pour le message d'exclusion : sinon un
    # article sortirait de la file sur un « aucun visuel publiable » muet.
    ecartees: list[str] = []

    # RenduIndisponible (Chromium absent) remonte telle quelle : c'est une panne
    # systemique, pas un defaut de l'article -- le runner ne doit pas l'exclure.
    rendus = rendre(post.slides, "kinda_post", palette_du_post(post))
    if not rendus:
        raise PreparationImpossible("la cover n'a pas pu etre rendue")
    if rendus[0].overflow:
        raise PreparationImpossible("accroche trop longue : la cover deborde")
    try:
        elements = [("jpg", _cadrer(rendus[0].png))]
    except ImageInexploitable as exc:
        raise PreparationImpossible(f"cover inexploitable : {exc}") from exc
    # Le manifeste, tenu en parallele des octets : une entree par element
    # EXAMINE, qu'il parte ou non. Les entrees ecartees n'ont pas de fichier et
    # portent leur raison -- c'est exactement ce que personne ne voyait.
    manifeste = [
        manifeste_mod.element(
            kind=manifeste_mod.COVER,
            type=PHOTO,
            origine=((getattr(post, "variants", None) or {}).get("fond") or {}).get("origine"),
            source_url=fond,
            # Marqueur provisoire : la cover part TOUJOURS (on n'arrive ici
            # qu'une fois rendue et cadree). Les vrais noms sont recolles par
            # `_recoller_fichiers`, le prefixe aleatoire n'etant tire qu'a la fin.
            fichier="",
        )
    ]

    def noter(media: dict, *, raison: str | None = None, part: bool) -> None:
        """Une ligne de manifeste pour ce media. `part` dit s'il sera fige ; le
        nom du fichier est recolle a la fin, quand le prefixe aleatoire existe.

        `type` est TOUJOURS celui du media d'origine, meme quand une video part
        sous forme de vignette : le couple (`type`, `source_url`) doit rester
        coherent, sinon l'editeur reecrirait une slide `photo` sur une URL de
        video. Ce qui a ete publie a la place se lit dans `raison`.
        """
        manifeste.append(
            manifeste_mod.element(
                kind=manifeste_mod.MEDIA,
                type=media.get("type"),
                origine=media.get("origine") or ORIGINE_ARTICLE,
                source_url=media.get("url") or media.get("preview_url"),
                compte=media.get("compte"),
                # Marqueur provisoire : remplace par le vrai nom plus bas. Il
                # n'est jamais rendu tel quel -- `_recoller_fichiers` l'exige.
                fichier="" if part else None,
                raison=raison,
            )
        )

    for media in medias_du_carrousel(post, article):
        if len(elements) >= CARROUSEL_MAX:
            # Plus de place chez Meta. ECARTE et non « oublie » : un carrousel
            # plein est une raison legitime, et la taire laissait croire que le
            # visuel n'existait pas.
            noter(media, raison=f"carrousel plein ({CARROUSEL_MAX} elements au maximum chez Meta)", part=False)
            continue
        if media.get("type") == VIDEO:
            mp4 = _video_de_carrousel(media, telecharger_video, sonder, transcoder)
            if mp4 is not None:
                elements.append(("mp4", mp4))
                noter(media, part=True)
                continue
            url = media.get("preview_url")
            if url and url_propre(url) == fond:
                noter(media, raison="vignette identique au fond de la cover", part=False)
                continue
            if url:
                # La video est inutilisable mais sa vignette part : le dire, car
                # ce qui arrive chez Meta n'est pas ce que le brouillon annonce.
                raison_video = "video inutilisable : sa vignette part en photo a la place"
            else:
                raison_video = None
        else:
            url = media.get("url")
            raison_video = None
        if not url:
            noter(media, raison="aucune URL exploitable", part=False)
            continue
        octets, raison = _octets_image(url, telecharger)
        if octets is None:
            panne_transport = panne_transport or echec_transitoire(raison)
            noter(media, raison=f"recuperation impossible : {raison}", part=False)
            continue
        try:
            elements.append(("jpg", _cadrer(octets)))
        except ImageEcartee as exc:
            ecartees.append(str(exc))
            logger.info("visuel ecarte du carrousel (%s) : %s", exc, url)
            noter(media, raison=str(exc), part=False)
            continue
        except ImageInexploitable as exc:
            logger.info("visuel inexploitable (%s) : %s", exc, url)
            noter(media, raison=str(exc), part=False)
            continue
        noter(media, raison=raison_video, part=True)

    if len(elements) < 2 and panne_transport:
        # Coupure, 429, 5xx : les visuels EXISTENT, seule leur recuperation a
        # echoue. Un article exclu ne revient jamais dans la file -- on retente
        # au passage suivant, comme le choix des visuels le fait deja.
        raise PreparationDifferee(
            "visuels du carrousel inaccessibles (reseau) : nouvelle tentative au prochain passage"
        )
    if len(elements) < 2 and fond == FOND_KINDA:
        # La lecon du post Noisia (2026-09-18, article 17946), et elle seule :
        # l'aplat Kinda et rien derriere, c'est un post qui ne montre AUCUNE
        # image de l'actu -- « la c'est n'importe quoi ! ». Exclusion, le
        # creneau passe a l'article suivant.
        #
        # Le cas symetrique -- une cover qui porte une vraie photo, et rien
        # derriere -- est desormais publiable tel quel (2026-09-19) : « on peut
        # se contenter juste de la cover normale et c'est tout ». Ce qui coute
        # un post, ce n'est pas d'en avoir un seul element, c'est de n'y rien
        # montrer.
        detail = f" ({' ; '.join(dict.fromkeys(ecartees))})" if ecartees else ""
        raise PreparationImpossible(
            f"cover seule sur l'aplat Kinda : aucun visuel de l'actu a montrer{detail}"
        )

    # Prefixe aleatoire : les fichiers seront publics quelques minutes, leurs noms
    # ne doivent pas se deviner.
    prefixe = secrets.token_urlsafe(12)
    noms = []
    for rang, (extension, octets) in enumerate(elements, start=1):
        nom = f"{prefixe}_{rang:02d}.{extension}"
        (dossier / nom).write_bytes(octets)
        noms.append(nom)
    return Figement(noms=noms, elements=_recoller_fichiers(manifeste, noms))


def _recoller_fichiers(manifeste: list[dict], noms: list[str]) -> list[dict]:
    """Donne a chaque entree qui PART le nom du fichier reellement ecrit.

    Les noms portent un prefixe aleatoire tire a la toute fin ; le manifeste est
    construit bien avant. Le recollage est positionnel et l'invariant est verifie
    plutot que suppose : les entrees non ecartees sont, dans l'ordre, exactement
    les fichiers figes -- si les deux divergeaient, le manifeste mentirait sur ce
    qui part, c'est-a-dire le defaut meme que ce chantier corrige.
    """
    a_publier = [entree for entree in manifeste if not entree["ecarte"]]
    if len(a_publier) != len(noms):  # pragma: no cover -- invariant de construction
        raise RuntimeError(
            f"manifeste incoherent : {len(a_publier)} elements annonces, {len(noms)} fichiers figes"
        )
    for entree, nom in zip(a_publier, noms, strict=True):
        entree["fichier"] = nom
    return manifeste
