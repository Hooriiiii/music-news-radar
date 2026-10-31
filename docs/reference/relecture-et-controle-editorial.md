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
(lecon du voyant de composition qui criait au loup, 2026-10-02). **Depuis le
2026-10-30 il ne passe plus a ATTENTION sur une cover qui deborde** : ce signal
lisait `render_report`, qu'aucun bouton n'ecrit, et il valait donc vert sans mesure ;
et « partiront ainsi » etait faux, le gel refuse une telle cover
(`preparation.py`). Detail : `docs/reference/dashboard.md`, « Bandeau de
supervision ».

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
va partir » dans la fiche. **`GET /relecture` ne rend plus de champ `deborde`
(decision du 2026-10-30)** : il affichait « inconnu » a cote de la mesure vivante
que la fiche demande a `/covers` en s'ouvrant, seul avertissement conserve. Rien
n'affirme plus qu'une cover tient sans l'avoir mesuree.

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
editer.

**Depuis le 2026-10-05, c'est la SEULE liste de visuels de la fiche.** Les cases
« Medias de l'actu » et la section « Visuels retenus » montraient le meme visuel
une seconde et une troisieme fois, et toutes deux editaient le BROUILLON en
ignorant les visuels retenus a la composition -- on les DESACTIVAIT donc des que
la liste etait arretee, avec leur raison. Elles ont disparu ; ce qu'elles
portaient d'utile (la provenance et le permalien d'un actif, les deux refus
« video sans URL directe » et « video trop longue pour une slide ») vit
maintenant sur la ligne de l'element et sur l'option de la reserve. Detail,
mesures et preuve par mutation : `docs/reference/dashboard.md`, « La fiche
d'edition d'un post, refondue le 2026-10-05 ».

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

### Ne pas publier (2026-10-30)

Decision de l'utilisateur : la console est un outil qui publie seul avec verification
humaine, et il veut gerer ce qui va sortir, dont « supprimer un post du programme ».
Le bouton **« 🚫 Ne pas publier »** remplace « Sauter », sur la ligne de Programmation,
dans « À relire » et dans la fiche, sur un creneau PROGRAMME ou GELE. **C'est un
retrait, jamais une condition** : ne rien faire publie a l'heure, et
`test_la_relecture_n_est_jamais_une_condition_de_publication` n'a pas bouge.

`PATCH /schedule/{id}` avec `sauter` etait limite a LIBRE/PROGRAMME a venir ; il a ete
etendu, sans second chemin d'ecriture. `reouverture.ne_pas_publier` partage avec
`rouvrir` la meme ecriture (`_invalider_le_gel`, sur `runner._reclamer`), seule la
destination change : LIBRE, desepingle, `article_id`, `post_id`, `fichiers`, `elements`
et `erreur` effaces, dossier fige supprime. A ne pas confondre avec `RAISON_RELECTURE`,
qui marque un gel a refaire : ici rien n'est refait, et l'erreur est effacee.

- **Pas de plancher de 20 minutes** : il protege un re-gel, or rien n'est refait. Le
  geste vaut aussi APRES l'heure, tant que le robot peut encore partir (Mac reveille en
  retard). Un creneau passe et libere n'est jamais rempli : l'arbitrage ne voit que les
  creneaux futurs. Les autres gestes restent refuses sur un GELE.
