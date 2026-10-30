# Scheduler : un seul ordonnanceur, launchd, tout en local

**Quand lire ce fichier** : avant de toucher a `scripts/install_agents.py`, a la
cadence d'un agent, a un nom de log, ou avant de proposer de planifier quoi que
ce soit (dans le cloud ou ailleurs). Il contient le piege du RENOMMAGE d'un
label launchd (qui double l'ingestion), la rotation `newsyslog`, le conflit de
port du dashboard, et pourquoi GitHub Actions a ete coupe.

Le reste des commandes du projet est dans `CLAUDE.md`.

## Scheduler

- **Scheduler : UN SEUL ordonnanceur, launchd, TOUT EN LOCAL sur le Mac**
  (2026-08-15). Plus rien n'est planifié dans le cloud.

  *Pourquoi on a coupé GitHub Actions.* Le runner n'avait qu'une seule raison
  d'être : il ne dort jamais, donc il portait les alertes Discord **temps réel**
  (sur un portable fermé, launchd rattrape le job au réveil et l'alerte serait
  arrivée le soir, sans intérêt). L'utilisateur ne veut plus de ces alertes :
  la raison d'être disparaît, et avec elle le cloisonnement par script qui
  n'existait que pour éviter le doublon avec le cloud. `.github/workflows/
  pipeline.yml` est supprimé ; plus aucun secret de dépôt n'est nécessaire.
  `ci.yml` (tests à chaque push) a suivi le 2026-10-21, à la demande de
  l'utilisateur, pour ne plus recevoir aucune alerte GitHub : `ruff`, `pytest`
  et la parité des migrations (`alembic upgrade head` puis `alembic check` sur
  une base vide, puis `alembic downgrade base`) se lancent désormais à la main
  en local — plus rien ne les exécute à chaque push.

  *Ce qu'on y gagne.* Le pipeline tourne désormais depuis l'IP **résidentielle** :
  Mixmag ne renvoie plus 403 (Cloudflare) et Reddit plus 429 — deux blocages
  documentés depuis les runners Azure, et qui étaient précisément la raison
  d'être de l'agent d'ingestion local. Coût cloud nul, secrets seulement dans
  `.env`.

  *Ce qu'on y perd, assumé.* Le Mac dort. Un créneau manqué est rattrapé au
  réveil (launchd, contrairement à cron), donc rien n'est perdu — mais l'actu
  peut arriver avec plusieurs heures de retard. Acceptable puisque la
  restitution est le dashboard, consulté quand l'ordinateur est allumé.

  *Les agents*, gérés par `uv run python -m scripts.install_agents`
  (sans option : affiche le plan ; `--apply`, `--status`, `--uninstall`) :
  - `com.musicnewsradar.pipeline` — `run_pipeline` toutes les **heures** :
    ingestion + dédup + scoring + mentions + purge. Une heure et non 30 min
    parce que chaque passage score (appels Claude payés) et que plus rien
    n'exige du temps réel.
  - `com.musicnewsradar.dashboard` — uvicorn en permanence, pour que
    `localhost:8000` soit debout. `KeepAlive`, sans `--reload`, sur `127.0.0.1`.
  - `com.musicnewsradar.publication` — `run_publication` toutes les **10 minutes** :
    la file de publication Instagram. Sort immédiatement en mode `arret`, le défaut
    (`PUBLICATION_MODE` dans `.env`). Une exception inattendue est notifiée (au plus
    une fois par heure, état dans `publication_panne.json` à côté du jeton) et le
    script sort en code 1. Un passage peut **dépasser 10 minutes** (yt-dlp,
    ffmpeg, tunnel, attente du conteneur chez Meta) : launchd ne lance jamais
    une seconde instance tant que la première tourne (`StartInterval` ne
    chevauche pas), le passage suivant part simplement en retard. D'où la
    relecture du mode avant chaque réclamation (voir « Publication automatique »).
    Chaque sortie, mode `arret` compris, consigne sa fin dans
    `publication_passage.json` à côté du jeton : c'est ce que relit le voyant
    `agent_publication` (voir « Bandeau de supervision », `dashboard.md`).
    L'intervalle vit dans `settings.publication_intervalle_s`.
  - `com.musicnewsradar.veille` — `run_veille` toutes les **5 minutes** : la
    veille rapide, c'est-à-dire les seuls comptes de `VEILLE_COMPTES` (voir
    « Veille rapide »). Silencieux quand il n'y a rien ; une exception est
    notifiée au plus une fois par heure (`veille_panne.json` à côté du jeton) et
    le script sort en code 1.

  **Renommage de l'agent, et son piège.** L'agent s'appelait
  `com.musicnewsradar.ingest`. launchd identifie un agent par son LABEL, pas par
  son contenu : sans traitement, l'ancien serait resté chargé à côté du nouveau
  et aurait **doublé toute l'ingestion**. `com.musicnewsradar.ingest` est donc
  passé dans `LABELS_OBSOLETES`, que `--apply` décharge et supprime AVANT
  d'installer, que `--status` signale s'il survit, et que `--uninstall` retire
  aussi. Les noms de logs suivent (`pipeline.log`), donc la conf `newsyslog`
  aussi : `afficher_rotation()` compare désormais le CONTENU du fichier `/etc`
  et non sa seule existence, sinon une conf périmée passerait pour valide.

  **Conflit de port :** l'agent tient le 8000. Avant un `uvicorn --reload` manuel,
  décharger l'agent (`launchctl bootout gui/$(id -u)/com.musicnewsradar.dashboard`)
  ou `--uninstall`.

  **Rotation des logs :** launchd n'en fait aucune (43 Mo accumulés en cinq
  semaines avant ce socle). `--status` dit si la conf `newsyslog` est en place et
  affiche la commande `sudo` à passer une fois — `/etc/newsyslog.d` est hors de
  portée du script. Après le renommage `ingest.log` → `pipeline.log`, la conf
  posée précédemment est signalée « obsolète » et doit être re-posée. **Idem
  depuis l'ajout de `publication.log`** : une conf posée avant ne fait pas
  tourner ce log — `--status` la dit « obsolète », re-passer la commande `sudo`
  qu'il affiche. **Idem depuis l'ajout de `veille.log`** (2026-09-18) : la conf
  posée avant ne le fait pas tourner, et il s'écrit 288 fois par jour —
  `--status` redit « obsolète », re-passer la commande `sudo`.

  Pas d'envoi de mail : l'utilisateur n'en veut pas — le digest n'est PAS
  planifié (le code `app/delivery/{digest,email}.py` et `scripts/livraison/send_digest.py`
  restent utilisables à la main ou pour une future UI). Ne pas re-proposer SMTP.
