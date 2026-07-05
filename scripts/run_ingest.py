"""Ingestion : fetch de toutes les sources actives, dédup, insertion en base.

Usage :
    uv run python -m scripts.run_ingest
"""

import logging

from app.db import SessionLocal
from app.pipeline.ingest import run_ingest


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    with SessionLocal() as db:
        all_stats = run_ingest(db)

    if not all_stats:
        print("Aucune source active en base — ajoute des sources via scripts/add_source.py")
        return
    for stats in all_stats:
        if stats.error:
            print(f"[ERREUR] {stats.source_name}: {stats.error}")
        else:
            print(
                f"{stats.source_name}: {stats.fetched} récupérés, "
                f"{stats.new} nouveaux, {stats.duplicates} doublons"
            )


if __name__ == "__main__":
    main()
