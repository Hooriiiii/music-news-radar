# Reels automatiques : deux reels par jour en plus des carrousels — design

Date : 2026-09-17
État : décisions de l'utilisateur intégrées, **une question d'approvisionnement à trancher** (voir « Questions ouvertes », n° 1), prêt pour le plan d'implémentation avec les défauts recommandés
Plan : `docs/superpowers/plans/2026-09-17-reels-automatiques.md`
S'appuie sur : `docs/superpowers/specs/2026-09-11-publication-meta-design.md` (révision du 2026-09-16) — la file, les trois modes, le gel, la réclamation atomique, tout ce qui suit en hérite.

## L'objectif

Décisions de l'utilisateur du 2026-09-17 :

- des reels automatiques **en plus** des carrousels : **2 carrousels/jour (inchangé) + 2 reels/jour** ;
- contenu : les articles les mieux notés qui portent une **vidéo exploitable** — aujourd'hui presque uniquement des posts Instagram de comptes d'artistes et de festivals ;
- **même modèle de sécurité** que les carrousels : composition à J-1, gel à T-2h (pour un reel, le gel TÉLÉCHARGE la vidéo, ASSEMBLE le MP4 avec `app/content/video.py::assembler_reel` et le SONDE avec ffprobe), 90 minutes de retard au plus, modes `arret`/`apercu`/`reel` partagés, réclamation atomique avant Meta, exclusions, notifications, dashboard qui édite pendant un passage ;
- publication par l'API Instagram avec **connexion Instagram** (`graph.instagram.com`, même jeton que les carrousels) ;
- **crédit obligatoire** en légende (`@compte` source + « via »).

---

## Le risque principal : l'approvisionnement en vidéos

**À lire avant tout le reste : la fonctionnalité risque de ne presque rien avoir à publier.**

### Ce que dit la documentation Meta

Vérifié le 2026-09-17 sur
<https://developers.facebook.com/docs/instagram-platform/reference/instagram-media> :

> `media_url` — « The URL for the media. The `media_url` field is omitted from responses if the media contains copyrighted material or has been flagged for a copyright violation. »
>
> `thumbnail_url` — « Media thumbnail URL. Only available on `VIDEO` media. »

### Ce qui a été mesuré (coordinateur, 2026-09-17, pas de nouvelle mesure en direct pour préserver le quota Graph)

- Appel `business_discovery` sur `davidguetta` : **10 reels sur 10** rendent `media_type=VIDEO`, `media_product_type=REELS`, **sans `media_url`**, avec seulement `thumbnail_url`. Les carrousels, eux, rendent leur `media_url`.
- En base : **59 reels ingérés, 39 sans aucune vidéo** (LE SSERAFIM 11, DJ Mag 6, Booska-P 5, ENHYPEN 5, IVE 3, David Guetta 2, Charlotte de Witte 2, Anyma 2…). Les reels qui portent un `media_url` viennent **surtout de comptes médias** (Billboard, par exemple).

L'explication tient dans la phrase de Meta : un reel d'artiste porte presque toujours de la musique, donc du « copyrighted material », donc pas de `media_url`. **Les reels d'artistes — précisément le contenu que la ligne éditoriale et la question des droits privilégient — ne sont pas téléchargeables par l'API.**

### Estimation de l'offre

