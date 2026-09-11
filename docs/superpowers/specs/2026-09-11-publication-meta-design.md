# Publication Meta : Kinda publie tout seul — design

Date : 2026-09-11
Chantiers : **A** (source Instagram), **B** (publication), **C** (file programmée)
État : validé avec l'utilisateur, prêt pour le plan d'implémentation

## L'objectif, dans les mots de l'utilisateur

« Des posts programmés automatiquement, une fois le matin et une fois l'après-midi,
avec une heure un peu aléatoire pour que ça fasse pas comme si c'était un bot qui
postait. » Et pour la lecture : « au lieu d'utiliser l'API de X, on utiliserait
l'API de Meta, qui est gratuite, pour récupérer les derniers posts de comptes
target ». Et pour l'arbitrage : « tous les jours, il va vérifier s'il n'y a pas une
actu plus pertinente à mettre sur ce qui est déjà programmé. S'il y a une grosse
actu à sortir le jour même, on remplace et on décale l'autre actu, ou alors on la
met jamais. »

Le dashboard change de rôle : il cesse d'être une liste d'articles à lire pour
devenir **le poste de contrôle d'une file qui tourne sans lui**.

## L'état de la machine au moment d'écrire, et pourquoi il compte

Ce chantier s'ouvre sur une installation en panne. Les chiffres sont du 2026-09-11 :

**Le scoring est arrêté depuis 41 runs** — « Your credit balance is too low to
access the Anthropic API ». **1419 articles sur 2553 ne sont pas scorés.** Or la
purge à 14 jours, elle, continue de tourner : une partie de ce stock sera jetée sans
avoir jamais été vue.

**Les 100 sources X renvoient toutes 402 Payment Required.** La facturation
developer.x.com n'est plus active. 86 sources en erreur, 85 muettes depuis plus de
sept jours. Le flux réel ne vient plus que de 15 RSS et 2 Reddit.

Ces deux faits ne sont pas du décor. **La file de programmation se nourrit
d'articles scorés** : à sec de crédits Anthropic, le chantier C produit une file
vide, quelle que soit la qualité de son code. Et le chantier A n'est pas un confort,
c'est le remplacement d'une source d'assets morte.

## Ce que l'API Meta permet vraiment

Vérifié dans la documentation Meta le 2026-09-11, pas de mémoire.

**`business_discovery` fonctionne et est gratuit.** Pour n'importe quel compte
**professionnel** (business ou creator), il rend les posts récents avec `caption`,
`media_url`, `permalink`, `timestamp`, `like_count`, `comments_count`,
`media_type`. Facebook Login obligatoire.

**Le Content Publishing API fonctionne.** 100 posts publiés par période glissante de
24 h — très au-dessus des 2/jour visés. Compte professionnel lié à une Page.

**`cover_url` existe.** Le pari écrit dans `CLAUDE.md` (« le moyen fiable viendra de
l'API Meta ») est confirmé : on peut fournir explicitement l'image de couverture
d'un reel, en 1080×1920.

### Les deux contraintes dures

**1. L'API Meta ne programme pas.** Aucun paramètre « publier à 14h37 » n'existe.
C'est notre ordonnanceur qui doit taper à l'heure. Or `CLAUDE.md` acte que le Mac
dort et que launchd rattrape au réveil — ce qui, pour une publication, produirait
deux posts lâchés d'un coup à 21h. C'est le contraire de l'effet recherché.

**2. Les médias doivent être sur une URL publique.** Meta va chercher le fichier
lui-même en cURL. `render.py` produit un PNG local, `export.py` fait un zip : rien
n'est exposé aujourd'hui, et `localhost:8000` ne peut pas l'être.

## Décisions prises avec l'utilisateur

| Question | Décision |
|---|---|
| Où tourne la publication | **Tout sur le Mac**, avec garde-fou de fraîcheur. Cohérent avec le 2026-08-15 ; prix assumé : des jours sans publication si le capot reste fermé. |
| Contenu d'un post | **Cover Kinda + assets** (photos/vidéos de l'actu). Les deux formats : le post fidélise les abonnés, le reel en recrute de nouveaux. |
| Horizon de la file | **3 jours d'avance**, 6 créneaux visibles, **gel à T-2h**. |

Le repost d'assets de tiers a été signalé à l'utilisateur (droits d'auteur, ToS
Meta) et assumé par lui, comme le font Electro News et Guettapen. La mention `@` du
compte source en légende est intégrée par défaut, comme minimum de bonne foi.

---

# Chantier A — Source Instagram

## Principe

