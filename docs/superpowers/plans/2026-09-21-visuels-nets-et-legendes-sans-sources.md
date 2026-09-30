# Visuels nets, légendes sans noms de médias

Retour de l'utilisateur sur les posts RÉELLEMENT publiés sur @kindanewsfrance,
relevé le 2026-09-21 en ouvrant le compte : « fais un retour sur les derniers
posts, notamment la qualité de l'image de background du dernier post » puis
« supprime les sources des descriptions. Et ne met pas les noms des sources.
Parle de l'actu en elle-même. »

## Global Constraints

- Projet Kinda. `CLAUDE.md` à la racine fait autorité : le lire pour les
  sections « Génération de posts », « Visuels : l'info des médias, les images
  des artistes », « Illustration des articles ». Le mettre à jour quand une
  règle qui y est écrite change — un document qui ment coûte plus cher qu'un
  document absent.
- Tests : `uv run pytest`. Lint : `uv run ruff check .`. Les deux doivent être
  verts avant de committer. TDD : test rouge d'abord, toujours.
- Commentaires et messages de commit en français, sans accents dans les
  messages de commit (convention du dépôt). Un commentaire dit POURQUOI, avec
  le fait mesuré qui l'a motivé — pas ce que le code fait déjà lire.
- Règle de robustesse du projet : les modules qui choisissent des visuels
  (`illustration.py`, `recherche_web.py`) ne lèvent JAMAIS. Toute panne rend
  une valeur neutre et l'appelant continue.
- Aucune migration : rien de ce chantier ne change le schéma.
- Ne jamais mutter une colonne JSON en place (SQLAlchemy ne la persiste pas) :
  toujours réassigner une liste / un dict neufs.

## Task 1 — Les légendes et les crédits ne nomment plus aucun média

### Le défaut, mesuré sur les posts publiés

Relevé sur les 6 derniers posts en ligne :

- post 71 : « Qu'en pensez-vous ? **Info à retrouver via Stereogum.** »
- post 63 : « **Info via Stereogum.** Vous auriez aimé être dans la salle… »
- post 65 : « … **selon Soompi** … »
- post 59 : crédit `📸 djmag.com beatportal.com`

Kinda veut être une SOURCE, pas un intermédiaire (voir `CLAUDE.md`, section
« Veille rapide »). Nommer le média d'où vient l'info fait exactement
l'inverse : ça transforme le post en renvoi vers un concurrent.

### Ce qu'il faut faire

**a) Règle de prompt** dans le `SYSTEM_PROMPT` de `app/content/compose.py` :
ne jamais nommer le média d'où vient l'information — ni « via X », ni
« selon X », ni « d'après X », ni « info à retrouver sur X ». Parler de l'actu
elle-même. Les noms d'ARTISTES, de labels, de festivals, de salles, de villes
restent évidemment permis : ce sont le sujet, pas la source.

**b) Un filet déterministe**, parce que le prompt ne garantit jamais rien à lui
seul — c'est une leçon déjà écrite trois fois dans `CLAUDE.md` (hashtags sans
dièse, légendes sans accents, garde-fous de scoring). Dans
`app/content/legende.py`, une fonction qui retire d'une légende les phrases
qui nomment la source de l'article.

Elle ne DEVINE pas : on lui passe les noms à bannir, construits depuis
l'article — le nom de la source débarrassé de son suffixe de canal
(« Transmusicales (IG) » → « Transmusicales », le nettoyage existe déjà dans
`app/content/recherche_web.py`, le réutiliser plutôt que de le réécrire) et le
domaine de `article.url` (avec et sans `www.`, et sans son TLD :
`stereogum.com` doit attraper « Stereogum »).

Découpage en phrases : `app/content/legende.py` sait déjà le faire pour
`aerer_legende` — réutiliser la même machinerie, ne pas en écrire une seconde.

Deux garde-fous :
- une phrase n'est retirée que si elle nomme la source ; le reste ne bouge pas ;
- si tout serait retiré, on garde la légende TELLE QUELLE. Une légende vide est
  pire qu'une légende qui cite un média, et `PostDraft` la validerait mal.

Appliqué à la COMPOSITION (`app/content/compose.py`, sur le brouillon rendu
par Claude), pas à la publication : la légende persistée doit déjà être propre,
sinon le dashboard affiche autre chose que ce qui partira réellement.

