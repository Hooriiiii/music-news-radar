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
    if report.duplicates_linked:
        # L'archive a part : un lien contre une actu deja publiee sort un article
        # neuf de la file pour de bon -- un faux positif doit se voir ici.
        # `arbitres` : sous-ensemble tranche par Claude (bande douteuse), pas
        # par le seuil "certain" -- Minor M2, sans lui des credits epuises ne
        # se voyaient que dans les logs, en WARNING repetes.
        print(f"  doublons : {report.duplicates_linked} lies, dont "
              f"{report.duplicates_archive} contre une actu deja publiee "
              f"({report.duplicates_arbitres} tranches par Claude)")
    if report.groupement_sources is not None and report.groupement_sources.sources_regroupees:
        print(
            f"  regroupement de sources : {report.groupement_sources.sources_regroupees} "
            f"source(s) regroupee(s), {report.groupement_sources.evenements_recalcules} "
            f"evenement(s) recalcule(s)"
        )
    if report.groupement_sources is not None and report.groupement_sources.avertissements:
        # Un groupe saute en silence (source renommee au dashboard, conflit
        # deja pose) fausserait la corroboration sans que rien ne le dise --
        # deja loggue en WARNING par `regrouper_sources_et_propager`, mais
        # invisible tant que ce script n'affiche que `sources_regroupees`.
        for avertissement in report.groupement_sources.avertissements:
            print(f"  ⚠ regroupement de sources : {avertissement}")
    if report.groupement_sources is not None and report.groupement_sources.erreur:
        print(f"  ⚠ regroupement de sources : {report.groupement_sources.erreur}")
    if report.duplicates_arbitrage_pannes:
        print(f"  ⚠ arbitrage de doublons : {report.duplicates_arbitrage_pannes} "
              f"panne(s) (paire redemandee au prochain passage)")
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
    if report.illustres:
        print(f"  illustration : {report.illustres} articles enrichis (og:image)")
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
    if report.alerte_jeton_sourcing:
        print(f"  \u26a0 jeton sourcing Instagram : {report.alerte_jeton_sourcing}")


if __name__ == "__main__":
    main()
