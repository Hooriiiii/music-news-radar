"""Le lien entre l'arbitrage, en Python pur, et la base.

Trois gestes, pour chaque format : tenir a jour les creneaux futurs, lister les
articles publiables, et ecrire le resultat de l'arbitrage. Toute date lue en base
passe par `utc()` : SQLite rend des datetime naifs, et comparer un naif a un
aware leve une erreur.

Deux formats, une regle : un article n'occupe JAMAIS deux creneaux. Les reels
sont arbitres d'abord -- la matiere video est rare, la matiere photo abondante --
et les carrousels piochent dans le reste.
"""

import datetime as dt

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.credit import credit_video
from app.content.media import video_recuperable, videos_exploitables
from app.models import (
    Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau, Post, PostStatus, SourceType,
)
from app.publish.mode import reels_actifs
from app.schedule.queue import Candidat, Place, arbitrer
from app.schedule.slots import prochains_horaires

# Un creneau dans l'un de ces etats ne bouge plus.
VERROUILLES = frozenset({
    EtatCreneau.GELE, EtatCreneau.PUBLIE, EtatCreneau.APERCU, EtatCreneau.ECHEC, EtatCreneau.MANQUE,
})
MODIFIABLES = frozenset({EtatCreneau.LIBRE, EtatCreneau.PROGRAMME})

TEMPLATES = {FormatCreneau.CARROUSEL: "kinda_post", FormatCreneau.REEL: "kinda_reel"}
ORDRE_ARBITRAGE = (FormatCreneau.REEL, FormatCreneau.CARROUSEL)
# Les videos sont rares dans le haut du classement : on lit plus loin avant le
# filtre Python pour ne pas rater un reel classe 70e.
LIMITE_CARROUSEL = 60
LIMITE_REEL = 300


def utc(moment: dt.datetime | None) -> dt.datetime | None:
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.timezone.utc)


def nombre_de_creneaux(format_: FormatCreneau) -> int:
    if format_ == FormatCreneau.REEL:
        # Reels coupes au dashboard : plus aucun creneau reel a venir n'est
        # programme (les epingles et les geles restent, voir le runner).
        return settings.publication_creneaux_reel if reels_actifs() else 0
    return settings.publication_creneaux


def _futurs(format_: FormatCreneau, maintenant: dt.datetime):
    return select(Creneau).where(Creneau.publier_a > maintenant, Creneau.format == format_)


def synchroniser_creneaux(
    db: Session, maintenant: dt.datetime, format_: FormatCreneau = FormatCreneau.CARROUSEL
) -> list[Creneau]:
    attendus = prochains_horaires(maintenant, nombre_de_creneaux(format_), format_.value)
    existants = {utc(c.publier_a): c for c in db.scalars(_futurs(format_, maintenant))}
    for heure in attendus:
        if heure not in existants:
            db.add(Creneau(publier_a=heure, format=format_))
    # Un creneau futur qui n'est plus dans la grille (fenetres changees en
    # config, reels desactives) disparait, sauf s'il est epingle ou deja engage.
    for heure, creneau in existants.items():
        if heure not in attendus and creneau.etat in MODIFIABLES and not creneau.epingle:
            db.delete(creneau)
    db.commit()
    return list(db.scalars(_futurs(format_, maintenant).order_by(Creneau.publier_a)))


def _a_une_image(article: Article) -> bool:
    # Sans image, la cover partirait sur le fond de secours : pas publiable.
    return any(
        (m.get("type") == "photo" and m.get("url")) or m.get("preview_url")
        for m in article.media or []
    )


def categories_reel() -> set[str]:
    return {c.strip() for c in settings.publication_reel_categories.split(",") if c.strip()}


def duree_max_reel_ms() -> int:
    return int(settings.publication_reel_duree_max_s * 1000)


def source_video_reel(article: Article) -> str | None:
    """D'ou viendra la video du reel : "api" (URL directe), "ytdlp" (recuperee sur
    le permalink au gel), ou None. L'ordre est la regle : l'API d'abord."""
    if videos_exploitables(article.media, duree_max_reel_ms()):
        return "api"
    source = article.source
    if (
        settings.publication_reel_ytdlp
        and source is not None
        and source.type == SourceType.INSTAGRAM
        and video_recuperable(article.media, article.url)
    ):
        return "ytdlp"
    return None


def raison_ineligible_reel(article: Article) -> str | None:
    """None si l'article peut partir en reel, sinon la raison, affichable."""
    if source_video_reel(article) is None:
        return "aucune video exploitable"
    source = article.source
    categorie = source.categorie.value if source is not None and source.categorie is not None else None
    if categorie not in categories_reel():
        autorisees = ", ".join(sorted(categories_reel())) or "aucune"
        return f"compte de categorie {categorie or 'inconnue'} : reels reserves a {autorisees}"
    if credit_video(article) is None:
        return "credit impossible : la video ne vient pas d'un compte Instagram"
    return None


