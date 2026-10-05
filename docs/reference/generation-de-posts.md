# Generation de posts (`app/content/`)

**Quand lire ce fichier** : avant de toucher a `app/content/` — composition
(`compose.py`), mise en page (`layout.py`), rendu Chromium (`render.py`), video
et reel (`video.py`), illustration et juge visuel (`illustration.py`),
agrandissement (`upscale.py`), legende (`legende.py`), export. Il porte
l'echelle de choix du FOND de cover (et pourquoi elle se termine par un SAUT du
creneau, jamais par un aplat Kinda), les cinq controles de forme non bloquants
de la legende et des covers, et les trois pieges ffmpeg.

Ou chercher les visuels, et la regle « jamais les visuels d'un compte media » :
`visuels.md`. Ce qui part reellement chez Meta : `publication.md`.

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
formats — `template` sur le post : `kinda_post` (1080×1350, POST, toujours
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
test-sentinelle par gabarit (`tests/content/test_render.py`) échoue si une valeur
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

**La qualité d'encodage est un RÉGLAGE, et elle est partagée** (2026-10-02,
`encodage_meta()` dans `app/content/video.py`, réglages dans `app/config.py`).
`ENCODAGE_META` ne portait ni `-crf` ni `-b:v` : x264 appliquait donc son défaut
de distribution web, `crf 23`, que personne n'avait choisi. Or la perte est
**composée** — Instagram encode quand l'artiste publie, Kinda réencode,
Instagram réencode une troisième fois à la réception : on lui donne la meilleure
source possible. Mesuré sur un vrai reel du pipeline (1080×1920, 23,8 s,
4,3 Mbit/s), VMAF contre la source : `crf 23` + audio 128k → 11,80 Mo / 96,81 ;
**`crf 20` + audio 192k → 14,22 Mo / 97,56** (+0,75, retenu, et à temps
d'encodage égal — à CRF explicite x264 cherche moins) ; `crf 18` preset `slow` →
15,78 Mo / 98,00 (+1,28, trop cher). 192k parce que c'est de la musique.
`encodage_meta()` est partagé par le reel et la vidéo de carrousel : le réglage
vaut pour les deux.

**Baisser le CRF réduit la marge de POIDS, et c'est la seule propriété dont la
violation coûte une ACTU.** Au-delà de `publication_reel_max_mo` (100),
`preparation_reel.verifier_sortie` lève `PreparationImpossible`, qui **exclut
l'article définitivement** de la file (voir « Exclusion »). Le garde-fou est donc
explicite : `video._encoder_sous_plafond` **réencode** une sortie trop lourde
sous un débit moyen calculé depuis le poids visé et la durée MESURÉE sur la
sortie (jamais déduite de l'entrée : la cover ajoute ses frames devant). Deux
mesures l'ont façonné, et chacune a cassé une version plus simple. (1) Le
« capped CRF » (`-crf` + `-maxrate`) **ne tient pas** : le VBV borne une fenêtre
de `bufsize` bits, qui sur une vidéo courte couvre tout le fichier — 22,7 Mo
pour un plafond de 16,6 Mo. D'où `-b:v`, qui vise la moyenne sur toute la durée,
c'est-à-dire exactement la question posée. (2) x264 **dépasse** sa consigne de
~26 % sur un clip court (son contrôle de débit ne converge pas en 60 frames) :
d'où une correction proportionnelle au dépassement CONSTATÉ, au plus
`encodage_reprises_max` (3) fois. Un garde-fou qui se vérifie, pas un calcul
auquel on fait confiance. La dégradation ne touche donc que les passages qui,
sinon, seraient perdus — le cas nominal ne voit jamais ce second encodage. Et le
garde-fou épuisé **rend quand même la sortie** : il ne remplace jamais un
dépassement par une exception, `verifier_sortie` tranche comme avant.

Limite mesurée et assumée : une vidéo assez pathologique pour franchir le
plafond à 90 s (du bruit pur) franchit D'ABORD `DELAI_MAX_S` (300 s) au premier
encodage — ce qui lève `subprocess.TimeoutExpired`, donc `PreparationDifferee`
(on retente), jamais une exclusion. La borne de temps échoue du bon côté ; c'est
la borne de poids qui échouait du mauvais, et c'est elle qu'on a traitée.

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

**La teinte suit l'actu : rose par défaut, VERTE pour l'écologie** (demande de
l'utilisateur, 2026-09-18). Une seule couleur gouverne le voile bas du post, le
`<mark>` des deux gabarits et la bande de titre du reel — d'où `Palette` dans
`layout.py`, qui porte un RGB décliné en opacités et non trois chaînes. Le vert
`#35C77A` est **calé sur la LUMINANCE du rose** `#FD8BC3` (0.430 contre 0.433,
soit 2.10:1 contre 2.09:1 face au texte crème `#FFF7FA`) : le contraste perçu du
design ne bouge pas d'une actu à l'autre, seule la couleur change. Un vert plus
sombre lirait mieux sous le texte, mais relever le contraste des DEUX teintes est
une décision de direction artistique, pas un effet de bord — un test-sentinelle
(`tests/content/test_layout.py`) échoue si quelqu'un déplace la couleur à l'œil.

Le signal est la catégorie de scoring **`ecology`**, ajoutée le même jour. Aucune
migration : `articles.category` est un `VARCHAR(100)` **sans contrainte CHECK**
(migration 0001), et l'API `/articles` filtre sur la chaîne libre — un test
vérifie que chaque valeur de `Category` a son option au dashboard, sinon une
catégorie produite par le scoring resterait invisible. La règle du prompt exige
que l'écologie soit LE SUJET (festival qui verdit son édition, prise de position
climat, tournée durable, menace climatique sur un événement) : une affiche verte,
une photo de nature ou un nom de label évoquant la nature ne suffisent pas. Elle
prime sur `event_festival` — l'édition qui passe au renouvelable est `ecology`,
son line-up reste `event_festival`.

Le nom de palette est **figé dans `posts.variants["palette"]`** à la composition
(`app/content/teinte.py`), pas relu depuis l'article : l'article est purgé à 14
jours et le post doit rester rendable sans lui, même motif que ses champs
dénormalisés. Le champ est donc aussi le point où le dashboard pourra forcer une
teinte sans re-scorer. Conséquence câblée : `PATCH /posts/{id}` écrit `variants`
en FUSION (`{**post.variants, ...}`) et non en bloc — l'écrire en bloc ferait
disparaître la palette au premier choix de variante.

**Le fond de la cover est une ILLUSTRATION, jamais une affiche**
(`app/content/illustration.py`, 2026-09-18). C'était la première image
exploitable ; or une affiche de line-up est exactement ce qu'un compte de
festival publie en tête de carrousel, et superposer l'accroche Kinda à un visuel
déjà couvert de texte donne une cover illisible et redondante. Une affiche ne se
reconnaît ni à son ratio (souvent 4:5, comme une photo de concert), ni à son URL,
ni à ses métadonnées : elle se reconnaît à ce qu'on y VOIT, d'où un modèle de
vision (`visuels_model`, défaut Haiku) qui rend un verdict strict par image
(`{affiche, texte_incruste, sujet: scene|portrait|autre, note}`).

**Et jamais non plus un visuel DÉJÀ COUVERT DE TEXTE** (`texte_incruste`,
2026-10-01). Post du 28/09 (Dimitri Vegas & Like Mike) : le fond était une
frame de story portant sa légende tapée par-dessus (« Much love to that one
person watching my show on FaceTime. Hope to see you soon in Ibiza »), et
l'accroche Kinda venait se poser dessus — deux textes sur la même cover, un
repost avec une bannière collée devant, le rôle d'intermédiaire dont le projet
cherche à sortir. Le juge répondait `affiche: false`, **et il avait raison** :
c'est bien une photographie prise sur le vif. D'où un CHAMP DE PLUS plutôt
qu'une définition d'`affiche` élargie jusqu'à devenir fausse. Le piège est de
viser trop large : presque toute photo porte un peu de texte (logo sur un
t-shirt, enseigne au fond, banderole, écran LED), et un critère qui les prend
toutes viderait les covers — la formulation vise donc le texte **ajouté
par-dessus** (légende de story, sous-titres, bandeau, typo posée à l'export),
assez gros pour entrer en concurrence avec l'accroche, jamais le texte qui
appartient à la scène photographiée ; « dans le doute, réponds false ».
Mesure (sur les 70 images non-affiche encore présentes dans
`illustration_cache_path`, relues à l'œil faute de crédits pour re-juger) :
**zéro** bascule — aucune photo de concert ou de portrait ordinaire n'est
touchée, y compris une banderole « FREE PARTY IS NOT A CRIME » et une enseigne
de pharmacie ; le fond du post 134 (720×1280, légende sur trois lignes) est le
cas positif.
`Verdict.inexploitable_en_fond` (`affiche or texte_incruste`) est le seul point
de lecture : les deux défauts posent le même problème et appellent la même
réponse, descendre l'échelle (compte source → artiste cité → web →
agrandissement → saut). **En SLIDE rien ne change** : `plafonner_affiches` ne
lit QUE `affiche` — sous l'accroche la frame de story est fautive, dans le
carrousel elle montre l'actu (test-sentinelle). Zéro appel de plus : c'est un
champ du même verdict, dans le même appel. Le cache passe en **version 2** et
ses verdicts de version 1 sont **jetés** (ils ne disent rien du nouveau critère,
et les relire comme « pas de texte » laisserait passer le défaut pendant
`illustration_cache_jours`) ; ses TAILLES sont gardées, elles ne dépendent
d'aucun critère du juge. Le champ porte un défaut `False` côté Python (règle du
doute, et un verdict ancien reste valide) mais est **remis dans le `required`
du schéma JSON** envoyé au modèle (`Verdicts.model_json_schema`) : un champ
facultatif est un champ que le modèle peut omettre, et le critère serait alors
inerte.

Trois règles de coût : on ne juge **que s'il y a un choix** (au moins deux
candidats — payer pour confirmer l'unique image disponible ne changerait rien) ;
au plus 3 images **réduites à 512 px** dans UN appel, le coût en jetons suivant
la SURFACE (une photo de presse en 2000×1333 coûte ~3 550 jetons, sa vignette
~230) ; et le verdict est **mémorisé par URL** dans `illustration_cache_path`,
donc une recomposition ne repaie rien. Total mesuré : ~1 400 jetons d'entrée et
un appel par composition, soit ~0,002 $.

Et une règle de robustesse qui prime : ce module **ne lève jamais**. Pas de clé
API, vision coupée (`ILLUSTRATION_VISION_ENABLED=false`), réseau mort, JSON hors
schéma, image illisible → il rend `None` et l'appelant garde la première image.
Un verdict manquant vaut « neutre », qui passe devant une affiche avérée et
derrière une photo reconnue : ne pas savoir vaut mieux que savoir que c'est une
affiche. Le juge n'est payé qu'à UN endroit, `create_post`, donc la cover reel
suit la même règle sans code en double.

**Le fond doit aussi être ASSEZ GRAND, pas seulement pas une affiche**
(2026-09-21). Relevé en ouvrant le compte : 4 des 6 derniers posts publiés
avaient un fond flou (post 71, visage baveux à l'œil nu) — l'`og:image`
retenue mesurait 564×345, agrandie 3,91× pour remplir 1080×1350. Rien ne
mesurait jamais la résolution avant de rendre. `illustration.eviter_l_affiche`
reçoit désormais `cible` (la taille du gabarit publié, fournie par
`create_post` depuis `template` — 1080×1350 en post, 1080×1920 en reel, un
reel étant plus exigeant sur la même photo) et traite un fond trop petit
EXACTEMENT comme une affiche avérée : il descend d'un barreau vers les mêmes
réserves — **web compris** —, et à défaut (depuis la Tâche 5, voir plus bas)
**le créneau saute** plutôt que de partir sur `FOND_KINDA`. Mais AVANT de
descendre, le premier barreau **balaye ses propres candidats par taille**
(`candidats`, tout le groupe de doublons) : `choisir_illustration` ne classe
que sur le verdict affiche/photo, et sans ce balayage la photo de presse 2 156 px
d'un doublon restait en slide pendant que la capture de 564 px du représentant
envoyait la cover vers les réserves (revue du 2026-09-21).

Seuil : `cover_fond_upscale_max` = **2,2×** (`app/config.py`), la MÊME valeur et
le même raisonnement que `publication_crop_upscale_max` (voir « Publication
automatique ») : laisser passer l'`og:image` standard de 1200×630 (2,14× pour
remplir 1080×1350), de loin l'illustration la plus fréquente du pipeline. Un
premier seuil à 1,5× la rejetait : la plupart des posts de presse seraient
partis sur l'aplat Kinda, et un article dont les slides étaient par ailleurs
écartées finissait en `PreparationImpossible`, donc exclu pour toujours. On ne
dégrade jamais le cas nominal pour attraper un cas limite. Assumé : le fond du
post 62 (720×900 en reel, 2,13×) passe, à un centième du seuil ; les trois
autres fonds flous mesurés (2,39×, 3,52×, 3,91×) sont attrapés. Une taille non
mesurable est GARDÉE — même règle du doute que le juge d'affiches. La mesure ne coûte rien de plus : les
dimensions réelles sont lues au moment où le juge visuel décode déjà l'image
pour la réduire à 512 px, et mémorisées dans le même `illustration_cache_path`
(`Cache.tailles`, à côté des verdicts).

Et l'`og:image` elle-même peut être en cause : certains CDN d'éditeurs
(WordPress) recopient le paramètre de redimensionnement de la page dans
`og:image` — mesuré sur le post 71, `?w=564` rendait 564×345 (50 Ko) là où la
même image sans ce paramètre pèse 386 Ko en 2156×1320. `app/pipeline/
illustrations.py::image_de_partage` retente donc, quand l'URL porte un
paramètre connu (`w`, `width`, `h`, `height`, `resize`, `fit`, `s`), la
variante sans ce paramètre — et ne la retient que si elle est RÉELLEMENT plus
grande une fois téléchargée et décodée (jamais déduit de l'URL, un éditeur
peut ignorer le paramètre). Mémorisé par URL dans
`illustration_variante_cache_path` : un article dont l'`og:image` ne change
pas ne repaie pas cette vérification à chaque passage.

**Et si tout est une affiche, on cherche ailleurs** (2026-09-18,
`illustration.eviter_l_affiche` +
`choix_visuels.{photos_du_compte,photos_des_artistes_cites}`). Post 40 puis 41
(Transmusicales) : cinq visuels, cinq affiches de line-up, et le compte ne
publiait que des programmes. La première version gardait « la moins mauvaise des
affiches » quand rien de mieux ne se trouvait, ce qui a donné trois
republications du même défaut (« ya toujours l'affiche en fond !! »). La règle
est donc **absolue** : une affiche avérée n'est jamais publiée en fond. À
l'époque (avant la Tâche 5, 2026-09-21), à défaut de photo, la cover partait sur
`FOND_KINDA` — l'aplat sombre du gabarit (`assets/fallback.jpg`), celui pour
lequel la typo a été dessinée, donc lisible. **Ce n'est plus le cas : à défaut
de photo, désormais, le créneau SAUTE** (voir le paragraphe suivant).

`FOND_KINDA` (`kinda:fond`, dans `app/content/visuels.py`) est une **sentinelle,
pas une URL** : un fond `None` veut dire « ce post n'a pas de visuel », et
`FOND_KINDA` veut dire « on a cherché partout, rien ne convient ». Le schéma
`kinda:` n'est ni http(s) ni `file://` : aucune requête, aucun fichier lu.

`FOND_KINDA` n'est plus une issue PUBLIABLE — seulement le signal interne que
`eviter_l_affiche` a parcouru toute l'échelle sans rien trouver. Demande de
l'utilisateur : « Dans l'ordre. […] Sinon skip le post/reels. »
`compose.resoudre_fond` (appelé par `create_post` ET, en amont, par
`app/schedule/runner.py::_composer` AVANT l'appel payé à Sonnet) lève
`VisuelIntrouvable` quand `eviter_l_affiche` rend `FOND_KINDA` : **aucun post
n'est plus jamais créé avec cet aplat en fond.** Dans le planificateur, cette
exception SAUTE le créneau (`_liberer`, jamais `_exclure`) — l'article n'est
PAS `file_exclu_raison`, il reste éligible et repartira dès qu'un barreau
donnera mieux (nouveau post du compte source, ou la recherche web qui ressort
du cache après `visuels_web_cache_jours`) ; c'est la purge à `retention_days`
(2 j) qui conclut s'il ne trouve jamais rien, jamais une exclusion posée à la
main. `resoudre_fond` calcule le choix EXACTEMENT une fois par créneau (les
réserves compte/artiste/web sont des lots paresseux qui lisent le réseau dès
qu'on les ouvre) : son résultat voyage jusqu'à `create_post` par le paramètre
`fond_precalcule`, pour ne jamais rouvrir une réserve deux fois. Un brouillon
composé quand l'aplat Kinda était encore une issue publiable et qui porte
encore `FOND_KINDA` en fond est traité comme périmé
(`runner._brouillon_perime`) : la composition suivante refait tourner
l'échelle au complet plutôt que de le geler tel quel. Le gel
(`app/publish/preparation.py::verifier_fond`) continue d'accepter `FOND_KINDA`
sur un tel brouillon ancien — filet de compatibilité, jamais un chemin qu'une
composition neuve peut encore emprunter. Même filet côté publication : un
créneau déjà GELÉ dont le post porte encore `FOND_KINDA` en fond est REFUSÉ
par `runner._publier` plutôt que publié — le créneau se libère (pas
d'exclusion), l'article se met à l'écart, dossier figé effacé.

**Un article sans bonne image faisait taire le compte TOUTE LA JOURNÉE.** Le
saut libère le créneau et relance l'arbitrage, mais l'arbitrage classe par
score et l'article reste éligible : s'il a le meilleur score, il revenait sur
le créneau le plus proche, sautait encore, jusqu'à MANQUE, puis glissait au
créneau suivant — mesuré : 3 créneaux du jour en MANQUE, zéro publication,
pour un seul article de 95 de pertinence sans visuel face à quatre bons
articles à 67-70. `app/schedule/pause_visuels.py::marquer_saute` met donc
l'article sauté À L'ÉCART TEMPORAIREMENT (`visuel_saute_pause_minutes`, 180 =
3 h par défaut — la moitié du cache du lecteur de comptes, 6 h, pour retenter
dès qu'une chance raisonnable existe sans laisser le compte silencieux une
demi-journée ; ni une exclusion — la spec l'interdit, l'article peut avoir une
photo demain — ni un délai trop long — la rétention est à 2 jours). Fichier
d'état à côté du jeton (`visuel_saute.json`), même motif que
`quota_graph.json` : écriture atomique, temporaire nommé par processus, purge
des entrées échues à chaque écriture, aucune écriture en LECTURE.
`store._articles_publiables` (le socle commun à `candidats_eligibles`,
`appliquer_arbitrage` et `offre_reels`) exclut les articles en pause. Le point
d'entrée est unique (`marquer_saute`) : la Task 6, qui déplacera une partie
des sauts au GEL, l'appelle de la même façon.

Un créneau réattribué à un NOUVEL article gardait l'ancienne `erreur`
(« visuel introuvable : … ») jusqu'à ce qu'une composition réussisse —
trompeur au dashboard. `appliquer_arbitrage` l'efface dès que `article_id`
change.

**Une panne de transport dans une RÉSERVE (compte, artiste, web) ne doit
jamais être confondue avec « rien trouvé ».** Avant que `FOND_KINDA` cesse
d'être publiable, les deux donnaient l'aplat Kinda — une issue sûre. Une fois
l'aplat devenu un SAUT, cette confusion faisait sauter un article sur une
simple coupure réseau, avec une raison fausse. Détail dans le paragraphe qui
suit (« Une réserve EN PANNE… »).

Le même défaut existe pour un REPORT (`VisuelsDifferes`) qui ne guérit
jamais : sans borne, le créneau n'est ni libéré ni l'article mis à l'écart, et
l'article campe dessus jusqu'à MANQUE au lieu qu'un autre en profite — mesuré
sur le même scénario (une réserve qui lève `VisuelDiffere` à chaque passage
au lieu d'un « rien trouvé ») : **1 publication sur 4, 3 MANQUE, 0 mise à
l'écart.** Un report reste le bon geste (retenter EST la réponse à une panne
de transport), mais pas éternellement : `pause_visuels.signaler_report`
compte les reports CONSÉCUTIFS par article et annonce le franchissement de
`visuel_differe_max_reports` (7 par défaut — calé sur la pause de quota Graph
à la composition, 60 min, rapportée à la cadence de publication, 10 min : le
1er report tombe au 1er passage en panne, le 7e tombe donc après 6
intervalles de 10 min, soit exactement les 60 min de la pause de quota,
assez pour la laisser se résorber TOUTE SEULE avant d'abandonner).
`reinitialiser_report` casse le compteur dès qu'une composition réussit :
seule une SUITE ininterrompue de reports compte. Visible via
`RapportPublication.reportes`, distinct de `sautes`, dans le log de
`run_publication`, même quand il reste sous le seuil.

`signaler_report` ne pose PAS elle-même la mise à l'écart : c'est à
l'appelant (`runner._differer`) de LIBÉRER le créneau d'abord, et de
n'appeler `marquer_saute` (et de ne compter le report comme converti) QUE SI
cette libération réussit — le même invariant d'ordre que la ceinture de
`_publier` : `_liberer` est une écriture CONDITIONNELLE qui peut échouer si le
dashboard est passé entre-temps, et poser la mise à l'écart avant de le savoir
infligerait une pause de plusieurs heures pour un créneau qu'on n'a même pas
réussi à retirer à l'article.

`Fond` ne transporte jamais une affiche avérée (ou une image trop petite)
dans `url` quand `differe=True` : `eviter_l_affiche` rend
`Fond(None, origine, differe=True)`, jamais l'URL fautive — sûr par
CONSTRUCTION, pas seulement parce que `compose.resoudre_fond` teste `differe`
avant `origine == ORIGINE_KINDA`.

Une horloge naïve ne fait plus tomber le planificateur : `pause_visuels.
_pause_valide` compare `maintenant < jusqu_a` dans un `try/except TypeError`
— un horodatage naïf sur le disque (corruption, bug amont) vaut désormais
« pause périmée », jamais une exception qui ferait tomber
`store._articles_publiables`, donc l'arbitrage, `candidats_eligibles` ET
`offre_reels`. Les compteurs de reports, eux, sont purgés par ANCIENNETÉ (un
compteur sans nouvelle entrée depuis plus longtemps que `visuel_saute_
pause_minutes` n'est plus une suite consécutive de quoi que ce soit) — sans
cette purge, des dizaines de compteurs abandonnés restaient sur le disque
indéfiniment.

`pause_visuels._fusionner_et_ecrire` relit le disque À L'INSTANT DE
L'ÉCRITURE et fusionne, exactement comme
`app.pipeline.cluster_arbitrage.fusionner_et_ecrire` (Tâche 7) — c'est
l'invariant du fichier d'état : un second agent qui pose sa propre pause
entre notre lecture et notre écriture ne doit jamais se faire écraser.

Deux **réserves**, ouvertes dans l'ordre et seulement tant qu'aucune n'a donné :
1. le compte qui a publié l'actu (jamais un `categorie=media`) ;
2. les artistes suivis **cités** par l'actu — un line-up cite des artistes qu'on
   suit, et le portrait de l'un d'eux vaut mieux qu'une grille de noms.

« Chercher plus loin » s'est traduit par trois réglages : `fond_secours_max_photos`
passé de 2 à **6** par réserve (deux posts d'un compte de festival en saison, ce
sont deux affiches de plus) ; une fenêtre propre au fond,
`fond_secours_fenetre_jours` = **30 j** au lieu des 10 j des visuels de carrousel
(un visuel de carrousel doit ILLUSTRER l'actu, un fond n'a qu'à être une belle
photo du bon compte) — **mais bornée par la purge** : ces réserves lisent la
table `articles`, que `retention_days` (**2** depuis le 2026-09-21) vide au-delà
de 2 jours. Tant que la rétention reste courte, les 30 j (et les 10 j des
visuels d'artistes) sont décoratifs et les réserves « compte source » et
« artiste cité » sont quasi vides — hors lecture en direct du compte source.
Décision de l'utilisateur : c'est la **recherche web**, seul barreau qui ne lit
pas la base, qui reprend le relais, pour une affiche comme pour un fond trop
petit (verrouillé de bout en bout dans `tests/content/test_visuels_web.py`). Les valeurs
sont gardées : elles redeviendront utiles si la rétention remonte ; et une lecture `business_discovery` en direct **même si le
pipeline vient d'ingérer le compte** (`LecteurComptes(..., ignorer_fraicheur=True)`).
Cette garde-là est une économie — « la base suffit » — et ici la base ne suffit
justement pas. Les **plafonds durs** du lecteur, eux, tiennent toujours :
2 lectures/passage, 24/jour, cache 6 h, pause sur le quota Graph.

Les photos sont COPIÉES en local (les URL signées du CDN expirent avant le gel) et
passent devant le **même juge, avec le même cache par URL**. Un candidat qu'on n'a
pas su juger n'est jamais retenu : contrairement au classement de
`choisir_illustration`, où l'inconnu passe devant une affiche avérée, ici
l'alternative est sûre (depuis la Tâche 5, un SAUT plutôt que l'aplat Kinda) —
on ne lui préfère qu'une image dont le juge a dit qu'elle n'est PAS une
affiche. Seul un juge muet **sur le fond lui-même** rend le comportement
d'avant (on ne sait rien, donc on ne touche à rien) — c'est aussi ce qui se
passe avec `ILLUSTRATION_VISION_ENABLED=false` : cette coupure est un RÉGLAGE
délibéré, jamais une panne, et ne doit jamais faire sauter ni reporter un
créneau à elle seule.

**Une réserve EN PANNE de transport (quota Graph, réseau, budget épuisé) n'est
plus confondue avec une réserve qui a réellement regardé et n'a rien trouvé.**
Avant cette distinction, `eviter_l_affiche` retombait sur `FOND_KINDA` dans
les deux cas, ce qui était sûr tant que l'aplat restait publiable ; devenu un
SAUT, la confusion faisait sauter un article sur une simple coupure réseau,
avec une raison fausse (« aucune image… ni sur le web » quand le web n'avait
jamais répondu). Les lots (`Lot.photos`) et `recherche_web.candidats_du_web`
lèvent désormais `app.content.visuels.VisuelDiffere` quand ils n'ont PAS PU
regarder ; `eviter_l_affiche` la capture par barreau (elle reste dans son
invariant « ne lève jamais » : c'est `Fond.differe`, pas une exception, qui en
sort) et, si aucun barreau qui a pu regarder n'a rien donné, rend
`Fond(None, origine, differe=True)` au lieu de l'aplat — `url` ne porte jamais
l'image fautive (affiche ou trop petite), sûr par CONSTRUCTION.
`compose.resoudre_fond` traduit ce `differe` en `VisuelsDifferes` (un REPORT,
comme une panne de lecture
Instagram) plutôt qu'en `VisuelIntrouvable` (un SAUT) — le créneau retente au
passage suivant, sans se mettre en pause. Si un barreau plus loin réussit
malgré la panne d'un barreau précédent, la panne n'a aucune conséquence : le
premier qui donne une vraie photo gagne, comme avant.

Coût : ce chemin **ne coûte rien** tant que le fond retenu n'est pas une affiche
avérée — ni lecture de compte, ni téléchargement, ni jeton. Les réserves voyagent
depuis `choisir_visuels` (où vivent la session et le budget du passage) jusqu'à
`create_post` sous forme de lots paresseux, dans `ChoixVisuels.secours`
(`illustration.Lot`) ; la réserve retenue alimente le FOND **et les slides** (voir
ci-dessous). Le carrousel garde par ailleurs les visuels de l'actu, affiche
comprise, qui y reste une slide honnête.

**Jamais un post qui ne MONTRE RIEN de l'actu** (titre corrigé le 2026-09-19 ; il
disait « jamais un post réduit à sa cover », et c'était la mauvaise cible).

*L'origine* (2026-09-18, article 17946 « Noisia Reunite After Seven-Year
Hiatus », Billboard, UNE image). Cette image a été jugée `affiche` : écartée du
fond (donc l'aplat Kinda), et absente des slides. Résultat publié : une cover
sans photo, et rien derrière — « la c'est n'importe quoi ! Il n'y a pas de slide
qui montre les artistes en question. » Trois règles en étaient sorties, chacune
posée là où l'information existe.

1. **Les photos de la réserve retenue deviennent des slides**
   (`compose.actifs_de_secours`, alimentée par `Fond.photos` — toutes les vraies
   photos du lot, la meilleure en tête, au plus `visuels_max_photos`). Elles sont
   déjà copiées en local et déjà jugées : les publier ne coûte plus ni requête ni
   jeton, et sans elles un article dont l'unique image est une affiche ne montre
   jamais l'artiste. Une affiche pêchée dans une réserve, elle, n'est ni fond ni
   slide : elle n'illustre pas l'actu. **Inchangée.**
2. ~~Une image seule sert à la fois de fond et de slide~~ — **RETIRÉE le
   2026-09-19**, post « Fred again.. joue sa mixtape à tout le monde au Palacio,
   trois heures après l'avoir finie » : slide 1 la cover avec sa photo recadrée,
   slide 2 **exactement la même image** en version nette. « Ça fait un peu bizarre
   d'avoir la même image en image normale juste après […] si c'est pour remettre
   la même image en deuxième slide, ça vaut pas le coup, parce qu'on la voit bien
   assez sur la première slide. » Le remède de Noisia dépassait sa cible : quand
   la cover porte déjà la photo, la répéter n'ajoute rien. **La photo qui sert de
   fond n'est donc JAMAIS republiée en slide, sans exception.** (Une vidéo dont
   c'est la *vignette* reste une slide : un clip n'est pas le même asset que son
   image fixe.)
3. **Un post d'un seul élément est acceptable quand sa cover porte une vraie
   photo** — « on peut se contenter juste de la cover normale et c'est tout ». La
   chaîne savait déjà le faire : sous deux éléments,
   `instagram.preparer_carrousel` crée un conteneur IMAGE simple au lieu d'un
   `CAROUSEL`. Rien de neuf à écrire, un seul fichier figé suffit.
   **Mais la leçon de Noisia tient** : si la cover est sur l'aplat Kinda
   (`FOND_KINDA`, aucune photo trouvée nulle part) ET qu'il n'y a aucune autre
   slide, le post ne montrerait rien — `PreparationImpossible`, exclusion et
   créneau suivant. C'est la distinction à garder en tête : ce qui est interdit,
   ce n'est pas « un seul élément », c'est « aucune image de l'actu ». Depuis la
   Tâche 5, ce chemin ne s'ouvre plus qu'sur un brouillon composé AVANT elle :
   une composition neuve ne pose plus jamais `FOND_KINDA`, elle SAUTE le créneau
   en amont (voir plus haut) — l'exclusion au gel reste le filet pour l'archive
   déjà en base, pas le cas normal.
   Inchangé : si les visuels existent et que seule leur récupération a échoué
   pour une raison passagère (`echec_transitoire`), c'est `PreparationDifferee`
   — on retente au passage suivant, parce qu'un article exclu ne revient jamais.
   Conséquence assumée : une panne de transport persistante fait un créneau
   `MANQUE` là où on aurait pu publier la cover seule ; le gel court 2 h avant
   l'heure, il a le temps de retenter.

Ordre de préférence du fond, mis à jour par la Tâche 6 (2026-09-23) : photo de
l'actu (non-affiche, assez grande) → photo du compte source → photo d'un
artiste cité → recherche web → **agrandissement par super-résolution du
meilleur candidat** → **on saute le créneau** (plus de fond Kinda en bout
d'échelle, voir ci-dessus). Le plancher de résolution
(`cover_fond_upscale_max`) s'applique à CHAQUE barreau, pas seulement au
premier : une photo de réserve trop petite ne sauve pas plus le post qu'une
photo d'actu trop petite. Une affiche n'est refusée qu'en FOND ; en slide,
c'est le visuel de l'actu.

**L'agrandissement, barreau ajouté juste avant le saut** (Tâche 6,
2026-09-23, `app/content/upscale.py`). Motivé par le post 76 (l'ADE et
Shlømo, 2026-09-21) : fond de 660×440 (3,07× pour remplir le gabarit), et le
CDN (URL signée) rend EXACTEMENT la même image avec ou sans ses paramètres de
redimensionnement — aucune version plus grande n'existe à aller chercher,
chez ce candidat ni ailleurs sur ce post ; sans ce barreau, un post par
ailleurs illustrable saute pour rien.

*L'outil* : `upscayl-bin`, le backend CLI headless d'Upscayl
(github.com/upscayl/upscayl-ncnn) — PAS `Real-ESRGAN-ncnn-vulkan`, le dépôt
officiel cité par le plan initial, figé à sa version de 2022 (aucune release
depuis) ; `upscayl-ncnn` publie encore en 2025-12 et embarque les mêmes
modèles ncnn Real-ESRGAN, avec la même interface CLI (`-i/-o/-s/-m/-n`).
Vérifié sur la machine de l'utilisateur (2026-09-23) : le binaire vit dans le
bundle de l'app GUI (`/Applications/Upscayl.app/Contents/Resources/bin/
upscayl-bin`), pas sur le PATH d'un terminal ni posé par Homebrew — même
piège que `ffmpeg`/`cloudflared` pour launchd
(`app/content/video.py::EMPLACEMENTS_CONNUS`), en pire : pas d'emplacement
Homebrew connu à essayer en repli, d'où un chemin en RÉGLAGE
(`visuels_upscale_binaire`, `visuels_upscale_modeles`) plutôt qu'une
recherche sur le PATH. Mesuré : 660×440 → 2640×1760 en 1,1 s
(`upscayl-lite-4x`) à 5,8-10,7 s (`upscayl-standard-4x`, selon la charge de
la machine), GPU sollicité via Vulkan/MoltenVK. Modèle par défaut
`upscayl-lite-4x` (réglage `visuels_upscale_modele`) : le fond de cover est
recouvert d'un voile sombre ET du texte de l'accroche, le détail que le
modèle `standard` ajouterait en plus n'y est de toute façon pas visible — pas
de raison de payer 5× le temps, six fois par jour, pour un élément décoratif.

*La règle qui borne tout le reste : le FOND SEULEMENT, jamais une slide.* La
super-résolution n'agrandit pas, elle INVENTE le détail manquant — sur une
photo de presse, le modèle fabrique de la texture de peau et de cheveux qui
n'était pas dans le fichier, visible d'abord sur les visages. Une slide est
DOCUMENTAIRE (photo de presse, capture d'un artiste) : elle n'est jamais
retouchée. `app/publish/preparation.py::_agrandir_fond_si_marque` est le SEUL
point d'appel à `upscale.agrandir_fond`, et il ne lit/écrit que
`slides[0].image_url` — deux tests-sentinelles, un par gabarit (le carrousel
et le reel appellent chacun leur propre préparateur) :
`tests/publish/test_publish_preparation.py::
test_l_agrandissement_ne_touche_que_le_fond_jamais_un_media_du_carrousel` et
`tests/publish/test_publish_preparation_reel.py::
test_l_agrandissement_ne_touche_que_la_cover_jamais_la_video_du_reel`
échouent si un media du carrousel, ou la vidéo d'un reel, passe par cette
fonction.

*Où la décision se prend, et pourquoi ce n'est PAS le même endroit que le
saut de la Tâche 5.* `illustration.eviter_l_affiche` distingue désormais,
pour le meilleur candidat rejeté PAR LE PLANCHER (jamais un rejeté pour
affiche — une garde `not est_affiche` en plus ici s'est révélée redondante
avec le filtre déjà posé par `_meilleur_agrandissable`, et coupait le
barreau dès qu'un SEUL candidat affiché était dans la course, même quand un
AUTRE candidat non-affiche restait éligible : exactement le cas
Transmusicales — affiche de line-up en fond, photo d'artiste à côté — que
l'échelle de secours existe pour résoudre ; retirée, revue du tour de
correction 1/5), s'il est assez grand pour être agrandi
(`visuels_upscale_source_min_px`, **230 px** de côté minimal — le vrai seuil
structurel est 219 px, en dessous duquel même le facteur 4 ne repasserait
plus le plancher du gabarit le plus exigeant, le reel à 1080×1920 ; 230 px
garde une marge de sécurité modeste). Si oui, `Fond.a_agrandir` est posé à
`True` et la
COMPOSITION NE SAUTE PAS : `compose.resoudre_fond` ne lève plus
`VisuelIntrouvable` pour ce cas, le brouillon est créé avec ce fond encore
trop petit, et le marqueur est persisté dans
`posts.variants["fond"]["a_agrandir"]` (l'article est purgé à 2 jours, le
post doit rester décidable sans lui). **L'agrandissement lui-même a lieu au
GEL (T-2h, jamais à la composition ni à l'ingestion)** : coût borné à une
tentative par post, budget de temps `visuels_upscale_delai_max_s` (60 s,
marge large sur les 1,1-10,7 s mesurés), et le fichier produit va dans
`visuels_cache_dir` comme les autres copies locales — même purge. Si le
résultat repasse le plancher, `post.slides` est RÉASSIGNÉ (jamais muté en
place, colonne JSON) sur la copie locale nette, que `render_post` lira
ensuite.

**Le candidat agrandissable garde son origine réelle et ses photos de
slides, même venu d'une RÉSERVE** (revue, tour de correction 1/5). Le
premier jet ne rendait `Fond(agrandissable, origine, a_agrandir=True)`
qu'avec l'origine par défaut (`origine_de` ne connaît que les médias de
l'article, jamais les lots compte/artiste/web) et sans `photos` : un
candidat venu de la réserve « compte » faisait alors mentir le dashboard
sous « Image de fond » (`variants["fond"]["origine"]` valait `article` au
lieu de `compte`), et `actifs_de_secours` ne produisait aucune slide,
rouvrant la faille « jamais un post qui ne montre rien » (post Noisia,
2026-09-18). Le crédit `📸 @compte`, lui, n'était PAS perdu ni avant ni
après ce correctif — `actifs_de_secours` pose `compte=None` sur ses actifs,
donc `credit_des_actifs` rend `None` et `create_post` retombe sur `or
credit` (celui de `visuels`), exactement comme le chemin nominal ; annoncé
à tort comme un troisième dégât au tour de correction 1/5, corrigé au tour
2/5. `eviter_l_affiche` associe désormais à CHAQUE candidat de réserve,
pendant le parcours de l'échelle, l'origine du
lot et le cortège de photos qu'il aurait reçu s'il avait gagné normalement
— sans coût, cette information est déjà là au moment où le candidat est
examiné.

*Trois issues au gel, jamais un contournement du plancher.* (1) Binaire ou
modèles absents (`upscale.binaire_disponible`) : PANNE MACHINE, jamais une
exclusion ni un saut silencieux — on retombe sur le comportement d'avant la
Tâche 6, c'est-à-dire le saut de la Tâche 5, avec sa raison habituelle
(`app.publish.preparation.PreparationSautee`). (2) Le résultat agrandi
échoue ENCORE le plancher (source proche du seuil bas, ratio extrême) : même
saut — l'outil ne devient jamais un moyen de publier flou. (3) Le
téléchargement du fond d'origine échoue au gel (réseau) : ce n'est ni l'un
ni l'autre, `PreparationDifferee` (retente au passage suivant), parce qu'une
coupure réseau n'est pas une panne de l'outil de super-résolution. Dans tous
les cas de saut, `runner._geler` suit l'INVARIANT D'ORDRE partagé avec la
Tâche 5 (`app/schedule/pause_visuels.py`) : `_liberer` le créneau D'ABORD,
`marquer_saute` seulement si la libération a réussi — jamais l'inverse.

Aucun test n'invoque le vrai binaire (`agrandir`/`binaire_disponible`
toujours injectés, comme `rendre`/`telecharger` le sont déjà pour Chromium et
le réseau) : la suite passe sur une machine où Upscayl n'est pas installé.

**Mais UNE SEULE affiche en slide, jamais sept** (2026-09-19, post du 18/09 13h10
sur les Trans Musicales : les slides 2 à 8 étaient la carte d'annonce entière du
festival — « TOUTE LA PROG RÉVÉLÉE », « 74 groupes », « 42 pays » — dans SA charte
graphique). Deux dégâts : notre post devenait leur carrousel avec une cover Kinda
posée devant, c'est-à-dire le rôle d'intermédiaire dont le projet veut sortir ; et
sept slides d'affilée ne ressemblaient plus à Kinda. Le plafond
(`illustration.plafonner_affiches`, `AFFICHES_MAX = 1`) est appliqué dans
`create_post` et donc PERSISTÉ dans `posts.assets` : le dashboard édite ce
brouillon, une règle rejouée au gel retirerait une slide remise à la main. On
garde la PREMIÈRE — sur un carrousel d'annonce la carte de tête porte le message
principal ; c'est une heuristique d'ORDRE, pas un jugement de contenu.

Corollaire de coût, et c'est ce qui a failli vider la règle de sa substance :
le juge ne notait que `illustration_max_candidats` images, soit 3, donc les sept
cartes des Trans seraient descendues à cinq et non à une. `prejuger(actifs)` juge
désormais d'un coup TOUT ce que le post peut montrer, en UN SEUL appel payé (le
lot s'élargit, il ne se dédouble pas), et `choisir_illustration` puis
`eviter_l_affiche` relisent ces verdicts gratuitement. La borne passe à **10**,
la taille d'un carrousel Meta : au-delà on paierait pour juger des images que le
post ne publiera jamais. ~0,002 $ par composition. La règle du doute est
intacte : une image non jugée (borne, décodage en échec, vision coupée) est
GARDÉE — une photo jetée par erreur appauvrit le post, une carte de trop ne fait
que l'alourdir.

**D'où vient le fond est enregistré** : `posts.variants["fond"]["origine"]`
(`article` | `compte` | `artiste` | `kinda`), figé à la composition au même titre
que la palette, et affiché sous « Image de fond » au dashboard. Une fois l'URL
écrite dans la slide, rien d'autre ne le dirait.

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
Une sortie structurée vide (`parsed_output` None : `max_tokens` ou refus) est
traitée comme un échec de validation : relance, puis `CompositionError`.
L'interdiction d'emoji dans `headline`/`title`/`subtitle` reste stricte (textes
rendus dans une police).

**Légendes : plus de média nommé, plus de clôture systématique en question**
(2026-09-21, retour de l'utilisateur en ouvrant le compte réellement publié :
« supprime les sources des descriptions [...] parle de l'actu en elle-même » —
posts 71, 63, 65 : « Info via Stereogum. », « ... selon Soompi ... » — puis,
sur les six derniers posts en ligne, une clôture identique à chaque fois,
« Qu'en pensez-vous ? »). Deux défauts, même mécanique de correction : une
règle de prompt (`SYSTEM_PROMPT`, règle 4) plus un filet, parce qu'un prompt ne
garantit jamais rien à lui seul. Le nom du média n'est PAS un défaut de forme
tolérable comme les emojis ou les accents — le retirer après coup est
nécessaire, `app/content/legende.sans_mentions_de_source` retire les phrases
qui nomment la source de l'article (nom de la source nettoyé de son canal,
domaine de l'URL avec et sans TLD — construits depuis l'article, jamais
devinés), appliqué à la composition — dans la boucle du compositeur, AVANT
le contrôle de forme, et encore dans `create_post` (idempotent) — pour que la
légende persistée soit déjà celle qui part en ligne ; une légende qui
deviendrait vide part telle quelle. Avant le contrôle et pas après : retirer
une dernière phrase « Info via Tsugi. » pouvait sinon laisser en fin de légende
la question que le contrôle de clôture venait d'écarter.

**Le filet ne bannit le NOM de la source que si c'est un MÉDIA** (catégorie
`media`, ou `None` : dans le doute on protège du plagiat). Ne pas simplifier
cette condition : pour un compte d'artiste, de festival, de label ou de club,
`source.name` est le SUJET de l'actu, pas la source de l'info (« Qui parle :
`sources.categorie` »). Mesuré en revue le 2026-09-21, source « Astropolis
(IG) » : « Astropolis dévoile sa programmation. Trois jours à Brest fin août. »
perdait sa première phrase — celle qui porte l'actu —, c'est-à-dire la
mutilation des posts mêmes que le feed priorise. La racine sans TLD suit la
même règle (elle n'est bannissable que parce qu'elle se confond avec le nom de
la source) ; le domaine COMPLET (`astropolis.org`) nomme un site, jamais le
sujet, et reste banni dans tous les cas.

La clôture en question, elle, est un défaut de FORME :
`probleme_cloture_question()` suit exactement `probleme_emojis()` /
`probleme_accents()`, relance puis accepte avec un warning plutôt que de perdre
un créneau. **La dernière phrase ne se termine JAMAIS par une question**, et le
prompt dit la même chose que le contrôle (revue du 2026-09-21) : il proposait
encore « une question » parmi les clôtures possibles alors que le contrôle les
refuse toutes — sur un modèle qui en mettait six sur six, une relance quasi
systématique, jusqu'à trois appels `composer_model` par composition. Un
contrôle sans mémoire ne sait pas faire « de temps en temps », seulement
« jamais » : c'est la question qui est sortie des clôtures proposées, pas le
contrôle qui s'est assoupli. Une question reste permise au milieu de la
légende.

**Les accents : le contrôle regardait la légende, pas la COVER** (2026-10-04,
post 168 publié le 03/10 — bande de titre du reel « **Troye Sivan** / invité
surprise de **Charli XCX** à l'ACL », publiée « invite … a »). `probleme_accents`
ne recevait que `caption` ; la légende de ce post était parfaitement accentuée,
donc aucune relance, et le texte **le plus visible du post** — dessiné en gros
sur l'image — est parti tel quel.

Le critère de la légende ne se transpose PAS : il demande une longueur
minimale, et un texte de cover est court. Mesure sur les 1 980 textes de
variantes en base : **58,6 % n'ont aucun accent**, encore 36,9 % au-delà de
70 caractères — une accroche française peut légitimement n'en porter aucun
(« Le line-up tombe ce week-end »). Tout seuil de longueur rendrait le contrôle
inerte ou bavard, et le précédent de la clôture en question (six sur six,
jusqu'à trois appels `composer_model`) interdit le second.

`app/content/accents.py` cherche donc un MOT, par deux références : la LÉGENDE
du même brouillon (le modèle sait écrire le français, il ne l'applique pas aux
textes rendus — zéro maintenance) et un lexique de 162 entrées DÉRIVÉ des 148
légendes accentuées en base (la procédure est dans l'en-tête du module ; les 20
légendes ASCII sont écartées du corpus, sinon leur « apres » sert de
contre-preuve à « après »). Deux garde-fous, tous deux mesurés : **l'accent
perdu ne doit pas être sur la dernière lettre** (`joue`/`joué`,
`invite`/`invité`, `signe`/`signé` sont tous deux du français valide — sans
cette règle, 15 des 26 rapprochements bruts étaient des participes CORRECTS),
et **un mot capitalisé en milieu de texte est ignoré** (le lexique porte
`video`, `special`, `piece`, qui sont de l'anglais valide : « Video Games »).
Résultat mesuré : **68 textes signalés sur 1 980 (3,4 %), 24 posts sur 165
(14,5 % des compositions), zéro faux positif**.

Conséquence assumée : une composition sur sept paie un appel `composer_model`
de plus — mais pour un défaut RÉEL à chaque fois. Même mécanique non bloquante
que les quatre contrôles voisins : relance, puis acceptation avec un
avertissement. Ce que le contrôle NE sait PAS faire : « invite » et « a », les
deux fautes exactes du post 168, sont ambiguës et restent hors de portée d'un
critère déterministe — c'est le reste du brouillon (« scene », « joues ») qui
déclenche la relance, et la relance réécrit les quatre variantes.

**Une énumération se surligne en entier, ou pas du tout** (2026-10-01, post
« **Angèle**, **Caroline Polachek** et SebastiAn s'unissent sur "Love
Triangle" » : deux artistes sur trois surlignés, rien ne justifie la
différence, ça se lit comme un oubli). **Le défaut n'est PAS le nombre de
segments surlignés**, et la correction naïve est interdite par la mesure : sur
les 1 704 textes de variantes en base (568 accroches, 568 titres, 568
sous-titres), 483 portent DEUX segments et la plupart le méritent — « **Bicep** referme le triptyque **AIR** »,
l'artiste et l'œuvre. Interdire le second surlignage casserait 28 % des textes,
dont la majorité sont bons. Ce qui est fautif est l'INCOHÉRENCE à l'intérieur
d'une énumération. `highlight.enumeration_incoherente` repère les suites d'au
moins **trois** noms propres séparés par des virgules ou « et » dont une partie
seulement est surlignée ; `compose.probleme_enumeration_surlignee` la décrit
pour la relance, sur le modèle exact des trois contrôles précédents (relance,
puis acceptation avec warning — jamais un créneau perdu), et la règle 3 du
prompt dit la même chose. Trois réglages, chacun payé d'une mesure sur ces
1 704 textes : le seuil à TROIS éléments (à deux, 18 textes signalés dont 9
légitimes — « **Palacio**, Mexico », « Adam Beyer et **Drumcode** » : des
couples de nature différente ; à trois, **5 signalés, 0 légitime**, soit
0,3 %) ; un segment ne peut OUVRIR une énumération que s'il se réduit à son nom
propre (sinon « C'est officiel, … » et « En **1996**, … » en fabriquent une) ;
et « et X » FERME l'énumération (sinon « entre Londres, Miami et Liverpool,
**Mele** sort son album » rattache Mele aux villes). Coût : ce contrôle ne
signale que 0,3 % des textes, là où la clôture en question en signalait six sur
six et provoquait jusqu'à trois appels `composer_model` par composition.

**Une date absolue, jamais « ce soir »** (2026-10-04, `app/content/reperes.py`).
Post 153, publié le 04/10 à 10h21 : la cover disait « **This Dream Of You**, le
nouveau son de **Martin Garrix**, sort à minuit » et la légende « sort ce soir à
minuit », pour un morceau sorti dans la nuit du 2 au 3 — le brouillon avait été
composé le 02/10 à 12h30, **45,9 heures plus tôt**. Sur les 12 derniers posts
publiés, 3 portaient un repère relatif et **2 étaient faux** au moment de partir.
C'est un défaut de **VÉRACITÉ**, pas de forme : un compte d'actu qui annonce
comme à venir ce qui est déjà sorti perd sa crédibilité bien plus vite qu'avec un
accent manquant — d'où sa place EN TÊTE de `probleme_de_variantes` et de
`probleme_de_legende`. La cause est structurelle : le modèle écrit à la
composition et il écrit juste **à cet instant**, mais le créneau glisse.

Même mécanique que les quatre contrôles précédents (relance, puis acceptation
avec avertissement — jamais un créneau perdu). Mesure sur la base : **3,8 % des
1 992 textes de cover, 9,5 % des 169 légendes, 13,0 % des posts** — l'ordre de
grandeur de `probleme_accents_de_cover`, et les 91 textes signalés portent tous
un repère réel, zéro faux positif. Deux formulations fréquentes sont **hors
table** parce qu'elles ne vieillissent pas : « dispo dès maintenant » (15 covers,
11 légendes — une sortie déjà faite le restera) et « bientôt » / « sous peu »
(vague par nature). Trois pièges mesurés : « ce soir-**là** » est un rappel du
passé (post 170), « la soir**ée** Trash » n'est pas « ce soir », et
**« aujourd'hui » est écarté de la PROSE** — dans une légende, 8 des 10
occurrences sont le sens atemporel (« le morceau ressurgit aujourd'hui sous une
nouvelle forme », posts 29/34/35/36/37), dans une cover les 6 sont datées. Même
précédent que `probleme_accents` contre `probleme_accents_de_cover` : deux textes
de nature différente, deux critères.

**Et `build_user_prompt` donne désormais « Date du jour »**, sinon la règle 9 est
INSATISFIABLE : on demande une date absolue à la place de « demain », or
« Publié le » seule ne dit pas au modèle quel jour on est — il ne peut ni
convertir le « demain » de l'article, ni voir que cet article date de la veille.
C'est exactement le post 156 (« sort demain » pour une actu de la veille, déjà
sortie le jour même) : le modèle a **recopié** le repère de l'article faute de
pouvoir le dater. Le nom du jour vient de `reperes.JOURS` et jamais de `%A`, qui
suit la locale du processus.
