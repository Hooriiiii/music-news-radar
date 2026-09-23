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
