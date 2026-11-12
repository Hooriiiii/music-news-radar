# AGENTS.md

This file provides guidance to Codex and other coding agents when working with code in this repository.

> **Copie de `CLAUDE.md`, qui fait foi.** Ne pas éditer ce fichier à la main : modifier `CLAUDE.md`, puis
> régénérer celui-ci (même contenu, seul cet en-tête change). Un test vérifie que les deux restent identiques.

**Ce fichier est court EXPRÈS, et il est chargé dans le contexte de chaque
agent.** Il porte l'identité du projet, la ligne éditoriale, les commandes et les
règles dont la violation coûte le plus cher — celles qu'on peut enfreindre
depuis n'importe quel fichier. Tout le reste vit dans `docs/reference/`, un
fichier par domaine, chacun autonome.

Chaque paragraphe de ces références a été payé par un post raté, une actu brûlée
ou une panne de plusieurs jours : **les mesures y restent collées au seuil
qu'elles justifient** (un seuil sans sa mesure devient un seuil qu'on change à
l'œil). Les titres de section n'ont pas bougé : un renvoi du type « voir
« Dédup à deux étages » » se résout avec l'index ci-dessous.

## Index : quoi lire, et QUAND

Lis la référence AVANT de toucher au domaine, pas après. Un seul fichier suffit
presque toujours.

- **`docs/reference/publication.md`** — avant de toucher à la file de
  publication, aux créneaux, au gel, à l'hébergement éphémère, au tunnel
  `cloudflared`, à yt-dlp, aux reels automatiques, ou à **tout chemin qui mène à
  `media_publish`**. Sections : « Publication automatique », « Exclusion »,
  « Reels automatiques », « Deux jetons Meta ».
- **`docs/reference/relecture-et-controle-editorial.md`** — avant de toucher à
  `GET /relecture`, `PATCH /posts/{id}`, la réouverture d'un créneau gelé ou le
  manifeste du gel (`creneaux.elements`). Sections : « La fenetre de relecture
  avant publication », « Le controle editorial sur ce qui va partir ».
- **`docs/reference/generation-de-posts.md`** — avant de toucher à
  `app/content/` : composition, gabarits et mise en page, rendu Chromium, vidéo
  et reel, juge d'affiches, agrandissement, légende, export. Section :
  « Génération de posts ».
- **`docs/reference/visuels.md`** — avant de faire LIRE un média à quoi que ce
  soit qui publie, ou de toucher aux réserves de visuels (compte source,
  artistes cités, recherche web). Section : « Visuels : l'info des médias, les
  images des artistes ».
- **`docs/reference/dedup-et-retention.md`** — avant de toucher à la dédup, au
  clustering, à l'élection du représentant, à l'arbitrage Claude, à
  `CACHE_VERSION`, à la purge, ou avant de réparer un lien de doublon à la main.
  Sections : « Dédup à deux étages », « Rétention ».
- **`docs/reference/scoring.md`** — avant de toucher à `app/pipeline/scoring.py`,
  au `SYSTEM_PROMPT`, au pré-filtre local, à `sources.categorie` ou à un
  garde-fou déterministe. Sections : « Deux axes de scoring », « Qui parle :
  `sources.categorie` ».
- **`docs/reference/sources-et-ingestion.md`** — avant d'ajouter ou de reclasser
  une source, de changer une cadence d'ingestion, ou de toucher à
  `app/sources/`, `veille.py`, `illustrations.py`. Sections : « Philosophie de
  sourcing », « Veille rapide », « Illustration des articles ».
- **`docs/reference/memoire-ar.md`** — avant de toucher à `artists` /
  `artist_mentions`, à `rising_artists`, ou de construire quoi que ce soit qui
  CLASSE des artistes. Section : « Mémoire A&R ».
- **`docs/reference/dashboard.md`** — avant de toucher à
  `app/static/dashboard.html`, à `app/supervision.py`, au panneau Artistes ou
  aux couleurs de la console. Sections : « Bandeau de supervision », « Le
  dashboard, apres la refonte UX », « Le panneau Artistes », « La charte Kinda
  dans la console ».
- **`docs/reference/contraintes-terrain.md`** — dès qu'une requête réseau, une
  API externe, un jeton, une URL de média, une migration écrite à la main ou un
  job launchd entre dans le tableau, et avant de conclure « ça devrait
  marcher ». Section : « Contraintes apprises sur le terrain ».