**c) Le crédit d'un DOMAINE disparaît.** Décision explicite de l'utilisateur le
2026-09-21, en réponse à la question posée : « retirer aussi le crédit web ».
Un crédit qui est un nom de domaine (`djmag.com`, `ra.co`) n'est plus émis ni
affiché. Les crédits de COMPTE (`@artiste`, `🎥 @compte`) restent strictement
inchangés : ils créditent l'artiste dont on publie le visuel, c'est une autre
règle et l'utilisateur y tient.

Le filtre doit valoir aussi à l'AFFICHAGE (`texte_legende`), pas seulement à la
création : des brouillons déjà en base portent un crédit de domaine (post 59
en est un) et ils ne doivent plus le publier.

**d) `CLAUDE.md` ment maintenant** : la section « Le dernier recours : une
image du WEB » dit « **Pas de filtrage de licence** […] En contrepartie le site
est TOUJOURS cité ». Cette contrepartie est levée par décision de
l'utilisateur. Réécrire ce passage pour dire ce qui est vrai, en gardant la
trace de ce qui a été échangé (l'absence de filtrage de licence reste, la
citation disparaît).

### Vérification attendue

Des tests qui prouvent, sur les phrases RÉELLEMENT publiées citées plus haut :
« Info via Stereogum. » et « Qu'en pensez-vous ? Info à retrouver via
Stereogum. » et « … selon Soompi … » ne survivent pas au filet ; une légende
qui nomme un ARTISTE homonyme d'un média n'est pas mutilée ; une légende
entièrement composée de mentions de source ressort intacte plutôt que vide ;
un crédit `djmag.com` ne s'affiche plus, un crédit `@defectedrecords` si.

## Task 2 — Le fond de la cover est net

### Le défaut, mesuré

Résolution du fond des 6 derniers posts publiés, et agrandissement qu'il a
fallu pour remplir le gabarit (1080×1350 en post, 1080×1920 en reel) :

| publié | post | fond | agrandissement | |
|---|---|---|---|---|
| 21/09 08:21 | 71 | 564×345 | **3,91×** | flou |
| 20/09 14:24 | 65 | 1920×1280 | 1,05× | net |
| 20/09 09:32 | 64 | 1215×2160 | 0,89× | net |
| 20/09 07:46 | 63 | 564×384 | **3,52×** | flou |
| 19/09 20:27 | 62 | 720×900 | **2,13×** | flou |
| 19/09 18:00 | 59 | 1005×565 | **2,39×** | flou |

Quatre sur six sont flous. Constaté à l'œil sur le post 71 ouvert dans le
navigateur : visage baveux, détails écrasés.

### Ce qu'il faut faire

**a) Demander la pleine résolution au CDN.** L'URL publiée du post 71 est
`https://lede-admin.stereogum.com/wp-content/uploads/sites/64/2026/09/sheeran-gaza-speech.png?w=564`.
Le `?w=564` est le paramètre de redimensionnement du CDN WordPress, recopié tel
quel depuis la balise `og:image`. Mesuré le 2026-09-21 :

- telle quelle : 564×345 (50 Ko)
- sans le paramètre : **2156×1320** (386 Ko)

Dans `app/pipeline/illustrations.py`, là où l'`og:image` est extraite :
proposer la variante SANS les paramètres de largeur/hauteur et la préférer
seulement si elle est RÉELLEMENT plus grande — vérifier, ne pas supposer.
Paramètres connus des CDN d'éditeurs : `w`, `width`, `h`, `height`, `resize`,
`fit`, `s`. Un échec (404, timeout, image illisible) garde l'URL d'origine,
sans exception qui remonte. Le coût est d'au plus une requête réseau de plus
par article illustré, et seulement quand l'URL porte un de ces paramètres.

**b) Un plancher de résolution sur le fond de cover.** Le garde-fou existant
(`publication_crop_upscale_max`, 2,2×, dans `app/publish/images.py`) n'écarte
une image trop petite que pour les SLIDES du carrousel. Le fond de la cover ne
passe pas par là : il part directement dans Chromium, sans aucun plancher.
C'est ce trou qui a publié un 564×345 en 1080×1350.

