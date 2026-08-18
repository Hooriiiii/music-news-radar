"""Run fréquent (cron/launchd) : ingestion -> scoring -> alertes Discord.

Usage :
    uv run python -m scripts.run_pipeline
"""

import datetime as dt
import logging

from app.config import settings
from app.db import BaseInjoignable, SessionLocal, attendre_base
from app.pipeline.run import run_pipeline


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Avant toute chose : ce script est lance par launchd, parfois au reveil du
    # Mac avant que le DNS reponde.
    try:
        attendre_base()
    except BaseInjoignable as exc:
        print(f"[ERREUR] {exc}")
        raise SystemExit(1) from exc

    started = dt.datetime.now()
    with SessionLocal() as db:
        report = run_pipeline(db)

    new_total = sum(s.new for s in report.ingest)
    errors = [s for s in report.ingest if s.error]
    print(f"[{started:%d/%m %H:%M}] ingestion : {new_total} nouveaux articles "
          f"({len(report.ingest)} sources, {len(errors)} en erreur)")
    for stats in errors:
        print(f"  [ERREUR] {stats.source_name}: {stats.error}")
    if report.scoring is not None:
        ligne = f"  scoring : {report.scoring.scored} scorés, {report.scoring.errors} erreurs"
        # N'afficher le pré-filtre que s'il a servi : sur une installation sans
        # LM Studio, un « 0 écartés » permanent serait du bruit dans le rapport.
        if report.scoring.prefiltered:
            ligne += (
                f", {report.scoring.prefiltered} écartés par le modèle local "
                f"(autant d'appels Claude non payés)"
            )
        print(ligne)
    else:
        print(f"  scoring sauté : {report.scoring_skipped_reason}")
    if report.alerts_skipped_reason is None:
        print(f"  alertes : {report.alerts_sent} envoyées, {report.alerts_errors} erreurs")
    else:
        print(f"  alertes sautées : {report.alerts_skipped_reason}")
    print(f"  rétention : {report.purged} articles purgés (> {settings.retention_days} j)")
    if report.problemes:
        print("  \u26a0 supervision :")
        for probleme in report.problemes:
            print(f"      {probleme}")


if __name__ == "__main__":
    main()
