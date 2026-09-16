# Publication Meta : Kinda publie tout seul — design

Date : 2026-09-11
Chantiers : **A** (source Instagram), **B** (publication), **C** (file programmée)
État : validé avec l'utilisateur, prêt pour le plan d'implémentation
Révisé : **2026-09-16 — connexion Instagram seule, sans Facebook** (voir ci-dessous)

## Révision du 2026-09-16 : connexion Instagram, pas de Facebook

La première version supposait la **connexion Facebook** (compte Facebook, Page reliée
au compte Kinda, jeton `EAA…`, `graph.facebook.com`). Elle est tombée à l'exécution :
la création du compte Facebook échoue sur « Une erreur s'est produite lors de
l'inscription », message générique de Meta, et l'utilisateur a choisi de ne pas
s'acharner.

Meta propose une seconde variante, **l'API Instagram avec connexion Instagram** : ni
compte ni Page Facebook, jeton `IG…`, hôte `graph.instagram.com`. Elle est en place :

| Élément | Valeur |
|---|---|
| App Meta | « Kinda Radar », cas d'usage « Gérer les messages et les contenus sur Instagram », sans portefeuille business |
| Compte | `kindanewsfrance`, type BUSINESS, déclaré testeur Instagram de l'app |
| `.env` | `META_ACCESS_TOKEN` (jeton `IG`, ~60 jours) et `META_IG_USER_ID=17841441705212514` |

**Vérifié en direct le 2026-09-16, en lecture seule** : `GET /me` rend le bon compte et
un `user_id` identique au `.env` ; `GET /{ig-user-id}/content_publishing_limit` rend
un quota de **100 publications / 24 h, 0 utilisée** — donc le droit de publier est
actif. Rien n'a été publié pendant la vérification.

Ce que la variante change, chantier par chantier :

- **A — la source Instagram est ABANDONNÉE.** `business_discovery` (lire les posts
  d'AUTRES comptes) n'existe qu'avec la connexion Facebook. Seul survit le nettoyage
  des sources X, indépendant de Meta.
- **B — publication : inchangée sur le fond.** Mêmes conteneurs, même sondage de
  `status_code`, mais sur `graph.instagram.com` avec le jeton `IG`. Le rafraîchissement
  du jeton passe par `GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token`
  — possible seulement pour un jeton âgé d'au moins 24 h et non expiré, d'où l'alerte
  à J-7 qui devient indispensable.
- **C — file et arbitrage : inchangé.**

**Conséquence directe pour les reels.** `CLAUDE.md` acte que les MP4 ne viennent que
de X. X est à sec (402) et la source Instagram ne se fera pas : **aucune nouvelle
vidéo n'entre plus dans la base**, et au 2026-09-16 il ne reste aucun article
pertinent avec vidéo. Tant que rien ne change, la file ne produira que des **posts
carrousel** ; le chemin reel du chantier B reste à coder, mais n'aura pas de matière.
Deux issues, à trancher plus tard par l'utilisateur : réactiver la facturation X pour
les seules sources vidéo (UGC et radar), ou accepter une file sans reels.

---

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
`media_type`. **Facebook Login obligatoire — d'où l'abandon du chantier A en révision
du 2026-09-16.**

**Le Content Publishing API fonctionne.** 100 posts publiés par période glissante de
24 h — très au-dessus des 2/jour visés. Avec la connexion Instagram, un compte
professionnel suffit : pas de Page Facebook (quota vérifié en direct le 2026-09-16).

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

# Chantier A — Source Instagram (ABANDONNÉE le 2026-09-16)

`business_discovery` exige la connexion Facebook, que l'utilisateur n'a pas pu mettre
en place. L'adapter, le type `instagram`, la migration et les comptes cibles ne sont
donc pas construits. La conception d'origine reste lisible dans l'historique git
(commit `8a4cddb`) si la connexion Facebook redevient possible un jour.

Ce qui en reste :

## Nettoyage joint

Les sources X — **85 encore actives au 2026-09-16**, 15 déjà coupées — sont désactivées
(`scripts/desactiver_sources_x.py`, idempotent, réversible). Sans ça chaque run
continue de produire un millier d'erreurs 402 qui noient la supervision — celle-là
même qui doit rester lisible pour signaler les vraies pannes.

---

# Chantier B — Publication

## `app/publish/instagram.py`

Deux chemins, un seul motif : créer un conteneur, attendre qu'il soit prêt, publier.
Tous les appels partent de `https://graph.instagram.com/{META_IG_USER_ID}` avec le
jeton `IG` (révision du 2026-09-16).

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

1. **Contrôle app Meta.** `instagram_business_content_publish` exige une revue pour
   une app en production. Tant qu'elle n'est pas faite, l'app ne publie que sur ses
   comptes **testeurs** — or `kindanewsfrance` en est un, ce qui suffit à Kinda. La
   revue ne devient nécessaire que pour publier sur un compte qui n'est pas testeur.
2. **Le jeton `IG` expire à 60 jours** (généré le 2026-09-16). Rafraîchissement
   automatique prévu via `refresh_access_token`,
   **plus une alerte de supervision à J-7**. Sans elle, la publication s'arrêterait
   en silence exactement comme l'agent digest pendant cinq semaines.
3. **Les crédits Anthropic.** Sans scoring, pas de file.
4. **Cloudflare Tunnel** à installer une fois sur le Mac.

# Stratégie de test

`slots.py` et `queue.py` sont du Python pur sans I/O — l'essentiel de la logique y
est concentré exprès, et s'y teste à fond : déterminisme du tirage sur une même
date, bornes des fenêtres, cascade de substitution, inviolabilité d'un créneau gelé
ou épinglé, sortie de file au-delà de trois jours.

La publication se teste sur un faux
client HTTP, avec le scénario `IN_PROGRESS` puis `FINISHED` — celui qui casse en
production si on ne le sonde pas.

Le garde-fou de retard se teste par injection d'horloge : publié à +20 min, refusé
et réarbitré à +120 min.

# Ordre de livraison

**A**, réduit au nettoyage des sources X, est livrable seul et rend la supervision
lisible. **B** est inutile sans hébergement public. **C** dépend de **B**.
