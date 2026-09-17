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
  qu'il affiche.

  Pas d'envoi de mail : l'utilisateur n'en veut pas — le digest n'est PAS
  planifié (le code `app/delivery/{digest,email}.py` et `scripts/send_digest.py`
  restent utilisables à la main ou pour une future UI). Ne pas re-proposer SMTP.
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
sortie est l'actu primaire. Les comptes MÉDIAS (Guettapen, Tsugi, Mixmag, DJ Mag,
Electro News, Pop Crave, Konbini, Booska-P, AllTime EDM, Billboard, FIP...) sont un
**filet de sécurité** : reposter leur contenu serait du plagiat, donc ils notent
nettement plus bas (décote 15-20, plafond 59 pour une actu ordinaire) — SAUF une
actu majeure que les artistes ne couvriront pas (loi qui menace la scène,
annulation de festival, décès, fermeture de club), qui garde sa note pour ne pas
être ratée. Festivals/labels/clubs : annonce officielle = source primaire, promo
courante 40-55. Et un post SANS INFORMATION (légende vide — titre « Post
Instagram » —, emojis/hashtags seuls, selfie, « link in bio », teaser d'idole sans
fait) note sous 30 quel que soit le compte : The Weeknd et IVE y prenaient 75. Les
vraies actus pop/K-pop gardent leurs notes hautes ; l'axe emergence n'est pas
touché.

Mécanique : colonne `sources.categorie` (VARCHAR + CHECK, migration 0016, NULL =
inconnu) ; `build_user_prompt` passe « Format : » (post Instagram, article de
presse, post Reddit, tweet) et « Type de compte : » ; règles 5 et 6 du
`SYSTEM_PROMPT`. Éditable au dashboard (panneau Sources), via `PATCH /sources/{id}`
et `add_source --categorie`.

**Pré-filtre local** : même prompt, plus `CONSIGNE_PREFILTRE` — la décote média ne
doit jamais le faire noter sous 45 un contenu qui porte un fait ; seuls le bruit
évident et les posts vides passent dessous. Sans ça, le 4B, plus brutal, écarterait
les exceptions « actu majeure » que Claude aurait repêchées à 60+.

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
   compté dans `prefiltered` et `quasi_vides`.
2. **Plafond média** : `sources.categorie == media` → relevance et hotness
   plafonnées à 59, SAUF si le modèle a posé `actu_majeure: true` (champ de
   `ArticleScore`, défaut false — le pré-filtre et les anciennes notes restent
   valides et ne lèvent jamais le plafond). Règle 5 du prompt : true seulement
   pour loi/réglementation qui menace la scène, annulation de festival, décès,
   fermeture de club emblématique ou événement de poids comparable. Le post vide
   l'emporte sur `actu_majeure`.
La décision d'écarter du pré-filtre se prend sur la note BRUTE : le plafond média
(59) est au-dessus du seuil (45) et ne fait jamais écarter un article.

