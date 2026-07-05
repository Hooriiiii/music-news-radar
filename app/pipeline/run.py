import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import settings
from app.delivery.discord import send_hot_alerts
from app.pipeline.ingest import IngestStats, run_ingest
from app.pipeline.scoring import ScoringStats, score_pending

logger = logging.getLogger(__name__)


@dataclass
class PipelineReport:
    ingest: list[IngestStats] = field(default_factory=list)
    scoring: ScoringStats | None = None
    scoring_skipped_reason: str | None = None
    alerts_sent: int = 0
    alerts_errors: int = 0
    alerts_skipped_reason: str | None = None


def run_pipeline(
    db: Session,
    ingest=run_ingest,
    score=score_pending,
    alert=send_hot_alerts,
) -> PipelineReport:
    """Le run fréquent : ingestion -> scoring des nouveaux -> alertes hot.

    Chaque étape est optionnelle selon la config (pas de clé API = pas de
    scoring, pas de webhook = pas d'alertes) pour que le pipeline reste
    utilisable pendant la mise en place progressive.
    """
    report = PipelineReport()
    report.ingest = ingest(db)

    if settings.anthropic_api_key:
        report.scoring = score(db)
    else:
        report.scoring_skipped_reason = "ANTHROPIC_API_KEY absente"
        logger.warning("Scoring sauté : %s", report.scoring_skipped_reason)

    if settings.discord_webhook_url:
        stats = alert(db)
        report.alerts_sent = stats.sent
        report.alerts_errors = stats.errors
    else:
        report.alerts_skipped_reason = "DISCORD_WEBHOOK_URL absente"
        logger.warning("Alertes sautées : %s", report.alerts_skipped_reason)

    return report
