"""Les posts des artistes cites, comme visuels d'une actu relayee par un media.

Regle de pertinence (design du 2026-09-17) : le MEME artiste, un post RECENT, et
SUR LE SUJET d'abord. Un portrait recent illustre honnetement un artiste ; un
vieux post sur autre chose non -- d'ou une fenetre dure et une fenetre plus
courte pour les posts hors sujet.

Ce module ne fait aucun appel reseau : il lit la base, ou des posts qu'on lui
donne (lecture en direct, `lecture_artistes.py`).
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.media import PHOTO, VIDEO, entree, video_recuperable, videos_exploitables
from app.models import Article, FormatCreneau
from app.pipeline.cluster import significant_tokens
from app.pipeline.comptes_artistes import CompteArtiste

# Borne de lecture des posts d'artistes candidats (voir `posts_en_base`).
LIMITE_POSTS = 200


def _utc(moment: dt.datetime | None) -> dt.datetime | None:
    # SQLite rend des datetime naifs ; `store.utc` n'est pas importable ici (cycle).
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.timezone.utc)


@dataclass
class PostArtiste:
    compte: CompteArtiste
    permalink: str
    titre: str
    texte: str
    publie_le: dt.datetime | None
    media: list[dict]
    article_id: int | None = None


@dataclass
class Retenu:
    post: PostArtiste
    medias: list[dict]
    recouvrement: int

    @property
    def sur_le_sujet(self) -> bool:
        return self.recouvrement > 0


def medias_utilisables(
    media: list[dict] | None, permalink: str | None, format_: FormatCreneau
) -> list[dict]:
    """Ce qu'un post d'artiste peut reellement fournir au format demande.

    Carrousel : des PHOTOS seulement. Les URL du CDN Instagram sont signees et
    expirent en quelques jours alors que la composition precede le gel de ~22 h :
    un actif d'artiste n'est publiable que copie en local, et seule une image se
    copie (le gel ne telecharge pas de video d'artiste). La vignette d'une video
    est ecartee pour la meme raison : reprise telle quelle, l'actif resterait de
    type `video` et le preparateur du carrousel irait chercher un fichier qui
    n'existe pas.
    """
    media = media or []
    if format_ == FormatCreneau.REEL:
        duree_min = settings.publication_reel_duree_min_s * 1000
        directes = [
            v
            for v in videos_exploitables(media, int(settings.publication_reel_duree_max_s * 1000))
            if v.get("duration_ms") is None or v["duration_ms"] >= duree_min
        ]
        if directes:
            return directes[:1]
        if video_recuperable(media, permalink):
            videos = [m for m in media if m.get("type") == VIDEO]
            return [videos[0] if videos else entree(VIDEO, None)]
        return []
    return [m for m in media if m.get("type") == PHOTO and m.get("url")]


def recouvrement(titre_article: str, post: PostArtiste) -> int:
    """Les mots du SUJET partages, le nom de l'artiste retire : sinon tout post de
    l'artiste « parlerait » de l'actu."""
    nom = significant_tokens(post.compte.nom) | significant_tokens(post.compte.handle)
    sujet = significant_tokens(titre_article) - nom
    return len(sujet & significant_tokens(f"{post.titre} {post.texte}"))


def classer(
    article,
    posts: list[PostArtiste],
    *,
    format_: FormatCreneau,
    maintenant: dt.datetime,
    fenetre_jours: int | None = None,
    generique_jours: int | None = None,
) -> list[Retenu]:
    """Les posts utilisables, du plus au moins pertinent.

    Les deux fenetres sont surchargeables parce que tous les appelants n'ont pas
    la meme exigence : un visuel de CARROUSEL doit illustrer l'actu (d'ou 10 et
    5 jours), un FOND DE COVER n'a qu'a etre une belle photo du bon compte --
    `choix_visuels.photos_du_compte` remonte donc bien plus loin, sans quoi un
    compte de festival en pleine saison d'annonces ne propose que des affiches.
    """
    reference = _utc(article.published_at or article.fetched_at) or maintenant
    fenetre = settings.visuels_artiste_fenetre_jours if fenetre_jours is None else fenetre_jours
    debut = reference - dt.timedelta(days=fenetre)
    generique = dt.timedelta(
        days=settings.visuels_artiste_generique_jours
        if generique_jours is None
        else generique_jours
    )
    retenus = []
    for post in posts:
        publie = _utc(post.publie_le)
        if publie is None or publie < debut or publie > maintenant:
            continue
        if post.article_id is not None and post.article_id == article.id:
            continue
        medias = medias_utilisables(post.media, post.permalink, format_)
        if not medias:
            continue
        commun = recouvrement(article.title, post)
        if commun == 0 and abs(publie - reference) > generique:
            continue
        retenus.append(Retenu(post, medias, commun))
    retenus.sort(
        key=lambda r: (
            not r.sur_le_sujet,
            -r.recouvrement,
            abs(_utc(r.post.publie_le) - reference),
            -(r.post.article_id or 0),
        )
    )
    return retenus


def posts_en_base(
    db: Session,
    comptes: list[CompteArtiste],
    maintenant: dt.datetime,
    fenetre_jours: int | None = None,
) -> list[PostArtiste]:
    """Les posts des comptes en base. `fenetre_jours` accompagne celui de
    `classer` : elargir l'un sans l'autre ne servirait a rien, la requete ne
    remonterait pas les posts que le classement accepterait."""
    if not comptes:
        return []
    par_source = {compte.source_id: compte for compte in comptes}
    fenetre = settings.visuels_artiste_fenetre_jours if fenetre_jours is None else fenetre_jours
    depuis = maintenant - dt.timedelta(days=fenetre + settings.publication_fraicheur_jours)
    vu = func.coalesce(Article.published_at, Article.fetched_at)
    articles = db.scalars(
        select(Article)
        .where(Article.source_id.in_(list(par_source)), Article.media.is_not(None), vu >= depuis)
        # Les plus RECENTS d'abord, et borne dure : la fenetre limite deja le
        # volume, mais l'arbitrage appelle cette requete par article candidat --
        # un compte tres actif ne doit pas faire remonter des centaines de posts.
        .order_by(vu.desc(), Article.id.desc())
        .limit(LIMITE_POSTS)
    ).all()
    return [
        PostArtiste(
            compte=par_source[a.source_id],
            permalink=a.url,
            titre=a.title or "",
            texte=a.summary or "",
            publie_le=_utc(a.published_at or a.fetched_at),
            media=list(a.media or []),
            article_id=a.id,
        )
        for a in articles
    ]
