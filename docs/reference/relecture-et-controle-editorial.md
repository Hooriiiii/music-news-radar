# La fenetre de relecture, et le controle editorial sur ce qui va partir

**Quand lire ce fichier** : avant de toucher a `app/schedule/relecture.py`,
`app/schedule/reouverture.py`, `app/publish/manifeste.py`, `GET /relecture`, ou
a `PATCH /posts/{id}`. Il porte l'invariant « si personne ne relit, ca publie
quand meme a l'heure », le manifeste du gel (`creneaux.elements`), le re-gel
automatique et la demonstration d'anti-doublon de la reouverture.

## La fenetre de relecture avant publication (2026-10-04)

**Pourquoi.** Trois defauts reels, reperes A L'OEIL sur le compte publie en deux
jours et invisibles de toute la suite de tests : un logo Wikipedia recadre en
derniere slide, un reel de 4 secondes sans rapport avec l'actu, une annonce
« sort ce soir » publiee deux jours apres la sortie. Demande de l'utilisateur :
pouvoir verifier et corriger avant le depart, parce que « certains elements sont
difficilement detectables par l'app ». C'est juste, et c'est tout le chantier.

**L'INVARIANT, qui gouverne tout : si personne ne relit, ca publie quand meme a
l'heure.** La relecture est une OPTION, jamais une condition. Un post qui
attendrait une validation ressemble a un post en cours, et le taux de publication
est deja a moins de la moitie des creneaux aboutis. Aucune fonction de
`app/schedule/relecture.py` n'est lue par `runner._publier` ; preuve par mutation
dans `tests/schedule/test_relecture.py::test_la_relecture_n_est_jamais_une_condition_de_publication`
(on ajoute dans `_publier` le `if post.status is PostStatus.DRAFT: continue`
qu'un contributeur presse ecrirait, et le test rougit — rien d'autre dans la
suite ne bougeait).

**LA MESURE QUI A DEPLACE LA FENETRE.** On cherchait d'abord cette fenetre au
GEL, puisque c'est la que tout est fige. Sur les 85 creneaux reellement geles en
base (sur 125) :

| ecart gel → heure prevue | n=85 |
|---|---|
| mediane | 105 min |
| p25 | 33 min |
| ≤ 30 min | 18 (21 %) |
| ≤ 5 min | 12 (14 %) |

Un creneau sur sept est gele a moins de **cinq minutes** de son depart (passage
rattrape au reveil du Mac) : une fenetre accrochee au gel n'existerait pas pour
lui. Alors que le POST existe BIEN plus tot — ecart composition → heure prevue,
n=111 creneaux portant un post : **mediane 737 min (12 h)**, p25 258 min, et
seulement 9 sur 111 sous 2 h. **La fenetre s'ouvre donc a la COMPOSITION**, ou le
creneau est PROGRAMME donc deja librement editable. Le gel n'est que le DERNIER
APPEL.

Ce qui manquait n'etait pas du temps. C'etait trois choses.

