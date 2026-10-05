# Visuels : l'info des medias, les images des artistes

**Quand lire ce fichier** : avant de toucher a `app/content/visuels.py`,
`choix_visuels.py`, `actifs_artistes.py`, `lecture_artistes.py`,
`recherche_web.py`, ou avant de faire lire un media a quoi que ce soit qui
PUBLIE. Il porte la regle absolue « l'info d'un compte media oui, ses visuels
jamais », l'ordre des reserves (medias de l'actu, groupe de doublons, compte
source, artistes cites, web), les budgets de lecture et le cache local des
copies.

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
(`tests/content/test_visuels_web.py`) qui prouve que la conclusion « rien ne convient »
(le saut depuis la Tâche 5, l'aplat Kinda avant elle) n'est atteinte qu'APRÈS
une recherche web restée bredouille.

**Les encyclopédies sont écartées comme les banques d'images** (2026-10-04,
post 164 Tomorrowland Winter publié le 03/10 : la troisième slide était le logo
du festival, recadré au point que les lettres étaient coupées — « MORROWL » —,
venu de l'`og:image` de `en.wikipedia.org`). L'image de tête d'un article
encyclopédique sur une marque, un festival ou un groupe est presque toujours
son emblème : c'est le producteur de logos le plus régulier du web, exactement
le motif qui faisait déjà écarter les banques d'images. Mesure sur
`visuels_web_cache_path` (7 requêtes, 21 candidats) : `en.wikipedia.org` est le
domaine le **plus fréquent**, 4 candidats sur 21 (19 %), dans 3 requêtes sur 7.
Ajoutés : `wikipedia.org`, `wikimedia.org`, `wikidata.org`, `wiktionary.org`,
`fandom.com`, `wikia.`.

Plus un filtre qui ne dépend d'aucun domaine : **une `og:image` dont le nom de
fichier se dit logo** (`est_un_logo`, le mot isolé par la ponctuation du CHEMIN
— jamais la requête, où un paramètre de CDN porte n'importe quoi). Les trois
logos du cache le disaient tous les trois (`Tomorrowland_festival_logo.svg`,
`OUT.FEST_2021_logo_1.png`, `logo_blue_white_320x250.png`), soit 14 % des
candidats. Total : **6 candidats sur 21 écartés (29 %), zéro requête vidée** —
aucune actu ne perd son dernier recours.

Le filtre vaut à la COLLECTE **et à la relecture du cache** : une entrée posée
avant ce correctif servirait sinon le défaut pendant encore
`visuels_web_cache_jours`, et refiltrer coûte zéro requête là où rejouer la
recherche en coûterait une payante. Et il est posé dans `recherche_web`, le
point par lequel passent les DEUX usages : le fond de cover ET les slides —
l'image fautive du post 164 était une slide. Le juge visuel n'est PAS durci :
il reste indulgent (`meilleures_photos(garder_inconnues=True)`) parce que
l'alternative y est de sauter l'article entier ; ces deux filtres-ci sont
déterministes et gratuits, ils le laissent tranquille.

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
VisuelDiffere` quand la recherche elle-même
n'a PAS PU être tentée (réseau, quota) — pour que l'appelant sache qu'il doit
REPORTER (`VisuelsDifferes`) plutôt que conclure à l'absence d'image : sans
cette distinction, une simple coupure réseau faisait sauter un créneau avec la
raison fausse « aucune image… ni sur le web » alors que le web n'avait jamais
répondu.

**Le filtre d'entrée de la file ignorait le dernier barreau** (desserré le
2026-10-05, décision de l'utilisateur). `store.raison_sans_visuel` refusait un
article qui n'a aucune image autorisée EN BASE et ne cite aucun artiste suivi —
à l'arbitrage comme à l'assignation manuelle (« Action refusée : Article sans
visuel autorisé : visuels d'un compte média Instagram interdits, et aucun
artiste suivi cité »). Il est délibérément SANS appel réseau, et c'est bien ;
mais il concluait « pas de visuel » là où il fallait conclure « pas de visuel
**en base** » : la recherche WEB est le dernier barreau de l'échelle et peut
illustrer à peu près n'importe quelle actu. Mesuré ce jour-là : **7 articles sur
48 publiables (14 %)** étaient bloqués, tous par ce seul motif, et après
desserrage **0 sur 47**.

Il reste **deux refus**, tous deux DÉTERMINISTES et gratuits
(`recherche_web.raison_sans_recherche`, pure) : `VISUELS_WEB_ENABLED=false` — le
barreau n'existe plus, le filtre reprend exactement sa sévérité d'avant —, et un
article dont RIEN ne donne de requête (`requete_pour` rend `None`). Laisser
passer ces deux cas ne ferait que leur faire sauter des créneaux.

**Et ce desserrage n'est sûr QUE grâce au correctif en aval du même jour.**
`choix_visuels` levait `AucunVisuel` quand le web avait regardé et n'avait rien
donné, et `runner._exclure` écartait alors l'article **définitivement**
(« composition impossible : … »). Desserrer sans toucher à ça aurait été PIRE
que le refus : au lieu d'être écartés proprement, ces 7 articles auraient été
**brûlés un par un**. Les chemins où le web a regardé pour rien lèvent donc
désormais `VisuelIntrouvable` — un SAUT (`_liberer`, puis
`pause_visuels.marquer_saute` seulement si la libération a réussi, l'INVARIANT
D'ORDRE documenté, désormais factorisé dans `runner._sauter`, UN seul point
d'appel pour les deux chemins de composition ; l'ancien handler l'enfreignait,
il appelait `marquer_saute` avant `_liberer`).

`AucunVisuel` garde un sens, et un seul — c'est ce qui justifie de ne pas
fusionner les deux exceptions : le **FORMAT** est impossible quoi que trouve le
web, c'est-à-dire un reel sans vidéo (le web ne sert jamais la vidéo d'un reel,
seulement le fond de sa cover). Aucun passage suivant n'y changera rien, donc
l'exclusion reste juste. La distinction est passée de « pas d'image » / « pas pu
regarder » à **« impossible par NATURE » / « introuvable AUJOURD'HUI » / « pas
pu regarder »**.

**Les règles de visuels ne bougent pas d'un pouce** : jamais le visuel d'un
compte média Instagram, jamais une affiche en fond, une seule affiche en slide.
On a desserré QUI est éligible, jamais CE QU'ON PUBLIE — un article de compte
média qui passe désormais le filtre est illustré par le web, et ses propres
médias restent interdits par les mêmes ceintures (`_propres`,
`preparation.medias_du_carrousel`, `export`).

Coût réseau mesuré : le cache par requête (`visuels_web_cache_jours`, 3) absorbe
**entièrement** la boucle de reprise — une recherche bredouille est mémorisée,
donc les ~8 nouvelles tentatives par jour qu'autorise la pause de 3 h ne coûtent
**zéro** appel. Ce qu'il n'absorbe pas est la PREMIÈRE recherche par requête
distincte : au plus une par composition, donc **≤ 3 par jour** (3 créneaux
carrousel), contre ~2,7/jour mesurées dans le cache en production. L'ordre de
grandeur double, il reste négligeable.

Sans visuel autorisé, l'article reste au dashboard (pastille « 🚫 sans visuel »,
raison via `store.raison_sans_visuel`, sans appel réseau — un artiste suivi cité
suffit à dire « pas encore », le vrai verdict attend la composition) mais sort
de la file de publication. À la composition, trois issues distinctes :
`AucunVisuel` exclut l'article (« composition impossible : … ») — le format est
impossible, voir ci-dessus ; `VisuelIntrouvable` SAUTE le créneau et met
l'article à l'écart quelques heures sans jamais l'exclure. Une panne de
TRANSPORT ne donne jamais ni l'un ni l'autre : elle lève toujours
`VisuelsDifferes`, qui pose seulement la raison
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
