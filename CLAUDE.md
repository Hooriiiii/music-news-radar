# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

**KINDA est une structure d'accompagnement de jeunes artistes**, pas seulement un
média. Trois briques, dans cet ordre de construction :
1. des **plugins** accessibles aux jeunes créateurs (outils de production) ;
2. le **compte d'actu** (cette app) : grosses actus électro ET pop d'un côté,
   mise en avant d'artistes locaux/émergents de l'autre — sur le même compte ;
3. une fois 1 et 2 installés, un **label de lancement** d'artistes, avec interviews
   croisées sur le compte, création de covers, push playlist.

Conséquence architecturale majeure : le compte d'actu est à la fois l'organe de
DISTRIBUTION (il fait connaître plugins et label) et l'organe de DÉTECTION (le flux
scoré est le deal-flow du label). D'où l'axe `emergence` et les tables `artists` /
`artist_mentions` — voir « Deux axes de scoring » et « Mémoire A&R » plus bas.

Le label sera électro, mais **le compte ne se ferme PAS à la pop** : les grosses
actus pop et K-pop gardent leurs scores hauts (règle écrite explicitement dans le
prompt, sinon le modèle les déprioris tout seul vu le poids de l'électro ailleurs).

Positionnement du compte arrêté avec l'utilisateur (2026-07-22) :
compte Instagram **français NATIONAL** (toute la scène FR : Paris, Lyon, Marseille,
Bordeaux, Lille, Toulouse, Nantes, Rennes, Brest), cœur électro + festivals, avec la
**Bretagne en bonus de départage** — un angle récurrent, jamais le sujet principal.
Ne pas dériver vers un compte régional.

Concurrents directs identifiés (médias électro FR natifs-Instagram, centrés
événements) : Electro News (~166K abonnés IG, existe depuis 2009, organise ses
soirées, label EN records), Guettapen (~117K, app mobile, affiliation billetterie
Shotgun + codes promo festivals, plusieurs articles/jour), Hands Up Électro (~12K,
interviews + guestmix). Ce ne sont NI la presse magazine (Tsugi, 49K) NI les
agrégateurs US (Pop Crave). Monétisation prouvée du segment : affiliation
billetterie, codes promo, giveaways marques, puis événements/label en propre — pas
la pub display ni les bonus Instagram (largement arrêtés). Leurs flux RSS sont
ingérés comme sources de veille (ils sortent les annonces FR en premier) ; Kinda ne
les reposte pas, il remonte à la source.

Ligne éditoriale (prompt scoring `app/pipeline/scoring.py`) : cœur électro + grosses
actus pop, PLUS quatre axes transversaux valorisés — festivals à toutes les échelles
(local Bretagne, national FR, international), infos positives/feel-good, musique ×
écologie/environnement, grosses actus musicales générales. Sources FR pour la
couverture locale : Tsugi (RSS) + Google News ciblés (festivals Bretagne, festivals
électro FR, musique×climat) — la source écologie est la plus bruyante (rel_moy ~49).

Veille automatisée sur l'actu musique électronique ET pop. Objectif long terme de
l'utilisateur : créer un MÉDIA (Instagram d'abord) — posts d'actu (description +
image) et reels (vidéos live UGC des derniers shows, actus, interviews). L'outil
doit à terme alimenter la création de contenu, pas seulement la veille.
Agent-Reach (github.com/Panniantong/Agent-Reach) est installé en LOCAL comme banc
d'essai : sonder hashtags/comptes/réseaux gratuitement avant d'engager des crédits
API X — jamais dans le pipeline planifié (fragile, sessions connectées, ToS).
Pipeline : sources → dédup (hash url_norm + titre) → scoring Claude → Postgres →
dashboard local. Le tout tourne EN LOCAL sur le Mac (voir « Scheduler »). Seuil
pertinence >= 60 pour le digest. Les alertes Discord (hotness >= 80) et le digest
mail existent toujours dans le code mais ne sont plus branchés : `DISCORD_WEBHOOK_URL`
est commentée dans `.env`, et le pipeline saute l'étape quand elle est absente.

## Commandes

- Installer / synchroniser les deps : `uv sync` (groupe dev inclus par défaut)
- Dashboard + API : `uv run uvicorn app.main:app --reload` → http://localhost:8000
  (page servie depuis `app/static/dashboard.html`, lit Neon en direct ; boutons
  vu/posté = workflow éditorial via PATCH /articles/{id})
- Ajouter une source : `uv run python -m scripts.add_source --name "..." --type rss|reddit_rss|instagram --url ... [--genre ...] [--categorie artiste|media|festival|label|club|autre]`
  (Instagram : URL au format `ig:<compte>`, ex. `ig:tsugimag` — compte professionnel
  business/creator uniquement, `business_discovery` ne voit pas les profils perso)
- Socle de sources FR : `uv run python -m scripts.seed_fr_sources [--apply]`
  (sans `--apply` : affiche le plan ; idempotent par URL)