Poser le plancher là où le fond est CHOISI, pas au gel — un fond écarté doit
faire descendre d'un barreau l'échelle existante (photo de l'actu non-affiche →
photo du compte source → photo d'un artiste suivi cité → web → `FOND_KINDA`),
exactement comme une affiche avérée le fait déjà (`illustration.eviter_l_affiche`).

Contraintes :
- le seuil est un RÉGLAGE (`app/config.py`), jamais un nombre en dur ;
- ne jamais rendre un fond `None` : le gel refuse un post sans fond, et la
  sentinelle `FOND_KINDA` existe précisément pour dire « on a cherché, rien ne
  convenait » ;
- une image dont on ne sait PAS mesurer la taille (réseau en panne, décodage en
  échec) est GARDÉE — même règle du doute que le juge visuel : une photo jetée
  par erreur appauvrit le post, et l'alternative ici est l'aplat Kinda ;
- le coût : mesurer une taille ne doit pas ajouter un téléchargement quand
  l'image est déjà copiée en local (`visuels_cache_dir`) ou déjà lue par le
  juge visuel. Regarder ce que `illustration.py` télécharge déjà avant
  d'ajouter la moindre requête.

### Vérification attendue

Des tests qui prouvent : une `og:image` portant `?w=564` fait bien demander la
variante sans paramètre et retient l'original quand il est plus grand ; elle
garde l'URL d'origine quand la variante échoue ou n'est pas plus grande ; un
fond trop petit fait descendre d'un barreau au lieu d'être publié ; un fond
dont la taille est inconnue est gardé ; et le bout en bout ne rend jamais un
fond `None`.

## Task 3 — Une actu déjà publiée ne ressort jamais

### Le trou, mesuré

`link_duplicates` (`app/pipeline/cluster.py`) ne compare que les articles d'une
fenêtre de **48 h** (`DEFAULT_WINDOW_HOURS`). Une actu re-couverte au-delà n'est
donc jamais rapprochée de celle qu'on a déjà postée, et rien d'autre ne
l'arrête : `_articles_publiables` (`app/schedule/store.py`) n'écarte que
l'article LUI-MÊME (`status != USED`, pas de créneau `PUBLIE`/`APERCU`), jamais
une reprise de la même actu par une autre source.

Or les articles `USED` ne sont JAMAIS purgés — c'est l'archive éditoriale,
dont `CLAUDE.md` dit qu'elle sert précisément à « ne pas reposter » (section
« Rétention »). Elle existe pour ça et on ne s'en sert pas. Décision de
l'utilisateur le 2026-09-21 : « il faut faire en sorte de ne pas sortir deux
fois la même info/actu même de sources différentes. »

### Ce qu'il faut faire

Comparer tout nouvel article aux articles **déjà publiés**, hors de la fenêtre
de 48 h, avec le MÊME rapprochement de titres et le MÊME seuil qu'aujourd'hui
(ne change ni `title_similarity_from_tokens`, ni `DEFAULT_THRESHOLD` : ils sont
éprouvés, 5 groupes sur 17 mélangent déjà presse et Instagram). Un nouvel
article qui atteint le seuil contre un article publié est lié comme doublon de
celui-ci — et il sort alors de la file tout seul, par le filtre
`duplicate_of_id IS NULL` qui existe déjà. Ne touche pas à `_articles_publiables`.

Trois contraintes :

- **Un représentant engagé ne change jamais.** `elire_representants`
  (`app/pipeline/election.py`) le garantit déjà pour `USED`, un `Post` ou un
  `Creneau` non `LIBRE`. Vérifie que la garantie tient quand le représentant
  est hors fenêtre — un article publié il y a cinq jours ne doit pas pouvoir
  être détrôné par un nouveau venu, sinon on publierait le nouveau.
- **Le coût ne doit pas croître sans fin.** L'archive grandit indéfiniment (18
  articles `USED` aujourd'hui, le plus ancien du 2026-09-15, mais dans un an il
  y en aura des milliers) et le rapprochement est quadratique. Borne la
  comparaison — par une ancienneté en jours, réglable dans `app/config.py`, et
  justifie la valeur par défaut que tu choisis. Une actu ne ressort
  raisonnablement pas après des mois ; mais elle ressort très bien après une
  semaine.
