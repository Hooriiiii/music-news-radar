"""Score les articles pas encore évalués via Claude.

Usage :
    uv run python -m scripts.score_articles [--limit N]
"""

import argparse
import logging

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Article
from app.pipeline.scoring import score_pending


def main() -> None:
    parser = argparse.ArgumentParser(description="Score les articles non évalués")
    parser.add_argument("--limit", type=int, default=None,
                        help="Nombre max d'articles à scorer (contrôle du coût)")
    args = parser.parse_args()

    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY manquante dans .env — clé à créer sur console.anthropic.com")
        raise SystemExit(1)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    with SessionLocal() as db:
        stats = score_pending(db, limit=args.limit)
        print(f"{stats.scored} articles scorés, {stats.errors} erreurs "
              f"(modèle : {settings.scoring_model})")

        digest_ready = db.scalars(
            select(Article)
            .where(Article.relevance_score >= settings.digest_relevance_threshold)
            .order_by(Article.relevance_score.desc())
            .limit(10)
        ).all()
        if digest_ready:
            print(f"\nTop pertinence (>= {settings.digest_relevance_threshold}, "
                  f"candidats digest) :")
            for a in digest_ready:
                hot = " [HOT]" if a.hotness_score >= settings.alert_hotness_threshold else ""
                print(f"  {a.relevance_score:>3} | hot {a.hotness_score:>3}{hot} | "
                      f"{a.category:<15} | {a.title[:60]}")


if __name__ == "__main__":
    main()