Les 59 reels proviennent du premier passage de la source Instagram (mise en service le 2026-09-17, jusqu'à 12 posts récents par compte, donc un arriéré de plusieurs jours à plusieurs semaines selon les comptes, pas un flux quotidien).

- 20 reels sur 59 portent une vidéo (34 %) ; « surtout » des médias, donc **à peine quelques-uns** venant d'artistes, festivals, labels ou clubs.
- S'y ajoutent les **vidéos enfants de carrousels**, qui gardent leur `media_url` quand elles n'ont pas de musique protégée — volume inconnu, probablement faible (les carrousels d'artistes sont surtout des photos).
- Il faut ensuite passer les filtres de la file : pertinence ≥ 60, moins de 3 jours, pas doublon, pas déjà publié, durée de 3 à 90 s (inconnue avant le téléchargement : `business_discovery` ne donne ni durée ni poids).

**Ordre de grandeur retenu : de zéro à un reel éligible par jour en régime établi, avec la restriction de catégorie — soit nettement sous les 2/jour visés.** Sans la restriction (médias acceptés), l'offre monterait peut-être à 1-3/jour, mais ce seraient des reposts de médias. C'est une estimation sur un échantillon d'un seul passage, pas une mesure ; le plan ajoute donc un **compteur d'offre** (`GET /schedule/offre-reels`, affiché dans le panneau Programmation) pour la mesurer deux semaines avant de décider.

### Les options

| Option | Offre | Droits / ligne éditoriale | Coût |
|---|---|---|---|
| **A. Accepter les vidéos de comptes médias**, avec crédit | la plus grande des options API | **Contraire à « Qui parle »** (reposter un média = plagiat, cf. `CLAUDE.md`) ; un média reposte souvent lui-même une vidéo d'artiste : double reprise | nul (réglage `publication_reel_categories`) |
| **B. Vidéos enfants de carrousels** d'artistes/festivals | faible, inconnue | acceptable (même régime que les photos reprises en carrousel) | **nul : déjà couvert** par l'aplatissement des carrousels de `app/sources/instagram.py::_media` |
| **C. Reel fabriqué à partir de photos** (cover + photos de l'actu, mouvement lent type Ken Burns, sans musique ou avec une piste libre de droits) | **toujours disponible** dès qu'il y a des photos | pas de musique protégée ; photos sous le même régime que les carrousels | un chantier de plus (générateur ffmpeg), engagement probablement moindre qu'une vraie vidéo live |
| **D. Garder les reels manuels** (export zip du dashboard) | ce que l'utilisateur trouve | l'utilisateur juge au cas par cas | nul, mais pas automatique ; télécharger à la main un reel Instagram pose le même problème de droits |

### Recommandation

1. **Construire le pipeline maintenant** (ce plan), avec la restriction de catégorie par défaut (artiste, festival, label, club) et **B** automatiquement inclus. Un créneau reel sans candidat **reste vide** : aucun dégât, aucun coût.
2. **Mesurer deux semaines** avec le compteur d'offre.
3. Si l'offre reste sous ~1/jour, ouvrir **C** comme chantier suivant. Le pipeline de ce plan en est le socle : `preparer_reel` isole l'obtention des octets vidéo (`telecharger`) du reste (cover, assemblage, sondage, contrôle des specs, hébergement, publication) ; un générateur de vidéo à partir de photos se branche à cet endroit sans toucher la file.
4. **A** reste un réglage (`PUBLICATION_REEL_CATEGORIES=artiste,festival,label,club,media`) que l'utilisateur peut activer en connaissance de cause. Non recommandé.

### Le trou d'ingestion joint

`app/sources/instagram.py::_media` jette une entrée `VIDEO` sans `media_url` **entièrement**, vignette comprise : 39 reels sont arrivés sans aucun média, donc sans image de cover et hors de la file carrousel (`_a_une_image`). Correction (tâche 1 du plan) : une vidéo sans `media_url` mais avec `thumbnail_url` devient une entrée **`photo`** dont l'`url` est la vignette. Ces posts redeviennent illustrés et éligibles en carrousel ; ils ne deviennent pas éligibles en reel (pas de vidéo), ce qui est exact. Les articles déjà ingérés ne sont pas repris (la dédup les écarte, la purge les emportera à 14 jours).

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

- `assembler_reel` sort déjà du H.264/AAC 1080×1920 à 30 i/s avec `+faststart` (moov en tête). **Deux écarts à corriger** : le muxer MP4 de ffmpeg écrit une **edit list** par défaut (décalage d'amorçage AAC) → ajouter `-use_editlist 0` ; le GOP fermé est le défaut de libx264 mais n'est pas garanti explicitement → ajouter `-flags +cgop`. Un test ffmpeg vérifie l'absence d'atome `elst` dans le `moov`.
- **90 s maximum** reste une borne ÉDITORIALE de Kinda (`DUREE_MAX_REEL_MS` de `app/content/media.py`), pas une limite Meta : un live de concert repris se regarde en extrait. Le plan la rend réglable (`publication_reel_duree_max_s`). Une vidéo trop longue est **refusée, pas coupée** : couper arbitrairement le contenu d'un artiste aggrave la question des droits et du sens.
- La cover `kinda_reel` fait 1080×1920 : c'est exactement le 9:16 que Meta garde. Elle part en JPEG **sans recadrage** (contrairement à la cover carrousel, recadrée en 4:5).
- **Sondage d'un reel : toutes les 15 s, 10 minutes au plus.** Écart assumé à la recommandation (1/min, 5 min) : l'encodage d'une vidéo prend plus longtemps qu'une image, et à ce stade le créneau est déjà RÉCLAMÉ — un dépassement de délai le fait passer en `ECHEC` sans retour possible. 40 lectures au pire pour un reel, négligeable pour le quota d'appels. Un conteneur non publié expire seul en 24 h.
- **Pas de limite de poids pour le tunnel.** Le tunnel rapide `trycloudflare.com` ne limite que les corps de REQUÊTE entrants (téléversement) ; Meta télécharge (réponse), sans plafond documenté. Kinda borne le reel assemblé à **100 Mo** (`publication_reel_max_mo`), très sous les 300 Mo de Meta : un reel de 90 s en 1080×1920 sort typiquement à 15-40 Mo. Le tunnel rapide reste « sans garantie de service » : une panne fait passer le créneau en `ECHEC` notifié, comme pour un carrousel.

---

## Décisions de design

### 1. Les créneaux portent un format

**Colonne `creneaux.format`** (`carrousel` | `reel`, VARCHAR + CHECK, défaut `carrousel`, migration **0017**). Les créneaux existants deviennent des carrousels sans backfill.

Pourquoi une colonne et pas une table : tout le cycle (LIBRE → PROGRAMME → GELE → PUBLIE/APERCU/ECHEC/MANQUE), la réclamation atomique, le panneau et l'API sont identiques ; seules trois étapes changent d'outil (gabarit composé, préparateur, publieur). Une seconde table dupliquerait tout le runner.

**Fenêtres séparées** pour les reels : `publication_reel_fenetres = "12:00-14:00,20:30-22:30"` (Paris). Deux fenêtres = deux reels/jour ; le nombre par jour est le nombre de fenêtres. Midi et soirée, hors des fenêtres carrousel (08:30-11:00, 17:00-19:30) : on n'empile pas deux publications à la même heure, et la soirée est le moment des lives de la veille et des annonces d'événements. **Horizon : `publication_creneaux_reel = 6`** (3 jours × 2) ; **0 désactive les reels** sans toucher au reste.

**`publier_a` reste UNIQUE globalement** (index de 0014 inchangé). C'est le garde-fou « deux créneaux à la même heure publieraient deux fois » ; il reste vrai entre formats. Pour qu'il ne casse jamais, `app/schedule/slots.py` **refuse des fenêtres reel qui chevauchent** une fenêtre carrousel ou une autre fenêtre reel (`FenetresInvalides`) : une configuration fautive fait crier l'étape d'arbitrage (notification) au lieu de lever une `IntegrityError` obscure.

**Graine.** Les carrousels gardent la graine actuelle (`kinda-{date}-{rang}`) : changer de graine déplacerait toutes les heures déjà programmées en base. Les reels ont la leur (`kinda-reel-{date}-{rang}`).

### 2. L'arbitrage par format, sans double emploi

**Un article n'occupe qu'un créneau, tous formats confondus.** Les reels sont arbitrés **d'abord**, les carrousels ensuite sur ce qui reste.

Pourquoi les reels d'abord : la matière vidéo est rare (voir le risque principal), la matière photo abondante. Un article vidéo placé en reel laisse les carrousels piocher dans le reste du flux ; l'inverse viderait les reels pour gagner un carrousel parmi d'autres.

Mécanique dans `appliquer_arbitrage` (store) :

1. pour chaque format, dans l'ordre `reel`, `carrousel` : synchroniser ses créneaux, lister ses candidats, retirer les articles **tenus ailleurs**, arbitrer avec `queue.arbitrer` **inchangé**, écrire ;
2. « tenus ailleurs » = portés par un créneau passé encore `PROGRAMME`/`GELE` (règle existante, tous formats), par un créneau futur **épinglé ou verrouillé de l'autre format**, ou placés par l'arbitrage du format précédent **dans ce même passage**.

Un carrousel `PROGRAMME` non épinglé dont l'article est pris par un reel est simplement réaffecté au passage carrousel qui suit : pas d'état intermédiaire visible hors d'une transaction.

`candidats_eligibles` continue d'écarter tout article déjà passé en `APERCU` ou `PUBLIE` **quel que soit le format** : pas de reel d'un article déjà publié en carrousel, et inversement.

**Éligibilité reel** (`raison_ineligible_reel(article) -> str | None`, partagée par l'arbitrage et l'API) :

- au moins une vidéo exploitable (`videos_exploitables` : type `video`, URL directe, durée connue ≤ `publication_reel_duree_max_s` ou inconnue) ;
- source de catégorie autorisée (`publication_reel_categories`, défaut `artiste,festival,label,club` — voir « Droits ») ;
- **crédit calculable** : la source est un compte Instagram (`ig:<compte>`). Un reel ne part jamais sans `@compte`.

En plus des filtres communs (pertinence ≥ 60, 3 jours, pas doublon, pas USED, pas exclu). La requête des candidats reel lit 300 articles au lieu de 60 avant le filtre Python, les vidéos étant rares dans le haut du classement.

L'avantage « déjà composé » (`MARGE_TITULAIRE`) ne compte qu'un post **du gabarit du format** : un brouillon carrousel ne donne pas d'avance en reel.

### 3. Composition

`_composer` passe `template="kinda_reel"` à `create_post` pour un créneau reel ; `post_a_reprendre` filtre sur le gabarit. Le brouillon Claude porte déjà `title`/`subtitle` : aucun changement de prompt, **aucun coût supplémentaire**. Avant de composer un reel, l'article est revérifié (`raison_ineligible_reel`) : devenu inéligible (catégorie changée au dashboard), le créneau est **libéré sans exclure** l'article, qui reste bon pour un carrousel.

### 4. Crédit et légende

**Colonne `posts.credit`** (`String(200)`, nullable, migration 0017), posée par `create_post` : `@compte` quand la source est Instagram, sinon `None`. Dénormalisée comme `source_name` : le post survit à la purge de l'article, son crédit aussi.

`texte_legende` ajoute une ligne **`🎥 via @compte`** (reel) ou **`📸 via @compte`** (carrousel) avant `Source : <url>`. Comme la source, **le crédit n'est jamais coupé** quand la légende dépasse 2200 caractères. Les carrousels issus d'Instagram en profitent aussi : même bonne foi, sans coût. La mention `@` notifie le compte cité — c'est voulu (transparence envers l'artiste).

Crédit **obligatoire pour un reel** à trois endroits : éligibilité, composition (via l'éligibilité), gel (`preparer_reel` refuse un post sans `credit`).

### 5. Le gel d'un reel : `app/publish/preparation_reel.py`

À T-2h, `preparer_reel(post, article, dossier)` :

1. vérifie le gabarit (`kinda_reel`) et le crédit ;
2. trouve l'URL vidéo (slide `media` vidéo du post, sinon première vidéo exploitable de l'article) ;
3. **vérifie ffmpeg ET ffprobe** (`video.exiger_outils()`) — absent = **panne systémique** (`AssemblageIndisponible` remonte) : rien n'est exclu, le créneau reste `PROGRAMME` avec l'erreur, retenté au passage suivant ;
4. rend la cover `kinda_reel` (Chromium) ; débordement → exclusion ;
5. **télécharge** la vidéo (`telecharger_video`, plafond 100 Mo) : HTTP 4xx (lien CDN signé **expiré** ou retiré), trop lourde, pas une vidéo → **faute de l'article** (`PreparationImpossible`, exclusion) ; erreur réseau ou 5xx → **panne systémique** (l'exception httpx remonte) ;
6. **sonde la source** : durée < 3 s ou > 90 s, fichier illisible → exclusion ;
7. **assemble** (`assembler_reel`) ; échec d'assemblage ou délai ffmpeg dépassé sur une entrée donnée → exclusion (ffmpeg est présent, c'est l'entrée qui pose problème) ;
8. **sonde la sortie** et la confronte aux specs Meta : 1080×1920, H.264/HEVC, AAC ≤ 48 kHz, 23-60 i/s, durée ≥ 3 s, ≤ 100 Mo → hors specs = exclusion (on ne découvre pas le refus chez Meta, après réclamation) ;
9. convertit la cover en JPEG ≤ 8 Mo **sans recadrage** (`images.en_jpeg`) ;
10. écrit `<prefixe>_reel.mp4` puis `<prefixe>_cover.jpg` (préfixe aléatoire, comme les carrousels) et rend ces deux noms **dans cet ordre** (`creneaux.fichiers`).

**Pourquoi télécharger au gel et pas avant** : les `media_url` du CDN Instagram sont signées et expirent en quelques jours ; à T-2h le lien a au plus ~3 jours (fraîcheur de la file). Un lien déjà mort est une exclusion propre, deux heures avant l'heure, visible au dashboard.

**Où vit le MP4** : dans `settings.publication_dir / <creneau.id>/`, comme les JPEG des carrousels. Les fichiers intermédiaires (source, cover PNG) vivent dans le `TemporaryDirectory` de `video.py` et disparaissent seuls.

**Cover : `cover_url` explicite**, pas `thumb_offset`. La cover `kinda_reel` est déjà la première frame de la vidéo (montage conservé), et `cover_url` garantit la vignette de l'onglet Reels même si Instagram réencode et perd cette frame unique — c'est le filet que `CLAUDE.md` annonçait (« le moyen fiable viendra de l'API Meta »).

### 6. Publication : `ClientInstagram.preparer_reel` et `publier_reel`

- `ClientInstagram.preparer_reel(video_url, cover_url, legende, *, share_to_feed=True) -> str` : conteneur `REELS` avec `video_url`, `cover_url`, `caption`, `share_to_feed`, puis sondage (15 s, 600 s max). Ne publie pas — même contrat que `preparer_carrousel`, c'est ce qui rend le mode `apercu` et le script d'essai possibles.
- `publier_reel(dossier, noms, legende, publier_vraiment, ...)` dans `app/publish/publier.py` : exige exactement deux fichiers (vidéo, cover), ouvre l'hébergement éphémère avec **la cover comme témoin** (`attendre_joignable` fait un GET complet : inutile d'y faire transiter la vidéo), crée le conteneur, publie si `publier_vraiment`.
- `share_to_feed = True` par défaut (`publication_reel_share_to_feed`) : le reel apparaît aussi dans la grille du compte.

### 7. Le runner, format par format

`tourner(...)` reçoit en plus `preparer_reel` et `publier_reel` (mots-clés, défaut `None`) et **dispatche par `creneau.format`**. Tout le reste est inchangé et vaut pour les deux formats : `_marquer_manques`, réclamation GELE → ECHEC committée avant Meta, exclusion « publication interrompue avant confirmation », relecture de l'horloge avant chaque publication, `_deja_publie`, UPDATE conditionnels.

Trois ajustements :

- **Une panne systémique n'arrête que son format.** Aujourd'hui `_geler` fait `break` à la première panne systémique. Avec ffmpeg absent, ça bloquerait aussi le gel des carrousels du même passage. Le `break` devient « ignorer les créneaux suivants de ce format ».
- **Gabarit contrôlé au gel** : un post dont le gabarit ne correspond pas au format du créneau (l'utilisateur a basculé le switch POST/REELS au dashboard) → erreur sur le créneau, pas d'exclusion (même règle que « le post ne correspond pas à l'article du créneau »).
- **Ménage des gros fichiers** : une étape `menage` supprime tout sous-dossier numérique de `publication_dir` dont le créneau n'est plus `GELE` (ou n'existe plus). Les chemins nominaux effacent déjà leur dossier ; le ménage rattrape les crashs (ECHEC après une réclamation interrompue, gel coupé au milieu). Le dossier `essais/` du script d'essai n'est pas touché.

**Mode `apercu` pour un reel** : tout, y compris le téléchargement de la vidéo par Meta et l'encodage jusqu'à `FINISHED`, sauf `media_publish`. Le créneau passe en `APERCU` et l'article est consommé, comme en carrousel. C'est l'essai qui valide pour de vrai les specs vidéo et le tunnel sous charge.

**Créneau reel sans candidat** : il reste `LIBRE`, puis passe en `MANQUE` à +90 min **sans notification** (règle existante : seuls les manqués avec article sont comptés). Pas de repli automatique sur un carrousel : ce serait un troisième post dans la journée.

**Durée d'un passage** : un reel ajoute au pire ~10 min de sondage pendant la publication, plus l'assemblage au gel (quelques dizaines de secondes pour 90 s de vidéo). launchd ne relance pas un agent encore en cours : le passage suivant attend. L'horloge est relue avant chaque publication, le garde-fou de retard s'applique donc toujours.

### 8. API et dashboard

- `CreneauOut.format` ; `GET /schedule` montre les deux formats mêlés, triés par heure.
- `PATCH /schedule/{id}` :
  - **remplacer** sur un créneau reel refuse (409) un article inéligible avec la raison lisible (`raison_ineligible_reel`) — la sécurité prime sur le choix manuel, comme pour un article exclu ;
  - **décaler** n'échange qu'avec le créneau modifiable suivant **du même format** ;
  - le brouillon repris l'est au gabarit du format.
- `GET /schedule/offre-reels` → `{avec_video, eligibles, fraicheur_jours}` : combien d'articles de la fenêtre de fraîcheur portent une vidéo exploitable, et combien passent toutes les règles reel. C'est le compteur qui tranchera la question n° 1.
- Panneau Programmation : pastille de format par ligne (🎬 Reel / 🖼 Carrousel), ligne d'offre reels sous le sélecteur de mode.

### 9. Quota et délais

| Élément | Valeur | Raison |
|---|---|---|
| Publications / 24 h | 4 (2 + 2) | quota Meta 100 (50 selon une autre ligne de la doc) |
| Lectures de statut par reel | ≤ 40 | pas de 15 s, 600 s max |
| Téléchargement source | plafond 100 Mo, délai httpx 120 s | vidéos Instagram de ≤ 90 s bien en dessous |
| ffmpeg / ffprobe | 300 s par appel (`DELAI_MAX_S` existant) | inchangé |
| Reel assemblé | ≤ 100 Mo | marge sous les 300 Mo Meta, limite l'exposition du tunnel |
| Quota d'appels de l'app | ~45 appels par reel au pire | la publication passe par `graph.instagram.com` (jeton IG), la source par `graph.facebook.com` (jeton FB). Que les deux partagent le même compteur d'app n'est **pas vérifié** : ~180 appels/jour au pire restent loin de l'incident du 2026-09-17 (~310 appels + 120 comptes en une heure), mais `x-app-usage` est à regarder au premier aperçu |

### 10. Tests

- Python pur et base SQLite : slots, arbitrage par format, éligibilité, crédit, légende, API, runner (outils factices) — sans réseau ni ffmpeg.
- `preparer_reel` : outils injectés (`rendre`, `telecharger`, `sonder`, `assembler`, `verifier_outils`) pour toutes les branches ; `telecharger_video` sur `httpx.MockTransport`.
- Client Instagram : faux HTTP, scénario `IN_PROGRESS` puis `FINISHED` avec le pas de sondage reel.
- **ffmpeg réel** seulement là où c'est ce qu'on veut prouver (specs du MP4 produit : codecs, cadence, absence d'`elst`, `moov` avant `mdat` ; chaîne `preparer_reel` complète), marqués `besoin_ffmpeg` (`skipif(shutil.which("ffmpeg") is None)`) comme `tests/test_video.py`.

---

## Droits

Reposter la vidéo d'un artiste est une vraie question de droits, et elle est plus lourde qu'une photo :

- **droit d'auteur sur l'image** (le vidéaste, souvent l'équipe de l'artiste ou un fan) ;
- **droits voisins sur la musique** qui accompagne presque toujours la vidéo — Meta le sait, c'est la raison même de l'omission de `media_url` ; publiée par l'API, la vidéo ne passe pas par la bibliothèque musicale sous licence d'Instagram : **risque de son coupé, de blocage du reel, voire d'avertissement sur le compte** ;
- **ToS Meta** sur la réutilisation de contenus de tiers.

Ce que le design fait pour limiter le risque, sans prétendre le supprimer :

1. **crédit obligatoire** `🎥 via @compte`, jamais coupé, qui notifie le compte cité ;
2. **restriction aux comptes qui produisent l'actu** (artiste, festival, label, club) par défaut — jamais un média, qui serait une double reprise et du plagiat au sens de « Qui parle » ;
3. le montage Kinda (cover en première frame, fond flouté) signale la reprise éditoriale sans prétendre à la paternité ;
4. retrait rapide possible à la main depuis Instagram en cas de réclamation.

Le repost d'assets de tiers a déjà été signalé et assumé par l'utilisateur pour les carrousels (design du 2026-09-11) ; la musique protégée en fait un risque d'un cran supérieur pour les reels, qu'il doit assumer explicitement (question n° 2).

---

## Questions ouvertes (décisions de l'utilisateur)

Le plan est exécutable tel quel avec les défauts recommandés.

1. **L'approvisionnement.** Les reels d'artistes n'ont pas de `media_url` (musique protégée) ; l'offre estimée est de 0 à 1 reel éligible par jour.
   **Défaut recommandé** : construire le pipeline, garder la restriction de catégorie, mesurer deux semaines avec `GET /schedule/offre-reels`, puis décider de l'option C (reel fabriqué à partir de photos) si l'offre reste sous ~1/jour. Alternatives : A (accepter les médias, non recommandé), D (reels manuels).
2. **Assumer le risque musique/droits des reels** (son coupé, blocage, avertissement de compte), au-delà de celui déjà assumé pour les photos.
   **Défaut recommandé** : oui, en mode `apercu` d'abord ; passer en `reel` est déjà une décision explicite (confirmation « PUBLIER »).
3. **Catégories autorisées en reel.**
   **Défaut recommandé** : `artiste,festival,label,club`. Ajouter `media` est l'option A.
4. **Fenêtres reel.**
   **Défaut recommandé** : `12:00-14:00,20:30-22:30`. Toute fenêtre qui chevauche une fenêtre carrousel est refusée au démarrage de l'arbitrage.
5. **Vidéo trop longue : refuser ou couper à 90 s ?**
   **Défaut recommandé** : refuser (ne pas couper le contenu d'un artiste).
6. **`share_to_feed`** (le reel apparaît aussi dans la grille).
   **Défaut recommandé** : `true`.

## Ce qui dépend de l'utilisateur, pas du code

1. Appliquer la **migration 0017** sur Neon.
2. `ffmpeg` et `ffprobe` installés (`brew install ffmpeg`) — déjà requis par l'export reel.
3. Essai sans publier : `uv run python -m scripts.tester_publication --post <ID d'un post kinda_reel>`.
4. Rester en `apercu` quelques jours et regarder les créneaux reel passer « Gelé » → « Aperçu ».