def _articles_publiables(db: Session, maintenant: dt.datetime, limite: int) -> list[Article]:
    depuis = maintenant - dt.timedelta(days=settings.publication_fraicheur_jours)
    return db.scalars(
        select(Article)
        .where(Article.relevance_score >= settings.publication_min_relevance)
        .where(Article.duplicate_of_id.is_(None))
        .where(Article.status != ArticleStatus.USED)
        .where(Article.file_exclu_raison.is_(None))
        # Un article deja parti (pour de vrai ou en apercu), quel que soit le
        # format, fait avancer la file : pas de reel d'un article deja publie en
        # carrousel, et le mode apercu ne reprend pas le meme article.
        .where(
            ~Article.id.in_(
                select(Creneau.article_id).where(
                    Creneau.etat.in_([EtatCreneau.APERCU, EtatCreneau.PUBLIE]),
                    Creneau.article_id.is_not(None),
                )
            )
        )
        .where(func.coalesce(Article.published_at, Article.fetched_at) >= depuis)
        .order_by(Article.relevance_score.desc(), Article.id)
        .limit(limite)
    ).all()


def candidats_eligibles(
    db: Session, maintenant: dt.datetime, format_: FormatCreneau = FormatCreneau.CARROUSEL
) -> list[Candidat]:
    reel = format_ == FormatCreneau.REEL
    articles = _articles_publiables(db, maintenant, LIMITE_REEL if reel else LIMITE_CARROUSEL)
    # L'avance « deja compose » ne vaut que pour un brouillon du bon gabarit.
    composes = set(
        db.scalars(
            select(Post.article_id).where(
                Post.status != PostStatus.PUBLISHED,
                Post.article_id.is_not(None),
                Post.template == TEMPLATES[format_],
            )
        )
    )
    garde = (lambda a: raison_ineligible_reel(a) is None) if reel else _a_une_image
    return [
        Candidat(a.id, a.relevance_score, a.hotness_score or 0, utc(a.published_at or a.fetched_at), a.id in composes)
        for a in articles
        if garde(a)
    ]


def post_a_reprendre(db: Session, article_id: int, template: str = "kinda_post") -> int | None:
    return db.scalar(
        select(Post.id)
        .where(Post.article_id == article_id, Post.status != PostStatus.PUBLISHED, Post.template == template)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(1)
    )


def _tenus_ailleurs(db: Session, maintenant: dt.datetime, format_: FormatCreneau) -> set[int]:
    return set(
        db.scalars(
            select(Creneau.article_id).where(
                Creneau.article_id.is_not(None),
                or_(
                    # Un creneau passe mais encore dans le delai de retard garde
                    # son article, quel que soit son format.
                    and_(
                        Creneau.publier_a <= maintenant,
                        Creneau.etat.in_([EtatCreneau.PROGRAMME, EtatCreneau.GELE]),
                    ),
                    # L'autre format ne cede pas un article epingle ou deja engage.
                    and_(
                        Creneau.format != format_,
                        Creneau.publier_a > maintenant,
                        or_(Creneau.epingle.is_(True), Creneau.etat.in_(VERROUILLES)),
                    ),
                ),
            )
        )
    )


def appliquer_arbitrage(db: Session, maintenant: dt.datetime) -> list[Creneau]:
    tous: list[Creneau] = []
    places_ce_passage: set[int] = set()
    for format_ in ORDRE_ARBITRAGE:
        creneaux = synchroniser_creneaux(db, maintenant, format_)
        occupes = _tenus_ailleurs(db, maintenant, format_) | places_ce_passage
        candidats = [c for c in candidats_eligibles(db, maintenant, format_) if c.article_id not in occupes]
        places = [
            Place(utc(c.publier_a), c.article_id, c.epingle or c.etat in VERROUILLES) for c in creneaux
        ]
        affectation = arbitrer(places, candidats, maintenant)
        for creneau in creneaux:
            if creneau.epingle or creneau.etat in VERROUILLES:
                continue
            article_id = affectation.get(utc(creneau.publier_a))
            if article_id != creneau.article_id:
                creneau.article_id = article_id
                creneau.post_id = post_a_reprendre(db, article_id, TEMPLATES[format_]) if article_id else None
            creneau.etat = EtatCreneau.PROGRAMME if article_id else EtatCreneau.LIBRE
        db.commit()
        places_ce_passage |= {c.article_id for c in creneaux if c.article_id is not None}
        tous += creneaux
    return sorted(tous, key=lambda c: utc(c.publier_a))


def offre_reels(db: Session, maintenant: dt.datetime) -> dict:
    """Combien d'articles de la fenetre de fraicheur portent une video, combien
    l'API donne directement (le reste passe par yt-dlp au gel), et combien passent
    toutes les regles reel. C'est la mesure qui dira si deux reels par jour sont
    tenables, et quelle part repose sur yt-dlp."""
    articles = _articles_publiables(db, maintenant, LIMITE_REEL)
    sources = {a.id: source_video_reel(a) for a in articles}
    avec_video = [a for a in articles if sources[a.id] is not None]
    return {
        "avec_video": len(avec_video),
        "directes": sum(1 for a in avec_video if sources[a.id] == "api"),
        "eligibles": sum(1 for a in avec_video if raison_ineligible_reel(a) is None),
        "fraicheur_jours": settings.publication_fraicheur_jours,
    }
