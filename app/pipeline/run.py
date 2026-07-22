import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import settings
from app.delivery.discord import send_hot_alerts
from app.pipeline.artists import record_mentions
from app.pipeline.cluster import link_duplicates
from app.pipeline.ingest import IngestStats, run_ingest
from app.pipeline.retention import purge_old_articles
from app.pipeline.scoring import ScoringStats, score_pending

logger = logging.getLogger(__name__)


@dataclass
class PipelineReport:
    ingest: list[IngestStats] = field(default_factory=list)
    duplicates_linked: int = 0
    scoring: ScoringStats | None = None
    mentions_recorded: int = 0
    scoring_skipped_reason: str | None = None
    alerts_sent: int = 0
    alerts_errors: int = 0
    alerts_skipped_reason: str | None = None
    purged: int = 0


def run_pipeline(
    db: Session,
    ingest=run_ingest,
    cluster=link_duplicates,
    score=score_pending,
    record=record_mentions,
    alert=send_hot_alerts,
    purge=purge_old_articles,
) -> PipelineReport:
    """Le run fréquent : ingestion -> dédup entre sources -> scoring -> alertes -> purge.

    Le regroupement des doublons passe AVANT le scoring : un doublon repéré à ce
    moment-là n'est jamais scoré, donc jamais payé.

    Chaque étape est optionnelle selon la config (pas de clé API = pas de
    scoring, pas de webhook = pas d'alertes) pour que le pipeline reste
    utilisable pendant la mise en place progressive.
    """
    report = PipelineReport()
    report.ingest = ingest(db)
    report.duplicates_linked = cluster(db)

    if settings.anthropic_api_key:
        report.scoring = score(db)
    else:
        report.scoring_skipped_reason = "ANTHROPIC_API_KEY absente"
        logger.warning("Scoring sauté : %s", report.scoring_skipped_reason)

    # Après le scoring, avant la purge : les artistes cités sont archivés pour de
    # bon, même quand les articles qui les mentionnaient seront supprimés
    report.mentions_recorded = record(db)

    if settings.discord_webhook_url:
        stats = alert(db)
        report.alerts_sent = stats.sent
        report.alerts_errors = stats.errors
    else:
        report.alerts_skipped_reason = "DISCORD_WEBHOOK_URL absente"
        logger.warning("Alertes sautées : %s", report.alerts_skipped_reason)

    # Rétention : on ne garde que les articles récents (par date de post)
    report.purged = purge(db, settings.retention_days)

    return report
