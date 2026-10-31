# Le dashboard : supervision, refonte UX, panneau Artistes, charte

**Quand lire ce fichier** : avant de toucher a `app/static/dashboard.html`, a
`app/supervision.py`, a `GET /supervision`, au panneau Artistes ou aux couleurs
de la console. Il porte la regle « une valeur qu'on n'a pas pu lire s'affiche
INCONNUE, jamais verte », les trois regles de conception sorties de l'audit UX,
les contrastes mesures de la charte et la raison pour laquelle le rose ne
signale JAMAIS une panne.

## Bandeau de supervision (`app/supervision.py`, `GET /supervision`)

Un bandeau permanent en tete du dashboard, visible quel que soit le panneau, qui
repond a une seule question : **« la machine tourne-t-elle, et qu'a-t-elle fait
en mon nom ? »** Onze voyants : mode de publication, **agent de publication**
(voir ci-dessous), composition/credits, creneaux du jour, **relecture** (voir
« La fenetre de relecture », juste au-dessus), dernier passage, sante des
sources, quota Graph, pauses (yt-dlp, visuels sautes), et les DEUX jetons Meta.

**Pourquoi.** Semaine du 2026-09-26 au 2026-10-02 : credits Anthropic epuises
deux fois (plus une seule composition pendant deux jours), 46 % des creneaux
manques dont 25 sur 37 sans raison, 751 erreurs de quota Graph avec 80 % des
comptes Instagram plus lus, et une actu deja publiee sur le point de repartir —
**aucun de ces incidents n'etait visible a l'ecran**, et chacun a ete decouvert
par hasard. Le signal existait a chaque fois (`sources.state.last_error`,
`creneaux.erreur`, les fichiers d'etat a cote du jeton) ; rien ne le lisait.

**Ca ne coute RIEN** : aucun appel paye, aucun appel reseau vers Meta ou
Anthropic a l'affichage. Une requete sur la base locale et quatre fichiers JSON.
Seule exception traitee a la source : l'echeance du jeton de SOURCING ne se lit
que chez Meta, donc `surveiller_jeton_sourcing` la **consigne** desormais dans
`sourcing_token_alert.json` (`expire_le`, `releve_le`) a chaque passage horaire —
il appelle `debug_token` de toute facon, ca ne coute pas un appel de plus — et le
bandeau la relit. Tant qu'aucun passage ne l'a consignee, le voyant dit
« inconnu ».

**La regle qui gouverne tout le module : une valeur qu'on n'a pas pu lire
s'affiche INCONNUE, jamais « tout va bien ».** Un voyant vert par defaut sur une
machine en panne est pire que pas de voyant — il apprend a ne plus regarder.
D'ou `lire_json`, qui distingue **trois** issues (absent / lisible / illisible)
la ou les lecteurs d'etat du pipeline (`app/publish/mode.py`,
`app/pipeline/quota_graph.py`) les confondent VOLONTAIREMENT en une seule : eux
doivent continuer a tourner sans fichier, celui-ci doit DIRE ce qu'il ne sait
pas. D'ou aussi `niveau_global`, ou l'inconnu passe devant l'attention (une
valeur non lue peut cacher une panne), et l'enveloppe de `superviser` : une
exception imprevue devient un voyant « inconnu » qui porte le type de
l'exception — jamais un voyant ABSENT (une case vide se lit « rien a signaler »)
ni une 500 qui viderait le bandeau. Propriete NEGATIVE, donc facile a casser par
un `or "ok"` bien intentionne sans qu'aucun autre test ne rougisse :
`tests/socle/test_supervision.py` et `tests/dashboard/test_dashboard_supervision.py` la
verrouillent aux quatre etages (agregation, lecteurs, enveloppe, JS).

**Chaque voyant porte sa raison en clair**, adjacente et visible sous les
pastilles — jamais seulement dans un `title`. Les details (un bloc par voyant)
sont derriere « Details ».

**Le voyant « composition » criait au loup, et c'est la faute la plus grave
qu'un bandeau puisse commettre** (2026-10-02). Il affichait « 4 article(s)
attendent une note depuis plus de 2 h alors que chaque passage horaire note
tout : scoring interrompu » **pendant que** le passage precedent notait 57
articles sans une seule erreur. Les quatre etaient d'anciens DOUBLONS — un
doublon n'est jamais note, c'est l'economie que `link_duplicates` existe pour
faire — dont le representant venait d'etre PURGE : `articles.duplicate_of_id`
est `ON DELETE SET NULL`, donc la purge les a rendus notables **apres** le
scoring du meme passage. Leur `fetched_at` avait 24 h, leur attente reelle
quelques minutes, et le passage suivant les a notes. Verifie sur les quatre :
tous portaient une `event_observations` creee par `cluster.lier`, donc tous
avaient bien ete lies.

