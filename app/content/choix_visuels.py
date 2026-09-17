"""Choisir les visuels d'un post, AVANT de payer sa composition.

Dans l'ordre (design du 2026-09-17) :
1. les medias de l'article, s'ils sont autorises (photo de presse, post d'un
   compte d'artiste, de festival, de label, de club) ;
2. sinon les posts des artistes SUIVIS cites : en base, puis -- carrousel
   seulement, sans rien sur le sujet en base -- lus en direct, avec un budget.

Les images d'artistes sont COPIEES en local : les URL du CDN Instagram sont
signees et expirent, et le gel vient ~22 h apres la composition. Une video de
reel ne l'est pas : le gel la telecharge (API) ou la recupere (yt-dlp).

Deux issues sans visuel : `AucunVisuel` (l'article n'en a pas, il sort de la
file) et `VisuelsDifferes` (la lecture en direct attend son budget ou le quota :
on reessaie au passage suivant, rien n'est exclu).
"""

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.actifs_artistes import Retenu, classer, medias_utilisables, posts_en_base
from app.content.credit import credit_video
from app.content.export import echec_transitoire, telecharger as telecharger_media
from app.content.lecture_artistes import LectureDifferee
from app.content.media import VIDEO
from app.content.visuels import (
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    a_une_image_autorisee,
    actif,
    chemin_cache,
    credit_des_actifs,
    image_de,
    origine_interdite,
    raison_sans_artiste,
    urls_interdites,
    visuels_autorises,
)
from app.models import Article, FormatCreneau, Source
from app.pipeline.comptes_artistes import CompteArtiste, IndexComptes, construire_index
from app.schedule.store import raison_reel_propre

logger = logging.getLogger(__name__)

EXTENSIONS_IMAGE = (".jpg", ".jpeg", ".png", ".webp", ".gif")


class AucunVisuel(RuntimeError):
    """Aucun visuel autorise pour cet article. Message affichable."""


class VisuelsDifferes(RuntimeError):
    """Les visuels ne peuvent pas etre choisis MAINTENANT. Message affichable."""


@dataclass
class ChoixVisuels:
    actifs: list[dict] = field(default_factory=list)
    credit: str | None = None


def _propres(article: Article, format_: FormatCreneau) -> ChoixVisuels | None:
    """Les visuels de l'article lui-meme, ou None s'il n'en a pas d'autorise."""
    medias = visuels_autorises(article)
    compte = credit_video(article)

    def en_actif(entree_media: dict) -> dict:
        return actif(
            entree_media,
            origine=ORIGINE_ARTICLE,
            compte=compte,
            permalink=article.url,
            article_id=article.id,
        )

    if format_ == FormatCreneau.REEL:
        if raison_reel_propre(article) is not None:
            return None
        videos = medias_utilisables(medias, article.url, format_)
        return ChoixVisuels([en_actif(videos[0])], compte) if videos else None

    # Tout ce qui donne une image fixe (photo, vignette, gif) -- c'est elle qui
    # fera le fond de la cover -- PLUS les videos publiables (le gel telecharge
    # la video, et retombe sur sa vignette). Un `gif` ne sert donc que de fond :
    # `medias_du_carrousel` ne publie, lui, que des photos et des videos.
    publiables = [m for m in medias if image_de(m) or (m.get("type") == VIDEO and m.get("url"))]
    # Il faut au moins une IMAGE FIXE. Une video avec URL mais sans vignette n'en
    # donne aucune -- MEME predicat que la file (`a_une_image_autorisee`), sinon
    # elle retiendrait un article dont le brouillon partirait sans fond, et le gel
    # l'exclurait pour de bon.
    if not a_une_image_autorisee(article):
        return None
    return ChoixVisuels([en_actif(m) for m in publiables], compte)


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
    retenus: list[Retenu], telecharger, interdites: set[str] = frozenset()
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
    for retenu in retenus:
        for entree_media in retenu.medias:
            if len(actifs) >= settings.visuels_max_photos:
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


def choisir_visuels(
    db: Session,
    article: Article,
    *,
    format_: FormatCreneau = FormatCreneau.CARROUSEL,
    maintenant: dt.datetime,
    lecteur=None,
    index: IndexComptes | None = None,
    telecharger=telecharger_media,
) -> ChoixVisuels:
    propres = _propres(article, format_)
    if propres is not None:
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
            raise AucunVisuel(raison_reel_propre(article))
        raise AucunVisuel(raison_sans_artiste(article))

    posts = posts_en_base(db, comptes, maintenant)
    retenus = classer(article, posts, format_=format_, maintenant=maintenant)

    if format_ == FormatCreneau.REEL:
        # Jamais de lecture en direct pour un reel : son eligibilite s'est decidee
        # a l'arbitrage, sans reseau, sur la base.
        if not retenus:
            raise AucunVisuel("aucune video recente des artistes suivis cites")
        video = _en_actif_artiste(retenus[0], retenus[0].medias[0])
        return ChoixVisuels([video], video["compte"])

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
    if panne_transport:
        # Des visuels existaient, seule la copie a echoue et pour une raison
        # passagere : on retente au passage suivant plutot que d'exclure.
        raise VisuelsDifferes(
            "copie des visuels d'artistes impossible (reseau) : nouvelle tentative au prochain passage"
        )
    raise AucunVisuel(
        "aucun visuel recent des artistes suivis cites (hors fenetre, hors sujet ou inaccessible)"
    )
