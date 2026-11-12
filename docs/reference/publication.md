# Publication automatique (`app/publish/`, `app/schedule/`)

**Quand lire ce fichier** : avant de toucher a la file de publication, aux
creneaux, au gel, a l'hebergement ephemere, au tunnel `cloudflared`, a yt-dlp,
aux reels automatiques, ou a tout chemin qui mene a `media_publish`. Il porte
l'anti-doublon (reclamation committee AVANT l'appel a Meta), la regle
d'EXCLUSION (une panne machine n'exclut jamais un article), les deux jetons
Meta, les regles Meta verifiees, le cadrage des images et le diagnostic mesure
des creneaux manques.

La fenetre de relecture et l'edition de ce qui va partir :
`relecture-et-controle-editorial.md`.

## Publication automatique (`app/publish/`, `app/schedule/`)

Kinda publie seul **trois carrousels et trois reels par jour** (2026-09-18 ; deux
et deux auparavant), choisis dans le flux scoré. Design : `docs/superpowers/specs/2026-09-11-publication-meta-design.md`
(révision du 2026-09-16) pour la file, et
`docs/superpowers/specs/2026-09-17-reels-automatiques-design.md` pour les reels.

**Connexion Instagram, sans Facebook.** App Meta « Kinda Radar », compte
`kindanewsfrance` déclaré testeur : une app non revue publie sur ses comptes
testeurs, ce qui suffit. Jeton `IG…` de 60 jours, rafraîchi chaque semaine dans
`meta_token_path` (droits 600) ; `.env` n'est que la valeur initiale. Alerte à J-7.

**Deux jetons Meta, jamais interchangeables.**
- `META_ACCESS_TOKEN` (`IGAA…`, connexion Instagram, `graph.instagram.com`) :
  **publication**. Rafraîchi automatiquement (ci-dessus).
- `META_FB_ACCESS_TOKEN` (`EAA…`, `graph.facebook.com`) : **source Instagram**
  (`business_discovery`, que le jeton de connexion Instagram ne sait pas
  appeler). **Depuis le 2026-11-12, c'est un jeton d'UTILISATEUR SYSTÈME, sans
  expiration** (`debug_token` : `type` SYSTEM_USER, `expires_at` 0,
  `data_access_expires_at` 0). Avant, c'était le jeton utilisateur Facebook
  PERSONNEL de l'utilisateur, 60 jours, sans rafraîchissement possible, et
  soumis à une seconde échéance (accès aux données, 90 jours) qui imposait de
  se reconnecter à la main : le robot lisait Instagram EN SON NOM.
  Montage : app `Kinda Radar` (1939961553647109) rattachée au portefeuille
  business 1045067402507429 ; utilisateur système `KindaRadar` (rôle
  Employee), à qui sont affectés la Page Kinda News France (Statistiques), le
  compte Instagram kindanewsfrance (Statistiques) et l'app (Développer + Tester
  — **« Développer l'app » est NÉCESSAIRE** : sans rôle de développeur,
  `debug_token` répond `#100` et l'alerte d'échéance ci-dessous ne peut plus
  lire l'échéance). Portées du jeton : `instagram_basic`,
  `instagram_manage_insights`, `pages_read_engagement`, `pages_show_list`,
  `business_management`. Vérifié le jour du passage : `business_discovery`
  sur un compte tiers rend ses médias, et `expiration_jeton_sourcing()` rend
  `None` (pas d'échéance). Le jeton personnel est gardé en commentaire dans
  `.env` pour un retour arrière ; il expire le 2026-11-16 et ne servira plus.
  À REGÉNÉRER seulement s'il est révoqué : Business Suite → Paramètres →
  Utilisateur(ice)s système → KindaRadar → Générer un token (app Kinda Radar,
  expiration « Jamais », les cinq portées ci-dessus).
  **Alerte d'échéance** (`app/sources/instagram_jeton.py`) : à chaque run horaire,
  `run_pipeline` interroge `debug_token` et notifie (macOS) à **J-7** ou dès
  l'expiration, **au plus une fois par jour** (date dans
  `sourcing_token_alert.json` à côté de `meta_token_path`). Sautée sans jeton ;
  une panne réseau ne donne qu'un warning (type d'exception seulement, jamais le
  texte, qui porterait l'URL donc le jeton). Expiré malgré tout, Meta répond
  code 190 : chaque source Instagram passe en erreur avec un message qui dit quoi
  faire, et la notification de fin de run le signale aussi.
`META_IG_USER_ID` et `META_API_VERSION` sont communs aux deux usages.

**La file.** 9 créneaux (3 jours × 3), heure tirée dans les fenêtres de
`PUBLICATION_FENETRES` (Paris, défaut `08:30-10:30,14:30-16:30,19:00-20:15`) avec
une graine dérivée de la date — sinon l'heure glisserait à chaque passage. Les
fenêtres carrousel sont une **liste séparée par des virgules**, exactement comme
celles des reels : une fenêtre = un post par jour, donc changer la cadence est
une ligne de `.env`, jamais une ligne de code. L'ancien couple
`PUBLICATION_FENETRE_MATIN` / `…_APRES_MIDI` survit en **repli** : il n'est lu
que si `PUBLICATION_FENETRES` est VIDE (`slots.fenetres()`), pour qu'un `.env`
d'avant ne soit pas ignoré en silence — mais la bonne migration est de le
réécrire en liste. Le coût dicte le calendrier : composition Claude à J-1, gel (rendu et
téléchargements) à T-2h, publication à l'heure. **Au-delà de 90 minutes de retard,
on ne publie pas** : un Mac rouvert le soir lâcherait sinon la journée d'un coup.

**Trois modes** : `arret`, `apercu` (tout, y compris le conteneur chez Meta, sauf
`media_publish`), `reel`. Publier est public et irréversible : passer à `reel` est
une décision de l'utilisateur. En `apercu`, un article passé en aperçu devient
**inéligible** (comme un article publié : son créneau APERCU le retire de
`candidats_eligibles`), sinon le même article reviendrait à chaque créneau — la
file avance donc comme en `reel`.

**Le mode se choisit dans le dashboard**, pas dans `.env`. `app/publish/mode.py` :
`mode_effectif()` lit `publication.json` à côté du jeton Instagram
(`settings.meta_token_path.parent`, chemin configurable via
`publication_mode_path`) et retombe sur `PUBLICATION_MODE` (`.env`) si le fichier
est absent, illisible ou invalide — jamais d'exception, l'agent planifié ne doit
pas planter faute de fichier. `enregistrer_mode()` écrit atomiquement (fichier
temporaire + `os.replace`). API : `GET /publication/mode` (`{mode, source}`,
`source` valant `dashboard` ou `env`) et `PUT /publication/mode` (`{mode,
confirmation}`) — passer à `reel` exige `confirmation: "PUBLIER"`, sinon 409 :
une publication publique et irréversible ne doit pas basculer sur un simple clic.
Le sélecteur segmenté vit dans le panneau Programmation du dashboard, à côté de
« Recalculer la file ».

**Règles Meta vérifiées dans la documentation (2026-09-16)** : JPEG uniquement,
8 Mo, 320 à 1440 px de large, proportions 4:5 à 1,91:1, carrousel de 10, légende de
2200 caractères et 30 hashtags. **La cover `kinda_post` est rendue NATIVEMENT en
1080×1350** (4:5) : elle traverse `app/publish/images.py` intacte. Jusqu'au
2026-09-18 elle était dessinée en 1080×1440 (3:4) puis recadrée au centre, ce qui
lui prenait 45 px en haut et en bas — la moitié de la marge du logo (87 → 42) et
60 % de celle de l'accroche (76 → 31). Les marges publiées n'étaient donc plus
celles du design, et cela se voyait. Les valeurs du dessin n'ont pas bougé (77
latéral, 87 logo, 76 accroche, voiles 817/727) : elles se comptent depuis un bord
ou depuis le bloc de texte, et seuls 90 px de PHOTO disparaissent du milieu du
cadre.

