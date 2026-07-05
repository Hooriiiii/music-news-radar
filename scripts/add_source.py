"""Ajoute une source de veille en base.

Usage :
    uv run python -m scripts.add_source --name "r/techno" --type reddit_rss \
        --url https://www.reddit.com/r/techno --genre techno
"""

import argparse

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Source, SourceType


def main() -> None:
    parser = argparse.ArgumentParser(description="Ajoute une source de veille")
    parser.add_argument("--name", required=True)
    parser.add_argument("--type", required=True, choices=[t.value for t in SourceType])
    parser.add_argument("--url", required=True)
    parser.add_argument("--genre", default=None)
    args = parser.parse_args()

    with SessionLocal() as db:
        existing = db.scalar(select(Source).where(Source.url == args.url))
        if existing:
            print(f"Source déjà présente (id={existing.id}) : {existing.name}")
            return
        source = Source(name=args.name, type=SourceType(args.type), url=args.url,
                        genre=args.genre)
        db.add(source)
        db.commit()
        print(f"Source ajoutée (id={source.id}) : {source.name} [{source.type.value}]")


if __name__ == "__main__":
    main()