- **`docs/reference/architecture.md`** — avant d'ajouter une source, une table,
  une migration ou un point d'entrée. Sections : « Architecture »,
  « Conventions », « Roadmap ».
- **`docs/reference/scheduler.md`** — avant de toucher à une cadence d'agent, à
  un nom de log, ou de proposer de planifier quoi que ce soit. Section :
  « Scheduler ».
- **`docs/cahier-des-charges.md`** — la feuille de route (v2, 2026-11-12) :
  avant de démarrer un chantier, de proposer une refonte ou de choisir quoi
  faire ensuite. Cap : **remettre en route sous budget et assainir au fil de
  l'eau, PAS réécrire** (la v1 « refonte » a été écartée après dix relectures
  critiques ; ses erreurs sont listées en annexe A). Chantiers C1-C9,
  invariants I1-I18, décisions D0-D8.
- **Maquettes : Figma fait foi** — fichier
  [🎀 Kinda](https://www.figma.com/design/FWfcG6QYizPW46L8gaDTGJ/) : page
  « 📰 Média · Templates » avant de toucher à un gabarit de cover, pages
  « 🖥️ Console » avant de toucher à la console. Les PNG de `exemples/` n'en
  sont que des exports ; en cas d'écart, Figma l'emporte.

## Règles qui ne se négocient jamais

Elles peuvent être enfreintes depuis n'importe quel fichier, et chacune a déjà
coûté quelque chose. Le détail et la mesure sont dans la référence citée.

**Publier est public et irréversible.**
- **Plutôt un post MANQUÉ qu'un post publié DEUX FOIS.** La réclamation d'un
  créneau (GELE → ECHEC, raison « publication interrompue avant confirmation »)
  est committée AVANT tout appel à Meta, et chaque étape s'écrit par un UPDATE
  CONDITIONNEL sur (état, article, post). N'ajoute jamais un chemin vers
  `media_publish` qui ne passe pas par là. (`publication.md`)
- **Une panne MACHINE n'exclut JAMAIS un article de la file** — réseau, tunnel
  injoignable, crédits Anthropic épuisés, ffmpeg ou Chromium absents, quota
  Graph. Seule une faute de l'article l'exclut, et un article exclu ne revient
  jamais. En cas de doute, DIFFÉRER (on retente) ou SAUTER (on libère le
  créneau), jamais exclure. (`publication.md`)
- **Passer en mode `reel` est une décision de l'utilisateur**, pas un défaut :
  confirmation « PUBLIER », et les reels automatiques sont en opt-in explicite au
  dashboard. (`publication.md`)
- **La relecture est une OPTION, jamais une condition** : si personne ne relit,
  ça publie quand même à l'heure. (`relecture-et-controle-editorial.md`)

**Kinda est une SOURCE, pas un intermédiaire.**
- **L'INFO d'un compte média, oui ; ses VISUELS, jamais** (watermarks, plagiat
  visible d'un concurrent). Tout ce qui lit des médias pour publier passe par
  `visuels_autorises` / `urls_interdites`. (`visuels.md`)
- **Jamais un post qui ne MONTRE RIEN de l'actu** ; jamais une affiche de
  line-up en fond de cover ; une seule affiche en slide.
  (`generation-de-posts.md`)
- **Le crédit `📸 @compte` / `🎥 @compte` est obligatoire et jamais coupé** ;
  on ne nomme pas le média d'où vient l'info dans la légende.
  (`generation-de-posts.md`)

**Un signal doit dire la vérité, ou se taire bruyamment.**
- **Une valeur qu'on n'a pas pu lire s'affiche INCONNUE, jamais verte, jamais
  « 0 ».** Un voyant vert sur une machine en panne est pire que pas de voyant :
  il apprend à ne plus regarder. Même règle pour une tendance ou une mesure
  absente. (`dashboard.md`)
- **Les deux axes `relevance` et `emergence` ne se compensent JAMAIS** : ne
  jamais revenir à un axe unique (il détruisait exactement le signal du label),
  et ne jamais mettre un quota de mix éditorial dans `relevance`. (`scoring.md`)
- **Un contrôle de FORME relance puis accepte avec un avertissement** — emojis,
  accents, clôture en question, énumération surlignée, repères de temps : jamais
  perdre un créneau pour un défaut de forme. (`generation-de-posts.md`)
- **Le seul canal de notification est macOS** : ni mail ni Discord. Ne pas
  re-proposer SMTP.

**Les seuils sont MESURÉS.**
- Chaque seuil, chaque couleur, chaque tolérance de ce dépôt est accompagné de
  la mesure qui l'a fixé, dans sa référence — souvent contre une alternative
  plus simple que la mesure a condamnée. **Ne change jamais un seuil à l'œil** :
  refais la mesure, et attends-toi à ce qu'un test-sentinelle rougisse.
- Corollaire : si tu ajoutes une règle, mesure-la sur les données réelles avant
  de la câbler, et écris la mesure à côté.

**Le budget IA est plafonné.**
- **40 $ par mois au grand maximum pour ce dépôt, cible 30 $** (décision de
  l'utilisateur du 2026-11-12 : 100 $ d'API par mois via Claude Max, partagés
  avec d'autres projets). Tout appel Anthropic compte, mesures et diagnostics
  `--apply` compris : chiffre le coût AVANT de lancer quoi que ce soit de
  payant. Au plafond, la chaîne notation → publication se met en PAUSE
  (différer) ; elle ne dépasse jamais et n'exclut jamais rien.
  (`cahier-des-charges.md`, C1 et D2)
- **Le jeton Meta de collecte est celui de l'utilisateur système
  `KindaRadar`, sans expiration**, depuis le 2026-11-12. Ne jamais revenir au
  jeton Facebook PERSONNEL de l'utilisateur : il veut Kinda dissocié de ses
  comptes perso. (`publication.md`, « Deux jetons Meta »)

**Base de données et état sur disque.**
- **`SessionLocal` tourne en `autoflush=False` en production** (`app/db.py`) : un
  `db.flush()` explicite est nécessaire pour qu'une requête voie une écriture
  posée plus tôt dans le MÊME passage. (`dedup-et-retention.md`)
- **Une colonne JSON/JSONB se RÉASSIGNE en entier**, jamais mutée en place,
  sinon SQLAlchemy ne persiste pas. `posts.variants` s'écrit en FUSION
  (`{**post.variants, ...}`), jamais en bloc. (`generation-de-posts.md`,
  `architecture.md`)
- **Les fichiers d'état à côté du jeton Meta sont partagés entre agents** :
  écriture atomique, temporaire nommé PAR PROCESSUS, relecture-fusion à
  l'instant de l'écriture, et AUCUNE écriture en LECTURE. (`publication.md`,
  `sources-et-ingestion.md`)
- **Tout point d'entrée planifié appelle `attendre_base()` en premier**, et ne
  tient jamais une transaction ouverte pendant une attente (Neon tue la
  session). (`architecture.md`, `contraintes-terrain.md`)

**Ce qui n'est jamais purgé.**
- Les articles `USED`, les tables `artists` / `artist_mentions` et les `posts` ne
  sont **jamais** purgés : c'est l'archive éditoriale (ne pas reposter) et
  l'actif A&R. Le reste part à `retention_days` (**2 jours**) — d'où un créneau
  AUTONOME de son article, et des champs dénormalisés exprès.
  (`dedup-et-retention.md`, `publication.md`)

## Projet

**KINDA est une structure d'accompagnement de jeunes artistes**, pas un média qui
aiderait des artistes à l'occasion. L'ambition est de lancer des carrières : donner
à de jeunes artistes une visibilité qui tienne dans la durée, et une place dans
l'industrie.

Formulation de l'utilisateur (2026-09-23), qui fait foi : **« un incubateur complet
qui cherche à valoriser de nouvelles sonorités et de nouvelles personnes, pour
grandir avec elles »**. Kinda agit sur TOUS les aspects de l'industrie **à la
fois** — un média, un label, des plugins. Ce n'est pas une progression en trois
étapes : un artiste qui démarre bute en même temps sur les outils, sur la
visibilité et sur l'accès au marché, et chacun de ces murs rend les autres plus
hauts.

**Repositionnement du 2026-09-30** (page Notion « Vision & stratégie de
lancement ») : Kinda est un **écosystème créatif** consacré aux artistes
émergents, en cinq branches (plugins, média, label, événementiel, communauté
& réseau), et **les plugins sont la priorité commerciale n°1**. Le média
continue en parallèle : il ne porte plus l'effort principal, d'où un temps de
développement à plafonner (décision D0 du cahier des charges).

Ce dépôt ne contient que la brique MÉDIA : le compte d'actu `@kindanewsfrance`.
Les plugins et le label existent dans le projet, pas dans le code.

Conséquence architecturale majeure : le compte d'actu est à la fois l'organe de
DISTRIBUTION (il fait connaître plugins et label) et l'organe de DÉTECTION (le flux
scoré est le deal-flow du label). D'où l'axe `emergence` et les tables `artists` /
`artist_mentions` — voir « Deux axes de scoring » et « Mémoire A&R » plus bas.

**Référence produit : `docs/contexte-produit.md`.** Il dit CE QU'ON VEUT
(positionnement éditorial, trajectoire vers une architecture par ÉVÉNEMENT plutôt
que par post) ; ce document-ci dit ce que le code FAIT et POURQUOI. Quand les deux
divergent, l'intention se lit là-bas, la réalité ici — et les écarts connus sont
listés à la fin de `contexte-produit.md` plutôt que laissés à découvrir.

Positionnement du compte arrêté avec l'utilisateur (2026-07-22) :
compte Instagram **français NATIONAL** (toute la scène FR : Paris, Lyon, Marseille,
Bordeaux, Lille, Toulouse, Nantes, Rennes, Brest), cœur électro + festivals, avec la
**Bretagne en bonus de départage** — un angle récurrent, jamais le sujet principal.
Ne pas dériver vers un compte régional.

**La pop est un territoire ADJACENT, ni banni ni protégé** (2026-09-23, section 2.4
de `docs/contexte-produit.md`). Elle est valorisée quand elle remplit une des onze
conditions écrites dans le prompt — grosse actu internationale, phénomène culturel
massif, artiste important en France, dance-pop, electro-pop, hyperpop, K-pop
réellement importante, collaboration électro, nouveau mouvement esthétique,
phénomène générationnel, tendance influente — et suit la grille ordinaire sinon
(40-59 pour une actu moyenne). **La K-pop suit exactement la même règle** : les noms
de groupes cités dans le prompt sont des exemples, pas un passe-droit.

La règle remplacée disait « ne les déprioris JAMAIS » et **n'était pas arbitraire** :
elle existait parce que le modèle déprioris la pop tout seul, vu le poids de
l'électro partout ailleurs dans le prompt. Le resserrement a donc été MESURÉ dans
les deux sens, sur 53 articles réels re-scorés avec l'ancien et le nouveau prompt
(106 appels Haiku, 0,11 $) :

| échantillon | avant | après | delta moyen |
|---|---|---|---|
| K-pop de promotion routinière (n=21) | 17 au-dessus de 60 | 3 | −18,8 |
| zone frontière pop/électro (n=10) | — | 8 restent du même côté | −8,0 |
| témoin électro et festivals (n=22) | 21 | 21, zéro franchissement | −0,4 |

Ce gradient est le résultat voulu : le resserrement mord sur la promotion
routinière, effleure la frontière, et ne touche pas au cœur de ligne.

**Le pré-filtre local partage ce prompt** (`prefilter.py` importe `SYSTEM_PROMPT`).
Une phrase de `CONSIGNE_PREFILTRE` lui interdit de ranger la pop en « actu hors
ligne » : sans elle, le resserrement rendait possible l'enchaînement « pop ∉ cœur de
ligne → pop ∉ axes prioritaires → hors ligne → sous 45 → jamais envoyée à Claude »,
qui aurait écarté en silence des actus que Claude aurait gardées à 75.

**Aucun garde-fou déterministe** n'accompagne cette règle, et c'est délibéré : la
différence entre une actu K-pop réellement importante et un classement hebdomadaire
est sémantique. Ni le nom de la source (Soompi peut porter une vraie grosse actu) ni
un motif de titre ne la capturent sans casser des cas légitimes.

**Ce que cette règle NE fait PAS** : elle baisse des notes, elle ne garantit aucune
proportion. Le mix éditorial visé par `docs/contexte-produit.md` (~10-15 % de pop
mainstream) demanderait un ordonnanceur à quotas dans la file de publication —
il n'existe pas. Et il ne devra PAS être mis dans `relevance` : une note répond à
« cette information est-elle bonne ? », pas à « en ai-je assez publié cette semaine ? ».

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

Ligne éditoriale (prompt scoring `app/pipeline/scoring.py`) : cœur électro, pop en
**territoire ADJACENT et CONDITIONNEL** (2026-09-23, voir ci-dessous), PLUS quatre
axes transversaux valorisés — festivals à toutes les échelles
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
  vu / à traiter = tri via PATCH /articles/{id} ; statut « utilisé » posé par le robot seul)
- Ajouter une source : `uv run python -m scripts.sources.add_source --name "..." --type rss|reddit_rss|instagram --url ... [--genre ...] [--categorie artiste|media|festival|label|club|autre]`
  (Instagram : URL au format `ig:<compte>`, ex. `ig:tsugimag` — compte professionnel
  business/creator uniquement, `business_discovery` ne voit pas les profils perso)
- Socle de sources FR : `uv run python -m scripts.sources.seed_fr_sources [--apply]`
  (sans `--apply` : affiche le plan ; idempotent par URL)
- Lancer une ingestion : `uv run python -m scripts.run_ingest`
- Scorer les articles : `uv run python -m scripts.score_articles [--limit N]`
  (nécessite `ANTHROPIC_API_KEY` dans `.env` ; modèle : `SCORING_MODEL`,
  défaut `claude-haiku-4-5` — choix validé par l'utilisateur pour le coût)
- Mesurer la notation Haiku 4.5 contre 5.5 : `uv run python -m scripts.diagnostic.mesurer_modele_notation [--taille N] [--graine G] [--sortie DOSSIER] [--apply]`
  (sans `--apply` : le plan et le coût estimé, aucun appel ; avec : appelle Claude, n'écrit
  jamais en base ; arrêter pipeline et veille avant — voir `docs/reference/scoring.md`)
- Digest mail : `uv run python -m scripts.livraison.send_digest [--dry-run] [--date YYYY-MM-DD]`
  (`--dry-run` écrit l'aperçu dans /tmp/digest_preview.html sans toucher la base)
- Alertes Discord : `uv run python -m scripts.livraison.send_hot_alerts`
- Rendu des posts : nécessite Chromium Playwright en local
  (`uv run playwright install chromium`). Le pipeline cron ne génère AUCUN post —
  c'est l'agent de publication qui compose, à T-24 h ; au dashboard, on programme
  (« Programmer »), on ne génère jamais à la main.
- Run complet (ingestion + scoring + alertes) : `uv run python -m scripts.run_pipeline`
- Passage de veille rapide (les seuls comptes de `VEILLE_COMPTES`) :
  `uv run python -m scripts.run_veille` — voir « Veille rapide »
- Ce que les appels Anthropic ont consommé, par jour et par poste :
  `uv run python -m scripts.diagnostic.consommation_api [--jours N]`
  (lecture seule, sans base ni réseau ; un coût non calculable s'affiche INCONNU —
  voir « Usage Anthropic consigné » dans les contraintes)
- Pourquoi les créneaux de publication sont manqués, avec des effectifs :
  `uv run python -m scripts.diagnostic.diagnostiquer_creneaux [--jours N] [--detail]`
  (lecture seule ; croise la base et les logs pour séparer une PANNE d'une
  ABSENCE de la machine — voir « Publication automatique »)
- **Scheduler : UN SEUL ordonnanceur, launchd, TOUT EN LOCAL sur le Mac**
  (2026-08-15). Plus rien n'est planifié dans le cloud. Cinq agents, gérés par
  `uv run python -m scripts.install_agents` (sans option : affiche le plan ;
  `--apply`, `--status`, `--uninstall`) : `pipeline` (horaire), `dashboard`
  (uvicorn permanent sur le 8000), `publication` (10 min), `veille` (5 min),
  `prefiltre` (30 min, modèle local). **Désinstallés le 2026-11-12 à la
  demande de l'utilisateur, jusqu'à la mise à jour du robot** : rien ne
  tourne, console comprise. Ne les réinstaller (`--apply`) que sur sa
  demande, et repasser d'abord le mode de publication en `apercu` (il est
  resté en `reel`). Le
  Mac dort : un créneau manqué est rattrapé au réveil, donc rien n'est perdu,
  mais l'actu peut arriver avec des heures de retard. Détail, pièges (le
  renommage d'un label launchd qui DOUBLE l'ingestion, la rotation `newsyslog`,
  le conflit de port) et pourquoi GitHub Actions a été coupé :
  **`docs/reference/scheduler.md`**.
  Pas d'envoi de mail : l'utilisateur n'en veut pas, ne pas re-proposer SMTP.
- Nettoyage des URL de médias déjà en base (entités HTML) :
  `uv run python -m scripts.reparations.nettoyer_urls_medias [--apply]` (sans `--apply` :
  le plan ; idempotent — voir « URL de médias » dans les contraintes)
- Remplissage des durées de vidéos déjà en base :
  `uv run python -m scripts.reparations.remplir_durees_medias [--apply]` (sans `--apply` :
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