**Toutes les images du carrousel sortent en 1080×1350, cover comprise**
(2026-09-18, « c'est mieux que le format corresponde, sinon les images rendent
bizarre »). Instagram recadre chaque élément au ratio du PREMIER : une photo de
presse 16:9 laissée en paysage — ce que faisait l'ancienne `normaliser`, qui ne
recadrait que HORS des bornes Meta (4:5 … 1,91:1) — revenait donc coupée sans
qu'on maîtrise ce qui partait. `app/publish/images.py::cadrer_carrousel` met
chaque image à l'échelle pour REMPLIR le cadre puis recadre ; le
recadrage se fait avant le redimensionnement (même résultat, sans travailler sur
des pixels destinés à partir). Même convention que les vidéos, déjà sorties en
1080×1350 par `video.py::transcoder_carrousel`.

**Le recadrage VERTICAL est ancré en HAUT, jamais au centre** (2026-09-19, après
le post du 18/09 17h33 où Travis Scott a été publié sans sa tête). Une photo plus
haute que le 4:5 perd de la hauteur ; la retirer pour moitié en haut et pour
moitié en bas décapite, parce que sur une photo de scène le sujet est presque
toujours dans le tiers haut et que la marge perdable (sol, pieds, scène) est en
bas. Un portrait 2:3 ne perd ainsi que ~17 % de hauteur, tous pris au bas. Le
recadrage HORIZONTAL, lui, reste CENTRÉ : couper autant à gauche qu'à droite est
le bon geste, et cette branche ne touche pas à la hauteur.

**Une image que ce cadrage abîmerait est ÉCARTÉE, pas publiée** (`ImageEcartee`,
sous-classe d'`ImageInexploitable` pour que le gel la saute comme il saute déjà
un média illisible) : jamais une erreur, jamais une exclusion d'article, la
raison est journalisée et rappelée dans le message si le carrousel finit vide.
Deux seuils, réglables (`publication_crop_upscale_max`,
`publication_crop_perte_max`) :
- **trop petite** — remplir 1080×1350 demanderait un agrandissement supérieur à
  **2,2×**. Ce plafond est choisi pour laisser passer l'`og:image` standard de
  1200×630 (2,14×), de loin l'illustration la plus fréquente du pipeline : un
  seuil sévère (1,3×) écarterait toute photo paysage sous ~1750 px de large,
  donc viderait la plupart des carrousels et finirait par exclure les articles
  faute de second visuel. Au-delà de 2,2× on est sur des vignettes et des avatars
  (300-400 px), où l'agrandissement Lanczos se voit ;
- **trop éloignée du 4:5** — le recadrage jetterait plus de **60 %** de la
  surface. 60 % garde le 16:9 (55 % de perte) et le 1,91:1 (58 %), les deux
  formats de presse courants, et écarte les panoramiques (≥ 2,35:1) et les
  bandeaux verticaux (≤ 1:3,3), dont il ne resterait qu'une tranche sans sujet.

Conséquence assumée : si les images écartées ne laissent que la cover, la règle
« jamais un post qui ne montre rien de l'actu » s'applique telle quelle — le post
part en image simple quand la cover porte une photo, et l'article n'est sauté
(avec sa raison) que si elle est sur l'aplat Kinda ; gel différé si c'est un
échec de téléchargement passager.

**Le carrousel porte les médias de l'actu, vidéos comprises** (2026-09-17). Premier
post réel (Charlotte de Witte, article 17839) : 5 vidéos en base, et un carrousel
parti avec la cover seule, le gel ne gardant que les photos. `preparation.py` :
cover, puis jusqu'à 9 médias dans l'ordre (`medias_du_carrousel` : slides puis
article, sans doublon). Une photo → JPEG normalisé ; une **vidéo** → téléchargée
(`telecharger_video`), sondée (3 à 60 s — la doc Meta citée dans le dépôt ne donne
pas de maximum pour un élément de carrousel, 60 s = `DUREE_MAX_SLIDE_MS`),
transcodée en **1080×1350** (le ratio du premier élément s'impose à tout le
carrousel) par `video.py::transcoder_carrousel` — même cadrage flouté et même
encodage Meta (H.264/AAC, GOP fermé, sans edit list, faststart) que le reel,
piste audio garantie —, publiée en enfant `media_type=VIDEO` +
`is_carousel_item`. Une vidéo inutilisable (trop longue, téléchargement, sonde ou
transcodage en échec, ffmpeg absent, sortie hors specs) **retombe sur sa vignette**
(`preview_url`) en photo : un média ne fait jamais échouer le gel. Le client crée
tous les enfants, sonde chaque enfant vidéo jusqu'à FINISHED au rythme des reels
(15 s, 10 min max), puis le parent ; un enfant vidéo refusé (création, ERROR,
EXPIRED, délai) est **retiré**, et s'il reste moins de deux éléments la cover part
seule en image. **La photo servant de fond à la cover** (`slides[0].image_url`)
n'est pas répétée ensuite ; une vidéo dont c'est la vignette **reste** (un clip
n'est pas le même asset que son image fixe), sauf si elle retomberait sur cette
vignette. Une sortie transcodée hors specs (dimensions, codecs, poids) retombe
aussi sur la vignette. **Visuels d'un compte Instagram de catégorie `media` jamais repris**
(watermarks, `visuels_interdits`) : cover seule.

**Hébergement éphémère.** Meta télécharge les images en cURL. Au moment de publier
seulement, un mini-serveur local sert le dossier du post (sans listing) et un tunnel
rapide `cloudflared` l'expose ; tout est fermé après `media_publish`. Le dashboard
n'est jamais exposé. `cloudflared` est cherché hors du PATH de launchd, comme ffmpeg.

**Un tunnel rapide est parfois lent à être visible, et on PATIENTE plutôt qu'on
ne recommence** (2026-10-02 ; la stratégie d'avant faisait trois essais de 90 s,
chacun un tunnel ENTIER). Le raisonnement d'origine — « un quick tunnel reçoit un
nouveau sous-domaine à chaque lancement, attendre plus longtemps sur celui qui ne
répond pas n'a aucun sens » — est contredit par la mesure : l'attente est une
**propagation DNS** (16 à 66 s, n=5, mesuré en direct), pas une panne. Chaque
reprise jetait donc un sous-domaine probablement sur le point d'être visible et
remettait le compteur à zéro, sur une cause qui est devenue la PREMIÈRE du
tableau des créneaux manqués (11 des 19 manques des cinq derniers jours, 57
échecs sur 57 avant 16:00 UTC).

`heberger` attend donc `DELAI_JOIGNABLE_S` (**180 s**, soit 2,7 × la pire
propagation mesurée) sur **UN** sous-domaine, et ne relance que ce qui le
mérite :
- le tunnel n'a **jamais démarré** (cloudflared mort au lancement, aucune URL) —
  il n'y a aucune adresse dont attendre la visibilité, et le budget de patience
  n'a rien payé ;
- le sous-domaine est **résolu mais ne rend jamais 200** (`dns_resolu`, l'autre
  moitié de la panne que le correctif du message avait séparée) — patienter ne
  guérit ni cloudflared ni le serveur local, un tunnel neuf peut.

Quand le DNS public n'a **jamais** vu le sous-domaine, la patience ÉTAIT le
remède et elle a été épuisée : on abandonne le passage plutôt que d'offrir à un
sous-domaine neuf un reste de budget trop court pour sa propre propagation — le
runner retente dix minutes plus tard, sans exclure l'article.
`cloudflared` introuvable (`CloudflaredIntrouvable`) remonte immédiatement :
trois relancements ne l'installeraient pas.

**Borne totale INCHANGÉE**, et c'est elle qui protège la limite des 90 minutes de
retard : `publication_tunnel_budget_s` (**270 s** = les anciens 3 × 90) plafonne
la SOMME des attentes, et `publication_tunnel_essais` (3) plafonne le nombre de
tunnels lancés, donc la lecture d'URL (`DELAI_URL_S`) comme avant. Première
tentative 180 s, relance éventuelle 90 s — exactement une ancienne fenêtre. Un
test-sentinelle (`tests/publish/test_publish_hosting.py::
test_le_budget_total_de_patience_n_augmente_pas`) échoue si quelqu'un allonge une
attente « juste un peu ». L'essai perdu est tué par GROUPE de processus
(`start_new_session`), le serveur local vit pour tout le `with`. Épuisés, ils
lèvent `TunnelInjoignable` (sous-classe de `HebergementIndisponible`).

**Un tunnel injoignable n'exclut PAS l'article.** C'est une panne machine, la classe
que la règle d'exclusion épargne. Rien n'est parti chez Meta : le conteneur n'est créé
qu'À L'INTÉRIEUR du `with heberger(...)`, donc après que le tunnel répond — retenter
ne peut pas publier deux fois. Le runner lève alors le `file_exclu_raison` posé à la
réclamation, remet le créneau en GELE (fichiers figés toujours valides, dossier
conservé), note la raison, notifie UNE fois et laisse le passage suivant retenter. Le
créneau n'est pas repris par la seconde étape de publication du MÊME passage (rouvrir
un tunnel tout de suite coûterait des minutes pour rien). Passé les 90 minutes de
retard, il devient MANQUE comme n'importe quel créneau gelé. Vaut pour les carrousels
comme pour les reels, et pour le chemin « publier à l'heure exacte » (la réclamation
anticipée suit exactement le même traitement).