Un adapter de plus dans la couche pluggable. Le contrat `fetch() -> list[RawItem]`
ne bouge pas, donc **rien en aval ne change** : dédup, clustering, scoring,
illustrations et mentions A&R fonctionnent tels quels.

- `app/sources/instagram.py` : `InstagramAdapter`
- `SourceType.INSTAGRAM` + entrée au registry de `sources/__init__.py`
- `source.url = "ig:<username>"` — même convention que le préfixe `ugc:` et
  `radar:` déjà en place côté X
- Config : `meta_access_token`, `meta_ig_user_id`

## Mapping vers `RawItem`

| `RawItem` | Source |
|---|---|
| `url` | `permalink` |
| `title` | première phrase de la `caption`, tronquée |
| `summary` | `caption` entière |
| `published_at` | `timestamp` |
| `media` | `[{type, url, preview_url, width, height}]` — `media_type` IMAGE/VIDEO/CAROUSEL_ALBUM |

Le champ `type` du média — celui dont `CLAUDE.md` explique qu'il rendait le format
REELS décidable — est rempli d'office ici, ce que X ne donnait qu'au prix des
`variants`.

## Deux pièges identifiés, et leur traitement

**Les URL du CDN Instagram sont signées et expirent** (quelques jours). Stocker
`media_url` à l'ingestion puis publier trois jours plus tard donnerait un 404
silencieux. → l'asset est **téléchargé localement au moment du gel** (chantier C),
jamais servi depuis l'URL d'origine. Si l'URL est morte au gel, le post est marqué
en défaut et le créneau repart à l'arbitrage.

**Un compte cible en profil personnel est invisible de l'API.** Or les artistes
émergents — précisément le deal-flow A&R que décrit `CLAUDE.md` — sont souvent en
perso. **L'ingestion Instagram complète les RSS, elle ne les remplace pas.** Écrire
ça noir sur blanc évite d'attendre d'elle une couverture qu'elle ne peut pas donner.

## Quota et état

`business_discovery` consomme le quota Graph API de l'app (de l'ordre de 200
appels/heure). Un appel par source et par run horaire reste très en dessous. L'état
`sources.state` porte le `timestamp` du dernier post vu, pour ne pas réingérer —
**réassigné en entier, jamais muté en place**, piège SQLAlchemy déjà documenté.

## Nettoyage joint

Les 100 sources X sont désactivées dans le même chantier
(`scripts/desactiver_sources_x.py`, idempotent, réversible). Sans ça chaque run
continue de produire un millier d'erreurs 402 qui noient la supervision — celle-là
même qui doit rester lisible pour signaler les vraies pannes.

---

# Chantier B — Publication

## `app/publish/instagram.py`

Deux chemins, un seul motif : créer un conteneur, attendre qu'il soit prêt, publier.

- **Carrousel** (gabarit `kinda_post`) : un conteneur enfant par slide avec
  `is_carousel_item=true`, puis un conteneur `CAROUSEL`, puis `media_publish`.
- **Reel** (gabarit `kinda_reel`) : conteneur `REELS` avec `video_url`, plus
  `cover_url` en filet.

**Le sondage de `status_code` jusqu'à `FINISHED` n'est pas optionnel.** Une vidéo
est encodée par Meta de façon asynchrone ; publier un conteneur `IN_PROGRESS`
échoue. C'est le pendant exact des trois pièges ffmpeg déjà documentés : une étape
qu'on ne voit pas tant qu'on ne la rencontre pas.

Le montage ffmpeg actuel (cover en première frame) est **conservé** : le résultat
visuel est meilleur, la cover est réellement dans la vidéo. `cover_url` est le filet
si ffmpeg devient indisponible.

## `app/publish/hosting.py`

Meta récupère les fichiers en cURL, il faut donc une URL publique.

- Route FastAPI `/public/<jeton>/<fichier>`, jeton non devinable par post
- Exposition par **Cloudflare Tunnel** (gratuit, pas de port ouvert sur la box)
- Config `public_base_url`
- **Le tunnel debout devient une condition de publication**, vérifiée par
  `diagnostiquer()` — une panne de tunnel doit crier, pas se taire

## Modèle

Sur `posts` : `scheduled_for` (indexé), `frozen_at`, `published_at`, `ig_media_id`,
`publish_error`, `pinned`. Sur `PostStatus` : `SCHEDULED`, `FAILED`.

Publier bascule l'article en `USED`, ce qui le protège de la purge — règle déjà en
place, elle prend ici tout son sens puisque plus personne ne clique.

---

