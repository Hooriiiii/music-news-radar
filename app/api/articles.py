import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.content.visuels import origine_interdite
from app.db import get_db
from app.config import settings
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Source, SourceType
from app.pipeline.cluster import raison_de_doublon
from app.pipeline.comptes_artistes import IndexComptes, construire_index
from app.schedule.publication_incertaine import (
    RAISON_RECLAMATION,
    erreur_incertaine,
    publication_incertaine,
)
from app.schedule.runner import RECLAMATION_EN_COURS
from app.schedule.store import etiquette_visuel, raison_sans_visuel, utc
from app.schemas import ArticleOut

router = APIRouter(prefix="/articles", tags=["articles"])


def _avec_visuel(article: Article, index: IndexComptes) -> ArticleOut:
    sortie = ArticleOut.model_validate(article)
    sortie.visuel = etiquette_visuel(article, index)
    sortie.raison_sans_visuel = raison_sans_visuel(article, index)
    sortie.visuels_interdits = origine_interdite(article.source)
    sortie.exclusion_a_verifier = publication_incertaine(article.file_exclu_raison)
    return sortie


@router.get("", response_model=list[ArticleOut])
def list_articles(
    status: ArticleStatus | None = None,
    min_relevance: int | None = Query(None, ge=0, le=100),
    min_hotness: int | None = Query(None, ge=0, le=100),
    min_emergence: int | None = Query(None, ge=0, le=100),
    source_id: int | None = None,
    source_type: SourceType | None = None,
    category: str | None = None,
    has_media: bool | None = None,
    since: dt.datetime | None = None,
    # Par défaut on ne montre que le représentant de chaque actu : les reprises
    # de la même info par d'autres sources sont masquées (cf. pipeline/cluster.py)
    include_duplicates: bool = False,
    sort: Literal["date", "relevance", "emergence"] = "date",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Article).options(selectinload(Article.source))
    if status is not None:
        stmt = stmt.where(Article.status == status)
    if min_relevance is not None:
        stmt = stmt.where(Article.relevance_score >= min_relevance)
    if min_hotness is not None:
        stmt = stmt.where(Article.hotness_score >= min_hotness)
    if min_emergence is not None:
        stmt = stmt.where(Article.emergence_score >= min_emergence)
    if source_id is not None:
        stmt = stmt.where(Article.source_id == source_id)
    if source_type is not None:
        stmt = stmt.join(Source, Article.source_id == Source.id).where(Source.type == source_type)
    if category is not None:
        stmt = stmt.where(Article.category == category)
    if has_media is not None:
        stmt = stmt.where(
            Article.media.is_not(None) if has_media else Article.media.is_(None)
        )
    if since is not None:
        stmt = stmt.where(Article.published_at >= since)
    if not include_duplicates:
        stmt = stmt.where(Article.duplicate_of_id.is_(None))
    if sort == "emergence":
        # La vue A&R : les jeunes artistes à suivre, indépendamment de l'actu
        stmt = stmt.order_by(Article.emergence_score.desc().nulls_last(), Article.id.desc())
    elif sort == "relevance":
        stmt = stmt.order_by(
            Article.relevance_score.desc().nulls_last(),
            Article.hotness_score.desc().nulls_last(),
            Article.id.desc(),
        )
    else:
        stmt = stmt.order_by(Article.published_at.desc().nulls_last(), Article.id.desc())
    articles = db.scalars(stmt.limit(limit).offset(offset)).all()
    # Un index pour toute la page : les comptes suivis ne changent pas d'un article a l'autre.
    index = construire_index(db)
    return [_avec_visuel(article, index) for article in articles]


@router.get("/{article_id}", response_model=ArticleOut)
def get_article(article_id: int, db: Session = Depends(get_db)):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return _avec_visuel(article, construire_index(db))


CONFIRMATION_PAS_PARTI = "PAS_PARTI"


class ReintegrationIn(BaseModel):
    confirmation: str | None = None


def _robot_peut_encore_publier(creneau: Creneau, maintenant: dt.datetime) -> bool:
    """Une reclamation peut etre prise jusqu'a `publication_retard_max_minutes`
    apres l'heure, puis attendre Meta : avant cette limite, ne rien voir sur le
    compte ne prouve pas que rien ne partira. Plus large que `RECLAMATION_EN_COURS`
    seule, qui ne couvre que l'affichage d'un creneau pris a l'heure.
    """
    limite = dt.timedelta(minutes=settings.publication_retard_max_minutes) + RECLAMATION_EN_COURS
    return creneau.erreur == RAISON_RECLAMATION and utc(creneau.publier_a) > maintenant - limite