- **Idempotent**, comme le reste de `link_duplicates` : rejouer ne crée aucun
  lien supplémentaire, et un article déjà lié est laissé en place.

### Vérification attendue

Des tests qui prouvent : un article publié il y a cinq jours (donc hors de la
fenêtre de 48 h) capture bien une reprise d'aujourd'hui ; la reprise sort de
`candidats_eligibles` sans qu'on ait touché à cette fonction ; le représentant
publié n'est jamais détrôné par la reprise ; au-delà de la borne d'ancienneté,
plus aucun rapprochement ; et la fonction reste idempotente.

## Task 4 — Les visuels sont pris dans TOUT le groupe de doublons

### Pourquoi

Quand plusieurs sources couvrent la même actu, on n'en publie qu'une — le
représentant élu — et on ne regarde que SES médias. Les autres membres du
groupe sont pourtant là, avec leurs images : 21 articles liés comme doublons
aujourd'hui, dont **13 portent des médias**. On jette donc la moitié des
visuels disponibles sur les actus les mieux couvertes.

L'effet est direct sur le défaut mesuré en Task 2 : une capture Instagram de
564 px peut être remplacée par la photo de presse 2156 px du même événement,
publiée par une autre source du même groupe. Demande de l'utilisateur le
2026-09-21 : « croiser les sources si possible » → « piocher les visuels dans
tout le groupe ».

### Ce qu'il faut faire

Dans `app/content/choix_visuels.py`, le premier barreau de l'échelle (« les
médias propres de l'article ») considère les médias du représentant ET de tous
les membres de son groupe (`Article.duplicate_of_id == representant.id`).
Les barreaux suivants (compte source, artiste cité, web, `FOND_KINDA`) ne
changent pas, et l'ordre de l'échelle ne change pas.

Contraintes, toutes déjà en vigueur et à ne surtout pas contourner :

- `origine_interdite` s'applique **par membre**, pas une fois pour toutes : un
  membre dont la source est un compte Instagram de catégorie `media` ne prête
  jamais ses visuels (règle du 2026-09-17, `app/content/visuels.py`). C'est le
  piège principal de cette tâche : un groupe mixte presse + compte média
  contient les deux, et seul le second est interdit.
- Le juge visuel (affiche ou non) et le plafond d'une seule affiche en slide
  s'appliquent au lot élargi, sans appel supplémentaire par membre : le lot
  s'élargit, il ne se dédouble pas (c'est déjà la règle de `prejuger`).
- L'ordre de préférence reste celui de l'actu : les médias du représentant
  passent avant ceux de ses doublons, à qualité égale.
- Le crédit doit rester juste : si le visuel retenu vient d'un AUTRE membre que
  le représentant, c'est ce membre-là qui est crédité.

### Vérification attendue

Des tests qui prouvent : une photo portée par un doublon est retenue quand le
représentant n'a rien d'exploitable ; les médias du représentant passent en
premier à qualité égale ; un doublon dont la source est un compte Instagram
`media` ne prête RIEN ; le crédit suit bien le membre d'où vient le visuel ; et
un article sans doublon se comporte exactement comme avant.

## Task 5 — L'échelle du fond va jusqu'au bout, et à défaut on SAUTE le post

### La décision

Demande de l'utilisateur le 2026-09-21, qui fixe l'ordre et le dernier barreau :
« Dans l'ordre. Si jamais tu n'as pas une image de bonne qualité, aller chercher
l'image des artistes/festival/etc. sur Insta. Sinon chercher sur le web avec une
recherche adaptée. Sinon skip le post/reels. »

L'échelle existe déjà dans cet ordre exact. **Ce qui change, c'est le dernier
barreau** : `FOND_KINDA` (l'aplat sombre du gabarit) n'est plus une issue
acceptable. Un post dont la cover ne porte aucune vraie photo ne part pas.

C'est un revirement explicite par rapport à la règle du 2026-09-18 (« ne saute
pas l'actu, cherche une image sur le web en dernier recours » — le web restait
au-dessus de l'aplat Kinda, qui concluait l'échelle). Le web reste le dernier
recours AVANT le saut ; l'aplat n'est plus derrière lui.

### L'échelle, telle qu'elle doit se lire après cette tâche

