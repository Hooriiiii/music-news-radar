import datetime as dt
import logging
import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.delivery.notify import notifier as notifier_par_defaut
from app.models import Article, Source, SourceType
from app.pipeline.dedup import compute_raw_hash
from app.pipeline.health import marquer_sante
from app.pipeline.quota_graph import declarer_quota, pause_en_cours
from app.pipeline.radar import top_artists
from app.pipeline.retention import _within_retention
from app.sources import get_adapter as default_get_adapter
from app.sources.base import SourceAdapter
from app.sources.instagram import QuotaGraphAtteint

logger = logging.getLogger(__name__)


@dataclass
class IngestStats:
    source_id: int
    source_name: str
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    error: str | None = None
    # La source n'a PAS été appelée, et ce n'est pas une panne : quota Graph en
    # pause. Une source non appelée n'est pas une source en erreur -- confondre
    # les deux a produit des « 116 source(s) en erreur » qui noyaient la vraie
    # panne sous 115 mensonges, et rendaient la supervision inutilisable.
    reportee: str | None = None


def ingest_source(db: Session, source: Source, adapter: SourceAdapter | None = None) -> IngestStats:
    """Fetch une source, déduplique par raw_hash et insère les nouveaux articles."""
    adapter = adapter or default_get_adapter(source)
    stats = IngestStats(source_id=source.id, source_name=source.name)
    items = adapter.fetch()
    stats.fetched = len(items)
    # On n'ingère (ni ne score) pas ce qui est déjà hors fenêtre de rétention :
    # inutile de payer le scoring d'un article qui serait purgé aussitôt
    items = [i for i in items if _within_retention(i.published_at, settings.retention_days)]
    if not items:
        return stats

    hashes = [compute_raw_hash(item.url, item.title) for item in items]
    seen = set(db.scalars(select(Article.raw_hash).where(Article.raw_hash.in_(hashes))))
    for item, raw_hash in zip(items, hashes):
        if raw_hash in seen:
            stats.duplicates += 1
            continue
        seen.add(raw_hash)
        db.add(
            Article(
                source_id=source.id,
                url=item.url[:2000],
                title=item.title,
                summary=item.summary,
                published_at=item.published_at,
                raw_hash=raw_hash,
                media=item.media or None,
            )
        )
        stats.new += 1
    db.commit()
    return stats


def run_ingest(
    db: Session,
    get_adapter=default_get_adapter,
    shuffle=random.shuffle,
    maintenant: dt.datetime | None = None,
    notify=notifier_par_defaut,
) -> list[IngestStats]:
    """Ingère toutes les sources actives. Une source qui casse n'arrête pas le run.

    L'ordre est mélangé à chaque run : certains hôtes (Reddit) ne laissent passer
    qu'une requête par IP de datacenter, et un ordre fixe sacrifierait toujours
    les mêmes sources.

    COUPE-CIRCUIT DE QUOTA (2026-09-19). Un appel Graph refusé pour quota
    consomme quand même du quota : insister sur les 123 comptes Instagram
    alimentait le dépassement de l'heure suivante, et le système se maintenait
    en panne tout seul (voir `app/pipeline/quota_graph.py`). Au premier code de
    quota, on cesse d'appeler Meta pour ce run et on pose une pause partagée.
    Le run CONTINUE sur tout le reste : les flux RSS et Reddit ne partagent pas
    ce quota, et un dépassement Meta ne doit jamais empêcher d'ingérer Mixmag.
    """
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    pause = pause_en_cours(maintenant)
    sources = list(db.scalars(select(Source).where(Source.active)))
    shuffle(sources)
    results = []
    for source in sources:
        if source.type == SourceType.INSTAGRAM and pause is not None:
            # Pas d'appel, donc pas de `marquer_sante` : une pause de quota ne
            # doit pas effacer ce que l'on savait de la santé de cette source.
            results.append(
                IngestStats(
                    source_id=source.id,
                    source_name=source.name,
                    reportee=f"pause quota Graph jusqu'à {pause.jusqu_a:%H:%M}",
                )
            )
            continue
        try:
            adapter = get_adapter(source)
            # Radar : injecter les artistes du moment (l'adapter reste agnostique
            # de la base, c'est le pipeline qui calcule et fournit la liste)
            if getattr(adapter, "is_radar", False):
                adapter.radar_artists = top_artists(
                    db,
                    window_days=settings.radar_window_days,
                    min_relevance=settings.radar_min_relevance,
                    limit=settings.radar_max_artists,
                )
            stats = ingest_source(db, source, adapter=adapter)
        except QuotaGraphAtteint as exc:
            # Refusé pour quota : la source n'est pas cassée, on ne l'a pas lue.
            # On s'arrête au PREMIER code, ici et pour les passages suivants.
            db.rollback()
            pause = declarer_quota(maintenant, str(exc), "ingestion horaire", notify=notify)
            results.append(
                IngestStats(
                    source_id=source.id,
                    source_name=source.name,
                    reportee=f"quota Graph atteint : {exc}",
                )
            )
            continue
        except Exception as exc:
            logger.exception("Ingestion échouée pour la source %s", source.name)
            db.rollback()
            stats = IngestStats(source_id=source.id, source_name=source.name, error=str(exc))
        # Consigné pour TOUTES les issues, succès comme échec : c'est cette trace
        # qui permet de repérer une source morte, les articles étant purgés à
        # 14 jours et ne pouvant donc pas en témoigner.
        marquer_sante(source, stats)
        db.commit()
        results.append(stats)

    reportees = [s for s in results if s.reportee]
    if reportees:
        # Journalisé à part des erreurs : ces sources ne sont pas en panne, on
        # ne les a pas appelées. Les mélanger rendrait le log illisible, comme
        # il l'était le 18/09 avec ses « 116 source(s) en erreur ».
        logger.info(
            "%s source(s) Instagram non appelées (%s)", len(reportees), reportees[0].reportee
        )
    return results