**Exclusion.** Un article dont la composition ou le gel échoue, ou que l'utilisateur
« saute », reçoit `articles.file_exclu_raison` et ne revient plus. C'est aussi le
cas dès qu'un créneau est **réclamé** pour publication (GELE → ECHEC committé
avant l'appel à Meta, raison « publication interrompue avant confirmation ») :
l'exclusion n'est levée que si Meta confirme ; un refus de Meta la remplace par
« echec de publication : … ». Un article ainsi exclu ne repart jamais, même après
un crash — mieux vaut un post manqué qu'un post publié deux fois. Une panne
systémique (crédits Anthropic, réseau, Chromium) n'exclut rien, pas plus qu'un
post qui ne porte plus l'article de son créneau (gel ou publication refusés,
erreur posée).

**« Ne pas publier » n'ajoute aucun chemin vers `media_publish`** (2026-10-30) : c'est
un retrait humain, seule exclusion qui ne soit la faute ni de l'article ni de la
machine, écrit dans la même transaction que la libération du créneau et sous la même
condition (état, article, post) que la réclamation. Détail et course :
`relecture-et-controle-editorial.md`, « Ne pas publier ».

**Les statuts de publication n'appartiennent qu'au robot** (2026-10-30, décision de
l'utilisateur : la console publie seule avec vérification humaine). `_publier` écrit
`PUBLISHED` et `USED` sans passer par l'API ; `PATCH /posts/{id}` refuse tout `status`
et `PATCH /articles/{id}` refuse de poser `used` ou d'en sortir (409, UPDATE conditionnel
contre une publication simultanée). `USED` est ce qui protège l'article de la purge et
d'une republication. `PostStatus.APPROVED` n'est lu par aucun code : l'enum et les
lignes existantes restent, sans migration, mais plus rien ne le pose.

**Le retour en arrière ne doit pas rouvrir le doublon** (2026-10-30, déduit du
code, **jamais observé en production**). La machine coupe juste après
`media_publish` : le post est en ligne, la base dit ECHEC de réclamation, article
exclu et non USED. `POST /articles/{id}/reintegrer` effaçait cette exclusion en un
clic ; `_articles_publiables` ne filtrant pas ECHEC, l'article redevenait candidat
et le brouillon se raccrochait. Reproduit par test : **deux** `media_publish`
(`test_la_seconde_ceinture_tient_si_l_exclusion_a_disparu`, `2 == 1` avant
correctif). Même chemin après « echec de publication : … », un délai dépassé
pouvant cacher une publication réussie. Deux ceintures, sur les mêmes traces
(`app/schedule/publication_incertaine.py`) :

- `reintegrer` répond 409 sans `confirmation: "PAS_PARTI"` : seul un humain qui a
  regardé le compte sait. Une exclusion « Ne pas publier » reste en un clic (les
  lignes anciennes disent « saute depuis le dashboard », même traitement).
- `_articles_publiables` écarte l'article d'un créneau ECHEC portant ces traces,
  même si l'exclusion a disparu. La confirmation réécrit l'erreur du créneau
  (« verifie a la main, pas parti — … », l'état reste ECHEC), seul moyen de lever
  cette ceinture : l'article repart (`test_apres_confirmation_humaine_l_article_repart`).

La confirmation est **refusée tant que le robot peut encore publier**
(`publication_retard_max_minutes` + `RECLAMATION_EN_COURS` après l'heure) : pendant
l'attente dans le tunnel, l'utilisateur ne voit rien sur le compte, son « pas parti »
est vrai, et le post part quand même ensuite. L'erreur du créneau porte le préfixe
depuis ce correctif : les échecs plus anciens (`str(faute)` nu) ne sont couverts que
par leur exclusion, pas par la seconde ceinture. Une panne d'hébergement n'est pas
concernée : rien n'a pu partir, `rendre()` repasse GELE sans exclusion
(`test_une_panne_de_tunnel_reste_retentee_et_n_est_pas_ecartee`).

**Une VIDÉO inadaptée n'est pas une faute de l'ARTICLE** (2026-09-21,
`preparation_reel.ReelInadapte`). L'article 18951 (Billboard, « The Weeknd
Debuts New Song Sampling "Neon Genesis Evangelion" Theme ») a été exclu à vie
parce qu'une vidéo de 190 s dépassait la limite du reel — or cet article n'avait
qu'**une photo** : la vidéo était EMPRUNTÉE à un compte d'artiste suivi
(`choix_visuels` / `_porteur_video`). On brûlait une actu à cause d'un visuel
prêté. `verifier_source` lève donc une exception distincte, sous-classe de
`PreparationImpossible` pour que tout ce qui l'attrape déjà continue de la
traiter comme un refus de gel (même motif qu'`ImageEcartee` sous
`ImageInexploitable`) ; le gel la reconnaît et **libère le créneau sans
exclure**, comme pour une inéligibilité relue.

Mais libérer ne suffit pas : sans mémoire, la même vidéo serait rechoisie au
créneau suivant et **recomposée à chaque fois** (Claude payé) — c'est
l'exclusion qui tenait ce rôle. L'exception porte donc la durée MESURÉE par
ffprobe, sa vidéo et l'article qui la détient, et `runner._memoriser_duree`
l'écrit dans `articles.media` : `videos_exploitables` l'écarte alors des reels
pour toujours, sans fermer les carrousels à l'article. **Si la leçon ne peut pas
être écrite** — article introuvable, ou URL qui ne correspond à aucun média, le
cas d'un repli yt-dlp qui porte un permalink —, on retombe sur l'exclusion : la
protection d'avant, là où l'information manque.

**Le dashboard édite pendant un passage.** L'agent relit chaque créneau avant de
le modifier et n'écrit le résultat d'une composition, d'un gel ou d'une
réclamation que par un UPDATE conditionnel (état, article, post attendus) ; si le
dashboard est passé entre-temps, son choix prime et le post composé reste un
brouillon. Un article publié à la main (USED, post PUBLISHED) libère et
désépingle son créneau. Un post sur un créneau GELE refuse toute édition de
contenu (409). Les créneaux déjà gelés sont publiés AVANT composition et gel, et
chaque étape est isolée : une panne amont ne bloque jamais un post prêt. L'heure
est relue juste avant chaque publication (retard > 90 min → MANQUE, notifié), et
le dossier figé est supprimé une fois le créneau terminé.

**L'arbitrage ecrivait sur une lecture perimee** (2026-10-31, **reproduit par test,
jamais observé en production**). « Recalculer la file » et le `PATCH /schedule/{id}`
tournent dans le processus web, en parallèle de l'agent et sans verrou. L'arbitrage lit
H0 PROGRAMME avec l'article A ; l'agent le gèle et committe ; l'arbitrage affecte B.
`creneau.etat = PROGRAMME` n'émet rien (inchangé aux yeux de SQLAlchemy), le SQL est
`UPDATE creneaux SET article_id=?, post_id=? WHERE id=?`. Le créneau reste GELE avec
l'article et le post de B et les fichiers de A ; le post de B porte bien l'article du
créneau, donc le contrôle de `_publier` passe. Le vrai `_publier`, publieur factice, a
reçu `(['a_01.jpg', 'a_02.jpg'], 'Legende de B')` : les visuels d'une actu sous la
légende d'une autre, sur SQLite et sur un Postgres jetable en Read Committed. Il faut
que le gel tombe entre lecture et écriture, et que B ait déjà un brouillon — le cas
ordinaire d'une grosse actu qui décale la précédente. La fréquence n'a pas été mesurée.

