import pytest

from app.config import settings
from app.pipeline.cluster import ClusterStats
from app.pipeline.election import ElectionStats
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


def test_run_pipeline_purges_and_reports_count(db_session, full_config):
    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        score=lambda db: ScoringStats(),
        alert=lambda db: FakeAlertStats(),
        purge=lambda db, days: 7,
    )
    assert report.purged == 7


def test_run_pipeline_surveille_le_jeton_de_sourcing(db_session, full_config):
    vus = []

    def surveiller(maintenant, notifier):
        vus.append((maintenant, notifier))
        return "jeton bientot expire"

    def notify(titre, message):
        pass

    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        score=lambda db: ScoringStats(),
        record=lambda db: 0,
        illustrer=lambda db: type("S", (), {"illustres": 0})(),
        alert=lambda db: FakeAlertStats(),
        purge=lambda db, days: 0,
        notify=notify,
        surveiller_jeton=surveiller,
    )
    assert len(vus) == 1 and vus[0][1] is notify
    assert vus[0][0].tzinfo is not None
    assert report.alerte_jeton_sourcing == "jeton bientot expire"


def test_une_surveillance_du_jeton_qui_casse_n_arrete_pas_le_run(db_session, full_config):
    def surveiller(maintenant, notifier):
        raise RuntimeError("imprevu")

    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        score=lambda db: ScoringStats(),
        record=lambda db: 0,
        illustrer=lambda db: type("S", (), {"illustres": 0})(),
        alert=lambda db: FakeAlertStats(),
        purge=lambda db, days: 0,
        notify=lambda titre, message: None,
        surveiller_jeton=surveiller,
    )
    assert report.alerte_jeton_sourcing is None


def test_run_pipeline_elit_avant_le_scoring_et_lie_les_artistes_apres(db_session, full_config):
    calls = []
    report = run_pipeline(
        db_session,
        ingest=lambda db: calls.append("ingest") or [],
        cluster=lambda db: calls.append("cluster") or ClusterStats(),
        elire=lambda db: calls.append("elire") or ElectionStats(reelus=1),
        score=lambda db: calls.append("score") or ScoringStats(),
        record=lambda db: calls.append("record") or 0,
        lier_artistes=lambda db: calls.append("lier") or 2,
        alert=lambda db: FakeAlertStats(),
    )
    assert calls == ["ingest", "cluster", "elire", "score", "record", "lier"]
    assert report.election.reelus == 1
    assert report.artistes_lies == 2


def test_run_pipeline_expose_les_compteurs_d_arbitrage_de_doublons(db_session, full_config):
    """Minor M2 : sans ces compteurs sur le rapport, des credits Anthropic
    epuises ne se voyaient que par des WARNING repetes dans les logs."""
    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        cluster=lambda db: ClusterStats(archive=3, arbitres=2, arbitrage_pannes=1),
        score=lambda db: ScoringStats(),
        alert=lambda db: FakeAlertStats(),
    )
    assert report.duplicates_archive == 3
    assert report.duplicates_arbitres == 2
    assert report.duplicates_arbitrage_pannes == 1
