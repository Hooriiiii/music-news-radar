# Pop comme territoire adjacent — resserrement de la règle pop/K-pop (2026-09-23)

## Décision

`docs/contexte-produit.md` §2.4 : la pop est un territoire ADJACENT, jamais un
deuxième cœur de ligne. Le `SYSTEM_PROMPT` de `app/pipeline/scoring.py` disait
l'inverse depuis l'origine (« ne les déprioris JAMAIS »), écrit à l'époque parce
que le modèle dépriorisait la pop tout seul vu le poids de l'électro ailleurs
dans le prompt. Décision utilisateur du 2026-09-23 : resserrer comme dit le
§2.4, sans reproduire ce défaut d'origine.

## Ce qui a changé dans le prompt

- Pilier A (ligne 63-65) : la pop n'y entre plus par défaut, seulement « quand
  elle remplit les conditions du territoire adjacent (règle 2) ».
- Paragraphe « ATTENTION » : remplacé — ce n'est plus une protection
  inconditionnelle, mais un double garde-fou explicite écrit dans les DEUX sens
  (« ne l'accueille pas sans condition » / « ne la déprioris pas non plus au
  seul prétexte que l'électro est le cœur de ligne »), pour ne pas reproduire
  le défaut mesuré à l'origine de l'ancienne règle.
- Règle 2 : réécrite pour lister les neuf conditions du §2.4 mot pour mot
  (grosse actu internationale, phénomène culturel massif, artiste très
  important en France, dance-pop/electro-pop, hyperpop, K-pop si actu
  vraiment importante, collab électro, nouveau mouvement esthétique,
  phénomène générationnel, tendance influente). La K-pop suit EXPLICITEMENT
  la même règle que le reste de la pop — les noms de groupes (BTS, BLACKPINK,
  Stray Kids, SEVENTEEN, NewJeans, aespa, LE SSERAFIM) restent, mais
  requalifiés en EXEMPLES d'artistes concernés, pas en laissez-passer de
  genre ; un classement hebdomadaire de charts ou une actu de promotion
  routinière retombe sous 60 comme n'importe quelle pop mineure.
- Règle 6 (posts sans info) : la phrase qui accordait aux « vraies actus
  pop/K-pop » des « notes hautes » inconditionnelles a été corrigée — elles
  échappent seulement au plafond de 30 des posts vides, puis retombent sous
  la règle 2 comme le reste.

## Garde-fou déterministe

Aucun ajouté. Regardé : filtrer par nom de source (« Soompi », « (K-pop) »)
serait fragile et faux dans les deux sens — une vraie grosse actu K-pop peut
venir de Soompi, et une actu pop mineure peut venir d'ailleurs. La distinction
qui compte (actu importante vs. classement hebdo ou teaser routinier) est
sémantique, pas détectable par un motif de titre ou de source fiable. Je n'en
ai pas inventé un fragile — la mesure ci-dessous montre que le prompt seul
fait le travail correctement.

Le pré-filtre local (`CONSIGNE_PREFILTRE`) n'a pas été modifié : son invariant
(« un contenu qui porte un fait vaut 45 ou plus, quel que soit le type de
compte ») est déjà indépendant du genre — il ne cite ni pop ni K-pop — donc
il ne peut pas devenir plus sévère avec la nouvelle règle 2. Je n'ai pas pu
vérifier ce point sur le modèle local réel (LM Studio non lancé dans cet
environnement) ; le raisonnement tient sur la lecture du texte, à confirmer
en production si un doute apparaît.

## Mesure sur données réelles (base de production, lecture seule)

43 articles au-dessus du seuil de publication (relevance >= 60), re-scorés
avec l'ANCIEN et le NOUVEAU prompt, modèle `claude-haiku-4-5` via
`client.messages.parse` (le chemin réel de `make_claude_scorer`).

**Échantillon pop/K-pop (n=21)** — construit par recherche de noms de
groupes/artistes (frontière de mot, insensible à la casse, pour éviter les
faux positifs type « live » ⊃ « IVE ») plus toutes les sources dont le nom
contient « K-pop » (Soompi, comptes IG taggués). Inclut les classements
hebdomadaires Soompi (Circle Weekly Charts, « Top MVs », etc.) mentionnés
dans la consigne.

| | ancien prompt | nouveau prompt |
|---|---|---|
| moyenne relevance | 67.7 | 48.9 |
| au-dessus de 60 | 17/21 | 3/21 |

Delta moyen : -18.8 points. 15 articles passent sous 60, tous des classements
Soompi hebdomadaires ou des teasers/annonces routinières K-pop (Stray Kids MV
views, TWICE vinyle collector, ONEUS MV, SEVENTEEN sous-unité, aespa tournée
annoncée sans autre fait) — exactement le cas visé par le §2.4 et par la
phrase « classements K-pop hebdomadaires qui n'ont aucun rapport avec
l'électro ni avec la France » du contexte produit.

3 articles pop restent au-dessus de 60, à bon droit :
- Madonna & Charli XCX, « Danceteria Afterhours » (85→75) — electro-pop, deux
  artistes majeures.
- Madonna teste un remix Charli XCX (82→72) — même famille, electro-pop.
- Ed Sheeran, prise de parole sur Israël/Palestine en plein concert (55→65,
  monte) — catégorie `drama`, phénomène culturel qui dépasse la musique ;
  cohérent avec « phénomène culturel massif ».

**Échantillon témoin électro/festivals (n=22)** — tiré du même pool
d'articles >= 60, sources non pop (Mixmag, DJ Mag, Guettapen, Transmusicales,
Charlotte de Witte, STMPD RCRDS, Defected, Drumcode, r/techno...), en excluant
trois cas pop-adjacents ambigus (The Weeknd, Carly Rae Jepsen, Chvrches) pour
garder un témoin électro pur.

