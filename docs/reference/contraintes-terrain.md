# Contraintes apprises sur le terrain

**Quand lire ce fichier** : des qu'une requete reseau, une API externe, un jeton,
une URL de media, une migration ecrite a la main ou un job launchd entre dans le
tableau — et avant de conclure « ca devrait marcher ». Chaque puce est une panne
reellement rencontree : headers ASCII, 429 Reddit, quota Graph partage, duree
d'une video Instagram cachee dans son URL, URL echappees en HTML, transaction
ouverte pendant une attente, `op.f()` hors contexte de migration.

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
  chaque run et noyaient la supervision. `scripts/sources/desactiver_sources_x.py` les
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
  `scripts/reparations/nettoyer_urls_medias.py`.
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
  au vert dessus : d'ou la sentinelle `tests/socle/test_migrations.py`, qui les fait
  toutes charger sans base de donnees.
- **Usage Anthropic consigné.** Chaque appel des cinq sites (`notation`,
  `arbitrage`, `composition`, `juge_image`, `recherche_web`) ajoute une ligne à
  `usage_anthropic-AAAA-MM.jsonl`, à côté du jeton Meta
  (`app/pipeline/usage_anthropic.py`). Exception assumée à la relecture-fusion des
  états partagés : ce fichier ne fait que grandir, trois agents y écrivent, donc
  `O_APPEND` et un seul `os.write` par ligne. Une valeur absente vaut `null`, jamais
  0 ; un `ValueError`/`ValidationError` de `messages.parse()` (réponse invalide mais
  facturée) laisse une ligne à tokens `null`, une erreur d'API (non facturée) aucune.
  Tout nouvel appel à Claude doit y passer, sinon sa dépense est invisible. Lecture :
  `scripts/diagnostic/consommation_api.py`, seul endroit des tarifs ; modèle hors
  table ou tokens `null` : coût INCONNU, compté à part, exclu et signalé au total.
  Aucune mesure encore : le compte est à l'arrêt sans crédits depuis le 2026-10-31,
  le premier chiffre réel viendra avec la reprise des appels.
