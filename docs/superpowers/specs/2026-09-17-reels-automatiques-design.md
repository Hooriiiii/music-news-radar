# Reels automatiques : deux reels par jour en plus des carrousels — design

Date : 2026-09-17 (révisé le même jour : yt-dlp)
État : décisions de l'utilisateur intégrées, **question d'approvisionnement tranchée** (yt-dlp, voir « Révision du 2026-09-17 — yt-dlp »), prêt pour le plan d'implémentation
Plan : `docs/superpowers/plans/2026-09-17-reels-automatiques.md`
S'appuie sur : `docs/superpowers/specs/2026-09-11-publication-meta-design.md` (révision du 2026-09-16) — la file, les trois modes, le gel, la réclamation atomique, tout ce qui suit en hérite.

## L'objectif

Décisions de l'utilisateur du 2026-09-17 :

- des reels automatiques **en plus** des carrousels : **2 carrousels/jour (inchangé) + 2 reels/jour** ;
- contenu : les articles les mieux notés qui portent une **vidéo** — aujourd'hui presque uniquement des posts Instagram de comptes d'artistes et de festivals ;
- **même modèle de sécurité** que les carrousels : composition à J-1, gel à T-2h (pour un reel, le gel OBTIENT la vidéo, ASSEMBLE le MP4 avec `app/content/video.py::assembler_reel` et le SONDE avec ffprobe), 90 minutes de retard au plus, modes `arret`/`apercu`/`reel` partagés, réclamation atomique avant Meta, exclusions, notifications, dashboard qui édite pendant un passage ;
- publication par l'API Instagram avec **connexion Instagram** (`graph.instagram.com`, même jeton que les carrousels) ;
- **crédit obligatoire** en légende (`@compte` source + « via ») ;
- la vidéo des reels d'artistes est récupérée par **yt-dlp** au moment du gel (révision ci-dessous).

---

## Révision du 2026-09-17 — yt-dlp

### Autorisation

Tapée par l'utilisateur dans la conversation principale, le 2026-09-17 :

> « je confirme l'intégration de yt-dlp pour les reels automatiques »

Ce qu'il avait vu et approuvé avant de confirmer : yt-dlp tourne **en local seulement**, **au gel seulement**, **jamais avec connexion ni cookies**, **à faible cadence** ; risques acceptés en connaissance de cause — **CGU d'Instagram**, **fragilité**, **détection de droits à la republication**, **limitation possible de l'IP** ; **démarrage en mode `apercu`**.

### Faits mesurés

- `business_discovery` omet `media_url` pour la plupart des reels d'artistes (musique protégée ; documenté par Meta, voir « L'approvisionnement en vidéos »).
- **Essai en direct du 2026-09-17** : yt-dlp **2026.08.19** (lancé par `uvx`) a téléchargé **3 reels publics d'artistes sur 3, sans connexion** — David Guetta, Anyma, Charlotte de Witte : **1080×1920**, vidéo **VP9** + audio **AAC**, **15,7 / 30,5 / 34,7 s**, **2 à 11 Mo**.
- La version **Homebrew 2025.12.08** échouait sur **« login required »** : la version compte, d'où une dépendance épinglée et une procédure de mise à jour.

### Décisions (tranchées)

