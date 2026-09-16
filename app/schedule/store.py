"""Le lien entre l'arbitrage, en Python pur, et la base.

Trois gestes : tenir a jour les creneaux futurs, lister les articles publiables,
et ecrire le resultat de l'arbitrage. Toute date lue en base passe par `utc()` :
SQLite rend des datetime naifs, et comparer un naif a un aware leve une erreur.
"""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, PostStatus
from app.schedule.queue import Candidat, Place, arbitrer
from app.schedule.slots import prochains_horaires

# Un creneau dans l'un de ces etats ne bouge plus.
VERROUILLES = frozenset({
    EtatCreneau.GELE, EtatCreneau.PUBLIE, EtatCreneau.APERCU, EtatCreneau.ECHEC, EtatCreneau.MANQUE,
})
MODIFIABLES = frozenset({EtatCreneau.LIBRE, EtatCreneau.PROGRAMME})


def utc(moment: dt.datetime | None) -> dt.datetime | None:
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.timezone.utc)


def synchroniser_creneaux(db: Session, maintenant: dt.datetime) -> list[Creneau]:
    attendus = prochains_horaires(maintenant, settings.publication_creneaux)
    existants = {
        utc(c.publier_a): c
        for c in db.scalars(select(Creneau).where(Creneau.publier_a > maintenant))
    }
    for heure in attendus:
        if heure not in existants:
            db.add(Creneau(publier_a=heure))
    # Un creneau futur qui n'est plus dans la grille (fenetres changees en
    # config) disparait, sauf s'il est epingle ou deja engage.
    for heure, creneau in existants.items():
        if heure not in attendus and creneau.etat in MODIFIABLES and not creneau.epingle:
            db.delete(creneau)
    db.commit()
    return list(
        db.scalars(select(Creneau).where(Creneau.publier_a > maintenant).order_by(Creneau.publier_a))
    )


def _a_une_image(article: Article) -> bool:
    # Sans image, la cover partirait sur le fond de secours : pas publiable.
    return any(
        (m.get("type") == "photo" and m.get("url")) or m.get("preview_url")
        for m in article.media or []
    )


def candidats_eligibles(db: Session, maintenant: dt.datetime) -> list[Candidat]:
    depuis = maintenant - dt.timedelta(days=settings.publication_fraicheur_jours)
    articles = db.scalars(
        select(Article)
        .where(Article.relevance_score >= settings.publication_min_relevance)
        .where(Article.duplicate_of_id.is_(None))
        .where(Article.status != ArticleStatus.USED)
        .where(Article.file_exclu_raison.is_(None))
        # Un article deja parti (pour de vrai ou en apercu) fait avancer la file :
        # sans ce filtre, le mode apercu reprendrait le meme article a chaque creneau.
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
        .limit(60)
    ).all()
    composes = set(
        db.scalars(
            select(Post.article_id).where(Post.status != PostStatus.PUBLISHED, Post.article_id.is_not(None))
        )
    )
    return [
        Candidat(a.id, a.relevance_score, a.hotness_score or 0, utc(a.published_at or a.fetched_at), a.id in composes)
        for a in articles
        if _a_une_image(a)
    ]


def post_a_reprendre(db: Session, article_id: int) -> int | None:
    return db.scalar(
        select(Post.id)
        .where(Post.article_id == article_id, Post.status != PostStatus.PUBLISHED)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(1)
    )


def appliquer_arbitrage(db: Session, maintenant: dt.datetime) -> list[Creneau]:
    creneaux = synchroniser_creneaux(db, maintenant)
    # Un creneau passe mais encore dans le delai de retard garde son article :
    # sans ce filtre, le meme article serait aussi programme sur un creneau futur.
    occupes = set(
        db.scalars(
            select(Creneau.article_id).where(
                Creneau.publier_a <= maintenant,
                Creneau.etat.in_([EtatCreneau.PROGRAMME, EtatCreneau.GELE]),
                Creneau.article_id.is_not(None),
            )
        )
    )
    candidats = [c for c in candidats_eligibles(db, maintenant) if c.article_id not in occupes]
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
            creneau.post_id = post_a_reprendre(db, article_id) if article_id else None
        creneau.etat = EtatCreneau.PROGRAMME if article_id else EtatCreneau.LIBRE
    db.commit()
    return creneaux
