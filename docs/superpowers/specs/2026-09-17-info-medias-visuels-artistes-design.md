# L'info des médias, les visuels des artistes — design

Date : 2026-09-17
État : décisions de l'utilisateur intégrées (2026-09-17), défauts proposés pour les points restés ouverts (voir « Questions ouvertes »), prêt pour le plan d'implémentation
Plan : `docs/superpowers/plans/2026-09-17-info-medias-visuels-artistes.md`
S'appuie sur : « Qui parle : `sources.categorie` » et « Dédup à deux étages » (`CLAUDE.md`), `docs/superpowers/specs/2026-09-11-publication-meta-design.md` (la file), `docs/superpowers/specs/2026-09-17-reels-automatiques-design.md` (reels, yt-dlp, crédit).
Mémoire : `visuels-medias-vs-artistes` (décision utilisateur).

## L'incident qui motive le chantier (2026-09-17)

La bande-son de GTA VI est annoncée avec Fred again.. — artiste dont Kinda suit le compte Instagram. Huit articles entrent en base (Billboard, Pitchfork ×2, Stereogum, DJ Mag, Mixmag ×2 en RSS ; Booska-P et Guettapen en Instagram). L'actu n'atteint jamais la file de publication :

1. **Le pré-filtre local** (Qwen3-4B) note Billboard 15, Pitchfork 25, Booska-P 25 : écartés sans passer par Claude.
2. **La dédup par titre** masque DJ Mag (« GTA VI full soundtrack announced featuring Fred again.. ») derrière Mixmag, et Stereogum derrière Pitchfork : le représentant est le plus ancien, pas le plus informatif.
3. **Re-notés par Claude**, les articles plafonnent entre 45 et 59 : décote des médias (règle 5, « retire 15 à 20 points ») et plafond à 59 hors `actu_majeure`. Seuil de la file : 60.

État relevé en base (lecture seule) : les huit articles sont aujourd'hui notés `claude`, entre 35 (Booska-P) et 59 (Mixmag 17462), et forment **six** groupes de doublons (17462←18080, 18077←18036, les autres seuls). Constat important : la règle « ≥ 3 sources » seule n'aurait **pas** sauvé cette actu — les titres varient trop pour former un seul groupe. Ce sont la suppression de la décote et la règle « artiste suivi cité » qui la rattrapent.

## Les décisions de l'utilisateur (2026-09-17), contraignantes

1. L'**info** peut venir des comptes d'actu et des médias : ne plus la pénaliser à la notation. Plafond média supprimé ; garde-fou « post Instagram quasi vide » (≤ 29) conservé.
2. Une actu couverte par **≥ 3 sources distinctes** est toujours notée par Claude, et le représentant du groupe est l'article **le plus informatif**, plus seulement le plus ancien ; croissance du groupe après notation gérée, coût borné.
3. Un article qui **cite un artiste dont Kinda suit le compte Instagram** n'est jamais écarté par le pré-filtre.
4. **Visuels** : les images et vidéos des comptes Instagram **médias** ne sont **jamais** utilisées — ni fond de cover, ni slide de carrousel, ni vidéo de reel. Les photos de presse des flux RSS (`og:image`, `media_content`) restent autorisées.
5. Pour un article sans visuel autorisé, chercher chez les **artistes cités** : (a) posts récents de leurs comptes suivis déjà en base ; (b) sinon lecture de leurs derniers posts par `business_discovery` à la composition, bornée ; (c) pour un reel, la vidéo du compte d'artiste (`media_url` de l'API ou yt-dlp sur le permalink, mêmes règles). Heuristique de pertinence définie.
6. **Crédit** : « 📸 @artiste » (🎥 pour un reel) pour le propriétaire du visuel ; « Source : » nomme toujours le média d'où vient l'info.
7. Sans visuel autorisé, l'article reste visible au dashboard mais **n'entre pas dans la file**, avec une raison lisible (comme `raison_ineligible_reel`).
8. Le cas GTA VI doit être **rattrapable** : un script de réévaluation (plan par défaut, `--apply`).

---

## Mesures (base de production, lectures seules, 2026-09-17)