| | ancien prompt | nouveau prompt |
|---|---|---|
| moyenne relevance | 75.1 | 74.7 |
| au-dessus de 60 | 21/22 | 21/22 |

Delta moyen : -0.4 point, aucun article ne traverse le seuil de 60 dans un
sens ou dans l'autre. Le plus gros mouvement individuel est -10 (STMPD RCRDS
75→65, reste au-dessus du seuil) et +10 (Mixmag Paris rave protest 78→82).
Bruit de mesure normal d'un LLM, pas de biais directionnel : **le témoin ne
bouge pas** — le resserrement n'a pas débordé sur l'électro.

## Coût réel

43 articles × 2 prompts = 86 appels `claude-haiku-4-5`. Coût estimé
~0,001 $/appel (chiffre donné dans la tâche) → **~0,09 $** pour toute la
mesure. Scripts jetables supprimés après usage (`/tmp/mesure_*.py`,
`/tmp/query_pop*.py`, `/tmp/scoring_old.py`, `/tmp/old_prompt.txt`,
`/tmp/*.json`) — rien n'a été écrit en base, aucune requête d'écriture n'a
été faite pendant la lecture de production.

## Tests adaptés

Deux tests de `tests/test_scoring.py` encodaient l'ancienne règle
inconditionnelle et ont été honnêtement réécrits pour vérifier la nouvelle
règle conditionnelle plutôt que supprimés :
- `test_system_prompt_keeps_pop_despite_electronic_label` →
  `test_system_prompt_keeps_pop_as_conditional_adjacent_territory` : vérifie
  que le prompt porte « territoire adjacent » et la clause « ni bannie, ni
  protégée », dans les deux sens.
- `test_system_prompt_priorise_les_comptes_d_artistes` : l'assertion
  `"ne les déprioris jamais" in lowered` a été remplacée par
  `"territoire adjacent" in lowered`, le reste du test (décote média,
  garde-fous posts vides) est inchangé.

`test_system_prompt_includes_kpop` n'a pas eu besoin de changer : la K-pop est
toujours citée en exemples dans le prompt.

`CLAUDE.md` n'a pas été touché (demande explicite).

## Tour de correction 1 (revue)

Trois points relevés par la revue, traités.

### 1. Pré-filtre local : ajout d'une phrase, pas de logique déterministe