1. **Ordre d'approvisionnement** : (a) la `media_url` de l'API quand elle est présente ; (b) sinon **yt-dlp sur le permalink**, **uniquement au gel**. Précision de conception : une `media_url` qui répond 4xx (lien CDN signé expiré entre l'ingestion et le gel) retombe sur (b) quand le permalink est Instagram — dans l'esprit de la règle, un reel récupérable ne doit pas être exclu pour un lien périmé.
2. **Dépendance uv épinglée** `yt-dlp>=2026.8.19` (verrouillée dans `uv.lock`), invoquée par **`sys.executable -m yt_dlp`** — jamais `uvx … @latest`, jamais le binaire Homebrew. Arguments **toujours** présents : `--ignore-config --no-cookies --no-cookies-from-browser` ; **aucune** option de cookies ou de connexion, sous aucune forme (test sentinelle). Sortie dans le **dossier de gel du créneau**, format « meilleur MP4, sinon fusion en MP4 » (`-f "b[ext=mp4]/bv*+ba/b" --merge-output-format mp4`), chemin de ffmpeg passé par `--ffmpeg-location` (le PATH de launchd n'a pas Homebrew). Le fichier source est effacé du dossier dès qu'il est lu : ce dossier sera servi par le tunnel.
3. **Cadence** : **au plus UN téléchargement yt-dlp par passage** de l'agent de publication ; après une panne, **pause de 60 minutes** ; état dans **`ytdlp_etat.json`**, à côté des autres fichiers d'état de Kinda (`settings.meta_token_path.parent`, comme `publication_panne.json`).
4. **Classement des erreurs** :
   - message explicite « indisponible / supprimée / privée » → **faute de l'article**, exclu (pas de pause : l'outil fonctionne) ;
   - tout le reste (connexion exigée, 429, limitation, erreur inconnue, délai dépassé, aucun fichier produit) → **panne systémique** : notification (message contenant `uv lock --upgrade-package yt-dlp && uv sync`) et pause ;
   - pendant la pause, ou budget du passage épuisé → **attente** : une raison inscrite sur le créneau, **sans notification répétée** ni exclusion.
   Le motif « connexion / limitation » est testé **avant** « indisponible » : le message de session d'Instagram contient aussi « not available ». Dans le doute, la pause : exclure un article à tort est définitif.
5. **Ingestion** : une vidéo sans `media_url` est gardée comme **`entree("video", None, preview_url=thumbnail_url)`** — et non plus comme une entrée `photo` (version précédente de ce design) — pour que le reel reste **identifiable**. Vérifié dans le code : la cover (`compose._fond_par_defaut`), la file carrousel (`store._a_une_image`) et le dashboard (vignette, case « pas d'URL directe » grisée) lisent déjà `preview_url`. `videos_exploitables` / `raison_pas_de_reel` sont inchangées : sans URL, pas de téléchargement direct (le switch REELS **manuel** reste grisé, l'export du dashboard ne lance pas yt-dlp). Une fonction distincte, **`video_recuperable(media, permalink)`**, dit si un reel **automatique** peut la chercher.
6. **Éligibilité reel** : source de type `instagram` ; catégorie dans `artiste,festival,label,club` (réglable) ; une vidéo — URL directe, **ou** entrée vidéo sans URL / permalink `/reel/` récupérable par yt-dlp ; plus l'exigence existante de crédit.
7. **Coupe-circuits** : **`reels_actifs`** dans `publication.json`, avec `GET/PUT /publication/reels` et un interrupteur « Reels auto » au dashboard (coupe tous les reels) ; **`PUBLICATION_REEL_YTDLP`** dans `.env`, défaut `true` (coupe yt-dlp seul).
8. **VP9 → H.264/AAC** : `assembler_reel` réencode déjà entièrement (graphe `-filter_complex`, `libx264`, `aac` — vérifié à la lecture) ; un test ffmpeg réel le prouve sur une source VP9, **sauté si ffmpeg n'a pas `libvpx-vp9`** ; le contrôle de sortie refuse de toute façon un codec vidéo autre que H.264/HEVC.
9. **Conservés** : défauts des anciennes questions ouvertes (catégories `artiste,festival,label,club`, fenêtres `12:00-14:00,20:30-22:30`, refus au-delà de 90 s, `share_to_feed = true`, démarrage en `apercu`), crédit obligatoire « 🎥 via @compte », section « Risques » honnête.

---

## L'approvisionnement en vidéos

### Ce que dit la documentation Meta

Vérifié le 2026-09-17 sur
<https://developers.facebook.com/docs/instagram-platform/reference/instagram-media> :

> `media_url` — « The URL for the media. The `media_url` field is omitted from responses if the media contains copyrighted material or has been flagged for a copyright violation. »
>
> `thumbnail_url` — « Media thumbnail URL. Only available on `VIDEO` media. »

### Ce qui a été mesuré

- Appel `business_discovery` sur `davidguetta` : **10 reels sur 10** rendent `media_type=VIDEO`, `media_product_type=REELS`, **sans `media_url`**, avec seulement `thumbnail_url`. Les carrousels, eux, rendent leur `media_url`.
- En base : **59 reels ingérés, 39 sans aucune vidéo** (LE SSERAFIM 11, DJ Mag 6, Booska-P 5, ENHYPEN 5, IVE 3, David Guetta 2, Charlotte de Witte 2, Anyma 2…). Les reels qui portent un `media_url` viennent **surtout de comptes médias** (Billboard, par exemple).
- yt-dlp 2026.08.19 : 3 reels d'artistes sur 3 récupérés sans connexion (voir la révision).

Un reel d'artiste porte presque toujours de la musique, donc du « copyrighted material », donc pas de `media_url` : **les reels d'artistes — précisément le contenu que la ligne éditoriale privilégie — ne sont pas téléchargeables par l'API.** Sans autre source, l'offre estimée était de **zéro à un reel éligible par jour**, nettement sous les 2/jour visés.

### Les options

| Option | Offre | Droits / ligne éditoriale | Coût |
|---|---|---|---|
| A. Accepter les vidéos de comptes médias, avec crédit | la plus grande des options API | **Contraire à « Qui parle »** (reposter un média = plagiat) ; double reprise | nul (réglage `publication_reel_categories`) — non retenue |
| B. Vidéos enfants de carrousels d'artistes/festivals | faible | acceptable | nul : **incluse** (aplatissement de `_media`) |
| C. Reel fabriqué à partir de photos (Ken Burns) | toujours disponible | pas de musique protégée | un chantier de plus — **en réserve** |
| D. Reels manuels (export zip) | ce que l'utilisateur trouve | au cas par cas | reste disponible |
| **E. yt-dlp sur le permalink, au gel** | **les reels d'artistes eux-mêmes** (3/3 à l'essai) | mêmes droits que la vidéo reprise ; **CGU Instagram** en plus | dépendance, cadence bornée, maintenance de version — **retenue** |

