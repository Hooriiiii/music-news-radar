"""Envoie les alertes Discord pour les articles hot jamais alertés.

Usage :
    uv run python -m scripts.send_hot_alerts
"""

import logging

from app.config import settings
from app.db import SessionLocal
from app.delivery.discord import send_hot_alerts


def main() -> None:
    if not settings.discord_webhook_url:
        print("DISCORD_WEBHOOK_URL manquante dans .env — webhook à créer dans les "
              "paramètres de ton serveur Discord (Intégrations > Webhooks).")
        raise SystemExit(1)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    with SessionLocal() as db:
        stats = send_hot_alerts(db)
    print(f"{stats.sent} alertes envoyées, {stats.errors} erreurs.")


if __name__ == "__main__":
    main()
