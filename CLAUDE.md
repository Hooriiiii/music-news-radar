# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

Veille automatisée sur l'actu musique électronique pour alimenter un compte Instagram.
Pipeline : sources → dédup (hash url_norm + titre) → scoring Claude → Postgres → digest
mail quotidien + alertes Discord temps réel. Seuils : pertinence >= 60 pour le digest,
hotness >= 80 pour l'alerte Discord.

## Commandes

- Installer / synchroniser les deps : `uv sync` (groupe dev inclus par défaut)
- Lancer l'API : `uv run uvicorn app.main:app --reload`
- Ajouter une source : `uv run python -m scripts.add_source --name "..." --type rss|reddit_rss --url ... [--genre ...]`
- Lancer une ingestion : `uv run python -m scripts.run_ingest`
- Scorer les articles : `uv run python -m scripts.score_articles [--limit N]`
  (nécessite `ANTHROPIC_API_KEY` dans `.env` ; modèle : `SCORING_MODEL`,
  défaut `claude-haiku-4-5` — choix validé par l'utilisateur pour le coût)
- Digest mail : `uv run python -m scripts.send_digest [--dry-run] [--date YYYY-MM-DD]`
  (`--dry-run` écrit l'aperçu dans /tmp/digest_preview.html sans toucher la base)
- Alertes Discord : `uv run python -m scripts.send_hot_alerts`
- Run complet (ingestion + scoring + alertes) : `uv run python -m scripts.run_pipeline`
- Scheduler : GitHub Actions (`.github/workflows/` — pipeline toutes les 2 h,
  CI à chaque push). Secrets requis côté repo : DATABASE_URL (Neon),
  ANTHROPIC_API_KEY, DISCORD_WEBHOOK_URL, APIFY_TOKEN.
  Pas d'envoi de mail : l'utilisateur n'en veut pas — le digest n'est PAS
  planifié (le code `app/delivery/{digest,email}.py` et `scripts/send_digest.py`
  restent utilisables à la main ou pour une future UI). Ne pas re-proposer SMTP.
  Cron GitHub = UTC et best-effort (retards de quelques minutes possibles).
  Ancienne alternative locale : agents launchd dans `deploy/`
  (`bash deploy/install_launchd.sh` / `launchctl bootout gui/$(id -u)/com.musicnewsradar.{pipeline,digest}`)
- Tests : `uv run pytest` (un seul test : `uv run pytest tests/test_api.py::test_health`)
- Lint : `uv run ruff check .`
- Migrations : `uv run alembic upgrade head` / `uv run alembic revision --autogenerate -m "..."`
- Parité modèles ↔ migrations : `uv run alembic check` (nécessite une base à jour)

La config vient de `.env` (voir `.env.example`) via `app/config.py` (pydantic-settings).
`alembic/env.py` lit `DATABASE_URL` depuis cette config — ne jamais mettre l'URL dans
`alembic.ini`. Postgres local (EDB, port 5432) protégé par mot de passe : pour vérifier
une migration sans toucher l'instance de l'utilisateur, créer un cluster jetable avec
`/Library/PostgreSQL/15/bin/initdb` sur un autre port.

## Architecture

Le principe central : la couche `app/sources/` est PLUGGABLE (un adapter par source,
contrat défini dans `sources/base.py` : `fetch() -> list[RawItem]`), tout le reste du
pipeline est source-agnostique et ne connaît que `RawItem`. Le registry dans
`sources/__init__.py` mappe le champ `sources.type` en base (`rss`, `reddit_rss`,
`bluesky`, `x`) vers la classe adapter.

- `app/models.py` — les 3 tables (Source, Article, Digest). Enums stockés en VARCHAR +
  CHECK (`native_enum=False`), pas d'enum natif Postgres. JSONB via `JSONVariant`
  (JSON générique sur SQLite pour les tests, JSONB sur Postgres).
- `app/pipeline/` — ingestion : dédup, scoring Claude, orchestration. Sync (pas d'async),
  volumes faibles.
- `app/delivery/` — restitution : digest (Jinja2 + SMTP), alertes Discord (webhook).
  Communique avec le pipeline uniquement via la base.
- `app/api/` — endpoints REST (`/articles`, `/digests`) pour une future UI web.
- `scripts/` — points d'entrée cron : `run_ingest.py` (fréquent, déclenche aussi les
  alertes hot au moment de l'ingestion), `send_digest.py` (quotidien).
- Tests sur SQLite in-memory (fixtures dans `tests/conftest.py`, override de `get_db`).

## Contraintes apprises sur le terrain

- Les headers HTTP (User-Agent) doivent être ASCII pur — pas d'accents, httpx
  plante avant d'envoyer la requête.
- Reddit renvoie des 429 persistants (plusieurs minutes) si deux fetchs arrivent
  coup sur coup : `RedditRssAdapter.request_delay = 5.0` espace les requêtes.
  Le blocage est par IP, pas par subreddit.
- Resident Advisor n'a plus de flux RSS public (`ra.co/xml/rss.xml` → 404).
- Stratégie X validée sur données (2026-07-06) : les ACTUS viennent des
  timelines de comptes curatés (les médias vérifiés ne hashtagent pas — une
  recherche hashtag+is:verified ne produit quasi rien) ; la recherche hashtags
  sert au contenu VIRAL (vidéos d'events, has:videos + seuils d'engagement).
  Les hashtags de genre bruts = ~100 % d'autopromo, toujours passer par la
  sélection popularité/vérifié. Vérifié par 5 sondes (2026-07-06) : même les
  tags de format (#DJset, #TrackID, #FrontRow) et les combos format x genre
  renvoient 0-2 likes / spam — la recherche API v2 expose le flux récent d'un
  hashtag, jamais sa traîne virale, et n'a pas d'opérateur min likes. Le viral
  X s'attrape via des timelines de comptes curateurs, OU via le mode UGC :
  recherche par NOMS D'ARTISTES en texte libre (pas de hashtags) + has:videos,
  préfixe "ugc:" sur l'url de la source -> sélection = engagement suffisant ET
  compte modeste (<= x_ugc_max_followers ; un gros compte = agrégateur, exclu
  même viral). L'utilisateur veut du brut filmé par des fans, PAS les clips
  léchés des médias — le prompt de scoring valorise ces clips UGC (60-80) au
  lieu de les classer en bruit perso. Source calme en semaine, productive les
  soirs d'events — c'est normal.
- X passe par l'API OFFICIELLE v2 pay-per-use (`app/sources/x_api.py`,
  ~0,005 $/tweet lu, since_id natif dans sources.state) — nécessite la
  facturation activée sur developer.x.com, sinon 402 Payment Required.
  L'adapter Apify (`x_apify.py`) reste en alternative : l'actor
  `apidojo~tweet-scraper` interdit l'API sur le plan Apify gratuit
  (runs "SUCCEEDED" mais `{"noResults": true}`) — plan payant ~39 $/mois requis.
- Depuis les runners GitHub Actions (IP datacenter Azure), certaines sources
  bloquent par intermittence : Mixmag (403 Cloudflare), Reddit (429). L'IP du
  runner change à chaque run → les blocages se rattrapent naturellement sur les
  48 runs quotidiens. Si un blocage devient systématique : passer Reddit sur son
  API officielle OAuth (fiable depuis les datacenters), remplacer la source RSS.
- Postgres local : auth en `trust` (pas de mot de passe), user `postgres`.

## Conventions

- SQLAlchemy 2.0 style (`Mapped` / `mapped_column`), naming convention des contraintes
  définie dans `app/db.py`. Dans les migrations écrites à la main, entourer les noms de
  contraintes CHECK avec `op.f()` sinon la convention double le préfixe.
- Source X/Apify : adapter séparé avec `since_id` et plafond `maxTotalChargeUsd` —
  à implémenter en dernier (source payante).

## Roadmap (validée)

1. ~~Structure de dossiers~~ ✓
2. ~~Scaffold FastAPI + Postgres + 3 tables + migration~~ ✓
3. ~~Adapters gratuits : RSS générique (feedparser) + Reddit RSS, dédup par hash~~ ✓
4. ~~Scoring Claude (JSON strict : pertinence, hotness, catégorie, imprint, résumé)~~ ✓
   (`app/pipeline/scoring.py` : structured outputs via `messages.parse()`, seuils dans
   `app/config.py`, commit par article pour ne pas perdre les appels payés)
5. ~~Digest mail (Jinja2) + webhook Discord~~ ✓
   (`app/delivery/` — digest idempotent par date, fenêtre depuis le digest précédent
   avec exclusion de ses articles ; `articles.alerted_at` (migration 0002) garantit
   qu'un article hot n'est alerté qu'une fois, posé seulement après envoi réussi.
   Limitation connue : la dédup par hash ne fusionne pas la même actu couverte par
   deux sources différentes — amélioration possible en V2.)
6. ~~Scheduler (digest quotidien, ingestion fréquente)~~ ✓
   (launchd plutôt que cron — natif macOS, rattrape les jobs manqués à la sortie
   de veille. Plists dans `deploy/`. Sur un futur VPS Linux : équivalents crontab
   `*/30 * * * *` pour le pipeline et `30 8 * * *` pour le digest.)
7. ~~Adapter X via Apify~~ ✓ — V1 complète.
   (`app/sources/x_apify.py`, actor `apidojo~tweet-scraper` ~0,40 $/1000 tweets avec
   **minimum 50 tweets facturés par requête** — d'où trois garde-fous de coût :
   `maxTotalChargeUsd` par run, `maxItems`, et un throttle de 6 h entre fetchs par
   source (état dans `sources.state` JSONB, migration 0003, avec le `since` qui
   évite de re-payer les tweets déjà vus). Réassigner `source.state` en entier,
   jamais de mutation in place, sinon SQLAlchemy ne persiste pas.)
