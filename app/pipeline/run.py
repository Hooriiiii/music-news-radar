import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import settings
from app.delivery.discord import send_hot_alerts
from app.delivery.notify import notifier
from app.pipeline.artists import record_mentions
from app.pipeline.cluster import ClusterStats, link_duplicates
from app.pipeline.cluster_arbitrage import make_claude_arbitre
from app.pipeline.comptes_artistes import construire_index, lier_artistes_comptes
from app.pipeline.election import ElectionStats, elire_representants
from app.pipeline.events import ObservationStats, observer_articles
from app.pipeline.health import diagnostiquer
from app.pipeline.illustrations import enrichir_articles
from app.pipeline.ingest import IngestStats, run_ingest
from app.pipeline.retention import (
    PurgeEvenementsStats,
    purge_old_articles,
    purger_evenements_sans_valeur,
)
from app.pipeline.scoring import ScoringStats, score_pending
from app.pipeline.source_groups import GroupementStats, regrouper_sources_et_propager
from app.sources.instagram_jeton import surveiller_jeton_sourcing

logger = logging.getLogger(__name__)


@dataclass
class PipelineReport:
    ingest: list[IngestStats] = field(default_factory=list)
    duplicates_linked: int = 0
    # Les liens poses CONTRE L'ARCHIVE (une actu deja publiee), comptes a part :
    # ce sont les seuls qui sortent un article neuf de la file pour de bon, donc
    # les seuls dont un faux positif coute une actu (cf. `cluster.ClusterStats`).
    duplicates_archive: int = 0
    # Sous-ensemble de `duplicates_archive` : liens tranches par Claude (bande
    # douteuse), et pannes d'arbitrage (jamais un lien, mais des credits
    # epuises ne se voyaient sinon que par des WARNING repetes dans les logs).
    duplicates_arbitres: int = 0
    duplicates_arbitrage_pannes: int = 0
    # Phase 2B, Task 3 : sous-ensemble de `duplicates_linked` tranche par Claude
    # dans la bande FRAICHE 0.30-0.65, et paires de cette bande ecartees sans
    # rien payer par l'anti-gabarit. Comptes a part de l'archive : ni la meme
    # mesure, ni le meme risque.
    duplicates_frais_arbitres: int = 0
    duplicates_gabarits_ecartes: int = 0
    # Porte de candidature sur le RESUME (2026-09-24) : sous-ensemble de
    # `duplicates_linked` pose entre deux articles dont les TITRES ne se
    # rapprochent pas du tout, et paires ecartees sans rien payer parce qu'un
    # des deux est une PUBLICATION RECURRENTE de sa source.
    duplicates_resumes_arbitres: int = 0
    duplicates_recurrences_ecartees: int = 0
    duplicates_resumes_archive_arbitres: int = 0
    duplicates_titres_vides_ecartes: int = 0
    groupement_sources: GroupementStats | None = None
    # Phase 2B, Task 1 : les articles qui viennent d'entrer dans un evenement
    # (y compris seuls). `None` = etape jamais atteinte.
    observation_evenements: ObservationStats | None = None
    election: ElectionStats | None = None
    scoring: ScoringStats | None = None
    mentions_recorded: int = 0
    artistes_lies: int = 0
    illustres: int = 0
    scoring_skipped_reason: str | None = None
    alerts_sent: int = 0
    alerts_errors: int = 0
    alerts_skipped_reason: str | None = None
    purged: int = 0
    # Phase 2B, Task 2 : les evenements sans valeur supprimes AVANT la purge
    # des articles (voir l'ordre plus bas). `None` = etape jamais atteinte.
    purge_evenements: PurgeEvenementsStats | None = None
    # Ce que la supervision a trouvé. Vide = rien à signaler.
    problemes: list[str] = field(default_factory=list)
    # Echeance proche du jeton de la source Instagram (notifiee une fois par jour).
    alerte_jeton_sourcing: str | None = None


def reevaluer_clusters(db: Session) -> ElectionStats:
    """Le representant le plus informatif de chaque groupe recent (48 h)."""
    return elire_representants(db, construire_index(db))