`fetched_at` n'est donc PAS la date a laquelle un article est devenu notable, et
rien dans le schema ne la date — ce qui ne vaut pas une colonne. Le voyant
mesure desormais la PREUVE au lieu de la duree
(`articles_jamais_atteints_par_le_scoring`) : `score_pending` sert les articles
notables par `fetched_at` CROISSANT et **sans limite** au passage horaire, donc
un article note a T prouve que le scoring a tourne apres T, et qu'il aurait
servi avant lui tout article notable plus ancien. On ne compte que les articles
sans note dont le `fetched_at` depasse STRICTEMENT le dernier `fetched_at` deja
note. Rien de note du tout = aucun repere = on compte tout (regle 1 du module :
le doute ne se degrade jamais en vert). Faux negatif ASSUME : `run_veille` note
au plus 20 articles toutes les 5 min, donc un arriere peut faire avancer le
repere au-dela d'articles encore en attente — le passage horaire le resorbe dans
l'heure, et un trou d'une heure vaut mieux qu'un faux positif permanent, qui
apprend a ignorer le bandeau.

**Et un article LEUR PROPRE doublon, lui, est reellement bloque** (meme jour,
voir « Dedup a deux etages ») : il est compte a part
(`articles_leur_propre_doublon`), **sans** le filtre `duplicate_of_id IS NULL`
qui le rendait invisible, avec son propre message et la commande de reparation —
dire « scoring interrompu » sur ce cas serait un autre message faux. Il figure
TOUJOURS dans les details, meme quand une branche plus grave prend le titre :
une corruption ne doit pas etre masquee par une panne passagere.

**Deux populations mal cadrees restent CONNUES et non corrigees** (relevees le
2026-10-02, hors perimetre du correctif ci-dessus) :
- `voyant_creneaux` compte comme « en echec » un creneau en `ECHEC` dont
  l'erreur est `RAISON_RECLAMATION` — or `app/api/posts.py` traite
  deliberement ce meme etat comme « Publication en cours » tant que
  `publier_a` est a moins de 20 min. Pendant les quelques minutes d'une
  publication en vol, le bandeau annonce donc un echec qui n'en est pas un.
  L'information pour les distinguer existe (`RECLAMATION_EN_COURS`).
- `voyant_passage` ecrit « N source(s) en erreur **au dernier passage** », alors
  que `sources.state.last_error` n'est PAS vieilli : une source qui n'a pas ete
  APPELEE (pause de quota Graph) garde son erreur — c'est voulu cote pipeline
  (« effacer l'ancienne trace ferait oublier une source reellement morte »),
  mais la phrase, elle, est fausse. `last_error_at` est deja ecrit par
  `app/pipeline/health.py` et n'est lu par personne.

**L'agent de publication pouvait mourir sous un bandeau vert** (2026-10-30,
DEDUIT DU CODE : la panne n'a pas ete observee). Aucun voyant ne mesurait son
passage, « Dernier passage » lit l'ingestion, et MANQUE n'est ecrit que par lui.
- *Ecriture.* `scripts/run_publication.py::main` consigne la fin dans
  `publication_passage.json` (`app/schedule/passage.py`), dans un `finally` : retour
  du mode `arret`, `SystemExit` et exception imprevue y passent tous. Point choisi :
  la FIN, donc rien n'est ecrit pendant la publication et rien ne peut la retarder.
  Cette ecriture ne leve jamais. Un seul ecrivain et un fichier qui decrit UN
  passage : pas de relecture-fusion ; atomique, temporaire nomme par processus.
  Le champ `panne` porte les pannes d'etape que `tourner` avale (sinon un agent dont
  tout echoue se lirait sain), nom d'etape et type d'exception (`rapport.pannes`), et
  jamais le message d'une exception, qui peut porter une URL avec son jeton. Une faute
  d'article n'y entre pas : ce n'est pas une panne de l'agent, et le voyant de
  composition, les creneaux et la notification la disent deja.
- *Un voyant de plus* (`agent_publication`) plutot que dans « Publication » : le
  mode est une DECISION, ceci est un FAIT. Dans la meme pastille, `Aperçu` (ATTENTION
  voulue) masquerait un agent mort. Le voyant vaut en mode `arret` aussi : il faut
  savoir que l'agent est mort AVANT de repasser en reel.
- *Le seuil derive des reglages* : 2 x `publication_intervalle_s` +
  `publication_avance_s` + `publication_tunnel_budget_s`, soit 35 min 30. Un agent
  sain peut se taire un intervalle plus un passage long, dont ces deux attentes sont
  les seules bornees ; le second intervalle couvre yt-dlp, ffmpeg et la composition.
  DEDUIT DU CODE : aucune duree de passage n'est journalisee, donc aucune n'a ete
  mesuree. `publication_intervalle_s` est le reglage que lit desormais
  `install_agents` (meme valeur, 600 s), comme `pipeline_intervalle_s`.