# Chantier C — File et arbitrage

## `schedule/slots.py` — les créneaux

Python pur, aucune base, aucun I/O. Une date → deux créneaux.

L'heure est tirée d'une **graine dérivée de la date**. C'est la décision centrale du
module : sans elle, l'heure serait retirée à chaque run horaire et glisserait, si
bien qu'un créneau pourrait n'être jamais atteint. Avec elle, recalculer à 9h et à
10h redonne le même 14h37.

Fenêtres en config : `publish_morning_window` (08:30-11:00),
`publish_afternoon_window` (17:00-19:30).

## `schedule/queue.py` — l'arbitrage

3 jours × 2 créneaux = 6 places. À chaque run :

1. Les articles éligibles (scorés, au-dessus du seuil, non doublons, non `USED`)
   sont classés : pertinence, urgence via `hotness`, pénalité d'âge.
2. Les meilleurs prennent les créneaux les plus proches.
3. Une grosse actu qui arrive prend la place et **décale la précédente d'un cran** ;
   ce qui tombe hors des trois jours sort de la file — le « ou alors on la met
   jamais » de l'utilisateur.

Intouchables : les créneaux **gelés** et ceux que l'utilisateur a **épinglés**.

## Le coût dicte le calendrier du travail

C'est ce qui rend l'horizon à trois jours tenable :

| Moment | Ce qui se passe | Coût |
|---|---|---|
| Entrée dans la file | rien, juste une affectation | nul |
| Créneau passe à « demain » | **composition Claude** (4 variantes + légende) | payé une fois |
| **T-2h : gel** | rendu Chromium, téléchargement des assets | local, gratuit |

On ne paie jamais la composition d'un post qui se fait doubler avant demain, et
l'utilisateur relit du vrai texte la veille plutôt qu'un titre d'article. La
variante retenue par défaut est la première des quatre ; elle reste modifiable
jusqu'au gel.

## `schedule/runner.py` — le garde-fou

À chaque passage, pour tout créneau échu et non publié :

- retard ≤ `publish_max_delay_minutes` (90) → **publier**
- au-delà → **ne pas publier**, renvoyer l'article à l'arbitrage

C'est la réponse à la contrainte n°1. Sans ce garde-fou, un Mac rouvert le soir
lâcherait la journée entière d'un coup.

**Un troisième agent launchd**, `com.musicnewsradar.publish`, toutes les **10
minutes**. Il ne score rien et ne coûte rien ; il donne une précision de ±10 min au
lieu de ±60. Il appelle `attendre_base()` en premier, comme tout point d'entrée
planifié.

## Dashboard et API

Section « Programmation » : 6 créneaux, chacun avec heure, article, statut
(libre / programmé / gelé / publié / échec), et les actions **épingler, remplacer,
décaler, sauter**.

`GET /schedule`, `PATCH /schedule/{id}`, `POST /schedule/rebuild`.

---

# Ce qui dépend de l'utilisateur, pas du code

1. **App Review Meta.** `instagram_business_content_publish` et `business_discovery`
   exigent une revue pour une app en production. En mode dev, tout fonctionne sur
   ses propres comptes — assez pour construire et tester l'intégralité — mais la
   mise en service réelle passe par un formulaire que lui seul peut remplir.
2. **Le token long-lived expire à 60 jours.** Rafraîchissement automatique prévu,
   **plus une alerte de supervision à J-7**. Sans elle, la publication s'arrêterait
   en silence exactement comme l'agent digest pendant cinq semaines.
3. **Les crédits Anthropic.** Sans scoring, pas de file.
4. **Cloudflare Tunnel** à installer une fois sur le Mac.

# Stratégie de test

`slots.py` et `queue.py` sont du Python pur sans I/O — l'essentiel de la logique y
est concentré exprès, et s'y teste à fond : déterminisme du tirage sur une même
date, bornes des fenêtres, cascade de substitution, inviolabilité d'un créneau gelé
ou épinglé, sortie de file au-delà de trois jours.

L'adapter Instagram se teste sur des fixtures JSON de réponse `business_discovery`,
y compris le cas du compte non professionnel. La publication se teste sur un faux
client HTTP, avec le scénario `IN_PROGRESS` puis `FINISHED` — celui qui casse en
production si on ne le sonde pas.

Le garde-fou de retard se teste par injection d'horloge : publié à +20 min, refusé
et réarbitré à +120 min.

# Ordre de livraison

**A** est livrable seul et a une valeur immédiate : il remplace une source d'assets
morte. **B** est inutile sans hébergement public. **C** dépend des deux.