def dedupliquer_avec_arbitrage(db: Session) -> ClusterStats:
    """Le cluster PAR DEFAUT du passage horaire, seul a arbitrer (Task 7,
    2026-09-21) : `run_veille` (toutes les 5 min) appelle `link_duplicates`
    sans argument, donc sans jamais construire cet arbitre -- distinction par
    PARAMETRE, pas par le nom de l'appelant. Construit un client Claude a
    chaque appel (jamais au chargement du module) : un `.env` de test peut
    ainsi neutraliser `cluster_arbitrage_enabled` ou `anthropic_api_key` sans
    qu'un client fantome ait deja ete cree avec une mauvaise cle."""
    if not settings.cluster_arbitrage_enabled or not settings.anthropic_api_key:
        return link_duplicates(db)
    # La bande FRAICHE (Phase 2B, Task 3) suit le meme principe : c'est un
    # PARAMETRE qui l'ouvre, jamais le nom de l'appelant. `run_veille` ne le
    # passe pas, donc n'arbitre jamais ; et `cluster_arbitrage_frais_enabled`
    # a false le laisse a None, ce qui rend au pipeline exactement les
    # decisions qu'il prenait avant ce chantier.
    frais_min = (
        settings.cluster_arbitrage_frais_seuil
        if settings.cluster_arbitrage_frais_enabled
        else None
    )
    # La PORTE SUR LE RESUME (2026-09-24) suit exactement la meme convention :
    # un PARAMETRE l'ouvre, jamais le nom de l'appelant. `run_veille` ne le
    # passe pas ; `cluster_arbitrage_resume_enabled` a false le laisse a None,
    # ce qui rend au pipeline exactement les decisions d'avant ce chantier.
    resume_min = (
        settings.cluster_arbitrage_resume_seuil
        if settings.cluster_arbitrage_resume_enabled
        else None
    )
    # La meme porte CONTRE L'ARCHIVE (2026-10-02), meme convention encore : un
    # PARAMETRE l'ouvre, jamais le nom de l'appelant. `run_veille` ne le passe
    # pas ; `cluster_arbitrage_resume_archive_enabled` a false le laisse a None,
    # ce qui rend au pipeline exactement les decisions d'avant ce correctif.
    resume_archive_min = (
        settings.cluster_arbitrage_resume_archive_seuil
        if settings.cluster_arbitrage_resume_archive_enabled
        else None
    )
    return link_duplicates(
        db,
        arbitre=make_claude_arbitre(),
        arbitrage_frais_min=frais_min,
        arbitrage_resume_min=resume_min,
        arbitrage_resume_archive_min=resume_archive_min,
    )