`store.ecrire_si_inchange` conditionne donc ces écritures sur (état, article, post),
sans toucher à ce que choisit l'arbitrage. Zéro ligne : **tout le lot du format** est
annulé, sans exception, avec un warning, et le passage suivant reprend — pas ce seul
créneau : l'arbitrage voulait A en H1 parce qu'il le croyait sorti de H0, écrire H1
poserait A sur deux créneaux (mutation « saut seul » : test rouge). Le PATCH rend 409,
rien d'écrit. Preuves : `tests/schedule/test_course_arbitrage_gel.py`, où retirer
chacune des trois conditions rougit au moins un test. L'ordre inverse (le web a écrit
sans committer, l'agent attend le verrou puis touche zéro ligne) n'a été vérifié que
par un essai SQL ponctuel sur Postgres, pas par un test gardé. **Déduit, non prouvé** :
la largeur réelle de la fenêtre sur Neon. **Non traité** : le `db.delete` de
`synchroniser_creneaux` reste inconditionnel (un créneau sorti de la grille et gelé
entre-temps serait supprimé : un post perdu, pas un faux post), et un épinglage seul
n'étant pas dans la condition, il peut être perdu face à un arbitrage simultané.

**Publication à l'heure affichée** (2026-09-17). L'agent passe toutes les 600 s :
sans rien de plus, un post partait jusqu'à ~10 min après l'heure du dashboard.
Un créneau GELE dont `publier_a` tombe dans les `publication_avance_s` (660 s,
un intervalle + marge) à venir est traité dès ce passage, **en mode `reel`
seulement** : réclamation (GELE → ECHEC committé, inchangée), tunnel, conteneur
(carrousel ou REELS, jusqu'à FINISHED), puis **attente dans le tunnel ouvert**
jusqu'à `publier_a`, par pas de 30 s relus sur l'horloge murale (jamais plus que
l'avance au total ; conteneur prêt après l'heure : aucune attente). En mode
`reel`, **retard puis mode sont relus** juste avant `media_publish` — pour TOUT
créneau, pas seulement ceux attendus : les enfants vidéo d'un carrousel peuvent
retenir le conteneur des minutes. Retard > 90 min (Mac endormi pendant
l'attente) → pas de `media_publish`, MANQUE, exclusion levée, notifié, dossier
effacé. Relu `apercu` → état APERCU ; relu `arret` → le créneau **redevient
GELE** par écriture conditionnelle sur l'état réclamé (si le dashboard est passé
entre-temps, son choix prime ; sinon exclusion levée, dossier gardé : il partira
si l'on repasse en `reel` avant la limite de retard). En `apercu`, un créneau à venir
n'est pas réclamé d'avance : il attend son heure. `publie_le` = heure réelle de
publication. Crochets : `publier_fichiers` / `publier_reel` acceptent
`attendre_jusqu_a` et `encore_publier` (appelés dans le `with heberger(...)`),
sans effet quand ils sont absents ; `tourner(dormir=...)` injecte le sommeil.
**Compromis assumé :** un processus tué PENDANT l'attente laisse le créneau en
ECHEC « publication interrompue avant confirmation » — ce post est perdu, jamais
doublé. launchd ne tue pas un passage en cours en temps normal. Pendant ces
quelques minutes, le créneau est en ECHEC de réclamation
(`RAISON_RECLAMATION`) : tant que `publier_a` est à moins de 20 min dans le passé
(ou à venir), le dashboard l'affiche « Publication en cours » sans détail
d'erreur, et `PATCH /posts/{id}` refuse toute édition de contenu (409), comme
sur un créneau GELE. Au-delà (crash), il redevient un échec éditable.

**Le creneau est AUTONOME de son article** (2026-09-30). Mesure ce jour-la :
46 % des creneaux aboutis etaient manques (37 contre 43 publies), et **25 des
37 ne portaient AUCUNE raison** alors que tous les 25 avaient un post compose.
Deux defauts distincts se cachaient sous ce meme silence.

*Le trou.* La file couvre TROIS jours (`publication_creneaux` = 9, trois par
jour) et `publication_fraicheur_jours` vaut 3, alors que `retention_days` vaut
**2** depuis le 2026-09-21 : un article attribue a un creneau du troisieme jour
est purge avant l'heure de sa publication. Or `creneaux.article_id` ET
`posts.article_id` sont tous deux `ON DELETE SET NULL` : la purge vide les deux
sans un mot. Dans `appliquer_arbitrage`, `if article_id != creneau.article_id`
ne voyait alors rien a faire — les deux valent `None` — donc `post_id`
survivait pendant que `etat` etait force a `LIBRE`. Le creneau devenait
invisible de la composition (qui exige un article) comme du gel (qui exige
`PROGRAMME`) et pourrissait jusqu'a `MANQUE` : 8 des 25 portaient exactement ce
profil (post compose, article NULL, jamais gele).

*Le choix de l'utilisateur* : rendre le creneau autonome, plutot que raccourcir
la file ou regonfler la base. Le post porte deja tout ce qu'il faut pour partir
— textes, visuels copies en local, credit, palette, `assets` —, denormalise
exprès pour survivre a cette purge. `store.porte_un_post_orphelin` traite donc
un creneau « post compose + article NULL » comme EPINGLE : l'arbitrage ne le
touche plus, et une ligne deja passee en `LIBRE` par l'ancien defaut redevient
`PROGRAMME` toute seule (aucune reprise manuelle). Au gel, la relecture
d'eligibilite reel est SAUTEE quand l'article est parti : ce n'est pas une
complaisance, il n'y a plus rien a relire — la composition l'a deja verifiee, et
`preparer_reel` exige de toute facon `posts.credit`. Les deux preparateurs
etaient deja tolerants a `article=None` (partout `getattr(article, ...)`), et
`_publier` gardait deja chaque touche a l'article derriere un `if article is not
None` — le basculement `USED` inclus, qui n'a plus d'objet sans article. La
COMPOSITION, elle, garde sa dependance : elle a besoin du titre, du resume et
des medias. Un creneau sans article n'est jamais compose, il est reattribue.

*Le silence.* `_marquer_manques` ecrivait `etat = MANQUE` **sans jamais toucher
`erreur`**, qui valait donc `None` des que la derniere etape reussie etait une
composition ou une reattribution — et `_compter_manque` sortait des que
`article_id` etait `None`, si bien que ces manques n'apparaissaient ni au
rapport ni a la notification de fin de passage. Le second chemin vers `MANQUE`
(le retard relu sur l'horloge fraiche dans `_publier`) etait muet de la meme
facon. Desormais **un creneau manque porte TOUJOURS une raison**
(`_raison_manque` : format coupe, gele mais jamais publie, aucun post compose,
post compose mais jamais gele), et n'est exempte du compteur que s'il etait
REELLEMENT vide — ni article ni post, rien n'attendait de partir.

*Ce que la mesure a aussi montre, et qui n'est PAS corrige ici* : les 17 autres
manques sans raison ne viennent pas du trou mais d'une absence de l'agent —
`publication.log` montre des trous de 2 a 3 h en pleine journee (16:52 → 19:57
le 30/09), donc un Mac ferme, pas une panne de code. Sept d'entre eux etaient
deja GELES, et sept autres ont vu leur post publie par un creneau suivant, sans
perte editoriale. Restent deux causes distinctes et hors perimetre : les tunnels
`cloudflared` injoignables (8 creneaux) et le solde de credits Anthropic epuise,
qui fait echouer toute composition depuis le matin du 30/09.

**Ce que les creneaux manques coutent vraiment : le TUNNEL, pas le code**
(2026-10-02, mesure complete sur les 97 creneaux aboutis en base, 44 manques
contre 45 publies). L'enquete du 30/09 avait corrige un trou de conception ; le
taux a pourtant EMPIRE. La mesure, reproductible par
`scripts/diagnostic/diagnostiquer_creneaux.py`, croise la base et les trois logs :

| cause | n | part |
|---|---|---|
| ABSENCE : aucun passage de l'agent dans la fenetre de rattrapage | 25 | 57 % |
| TUNNEL `cloudflared` injoignable | 11 | 25 % |
| yt-dlp : video Instagram indisponible | 3 | 7 % |
| GELE, l'agent est passe, cause inconnue | 3 | 7 % |
| aucun post compose / post jamais gele | 2 | 5 % |

Mais **ces deux causes ne vivent pas a la meme epoque**, et c'est tout le
resultat. Sur les cinq derniers jours (28/09 → 02/10) : 6 publies sur 29, et
**11 manques sur 19 viennent du tunnel contre 7 d'une absence**. Avant, c'etait
l'inverse. La degradation recente n'est donc PAS le Mac qui dort — l'agent a
tourne davantage que jamais ces jours-la (24, 16, 43, 31, 28 passages
journalises par jour) — c'est `cloudflared`.

**Ce n'est pas non plus un probleme de FENETRES.** Aucune heure de la grille
n'est systematiquement morte : le taux de publication va de 30 % a 75 % selon
l'heure, sans creux franc, et les absences sont des journees entieres (4 manques
le 25/09, 4 le 01/10) et non une heure qui tomberait toujours pendant la veille.
Deplacer les fenetres ne reglerait rien. En revanche les echecs de tunnel, eux,
ont une heure : **57 sur 57 avant 16:00 UTC, zero apres**, alors que l'agent
publie sans incident le soir (24 publications entre 16 h et 20 h UTC). C'est une
caracteristique du reseau en journee, pas du code.

**Ce qui a ete corrige, et rien de plus.** La publication elle-meme n'a pas
bouge : la mesure n'exigeait aucun changement de comportement, et publier est
irreversible. Deux pannes MUETTES, en revanche, rendaient l'enquete impossible
depuis la base seule et ont donc ete reparees :
1. `_raison_manque` ECRASAIT la cause deja inscrite sur le creneau
   (« hebergement indisponible : ... ») par un generique qui dit l'etat atteint
   et jamais la cause. Elle la conserve maintenant, en TETE (le voyant de
   composition de `app/supervision.py` filtre par `erreur.like("composition%")`,
   un prefixe le rendrait aveugle) et y ajoute l'etat. Sans ce correctif, les 11
   manques de tunnel n'ont pu etre retrouves qu'en recroisant `publication.log`.
2. `hosting.attendre_joignable` confondait DEUX pannes sous « injoignable apres
   90 s » : le DNS public qui ne voit pas encore le sous-domaine, et le
   sous-domaine resolu dont la sonde ne rend jamais 200. Les remedes sont
   opposes (patienter / regarder cloudflared et le serveur local), et 57 echecs
   complets n'avaient rien appris la-dessus. Le message les distingue, donne le
   temps du MUR (la boucle ne comptait que ses sommeils, donc « 90 s » n'etait
   pas la duree ecoulee) et `heberger` le transporte jusqu'a `creneaux.erreur`.

**La STRATEGIE de reprise, elle, a ete corrigee le jour meme** (voir « Un tunnel
rapide est parfois lent a etre visible » plus haut). Le diagnostic laissait le
budget a 3 essais de 90 s, faute de pouvoir reproduire le regime en panne ; mais
la mesure de propagation (**16 a 66 s**, n=5, jusqu'a 73 % du budget) suffit a
condamner la REPRISE elle-meme, independamment du budget : ce qu'on attend est une
propagation, et redemarrer jette un sous-domaine presque visible pour repartir de
zero. A budget total INCHANGE (270 s), on patiente donc 180 s sur un seul
sous-domaine et on ne relance que les pannes ou patienter n'est pas le remede.

**Une observation a surveiller, deliberement non corrigee** : un post qui echoue
est recompose sur le creneau suivant, et le post 140 a ainsi consomme **six
creneaux d'affilee** (93, 94, 96, 98, 102, 104) sans jamais partir — a chaque
fois le tunnel. Retenter EST le bon geste pour une panne machine ; borner ce
nombre de reprises traiterait le symptome et risquerait d'enterrer une actu pour
une coupure passagere. Le script le signale (« un meme post sur plusieurs
creneaux ») plutot que le code ne le decide.

Enfin, **19 passages ont eu lieu sans atteindre Neon** (« base injoignable apres
5 essais espaces de 6 s »), tous au bord d'un trou de veille : `attendre_base()`
accorde 30 s, parfois trop court au reveil. Compte avec les absences, pas comme
une panne distincte.

**La fraîcheur du TEXTE est relue au GEL** (2026-10-04). Le contrôle de
composition ci-dessus ne protège que les posts à venir ; un brouillon déjà en
base dont le créneau a glissé doit être **recomposé** plutôt que publié tel quel,
et le gel (T-2h) est le dernier moment où l'on peut agir. Un critère de plus à
côté de `_brouillon_perime` (`runner._reperes_perimes`) plutôt qu'un second
chemin : le mécanisme « ce brouillon est périmé, on recompose » existe déjà.

**Le critère n'est PAS un nombre d'heures, et c'est mesuré** : le post 101 était
FAUX à **10,8 h** d'écart (« demain à 18h », composé mer 20h12, publié jeu 07h03
— minuit franchi) quand le post 115 restait VRAI à **19,5 h** (« vendredi
prochain », composé ven 20h23, publié sam 15h56 — même semaine ISO, même vendredi
désigné). Le plus COURT est faux, le plus LONG est vrai : aucun seuil en heures ne
les sépare. Ce qui les sépare est la **frontière de calendrier** franchie, à
l'heure de Paris — jour pour « demain / ce soir / minuit / aujourd'hui », semaine
ISO pour « ce week-end / cette semaine / la semaine prochaine », mois pour « le
mois prochain », et pour un jour nommé (« ce vendredi », « vendredi prochain ») la
DATE calculée depuis la semaine d'écriture. Coût mesuré sur les 52 posts publiés
de la base : **2 recompositions (3,8 %)**, les deux justes, zéro faux positif —
soit ~0,2 appel `composer_model` de plus par jour à six publications. Réglage :
`publication_reperes_relus` (`true` par défaut ; `false` rend exactement le
comportement d'avant).

La référence de temps est `post.created_at` contre **maintenant**, jamais contre
`creneau.publier_a` : sinon un brouillon composé ce soir pour un créneau de demain
matin serait périmé à la seconde où on l'écrit, donc recomposé à chaque passage de
l'agent jusqu'à minuit. Avec `maintenant`, un brouillon frais n'est jamais périmé
et une recomposition pose un `created_at` du jour de lecture : **au plus une
recomposition par frontière franchie, jamais une boucle.** Seuls les textes
RETENUS sont relus (`slides[0]` et la légende), jamais les quatre variantes : trois
d'entre elles ne partiront jamais.

Deux garde-fous, parce qu'une recomposition ne doit **jamais** faire perdre le
créneau — on publie l'ancien texte plutôt que rien, en le journalisant :
- **Article purgé = pas de relâchement.** `retention_days` vaut 2 jours et le
  créneau est alors AUTONOME (`store.porte_un_post_orphelin`) : personne ne peut
  recomposer sans titre ni résumé, et lâcher le post rendrait le créneau invisible
  de la composition comme du gel, jusqu'à MANQUE. C'est le cas MÊME du post 153,
  dont l'article avait été purgé — c'est le contrôle de composition qui empêche ce
  texte d'exister, pas le gel.
- **Un REPLI.** Un brouillon périmé pour son seul TEXTE est par ailleurs
  publiable (visuels copiés en local, crédit, palette, légende intacts),
  contrairement à un brouillon périmé pour ses VISUELS, qui ne partirait pas de
  toute façon. `_composer` le garde donc en repli : si la recomposition échoue
  (`AucunVisuel`, `VisuelsDifferes`, `VisuelIntrouvable`, `CompositionError`, ou
  une panne systémique comme le solde Anthropic épuisé du 2026-09-30),
  `runner._replier` le rend au créneau avec un WARNING, au lieu d'exclure
  l'article ou de sauter. Il marque alors le créneau (`RAISON_TEXTE_PERIME`) et le
  gel ne relâche pas ce post une seconde fois : **une tentative par créneau**,
  sans quoi gel et composition se renvoyaient la balle à chaque passage de l'agent
  (10 min) pendant les 2 h du gel, à trois appels Sonnet chacun.

Essai sans publier : `uv run python -m scripts.diagnostic.tester_publication --post <ID>`.
Ce qui attend une relecture avant de partir : `GET /relecture`, ou la liste
« À relire avant publication » en tete du panneau Posts du dashboard.
Pourquoi les creneaux sont manques, avec des effectifs :
`uv run python -m scripts.diagnostic.diagnostiquer_creneaux [--jours N] [--detail]`
(lecture seule, aucun appel reseau).

**Reels automatiques (2026-09-17).** `creneaux.format` (`carrousel` | `reel`,
migration 0017) ; fenêtres reel propres (`PUBLICATION_REEL_FENETRES`, défaut
`11:00-12:30,17:00-18:30,21:00-22:30`), `PUBLICATION_CRENEAUX_REEL=0` les
désactive. Les six fenêtres du défaut s'alternent sur la journée (carrousel le
matin, l'après-midi et en début de soirée ; reel à midi, en fin d'après-midi et
tard) et laissent au moins 30 minutes entre elles.
`publier_a` reste unique tous formats confondus : `slots.py` refuse des fenêtres
qui se chevauchent — en ne vérifiant que les formats ACTIFS (le carrousel seul ;
le reel, contre lui-même et les carrousels, seulement s'il a des créneaux). Des
fenêtres reel fautives sautent les reels à l'arbitrage, les carrousels sont
programmés quand même, puis l'erreur remonte (notifiée à chaque passage).

**Reels en opt-in explicite.** `reels_actifs` vaut **false** tant que la case
« Reels auto » n'a pas été cochée au dashboard (clé absente, fichier illisible
ou valeur invalide = coupés). Motif : la production tournait déjà en mode `reel`
au moment de la fusion ; un défaut à true aurait publié des reels sans décision.
Le mode est **relu avant chaque réclamation** (`tourner(lire_mode=...)`,
`mode_effectif` dans l'agent) : repasser en `apercu` pendant un long passage
empêche tout `media_publish`, `arret` ne réclame plus rien. Un reel gelé qui finit
en « Manqué » parce que les reels ont été coupés est notifié comme tel
(« reels coupés »), pas comme un Mac en veille. Arbitrage **reels d'abord**, un article n'occupe jamais deux
créneaux. Éligible en reel : vidéo avec URL directe (≤ 90 s ou durée inconnue),
compte artiste/festival/label/club (`PUBLICATION_REEL_CATEGORIES`), source
Instagram (crédit), durée connue d'au moins **6 s**
(`PUBLICATION_REEL_DUREE_EDITORIALE_MIN_S` ; une vidéo plus courte reste bonne
pour un carrousel). L'éligibilité est revérifiée
au gel : un article devenu inapte (vidéo retirée, compte reclassé) libère son
créneau sans exclusion. Un brouillon `kinda_reel` antérieur au crédit est
complété (`credit_video`) à la reprise ou au gel, jamais exclu pour ça. Le gel (`app/publish/preparation_reel.py`) télécharge la
vidéo, l'assemble (`assembler_reel`, sans edit list, GOP fermé), sonde source et
sortie contre les specs Meta, et fige `<prefixe>_reel.mp4` puis
`<prefixe>_cover.jpg` ; la publication crée un conteneur `REELS` avec
`cover_url` explicite (sondage 15 s, 10 min max). Crédit `🎥 via @compte`
obligatoire (`posts.credit`), jamais coupé. **Plus de ligne « Source : <url> »** sous la légende
(2026-09-17, demande de l'utilisateur) — ni à la publication ni dans l'export zip
(`app/content/legende.py`) ; le crédit reste.
**Légende aérée** (2026-09-17) : le prompt demande 2 à 4 paragraphes courts (1-2
phrases) séparés par une ligne vide ; filet déterministe `aerer_legende()` dans
`texte_legende` (publication ET export) — une légende sans ligne vide voit ses
retours à la ligne simples doublés (sauf devant une ligne de liste — emoji ou
puce, « 📅 24.09 » —, qui garde son retour simple), ou, en bloc de plus de deux
phrases, est coupée par paires de phrases (les emojis de fin restent avec leur
phrase ; « ... » suivi d'une minuscule ne coupe pas ; pas de coupe après une
abréviation : initiales « J. », « U.S. », et feat, ft, Vol, Dr, St, Mr, Mme, No,
vs). Ordre publié : légende, crédit, puis les hashtags en dernier bloc. Longueur :
marge de **2150** points de code sur TOUT le texte (Meta : 2200, unité non
documentée) ; si crédit + hashtags débordent à eux seuls, des hashtags partent par
la fin (500 caractères gardés à la légende), puis la légende est coupée — le
crédit jamais. ffmpeg absent = panne systémique qui
ne suspend que les reels. `GET /schedule/offre-reels` mesure la matière.

**Trois défauts du texte publié, corrigés le 2026-09-19** après revue des posts
réellement en ligne — chacun corrigé là où l'information existe, le prompt ne
garantissant jamais rien à lui seul :
- **Des hashtags qui portent vraiment un dièse.** Le reel du 18/09 15h13 s'est
  terminé sur `iheartradiofestival bts lesserafim …` en texte brut : la règle 7
  demandait des hashtags « en minuscules » sans dire qu'ils portent un `#`, et
  rien ne rattrapait. `legende.py::normaliser_hashtags` (dièse en tête, alphanum
  et `_` seuls, dédup insensible à la casse, tag vide supprimé, avant le plafond
  de 30) est appelée par `texte_legende` — le SEUL goulot que traversent la
  publication automatique, l'export zip et `tester_publication`, et qui couvre
  donc aussi les hashtags tapés à la main via `PATCH /posts/{id}`.
- **Le français s'écrit avec ses accents.** Deux posts du 18/09 sur quatre ont
  une légende en ASCII (« un titre pense pour la bande son »). Règle de prompt,
  plus un contrôle qui RELANCE l'appel quand une légende d'une longueur
  significative n'a aucun caractère accentué — non bloquant, sur le modèle des
  emojis : on accepte avec un warning plutôt que de perdre la composition.
  Racine probable NON traitée : le `SYSTEM_PROMPT` est lui-même écrit sans
  accents et le modèle imite le registre de son prompt.
- **Le titre d'un reel nomme l'artiste de la VIDÉO.** Le même reel affichait
  « BTS » sur une vidéo de LE SSERAFIM (son propre crédit le disait). Le
  compositeur ne recevait que l'article, donc ignorait d'où venait la vidéo ;
  or `visuels = choisir(...)` est résolu AVANT l'appel payé dans
  `runner.py::_composer`. `compte_du_visuel` voyage désormais jusqu'au prompt
  (« Compte du visuel publié : @… »). Le crochet reste RÉTROCOMPATIBLE
  (`appeler_compositeur` n'ajoute le mot-clé que si la signature sait le
  recevoir) : un `TypeError` sur un crochet ancien passerait pour une panne
  systémique et coûterait un post.

**Le bloc de texte du reel est ancré PAR LE BAS** (`GABARIT_REEL.block_bottom`,
420 px du bas, soit un bas de bloc à y=1500 ; 2026-09-19). Deux contraintes le
fixent : l'interface d'Instagram couvre le bas du reel, et le crop 4:5 de la
grille s'arrête à y=1635 — la première est la plus stricte. L'ancien
`block_center` (fraction de hauteur) gardait le CENTRE fixe : dès que la machine
d'ajustement réduisait la police, le bloc raccourcissait en remontant vers le
milieu du cadre, et les reels ressortaient dans la grille comme des corps
étrangers au milieu des posts, dont le texte est en bas. Un ancrage par le bas
est stable par construction, quelle que soit la hauteur mesurée.

**Deux planchers de durée, pas un** (2026-10-04, reel 168 : 4,0 s publiées).
`PUBLICATION_REEL_DUREE_MIN_S` (3 s) est **technique** — la limite de Meta, en
dessous le conteneur `REELS` est refusé ; `PUBLICATION_REEL_DUREE_EDITORIALE_MIN_S`
(**6 s**) est **éditorial** — un reel plus court ne laisse pas le temps de lire la
bande de titre de la cover. C'est le plus haut des deux qui s'applique, en UN
SEUL point (`actifs_artistes.duree_min_reel_ms`), sur les trois chemins : la
vidéo propre de l'article (`store._trop_courte`), la vidéo **empruntée** à un
artiste cité (`actifs_artistes.medias_utilisables`) et le gel
(`preparation_reel.verifier_source`).

Le 6 est **mesuré**, pas choisi au goût. Sur les 70 brouillons `kinda_reel` de
la base (44 durées connues) : `4 s ×2`, `7 s ×1`, `8 s ×8`, `9 s ×3`, `10 s ×1`,
puis 12 s et au-delà. La distribution porte un **trou entre 4 et 7 s** : le
plancher se pose dedans. Coût mesuré : **2 brouillons sur 44** (4,5 %) et **1
reel publié sur les 13 de durée connue** — le 168, celui du défaut. Un plancher
à 8 s en coûterait 3 sur 13 (23 %), le peloton le plus dense.

Une durée **inconnue passe toujours** (règle du doute, inchangée) : le gel la
mesure à ffprobe, et `verifier_source` la refuse alors en `ReelInadapte`, qui
**libère le créneau sans exclure** et mémorise la durée dans `articles.media` —
l'article reste publiable en carrousel, la vidéo n'est plus jamais rechoisie.

**Une vidéo EMPRUNTÉE doit pouvoir montrer l'actu** (2026-10-04, même reel 168 :
*« Troye Sivan Joins Charli XCX To Perform Two Of His New Songs At ACL »*,
Stereogum, **une seule photo** et aucune vidéo — le pipeline a donc emprunté une
vidéo à `@charli_xcx`, le mécanisme prévu et sans lequel il n'y aurait pas eu de
reel). La vidéo publiée montrait Charli XCX **seule, en studio**, et surtout
elle avait été **publiée 22 h AVANT l'article** : elle ne pouvait rien montrer
d'un événement qui n'avait pas encore eu lieu.

Le remède est **dans `classer`, pas dans un étage de plus** : la fenêtre « hors
sujet » (`VISUELS_ARTISTE_GENERIQUE_JOURS`, 5 j) était **symétrique**, et c'est
elle qui laissait passer le cas. Pour la VIDÉO d'un REEL elle devient **à sens
unique** — une vidéo qui ne partage AUCUN mot du sujet ne peut plus précéder
l'actu de plus de `VISUELS_REEL_ANTERIORITE_TOLEREE_HEURES` (**0 h**).

Trois bornes à cette règle, chacune délibérée. (1) **Hors sujet seulement** :
une vidéo qui partage les mots du sujet garde la fenêtre pleine dans les deux
sens — un teaser, une annonce, un extrait d'un set précédent illustrent
légitimement une actu à venir, et interdire tout ce qui précède l'actu viderait
une bonne part des reels. (2) **En REEL seulement** : un fond de cover et une
photo de carrousel n'ont pas à montrer l'événement, ils illustrent l'artiste —
`choix_visuels._reel` classe d'ailleurs son fond en format `CARROUSEL`, et un
test-sentinelle le vérifie. (3) **Jamais une exclusion** : l'article perdu pour
le reel reste candidat au carrousel (`candidats_eligibles` filtre par format,
`file_exclu_raison` n'est pas posé).

Le 0 h est la seule valeur **non arbitraire** sur les données disponibles. La
tolérance existerait pour couvrir l'écart entre l'événement et l'article qui le
rapporte (une vidéo postée juste après le concert, l'article quelques heures
plus tard) — mais la mesure du 2026-10-04 sur la base vivante ne donne qu'**un
seul** candidat hors sujet antérieur, à −5,1 h (une vidéo de `@peggygou_` pour
les 25 ans de *Fever* de Kylie Minogue : un rapprochement sans valeur), face au
−22 h du défaut. Entre 0 et 22 h, rien dans les données ne tranche. Effet
mesuré : sur 74 articles publiables, 10 ont une vidéo propre (non concernés) et
**6 dépendent d'une vidéo empruntée** ; à 0 h, **5 des 6** gardent leur reel.

**yt-dlp pour les reels d'artistes (2026-09-17, autorisé par l'utilisateur).**
Meta omet `media_url` des reels à musique protégée ; la vidéo vient alors de
`app/publish/ytdlp.py`, **au gel seulement** : API d'abord, yt-dlp sur le
permalink sinon (et en repli d'un lien CDN expiré — seulement pour un permalink
de reel ou un post dont la vidéo est le seul média ; un carrousel `/p/` n'a pas
de repli, le lien expiré exclut). Dépendance uv épinglée
(`yt-dlp>=2026.8.19`, la version Homebrew 2025.12.08 échoue sur « login
required »), lancée par `sys.executable -m yt_dlp` avec `--ignore-config
--no-cookies --no-cookies-from-browser` — **jamais de cookies ni de connexion**.
Un téléchargement par passage ; après une panne (session exigée, 429, erreur
inconnue), notification avec la commande de mise à jour et **pause de 60
minutes** (`ytdlp_etat.json` à côté du jeton) ; pendant la pause, une raison sur
le créneau, sans notification. Vidéo supprimée/privée : article exclu. **Règle
des 3 échecs par lien** : une panne d'allure systémique n'exclut rien, mais après
3 pannes de suite sur le MÊME lien l'article est exclu (sinon un reel cassé
bloquerait tous les autres) ; les compteurs vivent dans `ytdlp_etat.json`
(clé `echecs`, par URL), remis à zéro au succès ou à l'exclusion. Coût assumé :
une panne globale de yt-dlp exclut un article toutes les ~3 h.
`scripts.diagnostic.tester_publication` supprime `essais/<id>/` en fin d'essai. Sources
en VP9 : `assembler_reel` réencode tout en H.264/AAC. Coupe-circuits : case
« Reels auto » du dashboard (`reels_actifs` dans `publication.json`,
`/publication/reels`) pour tous les reels, `PUBLICATION_REEL_YTDLP=false` pour
yt-dlp seul. **Mettre à jour yt-dlp** quand la notification le demande :
`uv lock --upgrade-package yt-dlp && uv sync`, puis committer `uv.lock`.
Risques acceptés : CGU Instagram, fragilité, détection de droits à la
republication, limitation de l'IP résidentielle — démarrer en `apercu`.

