from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/music_news_radar"
    sql_echo: bool = False

    # Rétention : on ne garde en base que les articles publiés dans les N derniers
    # jours (par date de POST, pas d'ajout) -- le reste est purgé à chaque run
    retention_days: int = 14

    # Scoring Claude (étape 4)
    anthropic_api_key: str | None = None
    scoring_model: str = "claude-haiku-4-5"
    # Composition des posts : contrairement au scoring (volume eleve, Haiku pour
    # le cout), la composition tourne quelques fois par jour a la demande et sa
    # qualite se voit directement sur le feed -- on paie un modele plus fort.
    composer_model: str = "claude-sonnet-5"
    # --- Pré-filtre local (cascade à deux étages) ---
    # Un petit modèle tournant en local note l'article AVANT Claude : sous le
    # seuil, on n'appelle jamais Claude et on garde la note locale.
    # Mesure du 2026-08 sur 36 articles réels : le modèle local n'a JAMAIS noté
    # sous 60 un article que Haiku gardait au-dessus de 60 -- ses erreurs sont
    # toutes des faux positifs (il laisse passer trop), jamais des faux négatifs.
    # Mauvais juge, bon videur : on l'utilise en pré-filtre, pas en remplaçant.
    #
    # Opt-in : le pipeline doit tourner à l'identique sans LM Studio installé.
    local_scoring_enabled: bool = False
    # Endpoint compatible OpenAI exposé par LM Studio
    local_scoring_url: str = "http://localhost:1234/v1/chat/completions"
    # Modèle chargé dans LM Studio (identifiant tel qu'il l'expose)
    local_scoring_model: str = "qwen/qwen3-4b"
    # Sous cette note de pertinence LOCALE, l'article est écarté sans appel Claude.
    # Volontairement plus bas que le seuil du digest (60) : la mesure porte sur
    # 36 articles, assez pour voir la tendance, pas pour jurer que le zéro faux
    # négatif tient à l'échelle. Ces 15 points sont la marge de sécurité.
    local_prefilter_threshold: int = 45
    # Le modèle local tourne sur CPU/GPU de la machine : quelques secondes par
    # article, mais un modèle qui se charge peut prendre bien plus long
    local_scoring_timeout: float = 120.0

    # Seuils du design doc
    digest_relevance_threshold: int = 60
    alert_hotness_threshold: int = 80
    # Un article "hot" publié il y a plus longtemps que ça n'est plus une alerte
    alert_max_age_hours: int = 48

    # X via l'API officielle v2, pay-per-use (~0,005 $/tweet lu, since_id natif)
    x_bearer_token: str | None = None
    x_max_items: int = 50  # max_results par requête timeline (borne API : 5-100)
    # Au tout premier fetch (pas encore de since_id), ne lire que les tweets des
    # N derniers jours -> pas de backfill payant sur du vieux (borne API : < 7 j)
    x_backfill_days: int = 2
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

    # Radar maison (source X avec url "radar:") : les artistes du moment sont
    # dérivés du flux scoré et injectés dans la recherche X has:videos
    radar_window_days: int = 7
    radar_min_relevance: int = 60
    radar_max_artists: int = 10
    # Seuls les artistes issus de ces genres de source alimentent le radar :
    # les sources festivals (multi-genres) ne doivent pas y injecter du metal/folk.
    # Le modèle ne filtre pas le genre de façon fiable -> gate déterministe ici.
    radar_source_genres: str = "electronic,pop,new_music,music"

    # Alternative Apify (nécessite un plan Apify payant -- non utilisée par défaut)
    apify_token: str | None = None
    apify_actor_id: str = "apidojo~tweet-scraper"
    apify_max_charge_usd: float = 0.25
    x_min_fetch_interval_hours: int = 6

    # --- Supervision : notifications macOS ---
    # Le canal d'alerte du socle local. Discord est coupé et l'utilisateur ne
    # veut pas de mail : sans ça, une panne ne se signale nulle part -- trois
    # sont passées inaperçues (agent digest 5 semaines, source X figée 7 jours,
    # crédits API épuisés).
    notifications_enabled: bool = True
    # Au-delà, une source qui n'a rien ramené est considérée morte et non calme.
    # 7 jours : assez long pour le radar X, muet en semaine par nature ; assez
    # court pour attraper une panne avant qu'elle s'installe.
    source_muette_jours: int = 7

    # --- Illustration des articles (og:image) ---
    # 57 % des articles arrivaient sans média, dont 81 % des articles RSS. La
    # balise og:image des éditeurs couvre 100 % des cas mesurés.
    # Seuil : celui du digest. Un article sous la barre ne sera jamais posté,
    # lui chercher une image serait une requête réseau pour rien.
    illustration_min_relevance: int = 60
    illustration_timeout: float = 20.0
    # Borne le coût réseau d'un run -- surtout au premier passage, où l'arriéré
    # se compte en centaines d'articles.
    illustration_max_par_run: int = 40

    # --- Instagram (connexion Instagram, sans Facebook) ---
    # App Meta « Kinda Radar », compte kindanewsfrance declare testeur. Le jeton
    # du .env n'est que la valeur INITIALE : une fois rafraichi, le jeton courant
    # vit dans `meta_token_path` -- le code ne reecrit jamais .env.
    meta_access_token: str | None = None
    meta_ig_user_id: str | None = None
    # Verifie en lecture seule le 2026-09-16 : v21.0 et v23.0 repondent.
    meta_api_version: str = "v23.0"
    meta_token_path: Path = Path.home() / "Library/Application Support/Kinda/instagram_token.json"

    # --- Publication automatique ---
    # Les fichiers figes au gel (T-2h), un sous-dossier par creneau.
    publication_dir: Path = Path.home() / "Library/Application Support/Kinda/publication"
    # Meta exige des proportions entre 4:5 et 1,91:1 (documentation, 2026-09-16) :
    # la cover 3:4 est recadree en 4:5. A passer a 0.75 seulement si un essai reel
    # montre que Meta accepte le 3:4.
    publication_ratio_min: float = 0.8
    # Fenetres de publication, heure de Paris. L'heure exacte est tiree au hasard
    # dans chaque fenetre, pour ne pas publier comme une horloge.
    publication_fuseau: str = "Europe/Paris"
    publication_fenetre_matin: str = "08:30-11:00"
    publication_fenetre_apres_midi: str = "17:00-19:30"
    # 3 jours x 2 creneaux.
    publication_creneaux: int = 6
    # Eligibilite a la file : la barre du digest, et une actu de moins de 3 jours.
    publication_min_relevance: int = 60
    publication_fraicheur_jours: int = 3
    # arret : l'agent ne fait rien. apercu : tout, jusqu'au conteneur cree chez
    # Meta, SAUF la publication. reel : publie. Publier est public et
    # irreversible : le passage a « reel » est une decision de l'utilisateur.
    publication_mode: Literal["arret", "apercu", "reel"] = "arret"
    # La composition (appel Claude paye) n'a lieu qu'a J-1 : on ne paie jamais
    # un post qui se fait doubler avant demain.
    publication_composition_heures: int = 24
    # Gel a T-2h : rendu et telechargements faits d'avance.
    publication_gel_heures: int = 2
    # Au-dela, on ne publie pas : un Mac rouvert le soir lacherait sinon la
    # journee entiere d'un coup.
    publication_retard_max_minutes: int = 90

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