| Mesure | Valeur |
|---|---|
| Articles ingérés par jour (15-17/09) | 178 / 184 / 323 |
| Notés par Claude par jour | 103 / 104 / 192 |
| Écartés par le pré-filtre local par jour | 61 / 55 / 105 (245 sur 7 jours) |
| Doublons liés par jour | 14 / 25 / 28 |
| Groupes de doublons sur 7 jours, par taille | 2 : 42 · 3 : 7 · 5 : 1 · 9 : 1 |
| Groupes ≥ 3 : représentant noté `local` / `claude` | 3 / 6 (aucun ≥ 60) |
| Doublons arrivés > 50 min après leur représentant (7 j) | 42 |
| Écartés `local` citant un compte d'artiste suivi (nom normalisé, 7 j) | 20 |
| Tous articles citant un artiste suivi (7 j) : `claude` / `local` | 99 (13 ≥ 60) / 20 |
| Sources Instagram actives : artiste / festival / média / label / club | 88 / 14 / 16 / 3 / 2 |
| Sources RSS média actives / Reddit | 14 / 2 |
| Articles de comptes Instagram média (7 j, non doublons) | 91 : 4 ≥ 60, 16 entre 50 et 59 |
| Articles RSS/Reddit média (7 j, non doublons) | 489 : 3 ≥ 60, 177 entre 45 et 59 |
| Articles média notés `claude` entre 40 et 59, 3 derniers jours | 226 (170 RSS, 44 Instagram, 12 Reddit) |
| Candidats de la file (≥ 60, non doublons, non exclus, 3 j) | 27, dont 8 venus de médias |
| Posts de comptes d'artistes en base | 88 (58 avec média), 31 comptes sur 88 ont posté — la source Instagram n'a que quelques heures d'historique |
| Fiches `artists` | 3 258, aucune suivie à la main |

Lecture : la décote a presque vidé la presse de la file (8 candidats média sur 27 en trois jours, alors que 226 articles média se tiennent entre 40 et 59). La supprimer va **augmenter l'offre** de la file et le nombre d'illustrations `og:image` (gratuites, plafonnées à 40 par run).

---

## Partie 1 — Notation

### 1.1 Plus de décote ni de plafond pour les médias

- `appliquer_garde_fous` ne garde que le garde-fou 1 (post Instagram quasi vide → relevance et hotness ≤ 29, emergence intacte). `PLAFOND_MEDIA` disparaît.
- **Règle 5 du `SYSTEM_PROMPT`** : la ligne `media` est réécrite. Le mot « PLAGIAT », la décote de 15-20 points et le plafond à 59 sont retirés. Nouveau sens : « Kinda reprend l'INFORMATION, jamais la formulation ni les visuels du média. Note l'information elle-même, comme si elle venait de la source primaire : aucune décote parce qu'elle arrive par un média. Une actu reprise par plusieurs médias est souvent une grosse actu. » Les lignes `artiste`, `festival / label / club` (promo courante 40-55) et `inconnu` restent. La règle 6 (posts sans information) reste.
  Justification : le plagiat portait sur les **visuels** (watermarks) et la **formulation** ; les deux sont traités ailleurs (Partie 2, et la composition réécrit toujours). Noter la même information plus bas selon le canal punissait exactement les grosses actus, que les artistes ne relaient pas toujours.