- *Le reveil, le cas qui gouverne.* Le Mac dort, c'est normal, et le bandeau tourne
  lui aussi depuis le reveil : il ne se rappelle rien de sa nuit. Sans garde, il
  crierait a la panne a chaque ouverture d'ecran, avant que l'agent ait fini le
  passage rattrape par launchd. Il demande donc au noyau (`reprise_machine` :
  `sysctl kern.waketime kern.boottime`, local, gratuit) et ne conclut qu'apres un
  seuil complet d'eveil. Avant : INCONNU, ni vert (aucune mesure fraiche) ni rouge
  (rien n'accuse l'agent). Date illisible : INCONNU aussi. Observe le 2026-10-30 :
  `kern.waketime` valait la derniere ligne `Wake` de `pmset -g log`. Non verifie :
  son comportement sur un `DarkWake to FullWake` (6 lignes, contre 893 lignes
  `Wake` ou `DarkWake`, dans le journal lu ce jour-la). Faux negatif assume : un Mac qui se rendort sans cesse avant le seuil
  ne conclut jamais.
- *`voyant_creneaux`.* Un creneau encore PROGRAMME ou GELE au-dela de
  `publication_retard_max_minutes` n'est plus jamais vert (le « 0/6 publiés » vert).
  ATTENTION d'abord, et PANNE quand l'agent a eu un seuil complet pour l'ecrire,
  depuis le depassement ET depuis le reveil : un agent sain repasse toutes les
  10 min, et un Mac rouvert a midi trouve toute sa matinee dans cet etat. Tous jours
  confondus, pour ne pas redevenir vert a minuit. LIBRE n'est pas compte : hors du
  perimetre decide, l'agent mort y est vu par son propre voyant. En mode `arret`,
  `tourner` sort avant `_marquer_manques` : le constat reste ATTENTION et le dit, sans
  accuser l'agent ; mode illisible (`_mode_lu`) : INCONNU.
- *Preuve negative* : `tests/socle/test_supervision.py` recompile ce module avec UNE
  ligne mutee (trop vieux devenu vert, absent devenu vert, garde du reveil retiree,
  constat des creneaux retire) et exige que le niveau change.

**Le voyant Relecture ne juge plus le debordement** (decision du 2026-10-30). Il
lisait `render_report`, que seul `POST /posts/{id}/render` ecrit et qu'aucun bouton
n'appelle : `deborde = bool(...)` rendait donc VERT une mesure qui n'existe pas,
pendant que la fiche disait « inconnu ». Son message « partiront ainsi » etait faux :
le gel refuse une cover qui deborde (`app/publish/preparation.py`). Il reste un
voyant « ce qui attend », toujours `ok`. La mesure vivante est `/covers`, a
l'ouverture de la fiche, dont l'avertissement demeure. `DepartOut.deborde` et les
mentions de la liste et de la fiche sont retires. `POST /posts/{id}/render` reste :
ses seuls appelants sont ses tests, et il n'est pas supprime ici.

