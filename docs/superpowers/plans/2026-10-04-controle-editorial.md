# Le contrôle éditorial sur ce qui va partir

Demande de l'utilisateur le 2026-10-04 :

> « J'aimerais que ce soit programmé tout seul à 100 %, mais que je puisse
> intervenir sur ce qui est généré. Si je n'ai mis aucune action, ça sera posté
> automatiquement. […] Pendant que c'est gelé, je peux les modifier. […]
> J'aimerais pouvoir cliquer et voir ce qui va être posté **exactement**, parce
> que c'est déjà généré quand c'est gelé, et pouvoir remodifier : changer le
> hook, voir la description et la modifier, et toutes les slides — en rajouter,
> en supprimer, les modifier. Avoir vraiment le contrôle total sur ce qui va
> être posté. Une première couche d'automatisation est faite, et moi je peux
> modifier après, si l'automatisation n'a pas réussi à 100 %. »

## Pourquoi le GEL est le bon moment, et pas avant

C'est le raisonnement de l'utilisateur, et il est juste : **avant le gel, le
créneau peut encore être réattribué** à une actu mieux notée par l'arbitrage —
modifier serait du travail perdu. **Une fois gelé, le choix est arrêté** : le
post ne bougera plus, donc l'édition porte.

Mesure à l'appui (85 créneaux gelés) : le post existe en médiane **12 h** avant
son départ, mais **14 % des créneaux sont gelés à moins de 5 minutes** du leur
(passage rattrapé au réveil du Mac). La fenêtre utile est donc « de la
composition au départ », avec le gel comme moment où l'édition devient **utile**.

## L'invariant qui prime sur tout

**Si l'utilisateur ne fait rien, ça publie à l'heure.** L'édition est une
option, jamais une condition. Un post qui attendrait une validation serait une
régression majeure : le taux de manqués est déjà élevé, et un post bloqué
ressemble exactement à un post en cours de traitement.

Verrouillé par un test dédié, déjà en place
(`test_la_relecture_n_est_jamais_une_condition_de_publication`).

## Le défaut de fond : la liste publiée est RECALCULÉE, jamais écrite

C'est la racine du problème, et ça se mesure : le post 164 (Tomorrowland)
affichait **1 slide** dans la fiche, et Instagram a reçu **3 images** — dont un
logo Wikipédia recadré, que l'utilisateur a repéré **après** publication.

`posts.slides` est le **brouillon**. `preparation.medias_du_carrousel` ajoute
au gel jusqu'à 9 médias de l'article, par-dessus, sans que rien ne persiste le
résultat. Personne ne peut donc voir — ni corriger — ce qui part réellement.

**Tant que cette liste reste calculée, aucun contrôle éditorial n'est
possible.** C'est la première tâche, et les autres en dépendent.

## Global Constraints

- **L'app reste AUTOMATIQUE.** Aucune édition n'est requise pour publier.
- **Plutôt manquer un post que le publier deux fois.** La réclamation (GELE →
  ECHEC committé **avant** l'appel à Meta) est le garde-fou contre le doublon.
  Toute tâche qui s'en approche doit démontrer qu'elle ne peut pas publier deux
  fois.
- **Une édition ne doit jamais pouvoir coûter une actu.** Le marqueur
  `RAISON_RELECTURE` fait déjà **libérer** au lieu d'**exclure** quand un re-gel
  échoue après une réouverture. Étendre, jamais contourner.
- **Ne jamais repayer ce qui n'a pas changé.** Une légende ne coûte rien, une
  cover coûte un rendu Chromium, une slide coûte un téléchargement. Claude n'est
  **jamais** repayé : le créneau garde son `post_id`.
- **Pas d'édition quand la publication est en vol** (créneau réclamé, conteneur
  créé chez Meta). La fenêtre se ferme avant.
- Le dashboard reste **un seul fichier** HTML+CSS+JS vanilla, sans dépendance ni
  build.
- Règles de la refonte UX : une valeur non lue s'affiche **inconnue, jamais
  verte** ; un contrôle désactivé imprime sa raison ; navigation par **onglets**,
  filtres par **pastilles** ; charte rose `#FB8AC2` / crème `#FEF1F7`, rouge
  réservé aux alertes.
- **`autoflush=False`** est le réglage de production ; `flush()` explicite si on
  écrit puis relit. Ce piège a masqué quatre défauts ici.
- Fixtures de test **relatives à maintenant** : les dates figées ont pourri deux
  fois ce mois-ci.
- Commits en français, messages **sans accents**, terminés par
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Task 1 — La liste publiée devient explicite

Le gel écrit la **liste ordonnée des éléments qui partiront** — cover comprise,
médias de l'article compris, dans l'ordre exact, avec pour chacun son origine et
son fichier figé. Elle est lisible par l'API et, à partir de là, modifiable.

Points d'attention :
- `creneaux.fichiers` existe déjà (JSON) : regarder s'il porte la bonne chose ou
  s'il faut un champ propre ;
- un élément **écarté** au gel (`ImageEcartee` : trop petite, trop loin du 4:5,
  vidéo retombée sur sa vignette) doit apparaître **avec sa raison**, pas
  disparaître — c'est précisément ce que l'utilisateur veut voir ;
- la liste doit survivre à la purge de l'article (rétention 2 j), comme le reste
  du post : dénormalisée.

## Task 2 — L'éditeur, dans la Programmation

Cliquer un créneau **gelé** ouvre ce qui va partir, et le rend modifiable :

- **le hook** — les quatre variantes rendues, celle retenue mise en avant ;
- **la description et les hashtags** — déjà éditables sur un gelé depuis le
  2026-10-04, à rapatrier ici ;
- **le fond de cover** ;
- **les slides** — voir la liste finale, en **supprimer**, les **réordonner**,
  en **remplacer** ;
- **ce qui partira vraiment** : `GET /relecture` rend déjà le texte exact après
  aération, normalisation des hashtags et crédit. Le réutiliser, ne pas le
  refaire.

**Le re-gel est AUTOMATIQUE** (choix de l'utilisateur) : on modifie, le système
refait ce qui a changé et rien d'autre. Aucun bouton à cliquer, et l'état
affiché est toujours celui qui partira. Le refus à moins de 20 min du départ
existe déjà, avec sa raison écrite.

L'utilisateur dit explicitement que **l'onglet Flux d'actus ne lui sert plus** :
il travaille depuis la Programmation. Ne pas le supprimer — il porte le tri et
la détection — mais ne pas obliger à y passer pour éditer.

## Task 3 — Ajouter une slide depuis le disque

**Choix de l'utilisateur** : téléverser un fichier depuis son Mac.

- stockage local (le motif de `visuels_cache_dir` existe : copie locale, nom
  dérivé du SHA-256, purge à `visuels_cache_jours`) ;
- **validation Meta** : JPEG, 8 Mo, 320 à 1440 px de large, proportions 4:5 à
  1,91:1 pour une image ; les vidéos ont leurs propres specs. Un fichier refusé
  doit dire **pourquoi**, avant le gel, pas après ;
- **recadrage au ratio du carrousel** (1080×1350), avec la règle déjà posée :
  vertical ancré **en haut**, horizontal **centré** — « sur une photo de scène
  le sujet est dans le tiers haut » ;
- l'ajout déclenche le re-gel de ce seul élément.

## Ce que ce plan ne fait PAS

- Pas de changement de l'heure d'un créneau (`publier_a`) — chantier distinct,
  déjà identifié.
- Pas de sélecteur d'article à la place de l'invite numérique — idem.
- Pas de retouche d'image (recadrage manuel, filtres) : on remplace, on ne
  retouche pas.
