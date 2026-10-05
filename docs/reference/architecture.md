# Architecture, conventions, roadmap

**Quand lire ce fichier** : avant d'ajouter une source, une table, une migration
ou un point d'entree ; avant de toucher a `app/models.py`, `app/db.py`,
`alembic/` ou au contrat `app/sources/base.py`. C'est le plan du depot et les
conventions SQLAlchemy / Alembic qui s'y appliquent.

## Architecture

Le principe central : la couche `app/sources/` est PLUGGABLE (un adapter par source,
contrat défini dans `sources/base.py` : `fetch() -> list[RawItem]`), tout le reste du
pipeline est source-agnostique et ne connaît que `RawItem`. Le registry dans
`sources/__init__.py` mappe le champ `sources.type` en base (`rss`, `reddit_rss`,
`bluesky`, `x`, `instagram`) vers la classe adapter.

- `app/models.py` — les 3 tables (Source, Article, Digest). Enums stockés en VARCHAR +
  CHECK (`native_enum=False`), pas d'enum natif Postgres. JSONB via `JSONVariant`
  (JSON générique sur SQLite pour les tests, JSONB sur Postgres).
- `app/pipeline/` — ingestion : dédup, scoring Claude, orchestration. Sync (pas d'async),
  volumes faibles.
- `app/delivery/` — restitution : digest (Jinja2 + SMTP), alertes Discord (webhook).
  Communique avec le pipeline uniquement via la base.
- `app/api/` — endpoints REST (`/articles`, `/digests`) pour une future UI web.
- `scripts/` — points d'entrée. Deux sont planifiés : `run_pipeline.py` enchaîne
  ingestion → dédup → scoring → mentions → purge (via `app/pipeline/run.py`), et
  `run_publication.py` fait un passage de la file de publication (voir
  « Publication automatique »).
  `run_ingest.py` fait de l'ingestion **et rien d'autre** et reste utilisable à la
  main (déboguer une source sans payer de scoring) — il n'est plus branché à aucun
  ordonnanceur depuis le passage au tout-local.
  Tout point d'entrée planifié appelle `attendre_base()` en premier.

  **Rangement de `scripts/` (2026-10-05) : les POINTS D'ENTRÉE restent à la
  racine, le reste descend par métier.** `scripts/install_agents.py` écrit
  `scripts.<nom>` dans les plists, donc déplacer un module que launchd lance
  casse l'agent EN SILENCE — restent donc à `scripts/<nom>.py` :
  `run_pipeline`, `run_publication`, `run_veille`, `assurer_prefiltre` (les
  quatre que les plists nomment), plus `run_ingest`, `score_articles` et
  `install_agents` lui-même. Les autres vivent dans
  `scripts/reparations/` (plan puis `--apply`, à passer une fois),
  `scripts/diagnostic/` (lecture seule, aucun appel payé),
  `scripts/sources/` (administration des sources),
  `scripts/outillage/` (outils de la machine) et
  `scripts/livraison/` (digest et alertes, non planifiés).
- Tests sur SQLite in-memory (fixtures dans `tests/conftest.py`, override de
  `get_db`). **`tests/` est rangé en miroir de `app/`** : `api/`, `content/`,
  `delivery/`, `pipeline/`, `publish/`, `schedule/`, `sources/`, plus
  `dashboard/` (la page statique), `scripts/` (les points d'entrée) et
  `socle/` (base, modèles, migrations, supervision). `conftest.py` reste à la
  racine de `tests/` : ses fixtures s'appliquent à tout ce qui est en dessous.
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
6. ~~Scheduler~~ ✓ — refondu en deux ordonnanceurs cloisonnés (2026-08-09), puis
   ramené à **un seul, tout en local** (2026-08-15 : alertes Discord coupées, donc
   plus rien à faire tourner dans le cloud — voir « Scheduler » dans les
   Commandes). launchd plutôt que cron côté Mac : natif macOS, et il rattrape les
   jobs manqués à la sortie de veille. Les plists ne sont plus des fichiers
   versionnés mais sont **générés** par `scripts/install_agents.py` (`plistlib`),
   ce qui les rend testables et supprime les chemins absolus du dépôt. Sur un futur
   VPS Linux : équivalent crontab `0 * * * *` pour `run_pipeline`, et un service
   systemd pour le dashboard.
7. ~~Adapter X via Apify~~ ✓ — V1 complète.
   (`app/sources/x_apify.py`, actor `apidojo~tweet-scraper` ~0,40 $/1000 tweets avec
   **minimum 50 tweets facturés par requête** — d'où trois garde-fous de coût :
   `maxTotalChargeUsd` par run, `maxItems`, et un throttle de 6 h entre fetchs par
   source (état dans `sources.state` JSONB, migration 0003, avec le `since` qui
   évite de re-payer les tweets déjà vus). Réassigner `source.state` en entier,
   jamais de mutation in place, sinon SQLAlchemy ne persiste pas.)
