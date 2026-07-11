# 🎧 Music News Radar

Veille automatisée sur l'actu musique **électronique & pop** pour alimenter un média
(Instagram). Le pipeline agrège une dizaine de sources (presse, Reddit, X, festivals,
sorties), les déduplique, les score avec Claude selon une ligne éditoriale précise, et
livre les actus chaudes sur Discord. Un **dashboard web** permet de piloter le tout.

Le pipeline tourne **tout seul dans le cloud** (GitHub Actions, toutes les 2 h) et écrit
dans une base **Neon** (Postgres). Ton ordinateur ne sert qu'à consulter le dashboard —
les données sont à jour même quand il est éteint.

---

## Lancer l'interface (dashboard)

C'est l'usage quotidien. Deux commandes :

```bash
uv sync                              # la première fois seulement (installe les dépendances)
uv run uvicorn app.main:app          # démarre le serveur
```

Puis ouvre **http://localhost:8000** dans ton navigateur.

Le dashboard lit la base Neon en direct : à chaque ouverture (ou rafraîchissement de la
page), tu vois tout ce que le cloud a récolté, y compris pendant que ton Mac dormait.

### Ce que tu peux y faire

- **Vues préréglées** : 🔎 À traiter · 📰 Pertinents (≥60) · 🔥 Hot (≥80) ·
  🖼 Avec média · ✓ Vus · 📸 Postés
- **Filtres** : par catégorie, par source, tri par pertinence ou par date
- **Workflow éditorial** : les boutons **✓ Vu** / **📸 Posté** sur chaque carte — un
  article posté ne réapparaît plus dans « À traiter »
- Les **vignettes** des photos/vidéos et les résumés français générés par Claude

> Pour arrêter le serveur : `Ctrl+C` dans le terminal.

---

## Prérequis

- [uv](https://docs.astral.sh/uv/) (gestionnaire de paquets Python)
- Python 3.11+
- Un fichier `.env` (copié depuis `.env.example`) contenant au minimum `DATABASE_URL`
  (la chaîne de connexion Neon). Les autres clés (`ANTHROPIC_API_KEY`,
  `DISCORD_WEBHOOK_URL`, `X_BEARER_TOKEN`…) ne sont nécessaires que pour lancer le
  pipeline en local ; pour juste consulter le dashboard, `DATABASE_URL` suffit.

```bash
cp .env.example .env   # puis renseigner DATABASE_URL
```

---

## Lancer le pipeline à la main

En temps normal le cloud s'en charge. Mais tu peux tout déclencher localement :

```bash
# Tout le pipeline : ingestion des sources → scoring Claude → alertes Discord
uv run python -m scripts.run_pipeline

# Ou étape par étape :
uv run python -m scripts.run_ingest            # récupère les nouveaux articles
uv run python -m scripts.score_articles        # score ceux pas encore évalués (--limit N)
uv run python -m scripts.send_hot_alerts       # envoie les alertes Discord des hot
uv run python -m scripts.send_digest --dry-run # aperçu du digest dans /tmp/digest_preview.html
```

---

## Gérer les sources

```bash
# Ajouter une source
uv run python -m scripts.add_source --name "Nom" --type rss --url "https://..." --genre electronic

# Types disponibles : rss, reddit_rss, x
#   - rss        : n'importe quel flux RSS/Atom
#   - reddit_rss : un subreddit (url = https://www.reddit.com/r/xxx)
#   - x          : compte X (url = https://x.com/compte), recherche (url = "#hashtag OR ..."),
#                  UGC (url = "ugc:..."), ou radar maison (url = "radar:")
```

Pour activer/désactiver une source sans la supprimer, passe `active` à `false` en base
(via le dashboard Neon ou DataGrip) — l'ingestion l'ignore, les articles déjà récoltés
restent.

---

## Comment ça tourne en production

- **GitHub Actions** (`.github/workflows/`) exécute le pipeline toutes les 2 h et lance
  les tests à chaque push. Les secrets (`DATABASE_URL`, `ANTHROPIC_API_KEY`,
  `DISCORD_WEBHOOK_URL`, `X_BEARER_TOKEN`) vivent dans les *repository secrets* GitHub.
- **Neon** héberge la base Postgres (source de vérité unique).
- **Discord** reçoit les alertes des actus chaudes (hotness ≥ 80) en temps réel.

Consulter l'état des runs : onglet **Actions** du dépôt GitHub.
Consulter les données : le dashboard, ou directement la console Neon / DataGrip.

---

## Développement

```bash
uv run pytest                    # la suite de tests
uv run ruff check .              # le linter
uv run alembic upgrade head      # applique les migrations à la base
uv run alembic revision -m "..." # crée une migration
```

Pour l'architecture détaillée, la ligne éditoriale et les contraintes apprises sur le
terrain, voir **`CLAUDE.md`**.