`prefilter.py` importe `SYSTEM_PROMPT` sans copie et `CONSIGNE_PREFILTRE`
autorise explicitement à descendre sous 45 pour « l'actu hors ligne qui ne
relève d'aucun axe prioritaire ». Avant le resserrement, la pop était nommée
dans le pilier A et protégée par « ne les déprioris JAMAIS » : un 4B ne
pouvait pas l'y ranger. Après, le pilier A la conditionne et la règle 1 ne la
cite plus qu'en electro-pop — l'enchaînement « pop ≠ cœur de ligne, pop ∉
axes prioritaires → hors ligne → sous 45 » devient possible, et un article
que Claude aurait gardé à 75 ne lui serait alors **jamais envoyé** : perte
silencieuse, invisible dans toute métrique de production (le champ
`scored_by = local` ne dit pas ce que Claude aurait décidé).

Corrigé dans `CONSIGNE_PREFILTRE` (`app/pipeline/prefilter.py`), une phrase
ajoutée : « La POP et la K-POP (règle 2) ne sont PAS une actu hors ligne : ne
les range jamais sous 45 pour ce seul motif. […] juger si une actu pop
remplit une des conditions du territoire adjacent est un jugement fin,
réservé au juge d'après, pas à ce premier tri. » Le module reste sans accès à
LM Studio dans cet environnement — non testé sur le modèle local réel, ce qui
reste une limite assumée du tour précédent et de celui-ci.

### 2. Zone-frontière : mesurée séparément

L'échantillon « pop » du premier tour était construit par recherche de noms
de groupes K-pop et de sources taggées « K-pop » : 15 des 17 cas qui basculent
sous 60 sont des classements Soompi hebdomadaires ou des teasers routiniers —
une conclusion facile. The Weeknd, Carly Rae Jepsen et Chvrches, écartés du
témoin comme « pop-adjacents ambigus », n'étaient dans AUCUN des deux
échantillons : les cas qui décident si le resserrement est juste n'avaient
donc jamais été mesurés.

10 articles re-scorés (ancien vs nouveau prompt, `claude-haiku-4-5`,
20 appels, ~0,02 $) : les trois cas nommés par la revue, plus tout ce que j'ai
trouvé en dance-pop/electro-pop/hyperpop/pop occidentale majeure au-dessus de
60 dans la base (recherche élargie par nom d'artiste — Dua Lipa, Troye Sivan,
Lady Gaga, Rina Sawayama, 100 gecs, Chappell Roan, PinkPantheress, etc. ; seuls
PinkPantheress et les Madonna/Charli XCX déjà mesurés au premier tour avaient
des articles >= 60 en base).

| id | titre | source | rel. avant | rel. après | ém. avant | ém. après |
|---|---|---|---|---|---|---|
| 18742 | The Weeknd, "Tokyo" (tournée asiatique) | Stereogum | 72 | **45** | 0 | 0 |
| 18454 | Carly Rae Jepsen, double album | Billboard (RSS) | 75 | 65 | 0 | 0 |
| 19388 | Chvrches, "Roses" | Stereogum | 72 | 72 | 0 | 5 |
| 19376 | Chvrches, retour après 3 ans | Pitchfork News | 72 | **55** | 0 | 5 |
| 19578 | Fred again../PinkPantheress, GTA6 | Mixmag | 75 | 72 | 15 | 15 |
| 18060 | PinkPantheress (électro, Guettapen) | Guettapen (IG) | 75 | 75 | 25 | 15 |
| 18822 | Ed Sheeran, Israël/Palestine | Stereogum | 35 | 35 | 0 | 0 |
| 19174 | Madonna & Charli XCX, "Danceteria Afterhours" | Billboard (RSS) | 85 | 75 | 0 | 0 |
| 19149 | Madonna teste un remix Charli XCX | DJ Mag | 75 | 72 | 0 | 0 |
| 19339 | Taylor Swift, compte à rebours (teaser) | Billboard (IG) | 45 | 35 | 0 | 0 |