- **`actu_majeure` est supprimé** du schéma `ArticleScore` et du prompt (règle 5 et liste des sorties). Justification : son seul consommateur était le plafond ; le champ n'est **pas une colonne** (jamais persisté), donc **aucune migration** ; le retirer économise des jetons de sortie et supprime une consigne que Haiku appliquait mal. Compatibilité : Pydantic ignore les clés supplémentaires, une réponse du pré-filtre local qui porterait encore `actu_majeure` reste valide (test).
- **`CONSIGNE_PREFILTRE`** : n'a plus de décote à neutraliser. Elle garde sa raison d'être — le 4B ne doit pas écarter ce que Claude garderait : « ne descends sous 45 que pour le bruit évident, l'actu hors ligne sans axe prioritaire et les posts réellement vides ; un contenu qui porte un fait vaut 45 ou plus, quel que soit le type de compte ».
- `scripts/appliquer_garde_fous.py` : sa docstring et son message ne parlent plus du plafond média (il ne l'appliquait déjà pas).

### 1.2 Le pré-filtre n'écarte jamais certains articles

`score_pending` construit une fois par run un `ContexteScoring` (index des comptes d'artistes suivis, taille de groupe des articles à noter). Quand la note locale est **sous le seuil** (45), l'article part quand même chez Claude si :

- **R2 — groupe** : le groupe dont il est le représentant réunit au moins `cluster_sources_claude = 3` **sources distinctes** (`source_id` distincts, représentant compris — deux posts du même compte ne font pas deux sources) ;
- **R3 — artiste suivi** : il cite un artiste dont Kinda suit le compte Instagram (voir 1.3), d'après les artistes rendus par le pré-filtre, `mentioned_artists` s'il existe, et le texte du titre et du résumé.

Ordre des décisions inchangé ailleurs : un post Instagram **quasi vide** reste noté sans Claude même dans un groupe ou s'il cite un artiste (garde-fou 1 d'abord — il n'y a rien à juger). La décision d'écarter se prend toujours sur la note brute. Compteurs ajoutés à `ScoringStats` : `forces_cluster`, `forces_artiste`.

### 1.3 Qui est « un artiste suivi » : l'index des comptes

`app/pipeline/comptes_artistes.py`.

- **Clé de nom** `cle_artiste(nom)` : parenthèses retirées (« Fred again.. (IG) », « DJ Snake (FR) (IG) »), `@` initial retiré, puis `cluster.normalize_title` (minuscules, sans accents ni ponctuation, traits d'union recollés) → `fred again`, `dj snake`. Une clé de moins de 4 caractères est ignorée.
- **Entrées** : chaque source `instagram` de catégorie `artiste`, active, sous deux clés — son nom nettoyé et son handle (`compte_depuis_url`). Puis chaque fiche `artists` liée à la main ou automatiquement à une source (`artists.source_instagram_id`, migration 0018), sous la clé de son nom : c'est le moyen de corriger un nom qui ne correspond pas au compte (les liens `artists` priment sur les clés dérivées des sources).
- **Citation** `IndexComptes.cites(textes, noms)` :
  - un nom rendu par un modèle (`artists` du pré-filtre, `mentioned_artists`) cite le compte si sa clé est dans l'index ;
  - dans le texte brut (titre, résumé), une clé d'**au moins deux mots** est cherchée en mots entiers dans le texte normalisé ;
  - une clé d'**un seul mot** (« anyma », « justice ») n'est jamais cherchée dans le texte libre — collisions avec des mots courants — sauf sous la forme `@handle` ;
  - rendu : les comptes distincts, dans l'ordre des `source_id`.
- **Liaison automatique** `lier_artistes_comptes(db)` : après `record_mentions` dans `run_pipeline`, toute fiche `artists` non liée dont la clé du nom correspond à une clé de **source** (jamais à un lien manuel posé sur une autre fiche) est liée. Jamais d'écrasement d'un lien existant.
- Mesure : 20 articles écartés en 7 jours citaient un artiste suivi par ce seul critère de nom (sans le texte libre).

### 1.4 Le représentant le plus informatif

`link_duplicates` ne change pas (les liens se forment toujours contre le plus ancien — c'est ce qui garde la liaison stable). Une étape nouvelle, **`elire_representants`**, tourne juste après dans `run_pipeline` (et AVANT le scoring, pour qu'un doublon promu représentant soit noté dans le même run).

**Clé d'information** (la plus grande gagne ; ordre total, déterministe, calculée sur des champs **immuables** — le titre et la source, jamais le résumé que le scoring réécrit, sinon l'élection oscillerait) :

1. nombre de comptes d'artistes suivis cités **dans le titre** ;
2. source **primaire** (catégorie `artiste`, `festival`, `label`, `club`) ;
3. **visuels autorisés** (pas un compte Instagram média) ;
4. nombre de mots significatifs du titre (`cluster.significant_tokens`) ;
5. le plus ancien (`published_at`, à défaut `fetched_at`) ;
6. le plus petit `id`.

Sur le cas GTA, DJ Mag (« GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more » : 8 mots significatifs) passe devant Mixmag (6), à artistes suivis égaux.

**Réélection** : pour chaque groupe dont un membre est dans la fenêtre de 48 h, le meilleur membre devient représentant (`duplicate_of_id = NULL`) et tous les autres pointent vers lui — jamais de chaîne. **Verrou** : un représentant *engagé* ne change jamais — article `USED`, un `Post` existe pour lui, ou un créneau non `LIBRE` le porte. Idempotent : rejouer ne change rien (clé immuable, ordre total).

**Réévaluation après croissance** — déclencheurs et coût :

| Situation | Effet | Coût Claude |
|---|---|---|
| Nouveau représentant jamais noté (un doublon, par construction) | noté au scoring du même run ; R2 le force chez Claude si le groupe a ≥ 3 sources | ≤ 1 appel |
| Représentant (nouveau ou non) noté `local`, groupe ≥ 3 sources, titre ≠ « Post Instagram » | ses notes sont remises à NULL (`remettre_a_noter`), il repasse au scoring, R2 le force chez Claude | 1 appel, une seule fois (il sera `claude` ensuite) |
| Représentant déjà noté `claude` | rien | 0 |
| Ancien représentant rétrogradé | garde ses notes (historique), masqué comme doublon | 0 |

Borne : `cluster_reevaluations_max_par_run = 10` (réélections + remises à noter) ; un représentant ne peut changer que vers un article strictement meilleur, donc au plus une fois par membre. Estimation : 42 doublons tardifs en 7 jours (≈ 6/jour) et 3 représentants `local` de groupes ≥ 3 en 7 jours → **≤ 7 appels Claude/jour** au pire, typiquement 1 à 2.

`remettre_a_noter` efface relevance, hotness, emergence, category, imprint, `mentioned_artists` et `scored_by`, garde le résumé (la caption d'origine est déjà perdue ; Claude jugera sur le résumé local — limite assumée). Les mentions d'artistes déjà archivées ne sont pas dupliquées (`record_mentions` est idempotent par article).

### 1.5 Rattrapage : `scripts/reevaluer_actus.py`

`uv run python -m scripts.reevaluer_actus [--jours 3] [--limite 100] [--apply]`. Sans `--apply` : le plan seul, **aucune écriture** (l'élection est calculée sans être appliquée). Avec `--apply` :

1. `elire_representants` sur la fenêtre `--jours × 24 h` ;
2. `score_pending` (les représentants promus ou remis à noter, avec les règles R2/R3) ;
3. re-notation **par Claude directement** (sans pré-filtre) des articles du plan, commit par article.

Le plan retient, sur `--jours` jours, les articles non doublons, non `USED`, non exclus de la file, déjà notés, titre ≠ « Post Instagram » :
- notés `local` et (groupe ≥ 3 sources **ou** artiste suivi cité) — raison `cluster` / `artiste_suivi` ;
- notés `claude`, source `media`, relevance entre 40 et 59, **et** (artiste suivi cité **ou** relevance ≥ 50 **ou** groupe ≥ 2 sources) — raison `media_decote` : les victimes probables de l'ancienne règle, sans re-noter toute la presse (226 articles média entre 40 et 59 sur 3 jours).
Tri : artistes suivis cités, taille de groupe, relevance, id — puis `--limite`. Coût : au plus `--limite` appels Haiku, une fois.

Sur GTA VI : le groupe 17462←18080 élit DJ Mag 18080 (plus de mots significatifs, Fred again.. dans le titre des deux) ; entrent sûrement dans le plan DJ Mag 18080 (45, cite Fred again.., groupe de 2), Pitchfork 18077 (45, cite Fred Again.., groupe de 2) et Mixmag 17462 s'il restait représentant (59 ≥ 50). Pitchfork 17326, Mixmag 17733 et Billboard 18054 (45, groupe seul) n'y entrent que s'ils citent un autre compte suivi (Travis Scott, Lil Wayne… selon la liste du jour). Booska-P (35) reste hors plan : même sans décote de 20 points, il n'aurait pas atteint 60.

---

## Partie 2 — Les visuels

### 2.1 La règle : un compte Instagram média ne fournit jamais d'image

`app/content/visuels.py`, fonctions pures :

- `origine_interdite(source)` : vrai pour une source `instagram` de catégorie `media`. Les flux RSS, Reddit et les comptes Instagram d'artiste, de festival, de label ou de club restent autorisés (décision 4 : « garde les photos de presse »). Une source Instagram sans catégorie n'existe plus en production (`categoriser_sources` les a toutes classées) et reste autorisée — lire la décision à la lettre.
- `visuels_autorises(article)` : `article.media`, ou `[]` si l'origine est interdite.
- Tous les points qui lisaient `article.media` pour produire une image passent par elle : `compose._fond_par_defaut`, `store._a_une_image`, `preparation.urls_des_photos` (qui refuse aussi une slide `media` dont l'URL vient d'un média interdit), `preparation_reel` (le porteur de vidéo), et le dashboard (cases grisées).
- `credit_video(article)` rend `None` pour une origine interdite : le crédit nomme le propriétaire d'un visuel **utilisé**.
- **Ceintures** : au gel d'un carrousel, un fond de cover égal à une URL d'un média interdit lève `PreparationImpossible` ; à la composition, un brouillon antérieur à la règle (`post.assets` NULL) sur un article d'origine interdite n'est **pas repris** — un nouveau est composé avec les visuels d'artistes.

### 2.2 Les actifs : où vit le visuel choisi

Un **actif** est une entrée média de forme complète (`type`, `url`, `preview_url`, `duration_ms`, `width`, `height`) enrichie de :

| Clé | Sens |
|---|---|
| `origine` | `article` (visuel propre de l'article) ou `artiste` (post d'un compte d'artiste suivi) |
| `compte` | `@handle` du propriétaire, ou `null` (photo de presse) |
| `permalink` | le post Instagram d'où vient l'actif (sert à yt-dlp et au dashboard), sinon l'URL de l'article |
| `article_id` | l'article du post d'artiste en base (lecture en direct : `null`) |
| `publie_le` | date ISO du post d'origine |
| `fichier` | `file://…` de la copie locale (voir 2.5), ou `null` |

Stockage (migration **0018**) :
- **`posts.assets`** (JSONB, nullable, liste ordonnée — réassigner en entier) : les actifs retenus à la composition. `NULL` = post antérieur à la règle (lecture historique : `article.media` filtré). Liste vide = composition manuelle sans visuel autorisé.
- **`posts.credit`** (existe, 0017) change de sens, pas de type : les handles **distincts** des actifs dans l'ordre, séparés par une espace (`@fredagain @pinkpantheress`), ou `null`. Pour un post d'origine `article` Instagram, c'est le compte source (comme avant).
- **`artists.source_instagram_id`** (FK `sources.id`, `ON DELETE SET NULL`, index) : le lien fiche artiste ↔ compte suivi (1.3).
- Ni `articles` ni `creneaux` ne changent : l'éligibilité se calcule, le choix est figé sur le post.

### 2.3 Heuristique de pertinence d'un visuel d'artiste

`app/content/actifs_artistes.py`. Candidats : les posts des **comptes cités** (1.3, hors la source de l'article lui-même), jamais d'autres comptes.

1. **Médias utilisables** : carrousel — photo avec URL, ou vignette (`preview_url`) ; reel — vidéo exploitable (URL directe, 3 à 90 s ou durée inconnue) ou vidéo récupérable par yt-dlp (`video_recuperable`).
2. **Fenêtre dure** : post publié entre `date de l'article − visuels_artiste_fenetre_jours (10)` et maintenant. Un post plus ancien n'est **jamais** retenu.
3. **Sur le sujet** : recouvrement ≥ 1 entre les mots significatifs du titre de l'article **privés des mots du nom et du handle de l'artiste** (sinon tout post de l'artiste « parlerait » de lui) et ceux du titre + résumé du post (« gta », « soundtrack »…).
4. **Générique** : un post hors sujet n'est accepté que publié à moins de `visuels_artiste_generique_jours (5)` de la date de l'article — un portrait récent illustre honnêtement l'artiste, un post ancien sur autre chose non.
5. **Classement** : sur le sujet d'abord, recouvrement décroissant, écart de date croissant, id décroissant.

Carrousel : jusqu'à `visuels_max_photos (3)` images, dans l'ordre du classement (plusieurs par post si c'est un carrousel d'artiste) ; la première fait le fond de cover. Reel : la première vidéo du classement. Le dashboard montre l'origine de chaque actif : l'humain garde le dernier mot (il peut remplacer le fond, décocher un média, « sauter »).

### 2.4 Lecture des comptes à la composition, bornée

Le quota Graph est **partagé** avec le pipeline (~120 comptes toutes les 3 h) et a déjà rendu l'erreur code 4 pendant une heure. La lecture en direct (b) est donc rare et bornée :

- **Quand** : seulement à la composition d'un **carrousel** (J-1 dans la file, ou « Générer le post » au dashboard), seulement si (a) ne rend **aucun actif sur le sujet**, pour les comptes cités dans l'ordre. Jamais à l'arbitrage, jamais au gel, jamais pour un reel (l'éligibilité d'un reel doit se décider sans réseau ; la vidéo vient de (a)).
- **Compte déjà frais** : si la source du compte a été lue par le pipeline il y a moins de `instagram_intervalle_heures` (3 h), la base est à jour — pas d'appel.
- **Bornes** : `visuels_lectures_max_par_passage = 2` (lecteur **neuf** à chaque passage de l'agent, comme le récupérateur yt-dlp) et `visuels_lectures_max_par_jour = 24`. Cache par handle `visuels_cache_lectures_heures = 6`. État dans `visuels_lectures.json` à côté de `meta_token_path`, écrit atomiquement.
- **Quota atteint** (codes Graph 4, 17, 32, 613) : pause de `visuels_pause_quota_minutes = 60` dans le même fichier. Pause, budget épuisé ou panne réseau → `LectureDifferee`.
- **Jeton absent** (`META_FB_ACCESS_TOKEN` ou `META_IG_USER_ID`) : pas de lecture, sans erreur — comme la source.
- **Autre erreur Graph** (compte non professionnel, renommé…) : pas d'actif pour ce compte, warning sans le jeton.
- Réutilisation : `app/sources/instagram.py` expose `url_business_discovery`, `lire_compte` et `items_depuis_payload` ; l'adapter les appelle. Les posts lus ne sont **pas** insérés en base (ils déclencheraient un scoring payé).
- Estimation : ≤ 4 compositions par jour dans la file, dont une minorité sans actif en base → **quelques appels par jour**, contre ~960 lectures/jour du pipeline.

### 2.5 Le choix et la copie locale

`app/content/choix_visuels.py` : `choisir_visuels(db, article, *, format_, maintenant, lecteur=None, index=None, telecharger=...) -> ChoixVisuels(actifs, credit)`.

1. **Visuels propres autorisés** — carrousel : ceux de `visuels_autorises` qui font une image ; reel : la vidéo de l'article si le chemin reel propre est éligible (règles des reels inchangées). Actifs d'origine `article`, crédit `credit_video(article)` (compte Instagram non média, sinon `null` : photo de presse). **Pas de copie locale** (comportement actuel inchangé).
2. Sinon, **comptes cités** ; aucun → `AucunVisuel` (« visuels d'un compte média Instagram interdits, et aucun artiste suivi cité » ou « aucune image, et aucun artiste suivi cité »).
3. (a) puis, pour un carrousel sans actif sur le sujet, (b) ; `LectureDifferee` pendant (b) est retenue.
4. Carrousel : chaque image retenue est **téléchargée** dans `visuels_cache_dir` (`Application Support/Kinda/visuels`, nom = hash de l'URL) — les URL du CDN Instagram sont signées et expirent en quelques jours, or la composition précède le gel de ~22 h et le rendu du dashboard peut venir plus tard. Échec d'une image → candidate suivante.
5. Rien de retenu : si (b) a été différée → `VisuelsDifferes` (réessayé au passage suivant, rien d'exclu) ; sinon → `AucunVisuel`.

Lecture de la copie : `visuels.lire_fichier_cache(uri)` n'accepte qu'un `file://` **résolu à l'intérieur de `visuels_cache_dir`** (un `image_url` éditable au dashboard ne doit pas lire n'importe quel fichier). `render.fetch_image_data_uri` et `preparation` l'utilisent. Ménage : fichiers de plus de `visuels_cache_jours = 7` jours supprimés par l'étape `menage` du runner.

### 2.6 Reels avec la vidéo d'un artiste

- **Éligibilité** (`raison_ineligible_reel(article, index=, maintenant=)`) : le chemin propre d'abord (inchangé : vidéo, catégorie `artiste/festival/label/club`, crédit). S'il échoue, un article qui cite un artiste suivi est éligible si (a) rend une **vidéo** d'artiste ; sinon la raison du chemin propre est rendue telle quelle.
- **Gel** (`preparation_reel`) : le « porteur » de la vidéo est l'actif vidéo de `post.assets` s'il existe, sinon l'article (jamais un article d'origine interdite). Ordre inchangé : `media_url` de l'API, sinon yt-dlp **sur le permalink du post d'artiste**, repli yt-dlp sur un lien expiré selon les mêmes règles, même budget, même pause, mêmes exclusions.
- Crédit : `🎥 @handle` de l'artiste, obligatoire comme avant.

### 2.7 Légende

`texte_legende(post)` :

```
<légende>

<hashtags>

📸 @fredagain @pinkpantheress        (🎥 pour kinda_reel ; ligne absente sans crédit)

Source : Billboard · https://www.billboard.com/…
```

- Le mot « via » disparaît (décision 6 : « 📸 @artiste »).
- `Source :` nomme le média : `posts.source_name` débarrassé de ses parenthèses (« Billboard (RSS) » → « Billboard », « Booska-P (IG) » → « Booska-P »), puis l'URL. Sans nom : l'URL seule (comportement actuel).
- Crédit et source ne sont jamais coupés (règle existante).

---

## Partie 3 — La file, l'API, le dashboard

### 3.1 Éligibilité et raison lisible

`store.raison_sans_visuel(article, index=None) -> str | None`, **sans réseau** :
- `None` si l'article a une image autorisée ;
- `None` si un artiste suivi est cité (le visuel sera cherché à la composition) ;
- sinon la raison : « visuels d'un compte media Instagram interdits, et aucun artiste suivi cite » ou « aucune image, et aucun artiste suivi cite ».

Elle remplace `_a_une_image` dans `candidats_eligibles` (carrousel ; index construit une fois). Le choix manuel d'un article sur un créneau carrousel au dashboard (`PATCH /schedule/{id}`) est refusé en 409 avec cette raison, comme pour les reels.

### 3.2 Runner

Dans `_composer`, **avant** l'appel payé à Claude : `choisir(db, article, format, maintenant)`.
- `AucunVisuel` → article exclu (`file_exclu_raison` = « composition impossible : … »), créneau libéré — l'article reste visible au dashboard avec sa raison ;
- `VisuelsDifferes` → raison sur le créneau (« composition differee : … »), ni exclusion ni notification, retenté au passage suivant ;
- le post est créé avec `visuels=choix`.

`tourner(..., choisir_visuels=None)` : par défaut, choix **sans lecteur** (tests et appels existants sans réseau). `scripts/run_publication.py` passe un `LecteurComptes` neuf par passage. `POST /articles/{id}/posts` (dashboard) choisit avec un lecteur : `AucunVisuel` crée le post sans fond (l'utilisateur peut en poser un) ; `VisuelsDifferes` → 503 lisible.

### 3.3 API et dashboard

- `ArticleOut` : `visuel` (« presse », « @compte », « artistes : @a, @b ») , `raison_sans_visuel`, `visuels_interdits` ; `GET /articles` et `GET /articles/{id}` les calculent (index construit une fois par requête, sources chargées d'un seul aller).
- `PostOut` : `assets`, `credit`.
- `ArtistOut` : `source_instagram_id` ; `PATCH /artists/{id}` accepte `source_instagram_id` (source Instagram `artiste` existante, sinon 422 ; `null` délie).
- Dashboard : pastille d'origine du visuel sur chaque carte (« 📸 presse », « 📸 @fredagain », « 🚫 sans visuel » avec la raison en infobulle) ; au détail d'un post, la liste des actifs avec origine et date du post, et les médias d'un article d'origine interdite affichés grisés, non cochables (« visuel d'un compte média : interdit »).

---

## Tests (aucun réseau)

- Pure : clés de nom, citations (mots entiers, un mot, `@handle`), clé d'information, heuristique (fenêtre, sujet, générique, ordre), politique des visuels, légende.
- SQLite in-memory : élection (verrou, idempotence, chaînes), remise à noter bornée, cascade R2/R3, éligibilité, runner (exclusion, différé), API.
- Lecture (b) : lecteur injecté (`lire=`), état dans `tmp_path` ; quota, cache, budget, pause, compte frais, jeton absent.
- Téléchargement injecté ; cache dans `tmp_path` via `conftest` (`visuels_cache_dir`).
- Sentinelles : prompt (plus de « PLAGIAT », plus de 59, plus d'`actu_majeure`), dashboard (chaînes clés).
- Script de rattrapage : plan sans écriture (la base est relue identique), `--apply` avec scorer factice.

## Déploiement et coût

Ordre : migration 0018 sur Neon **avant** la fusion (le runner et le dashboard lisent `posts.assets`), fusion, redémarrage du dashboard, `reevaluer_actus` en plan puis `--apply`, observation en mode `apercu`.

Appels Claude supplémentaires estimés :

| Poste | Estimation |
|---|---|
| R3 (artiste suivi) sur les écartés | ~3/jour (20 en 7 j par le nom seul ; le texte libre peut doubler → ≤ 6) |
| R2 (groupe ≥ 3) + réévaluations | ≤ 7/jour au pire, 1-2 typique, plafond 10/run |
| Plus de décote : aucun appel en plus (mêmes articles notés) | 0 |
| Rattrapage ponctuel | ≤ 100 appels Haiku, une fois |
| Composition (Sonnet) | inchangée (même nombre de créneaux) |
| Lectures Graph (b) | quelques-unes/jour, ≤ 24 |

Soit **≤ ~13 appels Haiku/jour** de plus sur ~100-190 aujourd'hui (+7 à +10 %).

## Risques

1. **Plus d'articles au-dessus de 60** : la presse n'est plus décotée ; la file et le digest reçoivent plus de candidats. Voulu — mais les « petites » actus de médias peuvent remonter. Garde : la règle de départage (tranche la plus basse) et la règle 6 restent ; surveiller la file une semaine en `apercu`.
2. **Faux positifs d'artistes** : un nom d'un mot n'est pas cherché en texte libre ; un nom de deux mots peut encore coïncider (« black coffee »). Coût d'une erreur : un appel Claude, jamais une publication (Claude note).
3. **Visuel d'artiste hors sujet** : un portrait récent pour une actu où l'artiste est secondaire (Fred again.. cité parmi 30 noms). Mitigé par le sujet d'abord, la fenêtre de 5 jours pour le générique, l'ordre « artistes cités dans le titre », l'affichage de l'origine au dashboard et le mode `apercu`.
4. **Droits** : reposter la photo d'un artiste reste une reprise ; le crédit « 📸 @artiste » notifie le compte. Même posture que les reels (acceptée le 2026-09-17).
5. **Quota Graph** : bornes par passage et par jour, cache, pause au code 4 ; la lecture (b) ne tourne jamais à l'arbitrage.
6. **Élection et engagements** : un représentant engagé ne change jamais ; un représentant non engagé peut changer alors qu'un créneau `PROGRAMME` sans post le portait — l'arbitrage suivant le remplace (l'article devenu doublon n'est plus candidat), comportement voulu.
7. **Re-notation sur un résumé local** : un représentant remis à noter a perdu sa caption ; Claude juge sur le résumé du 4B. Moins bon qu'à l'ingestion, meilleur que l'écart.
8. **Cache disque** : 3 images par composition, purgées à 7 jours — quelques Mo.
9. **Brouillons antérieurs** : un créneau déjà **gelé** a ses fichiers figés et partirait tel quel — la mise en service liste ces créneaux et les fait sauter avant la fusion. Un créneau programmé dont le brouillon a un fond interdit est recomposé (brouillon non repris), et le gel refuse en dernier recours un fond interdit (exclusion).

## Questions ouvertes (défauts proposés, appliqués par le plan)

1. **`Source :` avec nom ET URL** ? Défaut : « Source : Billboard · https://… » (nom pour le lecteur, URL pour la traçabilité ; Instagram ne rend pas le lien cliquable). Alternative : nom seul.
2. **Seuil de groupe en sources distinctes ou en articles** ? Défaut : sources distinctes (lecture littérale de « 3 sources distinctes »).
3. **Fenêtres de l'heuristique** : 10 jours dur, 5 jours pour un visuel générique, 3 photos au plus. À ajuster après une semaine d'`apercu`.
4. **Plan de rattrapage des médias** : 40-59 filtré (artiste suivi, ≥ 50 ou groupe ≥ 2), 3 jours, 100 appels. Alternative : `--jours 3 --limite 250` pour tout re-noter (226 articles).
5. **Composition manuelle sans visuel** : autorisée (post sans fond, à compléter à la main) plutôt que refusée — la décision 7 ne vise que la file.
6. **Reels via artiste** : vidéo prise en base seulement (pas de lecture en direct pour un reel). À rouvrir si l'offre de reels reste faible.