### Ce qui est construit

1. Le pipeline complet, avec la restriction de catégorie par défaut, **B** inclus et **E** en approvisionnement de second rang.
2. Un **compteur d'offre** (`GET /schedule/offre-reels`, affiché au panneau Programmation) qui distingue les vidéos **directes** (API) du reste (yt-dlp) : il mesure la dépendance à yt-dlp.
3. Si yt-dlp devient inutilisable durablement, **C** reste le chantier suivant : `preparer_reel` isole l'obtention des octets vidéo du reste (cover, assemblage, sondage, specs, hébergement, publication).

### L'ingestion

`app/sources/instagram.py::_media` jetait une entrée `VIDEO` sans `media_url` **entièrement**, vignette comprise : 39 reels sont arrivés sans aucun média. Correction : une vidéo sans `media_url` devient `entree("video", None, preview_url=thumbnail_url)`. Ces posts redeviennent illustrés (fond de cover, vignette au dashboard, candidats carrousel via `preview_url`) et **identifiables comme reels**. Les articles déjà ingérés ne sont pas repris (dédup, purge à 14 jours) — sauf qu'un reel déjà en base au permalink `/reel/` est éligible par yt-dlp.

`video_recuperable(media, permalink)` est vraie quand le permalink est sur `instagram.com` **et** que c'est un permalink `/reel/` (ou `/reels/`), **ou** que le seul média du post est une vidéo sans URL. Un carrousel (`/p/` à plusieurs médias) est écarté : yt-dlp y rendrait une liste sans dire quel élément est la vidéo. yt-dlp n'est jamais lancé hors d'`instagram.com` (hôte vérifié, pas une sous-chaîne).

---

## Contraintes Meta pour les reels, vérifiées le 2026-09-17

Pages consultées :

- Publication (connexion Instagram) : <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing>
- Référence du nœud `/{ig-user-id}/media` (specs vidéo, paramètres) : <https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media>
- Référence du conteneur : <https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-container>
- Référence du média (`media_url`) : <https://developers.facebook.com/docs/instagram-platform/reference/instagram-media>

| Sujet | Contrainte citée |
|---|---|
| Hôte | `graph.instagram.com` avec la connexion Instagram (« If using Instagram Login, the host URL is `graph.instagram.com` ») ; portée `instagram_business_content_publish` |
| Conteneur | `media_type=REELS` + `video_url` ; paramètres acceptés : `video_url`, `cover_url`, `thumb_offset`, `share_to_feed`, `caption` |
| `video_url` | « We cURL the video using the passed-in URL, so it must be on a public server. » |
| `cover_url` | « The path to an image to use as the cover image for the Reels tab. We will cURL the image… must be on a public server. » ; **si `cover_url` et `thumb_offset` sont fournis tous les deux, `cover_url` gagne** |
| `thumb_offset` | position en **millisecondes** de la frame servant de vignette |
| `share_to_feed` | « When `true`, indicates that the reel can appear in both the Feed and Reels tabs. » |
| Conteneur vidéo | **MOV ou MP4** (MPEG-4 Part 14), **sans edit lists**, **atome `moov` en tête** |
| Audio | **AAC**, **48 kHz maximum**, mono ou stéréo, **128 kbps** |
| Vidéo | **HEVC ou H.264**, balayage progressif, **GOP fermé**, **4:2:0** |
| Cadence | **23 à 60 i/s** |
| Dimensions | **1920 colonnes maximum** ; ratio accepté de 0,01:1 à 10:1, **9:16 recommandé** |
| Débit vidéo | VBR, **25 Mbps maximum** |
| Durée | **3 secondes minimum, 15 minutes maximum** |
| Poids | **300 Mo maximum** |
| Image de cover | **JPEG**, **8 Mo maximum**, sRGB ; Meta prend le **rectangle 9:16 central** pour l'onglet Reels et le **carré central** pour le fil |
| Statuts | `EXPIRED` (non publié en 24 h), `ERROR`, `FINISHED`, `IN_PROGRESS`, `PUBLISHED` |
| Sondage | « We recommend querying a container's status once per minute, for no more than 5 minutes. » |
| Quota | « 100 API-published posts within a 24-hour moving period » (la même page mentionne aussi 50 dans ses « Limitations » ; le quota lu en direct le 2026-09-16 était de 100). 4 publications/jour restent très en dessous dans les deux cas. |
| Récupération par Meta | aucune limite de temps de téléchargement documentée |