**Un endpoint dedie** plutot que cinq schemas elargis : la question est
transversale, le niveau global doit se calculer a UN endroit (une regle negative
repartie sur cinq vues n'est verrouillable nulle part), et le bandeau se
rafraichit toutes les 60 s quel que soit le panneau.

`SourceOut` expose desormais la sante (`last_error`, `last_error_at`,
`last_ok_at`, extraits de `sources.state` par `app/api/sources.py::vue`) : elle
etait ecrite a chaque ingestion depuis le debut et n'etait exposee par rien. Le
panneau Sources l'imprime sous chaque URL.
## Le dashboard, apres la refonte UX du 2026-10-02

Audit mene avec le skill `ux-audit`, couches code ET captures. Sa conclusion
gouverne la refonte : **c'etait une console pour un robot qui publie six fois
par jour, et elle ne savait RIEN dire du robot**. Tous les incidents de la
semaine du 26/09 au 02/10 -- credits epuises deux fois, 46 % de creneaux
manques, 751 erreurs de quota, une actu deja publiee sur le point de repartir --
etaient invisibles a l'ecran. L'utilisateur decouvrait les pannes par hasard.

**Le defaut le plus grave n'etait dans aucune liste** : `renderPostDetail`
appelait `querySelectorAll(".media-choice")` sur toute la fiche, or la section
« Visuels retenus » produit la meme classe SANS case a cocher -- `TypeError`
avant `detailEl.hidden = false`. **126 posts sur 157 (80 %) ne s'ouvraient pas
du tout**, silencieusement : le clic ne faisait rien. Present depuis la
migration 0018. Aucune lecture de code ne l'a trouve ; il a fallu ouvrir un
VRAI post dans un navigateur. La recherche est bornee a `#medias-actu`, avec un
test de regression.

**Trois regles de conception sorties de l'audit**, chacune verrouillee par un
test :
1. **Une valeur qu'on n'a pas pu lire s'affiche INCONNUE, jamais verte.** Un
   voyant au vert sur une machine en panne est pire que pas de voyant. Quatre
   mutations le verifient, dont `GRAVITE[INCONNU] = 0`.
2. **Une information de PANNE ne devient jamais moins visible** pour gagner de
   la place. Le repli du bandeau plie ce qui va bien, jamais une raison.
3. **Deux controles qui semblent redondants sont une question de COMPOSITION,
   pas de soustraction.** Navigation et filtres partageaient une classe et une
   rangee : portees differentes (application contre grille), donc deux formes
   (onglets contre pastilles), jamais une fusion.

**Ce qui etait casse a l'ecran et ne se voyait dans aucun test** : le panneau
Evenements avait son titre ecrase a zero de large (`.event-head` en flex, titre
a `flex: 1` contre sept badges a largeur intrinseque) -- Chromium le comptait
lui-meme comme *hidden*. Recompose en blocs empiles ; un test mesure la
LARGEUR RENDUE (>= 95 % de la ligne a 1280/820/390 px), pas une propriete CSS.
Aucune media query n'existait : l'entete faisait **705 px sur un telephone**,
plus que l'ecran, en `sticky`. Elle cesse de l'etre sous 900 px.

**Quatre textes de l'interface affirmaient des choses fausses** (« toutes les
2 h » quand le passage est horaire, « 14 jours de retention » quand elle est a
**2**, l'aplat Kinda decrit comme une issue publiable, le credit web presente
comme actif). Les deux premiers sont desormais **derives de la config** via
`GET /reglages` -- un nombre recopie dans une chaine d'interface derive au
premier changement de reglage.