**1. Rien ne prevenait.** `runner._annoncer_relecture`, etape isolee comme les
autres, notifie (macOS — le seul canal du projet : ni mail ni Discord, CLAUDE.md
interdit de re-proposer SMTP) quand un post compose entre dans les
`publication_relecture_avance_minutes` (**120**) qui precedent son depart.
Declenchee par l'HORLOGE et jamais par le gel, precisement a cause de la mesure
ci-dessus. **Au plus UNE annonce par creneau** (`relecture.json` a cote du jeton,
meme mecanique que `quota_graph.json` : ecriture atomique, temporaire nomme par
processus, purge des entrees de plus d'un jour, aucune ecriture en LECTURE) —
six notifications par post est la facon la plus sure d'apprendre a les ignorer.
Plus un voyant `relecture` au bandeau, et un compteur `annonces_relecture` dans
le log de `run_publication`. Le voyant reste **`ok`** quand des posts attendent :
peindre en ATTENTION ce qui part tout seul reapprendrait a ignorer le bandeau
(lecon du voyant de composition qui criait au loup, 2026-10-02). Il passe a
ATTENTION pour une seule chose, une cover qui DEBORDE — celle-la, personne
d'autre ne la rattrapera.

**2. Rien ne montrait ce qui va PARTIR.** `GET /relecture` (`app/api/relecture.py`,
`DepartOut`) rend, par creneau a venir portant un post, le texte EXACT que Meta
recevra — c'est-a-dire `texte_legende(post)`, qui aere les paragraphes, remet le
diese sur les hashtags, les deduplique, les plafonne a 30, ajoute la ligne de
credit obligatoire et tronque a 2150 points de code. **La legende editee n'est
pas la legende publiee**, l'audit UX du 02/10 l'avait releve, et `posts.credit`
n'etait affiche NULLE PART. Un endpoint dedie et non `PostOut` elargi, meme motif
que `GET /supervision` : la question croise `creneaux`, `posts` et un pipeline
dont le resultat n'est stocke nulle part (il est calcule a l'instant de publier),
et chaque lecteur de post paierait ce calcul pour rien. Au dashboard : une liste
« À relire avant publication » en tete du panneau Posts, et une section « Ce qui
va partir » dans la fiche. `deborde` vaut **`None`** tant qu'aucun rendu ne l'a
mesure et s'affiche « inconnu » : un `false` par defaut serait un faux feu vert
sur le seul signal qui doit empecher une publication en defaut.

**3. Le gel fermait la porte, et il la fermait TROP LARGE.** `PATCH /posts/{id}`
repondait 409 sur un creneau GELE pour les cinq champs de contenu. Or
**`caption` et `hashtags` ne sont PAS figes** : `runner._publier` appelle
`texte_legende(post)` juste avant l'appel a Meta, en lisant la base. Le 409 qui
les bloquait etait une SUR-PORTEE, pas une protection — il fermait la porte
exactement dans les deux heures ou l'utilisateur veut corriger (« modifier la
description »), alors que rien dans le gel n'en depend. Le refus se scinde donc
en deux (`CHAMPS_FIGES`) :
- **publication EN VOL** (creneau reclame, `RAISON_RECLAMATION` de moins de
  20 min) → tout est refuse, legende comprise : le conteneur est parti chez Meta
  AVEC son texte ;
