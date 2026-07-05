"""Construit et envoie le digest quotidien par mail.

Usage :
    uv run python -m scripts.send_digest [--dry-run] [--date YYYY-MM-DD]

--dry-run : génère l'aperçu HTML dans /tmp/digest_preview.html sans rien
            écrire en base ni envoyer de mail.
"""

import argparse
import datetime as dt
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.delivery.digest import build_digest, render_digest, render_digest_text
from app.delivery.email import send_email


def main() -> None:
    parser = argparse.ArgumentParser(description="Envoie le digest quotidien")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", default=None, help="Date du digest (défaut : aujourd'hui)")
    args = parser.parse_args()
    for_date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    with SessionLocal() as db:
        digest, articles, created = build_digest(db, for_date=for_date,
                                                 persist=not args.dry_run)
        if not articles:
            print(f"Aucun article au-dessus du seuil pour le {for_date:%d/%m/%Y} — pas de digest.")
            return

        html = render_digest(for_date, articles)
        if args.dry_run:
            preview = Path("/tmp/digest_preview.html")
            preview.write_text(html, encoding="utf-8")
            print(f"{len(articles)} articles sélectionnés — aperçu : {preview}")
            return

        if digest.sent_at is not None:
            print(f"Digest du {for_date:%d/%m/%Y} déjà envoyé le {digest.sent_at:%d/%m %H:%M}.")
            return
        subject = f"🎧 Digest musique du {for_date:%d/%m/%Y} — {len(articles)} actus"
        send_email(subject, html, render_digest_text(for_date, articles))
        digest.sent_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        print(f"Digest envoyé à {settings.digest_to} ({len(articles)} articles).")


if __name__ == "__main__":
    main()
