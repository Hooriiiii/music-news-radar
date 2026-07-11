from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/music_news_radar"
    sql_echo: bool = False

    # Scoring Claude (étape 4)
    anthropic_api_key: str | None = None
    scoring_model: str = "claude-haiku-4-5"
    # Seuils du design doc
    digest_relevance_threshold: int = 60
    alert_hotness_threshold: int = 80
    # Un article "hot" publié il y a plus longtemps que ça n'est plus une alerte
    alert_max_age_hours: int = 48

    # X via l'API officielle v2, pay-per-use (~0,005 $/tweet lu, since_id natif)
    x_bearer_token: str | None = None
    x_max_items: int = 50  # max_results par requête timeline (borne API : 5-100)
    # Mode recherche (hashtags) : volume potentiellement énorme -> cap serré
    # (borne API : 10-100) + throttle x_min_fetch_interval_hours appliqué.
    # Plafond de coût = max_results x runs/jour x 0,005 $
    x_search_max_results: int = 10
    # Sélection par popularité en mode recherche : un tweet passe s'il vient
    # d'un compte vérifié OU s'il atteint l'un de ces seuils d'engagement
    # Calibré sur sonde réelle (2026-07-06) : le flux "noms d'artistes" est
    # propre, 50 attrape les clips qui décollent sans reprendre le bruit
    x_search_min_likes: int = 50
    x_search_min_replies: int = 20
    # Mode UGC (préfixe "ugc:" dans l'url de la source) : au-delà de ce nombre
    # d'abonnés, un compte est considéré média/agrégateur -- pas de l'UGC
    x_ugc_max_followers: int = 25000

    # Alternative Apify (nécessite un plan Apify payant -- non utilisée par défaut)
    apify_token: str | None = None
    apify_actor_id: str = "apidojo~tweet-scraper"
    apify_max_charge_usd: float = 0.25
    x_min_fetch_interval_hours: int = 6

    # Livraison (étape 5)
    discord_webhook_url: str | None = None
    # Mention ajoutée aux alertes pour déclencher une vraie notification
    # (les webhooks sans mention ne pingent pas avec le réglage Discord par
    # défaut "Mentions @ uniquement"). Mettre vide pour désactiver.
    discord_mention: str | None = "@here"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    digest_to: str | None = None
    digest_from: str | None = None  # défaut : smtp_user
    digest_max_articles: int = 25
    # Anti-monopole : une source prolifique ne peut pas écraser les autres
    digest_max_per_source: int = 6


settings = Settings()