1. une photo de l'ACTU elle-même : ni affiche, ni trop petite pour le plancher ;
2. une photo du COMPTE SOURCE sur Instagram (artiste, festival, label, club —
   jamais un compte de catégorie `media`) ;
3. une photo d'un ARTISTE SUIVI cité par l'actu, sur Instagram ;
4. une RECHERCHE WEB adaptée au sujet (`app/content/recherche_web.py`) ;
5. **on saute** : ce créneau ne publie pas cet article.

### Ce qu'il faut faire

- Le plancher de qualité (`cover_fond_upscale_max`) vaut à CHAQUE barreau, pas
  seulement au premier : une photo de réserve trop petite ne sauve pas plus le
  post qu'une photo d'actu trop petite.
- `FOND_KINDA` cesse d'être le fond retenu en bout d'échelle. Décide de son
  sort : soit la sentinelle disparaît, soit elle devient le signal interne
  « rien trouvé » que l'appelant traduit en saut. Ne laisse pas les deux
  significations coexister — c'est ce qui rendait la distinction fragile.
- **Le saut n'exclut PAS l'article définitivement.** Un article sans visuel
  aujourd'hui peut en avoir un demain (un compte publie une photo, la recherche
  web sort du cache au bout de `visuels_web_cache_jours`). Le créneau se libère
  et prend le candidat suivant ; l'article reste éligible. Il sera purgé par la
  rétention de 2 jours s'il ne trouve jamais rien — c'est la bonne fin, pas une
  exclusion posée à la main.
  À l'inverse, une panne de TRANSPORT (réseau, quota, copie en échec) reste ce
  qu'elle est aujourd'hui : un report (`VisuelsDifferes` / `PreparationDifferee`),
  jamais un saut.
- Le saut doit être VISIBLE : une raison lisible sur le créneau et au dashboard,
  et un compteur. Un feed qui se tait sans rien dire est le pire des deux mondes.

### Le risque à mesurer, et à rapporter

Mesuré sur les 6 derniers posts publiés : avec le plancher à 2,2×, trois fonds
sur six (2,39× / 3,52× / 3,91×) descendent d'un barreau. Or la rétention est à
2 jours, donc les réserves Instagram (barreaux 2 et 3) ne voient presque rien :
**la recherche web devient ce qui sépare « le post part » de « le post est
sauté »**. Compte, sur les articles réellement en base, combien de posts
seraient sautés avec cette règle, et dis-le dans ton rapport. Si le chiffre est
élevé, ne change pas la règle — signale-le, c'est une décision de l'utilisateur.

### Vérification attendue

Des tests qui prouvent : l'ordre des cinq barreaux ; le plancher appliqué à
chaque barreau, pas seulement au premier ; le saut quand les quatre premiers
échouent, avec sa raison lisible ; l'article NON exclu après un saut ; une
panne de transport qui donne toujours un report et jamais un saut ; et
qu'aucune cover ne peut plus partir sur l'aplat du gabarit.

## Task 6 — Agrandir le fond plutôt que sauter le post

### La décision

Demande de l'utilisateur le 2026-09-21, après le post 76 (l'ADE et Shlømo,
publié à 14:19) : fond de **660×440**, soit **3,07×** pour remplir le cadre, et
— vérifié — le CDN (`cdn.craft.cloud`, URL signée) rend exactement la même
image avec ou sans ses paramètres `fit=cover&width=660`. Il n'existe donc PAS
de version plus grande à aller chercher : ni la variante pleine résolution de
la Task 2, ni aucune réserve ne sauve ce post.

D'où un barreau de plus, **juste avant le saut de la Task 5** :

photo de l'actu → compte source → artiste cité → recherche web →
**agrandissement du meilleur candidat** → saut

### La règle qui borne tout le reste : le FOND SEULEMENT, jamais une slide

La super-résolution n'agrandit pas, elle **invente** le détail manquant : sur
une photo de presse, le modèle fabrique de la texture de peau et de cheveux qui
n'était pas dans le fichier, et ça se voit d'abord sur les visages.

