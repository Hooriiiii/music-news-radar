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
    if report.duplicates_frais_arbitres or report.duplicates_gabarits_ecartes:
        # Bande fraiche 0.30-0.65 : ce que Claude a lie et ce que l'anti-gabarit
        # a evite de payer. Un compteur qui ne s'affiche nulle part est mort.
        print(f"  bande fraiche : {report.duplicates_frais_arbitres} lien(s) tranche(s) "
              f"par Claude, {report.duplicates_gabarits_ecartes} paire(s) ecartee(s) "
              f"sans appel (gabarit de source)")
    if report.duplicates_resumes_arbitres or report.duplicates_recurrences_ecartees:
        # Porte sur le resume : des paires que la dedup par titre ne voyait pas
        # du tout. Un compteur qui ne s'affiche nulle part est mort.
        print(f"  porte resume : {report.duplicates_resumes_arbitres} lien(s) tranche(s) "
              f"par Claude sur les resumes, {report.duplicates_recurrences_ecartees} "
              f"paire(s) ecartee(s) sans appel (publication recurrente)")
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
    observation = report.observation_evenements
    if observation is not None and observation.tables_absentes:
        # La couche evenement est DORMANTE, pas en panne et pas au repos : sans
        # cette ligne, une base ou la migration 0019 n'est pas jouee ressemble
        # exactement a un regime de croisiere sans rien a rattacher. Les deux
        # etapes partagent le meme diagnostic, une seule ligne suffit.
        print("  évènements : couche inactive (migration 0019 non jouée sur cette base)")
    elif observation is not None and (observation.evenements_crees or observation.articles_rattaches):
        # Phase 2B : ce que le passage a fait entrer dans un evenement. Muet
        # quand il n'y a rien eu a rattacher -- le regime de croisiere ne doit
        # pas remplir le log d'une ligne a zero toutes les heures.
        # Les deux compteurs ne disent PAS la meme chose et le libelle doit le
        # rendre evident : « cree » = un article isole qui recoit son propre
        # evenement, « rejoint » = un doublon qui entre dans l'evenement de son
        # groupe. Un « 0 rattache » a cote de « 200 crees » se lisait comme un
        # echec alors que 200 articles venaient d'entrer dans la couche.
        ligne = (
            f"  évènements : {observation.evenements_crees} créé(s) pour un article isolé, "
            f"{observation.articles_rattaches} article(s) rejoignant un évènement existant"
        )
        if observation.plafond_atteint:
            # Un arriere qui se resorbe sur quelques passages est normal ; un
            # plafond atteint a CHAQUE passage veut dire qu'on n'absorbe plus
            # le flux, et rien d'autre ne le dirait.
            ligne += " (plafond atteint, suite au prochain passage)"
        print(ligne)

    print(f"  rétention : {report.purged} articles purgés (> {settings.retention_days} j)")
    purge_ev = report.purge_evenements
    if purge_ev is not None and purge_ev.evenements_supprimes:
        # Ce qui meurt avec son article : une seule preuve, sous le seuil,
        # jamais publie. Deux preuves ou une publication = permanent.
        print(
            f"  rétention : {purge_ev.evenements_supprimes} évènement(s) sans valeur "
            f"purgé(s), {purge_ev.observations_supprimees} observation(s)"
        )
    if report.problemes:
        print("  \u26a0 supervision :")
        for probleme in report.problemes:
            print(f"      {probleme}")
    if report.alerte_jeton_sourcing:
        print(f"  \u26a0 jeton sourcing Instagram : {report.alerte_jeton_sourcing}")


if __name__ == "__main__":
    main()