@router.post("/{article_id}/reintegrer", response_model=ArticleOut)
def reintegrer_article(
    article_id: int, corps: ReintegrationIn | None = None, db: Session = Depends(get_db)
):
    """Leve `file_exclu_raison` : l'article redevient candidat a la file.

    C'etait le seul champ sans retour en arriere de tout le dashboard. « Ne pas
    publier » l'ecrit en un clic, le gel et la composition l'ecrivent tout seuls sur un
    echec -- et AUCUN endpoint ne le remettait a NULL : le seul recours etait un
    UPDATE a la main en base.

    Une exclusion posee par la DEDUP D'ARCHIVE est refusee, et ce n'est pas un
    scrupule : `link_duplicates` relie l'article au passage suivant, donc
    effacer la raison seule ne tient pas (docs/reference/dedup-et-retention.md, « Reparer un faux positif
    exige un verdict MANUEL »). On nomme donc l'outil qui, lui, tient.

    Une exclusion qui laisse le post peut-etre EN LIGNE exige `confirmation ==
    "PAS_PARTI"` : seul un humain qui a regarde le compte sait. La confirmation
    reecrit aussi l'erreur du creneau, sinon `_articles_publiables` continuerait
    d'ecarter l'article (docs/reference/publication.md, « Exclusion »).
    """
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    confirme = corps is not None and corps.confirmation == CONFIRMATION_PAS_PARTI
    if article.file_exclu_raison is None and not confirme:
        # Idempotent : reclamer deux fois la meme remise en file n'est pas une
        # erreur, et l'UI peut avoir une grille d'une seconde en retard.
        return _avec_visuel(article, construire_index(db))
    if raison_de_doublon(article.file_exclu_raison):
        raise HTTPException(
            status_code=409,
            detail=(
                "exclusion posee par la dedup d'archive : effacer la raison ne tient pas "
                "(le lien se reforme au passage suivant). Utiliser "
                "`uv run python -m scripts.reparations.marquer_doublon_manuel --article-a ID "
                "--article-b ID --different --raison \"...\"`."
            ),
        )
    douteux = db.scalars(
        select(Creneau).where(
            Creneau.article_id == article.id,
            Creneau.etat == EtatCreneau.ECHEC,
            erreur_incertaine(Creneau.erreur),
        )
    ).all()
    if publication_incertaine(article.file_exclu_raison) or douteux:
        if not confirme:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Le post est peut-être en ligne : la publication s'est interrompue sans "
                    "confirmation. Vérifie d'abord le compte Instagram, puis confirme qu'il "
                    "n'est pas parti."
                ),
            )
        maintenant = dt.datetime.now(dt.timezone.utc)
        if any(_robot_peut_encore_publier(creneau, maintenant) for creneau in douteux):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Publication peut-être encore en cours : ne rien voir sur le compte ne "
                    "prouve pas encore qu'il n'est pas parti. Réessaie dans quelques minutes."
                ),
            )
        for creneau in douteux:
            # UPDATE conditionnel : si l'etat a bouge depuis la lecture, on n'ecrit rien.
            db.execute(
                update(Creneau)
                .where(
                    Creneau.id == creneau.id,
                    Creneau.etat == EtatCreneau.ECHEC,
                    Creneau.erreur == creneau.erreur,
                )
                .values(erreur=f"verifie a la main, pas parti — {creneau.erreur}"[:1000])
            )
    article.file_exclu_raison = None
    db.commit()
    db.refresh(article)
    return _avec_visuel(article, construire_index(db))


class ArticleStatusUpdate(BaseModel):
    status: ArticleStatus


@router.patch("/{article_id}", response_model=ArticleOut)
def update_article_status(
    article_id: int, payload: ArticleStatusUpdate, db: Session = Depends(get_db)
):
    """Le tri manuel : « new » <-> « reviewed », rien d'autre.

    USED n'appartient qu'au robot, qui le pose a la publication
    (`runner._publier`, sans passer par ici). A la main dans un sens, un article
    jamais publie sortait de la file pour de bon ; dans l'autre, il perdait ce qui le
    protege de la purge et d'une republication.
    """
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if payload.status == ArticleStatus.USED and article.status != ArticleStatus.USED:
        raise HTTPException(
            status_code=409,
            detail="Le statut « utilisé » est posé par le robot quand le post part : il ne se pose pas à la main.",
        )
    # UPDATE conditionnel : le robot peut publier entre la lecture et l'ecriture.
    ecrit = db.execute(
        update(Article)
        .where(Article.id == article.id, Article.status != ArticleStatus.USED)
        .values(status=payload.status)
        .execution_options(synchronize_session=False)
    ).rowcount
    if ecrit != 1:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Cet article a été publié : il garde son statut, c'est ce qui le protège "
                "de la purge et d'une republication."
            ),
        )
    db.commit()
    db.refresh(article)
    return article
