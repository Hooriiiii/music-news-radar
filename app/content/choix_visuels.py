"""Choisir les visuels d'un post, AVANT de payer sa composition.

Dans l'ordre (design du 2026-09-17) :
1. les medias de l'article, s'ils sont autorises (photo de presse, post d'un
   compte d'artiste, de festival, de label, de club) -- ET, en carrousel
   (2026-09-21), ceux de tout son groupe de doublons : 21 articles lies comme
   doublons le 2026-09-21, dont 13 portaient des medias jetes faute de regarder
   au-dela du seul representant elu. `origine_interdite` reste verifiee PAR
   MEMBRE, jamais heritee du representant (`choix_visuels._propres`) ;
2. sinon les posts des artistes SUIVIS cites : en base, puis -- carrousel
   seulement, sans rien sur le sujet en base -- lus en direct, avec un budget.

Les images d'artistes sont COPIEES en local : les URL du CDN Instagram sont
signees et expirent, et le gel vient ~22 h apres la composition. Une video de
reel ne l'est pas : le gel la telecharge (API) ou la recupere (yt-dlp).

Et, pour le seul FOND de la cover, trois RESERVES (2026-09-18) attachees au
resultat sous forme de lots paresseux (`ChoixVisuels.secours`) : les photos du
compte qui a publie l'actu (`photos_du_compte`), celles des artistes suivis
cites (`photos_des_artistes_cites`), puis le WEB (`photos_du_web`). La
composition ne les ouvre que si le fond retenu est encore une affiche, et
s'arrete au premier lot qui donne une vraie photo. Si aucun ne donne, la cover
part sur le fond Kinda -- jamais sur une affiche.

3. et, quand rien de tout cela n'a donne, le WEB en dernier recours
   (`recherche_web.py`, 2026-09-18 : « ne saute pas l'actu, cherche une image sur
   le web en dernier recours ») -- une image du web plutot qu'un article saute.
   Jamais la VIDEO d'un reel, seulement des images.

Deux issues sans visuel : `AucunVisuel` (l'article n'en a pas, il sort de la
file) et `VisuelsDifferes` (la lecture en direct attend son budget ou le quota :
on reessaie au passage suivant, rien n'est exclu).
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from functools import partial

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.actifs_artistes import Retenu, classer, medias_utilisables, posts_en_base
from app.content.credit import credit_video
from app.content.export import echec_transitoire, telecharger as telecharger_media
from app.content.illustration import Lot, meilleures_photos
from app.content.lecture_artistes import LectureDifferee
from app.content.media import PHOTO, VIDEO
from app.content.recherche_web import attribution_de, candidats_du_web, credit_de
from app.content.visuels import (
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    ORIGINE_COMPTE,
    ORIGINE_WEB,
    VisuelDiffere,
    actif,
    chemin_cache,
    credit_des_actifs,
    image_de,
    image_de_fond,
    origine_interdite,
    raison_sans_artiste,
    urls_interdites,
    visuels_autorises,
)
from app.models import Article, FormatCreneau, Source, SourceType
from app.pipeline.comptes_artistes import (
    CompteArtiste,
    IndexComptes,
    compte_de_source,
    construire_index,
)
from app.schedule.store import raison_reel_propre

logger = logging.getLogger(__name__)

EXTENSIONS_IMAGE = (".jpg", ".jpeg", ".png", ".webp", ".gif")

# La cover d'un reel est rendue par Chromium : sans fond, elle partirait sur
# l'image de secours du gabarit. Message affichable au dashboard.
RAISON_SANS_FOND = (
    "aucune image de fond pour la cover du reel (ni photo de l'actu, ni vignette, "
    "ni photo recente des artistes suivis cites)"
)


class AucunVisuel(RuntimeError):
    """Aucun visuel autorise pour cet article. Message affichable."""


class VisuelsDifferes(RuntimeError):
    """Les visuels ne peuvent pas etre choisis MAINTENANT. Message affichable."""


class VisuelIntrouvable(RuntimeError):
    """Toute l'echelle a ete parcourue (actu, compte source, artiste cite, web)
    sans trouver une image ni affiche ni trop petite pour le gabarit (2026-09-21,
    Tache 5 : FOND_KINDA n'est plus une issue publiable, seulement le signal
    interne d'echec de `illustration.eviter_l_affiche`).

    A la difference d'`AucunVisuel`, ceci ne DOIT PAS exclure l'article : un
    article sans bonne image aujourd'hui peut en avoir une demain (le compte
    source publie, ou la recherche web sort du cache). Le creneau se libere
    (SAUT) et l'article reste eligible -- c'est la retention de 2 jours qui
    conclut, jamais une exclusion posee a la main. Message affichable."""


@dataclass
class ChoixVisuels:
    actifs: list[dict] = field(default_factory=list)
    credit: str | None = None
    # Ou aller chercher un FOND de secours si le fond retenu se revele etre une
    # affiche (2026-09-18, le post des Transmusicales). Des LOTS ordonnes, chacun
    # portant une fonction qui ne coute rien tant qu'on ne l'appelle pas : le
    # compte qui a publie l'actu, puis les artistes suivis cites. Ils vivent ici
    # parce que c'est ici que vivent la session, l'article et le lecteur Graph du
    # passage -- avec son budget --, et que la composition, qui en a besoin, ne
    # les a pas tous. Cf. `photos_du_compte`, `photos_des_artistes_cites` et
    # `photos_du_web` et `illustration.eviter_l_affiche`.
    secours: list[Lot] = field(default_factory=list)


def compte_du_visuel(choix, format_: FormatCreneau = FormatCreneau.CARROUSEL) -> str | None:
    """Le compte dont vient le visuel que le post affichera, a dire au compositeur.

    Le reel du 2026-09-18 titrait « BTS a l'iHeartRadio Festival » sur une video
    de LE SSERAFIM : le modele avait pris le premier nom du line-up de l'article,
    faute de savoir de qui etait la video. L'information existe pourtant ici, au
    moment ou l'appel est paye.

    Pour un REEL, c'est le compte du PORTEUR DE VIDEO et de lui seul : le fond de
    sa cover vient souvent d'ailleurs (photo d'un artiste cite, image du web), et
    c'est la video qu'on regarde. Pour un carrousel, le credit complet -- toutes
    les photos sont a l'ecran.
    """
    actifs = list(getattr(choix, "actifs", None) or [])
    if format_ == FormatCreneau.REEL:
        video = next((a for a in actifs if a.get("type") == VIDEO and a.get("compte")), None)
        if video is not None:
            return video["compte"]
    return getattr(choix, "credit", None)


def _membres_du_groupe(db: Session, article: Article) -> list[Article]:
    """Le representant et tous les membres de son groupe de doublons, dans
    l'ordre de preference -- le representant en tete.

    Un article qui atteint la composition est TOUJOURS un representant
    (`duplicate_of_id IS NULL`, `store._articles_publiables`) : les membres a
    consulter sont donc ceux dont `duplicate_of_id` pointe vers lui, jamais une
    chaine a remonter -- le projet garantit qu'un saut suffit toujours
    (CLAUDE.md, « Dedup a deux etages »).

    2026-09-21 : 21 articles lies comme doublons, dont 13 portent des medias --
    jetes jusqu'ici parce que seul le representant elu (le plus INFORMATIF par
    le TITRE, `elire_representants`) etait regarde. Le representant garde la
    priorite : c'est lui qui ouvre la liste, ses medias passent donc en premier
    a qualite egale dans `_propres`."""
    doublons = db.scalars(
        select(Article).where(Article.duplicate_of_id == article.id).order_by(Article.id)
    ).all()
    return [article, *doublons]


def _propres(
    article: Article, format_: FormatCreneau, membres: list[Article] | None = None
) -> ChoixVisuels | None:
    """Les visuels de l'article lui-meme -- et, en carrousel, de tout son groupe
    de doublons (`membres`) -- ou None s'il n'y a rien d'autorise.

    `membres` est le representant en tete puis ses doublons par id ; sans lui
    (tests unitaires sur des objets simples, sans session), seul l'article
    compte, comme avant. Le REEL n'est pas elargi : son eligibilite (categorie
    du compte, duree de la video) se decide sur l'article seul, et l'elargir
    demanderait de revalider ces regles par membre -- hors perimetre de cette
    tache (CLAUDE.md, « Reels automatiques »).

    `origine_interdite` (« jamais le visuel d'un compte Instagram media ») est
    reverifiee PAR MEMBRE : `visuels_autorises` et `credit_video` la relisent
    chacun sur LA SOURCE DU MEMBRE, jamais heritee du representant. Un groupe
    mixte presse + compte media (le cas courant) contient les deux ; seul le
    second est banni."""
    compte = credit_video(article)

    if format_ == FormatCreneau.REEL:
        medias = visuels_autorises(article)

        def en_actif(entree_media: dict) -> dict:
            return actif(
                entree_media,
                origine=ORIGINE_ARTICLE,
                compte=compte,
                permalink=article.url,
                article_id=article.id,
            )

        if raison_reel_propre(article) is not None:
            return None
        videos = medias_utilisables(medias, article.url, format_)
        if not videos:
            return None
        actifs = [en_actif(videos[0])]
        # La cover d'un reel est RENDUE par Chromium, comme celle d'un carrousel :
        # il lui FAUT un fond. Le porteur de video n'en donne aucun quand Meta
        # omet `media_url` ET la vignette -- `medias_utilisables` fabrique alors
        # une entree video nue. La photo de l'actu, elle, en donne un : sans cet
        # ajout, le post partait sans fond et le gel le refusait a chaque passage
        # jusqu'a MANQUE (article 17974, reel de Charlotte de Witte).
        image = next((m for m in medias if m is not videos[0] and image_de(m)), None)
        if image is not None:
            actifs.append(en_actif(image))
        # `image_de_fond` prefere deja la vraie photo a la vignette de la video.
        # Un fond peut manquer encore : l'appelant le cherchera chez les artistes.
        return ChoixVisuels(actifs, credit_des_actifs(actifs) or compte)

    # Carrousel : tout ce qui donne une image fixe (photo, vignette, gif) -- ce
    # sera le fond de la cover -- PLUS les videos publiables (le gel telecharge
    # la video, et retombe sur sa vignette), chez le representant ET chez tous
    # les membres de son groupe. Un `gif` ne sert donc que de fond :
    # `medias_du_carrousel` ne publie, lui, que des photos et des videos.
    paires = [
        (membre, media)
        for membre in (membres if membres is not None else [article])
        for media in visuels_autorises(membre)
    ]
    # Il faut au moins une IMAGE FIXE quelque part dans le groupe. Une video avec
    # URL mais sans vignette n'en donne aucune -- MEME predicat que la file pour
    # un article seul (`a_une_image_autorisee`), sinon on retiendrait un groupe
    # dont le brouillon partirait sans fond, et le gel l'exclurait pour de bon.
    if not any(image_de(media) for _, media in paires):
        return None
    # Borne au budget du JUGE (`illustration_max_candidats`, 10 -- la taille
    # d'un carrousel Meta) : `prejuger` ne note que ce nombre d'images, et un
    # actif au-dela n'est jamais juge, donc garde par la regle du doute -- et
    # invisible pour `plafonner_affiches`. Un groupe de 4-5 membres portant
    # chacun plusieurs medias depassait ce budget, et le defaut des sept cartes
    # des Trans Musicales (2026-09-19) redevenait atteignable (revue du
    # 2026-09-21). L'ordre du groupe (representant d'abord) decide de ce qui
    # reste, donc les medias de l'actu elle-meme ne sont jamais les sacrifies.
    publiables = [
        (membre, media)
        for membre, media in paires
        if image_de(media) or (media.get("type") == VIDEO and media.get("url"))
    ][: settings.illustration_max_candidats]
    if not any(image_de(media) for _, media in publiables):
        # Meme exigence d'une image fixe, APRES la borne : dix videos sans
        # vignette en tete de groupe ne donneraient aucun fond.
        return None
    actifs = [
        actif(
            media,
            origine=ORIGINE_ARTICLE,
            # Credite le membre DONT VIENT le visuel, pas systematiquement le
            # representant -- un doublon garde son propre compte (ou aucun,
            # pour une photo de presse RSS).
            compte=credit_video(membre),
            permalink=membre.url,
            article_id=membre.id,
        )
        for membre, media in publiables
    ]
    # `credit_des_actifs` agrege les comptes `@...` reellement presents parmi
    # les actifs retenus ; repli sur le credit du representant seul quand aucun
    # actif n'en porte (medias de presse RSS, jamais credites en compte).
    return ChoixVisuels(actifs, credit_des_actifs(actifs) or compte)


def _comptes_autorises(db: Session, comptes: list[CompteArtiste]) -> list[CompteArtiste]:
    """Les comptes cites dont Kinda peut publier les visuels.

    Ceinture : la regle « jamais l'image d'un compte media Instagram » est
    re-verifiee ICI, sur la source en base, et pas seulement heritee du filtre de
    `construire_index` -- un classement manuel, un lien de fiche artiste pose a
    cote ou une regression amont suffiraient sinon a faire reposter le visuel
    d'un media concurrent."""
    if not comptes:
        return []
    sources = {
        source.id: source
        for source in db.scalars(
            select(Source).where(Source.id.in_([compte.source_id for compte in comptes]))
        )
    }
    autorises = []
    for compte in comptes:
        source = sources.get(compte.source_id)
        if source is None or origine_interdite(source):
            logger.info("Visuels de @%s ecartes : origine interdite", compte.handle)
            continue
        autorises.append(compte)
    return autorises


def _en_actif_artiste(retenu: Retenu, entree_media: dict, fichier: str | None = None) -> dict:
    return actif(
        entree_media,
        origine=ORIGINE_ARTISTE,
        compte=f"@{retenu.post.compte.handle}",
        permalink=retenu.post.permalink,
        article_id=retenu.post.article_id,
        publie_le=retenu.post.publie_le,
        fichier=fichier,
    )


def _copier_images(
    retenus: list[Retenu],
    telecharger,
    interdites: set[str] = frozenset(),
    maximum: int | None = None,
) -> tuple[list[dict], bool]:
    """Les photos des posts retenus, copiees dans le cache local (au plus
    `visuels_max_photos`), et si une copie a echoue pour une raison PASSAGERE.

    Une copie en echec est sautee : l'URL signee du CDN Instagram a pu expirer
    entre l'ingestion et maintenant. Mais une coupure reseau n'est PAS une
    absence de visuel -- l'appelant differe alors au lieu d'exclure un article
    qui ne reviendrait jamais dans la file."""
    actifs: list[dict] = []
    vues: set[str] = set()
    panne_transport = False
    plafond = settings.visuels_max_photos if maximum is None else maximum
    for retenu in retenus:
        for entree_media in retenu.medias:
            if len(actifs) >= plafond:
                return actifs, panne_transport
            # `medias_utilisables` ne rend que des photos avec URL pour un
            # carrousel : une vignette de video n'est jamais un actif d'artiste.
            url = entree_media.get("url")
            if not url or url in vues or url in interdites:
                continue
            vues.add(url)
            octets, extension = telecharger(url)
            if octets is None:
                logger.info("Visuel d'artiste inaccessible (%s) : %s", url, extension)
                panne_transport = panne_transport or echec_transitoire(extension)
                continue
            chemin = chemin_cache(url, extension if extension in EXTENSIONS_IMAGE else ".jpg")
            chemin.parent.mkdir(parents=True, exist_ok=True)
            chemin.write_bytes(octets)
            actifs.append(_en_actif_artiste(retenu, entree_media, chemin.resolve().as_uri()))
    return actifs, panne_transport


def _reel(
    article: Article,
    propres: ChoixVisuels | None,
    retenus: list[Retenu],
    posts: list,
    *,
    maintenant: dt.datetime,
    telecharger,
    chercheur_web=None,
) -> ChoixVisuels:
    """Les visuels d'un reel : le porteur de la VIDEO, et une IMAGE DE FOND.

    Deux besoins distincts, et c'est toute la lecon du creneau bloque de
    l'article 17974 : `assembler_reel` a besoin de la video, mais la cover qui
    lui sert de premiere frame est rendue par Chromium et a besoin d'un fond.
    Un porteur de video nu (Meta omet `media_url` et la vignette) ne donne que
    le premier.

    Jamais de lecture en direct ici : l'eligibilite d'un reel se decide sans
    reseau, sur la base (design du 2026-09-17)."""
    actifs = list(propres.actifs) if propres is not None else []
    if not actifs:
        if not retenus:
            raise AucunVisuel("aucune video recente des artistes suivis cites")
        actifs = [_en_actif_artiste(retenus[0], retenus[0].medias[0])]
    if image_de_fond(actifs) is None:
        # Le fond suit le meme chemin et les memes budgets que les photos d'un
        # carrousel : classees sur le sujet, puis COPIEES dans le cache local (les
        # URL du CDN Instagram expirent avant le gel). Une seule suffit.
        photos = classer(article, posts, format_=FormatCreneau.CARROUSEL, maintenant=maintenant)
        copies, panne_transport = _copier_images(
            photos, telecharger, urls_interdites(article), maximum=1
        )
        if not copies:
            if panne_transport:
                raise VisuelsDifferes(
                    "copie du fond de cover impossible (reseau) : nouvelle tentative au prochain passage"
                )
            # Dernier recours : le web. JAMAIS pour la video du reel -- seulement
            # pour le fond de sa cover, qui est une image rendue par Chromium.
            try:
                copies = _actifs_du_web(
                    article, maintenant=maintenant, telecharger=telecharger,
                    chercheur=chercheur_web, maximum=1,
                )
            except VisuelDiffere as exc:
                # I-1 : la recherche web n'a pas pu etre TENTEE (reseau, quota) --
                # un report, jamais un saut ni une exclusion de l'article.
                raise VisuelsDifferes(
                    "recherche web impossible (reseau) : nouvelle tentative au prochain passage"
                ) from exc
            if not copies:
                raise AucunVisuel(RAISON_SANS_FOND)
        actifs += copies
    credit = credit_des_actifs(actifs)
    return ChoixVisuels(actifs, credit or (propres.credit if propres is not None else None))


def _avec_fond_du_web(
    article: Article,
    propres: ChoixVisuels | None,
    maintenant: dt.datetime,
    telecharger,
    raison: str,
    chercheur_web=None,
) -> ChoixVisuels:
    """Un reel qui a sa video mais aucun fond, et aucun artiste suivi cite pour
    en donner un : le web fournit le FOND, jamais la video."""
    actifs = list(propres.actifs) if propres is not None else []
    try:
        fond = _actifs_du_web(article, maintenant=maintenant, telecharger=telecharger,
                              chercheur=chercheur_web, maximum=1)
    except VisuelDiffere as exc:
        # I-1 : reseau/quota, pas une absence d'image -- un report.
        raise VisuelsDifferes(
            "recherche web impossible (reseau) : nouvelle tentative au prochain passage"
        ) from exc
    if not fond:
        raise AucunVisuel(raison)
    actifs += fond
    return ChoixVisuels(actifs, credit_des_actifs(actifs))


def _fenetre_secours() -> dict:
    """Les fenetres du fond de secours : bien plus larges que celles d'un visuel
    de carrousel. Un visuel de carrousel doit ILLUSTRER l'actu ; un fond n'a
    qu'a etre une belle photo du bon compte."""
    jours = settings.fond_secours_fenetre_jours
    return {"fenetre_jours": jours, "generique_jours": jours}


def photos_du_compte(
    db: Session,
    article: Article,
    *,
    maintenant: dt.datetime,
    lecteur=None,
    telecharger=telecharger_media,
    maximum: int | None = None,
) -> list[str]:
    """Des photos du compte qui a publie l'actu, copiees en local.

    La premiere reserve du fond de cover : « si tu trouves pas, va trouver une
    belle photo sur le compte Instagram de l'info en question » (utilisateur,
    2026-09-18, sur le post des Transmusicales, dont le carrousel n'etait fait
    que d'affiches de line-up).

    Ce n'est pas la meme chose que les visuels d'artistes CITES
    (`photos_des_artistes_cites`) : ici on lit le compte de l'article lui-meme,
    celui qui porte l'actu -- un festival, un label, un club, un artiste. Jamais
    un compte MEDIA : la regle du 2026-09-17 vaut aussi pour un fond de cover.

    La base d'abord, gratuite. Si elle ne donne pas de quoi choisir, on LIT le
    compte en direct -- et on leve pour cela la garde de fraicheur du lecteur
    (`ignorer_fraicheur`), qui existe pour epargner du quota quand la base
    suffit : ici elle ne suffit justement pas. Les plafonds durs du lecteur
    (2 lectures par passage, 24 par jour, cache 6 h, pause quota) restent.

    Les photos sont COPIEES dans le cache local, comme celles des artistes : les
    URL signees du CDN Instagram expirent avant le gel, qui suit la composition
    de ~22 h. Rendre l'URL distante donnerait un 404 silencieux, donc une cover
    sur le fond de secours du gabarit.
    """
    source = getattr(article, "source", None)
    if source is None or getattr(source, "type", None) != SourceType.INSTAGRAM:
        return []
    if origine_interdite(source):
        logger.info("Fond de secours ecarte : le compte source est un media")
        return []
    plafond = settings.fond_secours_max_photos if maximum is None else maximum
    compte = compte_de_source(source)
    fenetres = _fenetre_secours()
    posts = posts_en_base(db, [compte], maintenant, fenetres["fenetre_jours"])
    retenus = classer(article, posts, format_=FormatCreneau.CARROUSEL, maintenant=maintenant,
                      **fenetres)
    # I-1 (tour de correction 1/5) : une lecture en direct qui echoue pour une
    # raison de TRANSPORT (quota, budget, reseau) n'a pas le meme sens qu'une
    # lecture reussie qui ne trouve rien -- seule la premiere doit empecher de
    # conclure « cette reserve n'a rien ». Le doute ne se leve qu'A LA FIN, si
    # le resultat entier reste vide : une base deja suffisante ne doit jamais
    # etre eclipsee par l'echec d'une lecture devenue superflue.
    differe = False
    if len(retenus) < plafond and lecteur is not None:
        try:
            lus = lecteur(compte, maintenant, ignorer_fraicheur=True)
        except LectureDifferee as exc:
            logger.info("Lecture du compte source differee : %s", exc)
            lus, differe = [], True
        connus = {post.permalink for post in posts}
        nouveaux = [post for post in lus if post.permalink not in connus]
        if nouveaux:
            retenus = classer(article, [*posts, *nouveaux], format_=FormatCreneau.CARROUSEL,
                              maintenant=maintenant, **fenetres)
    # Les images de l'ARTICLE sont exclues nommement : `classer` ecarte deja le
    # post de l'article, mais un compte qui republie la meme affiche dans deux
    # posts la reproposerait comme remplacante d'elle-meme.
    deja = urls_interdites(article) | {
        url for m in (article.media or []) for url in (m.get("url"), m.get("preview_url")) if url
    }
    copies, panne_transport = _copier_images(retenus, telecharger, deja, maximum=plafond)
    resultat = [actif_["fichier"] for actif_ in copies if actif_.get("fichier")]
    if not resultat and (differe or panne_transport):
        # Rien a offrir, et ce n'est PAS parce qu'on a regarde et rien trouve :
        # la lecture en direct ou le telechargement d'un candidat connu a
        # echoue pour une raison passagere. `eviter_l_affiche` doit le savoir
        # pour reporter au lieu de sauter (Tache 5, I-1).
        raise VisuelDiffere("lecture ou copie du compte source impossible (reseau/quota)")
    return resultat


def photos_des_artistes_cites(
    db: Session,
    article: Article,
    *,
    maintenant: dt.datetime,
    telecharger=telecharger_media,
    index: IndexComptes | None = None,
    maximum: int | None = None,
) -> list[str]:
    """Des photos des artistes SUIVIS que l'actu cite, copiees en local.

    La seconde reserve du fond de cover, et elle tombe sous le sens pour le cas
    qui a tout declenche : un line-up des Transmusicales cite des artistes que
    Kinda suit deja, et le portrait de l'un d'eux fait un bien meilleur fond que
    l'affiche ou tous les noms sont ecrits en petit.

    C'est le meme chemin que les visuels de carrousel d'un article sans image
    propre (`_choisir_visuels`), reduit a ce qu'un fond demande : aucune lecture
    en direct ici -- la reserve precedente a deja pu consommer le budget du
    passage, et un seul fond ne vaut pas d'epuiser le quota Graph sur N comptes.
    """
    comptes = _comptes_autorises(
        db,
        (index if index is not None else construire_index(db)).cites(
            textes=[article.title, article.summary],
            noms=article.mentioned_artists or [],
            exclure_source_id=article.source_id,
        ),
    )
    if not comptes:
        return []
    fenetres = _fenetre_secours()
    retenus = classer(
        article,
        posts_en_base(db, comptes, maintenant, fenetres["fenetre_jours"]),
        format_=FormatCreneau.CARROUSEL,
        maintenant=maintenant,
        **fenetres,
    )
    copies, panne_transport = _copier_images(
        retenus,
        telecharger,
        urls_interdites(article),
        maximum=settings.fond_secours_max_photos if maximum is None else maximum,
    )
    resultat = [actif_["fichier"] for actif_ in copies if actif_.get("fichier")]
    if not resultat and panne_transport:
        # Meme raisonnement que `photos_du_compte` (I-1) : un candidat existait,
        # seul son telechargement a echoue pour une raison passagere.
        raise VisuelDiffere("copie des photos d'artistes cites impossible (reseau)")
    return resultat


def photos_du_web(
    article: Article,
    *,
    maintenant: dt.datetime,
    telecharger=telecharger_media,
    chercheur=None,
    maximum: int | None = None,
) -> list[str]:
    """Des photos trouvees SUR LE WEB, copiees en local. Le dernier barreau.

    « ne saute pas l'actu, cherche une image sur le web en dernier recours »
    (utilisateur, 2026-09-18) : apres les medias de l'actu, le compte qui l'a
    publiee et les artistes suivis cites, il ne reste que l'aplat Kinda -- et un
    article n'a pas a partir sans image quand le web en a une.

    `recherche_web` fait le choix des pages et l'extraction de leur `og:image` ;
    ici on ne fait que RAPATRIER, comme pour une photo d'artiste, parce que la
    composition precede le gel de ~22 h et qu'une URL d'editeur peut bouger.
    """
    trouves = candidats_du_web(article, maintenant=maintenant, chercheur=chercheur)
    plafond = settings.visuels_web_max_candidats if maximum is None else maximum
    return _copier_urls(
        [candidat.image for candidat in trouves], telecharger, urls_interdites(article), plafond
    )


def _copier_urls(urls, telecharger, interdites: set[str], maximum: int) -> list[str]:
    """Les URL rapatriees dans le cache local, en `file://`. Une copie en echec
    est sautee, jamais fatale : le web est un CONFORT de plus, pas une source."""
    fichiers: list[str] = []
    vues: set[str] = set()
    for url in urls:
        if len(fichiers) >= maximum:
            break
        if not url or url in vues or url in interdites:
            continue
        vues.add(url)
        octets, extension = telecharger(url)
        if octets is None:
            logger.info("image du web inaccessible (%s) : %s", url, extension)
            continue
        chemin = chemin_cache(url, extension if extension in EXTENSIONS_IMAGE else ".jpg")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(octets)
        fichiers.append(chemin.resolve().as_uri())
    return fichiers


def actif_du_web(fichier: str, *, maintenant: dt.datetime) -> dict:
    """Une copie locale venue du web, en actif CREDITE.

    Le credit (`compte`) est le domaine du site : « 📸 ra.co » une fois le
    pictogramme pose par la legende. Il est reconstitue depuis le cache des
    requetes (`recherche_web.attribution_de`), parce que le fond publie n'est
    qu'un `file://` et qu'il doit rester creditable apres un redemarrage.
    """
    attribution = attribution_de(fichier, maintenant=maintenant)
    return actif(
        {"type": PHOTO, "url": None},
        origine=ORIGINE_WEB,
        compte=credit_de(attribution),
        permalink=attribution.page if attribution else None,
        fichier=fichier,
    )


def _actifs_du_web(
    article: Article, *, maintenant: dt.datetime, telecharger, chercheur=None, maximum=None
) -> list[dict]:
    """Les photos du web, jugees puis mises en actifs -- vide si le web n'a rien.

    Juge INDULGENT (`garder_inconnues`) : ici l'alternative n'est pas le fond
    Kinda mais l'article SAUTE, et une image non jugee (vision coupee, quota)
    vaut mieux que pas d'article. Une affiche averee, elle, reste ecartee.
    """
    photos = photos_du_web(article, maintenant=maintenant, telecharger=telecharger,
                           chercheur=chercheur)
    retenues = meilleures_photos(photos, garder_inconnues=True)
    plafond = settings.visuels_max_photos if maximum is None else maximum
    return [actif_du_web(url, maintenant=maintenant) for url in retenues[:plafond]]


def choisir_visuels(
    db: Session,
    article: Article,
    *,
    format_: FormatCreneau = FormatCreneau.CARROUSEL,
    maintenant: dt.datetime,
    lecteur=None,
    index: IndexComptes | None = None,
    telecharger=telecharger_media,
    chercheur_web=None,
) -> ChoixVisuels:
    """Les visuels du post, ET de quoi rattraper un fond qui serait une affiche.

    Le `secours` attache au resultat n'est jamais appele ici : il porte le
    budget de lecture du passage jusqu'a la composition, qui seule sait si le
    fond retenu est une affiche (cf. `illustration.eviter_l_affiche`).
    """
    choix = _choisir_visuels(
        db, article, format_=format_, maintenant=maintenant, lecteur=lecteur,
        index=index, telecharger=telecharger, chercheur_web=chercheur_web,
    )
    choix.secours = [
        Lot(
            ORIGINE_COMPTE,
            partial(photos_du_compte, db, article, maintenant=maintenant, lecteur=lecteur,
                    telecharger=telecharger),
        ),
        Lot(
            ORIGINE_ARTISTE,
            partial(photos_des_artistes_cites, db, article, maintenant=maintenant,
                    telecharger=telecharger, index=index),
        ),
        # Le dernier barreau : le web. Ouvert seulement si les deux precedents
        # n'ont rien donne -- une recherche est payee, et le compte de l'actu
        # reste toujours la meilleure source d'une photo de l'actu.
        Lot(
            ORIGINE_WEB,
            partial(photos_du_web, article, maintenant=maintenant, telecharger=telecharger,
                    chercheur=chercheur_web),
        ),
    ]
    return choix


def _choisir_visuels(
    db: Session,
    article: Article,
    *,
    format_: FormatCreneau,
    maintenant: dt.datetime,
    lecteur,
    index: IndexComptes | None,
    telecharger,
    chercheur_web=None,
) -> ChoixVisuels:
    membres = _membres_du_groupe(db, article) if format_ != FormatCreneau.REEL else None
    propres = _propres(article, format_, membres)
    if propres is not None and (
        format_ != FormatCreneau.REEL or image_de_fond(propres.actifs) is not None
    ):
        return propres

    index = index if index is not None else construire_index(db)
    comptes = index.cites(
        textes=[article.title, article.summary],
        noms=article.mentioned_artists or [],
        exclure_source_id=article.source_id,
    )
    comptes = _comptes_autorises(db, comptes)
    if not comptes:
        if format_ == FormatCreneau.REEL:
            # Sans video, le web ne peut rien : il ne sert JAMAIS la video d'un
            # reel, seulement le fond de sa cover.
            if raison_reel_propre(article) is not None:
                raise AucunVisuel(raison_reel_propre(article))
            return _avec_fond_du_web(article, propres, maintenant, telecharger,
                                     RAISON_SANS_FOND, chercheur_web)
        try:
            actifs = _actifs_du_web(article, maintenant=maintenant, telecharger=telecharger,
                                    chercheur=chercheur_web)
        except VisuelDiffere as exc:
            # I-1 : reseau/quota, pas une absence d'image -- un report.
            raise VisuelsDifferes(
                "recherche web impossible (reseau) : nouvelle tentative au prochain passage"
            ) from exc
        if actifs:
            return ChoixVisuels(actifs, credit_des_actifs(actifs))
        raise AucunVisuel(raison_sans_artiste(article))

    posts = posts_en_base(db, comptes, maintenant)
    retenus = classer(article, posts, format_=format_, maintenant=maintenant)

    if format_ == FormatCreneau.REEL:
        return _reel(article, propres, retenus, posts, maintenant=maintenant,
                     telecharger=telecharger, chercheur_web=chercheur_web)

    differe: LectureDifferee | None = None
    if lecteur is not None and not any(r.sur_le_sujet for r in retenus):
        lus: list = []
        for compte in comptes:
            try:
                lus += lecteur(compte, maintenant)
            except LectureDifferee as exc:
                differe = exc
                break
        connus = {p.permalink for p in posts}
        nouveaux = [p for p in lus if p.permalink not in connus]
        if nouveaux:
            retenus = classer(article, [*posts, *nouveaux], format_=format_, maintenant=maintenant)

    actifs, panne_transport = _copier_images(retenus, telecharger, urls_interdites(article))
    if actifs:
        return ChoixVisuels(actifs, credit_des_actifs(actifs))
    if differe is not None:
        raise VisuelsDifferes(str(differe)) from differe
    # Les artistes suivis cites n'ont rien de recent : le web, en dernier recours,
    # plutot que de sauter l'actu.
    try:
        actifs = _actifs_du_web(article, maintenant=maintenant, telecharger=telecharger,
                                chercheur=chercheur_web)
    except VisuelDiffere as exc:
        # I-1 : reseau/quota, pas une absence d'image -- un report.
        raise VisuelsDifferes(
            "recherche web impossible (reseau) : nouvelle tentative au prochain passage"
        ) from exc
    if actifs:
        return ChoixVisuels(actifs, credit_des_actifs(actifs))
    if panne_transport:
        # Des visuels existaient, seule la copie a echoue et pour une raison
        # passagere : on retente au passage suivant plutot que d'exclure.
        raise VisuelsDifferes(
            "copie des visuels d'artistes impossible (reseau) : nouvelle tentative au prochain passage"
        )
    raise AucunVisuel(
        "aucun visuel recent des artistes suivis cites (hors fenetre, hors sujet ou inaccessible)"
    )
