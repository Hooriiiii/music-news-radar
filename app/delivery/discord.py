import datetime as dt
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article

logger = logging.getLogger(__name__)


def build_alert_payload(article: Article) -> dict:
    meta = " · ".join(
        part
        for part in (
            article.source.name if article.source else None,
            article.category,
            article.imprint,
        )
        if part
    )
    description = article.summary or ""
    if meta:
        description = f"{description}\n\n{meta}" if description else meta
    return {
        "embeds": [
            {
                "title": article.title[:256],
                "url": article.url,
                "description": description[:4000],
                "color": 0xFF4500,
                "fields": [
                    {"name": "Hotness", "value": str(article.hotness_score), "inline": True},
                    {"name": "Pertinence", "value": str(article.relevance_score),
                     "inline": True},
                ],
            }
        ]
    }


def post_alert(article: Article) -> None:
    if not settings.discord_webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL manquante dans .env")
    response = httpx.post(
        settings.discord_webhook_url, json=build_alert_payload(article), timeout=10
    )
    response.raise_for_status()


@dataclass
class AlertStats:
    sent: int = 0
    errors: int = 0


def send_hot_alerts(db: Session, poster=post_alert) -> AlertStats:
    """Alerte les articles hot (hotness >= seuil) jamais alertés.

    alerted_at n'est posé qu'après un envoi réussi : un webhook qui échoue
    sera retenté au run suivant.
    """
    freshness_floor = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        hours=settings.alert_max_age_hours
    )
    stmt = (
        select(Article)
        .where(Article.hotness_score >= settings.alert_hotness_threshold)
        .where(Article.alerted_at.is_(None))
        # Garde-fou back-fill : du "breaking" d'il y a des jours n'est plus une alerte
        .where(or_(Article.published_at.is_(None), Article.published_at >= freshness_floor))
        .order_by(Article.hotness_score.desc())
    )
    stats = AlertStats()
    for article in db.scalars(stmt):
        try:
            poster(article)
            article.alerted_at = dt.datetime.now(dt.timezone.utc)
            db.commit()
            stats.sent += 1
        except Exception:
            logger.exception("Alerte Discord échouée pour l'article %s", article.id)
            db.rollback()
            stats.errors += 1
    return stats