Ce que ça implique pour Kinda :

- `assembler_reel` sort déjà du H.264/AAC 1080×1920 à 30 i/s avec `+faststart` (moov en tête), en **réencodant tout** : une source VP9 (yt-dlp) en sort conforme. **Deux écarts à corriger** : le muxer MP4 de ffmpeg écrit une **edit list** par défaut (décalage d'amorçage AAC) → ajouter `-use_editlist 0` ; le GOP fermé est le défaut de libx264 mais n'est pas garanti explicitement → ajouter `-flags +cgop`. Un test ffmpeg vérifie l'absence d'atome `elst` dans le `moov`.
- **90 s maximum** reste une borne ÉDITORIALE de Kinda, pas une limite Meta. Réglable (`publication_reel_duree_max_s`). Une vidéo trop longue est **refusée, pas coupée**. La durée d'une vidéo yt-dlp n'est connue qu'après téléchargement : le contrôle se fait au sondage de la source (les reels mesurés font 15 à 35 s).
- La cover `kinda_reel` fait 1080×1920 : exactement le 9:16 que Meta garde. Elle part en JPEG **sans recadrage**.
- **Sondage d'un reel : toutes les 15 s, 10 minutes au plus.** Écart assumé : l'encodage d'une vidéo prend plus longtemps qu'une image, et à ce stade le créneau est déjà RÉCLAMÉ. 40 lectures au pire.
- **Pas de limite de poids pour le tunnel** côté téléchargement par Meta. Kinda borne le reel assemblé à **100 Mo** (`publication_reel_max_mo`) et la source à **100 Mo** (`--max-filesize` pour yt-dlp, plafond en lecture pour l'API).

---

## Décisions de design

### 1. Les créneaux portent un format

**Colonne `creneaux.format`** (`carrousel` | `reel`, VARCHAR + CHECK, défaut `carrousel`, migration **0017**). Les créneaux existants deviennent des carrousels sans backfill.

Pourquoi une colonne et pas une table : tout le cycle (LIBRE → PROGRAMME → GELE → PUBLIE/APERCU/ECHEC/MANQUE), la réclamation atomique, le panneau et l'API sont identiques ; seules trois étapes changent d'outil (gabarit composé, préparateur, publieur).

**Fenêtres séparées** pour les reels : `publication_reel_fenetres = "12:00-14:00,20:30-22:30"` (Paris). Deux fenêtres = deux reels/jour. Midi et soirée, hors des fenêtres carrousel. **Horizon : `publication_creneaux_reel = 6`** (3 jours × 2) ; **0 désactive les reels** sans toucher au reste. Le coupe-circuit du dashboard (`reels_actifs` à `false`) produit le même effet sur les créneaux à venir.

**`publier_a` reste UNIQUE globalement** (index de 0014 inchangé). Pour qu'il ne casse jamais, `app/schedule/slots.py` **refuse des fenêtres qui se chevauchent** (`FenetresInvalides`).

**Graine.** Les carrousels gardent la graine actuelle (`kinda-{date}-{rang}`) ; les reels ont la leur (`kinda-reel-{date}-{rang}`).

### 2. L'arbitrage par format, sans double emploi

**Un article n'occupe qu'un créneau, tous formats confondus.** Les reels sont arbitrés **d'abord**, les carrousels ensuite sur ce qui reste (la matière vidéo est rare, la matière photo abondante).

Mécanique dans `appliquer_arbitrage` (store) : pour chaque format, dans l'ordre `reel`, `carrousel` : synchroniser ses créneaux, lister ses candidats, retirer les articles **tenus ailleurs** (créneau passé encore `PROGRAMME`/`GELE`, créneau futur épinglé ou verrouillé de l'autre format, ou placé par le format précédent dans ce même passage), arbitrer avec `queue.arbitrer` **inchangé**, écrire.

`candidats_eligibles` continue d'écarter tout article déjà passé en `APERCU` ou `PUBLIE` **quel que soit le format**.

**Éligibilité reel** (`raison_ineligible_reel(article) -> str | None`, partagée par l'arbitrage, la composition et l'API), dans cet ordre :

- une vidéo : `source_video_reel(article)` rend `"api"` (vidéo à URL directe, `videos_exploitables`, durée ≤ 90 s ou inconnue) ou `"ytdlp"` (si `PUBLICATION_REEL_YTDLP`, source de type `instagram` et `video_recuperable`) ;
- source de catégorie autorisée (`publication_reel_categories`, défaut `artiste,festival,label,club`) ;
- **crédit calculable** : la source est un compte Instagram (`ig:<compte>`).

En plus des filtres communs (pertinence ≥ 60, 3 jours, pas doublon, pas USED, pas exclu). La requête des candidats reel lit 300 articles au lieu de 60 avant le filtre Python.

L'avantage « déjà composé » (`MARGE_TITULAIRE`) ne compte qu'un post **du gabarit du format**.

### 3. Composition

`_composer` passe `template="kinda_reel"` à `create_post` pour un créneau reel ; `post_a_reprendre` filtre sur le gabarit. **Aucun coût supplémentaire** (le brouillon porte déjà `title`/`subtitle`). Avant de composer un reel, l'article est revérifié : devenu inéligible (catégorie changée, yt-dlp coupé), le créneau est **libéré sans exclure** l'article.

### 4. Crédit et légende

**Colonne `posts.credit`** (`String(200)`, nullable, migration 0017), posée par `create_post` : `@compte` quand la source est Instagram, sinon `None`. Dénormalisée comme `source_name`.

`texte_legende` ajoute **`🎥 via @compte`** (reel) ou **`📸 via @compte`** (carrousel) avant `Source : <url>`, **jamais coupé**. Crédit **obligatoire pour un reel** à trois endroits : éligibilité, composition (via l'éligibilité), gel.

### 5. Le gel d'un reel : `app/publish/preparation_reel.py`

À T-2h, `preparer_reel(post, article, dossier, *, recuperer_ytdlp=...)` :

1. vérifie le gabarit (`kinda_reel`) et le crédit ;
2. détermine la **source de la vidéo** (`source_de_la_video`) : slide `media` vidéo du post, sinon vidéo à URL directe de l'article (`api`), sinon permalink récupérable (`ytdlp`), sinon faute de l'article ;
3. **vérifie ffmpeg ET ffprobe** (`video.exiger_outils()`) — absent = **panne systémique** ;
4. rend la cover `kinda_reel` (Chromium) ; débordement → exclusion. **Avant** la vidéo : une cover fautive ne consomme pas le téléchargement yt-dlp du passage ;
5. **obtient la vidéo** :
   - `api` : `telecharger_video` (plafond 100 Mo) — HTTP 4xx (`LienExpire`) → **repli yt-dlp** si le permalink est Instagram et yt-dlp disponible, sinon exclusion ; trop lourde, pas une vidéo → exclusion ; réseau ou 5xx → panne systémique ;
   - `ytdlp` : `RecuperateurYtdlp` (voir § 6) — indisponible/supprimée/privée → exclusion ; pause ou budget → attente (`PreparationDifferee`) ; panne → `YtdlpIndisponible`, systémique. Si yt-dlp a été coupé dans `.env` après la programmation → attente, sans exclusion ;
6. **sonde la source** : durée < 3 s ou > 90 s, fichier illisible → exclusion ;
7. **assemble** (`assembler_reel`, réencodage complet) ; échec ou délai dépassé → exclusion ;
8. **sonde la sortie** et la confronte aux specs Meta (1080×1920, H.264/HEVC, AAC ≤ 48 kHz, 23-60 i/s, durée ≥ 3 s, ≤ 100 Mo) → hors specs = exclusion ;
9. convertit la cover en JPEG ≤ 8 Mo **sans recadrage** ;
10. écrit `<prefixe>_reel.mp4` puis `<prefixe>_cover.jpg` (préfixe aléatoire) et rend ces deux noms **dans cet ordre**.

**Pourquoi au gel et pas avant** : les `media_url` du CDN Instagram sont signées et expirent en quelques jours ; et yt-dlp ne doit tourner qu'au dernier moment, une fois l'article réellement choisi — jamais sur tout le flux ingéré.

**Où vit le MP4** : `settings.publication_dir / <creneau.id>/`. La source yt-dlp y est écrite (`source-ytdlp.<ext>`) puis **effacée dès sa lecture** ; les fichiers intermédiaires de l'assemblage vivent dans le `TemporaryDirectory` de `video.py`.

**Cover : `cover_url` explicite**, pas `thumb_offset` : elle garantit la vignette de l'onglet Reels même si Instagram réencode et perd la frame de cover.

### 6. yt-dlp : `app/publish/ytdlp.py`

`RecuperateurYtdlp` est **créé neuf à chaque passage** de l'agent (`scripts/run_publication.py::outils_reel`) et injecté dans `preparer_reel` : c'est lui qui porte le **budget d'un téléchargement par passage**. Son exécuteur de sous-processus est injectable : aucun test ne lance yt-dlp.

Commande, toujours la même forme :

```
<sys.executable> -m yt_dlp --ignore-config --no-cookies --no-cookies-from-browser
  --no-playlist --no-progress --no-warnings --retries 1 --socket-timeout 30
  -f "b[ext=mp4]/bv*+ba/b" --merge-output-format mp4 --max-filesize <octets>
  --ffmpeg-location <ffmpeg> --print after_move:filepath --no-simulate
  -o <publication_dir>/<creneau.id>/source-ytdlp.%(ext)s -- <permalink>
```

Délai du sous-processus : 300 s. Déroulé d'un appel :

1. lien hors `instagram.com` → faute de l'article, yt-dlp non lancé, budget intact ;
2. `ytdlp_etat.json` indique une pause en cours → `YtdlpEnAttente` (raison : heure de reprise et dernière erreur), yt-dlp non lancé, budget intact ;
3. budget du passage épuisé → `YtdlpEnAttente` ;
4. lancement ; code non nul → classement (§ Révision, décision 4) : indisponible → `PreparationImpossible` ; sinon pause de 60 min écrite (`pause_jusqu_a`, `derniere_erreur`, `echec_le`) et `YtdlpIndisponible` dont le message contient `uv lock --upgrade-package yt-dlp && uv sync` ;
5. aucun fichier produit → « trop lourde » si yt-dlp l'a dit (`--max-filesize`), sinon panne systémique ;
6. lecture, effacement de la source, contrôle du plafond, `dernier_telechargement` écrit et pause levée.

L'état est écrit atomiquement (fichier temporaire + `os.replace`) ; un fichier illisible vaut « pas d'état », jamais une exception.

**Coupe-circuits.** `reels_actifs` (dans `publication.json`, écrit par `PUT /publication/reels` ; `enregistrer_mode` et `enregistrer_reels` préservent chacun la clé de l'autre) : à `false`, `nombre_de_creneaux(REEL)` vaut 0 (les créneaux reel à venir non épinglés disparaissent) et l'agent ne passe aucun outil reel au runner (un reel déjà gelé ne part plus et finit en `MANQUE`). `PUBLICATION_REEL_YTDLP=false` : seules les vidéos directes restent éligibles, et l'agent ne crée pas de récupérateur.

### 7. Publication : `ClientInstagram.preparer_reel` et `publier_reel`

- `ClientInstagram.preparer_reel(video_url, cover_url, legende, *, share_to_feed=True) -> str` : conteneur `REELS` avec `video_url`, `cover_url`, `caption`, `share_to_feed`, puis sondage (15 s, 600 s max). Ne publie pas.
- `publier_reel(dossier, noms, legende, publier_vraiment, ...)` : exige exactement deux fichiers (vidéo, cover), ouvre l'hébergement éphémère avec **la cover comme témoin**, crée le conteneur, publie si `publier_vraiment`.
- `share_to_feed = True` par défaut (`publication_reel_share_to_feed`).

### 8. Le runner, format par format

`tourner(...)` reçoit en plus `preparer_reel` et `publier_reel` (mots-clés, défaut `None`) et **dispatche par `creneau.format`**. Tout le reste est inchangé et vaut pour les deux formats.

Quatre ajustements :

- **Une panne systémique n'arrête que son format** (ffmpeg absent ou yt-dlp en panne ne bloque pas le gel des carrousels).
- **Une attente n'est ni une faute ni une panne** : `PreparationDifferee` → raison écrite sur le créneau (seulement si elle a changé), ni exclusion, ni notification, ni suspension du format (un reel à vidéo directe peut encore geler). Le créneau reste `PROGRAMME` ; s'il dépasse 90 minutes de retard, il passe en `MANQUE` comme tout créneau.
- **Gabarit contrôlé au gel** : un post dont le gabarit ne correspond pas au format du créneau → erreur sur le créneau, pas d'exclusion.
- **Ménage des gros fichiers** : suppression des sous-dossiers numériques de `publication_dir` dont le créneau n'est plus `GELE` ; `essais/` n'est pas touché.

**Mode `apercu` pour un reel** : tout, y compris yt-dlp, l'assemblage, le téléchargement par Meta et l'encodage jusqu'à `FINISHED`, sauf `media_publish`. Le créneau passe en `APERCU` et l'article est consommé.

**Créneau reel sans candidat** : il reste `LIBRE`, puis `MANQUE` sans notification. Pas de repli carrousel.

**Durée d'un passage** : un reel ajoute au pire ~10 min de sondage, plus l'assemblage, plus un téléchargement yt-dlp (5 min au plus, quelques secondes mesurées). launchd ne relance pas un agent encore en cours.

### 9. API et dashboard

- `CreneauOut.format` ; `GET /schedule` montre les deux formats mêlés, triés par heure.
- `PATCH /schedule/{id}` : **remplacer** sur un créneau reel refuse (409) un article inéligible avec la raison lisible — un reel d'artiste sans `media_url` est accepté (yt-dlp) ; **décaler** reste dans le format ; le brouillon repris l'est au gabarit du format.
- `GET /schedule/offre-reels` → `{avec_video, directes, eligibles, fraicheur_jours}`.
- `GET /publication/reels` → `{actifs, ytdlp}` ; `PUT /publication/reels` `{actifs}` écrit l'interrupteur et relance l'arbitrage. Pas de confirmation : couper ne publie rien, et rallumer reste soumis au mode.
- Panneau Programmation : pastille de format par ligne (🎬 Reel / 🖼 Carrousel), case « Reels auto » à côté du sélecteur de mode, ligne d'offre (« … dont N à lien direct — le reste via yt-dlp au gel »).

### 10. Quota, cadence et délais

| Élément | Valeur | Raison |
|---|---|---|
| Publications / 24 h | 4 (2 + 2) | quota Meta 100 (50 selon une autre ligne de la doc) |
| Lectures de statut par reel | ≤ 40 | pas de 15 s, 600 s max |
| Téléchargement API | plafond 100 Mo, délai httpx 120 s | vidéos ≤ 90 s bien en dessous |
| Téléchargement yt-dlp | **1 par passage** (≤ 6/h en théorie, en pratique ≤ 2/jour : un par reel gelé), plafond 100 Mo, délai 300 s | faible cadence décidée avec l'utilisateur ; limite l'exposition de l'IP résidentielle |
| Pause après panne yt-dlp | 60 min | ne pas insister contre une limitation ou une session exigée |
| ffmpeg / ffprobe | 300 s par appel | inchangé |
| Reel assemblé | ≤ 100 Mo | marge sous les 300 Mo Meta |
| Quota d'appels de l'app | ~45 appels par reel au pire | yt-dlp ne consomme **aucun** appel Graph ; `x-app-usage` à regarder au premier aperçu |

### 11. Tests

- Python pur et base SQLite : slots, arbitrage par format, éligibilité (dont yt-dlp et coupe-circuits), crédit, légende, API, runner (outils factices) — sans réseau ni ffmpeg.
- `app/publish/ytdlp.py` : **exécuteur de sous-processus factice** qui rejoue yt-dlp (écrit le fichier de `-o`, ou échoue avec un vrai message) ; commande, sentinelle « jamais de cookies ni de connexion », budget, pause, classement des erreurs, plafond, lien hors Instagram. Aucun test ne lance yt-dlp ; un test vérifie seulement la version installée (`yt_dlp.version.__version__ >= "2026.08.19"`).
- `preparer_reel` : outils injectés (`rendre`, `telecharger`, `recuperer_ytdlp`, `sonder`, `assembler`, `verifier_outils`) pour toutes les branches, dont l'ordre API puis yt-dlp et le repli sur lien expiré ; `telecharger_video` sur `httpx.MockTransport`.
- Client Instagram : faux HTTP, `IN_PROGRESS` puis `FINISHED` avec le pas de sondage reel.
- **ffmpeg réel** seulement là où c'est ce qu'on veut prouver (specs du MP4 produit ; source **VP9** réencodée en H.264/AAC, sautée sans `libvpx-vp9` ; chaîne `preparer_reel` complète), marqués `besoin_ffmpeg`.

---

## Risques

Dits sans détour : ces risques ne sont pas supprimés par le design, seulement bornés. L'utilisateur les a acceptés le 2026-09-17.

### yt-dlp

- **CGU d'Instagram.** Télécharger le contenu d'Instagram par un outil automatisé est contraire à ses conditions d'utilisation, même sans connexion. Conséquences possibles : blocage de l'IP, et, si Meta rapproche l'activité du compte `kindanewsfrance` (même machine, même IP), sanctions sur ce compte. Bornes : pas de session (rien ne relie les requêtes au compte Kinda côté Instagram), au gel seulement, un téléchargement par passage, deux reels par jour au plus, pause après panne.
- **Fragilité.** Instagram change régulièrement ; yt-dlp suit, avec retard. La panne est **attendue**, pas exceptionnelle : elle est notifiée avec la commande de mise à jour, n'exclut aucun article, ne bloque pas les carrousels, et les créneaux reel manqués ne coûtent rien. La version est épinglée pour qu'une mise à jour soit un geste volontaire, testé (essai sans publier), et versionné (`uv.lock`).
- **Limitation de l'IP.** L'agent tourne depuis l'IP résidentielle de l'utilisateur, qui sert aussi à la navigation et au reste du pipeline (Reddit, flux RSS). Un 429 déclenche la pause de 60 minutes, sans nouvelle tentative dans l'intervalle.
- **Surface d'exécution.** yt-dlp est du code tiers exécuté localement sur des pages distantes. Bornes : dépendance verrouillée, `--ignore-config` (aucune configuration utilisateur ne s'invite), liens Instagram uniquement, fichier source effacé du dossier servi par le tunnel.

### Droits

Reposter la vidéo d'un artiste est une vraie question de droits, et elle est plus lourde qu'une photo :

- **droit d'auteur sur l'image** (le vidéaste, souvent l'équipe de l'artiste ou un fan) ;
- **droits voisins sur la musique** qui accompagne presque toujours la vidéo — Meta le sait, c'est la raison même de l'omission de `media_url` ; publiée par l'API, la vidéo ne passe pas par la bibliothèque musicale sous licence d'Instagram : **détection de droits à la republication probable — son coupé, reel bloqué, voire avertissement sur le compte**. yt-dlp ne change rien à ce risque, il le rend réel : sans lui, ces vidéos n'étaient pas publiables du tout ;
- **CGU Meta** sur la réutilisation de contenus de tiers.

Ce que le design fait pour limiter le risque, sans prétendre le supprimer :

1. **crédit obligatoire** `🎥 via @compte`, jamais coupé, qui notifie le compte cité ;
2. **restriction aux comptes qui produisent l'actu** (artiste, festival, label, club) — jamais un média ;
3. le montage Kinda (cover en première frame, fond flouté) signale la reprise éditoriale sans prétendre à la paternité ;
4. **démarrage en `apercu`** : tout est préparé chez Meta sans rien publier ;
5. deux coupe-circuits (dashboard, `.env`) et retrait rapide à la main depuis Instagram en cas de réclamation.

---

## Défauts retenus (anciennes questions ouvertes, toutes tranchées)

1. **Approvisionnement** : API d'abord, **yt-dlp** ensuite, au gel (révision du 2026-09-17). Compteur d'offre pour mesurer la part yt-dlp ; option C en réserve.
2. **Risque musique/droits et CGU** : assumé, **mode `apercu` d'abord** ; passer en `reel` reste une décision explicite (confirmation « PUBLIER »).
3. **Catégories autorisées** : `artiste,festival,label,club`.
4. **Fenêtres reel** : `12:00-14:00,20:30-22:30`.
5. **Vidéo trop longue** : refusée au-delà de 90 s, jamais coupée.
6. **`share_to_feed`** : `true`.

## Ce qui dépend de l'utilisateur, pas du code

1. `uv sync` (installe yt-dlp épinglé) ; vérifier `uv run python -m yt_dlp --version`.
2. Appliquer la **migration 0017** sur Neon.
3. `ffmpeg` et `ffprobe` installés (`brew install ffmpeg`).
4. Mode **`apercu`** d'abord ; essai sans publier : `uv run python -m scripts.diagnostic.tester_publication --post <ID d'un post kinda_reel>`.
5. Observer quelques jours les créneaux reel « Gelé » → « Aperçu », l'offre et les raisons d'attente yt-dlp.
6. Mettre yt-dlp à jour quand la notification le demande : `uv lock --upgrade-package yt-dlp && uv sync`, puis committer `uv.lock`.