**Avant un `alembic downgrade 0016`** (retire `creneaux.format` et
`posts.credit`) : décocher « Reels auto », attendre que plus aucun créneau reel ne
soit « Gelé » (publié, aperçu ou manqué), puis descendre. Sinon un reel gelé
perdrait en cours de route son format et son crédit obligatoire.

## Programmer (2026-10-31)

Décision de l'utilisateur : la console publie seule avec vérification humaine ; il gère
le planning et ce qui va sortir, il ne génère JAMAIS à la main. `POST /articles/{id}/posts`
a disparu ; `create_post` reste, le robot s'en sert. Aucun appel à Meta ni à Claude au
clic, aucun nouveau chemin vers `media_publish` : le geste écrit un créneau PROGRAMME, et
l'agent compose puis gèle à son passage.

**Une seule règle, `app/schedule/programmation.py::raison_refus`.** `PATCH /schedule/{id}`
l'applique, `GET /articles/{id}/creneaux-possibles` et `GET /schedule/{id}/actus-possibles`
l'annoncent, le navigateur n'en recopie aucune.
`test_les_lectures_et_le_patch_ne_divergent_jamais` joue le PATCH sur toute une matrice et
compare la raison au mot près. Lectures groupées : ni requête par ligne (y compris sur un
créneau reel, où l'arbitrage garde son coût connu), ni écriture, ni réseau.

**La limite de purge, et son calcul.** La composition s'ouvre à
`publier_a - publication_composition_heures`. La purge vise un article non publié à
`published_at + retention_days` (`fetched_at` sans date ; sursis si aucune machine ne l'a
jugé, `moment_de_purge` relit `_predicat_purgeable` et un test les confronte). Si
`max(composition, maintenant) >= purge`, le créneau serait vidé par `ON DELETE SET NULL`
avant d'avoir un post : l'option est refusée avec sa raison, au PATCH aussi. Avec les
valeurs actuelles (2 j, 24 h), un article ne se programme donc pas à plus de 72 h de sa
date de publication. **Sauf brouillon valide** : `post_a_reprendre` l'accroche dès le geste,
et un post survit à son article. Valide = non `_brouillon_perime` : sans fond, le gel le
lâcherait, et le créneau mourrait quand même. La rétention n'a pas bougé.

**Non bloquants** : le seuil de pertinence et la fraîcheur. L'humain prime : on affiche
« sous le seuil de N, choix manuel » et l'âge. Une note absente n'est ni « 0 » ni sous le
seuil : « pas encore notée ». **Permis mais annoncé** : sous
`publication_relecture_avance_minutes`, « partira sans fenêtre de relecture » ; sous
`publication_relecture_regel_minutes`, une fois gelé la cover et les éléments ne se
corrigent plus.

**`occupant_attendu`** : la validation nomme ce qui sort, et l'arbitrage a pu changer
d'occupant depuis l'affichage. Présent et faux, le PATCH rend 409 sans rien écrire.

**`creneaux.erreur` s'efface quand l'article change** (reproduit d'abord). Survivant, un
marqueur `RAISON_TEXTE_PERIME` faisait sauter au nouveau post le contrôle de texte du gel
(`deja_tente`). Sur un échange, l'erreur s'efface aussi, sauf `RAISON_RELECTURE`, qui suit
son post : il protège d'une exclusion l'article qu'on vient de corriger.

**Le créneau mort** (reproduit d'abord) : épinglé à la main, article purgé avant sa
composition, ni article ni post. Ni la composition ni l'arbitrage ne le voyaient, jusqu'à
MANQUE. `store.creneau_mort` : l'arbitrage le désépingle et le rend à la file, par écriture
conditionnelle. Un post orphelin n'est pas mort (`porte_un_post_orphelin`).

**Une correction humaine épingle** : voir `relecture-et-controle-editorial.md`.

**Ce qui verrouille la main** (`programmation.RAISONS_DU_VERROU`, une seule source pour le
PATCH, les options et la carte) : un créneau PUBLIE, APERCU, GELE, ou un ECHEC qui porte une
trace de doute (`publication_incertaine`, la règle exacte de la ceinture du robot).
`store.VERROUILLES`, lui, dit qu'un CRENEAU ne bouge plus et reste tel quel.

- **MANQUE ne verrouille pas** (reproduit d'abord : 409 « déjà engagé » alors que le robot
  reprenait l'article). Vérifié dans le code : `_marquer_manques` ne lit que
  LIBRE/PROGRAMME/GELE, et le seul MANQUE après une réclamation (`manquer`, dans `_publier`)
  exige que `encore_publier` ait refusé, donc que `media_publish` n'ait pas été appelé.
- **Un ECHEC vérifié ne verrouille plus** : « Vérifié, il n'est pas parti » réécrit l'erreur,
  qui ne porte plus de trace de doute. Un ECHEC sans doute (« post supprime apres le gel »)
  non plus : le robot le reprenait déjà.
- **Limite héritée** : un échec d'avant le préfixe (`str(faute)` nu) n'a pas de trace, donc ne
  verrouille ni le robot ni la main. Seule son exclusion le couvre, comme ci-dessus.

**La carte d'un article retenu par la ceinture sans exclusion** (remis en file avant la
fusion) offrait « déjà engagé » sans issue : `exclusion_a_verifier` est vrai quand un créneau
porte le doute, et « Remettre dans la file » ouvre la même vérification. `reintegrer` lit le
`rowcount` : si le robot a enregistré entre-temps, 409 et rien n'est levé. Retirer un tel
ECHEC ne répond plus « Publication en cours » hors de la fenêtre de `publication_en_vol`.

**Limites connues, déduites du code.** La composition peut tomber jusqu'à un passage
d'agent après l'heure calculée, et plus si le Mac dort ; une purge dans cet intervalle
donne un créneau mort, que l'arbitrage répare. `PATCH /posts` accepte
encore `template` sans plus aucun bouton. `GET /posts/{id}/export.zip` reste sans bouton.

## Échanger (2026-10-31)

Demande de l'utilisateur : glisser une ligne de la Programmation sur une autre, et elles
permutent. Le geste remplace « Décaler », qui n'échangeait qu'avec le créneau suivant du même
format et échappait à la limite de purge. L'action `decaler` a quitté le serveur : le PATCH
la refuse en 422.

`PATCH /schedule/{id}` avec `{"echanger_avec": <creneau>}` ; `GET
/schedule/{id}/echanges-possibles` dit pour chaque ligne si c'est permis, et sinon pourquoi.
**Une seule règle, `programmation.raison_echange`**, lue par les deux :
`test_la_lecture_et_le_patch_ne_divergent_jamais` joue le PATCH sur toute une matrice et
compare la raison au mot près. Lecture groupée, sans requête par ligne, sans écriture, sans
réseau. Aucun appel à Meta ni à Claude, aucun chemin vers `media_publish` : la paire (actu,
post) se déplace, et l'agent gèle à son passage.

**Permuter deux lignes, dans une transaction.** Les deux, ou rien :
- Les lignes sont reprises sous `SELECT … ORDER BY id FOR UPDATE`, puis jugées sur cette
  lecture. Sans ordre stable, A↔B et B↔A croisés s'interbloquent : mutation vérifiée sur un
  cluster Postgres jetable (`deadlock detected`), test
  `test_sur_postgres_deux_echanges_croises_ne_se_bloquent_pas`.
- L'écriture reste conditionnelle sur (état, article, post), des deux côtés et par id
  croissant : SQLite n'a pas de `FOR UPDATE`, et c'est la même garantie que l'arbitrage (voir
  « L'arbitrage écrivait sur une lecture périmée »). Une ligne touchée à zéro : tout est
  annulé, 409. Sans la condition, un gel tombé entre les deux se faisait écraser : les fichiers
  de l'un, la légende de l'autre (`test_l_echange_n_ecrit_rien_si_l_agent_gele…`).
- `occupant_attendu` et `occupant_source_attendu` : la validation a nommé les deux actus,
  l'arbitrage a pu en changer. Présent et faux, 409. C'est aussi ce qui empêche « Annuler »
  d'échanger une actu que personne n'a vue.

**Ce qui est refusé.** Seuls LIBRE et PROGRAMME à venir échangent (`raison_creneau`, dite en
clair par `raison_fige` : gelé, publication en cours, publié, aperçu, manqué, passé). Deux
créneaux libres n'ont rien à échanger. Une actu exclue, doublon ou verrouillée ailleurs ne se
déplace pas : l'épingle la protégerait de l'arbitrage qui l'aurait retirée. **La limite de
purge s'applique à l'arrivée, dans les deux sens**, avec la même exception que Programmer
(brouillon valide). Une raison qui vient d'une actu la nomme : sur une ligne grisée,
« inutilisable en reel » ne dit pas laquelle des deux.

**Le format appartient au créneau, et le choix pour un post composé : REFUSER.** Ce qui se
passerait sinon, lu dans le code : au gel, `post.template != TEMPLATES[creneau.format]` fait
lâcher le post (« gel refuse : le post est au gabarit … »), la composition suivante en refait
un, et Claude est repayé, en silence et loin du geste. Le détacher dès l'échange supprimerait
le mauvais gabarit, pas la dépense ni la perte d'une correction humaine, et « Annuler » ne
rendrait plus ce qu'on a vu. L'échange déplace donc une paire et ne compose rien : un post ne
change jamais de gabarit, et le refus dit quoi faire (« Choisir une actu » sur ce créneau, qui
nomme ce qui sort). Sans post, carrousel ↔ reel est permis si chaque actu est éligible au
format d'arrivée (`_raison_format`). Le format n'est pas relu quand il ne change pas : la
composition applique de toute façon sa règle. Un brouillon existant est repris au gabarit de
l'arrivée, jamais à l'autre, comme pour Programmer.

**Épingle** : les deux créneaux qui reçoivent un choix humain, y compris un post orphelin
(`_reprogramme` ne regardait que l'article : il arrivait sur un créneau LIBRE). Un créneau
devenu vide n'est pas épinglé : le robot peut le remplir.

**Limites connues, déduites du code.** « Annuler » refait l'échange inverse : il ne rend ni une
épingle absente avant le geste, ni une erreur effacée. La notification de relecture est
mémorisée par créneau (`deja_annonce`) : un créneau rouvert puis échangé peut garder le mémo de
son ancien occupant. La lecture est faite au début du glissement : le dépôt peut donc être
refusé par le PATCH, et la raison s'affiche alors à côté de la ligne.

### Ce que le gel fait d'une edition humaine (2026-10-31)

Aucun chemin nouveau vers `media_publish`, `_reclamer` inchange. `_geler` ne fige pas un post edite
pendant son rendu, ne relache plus un post corrige pour un repere perime (il part avec un
avertissement), et libere sans exclure quand un contenu fige edite est refuse. Une panne machine
n'exclut toujours rien. Detail, preuves et limites : `relecture-et-controle-editorial.md`, sections
du 2026-10-31. Prouve par test, jamais observe en production.