- Lancer une ingestion : `uv run python -m scripts.run_ingest`
- Scorer les articles : `uv run python -m scripts.score_articles [--limit N]`
  (nécessite `ANTHROPIC_API_KEY` dans `.env` ; modèle : `SCORING_MODEL`,
  défaut `claude-haiku-4-5` — choix validé par l'utilisateur pour le coût)
- Digest mail : `uv run python -m scripts.send_digest [--dry-run] [--date YYYY-MM-DD]`
  (`--dry-run` écrit l'aperçu dans /tmp/digest_preview.html sans toucher la base)
- Alertes Discord : `uv run python -m scripts.send_hot_alerts`
- Rendu des posts : nécessite Chromium Playwright en local
  (`uv run playwright install chromium`). Le pipeline cron ne génère AUCUN post —
  la génération est déclenchée à la main depuis le dashboard.
- Run complet (ingestion + scoring + alertes) : `uv run python -m scripts.run_pipeline`
- Passage de veille rapide (les seuls comptes de `VEILLE_COMPTES`) :
  `uv run python -m scripts.run_veille` — voir « Veille rapide »
- **Scheduler : UN SEUL ordonnanceur, launchd, TOUT EN LOCAL sur le Mac**
  (2026-08-15). Plus rien n'est planifié dans le cloud.

  *Pourquoi on a coupé GitHub Actions.* Le runner n'avait qu'une seule raison
  d'être : il ne dort jamais, donc il portait les alertes Discord **temps réel**
  (sur un portable fermé, launchd rattrape le job au réveil et l'alerte serait
  arrivée le soir, sans intérêt). L'utilisateur ne veut plus de ces alertes :
  la raison d'être disparaît, et avec elle le cloisonnement par script qui
  n'existait que pour éviter le doublon avec le cloud. `.github/workflows/
  pipeline.yml` est supprimé ; plus aucun secret de dépôt n'est nécessaire.
  `ci.yml` (tests à chaque push) n'est PAS un ordonnanceur et reste tel quel.

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
  planifié (le code `app/delivery/{digest,email}.py` et `scripts/send_digest.py`
  restent utilisables à la main ou pour une future UI). Ne pas re-proposer SMTP.
- Nettoyage des URL de médias déjà en base (entités HTML) :
  `uv run python -m scripts.nettoyer_urls_medias [--apply]` (sans `--apply` :
  le plan ; idempotent — voir « URL de médias » dans les contraintes)
- Remplissage des durées de vidéos déjà en base :
  `uv run python -m scripts.remplir_durees_medias [--apply]` (sans `--apply` :
  le plan ; idempotent — voir « La durée d'une vidéo Instagram » dans les
  contraintes). Passé en production le 2026-09-21 : 655 vidéos datées dans 273
  articles et 12 posts, dont 25 au-delà de la limite du reel.
- Tests : `uv run pytest`
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
- Tests sur SQLite in-memory (fixtures dans `tests/conftest.py`, override de `get_db`).

## Philosophie de sourcing (source-level, pas aggregator)

Les gros comptes d'actu Insta/X (Pop Crave, Proximity, AllTime EDM, chart-trackers)
sont des REPOSTEURS en aval : ils repackagent les comptes d'artistes + la presse. Payer
pour les lire (API X), c'est payer un repost de ce que les sources primaires donnent
déjà, avec latence et doublons. La stratégie : taper AU NIVEAU DE LA SOURCE —
- primaire : comptes d'artistes/labels, organisateurs de festivals ;
- presse qui *break* l'actu : Mixmag, RA, Pitchfork, Tsugi, DJ Mag (électro),
  Music Business Worldwide + CMU + Billboard (business/pop) — flux RSS gratuits ;
- primaire aussi, depuis 2026-09-17 : comptes Instagram PROFESSIONNELS d'artistes,
  labels, festivals et médias via `business_discovery` (source `instagram`,
  gratuite) — la philosophie ne change pas : on lit le compte qui produit l'actu,
  jamais un reposteur ;
- social brut : X UGC/radar (éteint, voir plus bas), Reddit.
Corollaire coût : Billboard RSS (gratuit) rend les comptes X @billboard/@billboardcharts/
@chartdata redondants -> candidats à couper.

## Deux axes de scoring (relevance / emergence)

Le scoring rend DEUX notes indépendantes, qui ne se compensent jamais :
- `relevance` — intérêt pour le feed Instagram (pilier « grosses actus ») ;
- `emergence` — intérêt A&R : « cet artiste mérite-t-il d'être suivi par Kinda ? »
  (pilier « artistes locaux/émergents »).

Le cas qui justifie tout : un inconnu qui sort un premier EP est du bruit pour le
feed (relevance 20) et un signal fort pour le label (emergence 75). L'ancien prompt
notait « autopromo d'inconnu » sous 40 et **détruisait donc exactement le signal dont
le label a besoin** — ne jamais revenir à un axe unique. Priorité explicite aux
artistes français / à la scène locale sur l'axe emergence.
API : `GET /articles?sort=emergence&min_emergence=...`.

## Qui parle : `sources.categorie` (décision du 2026-09-17)

Le feed priorise les posts des COMPTES D'ARTISTES (électro, musiques dures — hard
techno, hardstyle, hardcore —, pop à tendance électro) : un artiste qui annonce sa
sortie est l'actu primaire. **Depuis le 2026-09-17, l'INFO d'un compte MÉDIA
(Guettapen, Tsugi, Mixmag, DJ Mag, Billboard, Booska-P…) n'est plus décotée** :
la décote de 15-20 points et le plafond à 59 ont enterré la BO de GTA VI (Fred
again..) à 45-59, jamais atteinte à cause d'un représentant de doublon mal choisi
(voir « Dédup à deux étages »). Kinda reprend l'information, jamais la
formulation ni les VISUELS du média (voir « Visuels : l'info des médias, les
images des artistes »). Festivals/labels/clubs : annonce officielle = source
primaire, promo courante 40-55. Un post SANS INFORMATION (légende vide — titre
« Post Instagram » —, emojis/hashtags seuls, selfie, « link in bio », teaser
d'idole sans fait) note sous 30 quel que soit le compte. L'axe emergence n'est
pas touché.

Mécanique : colonne `sources.categorie` (VARCHAR + CHECK, migration 0016, NULL =
inconnu) ; `build_user_prompt` passe « Format : » (post Instagram, article de
presse, post Reddit, tweet) et « Type de compte : » ; règles 5 et 6 du
`SYSTEM_PROMPT`. Éditable au dashboard (panneau Sources), via `PATCH /sources/{id}`
et `add_source --categorie`.

**Pré-filtre local** : même prompt, plus `CONSIGNE_PREFILTRE` — ne descendre sous
45 que pour le bruit évident, l'actu hors ligne et les posts réellement vides :
un contenu qui porte un fait vaut 45 ou plus, quel que soit le compte. **Deux cas
ne sont jamais écartés du passage à Claude**, même sous 45 en local : une actu
dont le groupe de doublons réunit au moins `cluster_sources_claude` (3) sources
distinctes (raison `cluster`), et un article qui cite un artiste suivi (raison
`artiste_suivi`) — `ContexteScoring` (`app/pipeline/scoring.py`), compteurs
`forces_cluster` / `forces_artiste` dans `ScoringStats`. Le post quasi vide
(garde-fou 1 ci-dessous) reste local dans tous les cas : aucune des deux
exceptions ne le fait passer à Claude.

Classement des sources existantes : `uv run python -m scripts.categoriser_sources
[--apply]` (sans option : le plan). Mapping explicite des comptes Instagram connus
(médias, festivals, labels, clubs — Keinemusik est un collectif, donc artiste),
tout autre compte Instagram = artiste, RSS/Reddit = média, X = NULL. Le mapping
explicite fait foi ; les défauts ne remplissent qu'une catégorie vide, pour ne
jamais écraser un classement manuel. Idempotent. Conséquence assumée : les flux RSS
de presse passent en `media` et notent donc plus bas qu'avant.

**Garde-fous déterministes (2026-09-17).** Le prompt ne suffit pas : mesuré le jour
même sur données réelles, Haiku applique les règles 5 et 6 de façon inégale — IVE
« [📸] » 65, « 🗂️ I-VIEW » 65, LE SSERAFIM « feat. » 72, et des Guettapen
ordinaires au-dessus du plafond (« LA SANTÉ MENTALE DE PETIT BISCUIT AU PLUS BAS »
68, « LE NÉERLANDAIS REVIENT SUR SON ÉVOLUTION » 70), alors que « LA LOI RIPOST MET
EN DANGER LA SCÈNE ÉLECTRONIQUE FRANÇAISE » (75) doit rester haut. Le code tranche
donc APRÈS le modèle, sur tous les chemins (Claude et pré-filtre local), dans
`scoring.appliquer_garde_fous(article, score)` (pure, rend une copie) :
1. **Post Instagram quasi vide** (`post_quasi_vide`) : moins de 6 mots
   significatifs dans la caption → relevance et hotness plafonnées à 29, emergence
   intacte. Un mot compte s'il contient une lettre ou un chiffre, après NFKC (les
   lettres « mathématiques » 𝚖𝚊𝚍𝚎 redeviennent ASCII) et retrait des URLs,
   hashtags et @mentions (`instagram.mots_significatifs`). La caption est relue
   dans `article.summary` sans le bandeau de métriques
   (`caption_depuis_summary`) — donc AVANT `apply_score`, qui écrase le summary.
   Sans caption connue, seul le titre « Post Instagram » fait foi.
   Coût : un post quasi vide n'est **jamais envoyé à Claude**. Note locale du
   pré-filtre si elle existe (plafonnée, son emergence est gardée), sinon note
   minimale (10/10/0, `other`, la caption en résumé) ; `scored_by = local`,
   compté dans `prefiltered` et `quasi_vides`. `actu_majeure` a été retiré du
   schéma le 2026-09-17 (jamais persisté, aucune migration) : plus de décote
   média à lever, donc plus de champ pour ça — `ArticleScore` l'ignore
   silencieusement si un ancien appel ou une réponse en cache le porte encore
   (Pydantic ignore les clés inconnues).

Nettoyage de la file sans rien payer : `uv run python -m scripts.appliquer_garde_fous
[--titres-courts] [--apply]`. Ne re-applique QUE le garde-fou 1. Une note déjà
posée a perdu sa caption : par défaut seul le titre « Post Instagram » est visé
(certain) ; `--titres-courts` vise aussi les titres de moins de 6 mots (probable
seulement — le titre n'est que la première phrase). Les articles USED ne sont
jamais touchés.

- Rattraper les actus récentes enterrées par l'ancienne notation (décote média,
  représentant de doublon mal élu) :
  `uv run python -m scripts.reevaluer_actus [--jours 3] [--limite 100] [--apply]`
  (sans `--apply` : le plan, aucune écriture ni appel Claude ; avec : élection
  des représentants sur la fenêtre, notation des articles mis en attente par
  cette élection, puis re-notation Claude — sans pré-filtre — du plan : articles
  notés en local dont le groupe atteint 3 sources ou qui citent un artiste
  suivi, et articles déjà notés par Claude en compte média entre 40 et 59 quand
  un artiste suivi est cité, la note dépasse 50, ou le groupe atteint 2 sources).
  Coût : l'élection garde le plafond d'un run ordinaire
  (`cluster_reevaluations_max_par_run`, 10) et chacune des deux phases payées est
  bornée par `--limite` — au pire `10 + 2 × --limite` appels, et le budget
  réellement dépensé est affiché par phase à la fin.
- Lier une fiche artiste à son compte suivi quand le nom ne suffit pas :
  `PATCH /artists/{id}` avec `{"source_instagram_id": <id de la source>}`
- Réparer un faux positif (ou confirmer un lien) de doublon d'archive :
  `uv run python -m scripts.marquer_doublon_manuel --article-a ID --article-b ID
  (--different|--meme-actu) --raison "..."` — voir « Dédup à deux étages » pour
  pourquoi effacer `duplicate_of_id`/`file_exclu_raison` seul ne suffit pas.

## Veille rapide (`app/pipeline/veille.py`, `scripts/run_veille.py`)

**Pourquoi.** Mesure du 2026-09-18 (`docs/superpowers/specs/2026-09-18-etre-une-
source-pas-un-intermediaire.md`) : Kinda ingérait la presse RSS en 48 min et les
comptes Instagram d'artistes en 141 min (médiane), parce que ces comptes n'étaient
lus que toutes les 3 h. Sur le cas Charlotte de Witte, il a donc **appris l'actu
par DJ Mag 39 heures avant de la voir chez l'artiste** — la définition mécanique
d'un intermédiaire. La propagation côté Meta est quasi immédiate : la latence
était **entièrement la nôtre**.

**L'arithmétique du quota, qui dicte tout.** Le quota Graph de l'app a rendu le
code 4 au-delà de ~430 appels dans l'heure (2026-09-17). Lire les 123 comptes
toutes les 5 min demanderait ~1 476 appels/h : impossible. D'où **deux cadences** :
- **12 comptes surveillés toutes les 5 min** = 144 appels/h, latence ~5 min ;
- **tous les autres toutes les heures** (`instagram_intervalle_heures` passé de 3
  à **1**) = ~111 appels/h, latence ~50 min au lieu de 141 min.

Soit **~255 appels/h en crête**, sous les ~430/h observés. (La note du 18/09
annonçait 181/h en gardant les autres comptes à 3 h ; on a préféré payer 74
appels/h de plus pour faire baisser aussi la latence des 111 autres. Revenir à
`INSTAGRAM_INTERVALLE_HEURES=3` redonne 181/h sans rien casser.)

**Les 12 comptes**, `VEILLE_COMPTES` dans `.env`, **modifiable** — des labels très
actifs et les festivals FR plutôt qu'un seul artiste : `stmpdrcrds`,
`defectedrecords`, `vladimircauchemar`, `fredagainagainagainagainagain`,
`etjusticepourtous`, `edbanger`, `astropolis`, `nuits_sonores`, `transmusicales`,
`positiveeducation`, `madeon`, `worakls.music`. **Chaque compte ajouté coûte
12 appels/h** sur un quota partagé avec la publication.

**Pas de migration** : la cadence est une liste de réglage, pas une colonne.
`InstagramAdapter.intervalle()` compare le username (`compte_depuis_url`) à
`comptes_surveilles()` ; `a_lire()` raisonne désormais en `timedelta` (marge d'un
quart d'heure au-delà de l'heure, un dixième de l'intervalle en dessous — les
cadences déjà mesurées sont inchangées).

**L'espacement est PARTAGÉ**, et c'est ce qui empêche le doublon : `dernier_appel`
est le même état pour les deux agents, donc un compte lu par la veille à T n'est
pas relu par le passage horaire à T+1 min. La veille **redistribue** les appels,
elle ne les double pas.

**Ce que la veille ne fait PAS** : ni illustration (bornée à 40 requêtes réseau
par run) ni purge — elles restent au passage horaire, conçues pour lui. Elle
ingère, `link_duplicates`, score **au plus `veille_scoring_max` (20)** articles
(jamais `score_pending` sans limite : l'arriéré peut se compter en centaines), et
enregistre les mentions. Rien de neuf = pas une requête de plus.

**Deux garde-fous, parce que le quota est partagé.** Un plafond dur
`veille_appels_max_par_heure` (200) compté dans `veille.json` à côté du jeton
(écriture atomique) ; et sur un code de quota Meta (4, 17, 32, 613) la veille
s'arrête **au premier**, se met en pause 30 min et notifie une seule fois.

**Cette pause est PARTAGÉE avec le passage horaire depuis le 2026-09-19**
(`app/pipeline/quota_graph.py`, fichier `quota_graph.json` à côté du jeton).
Ce document affirmait l'inverse — « le pipeline horaire n'est jamais suspendu :
sa cadence n'est pas en cause » — et la production l'a démenti : un appel refusé
pour quota **consomme quand même du quota**, donc le passage horaire qui
insistait sur 123 comptes entretenait tout seul le dépassement (voir la puce
« Le quota Graph de l'app est partagé » dans les contraintes). Le quota est
celui de l'APP, pas celui d'un agent : ce qu'un agent vient d'apprendre vaut
pour l'autre. Deux processus écrivent donc ce fichier — d'où un temporaire
nommé par processus, aucune écriture en LECTURE (une pause échue se reconnaît à
sa date, rien à effacer, donc rien qui puisse effacer la pause que l'autre vient
de poser), et la fin la plus tardive qui gagne quand deux pauses se croisent.

**La PUBLICATION, elle, n'est jamais suspendue par cette pause** : un post gelé
qui attend son heure part même si l'ingestion est à l'arrêt — rater un créneau
programmé coûte plus cher que de sauter une heure d'ingestion. La lecture de
visuels à la composition (`lecture_artistes.py`) garde sa propre pause. Un test
sentinelle (`tests/test_quota_graph.py`) échoue si un module de `app/publish` ou
`app/schedule` se met à lire cette pause.

## Mémoire A&R : tables `artists` / `artist_mentions`

Les articles sont purgés à 2 jours ; **les artistes et leurs citations, jamais**.
Ce qui intéresse un label n'est pas « qui est cité aujourd'hui » mais « depuis quand,
à quelle fréquence, est-ce que ça accélère » — cette courbe est l'actif de Kinda.
`app/pipeline/artists.py` : `record_mentions()` (idempotent, tourne après le scoring
dans `run_pipeline`) et `rising_artists()` (shortlist A&R). Les mentions sont
DÉNORMALISÉES (titre, url, scores) et le FK vers l'article est `ON DELETE SET NULL` :
la purge casse le lien, jamais l'historique. API : `GET /artists`,
`GET /artists/{id}/mentions`, `PATCH /artists/{id}` (suivi manuel `tracked`).

Positionnement vs le marché : inutile de concurrencer Chartmetric/Viberate/Soundcharts
sur les signaux plateformes (vélocité TikTok, ajouts playlist). Le signal propre de
Kinda est AMONT : de qui la presse électro FR, Reddit et l'UGC parlent avant que les
chiffres de streaming ne bougent.

## Génération de posts (`app/content/`)

Le compte d'actu ne s'arrête plus au flux scoré : un article peut devenir un
**post Instagram** prêt à publier. Ce n'est plus un carrousel de texte —
c'est une **cover générée** (fond + logo + accroche accrocheuse) suivie des
**médias réels de l'actu** (photos, vidéos) :

```
slide 1  = cover, kind: "cover" -> RENDUE par Chromium
slide 2+ = médias de l'actu, kind: "media" -> laissés passer tels quels
```

La cover est proposée en **4 variantes** (`Post.variants`, quatre formulations
de la même info, jamais quatre angles différents) sur le MÊME fond, et sur deux
formats — `template` sur le post : `kinda_post` (1080×1350, POST, toujours
disponible) et `kinda_reel` (1080×1920, REELS, disponible seulement quand
l'article a une vidéo exploitable). Chaque variante porte trois textes :
`headline` (gabarit post) et le couple `title`/`subtitle` (gabarit reel, deux
bandes empilées) — légende et hashtags sont communs aux quatre. `Post.variants`
est `{"variantes": [...], "retenue": int | None}` ; choisir une variante
(`PATCH /posts/{id}` avec `variante_retenue`) réécrit `slides[0]` avec ses
trois textes sans repayer Claude ni relancer Chromium.

Cinq unités indépendantes — `media.py` (le vocabulaire des médias d'un
article : `entree()`, `videos_exploitables()`, `raison_pas_de_reel()`),
`layout.py` (les valeurs de mise en page en Python pur : gabarits, zones,
typographie, machine d'ajustement d'échelle), `compose.py` (article → 4
variantes de cover + légende, via Claude), `render.py` (slides `kind: "cover"`
→ PNG, via Chromium headless ; `render_covers()` rend les 4 variantes sur un
fond commun avec un seul Chromium et une seule récupération d'image),
`export.py` (zip : covers rendues + médias `kind: "media"` téléchargés tels
quels), `api/posts.py` (file éditoriale : `POST /articles/{id}/posts`,
`POST /posts/{id}/covers`, `PATCH /posts/{id}` — `template`,
`variante_retenue`, `slides`, `status` —, `DELETE /posts/{id}`,
`POST /posts/{id}/render`, `GET /posts/{id}/export.zip`).

**Pourquoi les médias sont typés.** `articles.media` (`app/models.py`) est une
liste de dicts `{type, url, preview_url, duration_ms, width, height}` — pas la
simple `list[str]` d'avant. L'API X v2 rend `url` pour une photo mais une
**vignette** (`preview_image_url`) pour une vidéo, jamais un lien direct vers
le fichier ; sans stocker `type`, un tweet vidéo était indistinguable d'un
tweet photo, et le format REELS était donc INDÉCIDABLE — impossible de savoir,
au moment d'afficher le switch, si l'actu avait de quoi remplir un reel.
`media.py::raison_pas_de_reel()` est la fonction que le dashboard interroge
pour griser ce switch avec un motif lisible plutôt qu'un bouton muet.

`layout.py` porte TOUTES les valeurs de mise en page, `render.py` n'en déclare
aucune : Chromium MESURE (lignes rendues, largeur de chacune), Python DÉCIDE
(échelle à essayer, acceptation, abandon). Les feuilles de style des gabarits
(`templates/*/style.css`) n'ont donc plus de nombres en dur non plus — tout
passe par des variables CSS injectées par `variables_css()`, et un
test-sentinelle par gabarit (`tests/test_render.py`) échoue si une valeur
revient s'y nicher. Une accroche trop longue même à la taille de police
minimale n'est PLUS rendue coupée en silence : le rendu est marqué `overflow`
(retourné par `render_post`, persisté dans `posts.render_report` via
`POST /posts/{id}/render`) plutôt que publié tel quel.

**Le reel est une VIDÉO, assemblée par ffmpeg** (`app/content/video.py`). Le
gabarit `kinda_reel` produit une cover ; `assembler_reel()` en fait la première
frame d'un MP4 1080×1920 qui enchaîne sur la vidéo de l'actu. L'export d'un post
`kinda_reel` contient donc `01_reel.mp4` et NON la cover et la vidéo à recoller.

Trois pièges ffmpeg traités, et chacun casse l'assemblage : une image fixe n'a
pas de piste audio et une vidéo X en est souvent dépourvue, or le filtre `concat`
refuse d'assembler deux flux dont un seul en porte une (d'où un silence fabriqué
des deux côtés, et un sondage préalable de la source — un graphe qui référence
`[1:a]` sur une vidéo muette est INVALIDE, pas vide) ; on passe par le FILTRE
`concat` et non par le démultiplexeur, qui exige des flux strictement identiques ;
et les dimensions impaires font échouer `libx264`, d'où les arrondis au pair dans
les `scale` intermédiaires.

Le cadrage est un **fond flouté** : la vidéo entière au centre, sur elle-même
agrandie et floutée. Rien n'est perdu de l'image, contrairement au recadrage
centré — sur une vidéo de concert, ce qui compte est souvent hors du centre.

La cover ne dure qu'**une frame** : elle sert de vignette de couverture, pas de
carton d'introduction. `frames_cover` est paramétrable parce qu'Instagram
réencode à l'import et qu'une frame unique peut ne pas survivre. Pour les reels
**publiés automatiquement**, c'est désormais fait : le conteneur `REELS` reçoit
la cover explicitement via `cover_url` (voir « Reels automatiques »), la frame
n'est plus qu'un filet pour l'export manuel.

**Figma n'est jamais appelé à l'exécution.** Son API REST est en lecture seule sur
le contenu d'un fichier ; l'écriture passe par l'API Plugin, qui n'existe que dans
l'éditeur ouvert. Chaque gabarit est donc extrait UNE FOIS en HTML/CSS dans
`app/content/templates/<gabarit>/` — voir le `REFERENCE.md` de ce dossier pour la
procédure de ré-extraction après une refonte du design.

Le **surlignage rose** est porté par le texte lui-même : `**segment**` dans une
accroche devient un `<mark>` que le CSS reflow tout seul. Dans Figma ce sont des
rectangles à repositionner à la main — le rendu HTML est ici plus robuste que
l'original.

**La teinte suit l'actu : rose par défaut, VERTE pour l'écologie** (demande de
l'utilisateur, 2026-09-18). Une seule couleur gouverne le voile bas du post, le
`<mark>` des deux gabarits et la bande de titre du reel — d'où `Palette` dans
`layout.py`, qui porte un RGB décliné en opacités et non trois chaînes. Le vert
`#35C77A` est **calé sur la LUMINANCE du rose** `#FD8BC3` (0.430 contre 0.433,
soit 2.10:1 contre 2.09:1 face au texte crème `#FFF7FA`) : le contraste perçu du
design ne bouge pas d'une actu à l'autre, seule la couleur change. Un vert plus
sombre lirait mieux sous le texte, mais relever le contraste des DEUX teintes est
une décision de direction artistique, pas un effet de bord — un test-sentinelle
(`tests/test_layout.py`) échoue si quelqu'un déplace la couleur à l'œil.

Le signal est la catégorie de scoring **`ecology`**, ajoutée le même jour. Aucune
migration : `articles.category` est un `VARCHAR(100)` **sans contrainte CHECK**
(migration 0001), et l'API `/articles` filtre sur la chaîne libre — un test
vérifie que chaque valeur de `Category` a son option au dashboard, sinon une
catégorie produite par le scoring resterait invisible. La règle du prompt exige
que l'écologie soit LE SUJET (festival qui verdit son édition, prise de position
climat, tournée durable, menace climatique sur un événement) : une affiche verte,
une photo de nature ou un nom de label évoquant la nature ne suffisent pas. Elle
prime sur `event_festival` — l'édition qui passe au renouvelable est `ecology`,
son line-up reste `event_festival`.

Le nom de palette est **figé dans `posts.variants["palette"]`** à la composition
(`app/content/teinte.py`), pas relu depuis l'article : l'article est purgé à 14
jours et le post doit rester rendable sans lui, même motif que ses champs
dénormalisés. Le champ est donc aussi le point où le dashboard pourra forcer une
teinte sans re-scorer. Conséquence câblée : `PATCH /posts/{id}` écrit `variants`
en FUSION (`{**post.variants, ...}`) et non en bloc — l'écrire en bloc ferait
disparaître la palette au premier choix de variante.

**Le fond de la cover est une ILLUSTRATION, jamais une affiche**
(`app/content/illustration.py`, 2026-09-18). C'était la première image
exploitable ; or une affiche de line-up est exactement ce qu'un compte de
festival publie en tête de carrousel, et superposer l'accroche Kinda à un visuel
déjà couvert de texte donne une cover illisible et redondante. Une affiche ne se
reconnaît ni à son ratio (souvent 4:5, comme une photo de concert), ni à son URL,
ni à ses métadonnées : elle se reconnaît à ce qu'on y VOIT, d'où un modèle de
vision (`visuels_model`, défaut Haiku) qui rend un verdict strict par image
(`{affiche, sujet: scene|portrait|autre, note}`).

Trois règles de coût : on ne juge **que s'il y a un choix** (au moins deux
candidats — payer pour confirmer l'unique image disponible ne changerait rien) ;
au plus 3 images **réduites à 512 px** dans UN appel, le coût en jetons suivant
la SURFACE (une photo de presse en 2000×1333 coûte ~3 550 jetons, sa vignette
~230) ; et le verdict est **mémorisé par URL** dans `illustration_cache_path`,
donc une recomposition ne repaie rien. Total mesuré : ~1 400 jetons d'entrée et
un appel par composition, soit ~0,002 $.

Et une règle de robustesse qui prime : ce module **ne lève jamais**. Pas de clé
API, vision coupée (`ILLUSTRATION_VISION_ENABLED=false`), réseau mort, JSON hors
schéma, image illisible → il rend `None` et l'appelant garde la première image.
Un verdict manquant vaut « neutre », qui passe devant une affiche avérée et
derrière une photo reconnue : ne pas savoir vaut mieux que savoir que c'est une
affiche. Le juge n'est payé qu'à UN endroit, `create_post`, donc la cover reel
suit la même règle sans code en double.

**Le fond doit aussi être ASSEZ GRAND, pas seulement pas une affiche**
(2026-09-21). Relevé en ouvrant le compte : 4 des 6 derniers posts publiés
avaient un fond flou (post 71, visage baveux à l'œil nu) — l'`og:image`
retenue mesurait 564×345, agrandie 3,91× pour remplir 1080×1350. Rien ne
mesurait jamais la résolution avant de rendre. `illustration.eviter_l_affiche`
reçoit désormais `cible` (la taille du gabarit publié, fournie par
`create_post` depuis `template` — 1080×1350 en post, 1080×1920 en reel, un
reel étant plus exigeant sur la même photo) et traite un fond trop petit
EXACTEMENT comme une affiche avérée : il descend d'un barreau vers les mêmes
réserves — **web compris** —, et à défaut (depuis la Tâche 5, voir plus bas)
**le créneau saute** plutôt que de partir sur `FOND_KINDA`. Mais AVANT de
descendre, le premier barreau **balaye ses propres candidats par taille**
(`candidats`, tout le groupe de doublons) : `choisir_illustration` ne classe
que sur le verdict affiche/photo, et sans ce balayage la photo de presse 2 156 px
d'un doublon restait en slide pendant que la capture de 564 px du représentant
envoyait la cover vers les réserves (revue du 2026-09-21).

Seuil : `cover_fond_upscale_max` = **2,2×** (`app/config.py`), la MÊME valeur et
le même raisonnement que `publication_crop_upscale_max` (voir « Publication
automatique ») : laisser passer l'`og:image` standard de 1200×630 (2,14× pour
remplir 1080×1350), de loin l'illustration la plus fréquente du pipeline. Un
premier seuil à 1,5× la rejetait : la plupart des posts de presse seraient
partis sur l'aplat Kinda, et un article dont les slides étaient par ailleurs
écartées finissait en `PreparationImpossible`, donc exclu pour toujours. On ne
dégrade jamais le cas nominal pour attraper un cas limite. Assumé : le fond du
post 62 (720×900 en reel, 2,13×) passe, à un centième du seuil ; les trois
autres fonds flous mesurés (2,39×, 3,52×, 3,91×) sont attrapés. Une taille non
mesurable est GARDÉE — même règle du doute que le juge d'affiches. La mesure ne coûte rien de plus : les
dimensions réelles sont lues au moment où le juge visuel décode déjà l'image
pour la réduire à 512 px, et mémorisées dans le même `illustration_cache_path`
(`Cache.tailles`, à côté des verdicts).

Et l'`og:image` elle-même peut être en cause : certains CDN d'éditeurs
(WordPress) recopient le paramètre de redimensionnement de la page dans
`og:image` — mesuré sur le post 71, `?w=564` rendait 564×345 (50 Ko) là où la
même image sans ce paramètre pèse 386 Ko en 2156×1320. `app/pipeline/
illustrations.py::image_de_partage` retente donc, quand l'URL porte un
paramètre connu (`w`, `width`, `h`, `height`, `resize`, `fit`, `s`), la
variante sans ce paramètre — et ne la retient que si elle est RÉELLEMENT plus
grande une fois téléchargée et décodée (jamais déduit de l'URL, un éditeur
peut ignorer le paramètre). Mémorisé par URL dans
`illustration_variante_cache_path` : un article dont l'`og:image` ne change
pas ne repaie pas cette vérification à chaque passage.

**Et si tout est une affiche, on cherche ailleurs** (2026-09-18,
`illustration.eviter_l_affiche` +
`choix_visuels.{photos_du_compte,photos_des_artistes_cites}`). Post 40 puis 41
(Transmusicales) : cinq visuels, cinq affiches de line-up, et le compte ne
publiait que des programmes. La première version gardait « la moins mauvaise des
affiches » quand rien de mieux ne se trouvait, ce qui a donné trois
republications du même défaut (« ya toujours l'affiche en fond !! »). La règle
est donc **absolue** : une affiche avérée n'est jamais publiée en fond. À
l'époque (avant la Tâche 5, 2026-09-21), à défaut de photo, la cover partait sur
`FOND_KINDA` — l'aplat sombre du gabarit (`assets/fallback.jpg`), celui pour
lequel la typo a été dessinée, donc lisible. **Ce n'est plus le cas : à défaut
de photo, désormais, le créneau SAUTE** (voir le paragraphe suivant).

`FOND_KINDA` (`kinda:fond`, dans `app/content/visuels.py`) est une **sentinelle,
pas une URL** : un fond `None` veut dire « ce post n'a pas de visuel », et
`FOND_KINDA` veut dire « on a cherché partout, rien ne convient ». Le schéma
`kinda:` n'est ni http(s) ni `file://` : aucune requête, aucun fichier lu.

**Depuis la Tâche 5 (2026-09-21), `FOND_KINDA` n'est plus une issue
PUBLIABLE — seulement le signal interne que `eviter_l_affiche` a parcouru
toute l'échelle sans rien trouver.** Demande de l'utilisateur : « Dans l'ordre.
[…] Sinon skip le post/reels. » `compose.resoudre_fond` (appelé par
`create_post` ET, en amont, par `app/schedule/runner.py::_composer` AVANT
l'appel payé à Sonnet) lève `VisuelIntrouvable` quand `eviter_l_affiche` rend
`FOND_KINDA` : **aucun post n'est plus jamais créé avec cet aplat en fond.**
Dans le planificateur, cette exception SAUTE le créneau (`_liberer`, jamais
`_exclure`) — l'article n'est PAS `file_exclu_raison`, il reste éligible et
repartira dès qu'un barreau donnera mieux (nouveau post du compte source, ou
la recherche web qui ressort du cache après `visuels_web_cache_jours`) ; c'est
la purge à `retention_days` (2 j) qui conclut s'il ne trouve jamais rien,
jamais une exclusion posée à la main. Compté à part dans
`RapportPublication.sautes`, avec une raison lisible sur le créneau (« visuel
introuvable : … »). `resoudre_fond` calcule le choix EXACTEMENT une fois par
créneau (les réserves compte/artiste/web sont des lots paresseux qui lisent le
réseau dès qu'on les ouvre) : son résultat voyage jusqu'à `create_post` par le
paramètre `fond_precalcule`, pour ne jamais rouvrir une réserve deux fois. Un
brouillon composé AVANT cette tâche et qui porte encore `FOND_KINDA` en fond
est traité comme périmé (`runner._brouillon_perime`) : la composition
suivante refait tourner l'échelle au complet plutôt que de le geler tel quel.
Le gel (`app/publish/preparation.py::verifier_fond`) continue d'accepter
`FOND_KINDA` sur un tel brouillon ancien — filet de compatibilité, jamais un
chemin qu'une composition neuve peut encore emprunter.

**Tour de correction 1/5 (2026-09-21), trois défauts mesurés en exécutant le
planificateur sur 120 passages simulés.**

- **C-1 : un article sans bonne image faisait taire le compte TOUTE LA
  JOURNÉE.** Le saut libère le créneau et relance l'arbitrage, mais
  l'arbitrage classe par score et l'article reste éligible : s'il a le
  meilleur score, il revenait sur le créneau le plus proche, sautait encore,
  jusqu'à MANQUE, puis glissait au créneau suivant — mesuré : 3 créneaux du
  jour en MANQUE, zéro publication, pour un seul article de 95 de pertinence
  sans visuel face à quatre bons articles à 67-70. `app/schedule/pause_
  visuels.py::marquer_saute` met donc l'article sauté À L'ÉCART
  TEMPORAIREMENT (`visuel_saute_pause_minutes`, 180 = 3 h par défaut — la
  moitié du cache du lecteur de comptes, 6 h, pour retenter dès qu'une
  chance raisonnable existe sans laisser le compte silencieux une demi-
  journée ; ni une exclusion — la spec l'interdit, l'article peut avoir une
  photo demain — ni un délai trop long — la rétention est à 2 jours). Fichier
  d'état à côté du jeton (`visuel_saute.json`), même motif que
  `quota_graph.json` : écriture atomique, temporaire nommé par processus,
  purge des entrées échues à chaque écriture, aucune écriture en LECTURE.
  `store._articles_publiables` (le socle commun à `candidats_eligibles`,
  `appliquer_arbitrage` et `offre_reels`) exclut les articles en pause. Le
  point d'entrée est unique (`marquer_saute`) : la Task 6, qui déplacera une
  partie des sauts au GEL, l'appelle de la même façon.
- **I-1 : une panne de transport dans une RÉSERVE (compte, artiste, web) était
  confondue avec « rien trouvé ».** Avant la Tâche 5, les deux donnaient
  l'aplat Kinda — une issue sûre. Devenues un SAUT, cette confusion faisait
  sauter un article sur une simple coupure réseau, avec une raison fausse.
  Détail dans le paragraphe qui suit (« Une réserve EN PANNE… »).
- **Ceinture de mise en service** : un créneau déjà GELÉ avant le déploiement
  de cette tâche peut porter un post composé avec `FOND_KINDA` en fond — la
  publication (`runner._publier`) le détecte désormais et REFUSE de publier :
  le créneau se libère (`_liberer`, pas d'exclusion), l'article se met en
  pause comme un saut normal, dossier figé effacé. Sans cette ceinture, la
  fusion aurait exigé une opération manuelle sur les créneaux déjà gelés en
  base.
- **M-1** : un créneau réattribué à un NOUVEL article gardait l'ancienne
  `erreur` (« visuel introuvable : … ») jusqu'à ce qu'une composition
  réussisse — trompeur au dashboard. `appliquer_arbitrage` l'efface dès que
  `article_id` change.

**Tour de correction 2/5 (2026-09-22)**, la revue rejoue le scénario ci-dessus
avec les VRAIES fenêtres de `PUBLICATION_FENETRES` et 200+ passages : **4
publications sur 4** (contre 0 avant C-1), l'article fautif jamais exclu, qui
retente toutes les 3 h. Six points restaient.

1. **Le chemin REPORT rejouait EXACTEMENT le défaut C-1.** Une réserve qui
   lève `VisuelDiffere` à chaque passage (au lieu d'un « rien trouvé ») faisait
   `continue` sans jamais libérer le créneau ni mettre l'article à l'écart —
   mesuré : 1 publication, 3 MANQUE, 0 sauté. Un report reste le bon geste
   (retenter EST la réponse à une panne de transport), mais pas éternellement :
   `pause_visuels.signaler_report` compte les reports CONSÉCUTIFS par article
   et, au-delà de `visuel_differe_max_reports` (6 par défaut — calé sur la
   pause de quota Graph à la composition, 60 min, rapportée à la cadence de
   publication, 10 min : 6 passages laissent à une panne de quota le temps de
   se résorber toute seule avant d'abandonner), bascule dans la MÊME mise à
   l'écart qu'un saut. `reinitialiser_report` casse le compteur dès qu'une
   composition réussit. Visible via `RapportPublication.reportes`, distinct de
   `sautes`, dans le log de `run_publication`.
2. **Le bouton « Générer un post » du dashboard renvoyait un 500** :
   `resoudre_fond` peut lever la même `VisuelsDifferes` que `choisir_visuels`,
   et `app/api/posts.py` ne rattrapait que `VisuelIntrouvable`. Rattrapée,
   même 503 que pour `choisir_visuels`.
3. **`Fond` ne transporte plus une affiche avérée (ou une image trop petite)
   dans `url` quand `differe=True`** : `eviter_l_affiche` rend
   `Fond(None, origine, differe=True)`, jamais l'URL fautive — sûr par
   CONSTRUCTION, pas seulement parce que `compose.resoudre_fond` teste
   `differe` avant `origine == ORIGINE_KINDA`.
4. **Une horloge naïve ne fait plus tomber le planificateur.**
   `pause_visuels._pause_valide` compare `maintenant < jusqu_a` dans un
   `try/except TypeError` — un horodatage naïf sur le disque (corruption, bug
   amont) vaut désormais « pause périmée », jamais une exception qui ferait
   tomber `store._articles_publiables`, donc l'arbitrage, `candidats_
   eligibles` ET `offre_reels`.
5. **Écritures concurrentes : `pause_visuels._fusionner_et_ecrire` relit le
   disque À L'INSTANT DE L'ÉCRITURE et fusionne**, exactement comme
   `app.pipeline.cluster_arbitrage.fusionner_et_ecrire` (Tâche 7) — un second
   agent qui pose sa propre pause entre notre lecture et notre écriture ne se
   fait plus écraser.
6. **`runner._publier` met l'article en pause SEULEMENT si `_liberer` a
   réussi** (ordre inversé) : `_liberer` est une écriture conditionnelle qui
   peut échouer si le dashboard est passé entre-temps ; l'appeler avant de le
   savoir infligeait 3 h de pause pour un créneau qu'on n'a même pas réussi à
   libérer.

Deux **réserves**, ouvertes dans l'ordre et seulement tant qu'aucune n'a donné :
1. le compte qui a publié l'actu (jamais un `categorie=media`) ;
2. les artistes suivis **cités** par l'actu — un line-up cite des artistes qu'on
   suit, et le portrait de l'un d'eux vaut mieux qu'une grille de noms.

« Chercher plus loin » s'est traduit par trois réglages : `fond_secours_max_photos`
passé de 2 à **6** par réserve (deux posts d'un compte de festival en saison, ce
sont deux affiches de plus) ; une fenêtre propre au fond,
`fond_secours_fenetre_jours` = **30 j** au lieu des 10 j des visuels de carrousel
(un visuel de carrousel doit ILLUSTRER l'actu, un fond n'a qu'à être une belle
photo du bon compte) — **mais bornée par la purge** : ces réserves lisent la
table `articles`, que `retention_days` (**2** depuis le 2026-09-21) vide au-delà
de 2 jours. Tant que la rétention reste courte, les 30 j (et les 10 j des
visuels d'artistes) sont décoratifs et les réserves « compte source » et
« artiste cité » sont quasi vides — hors lecture en direct du compte source.
Décision de l'utilisateur : c'est la **recherche web**, seul barreau qui ne lit
pas la base, qui reprend le relais, pour une affiche comme pour un fond trop
petit (verrouillé de bout en bout dans `tests/test_visuels_web.py`). Les valeurs
sont gardées : elles redeviendront utiles si la rétention remonte ; et une lecture `business_discovery` en direct **même si le
pipeline vient d'ingérer le compte** (`LecteurComptes(..., ignorer_fraicheur=True)`).
Cette garde-là est une économie — « la base suffit » — et ici la base ne suffit
justement pas. Les **plafonds durs** du lecteur, eux, tiennent toujours :
2 lectures/passage, 24/jour, cache 6 h, pause sur le quota Graph.

Les photos sont COPIÉES en local (les URL signées du CDN expirent avant le gel) et
passent devant le **même juge, avec le même cache par URL**. Un candidat qu'on n'a
pas su juger n'est jamais retenu : contrairement au classement de
`choisir_illustration`, où l'inconnu passe devant une affiche avérée, ici
l'alternative est sûre (depuis la Tâche 5, un SAUT plutôt que l'aplat Kinda) —
on ne lui préfère qu'une image dont le juge a dit qu'elle n'est PAS une
affiche. Seul un juge muet **sur le fond lui-même** rend le comportement
d'avant (on ne sait rien, donc on ne touche à rien) — c'est aussi ce qui se
passe avec `ILLUSTRATION_VISION_ENABLED=false` : cette coupure est un RÉGLAGE
délibéré, jamais une panne, et ne doit jamais faire sauter ni reporter un
créneau à elle seule.

**Une réserve EN PANNE de transport (quota Graph, réseau, budget épuisé) n'est
plus confondue avec une réserve qui a réellement regardé et n'a rien trouvé**
(tour de correction 1/5, 2026-09-21 — I-1). Avant cette correction, `eviter_
l_affiche` retombait sur `FOND_KINDA` dans les deux cas, ce qui était sûr tant
que l'aplat restait publiable ; devenu un SAUT (Tâche 5), la confusion faisait
sauter un article sur une simple coupure réseau, avec une raison fausse
(« aucune image… ni sur le web » quand le web n'avait jamais répondu). Les
lots (`Lot.photos`) et `recherche_web.candidats_du_web` lèvent désormais
`app.content.visuels.VisuelDiffere` quand ils n'ont PAS PU regarder ;
`eviter_l_affiche` la capture par barreau (elle reste dans son invariant « ne
lève jamais » : c'est `Fond.differe`, pas une exception, qui en sort) et,
si aucun barreau qui a pu regarder n'a rien donné, rend `Fond(fond, origine,
differe=True)` au lieu de l'aplat. `compose.resoudre_fond` traduit ce
`differe` en `VisuelsDifferes` (un REPORT, comme une panne de lecture
Instagram) plutôt qu'en `VisuelIntrouvable` (un SAUT) — le créneau retente au
passage suivant, sans se mettre en pause. Si un barreau plus loin réussit
malgré la panne d'un barreau précédent, la panne n'a aucune conséquence : le
premier qui donne une vraie photo gagne, comme avant.

Coût : ce chemin **ne coûte rien** tant que le fond retenu n'est pas une affiche
avérée — ni lecture de compte, ni téléchargement, ni jeton. Les réserves voyagent
depuis `choisir_visuels` (où vivent la session et le budget du passage) jusqu'à
`create_post` sous forme de lots paresseux, dans `ChoixVisuels.secours`
(`illustration.Lot`) ; la réserve retenue alimente le FOND **et les slides** (voir
ci-dessous). Le carrousel garde par ailleurs les visuels de l'actu, affiche
comprise, qui y reste une slide honnête.

**Jamais un post qui ne MONTRE RIEN de l'actu** (titre corrigé le 2026-09-19 ; il
disait « jamais un post réduit à sa cover », et c'était la mauvaise cible).

*L'origine* (2026-09-18, article 17946 « Noisia Reunite After Seven-Year
Hiatus », Billboard, UNE image). Cette image a été jugée `affiche` : écartée du
fond (donc l'aplat Kinda), et absente des slides. Résultat publié : une cover
sans photo, et rien derrière — « la c'est n'importe quoi ! Il n'y a pas de slide
qui montre les artistes en question. » Trois règles en étaient sorties, chacune
posée là où l'information existe.

1. **Les photos de la réserve retenue deviennent des slides**
   (`compose.actifs_de_secours`, alimentée par `Fond.photos` — toutes les vraies
   photos du lot, la meilleure en tête, au plus `visuels_max_photos`). Elles sont
   déjà copiées en local et déjà jugées : les publier ne coûte plus ni requête ni
   jeton, et sans elles un article dont l'unique image est une affiche ne montre
   jamais l'artiste. Une affiche pêchée dans une réserve, elle, n'est ni fond ni
   slide : elle n'illustre pas l'actu. **Inchangée.**
2. ~~Une image seule sert à la fois de fond et de slide~~ — **RETIRÉE le
   2026-09-19**, post « Fred again.. joue sa mixtape à tout le monde au Palacio,
   trois heures après l'avoir finie » : slide 1 la cover avec sa photo recadrée,
   slide 2 **exactement la même image** en version nette. « Ça fait un peu bizarre
   d'avoir la même image en image normale juste après […] si c'est pour remettre
   la même image en deuxième slide, ça vaut pas le coup, parce qu'on la voit bien
   assez sur la première slide. » Le remède de Noisia dépassait sa cible : quand
   la cover porte déjà la photo, la répéter n'ajoute rien. **La photo qui sert de
   fond n'est donc JAMAIS republiée en slide, sans exception.** (Une vidéo dont
   c'est la *vignette* reste une slide : un clip n'est pas le même asset que son
   image fixe.)
3. **Un post d'un seul élément est acceptable quand sa cover porte une vraie
   photo** — « on peut se contenter juste de la cover normale et c'est tout ». La
   chaîne savait déjà le faire : sous deux éléments,
   `instagram.preparer_carrousel` crée un conteneur IMAGE simple au lieu d'un
   `CAROUSEL`. Rien de neuf à écrire, un seul fichier figé suffit.
   **Mais la leçon de Noisia tient** : si la cover est sur l'aplat Kinda
   (`FOND_KINDA`, aucune photo trouvée nulle part) ET qu'il n'y a aucune autre
   slide, le post ne montrerait rien — `PreparationImpossible`, exclusion et
   créneau suivant. C'est la distinction à garder en tête : ce qui est interdit,
   ce n'est pas « un seul élément », c'est « aucune image de l'actu ». Depuis la
   Tâche 5, ce chemin ne s'ouvre plus qu'sur un brouillon composé AVANT elle :
   une composition neuve ne pose plus jamais `FOND_KINDA`, elle SAUTE le créneau
   en amont (voir plus haut) — l'exclusion au gel reste le filet pour l'archive
   déjà en base, pas le cas normal.
   Inchangé : si les visuels existent et que seule leur récupération a échoué
   pour une raison passagère (`echec_transitoire`), c'est `PreparationDifferee`
   — on retente au passage suivant, parce qu'un article exclu ne revient jamais.
   Conséquence assumée : une panne de transport persistante fait un créneau
   `MANQUE` là où on aurait pu publier la cover seule ; le gel court 2 h avant
   l'heure, il a le temps de retenter.

Ordre de préférence du fond, mis à jour par la Tâche 5 (2026-09-21) : photo de
l'actu (non-affiche, assez grande) → photo du compte source → photo d'un
artiste cité → recherche web → **on saute le créneau** (plus de fond Kinda en
bout d'échelle, voir ci-dessus). Le plancher de résolution
(`cover_fond_upscale_max`) s'applique à CHAQUE barreau, pas seulement au
premier : une photo de réserve trop petite ne sauve pas plus le post qu'une
photo d'actu trop petite. Une affiche n'est refusée qu'en FOND ; en slide,
c'est le visuel de l'actu.

**Mais UNE SEULE affiche en slide, jamais sept** (2026-09-19, post du 18/09 13h10
sur les Trans Musicales : les slides 2 à 8 étaient la carte d'annonce entière du
festival — « TOUTE LA PROG RÉVÉLÉE », « 74 groupes », « 42 pays » — dans SA charte
graphique). Deux dégâts : notre post devenait leur carrousel avec une cover Kinda
posée devant, c'est-à-dire le rôle d'intermédiaire dont le projet veut sortir ; et
sept slides d'affilée ne ressemblaient plus à Kinda. Le plafond
(`illustration.plafonner_affiches`, `AFFICHES_MAX = 1`) est appliqué dans
`create_post` et donc PERSISTÉ dans `posts.assets` : le dashboard édite ce
brouillon, une règle rejouée au gel retirerait une slide remise à la main. On
garde la PREMIÈRE — sur un carrousel d'annonce la carte de tête porte le message
principal ; c'est une heuristique d'ORDRE, pas un jugement de contenu.

Corollaire de coût, et c'est ce qui a failli vider la règle de sa substance :
le juge ne notait que `illustration_max_candidats` images, soit 3, donc les sept
cartes des Trans seraient descendues à cinq et non à une. `prejuger(actifs)` juge
désormais d'un coup TOUT ce que le post peut montrer, en UN SEUL appel payé (le
lot s'élargit, il ne se dédouble pas), et `choisir_illustration` puis
`eviter_l_affiche` relisent ces verdicts gratuitement. La borne passe à **10**,
la taille d'un carrousel Meta : au-delà on paierait pour juger des images que le
post ne publiera jamais. ~0,002 $ par composition. La règle du doute est
intacte : une image non jugée (borne, décodage en échec, vision coupée) est
GARDÉE — une photo jetée par erreur appauvrit le post, une carte de trop ne fait
que l'alourdir.

**D'où vient le fond est enregistré** : `posts.variants["fond"]["origine"]`
(`article` | `compte` | `artiste` | `kinda`), figé à la composition au même titre
que la palette, et affiché sous « Image de fond » au dashboard. Une fois l'URL
écrite dans la slide, rien d'autre ne le dirait.

Les posts ne sont **jamais purgés** (archive éditoriale, même motif que
`artist_mentions`) et publier un post bascule son article en `USED`, ce qui le
protège aussi.

Le modèle de composition est `composer_model` (défaut `claude-sonnet-5`), plus fort
que celui du scoring : la composition tourne quelques fois par jour et sa qualité
se voit directement sur le feed.

**Emojis de légende : relancés, jamais bloquants** (2026-09-17). Le compte
d'emojis de `caption` n'est plus un validateur de `PostDraft` : `messages.parse()`
jetait sinon tout le brouillon, et trois légendes sans emoji faisaient perdre la
composition d'un créneau (articles 17930 et 18078). `probleme_emojis()` décrit
l'écart (nombre trouvé, attendu, « `caption` seulement »), le compositeur relance
avec ce message — toute erreur de validation est d'ailleurs renvoyée au modèle à
la relance —, puis accepte le brouillon avec un warning si rien ne fait mieux.
Une sortie structurée vide (`parsed_output` None : `max_tokens` ou refus) est
traitée comme un échec de validation : relance, puis `CompositionError`.
L'interdiction d'emoji dans `headline`/`title`/`subtitle` reste stricte (textes
rendus dans une police).

**Légendes : plus de média nommé, plus de clôture systématique en question**
(2026-09-21, retour de l'utilisateur en ouvrant le compte réellement publié :
« supprime les sources des descriptions [...] parle de l'actu en elle-même » —
posts 71, 63, 65 : « Info via Stereogum. », « ... selon Soompi ... » — puis,
sur les six derniers posts en ligne, une clôture identique à chaque fois,
« Qu'en pensez-vous ? »). Deux défauts, même mécanique de correction : une
règle de prompt (`SYSTEM_PROMPT`, règle 4) plus un filet, parce qu'un prompt ne
garantit jamais rien à lui seul. Le nom du média n'est PAS un défaut de forme
tolérable comme les emojis ou les accents — le retirer après coup est
nécessaire, `app/content/legende.sans_mentions_de_source` retire les phrases
qui nomment la source de l'article (nom de la source nettoyé de son canal,
domaine de l'URL avec et sans TLD — construits depuis l'article, jamais
devinés), appliqué à la composition — dans la boucle du compositeur, AVANT
le contrôle de forme, et encore dans `create_post` (idempotent) — pour que la
légende persistée soit déjà celle qui part en ligne ; une légende qui
deviendrait vide part telle quelle. Avant le contrôle et pas après : retirer
une dernière phrase « Info via Tsugi. » pouvait sinon laisser en fin de légende
la question que le contrôle de clôture venait d'écarter.

**Le filet ne bannit le NOM de la source que si c'est un MÉDIA** (catégorie
`media`, ou `None` : dans le doute on protège du plagiat). Ne pas simplifier
cette condition : pour un compte d'artiste, de festival, de label ou de club,
`source.name` est le SUJET de l'actu, pas la source de l'info (« Qui parle :
`sources.categorie` »). Mesuré en revue le 2026-09-21, source « Astropolis
(IG) » : « Astropolis dévoile sa programmation. Trois jours à Brest fin août. »
perdait sa première phrase — celle qui porte l'actu —, c'est-à-dire la
mutilation des posts mêmes que le feed priorise. La racine sans TLD suit la
même règle (elle n'est bannissable que parce qu'elle se confond avec le nom de
la source) ; le domaine COMPLET (`astropolis.org`) nomme un site, jamais le
sujet, et reste banni dans tous les cas.

La clôture en question, elle, est un défaut de FORME :
`probleme_cloture_question()` suit exactement `probleme_emojis()` /
`probleme_accents()`, relance puis accepte avec un warning plutôt que de perdre
un créneau. **La dernière phrase ne se termine JAMAIS par une question**, et le
prompt dit la même chose que le contrôle (revue du 2026-09-21) : il proposait
encore « une question » parmi les clôtures possibles alors que le contrôle les
refuse toutes — sur un modèle qui en mettait six sur six, une relance quasi
systématique, jusqu'à trois appels `composer_model` par composition. Un
contrôle sans mémoire ne sait pas faire « de temps en temps », seulement
« jamais » : c'est la question qui est sortie des clôtures proposées, pas le
contrôle qui s'est assoupli. Une question reste permise au milieu de la
légende.

## Publication automatique (`app/publish/`, `app/schedule/`)

Kinda publie seul **trois carrousels et trois reels par jour** (2026-09-18 ; deux
et deux auparavant), choisis dans le flux scoré. Design : `docs/superpowers/specs/2026-09-11-publication-meta-design.md`
(révision du 2026-09-16) pour la file, et
`docs/superpowers/specs/2026-09-17-reels-automatiques-design.md` pour les reels.

**Connexion Instagram, sans Facebook.** App Meta « Kinda Radar », compte
`kindanewsfrance` déclaré testeur : une app non revue publie sur ses comptes
testeurs, ce qui suffit. Jeton `IG…` de 60 jours, rafraîchi chaque semaine dans
`meta_token_path` (droits 600) ; `.env` n'est que la valeur initiale. Alerte à J-7.

**Deux jetons Meta, jamais interchangeables.**
- `META_ACCESS_TOKEN` (`IGAA…`, connexion Instagram, `graph.instagram.com`) :
  **publication**. Rafraîchi automatiquement (ci-dessus).
- `META_FB_ACCESS_TOKEN` (`EAA…`, jeton utilisateur de connexion **Facebook**,
  `graph.facebook.com`) : **source Instagram** (`business_discovery`, que le jeton
  de connexion Instagram ne sait pas appeler). Portées : `instagram_basic`,
  `instagram_manage_insights`, `pages_read_engagement`, `pages_show_list`,
  `business_management`. **60 jours une fois étendu, AUCUN rafraîchissement
  automatique** (il faudrait le secret de l'app) : le renouveler à la main —
  developers.facebook.com/tools/explorer → Generate Access Token → ⓘ → outil de
  débogage → « Étendre le jeton d'accès » → remplacer `META_FB_ACCESS_TOKEN` dans
  `.env`. Étendu le 2026-09-17 : `expires_at` 2026-11-16,
  `data_access_expires_at` 2026-12-16.
  **Alerte d'échéance** (`app/sources/instagram_jeton.py`) : à chaque run horaire,
  `run_pipeline` interroge `debug_token` et notifie (macOS) à **J-7** ou dès
  l'expiration, **au plus une fois par jour** (date dans
  `sourcing_token_alert.json` à côté de `meta_token_path`). Sautée sans jeton ;
  une panne réseau ne donne qu'un warning (type d'exception seulement, jamais le
  texte, qui porterait l'URL donc le jeton). Expiré malgré tout, Meta répond
  code 190 : chaque source Instagram passe en erreur avec un message qui dit quoi
  faire, et la notification de fin de run le signale aussi.
`META_IG_USER_ID` et `META_API_VERSION` sont communs aux deux usages.

**La file.** 9 créneaux (3 jours × 3), heure tirée dans les fenêtres de
`PUBLICATION_FENETRES` (Paris, défaut `08:30-10:30,14:30-16:30,19:00-20:15`) avec
une graine dérivée de la date — sinon l'heure glisserait à chaque passage. Les
fenêtres carrousel sont une **liste séparée par des virgules**, exactement comme
celles des reels : une fenêtre = un post par jour, donc changer la cadence est
une ligne de `.env`, jamais une ligne de code. L'ancien couple
`PUBLICATION_FENETRE_MATIN` / `…_APRES_MIDI` survit en **repli** : il n'est lu
que si `PUBLICATION_FENETRES` est VIDE (`slots.fenetres()`), pour qu'un `.env`
d'avant ne soit pas ignoré en silence — mais la bonne migration est de le
réécrire en liste. Le coût dicte le calendrier : composition Claude à J-1, gel (rendu et
téléchargements) à T-2h, publication à l'heure. **Au-delà de 90 minutes de retard,
on ne publie pas** : un Mac rouvert le soir lâcherait sinon la journée d'un coup.

**Trois modes** : `arret`, `apercu` (tout, y compris le conteneur chez Meta, sauf
`media_publish`), `reel`. Publier est public et irréversible : passer à `reel` est
une décision de l'utilisateur. En `apercu`, un article passé en aperçu devient
**inéligible** (comme un article publié : son créneau APERCU le retire de
`candidats_eligibles`), sinon le même article reviendrait à chaque créneau — la
file avance donc comme en `reel`.

**Le mode se choisit dans le dashboard**, pas dans `.env`. `app/publish/mode.py` :
`mode_effectif()` lit `publication.json` à côté du jeton Instagram
(`settings.meta_token_path.parent`, chemin configurable via
`publication_mode_path`) et retombe sur `PUBLICATION_MODE` (`.env`) si le fichier
est absent, illisible ou invalide — jamais d'exception, l'agent planifié ne doit
pas planter faute de fichier. `enregistrer_mode()` écrit atomiquement (fichier
temporaire + `os.replace`). API : `GET /publication/mode` (`{mode, source}`,
`source` valant `dashboard` ou `env`) et `PUT /publication/mode` (`{mode,
confirmation}`) — passer à `reel` exige `confirmation: "PUBLIER"`, sinon 409 :
une publication publique et irréversible ne doit pas basculer sur un simple clic.
Le sélecteur segmenté vit dans le panneau Programmation du dashboard, à côté de
« Recalculer la file ».

**Règles Meta vérifiées dans la documentation (2026-09-16)** : JPEG uniquement,
8 Mo, 320 à 1440 px de large, proportions 4:5 à 1,91:1, carrousel de 10, légende de
2200 caractères et 30 hashtags. **La cover `kinda_post` est rendue NATIVEMENT en
1080×1350** (4:5) : elle traverse `app/publish/images.py` intacte. Jusqu'au
2026-09-18 elle était dessinée en 1080×1440 (3:4) puis recadrée au centre, ce qui
lui prenait 45 px en haut et en bas — la moitié de la marge du logo (87 → 42) et
60 % de celle de l'accroche (76 → 31). Les marges publiées n'étaient donc plus
celles du design, et cela se voyait. Les valeurs du dessin n'ont pas bougé (77
latéral, 87 logo, 76 accroche, voiles 817/727) : elles se comptent depuis un bord
ou depuis le bloc de texte, et seuls 90 px de PHOTO disparaissent du milieu du
cadre.

**Toutes les images du carrousel sortent en 1080×1350, cover comprise**
(2026-09-18, « c'est mieux que le format corresponde, sinon les images rendent
bizarre »). Instagram recadre chaque élément au ratio du PREMIER : une photo de
presse 16:9 laissée en paysage — ce que faisait l'ancienne `normaliser`, qui ne
recadrait que HORS des bornes Meta (4:5 … 1,91:1) — revenait donc coupée sans
qu'on maîtrise ce qui partait. `app/publish/images.py::cadrer_carrousel` met
chaque image à l'échelle pour REMPLIR le cadre puis recadre ; le
recadrage se fait avant le redimensionnement (même résultat, sans travailler sur
des pixels destinés à partir). Même convention que les vidéos, déjà sorties en
1080×1350 par `video.py::transcoder_carrousel`.

**Le recadrage VERTICAL est ancré en HAUT, jamais au centre** (2026-09-19, après
le post du 18/09 17h33 où Travis Scott a été publié sans sa tête). Une photo plus
haute que le 4:5 perd de la hauteur ; la retirer pour moitié en haut et pour
moitié en bas décapite, parce que sur une photo de scène le sujet est presque
toujours dans le tiers haut et que la marge perdable (sol, pieds, scène) est en
bas. Un portrait 2:3 ne perd ainsi que ~17 % de hauteur, tous pris au bas. Le
recadrage HORIZONTAL, lui, reste CENTRÉ : couper autant à gauche qu'à droite est
le bon geste, et cette branche ne touche pas à la hauteur.

**Une image que ce cadrage abîmerait est ÉCARTÉE, pas publiée** (`ImageEcartee`,
sous-classe d'`ImageInexploitable` pour que le gel la saute comme il saute déjà
un média illisible) : jamais une erreur, jamais une exclusion d'article, la
raison est journalisée et rappelée dans le message si le carrousel finit vide.
Deux seuils, réglables (`publication_crop_upscale_max`,
`publication_crop_perte_max`) :
- **trop petite** — remplir 1080×1350 demanderait un agrandissement supérieur à
  **2,2×**. Ce plafond est choisi pour laisser passer l'`og:image` standard de
  1200×630 (2,14×), de loin l'illustration la plus fréquente du pipeline : un
  seuil sévère (1,3×) écarterait toute photo paysage sous ~1750 px de large,
  donc viderait la plupart des carrousels et finirait par exclure les articles
  faute de second visuel. Au-delà de 2,2× on est sur des vignettes et des avatars
  (300-400 px), où l'agrandissement Lanczos se voit ;
- **trop éloignée du 4:5** — le recadrage jetterait plus de **60 %** de la
  surface. 60 % garde le 16:9 (55 % de perte) et le 1,91:1 (58 %), les deux
  formats de presse courants, et écarte les panoramiques (≥ 2,35:1) et les
  bandeaux verticaux (≤ 1:3,3), dont il ne resterait qu'une tranche sans sujet.

Conséquence assumée : si les images écartées ne laissent que la cover, la règle
« jamais un post qui ne montre rien de l'actu » s'applique telle quelle — le post
part en image simple quand la cover porte une photo, et l'article n'est sauté
(avec sa raison) que si elle est sur l'aplat Kinda ; gel différé si c'est un
échec de téléchargement passager.

**Le carrousel porte les médias de l'actu, vidéos comprises** (2026-09-17). Premier
post réel (Charlotte de Witte, article 17839) : 5 vidéos en base, et un carrousel
parti avec la cover seule, le gel ne gardant que les photos. `preparation.py` :
cover, puis jusqu'à 9 médias dans l'ordre (`medias_du_carrousel` : slides puis
article, sans doublon). Une photo → JPEG normalisé ; une **vidéo** → téléchargée
(`telecharger_video`), sondée (3 à 60 s — la doc Meta citée dans le dépôt ne donne
pas de maximum pour un élément de carrousel, 60 s = `DUREE_MAX_SLIDE_MS`),
transcodée en **1080×1350** (le ratio du premier élément s'impose à tout le
carrousel) par `video.py::transcoder_carrousel` — même cadrage flouté et même
encodage Meta (H.264/AAC, GOP fermé, sans edit list, faststart) que le reel,
piste audio garantie —, publiée en enfant `media_type=VIDEO` +
`is_carousel_item`. Une vidéo inutilisable (trop longue, téléchargement, sonde ou
transcodage en échec, ffmpeg absent, sortie hors specs) **retombe sur sa vignette**
(`preview_url`) en photo : un média ne fait jamais échouer le gel. Le client crée
tous les enfants, sonde chaque enfant vidéo jusqu'à FINISHED au rythme des reels
(15 s, 10 min max), puis le parent ; un enfant vidéo refusé (création, ERROR,
EXPIRED, délai) est **retiré**, et s'il reste moins de deux éléments la cover part
seule en image. **La photo servant de fond à la cover** (`slides[0].image_url`)
n'est pas répétée ensuite ; une vidéo dont c'est la vignette **reste** (un clip
n'est pas le même asset que son image fixe), sauf si elle retomberait sur cette
vignette. Une sortie transcodée hors specs (dimensions, codecs, poids) retombe
aussi sur la vignette. **Visuels d'un compte Instagram de catégorie `media` jamais repris**
(watermarks, `visuels_interdits`) : cover seule.

**Hébergement éphémère.** Meta télécharge les images en cURL. Au moment de publier
seulement, un mini-serveur local sert le dossier du post (sans listing) et un tunnel
rapide `cloudflared` l'expose ; tout est fermé après `media_publish`. Le dashboard
n'est jamais exposé. `cloudflared` est cherché hors du PATH de launchd, comme ffmpeg.

**Un tunnel rapide est parfois lent à être visible** (deux publications refusées en
14 h le 2026-09-18, alors qu'un essai isolé juste après répondait en 14,7 s).
`heberger` fait donc jusqu'à `publication_tunnel_essais` (3) essais : un essai = un
tunnel ENTIER, budget `DELAI_JOIGNABLE_S` (90 s) chacun — un quick tunnel reçoit un
nouveau sous-domaine à chaque lancement, attendre plus longtemps sur celui qui ne
répond pas n'a aucun sens. L'essai perdu est tué par GROUPE de processus
(`start_new_session`), le serveur local vit pour tout le `with`. Total borné à ~4,5 min.
Épuisés, ils lèvent `TunnelInjoignable` (sous-classe de `HebergementIndisponible`).

**Un tunnel injoignable n'exclut PAS l'article.** C'est une panne machine, la classe
que la règle d'exclusion épargne. Rien n'est parti chez Meta : le conteneur n'est créé
qu'À L'INTÉRIEUR du `with heberger(...)`, donc après que le tunnel répond — retenter
ne peut pas publier deux fois. Le runner lève alors le `file_exclu_raison` posé à la
réclamation, remet le créneau en GELE (fichiers figés toujours valides, dossier
conservé), note la raison, notifie UNE fois et laisse le passage suivant retenter. Le
créneau n'est pas repris par la seconde étape de publication du MÊME passage (rouvrir
un tunnel tout de suite coûterait des minutes pour rien). Passé les 90 minutes de
retard, il devient MANQUE comme n'importe quel créneau gelé. Vaut pour les carrousels
comme pour les reels, et pour le chemin « publier à l'heure exacte » (la réclamation
anticipée suit exactement le même traitement).

**Exclusion.** Un article dont la composition ou le gel échoue, ou que l'utilisateur
« saute », reçoit `articles.file_exclu_raison` et ne revient plus. C'est aussi le
cas dès qu'un créneau est **réclamé** pour publication (GELE → ECHEC committé
avant l'appel à Meta, raison « publication interrompue avant confirmation ») :
l'exclusion n'est levée que si Meta confirme ; un refus de Meta la remplace par
« echec de publication : … ». Un article ainsi exclu ne repart jamais, même après
un crash — mieux vaut un post manqué qu'un post publié deux fois. Une panne
systémique (crédits Anthropic, réseau, Chromium) n'exclut rien, pas plus qu'un
post qui ne porte plus l'article de son créneau (gel ou publication refusés,
erreur posée).

**Une VIDÉO inadaptée n'est pas une faute de l'ARTICLE** (2026-09-21,
`preparation_reel.ReelInadapte`). L'article 18951 (Billboard, « The Weeknd
Debuts New Song Sampling "Neon Genesis Evangelion" Theme ») a été exclu à vie
parce qu'une vidéo de 190 s dépassait la limite du reel — or cet article n'avait
qu'**une photo** : la vidéo était EMPRUNTÉE à un compte d'artiste suivi
(`choix_visuels` / `_porteur_video`). On brûlait une actu à cause d'un visuel
prêté. `verifier_source` lève donc une exception distincte, sous-classe de
`PreparationImpossible` pour que tout ce qui l'attrape déjà continue de la
traiter comme un refus de gel (même motif qu'`ImageEcartee` sous
`ImageInexploitable`) ; le gel la reconnaît et **libère le créneau sans
exclure**, comme pour une inéligibilité relue.

Mais libérer ne suffit pas : sans mémoire, la même vidéo serait rechoisie au
créneau suivant et **recomposée à chaque fois** (Claude payé) — c'est
l'exclusion qui tenait ce rôle. L'exception porte donc la durée MESURÉE par
ffprobe, sa vidéo et l'article qui la détient, et `runner._memoriser_duree`
l'écrit dans `articles.media` : `videos_exploitables` l'écarte alors des reels
pour toujours, sans fermer les carrousels à l'article. **Si la leçon ne peut pas
être écrite** — article introuvable, ou URL qui ne correspond à aucun média, le
cas d'un repli yt-dlp qui porte un permalink —, on retombe sur l'exclusion : la
protection d'avant, là où l'information manque.

**Le dashboard édite pendant un passage.** L'agent relit chaque créneau avant de
le modifier et n'écrit le résultat d'une composition, d'un gel ou d'une
réclamation que par un UPDATE conditionnel (état, article, post attendus) ; si le
dashboard est passé entre-temps, son choix prime et le post composé reste un
brouillon. Un article publié à la main (USED, post PUBLISHED) libère et
désépingle son créneau. Un post sur un créneau GELE refuse toute édition de
contenu (409). Les créneaux déjà gelés sont publiés AVANT composition et gel, et
chaque étape est isolée : une panne amont ne bloque jamais un post prêt. L'heure
est relue juste avant chaque publication (retard > 90 min → MANQUE, notifié), et
le dossier figé est supprimé une fois le créneau terminé.

**Publication à l'heure affichée** (2026-09-17). L'agent passe toutes les 600 s :
sans rien de plus, un post partait jusqu'à ~10 min après l'heure du dashboard.
Un créneau GELE dont `publier_a` tombe dans les `publication_avance_s` (660 s,
un intervalle + marge) à venir est traité dès ce passage, **en mode `reel`
seulement** : réclamation (GELE → ECHEC committé, inchangée), tunnel, conteneur
(carrousel ou REELS, jusqu'à FINISHED), puis **attente dans le tunnel ouvert**
jusqu'à `publier_a`, par pas de 30 s relus sur l'horloge murale (jamais plus que
l'avance au total ; conteneur prêt après l'heure : aucune attente). En mode
`reel`, **retard puis mode sont relus** juste avant `media_publish` — pour TOUT
créneau, pas seulement ceux attendus : les enfants vidéo d'un carrousel peuvent
retenir le conteneur des minutes. Retard > 90 min (Mac endormi pendant
l'attente) → pas de `media_publish`, MANQUE, exclusion levée, notifié, dossier
effacé. Relu `apercu` → état APERCU ; relu `arret` → le créneau **redevient
GELE** par écriture conditionnelle sur l'état réclamé (si le dashboard est passé
entre-temps, son choix prime ; sinon exclusion levée, dossier gardé : il partira
si l'on repasse en `reel` avant la limite de retard). En `apercu`, un créneau à venir
n'est pas réclamé d'avance : il attend son heure. `publie_le` = heure réelle de
publication. Crochets : `publier_fichiers` / `publier_reel` acceptent
`attendre_jusqu_a` et `encore_publier` (appelés dans le `with heberger(...)`),
sans effet quand ils sont absents ; `tourner(dormir=...)` injecte le sommeil.
**Compromis assumé :** un processus tué PENDANT l'attente laisse le créneau en
ECHEC « publication interrompue avant confirmation » — ce post est perdu, jamais
doublé. launchd ne tue pas un passage en cours en temps normal. Pendant ces
quelques minutes, le créneau est en ECHEC de réclamation
(`RAISON_RECLAMATION`) : tant que `publier_a` est à moins de 20 min dans le passé
(ou à venir), le dashboard l'affiche « Publication en cours » sans détail
d'erreur, et `PATCH /posts/{id}` refuse toute édition de contenu (409), comme
sur un créneau GELE. Au-delà (crash), il redevient un échec éditable.

Essai sans publier : `uv run python -m scripts.tester_publication --post <ID>`.

**Reels automatiques (2026-09-17).** `creneaux.format` (`carrousel` | `reel`,
migration 0017) ; fenêtres reel propres (`PUBLICATION_REEL_FENETRES`, défaut
`11:00-12:30,17:00-18:30,21:00-22:30`), `PUBLICATION_CRENEAUX_REEL=0` les
désactive. Les six fenêtres du défaut s'alternent sur la journée (carrousel le
matin, l'après-midi et en début de soirée ; reel à midi, en fin d'après-midi et
tard) et laissent au moins 30 minutes entre elles.
`publier_a` reste unique tous formats confondus : `slots.py` refuse des fenêtres
qui se chevauchent — en ne vérifiant que les formats ACTIFS (le carrousel seul ;
le reel, contre lui-même et les carrousels, seulement s'il a des créneaux). Des
fenêtres reel fautives sautent les reels à l'arbitrage, les carrousels sont
programmés quand même, puis l'erreur remonte (notifiée à chaque passage).

**Reels en opt-in explicite.** `reels_actifs` vaut **false** tant que la case
« Reels auto » n'a pas été cochée au dashboard (clé absente, fichier illisible
ou valeur invalide = coupés). Motif : la production tournait déjà en mode `reel`
au moment de la fusion ; un défaut à true aurait publié des reels sans décision.
Le mode est **relu avant chaque réclamation** (`tourner(lire_mode=...)`,
`mode_effectif` dans l'agent) : repasser en `apercu` pendant un long passage
empêche tout `media_publish`, `arret` ne réclame plus rien. Un reel gelé qui finit
en « Manqué » parce que les reels ont été coupés est notifié comme tel
(« reels coupés »), pas comme un Mac en veille. Arbitrage **reels d'abord**, un article n'occupe jamais deux
créneaux. Éligible en reel : vidéo avec URL directe (≤ 90 s ou durée inconnue),
compte artiste/festival/label/club (`PUBLICATION_REEL_CATEGORIES`), source
Instagram (crédit), durée connue d'au moins **3 s** (`PUBLICATION_REEL_DUREE_MIN_S` ;
une vidéo plus courte reste bonne pour un carrousel). L'éligibilité est revérifiée
au gel : un article devenu inapte (vidéo retirée, compte reclassé) libère son
créneau sans exclusion. Un brouillon `kinda_reel` antérieur au crédit est
complété (`credit_video`) à la reprise ou au gel, jamais exclu pour ça. Le gel (`app/publish/preparation_reel.py`) télécharge la
vidéo, l'assemble (`assembler_reel`, sans edit list, GOP fermé), sonde source et
sortie contre les specs Meta, et fige `<prefixe>_reel.mp4` puis
`<prefixe>_cover.jpg` ; la publication crée un conteneur `REELS` avec
`cover_url` explicite (sondage 15 s, 10 min max). Crédit `🎥 via @compte`
obligatoire (`posts.credit`), jamais coupé. **Plus de ligne « Source : <url> »** sous la légende
(2026-09-17, demande de l'utilisateur) — ni à la publication ni dans l'export zip
(`app/content/legende.py`) ; le crédit reste.
**Légende aérée** (2026-09-17) : le prompt demande 2 à 4 paragraphes courts (1-2
phrases) séparés par une ligne vide ; filet déterministe `aerer_legende()` dans
`texte_legende` (publication ET export) — une légende sans ligne vide voit ses
retours à la ligne simples doublés (sauf devant une ligne de liste — emoji ou
puce, « 📅 24.09 » —, qui garde son retour simple), ou, en bloc de plus de deux
phrases, est coupée par paires de phrases (les emojis de fin restent avec leur
phrase ; « ... » suivi d'une minuscule ne coupe pas ; pas de coupe après une
abréviation : initiales « J. », « U.S. », et feat, ft, Vol, Dr, St, Mr, Mme, No,
vs). Ordre publié : légende, crédit, puis les hashtags en dernier bloc. Longueur :
marge de **2150** points de code sur TOUT le texte (Meta : 2200, unité non
documentée) ; si crédit + hashtags débordent à eux seuls, des hashtags partent par
la fin (500 caractères gardés à la légende), puis la légende est coupée — le
crédit jamais. ffmpeg absent = panne systémique qui
ne suspend que les reels. `GET /schedule/offre-reels` mesure la matière.

**Trois défauts du texte publié, corrigés le 2026-09-19** après revue des posts
réellement en ligne — chacun corrigé là où l'information existe, le prompt ne
garantissant jamais rien à lui seul :
- **Des hashtags qui portent vraiment un dièse.** Le reel du 18/09 15h13 s'est
  terminé sur `iheartradiofestival bts lesserafim …` en texte brut : la règle 7
  demandait des hashtags « en minuscules » sans dire qu'ils portent un `#`, et
  rien ne rattrapait. `legende.py::normaliser_hashtags` (dièse en tête, alphanum
  et `_` seuls, dédup insensible à la casse, tag vide supprimé, avant le plafond
  de 30) est appelée par `texte_legende` — le SEUL goulot que traversent la
  publication automatique, l'export zip et `tester_publication`, et qui couvre
  donc aussi les hashtags tapés à la main via `PATCH /posts/{id}`.
- **Le français s'écrit avec ses accents.** Deux posts du 18/09 sur quatre ont
  une légende en ASCII (« un titre pense pour la bande son »). Règle de prompt,
  plus un contrôle qui RELANCE l'appel quand une légende d'une longueur
  significative n'a aucun caractère accentué — non bloquant, sur le modèle des
  emojis : on accepte avec un warning plutôt que de perdre la composition.
  Racine probable NON traitée : le `SYSTEM_PROMPT` est lui-même écrit sans
  accents et le modèle imite le registre de son prompt.
- **Le titre d'un reel nomme l'artiste de la VIDÉO.** Le même reel affichait
  « BTS » sur une vidéo de LE SSERAFIM (son propre crédit le disait). Le
  compositeur ne recevait que l'article, donc ignorait d'où venait la vidéo ;
  or `visuels = choisir(...)` est résolu AVANT l'appel payé dans
  `runner.py::_composer`. `compte_du_visuel` voyage désormais jusqu'au prompt
  (« Compte du visuel publié : @… »). Le crochet reste RÉTROCOMPATIBLE
  (`appeler_compositeur` n'ajoute le mot-clé que si la signature sait le
  recevoir) : un `TypeError` sur un crochet ancien passerait pour une panne
  systémique et coûterait un post.

**Le bloc de texte du reel est ancré PAR LE BAS** (`GABARIT_REEL.block_bottom`,
420 px du bas, soit un bas de bloc à y=1500 ; 2026-09-19). Deux contraintes le
fixent : l'interface d'Instagram couvre le bas du reel, et le crop 4:5 de la
grille s'arrête à y=1635 — la première est la plus stricte. L'ancien
`block_center` (fraction de hauteur) gardait le CENTRE fixe : dès que la machine
d'ajustement réduisait la police, le bloc raccourcissait en remontant vers le
milieu du cadre, et les reels ressortaient dans la grille comme des corps
étrangers au milieu des posts, dont le texte est en bas. Un ancrage par le bas
est stable par construction, quelle que soit la hauteur mesurée.

**yt-dlp pour les reels d'artistes (2026-09-17, autorisé par l'utilisateur).**
Meta omet `media_url` des reels à musique protégée ; la vidéo vient alors de
`app/publish/ytdlp.py`, **au gel seulement** : API d'abord, yt-dlp sur le
permalink sinon (et en repli d'un lien CDN expiré — seulement pour un permalink
de reel ou un post dont la vidéo est le seul média ; un carrousel `/p/` n'a pas
de repli, le lien expiré exclut). Dépendance uv épinglée
(`yt-dlp>=2026.8.19`, la version Homebrew 2025.12.08 échoue sur « login
required »), lancée par `sys.executable -m yt_dlp` avec `--ignore-config
--no-cookies --no-cookies-from-browser` — **jamais de cookies ni de connexion**.
Un téléchargement par passage ; après une panne (session exigée, 429, erreur
inconnue), notification avec la commande de mise à jour et **pause de 60
minutes** (`ytdlp_etat.json` à côté du jeton) ; pendant la pause, une raison sur
le créneau, sans notification. Vidéo supprimée/privée : article exclu. **Règle
des 3 échecs par lien** : une panne d'allure systémique n'exclut rien, mais après
3 pannes de suite sur le MÊME lien l'article est exclu (sinon un reel cassé
bloquerait tous les autres) ; les compteurs vivent dans `ytdlp_etat.json`
(clé `echecs`, par URL), remis à zéro au succès ou à l'exclusion. Coût assumé :
une panne globale de yt-dlp exclut un article toutes les ~3 h.
`scripts.tester_publication` supprime `essais/<id>/` en fin d'essai. Sources
en VP9 : `assembler_reel` réencode tout en H.264/AAC. Coupe-circuits : case
« Reels auto » du dashboard (`reels_actifs` dans `publication.json`,
`/publication/reels`) pour tous les reels, `PUBLICATION_REEL_YTDLP=false` pour
yt-dlp seul. **Mettre à jour yt-dlp** quand la notification le demande :
`uv lock --upgrade-package yt-dlp && uv sync`, puis committer `uv.lock`.
Risques acceptés : CGU Instagram, fragilité, détection de droits à la
republication, limitation de l'IP résidentielle — démarrer en `apercu`.

**Avant un `alembic downgrade 0016`** (retire `creneaux.format` et
`posts.credit`) : décocher « Reels auto », attendre que plus aucun créneau reel ne
soit « Gelé » (publié, aperçu ou manqué), puis descendre. Sinon un reel gelé
perdrait en cours de route son format et son crédit obligatoire.

## Illustration des articles (`app/pipeline/illustrations.py`)

57 % des articles arrivaient sans aucun média, et le déficit était concentré sur
les flux RSS — 19 % d'illustration, alors que ce sont eux qui portent la matière
éditoriale. La réponse n'est PAS une recherche web : chaque éditeur publie une
balise **`og:image`**, celle que Facebook, X et Instagram affichent quand on
partage le lien. Elle est à jour, gratuite, et illustre l'article EXACT — une
recherche ramènerait au mieux la même image, au pire celle d'un autre événement.

Mesuré en production : 35 illustrés, 1 échec sur 36 tentatives (97 %).

Deux règles de coût. On n'illustre qu'**après le scoring** et au-dessus du seuil
du digest : un article de bruit ne sera jamais posté, lui chercher une image est
une requête réseau pour rien. Et `illustration_max_par_run` (40) borne un run,
sinon le premier passage partirait sur des centaines de requêtes.

**Plus aucune source Google News** (2026-08-18). Ces liens ne mènent pas à
l'article mais à la page de consentement de Google — irrésoluble, y compris avec
un navigateur complet et le cookie de consentement, tous deux essayés. Deux
conséquences : pas d'`og:image` donc pas d'illustration, et un post citerait
Google au lieu de l'éditeur.

Les quatre sources concernées ont migré, par `scripts/migrer_sources_google.py` :
Mixmag, qui est un ÉDITEUR, retrouve son flux direct (`mixmag.net/rss.xml`) —
le tout-local le rend de nouveau accessible, Cloudflare ne renvoyant 403 que
depuis les runners Azure ; les trois autres sont des RECHERCHES thématiques et
passent sur **Bing News RSS**, qui porte l'URL réelle dans son paramètre `url=`.
`_url_reelle()` dans l'adapter RSS la décode. Résultat : 105 items, zéro lien de
redirecteur, et de la vraie presse française (Ouest-France, Le Dauphiné,
20 Minutes) là où Google ne rendait que des liens opaques.

Deux pièges rencontrés. Bing ne comprend PAS l'opérateur `OR` : « A OR B » y rend
zéro résultat, ce qui éteindrait la source en silence — les requêtes concernées
sont réécrites. Et une source Mixmag directe existait déjà, désactivée à l'époque
du 403 : migrer sans regarder aurait créé un doublon interrogeant le même flux
deux fois par run.

L'adapter RSS lit aussi les `<img>` du HTML de la description, en DERNIER recours
— un `<img>` peut être un logo, une pub ou un pixel de suivi (ceux d'au plus
32 px sont écartés), là où `media_content` est choisi par l'éditeur. Gain mesuré
sur 12 flux réels : +12 % d'articles illustrés sans aucune requête réseau.

## Visuels : l'info des médias, les images des artistes (2026-09-17)

Décision de l'utilisateur : l'INFO peut venir d'un compte d'actu, ses images et
vidéos JAMAIS (watermarks, plagiat visible d'un média concurrent).
`app/content/visuels.py::origine_interdite` : source `instagram` de catégorie
`media`, et elle seule — les photos de presse RSS (`og:image`, `media_content`)
restent autorisées, quelle que soit la catégorie de leur source. Tout ce qui lit
des médias pour publier passe par `visuels_autorises`/`urls_interdites` (fond de
cover, photos du gel, vidéo d'un reel, cases du dashboard), avec plusieurs
ceintures posées à chaque tâche où la règle aurait pu être contournée : un
brouillon antérieur dont le fond vient d'un média n'est pas repris, le gel
refuse un tel fond, et `choix_visuels._comptes_autorises` revérifie la source EN
BASE (pas seulement héritée de l'index) avant de citer un compte d'artiste.

**Pas de plafond ni de cap média, plus d'`actu_majeure`.** La ligne éditoriale
change de mécanisme : ce n'est plus la note qui protège Kinda du plagiat visuel
(voir « Qui parle : `sources.categorie` »), c'est le choix des visuels.

**Où chercher à la place** (`choix_visuels.py`, AVANT l'appel Claude de la
composition — le choix des visuels précède le coût payé) : d'abord les médias
propres de l'article s'ils sont autorisés — et, en CARROUSEL (2026-09-21,
`choix_visuels._propres`/`_membres_du_groupe`), ceux de TOUT son groupe de
doublons (les articles dont `duplicate_of_id` pointe vers le représentant,
jamais de remontée de chaîne : un saut suffit toujours). Mesuré le 2026-09-21 :
21 articles liés comme doublons, dont 13 portaient des médias jusque-là jetés
parce que seul le représentant élu (le plus INFORMATIF par le TITRE, pas par la
photo) était regardé — une photo de presse nette peut ainsi remplacer une
capture Instagram basse résolution du même événement postée par une autre
source du groupe. `origine_interdite` reste vérifiée PAR MEMBRE, sur sa source
lue en base, jamais héritée du représentant : un groupe mixte presse + compte
Instagram média contient les deux, seul le second reste interdit. À qualité
égale les médias du représentant passent en premier, et le crédit suit le
membre dont vient le visuel retenu. Le REEL n'est pas élargi (son éligibilité —
catégorie du compte, durée de la vidéo — se décide sur l'article seul). Le
juge visuel (`illustration.prejuger`) et le plafond d'une affiche par
carrousel s'appliquent au lot élargi sans appel supplémentaire : le lot
s'élargit, il ne se dédouble pas. Sinon les comptes Instagram des
artistes SUIVIS cités (`comptes_artistes.py` : nom de la source sans
parenthèses, handle, fiches `artists` liées par `source_instagram_id` qui
écrasent l'index — un nom d'un seul mot comme « Anyma » n'est jamais cherché en
texte libre, seulement via la liste `mentioned_artists` ou un `@handle`). (a)
Leurs posts déjà en base ; (b) pour un carrousel seulement, sans rien SUR LE
SUJET en base, une lecture en direct par `business_discovery`
(`lecture_artistes.py`) : `visuels_lectures_max_par_passage` (2) par lecteur —
un lecteur par passage de l'agent —, `visuels_lectures_max_par_jour` (24),
cache par handle `visuels_cache_lectures_heures` (6 h), pause
`visuels_pause_quota_minutes` (60) sur les codes Graph 4/17/32/613, horloge
AWARE UTC, rien inséré en base ; (c) pour un reel, jamais de lecture en direct
(son éligibilité se décide sans réseau, sur la base) : la première vidéo
exploitable d'un post d'artiste déjà en base.

Heuristique (`actifs_artistes.py`) : même artiste (nom et handle retirés du
sujet), fenêtre dure `visuels_artiste_fenetre_jours` (10 j — en pratique
bornée par la purge à `retention_days`, 2 j, puisque ces posts sont lus en
base) sur la date de l'article, sur le sujet d'abord (recouvrement des mots significatifs du titre),
hors sujet accepté seulement à `visuels_artiste_generique_jours` (5 j) au plus
de la date de l'article, tri par recouvrement puis écart de date. Carrousel :
au plus `visuels_max_photos` (3) PHOTOS — jamais de vignette de vidéo, qui
n'est pas copiable en fichier exploitable. Reel : première vidéo directe
(3-90 s) ou récupérable par yt-dlp sur son permalink, jamais copiée (le gel la
télécharge).

**Un reel a besoin de DEUX visuels, pas d'un** (2026-09-17, bug de production).
Sa cover est une image rendue par Chromium — c'est elle que Meta affiche en
couverture (`cover_url`) —, donc un porteur de vidéo NU (Meta omet `media_url`
*et* la vignette) ne suffit pas : `choisir_visuels` garde le porteur ET ajoute un
fond, dans l'ordre photo de l'actu → vignette de la vidéo → photo d'un artiste
suivi cité (même chemin, mêmes budgets et même copie en cache que le carrousel,
mais jamais de lecture en direct). Sans aucune image : `AucunVisuel` AVANT la
composition payée. Sans cette règle, le créneau de l'article 17974 (reel de
Charlotte de Witte) se composait puis échouait au gel à chaque passage
(« gel refusé : le post n'a pas de visuel publiable ») jusqu'à MANQUE. Trois
lectures uniques en découlent : `visuels.fond_de_cover` (seul lecteur de
`slides[0].image_url`, qui traite `""` et le `str(None)` de brouillons de
production comme un fond ABSENT — sinon le rendu retombe en silence sur
`fallback.jpg`, le bug du fond de secours), `preparation.verifier_fond` (partagé
par les deux gabarits) et `_brouillon_perime`, qui relâche donc aussi un
brouillon de reel sans fond — c'est ce qui fait recomposer les brouillons cassés
déjà en base, sans script de rattrapage. `create_post` lève plutôt que de
persister des actifs sans fond extractible.

Les images d'artistes retenues sont COPIÉES dans `visuels_cache_dir` (défaut
`~/Library/Application Support/Kinda/visuels`, nom = 32 hex du SHA-256 de l'URL)
— les URL du CDN Instagram sont signées et expirent en quelques jours, et la
composition précède le gel de ~22 h. Lues ensuite seulement via un `file://`
résolu DANS ce dossier (`lire_fichier_cache`) ; ménage à `visuels_cache_jours`
(7 j) par `purger_cache`, câblé dans `_menage` (`app/schedule/runner.py`), donc
appelé à chaque passage de l'agent de publication.

`posts.assets` (migration 0018, JSONB, réassigné en entier) garde les actifs
choisis — origine (`article` | `artiste`), compte, permalink, date de
publication, copie locale ; `NULL` = post antérieur à la fonctionnalité, lu
comme avant. `artists.source_instagram_id` (même migration, FK `ON DELETE SET
NULL`) lie une fiche artiste à son compte suivi quand le nom seul ne suffit
pas ; `lier_artistes_comptes` le fait automatiquement par nom, sans jamais
écraser un lien déjà posé. `posts.credit` garde les handles distincts des
actifs, séparés par une espace. Légende : « 📸 @artiste » (« 🎥 » pour un reel),
**sans « via »** — et depuis la demande de l'utilisateur le 2026-09-17 au soir
(après le premier post publié), **plus de ligne « Source : <média> · <url> »** :
le crédit du compte reste seul obligatoire, jamais coupé (`app/content/
legende.py`).

**Le dernier recours : une image du WEB** (2026-09-18, `app/content/
recherche_web.py`). Après un post publié sur l'aplat Kinda parce que chaque
candidat était une affiche de line-up : « ne saute pas l'actu, cherche une image
sur le web en dernier recours. » L'échelle a donc un barreau de plus. Depuis
la Tâche 5 (2026-09-21), ce n'est plus le dernier : médias de l'actu → compte
source → artistes suivis cités → **web** → **on saute le créneau** (plus de
fond Kinda en bout d'échelle, voir plus haut « Génération de posts »). Le web
ne s'ouvre jamais tant qu'un barreau au-dessus a donné.

Mécanique : l'outil **`web_search`** de l'API Anthropic (server tool, variante de
base `web_search_20250305` — `visuels_model` est Haiku 4.5) rend des PAGES, dont
on extrait l'`og:image` avec le code déjà éprouvé de l'illustration des articles
(`app/pipeline/illustrations.py`, 97 % de réussite). Deux pièges vérifiés sur
sonde réelle : les URL sont lues dans les blocs `web_search_tool_result` et
**jamais dans le texte du modèle**, qui a inventé deux liens absents des
résultats ; et `image_de_partage` est résolu à l'appel, pas en valeur par défaut,
sinon le module n'est pas testable sans réseau. Requête déterministe (c'est la
clé du cache) et en QUATRE reprises, parce qu'elle ne doit jamais renoncer :
artistes cités → nom propre en tête du titre (« Les Transmusicales », pas la
phrase entière, qui ne ramènerait que des reprises de l'article) → **nom de la
source** nettoyé de son suffixe de canal (« Transmusicales (IG) » →
« Transmusicales »), et seulement d'un compte qui NOMME son sujet (festival,
label, club, artiste — « Tsugi festival photo » ramènerait le logo de Tsugi) →
les noms propres du titre. Plus un indice de discipline : la `categorie` de la
source le donne quand le texte ne le dit pas.

**Le défaut qui a valu cette cascade** (mesuré en production, article 18260,
2026-09-18) : « Le voile se lève sur le programme des 48es Rencontres Trans
Musicales », source « Transmusicales (IG) », `mentioned_artists` VIDE. Les deux
premières reprises échouaient (titre commençant par un verbe, aucun artiste
cité), la requête valait None, et le dernier recours ne partait JAMAIS — sur le
cas même pour lequel il a été écrit. La requête produite est désormais
« Transmusicales festival photo ». Verrouillé par un test de bout en bout
(`tests/test_visuels_web.py`) qui prouve que la conclusion « rien ne convient »
(le saut depuis la Tâche 5, l'aplat Kinda avant elle) n'est atteinte qu'APRÈS
une recherche web restée bredouille.

**Pas de filtrage de licence**, décision explicite de l'utilisateur (« non mais
c'est un projet perso donc c'est ok »). **Le site n'est en revanche plus cité**
(revirement du 2026-09-21, en réponse à la question posée après le retour sur
les posts publiés : « retirer aussi le crédit web ») : un `compte` d'actif qui
n'est pas un `@handle` est un domaine du web, et `credit_des_actifs`
(`app/content/visuels.py`) ne l'émet plus dans le crédit de la légende — seul
un crédit de COMPTE (`@artiste`) y reste. `recherche_web.attribution_de` et
`credit_de` continuent d'exister et de reconstituer l'attribution depuis le
seul `file://` publié (le nom du fichier de cache EST le SHA-256 de l'URL
d'origine) : cette reconstitution reste vraie et sert encore le permalien
gardé sur l'actif, elle n'alimente simplement plus l'affichage. Un crédit de
domaine posé avant ce revirement (`djmag.com`, `ra.co`, sur un brouillon déjà
en base) ne s'affiche plus non plus : le filtre vaut aussi à la lecture
(`app/content/legende.credit_de_compte`), seul endroit que publication, export
et essai à la main traversent tous.

Coût : une recherche par requête, mémorisée `visuels_web_cache_jours` (3) dans
`visuels_web_cache_path` ; au plus `visuels_web_max_candidats` (3) pages ; image
copiée dans `visuels_cache_dir` comme les autres ; coupe-circuit
`VISUELS_WEB_ENABLED`. Banques d'images et réseaux sociaux sont écartés par
domaine (leur `og:image` est un logo, et la règle du 2026-09-17 interdit déjà les
visuels des comptes Instagram média). **Jamais pour la VIDÉO d'un reel** — un
reel sans vidéo n'existe pas ; le web peut en revanche fournir le FOND de sa
cover, qui est une image rendue par Chromium. Le juge visuel y est INDULGENT
(`illustration.meilleures_photos(garder_inconnues=True)`) : pour une réserve de
fond l'alternative est désormais un SAUT (avant la Tâche 5 : l'aplat Kinda, sûr
lui aussi), mais ici l'alternative est de sauter l'ARTICLE tout entier (pas de
visuel du tout, `AucunVisuel`) — une image non jugée (vision coupée, quota) vaut
mieux que rien. Une affiche avérée reste écartée. Le module ne lève JAMAIS pour
un défaut de JUGEMENT (image illisible, quota du juge) : toute panne de ce genre
rend une liste vide. Il lève en revanche délibérément `app.content.visuels.
VisuelDiffere` (tour de correction 1/5, 2026-09-21) quand la recherche elle-même
n'a PAS PU être tentée (réseau, quota) — pour que l'appelant sache qu'il doit
REPORTER (`VisuelsDifferes`) plutôt que conclure à l'absence d'image : sans
cette distinction, une simple coupure réseau faisait sauter un créneau avec la
raison fausse « aucune image… ni sur le web » alors que le web n'avait jamais
répondu.

Sans visuel autorisé, l'article reste au dashboard (pastille « 🚫 sans visuel »,
raison via `store.raison_sans_visuel`, sans appel réseau — un artiste suivi cité
suffit à dire « pas encore », le vrai verdict attend la composition) mais sort
de la file de publication. À la composition, deux issues distinctes :
`AucunVisuel` exclut l'article (« composition impossible : … », comme un échec
de composition classique) : l'article n'a aucun visuel autorisé — hors sujet,
hors fenêtre, ou aucun artiste suivi cité. Une panne de TRANSPORT ne donne
jamais ça : elle lève toujours `VisuelsDifferes`, qui pose seulement la raison
« composition differee : … » sur le créneau, sans exclure ni notifier, et attend
le passage suivant (budget/quota de lecture épuisé, ou copie en échec
transitoire — `echec_transitoire`) ; une panne persistante refait donc le même
report à chaque passage, jusqu'à ce que le retard fasse passer le créneau en
`MANQUE`. Un brouillon sans visuel publiable n'est jamais gelé : `assets` vides,
aucun fond extractible, actif devenu interdit, ou fond de cover **effacé à la
main au dashboard** — le créneau lâche le post (pas d'exclusion : l'article n'y
est pour rien) et la composition suivante en refait un. Et si le fond est un
`file://` dont la copie a disparu du cache, le gel DIFFÈRE
(`PreparationDifferee`) : jamais une publication sur l'image de secours du
gabarit.
`export.zip` filtre les visuels interdits avant de les inclure.

**Mise en service et retour arrière** (point 9 du design) : un créneau déjà
**gelé** part avec ses fichiers figés — les lister et les faire sauter avant la
fusion. Et un post composé SUR CETTE BRANCHE porte un fond `file://` qui pointe
dans `visuels_cache_dir` : ce chemin n'a aucun sens pour le code d'avant (et
`lire_fichier_cache` n'existe pas), donc avant un retour arrière il faut **vider
ou repointer** le fond de ces brouillons (`slides[0].image_url`), sinon leur
cover part sur l'image de secours du gabarit.

## Dédup à deux étages

1. **Par hash** (`pipeline/dedup.py`) : même url normalisée + même titre → le même
   lien vu deux fois n'entre qu'une fois. Attrape les re-fetchs, pas les reprises.
2. **Par titre, ENTRE sources** (`pipeline/cluster.py`) : la même actu couverte par
   Mixmag, DJ Mag et Tsugi produisait trois articles. On rapproche les titres
   normalisés (accents, ponctuation, traits d'union recollés, mots vides FR+EN) par
   **coefficient de recouvrement** des mots significatifs — pas Jaccard, qui
   punirait le titre traduit ou suffixé du nom du média. Seuil 0.65, fenêtre 48 h,
   minimum 2 mots partagés. Les liens se forment contre l'article vu le plus tôt ;
   puis **`elire_representants`** (`app/pipeline/election.py`, juste après, AVANT
   le scoring) promeut le membre le plus INFORMATIF — clé sur le titre et la
   source seulement (le résumé est réécrit par le scoring, il ferait osciller) :
   artistes suivis cités dans le titre, source primaire (artiste/festival/label/
   club), visuels autorisés, mots significatifs du titre, ancienneté, id. Un
   représentant engagé (`USED`, un `Post`, un `Creneau` non `LIBRE`) ne change
   jamais — il garde alors ses notes. Un représentant noté par le pré-filtre local
   dont le groupe atteint `cluster_sources_claude` (3) sources est remis à noter
   par Claude, sauf s'il est lui-même engagé. Borne :
   `cluster_reevaluations_max_par_run` (10) réélections + remises à noter par run.
   Jamais de chaîne (un saut suffit toujours). Idempotent.

   Conséquences câblées : `link_duplicates` tourne AVANT le scoring dans
   `run_pipeline` (un doublon n'est jamais scoré, donc jamais payé), et l'API
   `/articles` masque les doublons par défaut (`include_duplicates=true` pour les
   voir).

   **Une actu déjà publiée ne ressort presque jamais** (2026-09-21 ; titre
   ajusté le même jour après l'arbitrage Claude ci-dessous — la bande
   « différente » et une panne d'arbitrage laissent volontairement passer une
   reprise plutôt que de risquer d'enterrer une actu neuve). La fenêtre de 48 h
   ne comparait que des articles frais entre eux : une reprise plus tardive de
   la même actu par une autre source n'était rapprochée de rien, alors que les
   articles `USED` ne sont jamais purgés (« Rétention » ci-dessous) précisément
   pour servir d'archive éditoriale — l'archive existait, rien ne la
   consultait. `link_duplicates` élargit désormais l'ensemble des
   représentants POSSIBLES aux articles `USED` de l'archive, bornés par
   `cluster_archive_jours` (**7**, `app/config.py`) et hors de la fenêtre de 48 h
   (pas de double compte avec les articles frais). Un article archivé ne peut jamais devenir lui-même un
   doublon (seulement servir de cible), et un représentant déjà `USED` reste
   verrouillé par `elire_representants` même s'il est hors fenêtre — cette
   fonction se fonde sur la date du DOUBLON, pas celle du représentant, donc un
   groupe formé contre un article publié il y a des jours est bien réexaminé,
   mais jamais détrôné.

   **Contre l'archive, une mesure et un seuil PROPRES** (revue du 2026-09-21).
   La première version réutilisait le seuil de 0.65, éprouvé sur 48 h ; sur une
   archive d'un mois d'un feed centré sur quelques artistes récurrents, il
   enterrait des actus NEUVES, et le lien est irréversible en pratique (l'article
   sort de la file, le représentant `USED` est verrouillé, rien ne remonte).
   Paires mesurées, toutes des actus différentes : « Charlotte de Witte annonce
   un nouvel EP » / « … annonce une tournée mondiale » (1.00 : « EP » tombe, le
   premier titre est CONTENU dans le second), « Fred again.. sort un nouveau
   morceau » / « … un nouvel album » (0.75), « Les Transmusicales dévoilent leur
   programmation » / « … une nouvelle vague » (0.67). Aucun seuil de
   recouvrement n'épargne la première, qui vaut 1.00 : l'archive utilise donc
   `archive_similarity_from_tokens` — mots partagés rapportés au titre le plus
   LONG, qui compte ce qui DIFFÈRE — avec `cluster_archive_seuil` (**0.8**). Les
   trois paires tombent à 0.60 / 0.75 / 0.67, une vraie reprise traduite
   (« Dour Festival unveils its 2027 lineup » / « … dévoile son line-up 2027 »)
   reste à 0.80. La fenêtre fraîche de 48 h, elle, ne change pas. Et la fenêtre
   d'archive est passée de 30 à **7 jours** : une reprise ressort dans la
   semaine, au-delà le risque de faux positif dépasse le service rendu.

   **Un lien d'archive se VOIT** : compté à part (`ClusterStats.archive`,
   distinct de `frais` ; « dont N contre une actu déjà publiée » dans la sortie
   de `run_pipeline` et de `run_veille`), journalisé en WARNING avec les deux
   titres et la similarité, et tracé sur l'article lié dans `file_exclu_raison`
   (« doublon d'une actu deja publiee (article N, similarite 0.80) »).

   **Claude tranche la bande douteuse, entre les deux seuils** (Task 7,
   2026-09-21). `archive_similarity_from_tokens` et `title_similarity_from_tokens`
   se contredisent sur toute une bande de scores : mesuré sur 15 reprises
   plausibles d'une même actu, 11 passaient entre les mailles du seuil de 0.8,
   et aucun seuil ne peut à la fois les attraper et épargner les actus neuves
   (voir la démonstration mathématique dans `archive_similarity_from_tokens`).
   `link_duplicates` classe donc chaque rapprochement contre l'archive en
   TROIS bandes :
   - **certaine** — `archive_similarity_from_tokens` ≥ `cluster_archive_seuil`
     (0.8) → lié sans rien demander, comme avant ;
   - **douteuse** — sous ce seuil, mais `title_similarity_from_tokens` (le
     recouvrement) ≥ 0.65 → **Claude tranche** (`app/pipeline/
     cluster_arbitrage.py`), avec les DEUX titres et leurs résumés
     (`Article.summary`) ; motif `messages.parse()` identique à
     `scoring.make_claude_scorer`, modèle `settings.scoring_model` (Haiku,
     ~800 jetons d'entrée / ~35 de sortie mesurés, ~0,001 $/appel). Le résumé
     n'est PAS encore réécrit par le scoring à ce stade — `link_duplicates`
     tourne AVANT (voir plus haut) : c'est la description RSS brute (parfois
     l'article entier) ou la caption Instagram avec son bandeau de
     métriques. Tronqué à `scoring.RESUME_MAX_CARACTERES` (1500), comme
     `scoring.build_user_prompt` et pour la même raison : un coût d'entrée
     non maîtrisé sinon ;
   - **différente** — sous les deux seuils → aucun lien, aucun appel.

   Chaque paire (les deux ids d'article, ordre indifférent) n'est arbitrée
   qu'UNE FOIS : le verdict est mémorisé dans `cluster_arbitrage_cache_path`
   (JSON à côté du jeton, écriture atomique — même mécanique que
   `illustration_cache_path`), et relu avant tout appel.

   **Changer `CACHE_VERSION` (`app/pipeline/cluster_arbitrage.py`) désarme
   les verdicts manuels.** Un fichier d'une autre version est traité comme
   illisible, des DEUX côtés. En LECTURE, `lire_cache` rend alors un cache
   VIDE : les verdicts manuels « différent » existants **cessent de
   protéger** — l'auto-réparation ne les voit plus, et les liens qu'ils
   interdisaient se reforment dès le passage suivant, actus neuves
   enterrées sous l'archive comprises. En ÉCRITURE, `_lire_pour_ecriture`
   lève et plus aucun verdict ni compteur de tentatives ne s'écrit : chaque
   passage horaire redemande donc les mêmes paires douteuses et repaie
   jusqu'à `cluster_arbitrage_max_par_passage` appels, et
   `scripts.marquer_doublon_manuel` échoue. Supprimer le fichier « pour
   débloquer » efface les verdicts manuels pour de bon. Qui change la
   version doit donc fournir, dans le même commit, une **migration** qui
   relit l'ancien format et réécrit le nouveau en **préservant toutes les
   entrées `manuel`** (les verdicts de Claude et les tentatives peuvent, au
   pire, être rejoués) — jamais un simple changement de la constante. Une PANNE (clé
   absente, crédits épuisés, réseau mort, réponse hors schéma) n'est JAMAIS
   mémorisée comme un verdict et ne pose aucun lien : le doute favorise
   toujours la publication, la paire sera réarbitrée au passage suivant (sous
   le plafond de tentatives ci-dessous). Coût borné par passage
   (`cluster_arbitrage_max_par_passage`, 20). **Seul le passage horaire
   (`run_pipeline`, via `dedupliquer_avec_arbitrage`) arbitre** — `run_veille`
   (toutes les 5 min) appelle `link_duplicates` sans argument `arbitre`,
   distinction par PARAMÈTRE et non en devinant l'appelant. Mesuré en
   production le 2026-09-21 (352 articles frais, 14 dans l'archive) : 1 seule
   paire douteuse, 0 certaine — le volume réel est loin du plafond. Les
   compteurs `duplicates_arbitres` / `duplicates_arbitrage_pannes` du rapport
   de `run_pipeline` rendent ça visible sans fouiller les logs. Coupe-circuit :
   `cluster_arbitrage_enabled` (`.env`).

   Une réponse dont `raison` (la trace de supervision) dépasse la longueur
   attendue n'est jamais rejetée : un validateur la tronque à
   `RAISON_MAX_CARACTERES` (400), le SDK Anthropic ne garantissant cette
   longueur que côté description du schéma, pas côté validation. Chaque paire
   garde aussi un compteur de TENTATIVES distinct des verdicts
   (`Cache.tentatives`) ; passé `cluster_arbitrage_tentatives_max` (3), elle
   n'est plus jamais retentée (sans qu'aucun verdict ne soit mémorisé pour
   autant) — une panne systématique sur une paire ne doit pas consommer le
   budget de chaque passage indéfiniment. `link_duplicates` sert d'abord les
   paires JAMAIS tentées (passe 1) avant de rejouer celles déjà en échec sous
   le plafond (passe 2). Un article DIFFÉRÉ en passe 1 reste néanmoins un
   représentant POSSIBLE dès cette passe (avec un garde-fou contre
   l'auto-comparaison) : sinon un article frais plus RÉCENT qui le reprend
   usurpe sa place, et le plus ancien finit par se lier au plus récent —
   l'inverse de la règle « le représentant est l'article vu le plus tôt ».
   L'écriture du cache atomique utilise un nom de temporaire PAR PROCESSUS
   (`tempfile.mkstemp`), même mécanique que `app/pipeline/quota_graph.py` :
   un nom fixe serait écrit à deux mains par le passage horaire et
   `scripts.marquer_doublon_manuel` lancé pendant ce passage.

   **Réparer un faux positif exige un verdict MANUEL, pas seulement d'effacer
   les deux champs** — effacer `duplicate_of_id` et `file_exclu_raison` ne
   suffit pas : le passage suivant de `link_duplicates` relie l'article à
   nouveau tant qu'il reste dans la fenêtre de 48 h de sa vie publiable,
   c'est-à-dire pendant toute sa vie publiable. La réparation qui tient :
   `uv run python -m scripts.marquer_doublon_manuel --article-a ID
   --article-b ID --different --raison "..."` défait le lien existant ET pose
   un verdict manuel qui prime sur tout rapprochement automatique ultérieur —
   y compris sur la bande CERTAINE et sur une paire d'articles FRAIS (aucune
   des deux mesures de score n'échappe à cette vérification), vérifié avant
   toute mesure de score. Le même script avec `--meme-actu` confirme au
   contraire un lien qu'on veut garder quoi qu'il arrive
   (`app/pipeline/cluster_arbitrage.marquer_manuel`, qui LÈVE si l'écriture
   n'est pas confirmée par une relecture, ou si le cache existant est présent
   mais illisible — jamais un cache reconstruit à partir de rien par-dessus
   un fichier corrompu, ce qui effacerait des verdicts manuels existants ;
   `fusionner_et_ecrire` applique la même règle côté écriture automatique,
   en annulant l'écriture plutôt qu'en écrasant). Un verdict manuel posé
   PENDANT un passage — pendant que Claude, sur le même appel, répond le
   contraire — prime toujours : `link_duplicates` relit le disque juste avant
   de poser un lien tranché par Claude, et une auto-réparation tourne en tête
   de CHAQUE passage pour défaire tout lien déjà présent en base dont la
   paire porte un verdict manuel « différent », quelle que soit la façon dont
   il est apparu (course, crash entre deux écritures, version antérieure du
   code) — elle n'efface que sa propre raison d'exclusion
   (`RAISON_DOUBLON_PREFIXE`), jamais une exclusion posée ailleurs (gel ou
   composition impossibles). `scripts.marquer_doublon_manuel --different`
   applique le même filtre quand il défait un lien.

   **Deux points posent un lien vers un autre article, et chacun garde la
   règle « jamais de chaîne, un seul saut suffit toujours ».**
   - `link_duplicates.lier` (`app/pipeline/cluster.py`), pour TOUS les liens
     de la dédup (frais, certain, arbitré, manuel) : il aplatit
     IMMÉDIATEMENT toute chaîne qui en découlerait, en repointant vers la
     cible finale tout article qui pointait déjà vers l'article qu'on vient
     de lier — par construction plutôt que par un correctif localisé à une
     passe ou à une origine de lien. `db.flush()` y est explicite et
     nécessaire : `SessionLocal` tourne en `autoflush=False` en production
     (`app/db.py`), donc sans ce flush, la requête qui cherche « qui pointe
     vers cet article » ne verrait pas un lien posé plus tôt dans le MÊME
     passage. Un article ainsi repointé vers un représentant d'ARCHIVE
     (P → D → R devenu P → R) reçoit la même trace `file_exclu_raison`
     (« … via son representant D … », sans recouvrir une exclusion posée
     ailleurs) et le même compteur `archive` qu'un lien d'archive direct :
     sinon il sortirait de la file en silence.
   - `elire_representants` (`app/pipeline/election.py`), quand l'élection
     promeut un nouveau représentant : il délie l'élu et repointe vers lui
     tous les membres du groupe. Sans danger pour la règle : il ne traite
     que le groupe d'un représentant lui-même NON lié, dont tous les membres
     pointent vers lui en un saut, et jamais celui d'un représentant engagé
     — or un représentant d'archive est toujours `USED`, donc verrouillé
     (`representant_engage`). Un groupe rattaché à l'archive ne change donc
     jamais de tête par ce chemin.

   Trois limites résiduelles, assumées : une reprise ingérée par `run_veille`
   (toutes les 5 min) reste non arbitrée — donc potentiellement publiable —
   jusqu'au prochain passage horaire, qui seul arbitre ; une paire qui échoue
   à répétition reste redemandée à chaque passage tant que
   `cluster_arbitrage_tentatives_max` n'est pas atteint, avant d'être
   abandonnée pour de bon (sans jamais poser de lien) ; et un verdict manuel
   vaut aussi entre deux articles FRAIS (hors archive), pas seulement contre
   l'archive.

## Rétention

**Exception `USED`** : un article marqué posté n'est JAMAIS purgé — c'est l'archive
éditoriale (savoir ce qui a été publié, ne pas le reposter). `REVIEWED` n'est que du
tri et ne protège pas.

On ne garde en base que les articles publiés dans les `retention_days` (**2** depuis le
2026-09-21 ; 14 auparavant) derniers
jours, par DATE DE POST (`published_at`), pas date d'ajout — `app/pipeline/retention.py`.
Purge à chaque run + skip à l'ingestion (on ne score pas un article déjà hors fenêtre).
Articles sans date de publication : filet sur `fetched_at` (seul recours possible).
Récolte X bornée à `x_backfill_days` (2) au premier fetch. Réglables via `.env`.

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
- Radar maison (`app/pipeline/radar.py` + source X `url="radar:"`) : les artistes
  pop/électro extraits par le scoring (`mentioned_artists`) sont agrégés (top N sur
  7j, pertinence >= 60) et injectés dynamiquement dans une recherche X `has:videos`
  pour chasser les vidéos live. Deux garde-fous appris à l'usage : (1) GATE PAR
  GENRE DE SOURCE — seules les sources pop/électro alimentent le radar, car les
  sources festivals sont multi-genres et le modèle ne filtre pas le genre de façon
  fiable (Haiku listait Ultra Vomit/Dan ar Braz) ; (2) ANTI-SPAM — le tweet doit
  vraiment mentionner l'artiste (le filtre engagement seul laissait passer du spam
  viral). Rendement réaliste : brille quand un artiste suivi a un moment live viral,
  calme sinon — ce n'est pas un firehose. Seuil `X_SEARCH_MIN_LIKES` ajustable.
- Spotify/Beatport écartés (2026 : API fermées/gated) — le "radar maison" les
  remplace : notre propre flux scoré EST le signal "artistes du moment".
- X passe par l'API OFFICIELLE v2 pay-per-use (`app/sources/x_api.py`,
  ~0,005 $/tweet lu, since_id natif dans sources.state) — nécessite la
  facturation activée sur developer.x.com, sinon 402 Payment Required.
  L'adapter Apify (`x_apify.py`) reste en alternative : l'actor
  `apidojo~tweet-scraper` interdit l'API sur le plan Apify gratuit
  (runs "SUCCEEDED" mais `{"noResults": true}`) — plan payant ~39 $/mois requis.
- **Historique (plus d'actualité depuis le tout-local) :** depuis les runners
  GitHub Actions (IP datacenter Azure), Mixmag bloquait par intermittence (403
  Cloudflare) et Reddit aussi (429). C'est la raison pour laquelle l'ingestion
  était déjà locale, et c'est le gain concret du passage complet en local. À
  ressortir seulement si le pipeline repart un jour sur un datacenter : passer
  alors Reddit sur son API officielle OAuth, fiable depuis ces IP.
- Postgres local : auth en `trust` (pas de mot de passe), user `postgres`.
- **Un job launchd peut partir avant que le réseau soit debout.** Un agent avec
  `RunAtLoad`, ou réveillé par `StartInterval` à la sortie de veille, s'exécute
  parfois avant que la résolution DNS réponde : l'ancien agent `digest` a échoué
  ainsi **chaque jour pendant cinq semaines** sur `failed to resolve host
  … .neon.tech`, sans que rien ne le signale. D'où `attendre_base()` dans
  `app/db.py` — quelques `SELECT 1` espacés, puis une erreur explicite et un code
  de sortie non nul, que launchd enregistre et que `--status` affiche. **Tout
  nouveau point d'entrée planifié doit l'appeler en premier.**
- **`newsyslog` n'étend pas les jokers** dans le champ du nom de fichier : un
  `*.log` ne fait tourner aucun fichier, silencieusement. D'où l'énumération des
  quatre logs. Et le drapeau `N` est nécessaire : sans champ `path_to_pid_file`,
  chaque rotation envoie un SIGHUP à `syslogd`, étranger au projet.
- **La rotation ne peut pas fonctionner pour l'agent dashboard.** `newsyslog`
  renomme puis compresse ; un uvicorn `KeepAlive` garde son descripteur ouvert
  toute sa vie et ne le réouvre jamais, donc la sortie post-rotation part vers un
  inode sans nom. L'agent de pipeline y échappe (nouveau processus chaque
  heure). Impact faible grâce à `--no-access-log`, mais à savoir avant de chercher
  pourquoi `dashboard.log` est vide.
- `media.fields=url,preview_image_url` sur l'API X v2 ne suffit PAS pour les
  reels : ce sont les `variants` d'un tweet vidéo qui portent les MP4
  effectivement téléchargeables (`url` reste une vignette) — et sans le champ
  `type` en plus, une vidéo est indistinguable d'une photo, donc le format
  REELS reste indécidable même avec `preview_image_url` demandé.
- Un flux RSS ne publie jamais de FICHIER vidéo, seulement un lien vers une
  page qui en contient un (Instagram, YouTube...) : ce n'est pas une limite du
  code, c'est une propriété de ce que les flux exposent. Les reels viendront
  donc de X (qui donne un MP4 direct via `variants`) ou de rien — jamais d'une
  source RSS.
- L'union discriminée `SlideIn` (`app/schemas.py`, `CoverIn | MediaIn` sur
  `kind`) REJETTE en 422 une slide qui arrive sans `kind` du tout
  (`union_tag_not_found`) plutôt que de retomber sur `CoverIn` par défaut :
  toute slide envoyée dans un `PATCH /posts/{id}` doit porter `kind`
  explicitement, y compris quand elle vient d'un éditeur qui ne manipulait
  jusque-là que des covers.
- Dans une migration écrite à la main (`0010_typed_media.py`), un alias `AS
  url` dans un `UPDATE ... FROM` est résolu par Postgres sur la colonne RÉELLE
  `articles.url` plutôt que sur l'alias, sans avertissement : la transformation
  écrivait donc l'URL de l'article, pas celle du média. Silencieux — repéré
  seulement en vérifiant la transformation à la main sur un cluster jetable, pas
  par une erreur SQL. Nommer l'alias différemment de toute colonne existante
  (`media_url`, pas `url`) évite le piège.
- **`business_discovery` ne voit que les comptes PROFESSIONNELS** (business ou
  creator). Un artiste emergent en profil perso — soit une bonne part du deal-flow
  A&R — est invisible de cette API. La source Instagram COMPLETE les RSS, elle ne
  les remplace pas. Et les `media_url` du CDN Instagram sont SIGNEES et EXPIRENT
  en quelques jours : stocker l'URL puis la servir plus tard donne un 404
  silencieux, d'ou le telechargement de l'asset au moment du gel.
- **Meta omet `media_url` des médias qui contiennent du contenu protégé**
  (documenté, et constaté le 2026-09-17 : 10 reels sur 10 de `davidguetta`, 39
  reels ingérés sur 59 sans vidéo). Un reel d'artiste porte presque toujours de la
  musique : **il n'est pas téléchargeable par l'API**. `_media` garde alors une
  entrée `video` **sans URL**, la vignette (`thumbnail_url`) en `preview_url` :
  le post reste illustré et identifiable comme reel, et `video_recuperable` dit
  si yt-dlp pourra le chercher au gel (permalink `/reel/`, ou vidéo seule).
  L'offre (`directes` contre le reste) est à surveiller au dashboard.
- **La durée d'une vidéo Instagram n'est pas dans Graph, elle est dans son
  URL.** Le champ demandé est `media_url,thumbnail_url` et rien d'autre : les
  642 vidéos Instagram de la base avaient `duration_ms` à NULL le 2026-09-21.
  Or `videos_exploitables` laisse passer une durée INCONNUE — choix assumé
  (« mieux vaut proposer le reel que griser un bouton sur une donnée absente »).
  Conséquence mesurée ce jour-là : une vidéo de **190 s** est restée éligible,
  a fait composer un post (Sonnet payé) et n'a échoué qu'au gel, APRÈS
  téléchargement. L'URL signée du CDN portait pourtant la réponse depuis le
  début, dans le paramètre `efg` (base64 → JSON → `duration_s`) : **642 sur
  642** le portaient. D'où `media.duree_depuis_url()`, appelée dans `entree()`
  — le point de passage de toutes les sources — en DERNIER recours seulement :
  une durée donnée par la source (sonde ffprobe, API X) est mesurée, celle de
  l'URL n'est que déclarative, et elle n'est jamais écrasée. Le module ne lève
  jamais : paramètre absent, base64 ou JSON invalide, valeur absurde → `None`,
  donc exactement le comportement d'avant.
- **Le quota Graph de l'app est partagé et vite atteint** : le 2026-09-17, ~310
  appels de vérification plus un passage sur 120 comptes dans la même heure ont
  rendu l'erreur code 4 (« Application request limit reached ») pendant une heure.
  D'où `instagram_intervalle_heures` : chaque compte n'est lu qu'une fois
  toutes les N heures, et les comptes jamais lus attendent leur créneau (heure
  modulo N == id modulo N) pour qu'un lot ajouté d'un coup ne parte pas dans la
  même heure. Vérifier des comptes en masse (`x-app-usage` à surveiller) consomme
  le même quota que le pipeline.
- **UN APPEL REFUSÉ POUR QUOTA CONSOMME QUAND MÊME DU QUOTA — donc insister
  entretient la panne** (mesuré le 2026-09-19 dans `pipeline.log`). Le passage
  horaire attaquait les 123 comptes, encaissait ~115 refus, et ces refus
  alimentaient le dépassement de l'heure suivante : le système se maintenait
  lui-même en panne. Nombre de sources en erreur par run horaire, presque toutes
  en « Meta HTTP 403 (code 4) » :

      18/09 09:05    36 sur 145        18/09 17:14    94
      18/09 10:14     0                18/09 19:42    99
      18/09 11:21    70                18/09 22:03   112
      18/09 15:08   129                18/09 23:10   116

  751 erreurs de quota dans le log, et environ 80 % des comptes Instagram plus
  lus du tout depuis le 18/09 vers 11 h. Deux leçons câblées. (1) Le
  **coupe-circuit** : au premier code de quota, `run_ingest` cesse d'appeler Meta
  et pose la pause partagée (`app/pipeline/quota_graph.py`) ; le run continue sur
  les RSS et Reddit, qui ne partagent pas ce quota. (2) Une **source non appelée
  n'est pas une source en erreur** : `IngestStats.reportee` la distingue,
  `marquer_sante` ne touche plus à son état (un succès mentirait, une erreur
  l'accuserait à tort, et effacer l'ancienne trace ferait oublier une source
  réellement morte) et `diagnostiquer` ne la compte plus. Les « 116 source(s) en
  erreur » étaient un mensonge de supervision sous lequel la vraie panne était
  invisible. Palliatif appliqué par l'utilisateur en attendant :
  `INSTAGRAM_INTERVALLE_HEURES` passé de 2 à 3.
- **Source Instagram** (`app/sources/instagram.py`) : jeton `meta_fb_access_token`
  UNIQUEMENT (voir « Deux jetons Meta »). Absent (ou `META_IG_USER_ID` absent), la
  source est SAUTÉE en silence et rend zéro item — comme les alertes sans webhook ;
  run_pipeline ne casse pas. Une erreur Graph lève un `RuntimeError` lisible (sans
  le jeton, que l'URL de requête porte), attrapé par `run_ingest`, consigné dans
  `sources.state.last_error` et remonté par `diagnostiquer`. État : `dernier_post`
  (date du post le plus récent vu). Migration `0015_source_instagram` (CHECK
  `sources.type` élargi) — elle était numérotée 0014 avant de rejoindre `main`,
  où `0014_creneaux` avait pris le numéro.
- **Les sources X sont eteintes** (2026-09-11) : la facturation developer.x.com
  n'est plus active, les 85 sources actives (sur 100 au total) renvoyaient 402 a
  chaque run et noyaient la supervision. `scripts/desactiver_sources_x.py` les
  eteint sans les supprimer — leur `since_id` est conserve. **Déjà appliqué en
  production le 2026-09-17** (à la main ; ids sauvegardés dans
  `~/Library/Application Support/Kinda/sources_x_desactivees.json`). Le script
  reste, idempotent : le relancer ne touche rien. Le code de l'adapter X est
  conservé intact.
- **Une URL de média qui traverse du HTML en ressort ÉCHAPPÉE.** `&` y devient
  `&amp;` — c'est la norme d'un attribut (`<img src>` d'une description RSS,
  `<meta og:image>`). Stockée telle quelle, elle n'est plus la même URL, et le CDN
  Instagram, dont les liens signés portent `_nc_cat`, `ccb`, `oh`, `oe` séparés par
  des `&`, la refuse. Le reel du 2026-09-17 20:24 (post 29, article 17974) est
  ainsi parti sur le FOND DE SECOURS du gabarit : Chromium demandait l'URL
  échappée, recevait une erreur, et `fetch_image_data_uri` retombait EN SILENCE
  sur `fallback.jpg`. D'où `app/content/urls.py` et deux étages : nettoyage à
  l'ingestion dans `media.entree()` — le point de passage de TOUTES les sources,
  plutôt qu'une fois par adapter —, et ceinture à l'usage partout où une URL part
  sur le réseau (rendu, export, gel des photos et des vidéos, clé du cache des
  visuels — sinon la même image occupe deux copies et celle posée depuis l'URL
  échappée n'est jamais retrouvée). Le schéma est vérifié au passage : http(s)
  seulement, jamais un `file://` qui lirait un fichier de la machine.
  `urls_interdites` rend les DEUX formes de chaque URL, pour que la règle
  « jamais le visuel d'un compte média » vaille sur les lignes anciennes comme
  sur les neuves. Les lignes déjà en base se rattrapent avec
  `scripts/nettoyer_urls_medias.py`.
- **Une transaction ouverte pendant une attente, c'est une session tuée par
  Neon.** Une session qui a LU garde sa transaction ouverte tant qu'on ne la
  clôt pas : la publication à l'heure lisait le créneau, puis dormait jusqu'à
  l'heure affichée et appelait Meta, connexion « idle in transaction » pendant
  tout ce temps. Le 2026-09-17, le reel était EN LIGNE et l'écriture d'après
  publication tombait sur un `IdleInTransactionSessionTimeout` : créneau resté
  ECHEC, `ig_media_id` NULL, article exclu et jamais USED — la base ne disait
  plus la réalité, et l'utilisateur voyait un faux échec. `pool_pre_ping` ne
  couvre PAS ce cas (il remplace une connexion morte, pas une transaction
  avortée). D'où, dans `app/schedule/runner.py`, `_clore_transaction()` avant
  l'attente (tout ce dont l'appel a besoin est lu avant, `expire_on_commit=False`
  garde les valeurs) et `_ecrire_apres_publication()`, qui rejoue UNE fois
  l'écriture après un `rollback()` si la connexion est tombée. Ce second essai ne
  republie rien : seule la base est retouchée, la réclamation committée avant
  l'appel reste le garde-fou contre le doublon. À savoir : le gel et la
  composition tiennent encore une transaction pendant leurs minutes de travail
  (yt-dlp, ffmpeg, Claude) — même risque, non traité.
- **`op.f()` ne s'appelle QUE dans un callable de migration.** Pose au niveau
  module, il leve `NameError: Can't invoke function 'f', as the proxy object
  has not yet been established` — mais seulement sur les chemins qui chargent
  les revisions hors contexte de migration (`alembic history`, `alembic check`) ;
  `alembic upgrade` passe, ce qui rend le defaut invisible a l'usage courant.
  Aucun test n'importait les migrations, si bien que la suite entiere passait
  au vert dessus : d'ou la sentinelle `tests/test_migrations.py`, qui les fait
  toutes charger sans base de donnees.

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
