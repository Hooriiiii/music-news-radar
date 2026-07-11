import datetime as dt

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app.models import Article


def purge_old_articles(db: Session, retention_days: int) -> int:
    """Supprime les articles trop vieux, par DATE DE PUBLICATION (pas d'ajout).

    Les articles sans date de publication n'ont pas de date de post exploitable :
    pour eux seulement, on retombe sur la date d'ajout comme filet, sinon ils
    s'accumuleraient indéfiniment.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retention_days)
    result = db.execute(
        delete(Article).where(
            or_(
                Article.published_at < cutoff,
                (Article.published_at.is_(None)) & (Article.fetched_at < cutoff),
            )
        )
    )
    db.commit()
    return result.rowcount


def _within_retention(published_at: dt.datetime | None, retention_days: int) -> bool:
    """Un article est ingérable si non daté (âge inconnu) ou publié dans la fenêtre."""
    if published_at is None:
        return True
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retention_days)
    return published_at >= cutoff