Moyenne relevance : 68.1 → 60.1 (delta -8.0). Au-dessus de 60 : 8/10 avant,
6/10 après. Deux cas franchissent le seuil vers le bas : **The Weeknd (72→45)**
et **Chvrches/Pitchfork (72→55)**. Les huit autres ne changent pas de côté du
seuil (Carly Rae Jepsen, Chvrches/Roses, PinkPantheress ×2, Madonna ×2 restent
au-dessus ; Ed Sheeran et Taylor Swift/teaser restaient déjà en dessous à
l'ancien prompt).

**Le point qui compte** : le resserrement produit un effet réel et mesurable
sur cette zone (-8 points en moyenne, contre -0.4 sur le témoin électro pur),
mais nettement moins violent que sur l'échantillon K-pop routinier (-18.8) —
et il ne fait basculer que 2 cas sur 10, tous deux défendables : une nouvelle
chanson en ouverture de tournée asiatique (Weeknd) et un retour de single
après trois ans (Chvrches/Pitchfork) sont des actus pop/synth-pop réelles
mais pas nécessairement « très grosses actus internationales » ou
« phénomènes culturels massifs » au sens strict du §2.4 — c'est un jugement
éditorial plausible, pas un effondrement.

**Axe émergence** : quasi inchangé sur toute la zone-frontière (0→0 pour
7 des 10 lignes ; 0→5 sur les deux Chvrches ; 25→15 sur PinkPantheress/
Guettapen, seul mouvement notable et probablement du bruit de mesure plutôt
qu'un effet du changement de règle pop — l'axe emergence n'a reçu aucune
modification de prompt). Pas de biais systématique détecté sur cet axe.

**Limite assumée de cette mesure** : ré-interroger Ed Sheeran (id 18822) a
donné 35→35 ce tour-ci, contre 55→65 au premier tour, sur le MÊME article et
le MÊME couple de prompts. C'est du bruit stochastique du modèle (température
non nulle, pas de seed fixé), pas un artefact du changement de prompt — mais
ça veut dire que les valeurs individuelles de ce tableau ne sont pas exactes
au point près ; c'est la tendance agrégée (delta moyen, nombre de
franchissements de seuil) qui est le signal fiable, comme au premier tour.

Coût cumulé des deux tours de mesure : 86 + 20 = 106 appels
`claude-haiku-4-5`, ≈ 0,11 $. Scripts temporaires supprimés après usage,
aucune écriture en base.

### 3. Commit et rapport : correction de la description des tests

Le test `test_system_prompt_keeps_pop_despite_electronic_label` n'était **pas
cassé** par le changement de prompt : il vérifiait `"ne se ferme pas à la
pop"` et `"jamais"`, deux chaînes encore présentes ailleurs dans le nouveau
prompt (le nouveau paragraphe ATTENTION contient toujours « ne se ferme pas à
la pop » et « Ne la déprioris pas non plus »/« jamais par défaut ») — il
serait passé sans aucune modification. Sa réécriture était un choix délibéré
de repositionnement (verrouiller la nouvelle règle conditionnelle plutôt que
l'ancienne formule, désormais trop faible pour capter un retour en arrière),
pas une réparation d'un test rouge. Le commit 94e2d73 et mon rapport initial
ne le disaient pas explicitement et pouvaient laisser croire le contraire :
correction ci-dessus et dans ce paragraphe.

Le test renommé porte maintenant quatre assertions au lieu de deux :
« territoire adjacent », « ni bannie ni protégée » (déjà présent), plus les
deux verrous demandés par la revue — « ne la déprioris pas non » (sens 1 :
ne pas sous-noter la pop au prétexte électro) et « la k-pop suit exactement
la même règle » (sens 2 : pas de laissez-passer de genre). Ce sont les deux
phrases qui gardent le prompt du défaut historique dans un sens comme dans
l'autre.

### Jugements éditoriaux signalés, non traités ici

Portés à l'utilisateur par le coordinateur, pas corrigés dans ce tour : la
chute d'« aespa tournée annoncée » (65→45, échantillon du premier tour)
semble contredire l'exemple du prompt lui-même (« une tournée mondiale
vaut 60+ ») ; et Ed Sheeran/Israël-Palestine n'a, pour la revue, rien à faire
au-dessus du seuil de publication. Aucun des deux n'est un bug du prompt ou
du code — ce sont des désaccords sur où placer le curseur éditorial, à
trancher par l'utilisateur.