**La console publie seule avec verification humaine (decision de l'utilisateur,
2026-10-30)** : il ne publie jamais a la main, il gere le programme. Sont donc retires
« Valider le statut » (`approved`, lu par aucun code), « Marquer comme publié » et
« 📸 Posté » (ils mentaient sur le compte, et USED ecartait l'article de la file), plus
l'option « Validé » du filtre : une ligne ancienne s'affiche encore « Validé », on ne
reecrit pas l'historique. La vue « 📸 Postés » reste, en lecture. Sur la carte, « ✓ Vu »
n'apparait que sur un article `new` et « ↩ À traiter » que sur un `reviewed` : un geste
sans effet ou que le serveur refuserait n'est pas offert, et sur un USED il n'y en a
aucun. Les gestes de carte lisent la reponse et disent un refus a cote du bouton
(`.refus-statut`), plus d'`alert`. « 🚫 Ne pas publier » remplace « Sauter » partout :
`relecture-et-controle-editorial.md`, « Ne pas publier ». Preuves sur Chromium, mutations
comprises : `tests/dashboard/test_dashboard_verification_humaine.py`.

**Remettre un article dans la file** : `POST /articles/{id}/reintegrer`
(bouton « ↩ Remettre dans la file » sur la carte, pastille « ⛔ hors file »
depuis `ArticleOut.file_exclu_raison`). Il **refuse (409) une exclusion posee
par la dedup d'archive** et renvoie vers `scripts.reparations.marquer_doublon_manuel` :
effacer la raison seule ne tient pas, `link_duplicates` relierait l'article au
passage suivant (voir « Dedup a deux etages »). Une exclusion de gel sur un
article par ailleurs doublon se leve, elle : c'est la RAISON qui decide, pas le
statut de doublon. Si le post est peut-etre en ligne, le bouton ouvre une
verification sur la carte et n'envoie la confirmation qu'apres « Vérifié, il
n'est pas parti » (`publication.md`, « Exclusion »).

**Une legende tapee survit desormais a un re-rendu de la fiche.** Six gestes
reconstruisaient `detailEl.innerHTML` depuis la reponse du serveur et
detruisaient le texte en cours de frappe -- le geste editorial normal (ecrire
la legende, puis choisir la cover qui va avec) perdait la legende. Un tampon a
UN seul point de capture (l'evenement `input`) et UN seul point de lecture
(`textesAReinjecter`, pure), plus une garde a la fermeture.

**Le lien vers Instagram est DEDUIT** du shortcode (base 64 de l'id media), pas
verifie : aucun permalien n'est stocke apres `media_publish`. S'il se trompe le
lien ouvre un 404, et l'identifiant brut reste lisible a cote. Le vrai remede
serait de stocker le `permalink` que Meta renvoie a la publication.

### Le panneau Artistes (2026-10-04)

La moitie DETECTION de Kinda etait INVISIBLE. `artists` / `artist_mentions` sont
« l'actif de Kinda » et le flux score « le deal-flow du label » ; en production,
**4 752 artistes et 13 533 citations archivees depuis des semaines**, et aucune
interface ne les montrait — `GET /artists` existait, teste, **sans un seul
consommateur**. L'audit UX du 02/10 l'avait releve.

**Ce panneau ne pouvait PAS etre construit plus tot**, et c'est le point le plus
important a retenir : le signal etait faux jusqu'au 03/10 (tri par VOLUME de
citations, donc Taylor Swift et BTS en tete). Pose sur cet ancien signal, le
panneau aurait propose de signer Taylor Swift. Il arrive apres la reparation,
pas avant.

Trois choses se lisent d'un coup d'oeil, parce que ce sont elles qui separent
une decouverte d'une star :
- **la TENDANCE** (`acceleration`) — « ↗ on commence a en parler », « → regulier »,
  « ↘ ca retombe », ou **« tendance inconnue »** ;
- **« deja suivi » contre « a decouvrir »**, avec le NOM du compte — l'action
  implicite du panneau, et un FAIT (une source Instagram active de categorie
  artiste liee a la fiche), jamais un jugement de notoriete ;
- **les CITATIONS elles-memes** au clic sur la ligne (titre, source, date,
  pertinence, emergence, renvoi vers l'actu) : juger sur pieces, pas sur un score.

Reglages manipulables dans la barre du panneau, en `select` et non en `.chip` —
les pastilles appartiennent a la grille, les onglets a l'application, et melanger
les trois est la confusion de portees que la refonte du 02/10 a demontee :
fenetre, `citations_min` (3 pour une shortlist, **1 pour fouiller un
primo-signal**), `min_emergence`, nombre affiche, et « inclure les comptes deja
suivis ».

**Le seuil de la tendance est mesure, pas arrondi.** `ACCELERATION_MONTE` vaut
**0,34** et non 0,3 : `acceleration` porte un biais structurel de **+1/n** pour un
nombre IMPAIR de citations regulierement espacees (son propre docstring le dit),
donc **trois citations equidistantes valent +0,33**, et trois est le plancher par
defaut. Un seuil sous 0,33 qualifierait de montee la regularite la plus plate du
cas le plus frequent. Verifie sur les donnees REELLES le 2026-10-04 : FRÈJUS et
G2G sont exactement a 0,3333 et se lisent « → regulier » ; Ahadadream a 0,43 se
lit « ↗ on commence a en parler ». Au-dela de trois citations le biais ne fait
que diminuer : le seuil est conservateur dans le bon sens.

**La regle « inconnue, jamais zero » est ici la plus couteuse du dashboard**, et
elle passe par UN SEUL point (`mesure()` dans `app/static/dashboard.html`).
`velocite` vaut `None` sous deux citations datees, `acceleration` sous trois, et
17 % des citations n'ont aucune note d'emergence : une acceleration inconnue
affichee « 0 » se lit **« cet artiste stagne »**, une affirmation fausse sur la
seule grandeur qui separe une decouverte d'une star. Preuve par mutation sur
Chromium reel (`tests/dashboard/test_dashboard_artistes.py`) : on remplace le garde-fou par
le `|| 0` exact qu'un contributeur presse ecrirait, et la ligne doit afficher
« → regulier 0.00 » — la mesure refuse. L'inconnu porte sa propre teinte
(gris-bleu, celle de la supervision), jamais celle d'un des trois verdicts ni
l'accent de la marque, qui se lirait comme une promesse.

Le seul geste d'ECRITURE du panneau est `PATCH /artists/{id}` avec
`source_instagram_id` : rattacher une fiche a son compte quand le nom ne suffit
pas. Sans compte lisible, le selecteur est desactive **avec sa raison ecrite a
cote** — jamais gris et muet.
### La fiche d'edition d'un post, refondue le 2026-10-05

Retour de l'utilisateur : « **Je n'arrive pas a enregistrer**, et l'UX est
desastreuse. L'interface doit etre plus claire, moins de fonctions dispatchees
pour rien qui peuvent etre fusionnees. Et l'interface ne respire pas. »

**Le « bug » d'enregistrement n'en etait pas un, et c'est pire.** Verifie au
navigateur : le clic envoyait bien `PATCH /posts/176`, le serveur repondait
**200**, la fiche se rechargeait. **Rien ne le disait** -- aucun etat « en
cours », aucun libelle qui change, aucune confirmation. Un succes muet est
indistinguable d'un echec muet, donc l'utilisateur conclut que ca ne marche pas,
et **il a raison de le conclure**. Meme defaut que l'ecriture muette de la
categorie d'une source (corrigee le 02/10), reste sur **cinq** boutons de cette
fiche -- et un double-clic envoyait deux ecritures.

Correctif : UN point de passage pour toute ecriture de la fiche,
`ecrireDepuisLaFiche`. Il verrouille le geste en cours (plus de seconde ecriture
sur un double-clic), montre que quelque chose se passe, et DIT le resultat --
reussite comprise, dans une zone adjacente aux boutons (la barre d'actions est
`sticky`). Un echec y RESTE avec sa raison, au lieu d'un `alert()` qu'on chasse
d'un clic et qui ne dit plus rien ensuite ; `raisonDuRefus` en extrait le
`detail` plutot que d'imprimer le JSON brut.

La propriete est NEGATIVE et silencieuse a casser -- retirer la ligne qui annonce
la reussite ne fait rougir aucune exception, aucun test de structure, et la page
reste belle. D'ou la **preuve par mutation** sur Chromium reel
(`tests/dashboard/test_dashboard_ecritures.py`) : on retire les deux annonces du
chemin qui REUSSIT, le `PATCH` part toujours, et l'ecran redevient muet -- c'est
mot pour mot l'etat du 05/10. Le message survit au re-rendu (`dernierGeste`, hors
DOM), meme procede que le tampon de la legende : les gestes rappellent tous
`renderPostDetail`, donc un message pose dans le DOM juste avant serait efface
par le rendu qui suit l'ecriture reussie.

**TROIS listes montraient le meme visuel** : « Visuels retenus » (les actifs),
« Medias de l'actu (carrousel) » (des cases a cocher sur `article.media`) et
« Ce que Meta recevra ». Decision de l'utilisateur : **il n'en reste qu'UNE**, la
liste finale -- celle qui part. Les candidats se proposent AU MOMENT D'AJOUTER,
pas en permanence dans deux sections concurrentes. Les deux premieres editaient
d'ailleurs le BROUILLON en ignorant les visuels retenus a la composition : cocher
une case pouvait faire disparaitre de la publication un visuel que le gel aurait
repris.

Rien d'utile n'est parti avec elles, et c'est ce que
`tests/dashboard/test_dashboard_fiche.py` verrouille :
- l'origine, le compte, la **date du post** et le lien « voir le post » ne
  vivaient que dans « Visuels retenus » ; ils sont desormais sur la ligne de
  l'element. `candidats_editables` transporte donc `permalink` et `publie_le`,
  qu'il jetait ;
- cocher / decocher un media de l'actu est devenu « ajouter » / « retirer », qui
  passent par `carrousel_explicite` et sont donc REELLEMENT pris en compte par le
  gel ;
- les deux refus que portaient les cases (une video **sans URL directe** -- un
  `PATCH` avec `url: null` echoue en 422 -- et une video **trop longue pour une
  slide**) sont desormais sur l'option de la reserve, `disabled` AVEC sa raison ;
- un post qui n'est sur **aucun creneau** reste editable : `GET /relecture` ne
  rend `candidats` que pour un creneau, donc la fiche recalcule la meme reserve
  localement (`reserveLocale`), regle du 2026-09-17 comprise.

**La legende etait affichee DEUX FOIS** : la zone editable, puis « Ce qui va
partir » en entier. Une zone d'edition, plus un apercu **repliable**. Les
COMPTEURS, eux, restent toujours visibles (505/2150, 8/30 hashtags, credit ; le
debordement en a ete retire le 2026-10-30, voir « Bandeau de supervision ») : ils signalent un depassement avant qu'il coute un post, les cacher
dans le repli les rendrait inutiles.

**Le selecteur de format MENTAIT**, et c'est le defaut le plus couteux des trois.
Post 176 : `template = "kinda_reel"` en base, et l'ecran montrait POST actif,
REELS grise « pas d'URL directe vers la video ». **Deux causes distinctes.**
1. Aucun des deux boutons ne portait `on` dans ce cas (chaque libelle etait teste
   separement contre `post.template`), et un REELS grise a cote d'un POST normal
   **se lit** « POST est l'actif ». Le bouton du gabarit COURANT porte donc `on`
   dans tous les cas, et n'est **jamais desactive** : on ne grise pas l'etat dans
   lequel on est.
2. `raisonPasDeReel` ne mirrorait que `app/content/media.py::videos_exploitables`
   et ignorait `video_recuperable` -- la SECONDE question, « yt-dlp saura-t-il
   aller la chercher au gel ? ». Meta omet `media_url` des reels a musique
   protegee, donc de presque tous les reels d'artistes et de festivals : sans
   elle, le dashboard annonce impossible ce que le pipeline fait tous les jours.
   Mesure du 05/10 sur l'article 22571 (Tomorrowland) : `store.source_video_reel`
   rend **"ytdlp"** et `raison_ineligible_reel` rend **None**, pendant que la
   fiche affichait « pas d'URL directe ». Le manifeste du gel, DANS LA MEME
   FICHE, disait deja « video recuperee par yt-dlp ».

Deux conditions du serveur ne sont pas visibles du navigateur (le type de la
source, `PUBLICATION_REEL_YTDLP`) : on penche du cote qui est deja la regle du
depot (« mieux vaut proposer le reel que griser un bouton sur une donnee
absente »), et l'eligibilite est de toute facon REVERIFIEE au gel, qui libere le
creneau sans exclure. Troisieme cas, celui qu'on ne peut pas resoudre en grisant :
le post EST un reel et l'actu n'offre plus de video -- griser mentirait sur la
base, se taire cacherait un vrai probleme, donc on dit ce qui va se passer.

**« L'interface ne respire pas »** est un jugement visuel, et on n'y repond pas
depuis le code : constate sur capture a 1440 / 900 / 390 px. La fiche enchainait
**neuf `.section-title` de meme poids** sur un fond uni -- aucune hierarchie, donc
aucun endroit ou l'oeil se pose. Trois niveaux desormais : le BLOC (Cover / Ce que
Meta recevra / Texte publie), le sous-titre a l'interieur d'un bloc (Format, Image
de fond), la note. Le depart passe en BANDEAU en tete, parce que c'est le cadre de
tout le reste et non une section de plus en bas. Et la classe `.note` existe parce
que la moitie des phrases **explicatives** portaient `.reel-reason`, donc le rouge
d'alerte : le rouge ne voulait plus rien dire la ou il compte. Hauteur de la fiche, mesuree sur capture, pour la MEME
information : **2150 -> 1572 px** a 1440, **2083 -> 1544** a 900,
**2882 -> 2238** a 390.

Enfin « Supprimer » etait le voisin de meme poids d'« Enregistrer » : il part a
l'autre bout de la barre (`margin-left: auto`), qui devient `sticky` -- sauf sous
700 px, ou les boutons passent pleine largeur et une barre collante mangerait la
moitie de l'ecran.

### « Programmer » remplace « Générer le post » (2026-10-31)

Règles, calcul de purge et limites : `publication.md`, « Programmer ». Ici, l'écran.

- **Un seul sélecteur, deux sens** (`ouvrirSelecteur`) : « Programmer » / « Changer » sur la
  carte, « Choisir une actu » sur la ligne de créneau (avec titres, pertinence et âge, sans
  identifiant à recopier : le `prompt` numérique et « ✏️ Remplacer » sont partis), et le
  même bouton sur une citation vivante et l'actu représentante d'un événement
  (`EventObservationOut.representant`), sans code dupliqué. Pas de `prompt`, `alert` ni
  `confirm`.
- Le prochain créneau compatible en premier, puis par jour. Une option impossible est
  désactivée, sa raison à côté, jamais dans une infobulle. Le mode est LU (`annonce_mode`) :
  en `arret`, le sélecteur dit que rien ne sera composé ni publié.
- La validation nomme ce qui sort, avec une étape de plus si l'occupant est épinglé ou
  composé. Un refus s'affiche à côté du bouton, avec la raison du serveur.
- La carte montre l'état réel (`ArticleOut.creneau`, lecture groupée) : « 🗓 jeu. 18:00 » et
  « Changer » ; gelée, l'heure sans bouton ; publiée ou exclue, pas de Programmer. Après le
  geste, seules les cartes touchées se repeignent : un `load()` perdrait le résultat.
- Une ligne programmée sans post dit « composé vers … », ou « au prochain passage », ou
  « composition suspendue » en mode `arret`. « Désépingler » reste sur un créneau mort.
- **Retraits.** Le sélecteur POST / REELS devient `#format-lu`, en lecture seule, et
  `raisonPasDeReel` part avec ses miroirs navigateur. « ⬇ Télécharger » quitte la fiche,
  l'endpoint reste. Un post hors créneau reste consultable et éditable.

Validé au navigateur sur données de test à 1440 et 390 px : aucun débordement mesuré
(`scrollWidth - clientWidth` = 0) sur la carte, la Programmation, les Artistes, les
Événements et la fiche.

## La charte Kinda dans la console (2026-10-02)

Le compte Instagram etait a la charte ; la console qui le pilote portait un
violet `#7c5cff` sans rapport avec la marque. Le rose signature `#FB8AC2` est a
un cheveu du `#FD8BC3` des gabarits de post (`app/content/layout.py`) : **la
charte etait deja dans le produit PUBLIE, elle manquait seulement a l'outil**.

**Le fond reste SOMBRE** (`#101014`), decision de l'utilisateur : la console se
regarde des heures et le sombre sert la densite de donnees. La marque y entre en
ACCENT, et la mesure le confirme -- le violet etait en dessous du seuil :

| paire | contraste | |
|---|---|---|
| violet sur fond sombre | 4,37:1 | sous 4,5 |
| **rose Kinda sur fond sombre** | **8,60:1** | presque le double |
| blanc sur bouton violet plein | 4,35:1 | echouait |
| blanc sur bouton rose plein | 2,21:1 | **interdit** |
| **texte `#101014` sur bouton rose plein** | **8,60:1** | la correction |

**Le bouton principal porte donc un texte SOMBRE sur le rose** (`--sur-accent`),
jamais blanc. C'est contre-intuitif et c'est mesure. La meme regle a corrige le
bouton rouge plein du mode `reel`, ou le blanc ne valait que 3,4:1.

**Le ROSE NE SIGNALE JAMAIS UNE PANNE**, et c'est la propriete la plus fragile
de ce chantier : rose (teinte 330) et rouge d'alerte sont voisins, une panne qui
vire au rose ne CASSE rien -- la page reste belle, les tests de structure
restent verts -- elle rend seulement la supervision aveugle, c'est-a-dire ce que
l'audit UX a fait construire en premier. Les rouges sont donc ramenes a la
teinte 0 EXACTE et montes en saturation (`#ff8a8a` etait a 0 mais aussi pale que
le rose ; `#ff9aa5` penchait carrement vers lui, a 353) : `--alerte` `#ff7b7b`
(7,57:1) et `--alerte-douce` `#ff8c8c` (8,48:1), tous deux au-dessus du seuil --
une raison de panne ne perd jamais en lisibilite pour gagner en teinte.
`tests/dashboard/test_dashboard_marque.py` le verrouille par **deux preuves par
mutation** : une alerte qui prend la teinte de l'accent, et une regle de panne
qui s'ecrit `var(--accent)`. Une seule exception, ecrite plutot que devinee :
`.zone-erreur .relance` est une ACTION dans un etat de panne, pas le signal de
panne.

Consequence sur un test existant : `test_dashboard_navigation.py` epinglait le
literal `rgb(255, 138, 138)`. Un literal aurait rougi pour une AMELIORATION ; il
verifie desormais l'intention (le jeton `--alerte`, une teinte rouge, un
contraste suffisant sur son fond).

