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
from app.pipeline.health import diagnostiquer
from app.pipeline.illustrations import enrichir_articles
from app.pipeline.ingest import IngestStats, run_ingest
from app.pipeline.retention import purge_old_articles
from app.pipeline.scoring import ScoringStats, score_pending
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
    return link_duplicates(db, arbitre=make_claude_arbitre())


def run_pipeline(
    db: Session,
    ingest=run_ingest,
    cluster=dedupliquer_avec_arbitrage,
    elire=reevaluer_clusters,
    score=score_pending,
    record=record_mentions,
    lier_artistes=lier_artistes_comptes,
    illustrer=enrichir_articles,
    alert=send_hot_alerts,
    purge=purge_old_articles,
    notify=notifier,
    surveiller_jeton=surveiller_jeton_sourcing,
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
    doublons = cluster(db)
    report.duplicates_linked = doublons.total
    report.duplicates_archive = doublons.archive
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

    # Rétention : on ne garde que les articles récents (par date de post)
    report.purged = purge(db, settings.retention_days)

    # Supervision en DERNIER : elle observe le run entier, y compris la purge.
    # Trois pannes sont passees inapercues faute de ce controle -- l'agent
    # digest cinq semaines, une source X figee sept jours, les credits epuises.
    report.problemes = diagnostiquer(db, report.ingest, report.scoring)
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