Le fond de cover est un élément décoratif, recouvert d'un voile sombre et du
texte de l'accroche : du détail inventé y est invisible et sans conséquence.
Une slide est une image DOCUMENTAIRE de l'actu : on ne la retouche pas.
**L'agrandissement ne touche donc que `slides[0].image_url`, jamais un média du
carrousel, jamais la vidéo d'un reel.** Un test-sentinelle doit échouer si
quelqu'un branche un jour l'agrandissement ailleurs.

### L'outil

`Real-ESRGAN-ncnn-vulkan` (https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan) :
binaire autonome en C++, aucune dépendance Python, GPU via Vulkan. Même patron
que `ffmpeg`, `cloudflared` et `yt-dlp` — un exécutable **cherché hors du PATH
de launchd** (le piège est déjà documenté dans `CLAUDE.md`, reprends la même
mécanique de découverte plutôt que d'en écrire une autre).

Vérifie avant de t'engager que les binaires macOS arm64 sont utilisables : ceux
du dépôt officiel datent de 2022. https://github.com/upscayl/upscayl empaquette
les mêmes modèles ncnn et est plus activement maintenu. Rapporte ce que tu as
retenu et pourquoi.

### Contraintes

- **Binaire absent = panne machine, jamais une exclusion d'article.** Exactement
  comme `ffmpeg` : l'absence de l'outil ne doit ni exclure, ni sauter, ni faire
  publier une bouillie — elle fait retomber sur le comportement d'avant (le
  candidat tel quel, ou le saut de la Task 5 si le plancher le refuse).
- **On n'agrandit que ce qui en vaut la peine.** Un modèle 4× sur un 660×440
  donne 2640×1760, largement au-dessus du plancher. Mais agrandir un 200×150
  ne produira jamais une image publiable : fixe une taille source minimale,
  réglable, et justifie-la.
- **Le résultat repasse par le plancher** (`cover_fond_upscale_max`). Si
  l'agrandissement ne suffit pas, on saute — l'outil ne doit pas devenir un
  moyen de contourner la règle de qualité.
- **Le coût est borné** : l'agrandissement a lieu au GEL (T-2h), jamais à la
  composition ni à l'ingestion, au plus une fois par post, avec un délai
  maximum au-delà duquel on abandonne (même esprit que les budgets de
  `telecharger_video`). Mesure le temps réel sur cette machine et mets-le dans
  ton rapport.
- Le fichier agrandi va dans `visuels_cache_dir` comme les autres copies
  locales, et suit la même purge.

### Vérification attendue

Des tests qui prouvent : l'agrandissement n'est tenté qu'après l'échec des
quatre premiers barreaux ; il ne touche QUE le fond de cover (sentinelle) ; un
binaire absent ne casse rien et n'exclut rien ; une source trop petite n'est pas
agrandie ; un résultat encore sous le plancher mène au saut et non à une
publication ; et le fichier produit est bien copié dans le cache des visuels.

## Task 7 — Claude tranche les reprises douteuses (à faire AVANT les Tasks 5 et 6)

### Le constat qui la motive, mesuré

Fusionné sur `main` le 2026-09-21 (`6f6a6d3`) : le rapprochement d'un article neuf
contre l'archive des articles publiés (`app/pipeline/cluster.py`) utilise une
mesure stricte, `archive_similarity_from_tokens` (mots partagés rapportés au titre
le plus LONG), seuil `cluster_archive_seuil` = 0,8. La re-revue a testé 15
reprises plausibles d'une même actu : **11 passent entre les mailles**. Au seuil
0,8, un titre de 4 à 6 mots porteurs ne tolère qu'un mot différent.

Aucun seuil ne peut faire mieux, dans aucun sens :
- **assez souple pour attraper les reprises**, il enterre des actus neuves — le
  coefficient de recouvrement donne **1,00** à « Charlotte de Witte annonce un
  nouvel EP » contre « … annonce une tournée mondiale » ;
- **assez strict pour les épargner**, il laisse passer « … annonce son premier
  album » contre « … premier album, 'Apex', attendu le 12 décembre chez KNTXT »
  (0,50), « Justice cancel » contre « Justice annule » (0,60), etc.

La décision entre « même actu » et « actu différente » n'est pas une propriété
des mots du titre. Demande de l'utilisateur le 2026-09-21 : « ajoute l'arbitrage
par Claude ».

### Ce qu'il faut faire