- **fichiers figes** (creneau GELE) → `slides`, `variante_retenue`, `template` et
  `carrousel_explicite` **rouvrent le creneau TOUT SEULS** (voir « Le controle
  editorial » ci-dessous ; jusqu'au 2026-10-04 ils rendaient 409 en nommant le
  remede, que l'utilisateur devait aller cliquer).

**`POST /schedule/{id}/rouvrir`** est ce remede : il n'ouvre pas l'edition, il
**INVALIDE le gel** (GELE → PROGRAMME, `fichiers = NULL`, `elements = NULL`,
dossier fige efface) et le gel normal refait les fichiers au passage suivant. Le
geste lui-meme vit desormais dans `app/schedule/reouverture.py`, appele par les
DEUX portes (le bouton et `PATCH /posts/{id}`) : deux copies de cette ecriture
conditionnelle divergeraient, et c'est l'ecriture la plus delicate du depot.
Trois refus, chacun avec sa raison EN CLAIR : pas gele (rien a rouvrir),
publication en vol, et **trop pres du depart** — `publication_relecture_regel_minutes` (**20** = deux passages de
l'agent, pour qu'un premier gel differe ne soit pas fatal). Il **EPINGLE** le
creneau : un PROGRAMME non epingle est repris par `appliquer_arbitrage` dans les
10 minutes, et le post qu'on vient de rouvrir pour le corriger serait remplace.

**Ce qui est GRATUIT ne passe jamais par la.** Legende et hashtags : zero re-gel,
ils sont relus a la publication. Cover, slides, gabarit : un seul re-gel, qui est
le minimum — le JPEG fige EST la cover. On ne repaie jamais Claude : le creneau
garde son `post_id`, la composition n'est pas refaite.

**Si le re-gel echoue, on ne perd pas le post.** `rouvrir` ecrit
`RAISON_RELECTURE` dans `creneaux.erreur` — meme procede que `RAISON_RECLAMATION`,
un marqueur lisible dans un champ libre relu par un autre module. `_geler` le lit
AVANT toute ecriture du passage et change une seule chose : une
`PreparationImpossible` **libere** le creneau au lieu d'**exclure** l'article a
vie. Sans ca, une accroche rallongee d'un caractere de trop (cover qui deborde)
brulerait l'actu pour une faute de frappe de l'utilisateur. Le marqueur passe en
TETE d'un `gel differe`, exactement comme `_raison_manque` conserve la cause
precedente : sinon un premier report effacerait la protection. Hors reouverture
manuelle, un gel impossible exclut comme il l'a toujours fait (test-sentinelle).

**Aucune double publication possible.** `rouvrir` n'appelle jamais Meta, et son
ecriture est CONDITIONNELLE sur (etat, article, post) comme la reclamation de
l'agent : un creneau deja reclame est en ECHEC, donc hors de la condition
`etat == GELE` ; si l'agent reclame entre la lecture et l'ecriture, zero ligne
est touchee et l'API rend 409. Apres l'appel le creneau est PROGRAMME, or
`_publier` ne selectionne que `etat == GELE` — aucun chemin ne mene a
`media_publish` avant un nouveau gel. La reclamation (GELE → ECHEC committe avant
l'appel a Meta) n'a pas ete touchee.

**Limite connue, assumee.** Le marqueur de relecture vit dans `creneaux.erreur` :
les chemins de `_geler` qui ecrivent une AUTRE erreur et lachent le post
(gabarit qui ne correspond plus, post supprime) l'effacent. Ces cas font de toute
facon perdre le post au creneau, donc la protection n'y a pas d'objet — mais
c'est une propriete du marqueur, pas une garantie du schema.
## Le controle editorial sur ce qui va partir (2026-10-04)

Suite directe de la fenetre de relecture ci-dessus. Demande de l'utilisateur :
« j'aimerais pouvoir cliquer et voir ce qui va etre poste **exactement** […] et
pouvoir remodifier : changer le hook, voir la description et la modifier, et
toutes les slides — en rajouter, en supprimer, les modifier. Une premiere couche
d'automatisation est faite, et moi je peux modifier apres. »

### Le defaut de fond : la liste publiee etait RECALCULEE, jamais ecrite

Le post 164 (Tomorrowland) affichait **1 slide** dans la fiche. Instagram a recu
**3 images**, dont un logo Wikipedia recadre au point que les lettres etaient
coupees — repere **apres** publication. `posts.slides` est le BROUILLON ;
`preparation.medias_du_carrousel` y ajoute au gel jusqu'a neuf medias de l'actu,
et rien ne persistait le resultat.

Mesure du 2026-10-04 sur la base vivante : **86 creneaux** portent des fichiers
figes ; parmi les **48 carrousels** dont les candidats sont encore recalculables,
**27 (56 %)** ne retombent pas sur le nombre de fichiers reellement figes. Et le
creneau 3 annonce 5 fichiers pour 1 seul candidat recalculable : son article est
purge. Le recalcul ne dit pas ce qui est parti, et ne le pourra jamais — d'ou la
regle : **tant que cette liste reste calculee, aucun controle editorial n'est
possible.**

### `creneaux.elements` : le manifeste du gel (migration 0022)

Le gel ECRIT desormais la liste ordonnee de ce qui part — cover comprise, medias
de l'actu compris, **plus les elements ECARTES avec leur raison**. Une image trop
petite ou trop eloignee du 4:5 (`images.ImageEcartee`), une video inutilisable
retombee sur sa vignette, un carrousel plein a dix : ces decisions n'etaient que
JOURNALISEES, donc invisibles — c'est precisement ce que l'utilisateur veut voir.

- `app/publish/manifeste.py` : `Figement(noms, elements)`, ce qu'un preparateur
  rend desormais. Les deux ENSEMBLE et jamais deux appels : le manifeste n'est
  juste que s'il est construit au moment ou les fichiers s'ecrivent. Un
  preparateur injecte qui ne rend qu'une liste reste accepte (`normaliser`) —
  son manifeste vaut alors INCONNU, jamais « vide ».
- `_recoller_fichiers` verifie l'invariant plutot que de le supposer : les
  entrees non ecartees sont, dans l'ordre, exactement les fichiers figes.
- Ecrit par `runner._geler` dans la MEME ecriture conditionnelle que `fichiers` :
  les deux decrivent le meme gel et ne doivent jamais se desynchroniser.
  Efface avec eux a la reouverture.
- **Denormalise** : aucune reference a l'article, donc la liste survit a la purge
  (`retention_days` = 2 j) et a un creneau devenu autonome de son article.
  `NULL` = inconnu (creneau gele avant la migration, ou preparateur sans
  manifeste), jamais « vide ».
- Expose par `GET /relecture` (`DepartOut.elements`), avec `candidats` (la
  reserve de l'editeur : actifs du post + medias autorises de l'actu) et
  `liste_arretee`.

**`GET /relecture/{id}/fichiers/{nom}`** sert le fichier fige LUI-MEME. Tout le
reste DECRIT ce qui part ; ceci le MONTRE, avec son recadrage reel — aucune
description n'aurait dit que le logo du post 164 etait coupe. Aucune traversee de
chemin possible : `nom` n'est jamais construit, il est CHERCHE dans
`creneaux.fichiers`, la liste que le gel a ecrite lui-meme.

### La liste devient editable, et le re-gel est AUTOMATIQUE

Choix explicite de l'utilisateur : « on modifie, le systeme refait ce qui a change
et rien d'autre. Aucun bouton a cliquer, et l'etat affiche est toujours celui qui
partira. » `PATCH /posts/{id}` rouvre donc le creneau LUI-MEME
(`_regel_automatique` → `app/schedule/reouverture.rouvrir`) des qu'un champ fige
change, au lieu du 409 qui renvoyait vers un bouton. Le bouton « Rouvrir » a
disparu du dashboard ; `POST /schedule/{id}/rouvrir` reste, sur le meme chemin.

- **Ce qui est gratuit ne passe toujours pas par la** : legende et hashtags ne
  rouvrent RIEN (relus en base juste avant Meta). Preuve par mutation —
  remplacer la condition de `_regel_automatique` par `if True:` laissait toute
  la suite verte, et coutait un gel entier pour une virgule ; verrouille par
  `tests/publish/test_manifeste_gel.py::test_editer_la_legende_ne_jette_JAMAIS_les_fichiers_figes`.
- **Claude n'est jamais repaye** : le creneau garde son `post_id` — qui est aussi
  la condition de l'ecriture conditionnelle.
- **Un refus ne touche a RIEN** : publication en vol, ou moins de 20 min avant le
  depart → 409 avec la raison, et aucune edition appliquee. L'ancien
  comportement (editer le brouillon pendant que les fichiers figes disent autre
  chose) etait exactement le defaut a supprimer. `variante_retenue` est validee
  AVANT le re-gel, pour ne pas jeter des fichiers figes au profit d'un 422.
- **Un creneau GELE est desormais `editable: true`.** Il etait « a moitie
  editable » ; dire le contraire enverrait chercher un bouton qui n'existe plus.
  Ce qui peut encore refuser une edition de COVER se lit dans
  `raison_non_reouvrable`, parce que la legende, elle, reste modifiable jusqu'au
  bout.

**`posts.variants["carrousel"]["explicite"]`** (`preparation.liste_explicite`) :
pose des que l'utilisateur arrete la liste. Le gel ne reprend alors plus ni
`posts.assets` ni les medias de l'article — `slides` fait foi. Sans ce marqueur,
retirer un element n'aurait AUCUN effet : le gel suivant le remettrait, et
l'edition serait annulee en silence. Les autres filtres (visuels interdits, fond
jamais republie, doublons) restent appliques : ce sont des REGLES du projet, pas
une source de candidats. Ecrit en FUSION dans `variants`, jamais en bloc — sinon
la palette et l'origine du fond disparaitraient. `MediaIn` gagne `origine` et
`compte`, sans quoi une liste reecrite a la main perdrait la provenance de chaque
visuel.

### Au dashboard

Section « Ce que Meta recevra » dans la fiche de post : la liste finale, vignettes
= fichiers figes, un element ecarte en pointille avec sa raison en rouge, et par
element **supprimer / monter / descendre / remplacer**, plus un selecteur
« ajouter un visuel » alimente par `candidats`. Un seul chemin d'ecriture pour
les cinq gestes (la liste entiere + `carrousel_explicite`).

**Et la Programmation devient le poste de travail** : cliquer un creneau ouvre
l'editeur, et chaque ligne resume « N element(s) · M ecarte(s) au gel ».
L'utilisateur l'a dit — « l'onglet Flux d'actus ne me sert plus » —, et cet
onglet reste pour le tri et la detection, sans qu'on soit oblige d'y passer pour
editer. Quand la liste est arretee, les cases « Medias de l'actu » sont
DESACTIVEES avec leur raison : elles editent le brouillon et ignorent les visuels
retenus a la composition, donc en cocher une les ferait disparaitre de la liste
publiee.

### Televerser un visuel depuis le disque (2026-10-04)

La derniere piece : l'editeur ne proposait que ce que le PIPELINE avait trouve
(`candidats_editables` : medias de l'actu, de son groupe de doublons, actifs
d'artistes, candidats web). Utile quand la bonne image est la, inutile quand elle
n'y est pas. Choix explicite de l'utilisateur : deposer un fichier de son Mac.

**Le depot produit UNE ENTREE DE PLUS dans `candidats`, et rien d'autre**
(`app/publish/televersement.py`, `POST /posts/{id}/visuels`) : un actif de plus
dans `posts.assets`, origine **`depot`**. Tout le reste de l'editeur le traite
alors sans une ligne de changement, et le mettre dans la liste passe par
`PATCH /posts/{id}` -- qui rouvre deja le creneau tout seul. **Aucun second
chemin d'ecriture, et rien ici n'approche Meta** : la reclamation (GELE -> ECHEC
committe AVANT l'appel) n'est pas touchee, le creneau GELE n'est pas touche.

**La copie locale est celle de tout le monde** : `visuels_cache_dir`, nom derive
du SHA-256 **du CONTENU**, relue par un `file://` resolu dans ce dossier
(`lire_fichier_cache`), menage a `visuels_cache_jours`. La purge supprime sur le
`mtime` : un depot frais ne peut pas disparaitre avant son gel, et un depot
IDEMPOTENT (memes octets, meme nom) rafraichit ce `mtime`.

**Le verdict est rendu AU DEPOT, par le code du GEL.** `images.cadrer_carrousel`
avec les seuils de la configuration (`publication_crop_upscale_max` 2,2 ;
`publication_crop_perte_max` 60 %) : memes seuils, memes messages, et le refus
dit LEQUEL a mordu. Attendre T-2h pour apprendre qu'un fichier qu'on vient de
choisir ne passe pas est absurde. **Ce qui est reparable n'est pas refuse** --
PNG, transparence, orientation EXIF, 16:9 de presse (55 % de perte, sous le
plafond) passent tous.

**Les trois bornes de Meta sont alors satisfaites PAR CONSTRUCTION**, et c'est
la seule reponse honnete : ce que le gel fige est la sortie de
`cadrer_carrousel`, exactement 1080x1350 en JPEG borne a 8 Mo par l'echelle de
qualites. Le depot ne verifie pas « largeur 320-1440 » ni « 4:5 a 1,91:1 », il
verifie qu'on peut les ATTEINDRE.

**Pas de video, et ce n'est pas une paresse** : `preparation_reel.
telecharger_video` refuse tout schema autre que http(s), pour ne jamais lire un
fichier de la machine sur une URL de base. Une video deposee serait acceptee
puis ECARTEE au gel sans rien montrer -- exactement le defaut que ce chantier
corrige. Elle est refusee au depot, avec cette raison.

**Premiere surface ou un fichier entre depuis l'exterieur**, traitee comme non
fiable meme si le dashboard n'ecoute que sur `127.0.0.1` :
- le **type REEL** vient du decodage (`Image.format` dans `FORMATS_ACCEPTES`),
  jamais l'extension ni le `Content-Type`. SVG, PDF, archive et **HEIC** sont
  NOMMES dans le refus plutot que renvoyes en « illisible » -- le HEIC est le
  premier candidat sur un Mac, et « illisible » l'aurait laisse sans remede ;
- **la borne de taille tombe AVANT la lecture en memoire** : un
  `Content-Length` annonce au-dela de `TAILLE_MAX_OCTETS` (25 Mo) rend 413 sans
  lire un octet, et le flux est lu morceau par morceau (`request.stream()`,
  jamais `request.body()`) ;
- une **borne de pixels** lue dans l'EN-TETE (`PIXELS_MAX`, 50 Mpx) : 25 Mo de
  PNG decodent en gigaoctets ;
- **aucun nom de fichier n'arrive jamais au serveur.** Ce n'est pas un nom
  assaini, c'est un nom ABSENT : le corps de la requete est le fichier BRUT (pas
  de `multipart`, donc pas de dependance `python-multipart` non plus), la
  fonction qui nomme ne prend qu'un `bytes`, et le nom sur disque derive du
  contenu. Un nom ne peut pas composer un chemin par CONSTRUCTION. Preuve par
  mutation (`tests/publish/test_televersement.py`) : rendre le nom au choix de
  l'appelant fait rougir ce test, et lui seul.

**Les regles du projet ne sont pas contournees** : un fichier depose est un
CANDIDAT de plus, pas un passe-droit. `urls_interdites` ne le vise pas (il n'a
pas de source Instagram), `store.actifs_interdits` ne le cherche pas en base
(origine a part, voir `ORIGINE_DEPOT`), il ne porte aucun `@` donc aucun credit,
et le gel lui applique les memes regles qu'a tout media : plafond de dix, fond
jamais republie, une seule affiche, cadrage 4:5 ancre en haut.

**Connu et assume** : avant le gel, un visuel depose n'a **pas de vignette** --
sa copie est un `file://`, qu'un navigateur n'affiche pas, et `vignetteElement`
ne l'essaie meme pas. Le comportement est le meme pour toute copie locale
(actifs d'artistes) et date d'avant cette tache. Des que le creneau est gele, la
vignette est le FICHIER FIGE, avec son recadrage reel. Et le nom du fichier
depose n'est garde nulle part : la reserve le montre comme « photo 1200x1500 ·
fichier depose depuis le disque ».

### Anti-doublon, demontre

Rien ici n'appelle Meta. La reouverture est une ecriture CONDITIONNELLE sur
(`etat == GELE`, meme `post_id`), face a une reclamation d'agent qui fait
GELE → ECHEC **committee avant** tout appel a Meta : soit l'agent passe le
premier et notre UPDATE touche zero ligne (409), soit nous passons le premier et
le creneau devient PROGRAMME — or `_publier` ne selectionne que `etat == GELE`.
Aucun chemin ne mene a `media_publish` avant un nouveau gel. La course elle-meme
est verrouillee par
`tests/publish/test_manifeste_gel.py::test_une_reclamation_de_l_agent_pendant_la_reouverture_fait_echouer_l_ecriture`
(retirer la condition d'etat de l'UPDATE laissait toute la suite verte).