- **Atomique** : le creneau et l'exclusion de l'article partent dans la meme
  transaction. Une raison deja posee (dedup d'archive) n'est pas ecrasee.
- **Anti-doublon** : si l'agent a reclame, zero ligne touchee, 409 « Publication en
  cours ». Si nous passons les premiers, `_publier` ne selectionne que `etat == GELE`.
  Le retrait d'un PROGRAMME ne touche pas au dossier : un gel en cours perd sa propre
  ecriture conditionnelle, et `_menage` ramasse ses fichiers.
- **Le brouillon reste, Claude n'est pas repaye** : apres « Remettre dans la file »,
  `post_a_reprendre` raccroche le meme post. Le creneau libere peut etre repris par un
  autre article (normal) ; l'article ecarte, lui, ne revient pas (`file_exclu_raison`).
- **Un post sans article** (purge, `porte_un_post_orphelin`) se retire aussi, mais sans
  retour possible : la confirmation le dit au lieu de promettre une carte qui n'existe
  plus. Meme chose a terme pour tout article ecarte : non USED, il part a la purge
  (`retention_days`), et le retour avec lui.
- **Un arbitrage en panne apres le retrait ne rend pas 500** : le retrait est committe,
  et l'agent remplira le creneau a son passage.

`DELETE /posts/{id}` refuse desormais (409) une publication en vol, un post porte par un
creneau LIBRE, PROGRAMME ou GELE (la suppression vidait `post_id` et repayait Claude :
la raison renvoie vers « Ne pas publier ») et un post peut-etre en ligne (ECHEC portant
une trace de `publication_incertaine`, meme ancienne : une reclamation prise en retard
reste en vol au-dela de `_publication_en_vol`). La fiche masque « Supprimer » tant que le
post est au programme, ou si cette information n'a pas pu etre lue.

**Limites connues, deduites du code.** Un article ecarte SANS post compose n'est plus
« engage » (`election.articles_engages`) : l'election peut elire un autre membre de son
groupe, et la meme actu revenir par un autre article. Et l'annonce de relecture vaut un
par creneau : le post qui reprend un creneau deja annonce n'est pas annonce a nouveau.

**Preuves par mutation** (`tests/api/test_console_verification.py`, a la main) : retirer
la condition d'etat de `_reclamer` rougit
`test_une_reclamation_concurrente_fait_echouer_l_ecriture`, celle du post
`test_un_post_change_entre_temps_fait_echouer_l_ecriture`, committer avant l'exclusion
`test_l_exclusion_et_le_creneau_partent_ensemble_ou_pas_du_tout`. Temoin de
`test_un_passage_du_robot_apres_le_retrait_ne_publie_rien` : le meme creneau, sans
retrait, publie.

### Une correction humaine epingle (2026-10-31)

Toute ecriture de `PATCH /posts/{id}` qui CHANGE le contenu d'un post porte par un creneau
PROGRAMME (legende, hashtags, liste, variante) epingle ce creneau, dans la meme
transaction, par un UPDATE conditionnel sur (etat, post) : sinon l'arbitrage pouvait
remplacer le post corrige 10 minutes apres, le defaut que `rouvrir` corrigeait deja pour un
GELE. Gratuit, sans re-gel : un GELE garde ses fichiers
(`test_editer_la_legende_ne_jette_JAMAIS_les_fichiers_figes` n'a pas bouge). « Enregistrer »
sans rien changer n'epingle pas, un refus non plus. La fiche le dit (`DepartOut.epingle`)
et propose « Desepingler ». Un depot de visuel n'epingle pas : ce n'est qu'un candidat, le
mettre dans la liste passe par ici.

### Modifier l'accroche (2026-10-31)

`hl-hint` decrivait « une accroche » a entourer d'asterisques, sans champ ou le faire. La
fiche porte desormais ce que le gabarit lit vraiment (`layout.GABARIT_*.roles`) : `headline`
pour `kinda_post`, `title` et `subtitle` pour `kinda_reel`, le rappel sous le champ. Meme
chemin que le reste : `ecrireDepuisLaFiche`, `PATCH /posts/{id}` avec `slides`, donc re-gel
automatique d'un GELE, epingle d'un PROGRAMME, refus a moins de 20 min dit a cote du champ
(`#etat-accroche`, parce qu'a 390 px la barre d'actions est loin) sans rien ecrire et sans
perdre la saisie. Claude n'est pas repaye, rien n'approche Meta.

**Le piege, et la seule regle nouvelle cote serveur.** Le gel rend `slides[0]`, mais `/covers`
et le choix de variante lisent `variants.variantes`. Ecrire `slides` seul figeait le bon
texte, montrait l'ancien, et un aller-retour de variante effacait la correction en 200.
`posts._aligner_variante_sur_la_cover` recopie donc les textes dans la variante retenue (la
premiere si aucune), en FUSION. Preuve : `tests/publish/test_accroche_gel.py` compare le JPEG
gele par `_geler` et `preparer_fichiers`, avec le vrai rendu Chromium, a l'accroche corrigee
et a l'ancienne ; retirer l'alignement fait perdre l'edition (mutation dans le meme fichier).

**Son propre bouton, desactive sans changement** : envoyer `slides` rouvre un gel meme a
l'identique, et « Enregistrer » legende ne doit jamais couter un gel, ni etre refuse a
20 min. Son tampon (`brouillonAccroche`) survit au re-rendu comme celui de la legende,
garde a la fermeture comprise. Apres ecriture, `/covers` est relu : un debordement s'affiche
sous le champ, avec la saisie, et un apercu en panne se dit sans annuler l'ecriture.

### Le robot ne remplace jamais une correction humaine (2026-10-31)

`_reperes_perimes` comparait tout texte a `post.created_at` : un « ce soir » juste, ecrit a la
main sur un post compose la veille, faisait lacher le post au gel, et la composition suivante
repayait Sonnet et ecrasait la correction. Un texte que personne n'avait lu partait a la place.

**La reference de temps d'un texte est sa derniere edition humaine.** Elle vit dans
`posts.variants["edition_humaine"]`, **sans migration** : `created_at` date la composition, et
aucun champ existant ne separe l'humain du robot. Ecrit en FUSION par `PATCH /posts/{id}`
seulement si le contenu change (`relecture.noter_edition`). Chaque texte (`headline`, `title`,
`subtitle`, `caption`) garde son heure AVEC sa valeur : revenir sur une variante du robot rend
son controle a ce texte. Choisir une variante n'est donc pas une correction. `corrige_le` note
ce qui n'est pas un texte (hashtags, liste, fond).

`relecture.porte_une_correction` : ni `_geler` ni `_composer` ne relachent plus un tel post.
**Si un repere reste perime** (ecrit lundi « ce soir », parti mercredi, ou un texte du robot a
cote d'une correction), le post part tel quel, conformement a « relance, puis acceptation avec
avertissement » : une relance serait un appel Claude sur un texte humain, donc exclue.
L'avertissement est ecrit dans `creneaux.erreur` du GELE (ligne de Programmation) et notifie une
fois, a ce gel. **Un texte du robot jamais touche garde le controle entier** : le post 153 ne
revient pas.

**Limites.** Une correction faite avant ce correctif n'est pas datee : elle reste vue comme du
robot. `_brouillon_perime` (visuels interdits, fond absent) peut encore lacher un post corrige :
ce sont des regles du projet, pas un controle de forme. Prouve par test
(`tests/schedule/test_verification_humaine.py`), jamais observe en production.

### Ce que l'humain a vu est ce qu'il frappe (2026-10-31)

`PATCH /schedule/{id}` avec `sauter` envoyait `{sauter: true}` et la liste ne se rechargeait
jamais seule, alors que l'arbitrage reattribue un creneau non epingle a chaque passage.
Reproduit sur le vrai code (bilan du 2026-10-31) : l'ecran montre A, le creneau porte B, le
retrait rend 200, B est exclue, A reste candidate et l'arbitrage la remet a cette heure, et
l'ecran dit « A ne partira pas ». Une confirmation fausse sur le geste le plus sensible.

- `occupant_attendu` et `post_attendu` sont **obligatoires** sur `sauter` et sur `epingle`
  (422 sinon ; `null` est une valeur, un creneau vu vide). Ils sont la CONDITION de l'UPDATE,
  a la place de la lecture du moment : pas un `if` avant l'ecriture. Occupant change : 409
  `OCCUPANT_CHANGE`, rien n'est ecrit, ni le creneau ni l'exclusion. Le post compte autant que
  l'article : une recomposition est un texte que l'humain n'a pas lu.
- Les etats restent juges avant : publication en vol ou incertaine, creneau termine.
- Les trois surfaces de retrait et les deux epingles envoient ce que leur ligne affiche
  (`DepartOut.article_id` est ajoute pour « A relire » et la fiche). Sur le 409, elles relisent
  `/schedule` et nomment l'occupant actuel, ou « recompose » si l'actu est la meme.
- La Programmation se recharge avec le bandeau (60 s) et au retour sur l'onglet, seulement
  si elle est affichee. Un glissement reporte (`rechargementDiffere`) et un selecteur ouvert
  suspend : le minuteur suivant reprend. `#schedule-lue` dit « liste lue a HH:MM » ; une lecture
  en echec garde la liste, passe la ligne en erreur et dit qu'elle peut avoir change. Le
  numero de tour (`tourDeChargement`) empeche une reponse ancienne d'ecraser une plus recente.

**Prouve par test, jamais observe en production.**
`tests/schedule/test_verification_humaine.py` (deux sessions, rejouable sur Postgres) et
`tests/dashboard/test_dashboard_programmation_vivante.py` (Chromium reel, horloge de page
controlee).

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