def run_pipeline(
    db: Session,
    ingest=run_ingest,
    cluster=dedupliquer_avec_arbitrage,
    elire=reevaluer_clusters,
    score=score_pending,
    record=record_mentions,
    lier_artistes=lier_artistes_comptes,
    illustrer=enrichir_articles,
    observer=observer_articles,
    alert=send_hot_alerts,
    purger_evenements=purger_evenements_sans_valeur,
    purge=purge_old_articles,
    notify=notifier,
    surveiller_jeton=surveiller_jeton_sourcing,
    grouper_sources=regrouper_sources_et_propager,
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

    # Avant le cluster : un media qui vient de rejoindre son groupe (RSS +
    # Instagram, voir `scripts/reparations/grouper_sources.py`) doit compter pour une
    # seule confirmation independante des le meme passage, pas seulement au
    # suivant. `regrouper_sources_et_propager` ne leve deja jamais (voir
    # `app.pipeline.source_groups`) ; ce `try/except` est une seconde
    # ceinture, sur le modele de `surveiller_jeton` plus bas -- une injection
    # de test ou une future implementation qui laisserait echapper une
    # exception ne doit quand meme jamais interrompre le passage horaire.
    try:
        report.groupement_sources = grouper_sources(db)
    except Exception:
        logger.exception("Regroupement des sources impossible")
        try:
            db.rollback()
        except Exception:
            logger.exception("Rollback impossible apres l'echec du regroupement des sources")

    doublons = cluster(db)
    report.duplicates_linked = doublons.total
    report.duplicates_archive = doublons.archive
    report.duplicates_arbitres = doublons.arbitres
    report.duplicates_arbitrage_pannes = doublons.arbitrage_pannes
    report.duplicates_frais_arbitres = doublons.frais_arbitres
    report.duplicates_gabarits_ecartes = doublons.gabarits_ecartes
    report.duplicates_resumes_arbitres = doublons.resumes_arbitres
    report.duplicates_recurrences_ecartees = doublons.recurrences_ecartees
    report.duplicates_resumes_archive_arbitres = doublons.resumes_archive_arbitres
    report.duplicates_titres_vides_ecartes = doublons.titres_vides_ecartes
    # AVANT le scoring : un doublon promu representant est note dans le meme run,
    # et un representant local de groupe >= 3 sources repasse chez Claude.
    report.election = elire(db)

    if settings.anthropic_api_key:
        report.scoring = score(db)
    else:
        report.scoring_skipped_reason = "ANTHROPIC_API_KEY absente"
        logger.warning("Scoring sauté : %s", report.scoring_skipped_reason)

    # Après le scoring : on ne sait qu'à ce moment-là si l'article mérite qu'on
    # dépense une requête réseau pour l'illustrer.
    report.illustres = illustrer(db).illustres

    # Apres le scoring, avant `record` : tout article recoit un evenement, meme
    # seul (Phase 2B, Task 1). Apres le scoring parce que la pertinence, qui
    # decidera de la purge des evenements sans valeur (Task 2), n'existe qu'a
    # ce moment-la. `observer_articles` ne leve deja jamais ; ce `try/except`
    # est une seconde ceinture, sur le modele de `grouper_sources` plus haut --
    # une injection de test ou une future implementation qui laisserait
    # echapper une exception ne doit quand meme jamais interrompre le passage.
    try:
        report.observation_evenements = observer(db)
    except Exception:
        logger.exception("Rattachement des articles a un evenement impossible")
        try:
            db.rollback()
        except Exception:
            logger.exception("Rollback impossible apres l'echec du rattachement aux evenements")

    # Après le scoring, avant la purge : les artistes cités sont archivés pour de
    # bon, même quand les articles qui les mentionnaient seront supprimés
    report.mentions_recorded = record(db)
    # Apres les mentions : une fiche artiste nouvelle se lie a son compte suivi,
    # l'index du run suivant la connait.
    report.artistes_lies = lier_artistes(db)

    if settings.discord_webhook_url:
        stats = alert(db)
        report.alerts_sent = stats.sent
        report.alerts_errors = stats.errors
    else:
        report.alerts_skipped_reason = "DISCORD_WEBHOOK_URL absente"
        logger.warning("Alertes sautées : %s", report.alerts_skipped_reason)

    # AVANT la purge des articles, et c'est un INVARIANT, pas un detail d'ordre
    # (Phase 2B, Task 2) : `event_observations.article_id` est
    # `ON DELETE SET NULL`, donc une fois l'article efface l'observation survit
    # sans lui et plus rien ne permet de juger sa pertinence ni son statut.
    # Appelee apres `purge`, cette etape ne trouverait plus aucun candidat et la
    # table `events` grossirait quand meme, en silence. Verrouille par
    # `tests/pipeline/test_retention_evenements.py`.
    # `purger_evenements_sans_valeur` ne leve deja jamais ; ce `try/except` est
    # une seconde ceinture, comme pour `grouper_sources` et `observer`.
    try:
        report.purge_evenements = purger_evenements(db, settings.retention_days)
    except Exception:
        logger.exception("Purge des evenements sans valeur impossible")
        try:
            db.rollback()
        except Exception:
            logger.exception("Rollback impossible apres l'echec de la purge des evenements")

    # Rétention : on ne garde que les articles récents (par date de post)
    report.purged = purge(db, settings.retention_days)

    # Supervision en DERNIER : elle observe le run entier, y compris la purge.
    # Trois pannes sont passees inapercues faute de ce controle -- l'agent
    # digest cinq semaines, une source X figee sept jours, les credits epuises.
    report.problemes = diagnostiquer(db, report.ingest, report.scoring)
    if report.groupement_sources is not None and report.groupement_sources.erreur:
        # Sans cette ligne, une panne de regroupement des sources ne serait
        # jamais notifiee : `diagnostiquer` ne regarde que l'ingestion et le
        # scoring. Meme forme exactement que l'agent digest qui a echoue
        # chaque jour pendant cinq semaines sans que rien ne le signale.
        report.problemes.append(
            f"regroupement de sources : {report.groupement_sources.erreur}"
        )
    if report.observation_evenements is not None and report.observation_evenements.erreur:
        # Meme motif que le regroupement de sources juste au-dessus : un
        # compteur qui n'est affiche nulle part est un compteur mort, et une
        # panne silencieuse a deja coute cinq semaines a ce depot.
        report.problemes.append(
            f"rattachement aux evenements : {report.observation_evenements.erreur}"
        )
    if report.purge_evenements is not None and report.purge_evenements.erreur:
        # Meme motif : un compteur qui n'est affiche nulle part est un compteur
        # mort, et une purge qui ne tourne plus est une fuite lente que rien ne
        # signalerait autrement.
        report.problemes.append(
            f"purge des evenements : {report.purge_evenements.erreur}"
        )
    if report.problemes:
        try:
            notify("Kinda", " · ".join(report.problemes))
        except Exception:
            # Le canal d'alerte est la DERNIERE chose qui doit pouvoir tuer le
            # run qu'il surveille. On logue, le rapport garde le diagnostic.
            logger.exception("Notification impossible ; problemes : %s", report.problemes)

    # Le jeton Facebook de la source Instagram ne se rafraichit pas tout seul :
    # prevenir a J-7. Notification separee, dedupliquee par JOUR et non par run.
    try:
        report.alerte_jeton_sourcing = surveiller_jeton(
            dt.datetime.now(dt.UTC), notifier=notify
        )
    except Exception:
        logger.exception("Surveillance du jeton de sourcing Instagram impossible")

    return report
