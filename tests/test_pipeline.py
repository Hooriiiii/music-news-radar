import pytest

from app.config import settings
from app.pipeline.ingest import IngestStats
from app.pipeline.run import run_pipeline
from app.pipeline.scoring import ScoringStats


class FakeAlertStats:
    sent = 2
    errors = 0


@pytest.fixture()
def full_config(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(settings, "discord_webhook_url", "https://discord.com/api/webhooks/x")


def test_run_pipeline_chains_ingest_scoring_alerts(db_session, full_config):
    calls = []
    report = run_pipeline(
        db_session,
        ingest=lambda db: calls.append("ingest") or [IngestStats(1, "Feed", new=3)],
        score=lambda db: calls.append("score") or ScoringStats(scored=3),
        alert=lambda db: calls.append("alert") or FakeAlertStats(),
    )
    assert calls == ["ingest", "score", "alert"]
    assert report.ingest[0].new == 3
    assert report.scoring.scored == 3
    assert report.alerts_sent == 2


def test_run_pipeline_skips_alerts_without_webhook(db_session, full_config, monkeypatch):
    monkeypatch.setattr(settings, "discord_webhook_url", None)
    calls = []
    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        score=lambda db: ScoringStats(),
        alert=lambda db: calls.append("alert"),
    )
    assert calls == []
    assert report.alerts_skipped_reason is not None


def test_run_pipeline_skips_scoring_without_api_key(db_session, full_config, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    calls = []
    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        score=lambda db: calls.append("score"),
        alert=lambda db: FakeAlertStats(),
    )
    assert calls == []
    assert report.scoring is None
    assert report.scoring_skipped_reason is not None