**Typographie : aucune police web.** La charte demande une grotesque condensee ;
le fichier est autonome et ne depend d'aucun CDN, propriete qu'on garde. La pile
`--police-titre` part d'« Avenir Next Condensed » (presente sur macOS, et seule
condensee locale a offrir quatre graisses REELLES, donc un 600 vrai et non
synthetise), puis « DIN Condensed », puis des condensees Linux/Windows, et le
dernier repli est exactement la pile d'aujourd'hui : une machine sans condensee
rend la page telle qu'elle etait, jamais cassee. Elle ne s'applique qu'aux
titres et aux tetes de ligne -- « pas de fantaisie dans le texte courant ».

**Les deux signes de marque sont INLINES en `data:`**, pas servis. `app/main.py`
rend le dashboard par un `FileResponse` UNIQUE et ne monte aucun `StaticFiles` :
monter un repertoire statique pour deux PNG ajouterait une route, deux requetes
au premier rendu (un logo qui apparait apres le texte) et un 404 muet de plus a
pouvoir rater. Cout mesure et borne a **+15,7 ko** sur la page, parce que les
deux images sont DERIVEES avant d'etre encodees (`scripts/outillage/inliner_marque.py`,
idempotent, a relancer apres une refonte du logo) :
- le **logo** est aplati a la creme EXACTE de la charte `#FEF1F7` -- l'original
  portait 7 648 couleurs distinctes (un anticrenelage autour d'un `#FFF7FA` a
  peine different) qui coutaient 30 ko de base64 pour rien ; a plat, 13,7 ko, et
  la teinte devient celle de la charte. Il va dans le `src` d'une `<img
  alt="Kinda">` : seul endroit ou un `alt` donne le nom accessible sans texte
  masque a entretenir ;
- l'**etoile** devient un MASQUE gris+alpha (1,9 ko) dans une variable CSS, donc
  recoloree librement par `background-color`. Deux emplois seulement -- l'etat
  vide et la variante retenue d'une cover : un outil de travail ne se decore
  pas, il se signe.

Les PNG de `app/static/marque/` restent la source de verite.