Trois bandes, selon les deux mesures déjà en place contre un article d'archive :
- **certain** : `archive_similarity_from_tokens` ≥ `cluster_archive_seuil` (0,8)
  → lié sans demander, comme aujourd'hui ;
- **douteux** : sous ce seuil, mais `title_similarity_from_tokens` (le
  recouvrement) ≥ `DEFAULT_THRESHOLD` (0,65) → **Claude tranche** ;
- **différent** : sous les deux → aucun lien, aucun appel.

C'est exactement la bande où les deux mesures se contredisent, et c'est là que
les 11 reprises ratées ET les 3 faux positifs tombent tous.

Claude reçoit les deux titres **et leurs résumés** (`Article.summary`, réécrit
par le scoring en français : c'est lui qui porte le fait, le titre seul ne suffit
pas) et rend une sortie structurée — suis le motif `messages.parse()` déjà en
place dans `app/pipeline/scoring.py`, ne réinvente pas l'appel.

**Lis le skill `claude-api` avant d'écrire la moindre ligne qui touche l'API
Anthropic** (invoque-le avec l'outil Skill) : identifiants de modèle, sorties
structurées, gestion d'erreurs.

### Contraintes

- **Le modèle** : réutilise `settings.scoring_model` (défaut `claude-haiku-4-5`,
  choix validé par l'utilisateur pour le coût). Pas de nouveau modèle.
- **Le verdict est mémorisé par PAIRE** (les deux ids), en base ou dans un
  fichier à côté du jeton comme les autres caches du projet — tranche et
  justifie. Une paire n'est JAMAIS redemandée. C'est aussi ce qui répare la
  procédure documentée fausse (voir plus bas).
- **Le coût est borné** : un plafond d'appels par passage, réglable, et
  `run_veille` (toutes les 5 minutes) ne doit PAS arbitrer — laisse ça au
  passage horaire. Mesure les jetons réels d'un appel et rapporte le coût
  quotidien estimé.
- **Le doute favorise la publication.** Pas de clé API, crédits épuisés, réseau
  mort, réponse hors schéma → **pas de lien**, jamais un lien par défaut. Une
  reprise publiée se voit et se corrige ; une actu neuve enterrée ne laisse
  aucune trace. Même principe que le reste du dossier C-2.
- **Une panne n'est pas mémorisée comme un verdict** : seule une vraie réponse
  de Claude l'est, sinon une panne passagère figerait « différent » pour toujours.
- **Observabilité** : les liens posés par Claude sont comptés à part des liens
  « certains » (`ClusterStats`), et la trace sur l'article dit que c'est Claude
  qui a tranché, avec sa raison.

### La réparation manuelle doit enfin tenir

La re-revue a prouvé que la procédure de `CLAUDE.md` (« effacer les deux champs
`duplicate_of_id` et `file_exclu_raison` ») est FAUSSE : le passage suivant de
`link_duplicates` relie l'article tant qu'il est dans la fenêtre de 48 h, c'est-
à-dire pendant toute sa vie publiable. Avec des verdicts mémorisés par paire, un
utilisateur qui défait un lien doit pouvoir enregistrer « ces deux-là sont
différents », et ce verdict doit primer sur tout rapprochement ultérieur. Donne
un moyen concret de le faire (un script dans `scripts/`, sur le modèle des
autres, ou un endpoint de l'API) et documente-le.

### `CLAUDE.md` doit dire la vérité

Réécris la section du rapprochement d'archive : ce que le code fait vraiment,
les trois bandes, ce que coûte l'arbitrage, et la procédure de réparation qui
fonctionne. Le titre « Une actu déjà publiée ne ressort jamais » promet plus que
le code : ajuste-le à ce que tu livres.

### Vérification attendue

Des tests qui prouvent : les trois bandes (certain lié sans appel, douteux
tranché, différent jamais demandé) ; les 3 faux positifs mesurés ne sont JAMAIS
liés quand Claude dit « différent » ; une reprise mesurée ratée aujourd'hui est
liée quand Claude dit « même » ; une paire n'est jamais redemandée ; une panne
de l'API ne crée aucun lien et n'est pas mémorisée ; `run_veille` n'arbitre pas ;
le plafond par passage tient ; et un verdict manuel « différent » survit aux
passages suivants.