Nettoyage de la file sans rien payer : `uv run python -m scripts.appliquer_garde_fous
[--titres-courts] [--apply]`. Ne re-applique QUE le garde-fou 1 : les anciennes
notes n'ont pas `actu_majeure`, plafonner les médias écraserait les vraies actus
majeures. Une note déjà posée a perdu sa caption : par défaut seul le titre « Post
Instagram » est visé (certain) ; `--titres-courts` vise aussi les titres de moins
de 6 mots (probable seulement — le titre n'est que la première phrase). Les
articles USED ne sont jamais touchés.

## Mémoire A&R : tables `artists` / `artist_mentions`

Les articles sont purgés à 14 jours ; **les artistes et leurs citations, jamais**.
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
formats — `template` sur le post : `kinda_post` (1080×1440, POST, toujours
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
L'interdiction d'emoji dans `headline`/`title`/`subtitle` reste stricte (textes
rendus dans une police).

## Publication automatique (`app/publish/`, `app/schedule/`)

Kinda publie seul **deux carrousels et deux reels par jour**, choisis dans le flux
scoré. Design : `docs/superpowers/specs/2026-09-11-publication-meta-design.md`
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

**La file.** 6 créneaux (3 jours × 2), heure tirée dans 08:30-11:00 et 17:00-19:30
(Paris) avec une graine dérivée de la date — sinon l'heure glisserait à chaque
passage. Le coût dicte le calendrier : composition Claude à J-1, gel (rendu et
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
2200 caractères et 30 hashtags. La cover `kinda_post` (3:4) est donc recadrée au
centre en 1080×1350 par `app/publish/images.py` (`publication_ratio_min`).

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
(15 s, 10 min max), puis le parent. **Le média servant de fond à la cover**
(`slides[0].image_url`, comparé à `url` et à `preview_url`) n'est pas répété
ensuite. **Visuels d'un compte Instagram de catégorie `media` jamais repris**
(watermarks, `visuels_interdits`) : cover seule.

**Hébergement éphémère.** Meta télécharge les images en cURL. Au moment de publier
seulement, un mini-serveur local sert le dossier du post (sans listing) et un tunnel
rapide `cloudflared` l'expose ; tout est fermé après `media_publish`. Le dashboard
n'est jamais exposé. `cloudflared` est cherché hors du PATH de launchd, comme ffmpeg.

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
jusqu'à `publier_a` (jamais plus que l'avance ; conteneur prêt après l'heure :
aucune attente), **mode relu** juste avant `media_publish`. Relu `apercu` → pas
de `media_publish`, état APERCU ; relu `arret` → pas de `media_publish`, le
créneau **redevient GELE** (exclusion levée, dossier gardé : il partira si l'on
repasse en `reel` avant la limite de retard). En `apercu`, un créneau à venir
n'est pas réclamé d'avance : il attend son heure. `publie_le` = heure réelle de
publication. Crochets : `publier_fichiers` / `publier_reel` acceptent
`attendre_jusqu_a` et `encore_publier` (appelés dans le `with heberger(...)`),
sans effet quand ils sont absents ; `tourner(dormir=...)` injecte le sommeil.
**Compromis assumé :** un processus tué PENDANT l'attente laisse le créneau en
ECHEC « publication interrompue avant confirmation » — ce post est perdu, jamais
doublé. launchd ne tue pas un passage en cours en temps normal. Pendant ces
quelques minutes, le dashboard affiche le créneau en « Échec » (état de
réclamation) jusqu'à la publication.

Essai sans publier : `uv run python -m scripts.tester_publication --post <ID>`.

**Reels automatiques (2026-09-17).** `creneaux.format` (`carrousel` | `reel`,
migration 0017) ; fenêtres reel propres (`PUBLICATION_REEL_FENETRES`, défaut
`12:00-14:00,20:30-22:30`), `PUBLICATION_CRENEAUX_REEL=0` les désactive.
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
retours à la ligne simples doublés, ou, en bloc de plus de deux phrases, est
coupée par paires de phrases (les emojis de fin restent avec leur phrase ; « ... »
suivi d'une minuscule ne coupe pas). Ordre publié : légende, crédit, puis les
hashtags en dernier bloc ; la coupe à 2200 caractères compte les lignes ajoutées. ffmpeg absent = panne systémique qui
ne suspend que les reels. `GET /schedule/offre-reels` mesure la matière.

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

## Dédup à deux étages

1. **Par hash** (`pipeline/dedup.py`) : même url normalisée + même titre → le même
   lien vu deux fois n'entre qu'une fois. Attrape les re-fetchs, pas les reprises.
2. **Par titre, ENTRE sources** (`pipeline/cluster.py`) : la même actu couverte par
   Mixmag, DJ Mag et Tsugi produisait trois articles. On rapproche les titres
   normalisés (accents, ponctuation, traits d'union recollés, mots vides FR+EN) par
   **coefficient de recouvrement** des mots significatifs — pas Jaccard, qui
   punirait le titre traduit ou suffixé du nom du média. Seuil 0.65, fenêtre 48 h,
   minimum 2 mots partagés. Le représentant est l'article vu le plus tôt ; les liens
   ne forment jamais de chaîne (un saut suffit toujours). Idempotent.

   Conséquences câblées : `link_duplicates` tourne AVANT le scoring dans
   `run_pipeline` (un doublon n'est jamais scoré, donc jamais payé), et l'API
   `/articles` masque les doublons par défaut (`include_duplicates=true` pour les
   voir).

## Rétention

**Exception `USED`** : un article marqué posté n'est JAMAIS purgé — c'est l'archive
éditoriale (savoir ce qui a été publié, ne pas le reposter). `REVIEWED` n'est que du
tri et ne protège pas.

On ne garde en base que les articles publiés dans les `retention_days` (14) derniers
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
- **Le quota Graph de l'app est partagé et vite atteint** : le 2026-09-17, ~310
  appels de vérification plus un passage sur 120 comptes dans la même heure ont
  rendu l'erreur code 4 (« Application request limit reached ») pendant une heure.
  D'où `instagram_intervalle_heures` (3) : chaque compte n'est lu qu'une fois
  toutes les 3 h, et les comptes jamais lus attendent leur créneau (heure modulo
  3 == id modulo 3) pour qu'un lot ajouté d'un coup ne parte pas dans la même
  heure. Vérifier des comptes en masse (`x-app-usage` à surveiller) consomme le
  même quota que le pipeline.
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
