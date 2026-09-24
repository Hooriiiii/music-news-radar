# Phase 2B — tout article appartient à un événement, et la dédup gagne du sens

Demande de l'utilisateur le 2026-09-24 : « fais la phase 2B ». La roadmap
(`docs/contexte-produit.md` §37) la définit en une ligne : « faire évoluer la
dedup vers de l'event clustering ». Ce plan ne couvre que 2B, et répète la règle
du blueprint : **pas de big rewrite**.

## Ce que la mesure a dit, et ce qu'elle a renversé

Mesuré le 2026-09-24 sur la base de production (640 articles, 46 événements,
116 observations ; 50 verdicts Haiku, ~0,05 $ — scripts dans le scratchpad de la
session) :

| constat | chiffre |
|---|---|
| articles rattachés à un événement | 113 / 640 (**18 %**) |
| événements à 2 ou 3 preuves seulement | 89 % |
| solitaires à recouvrement de titre **strictement nul** | 272 (52 %) |
| faux rapprochements si le seuil descendait à 0.40 | **87 %** (20 sur 23) |

Trois conclusions, dans l'ordre d'importance.

**1. Le vrai manque n'est pas le rappel, c'est la COUVERTURE.** 82 % du flux
n'appartient à aucun événement, parce qu'un événement naît aujourd'hui
exclusivement d'un groupe de doublons (`attacher` n'est appelé que depuis
`cluster.lier`). Tout ce que 2C (vitesse, corroboration) et 2D (classement par
événement) veulent calculer est donc structurellement impossible sur l'essentiel
du flux. Un événement à une seule preuve reste un événement.

**2. Abaisser le seuil de la dédup est DISQUALIFIÉ.** 87 % de faux rapprochements
dans la bande 0.40-0.65, mesuré, sur un lien irréversible en pratique — un
article lié sort de la file de publication. Ce n'est pas un réglage à ajuster,
c'est une voie fermée.

**3. Les vrais ratés commencent à 0.33 et la courbe est PLATE jusqu'à 0.57**
(22 % / 20 % / 10 % de vrais même-événements par tranche). Trois des cinq sont
hors de portée de n'importe quel seuil : « Taylor Swift annonce *Patient Zero* »
contre « un compte à rebours sur son site » ; une Techno Parade racontée en
anglais par son nombre de participants et en français par des ravers sur un
échafaudage. Seul le SENS les attrape — d'où un arbitrage, pas un seuil.

**Ce que la mesure n'a pas pu dire, et qu'il ne faut pas faire semblant de
savoir** : 5 positifs sur 50 est trop mince pour un taux (l'intervalle sur
« 2/9 » couvre ~3-60 %), le témoin ≥0.65 n'a jamais été mesuré (crédits épuisés),
et surtout **les 272 solitaires à recouvrement strictement nul n'ont jamais été
testés**. C'est le seul endroit où des embeddings se justifieraient un jour ;
aucune preuve n'existe aujourd'hui qu'il y ait quoi que ce soit dedans. **Ce plan
n'ajoute donc AUCUNE dépendance d'embeddings** (le dépôt n'en a aucune, et en
ajouter une veut dire soit un quatrième fournisseur externe avec sa clé, son
quota et sa pause, soit torch dans un agent launchd).

## Décisions de l'utilisateur (2026-09-24)

- **Portée** : structure + rappel ciblé. Chaque article devient l'observation
  d'un événement ; ET l'arbitrage Claude s'élargit aux paires fraîches.
- **Croissance** : les événements sans valeur sont purgés. Un événement à UNE
  seule observation, dont l'article est sous le seuil de pertinence et n'a pas
  été publié, meurt avec son article à 2 jours. Dès deux preuves, ou dès qu'il
  est publié, il devient permanent.

## Global Constraints

- **Rien ne change dans le comportement de PUBLICATION.** Aucune note, aucun
  créneau, aucun post ne doit bouger du fait de ce chantier, à la seule exception
  voulue des liens de doublon nouvellement posés par l'arbitrage élargi (Task 3).
  Un test doit prouver que le pipeline produit les mêmes décisions sur les mêmes
  données quand l'arbitrage élargi est coupé.
- **Aucune migration.** Les tables `events` / `event_observations` de la
  migration 0019 suffisent. Si un besoin de colonne apparaît, il est signalé et
  la tâche s'arrête — pas de schéma inventé en cours de route.
- **Une panne n'interrompt JAMAIS le passage horaire.** Même invariant que le
  regroupement des sources : rattrapage, `rollback`, aucune exception qui remonte,
  et le passage continue sur la dédup, le scoring, les mentions et la purge.
- **`autoflush=False` est le réglage de production** (`app/db.py`). Tout code qui
  écrit puis relit dans la même transaction pose un `flush()` explicite. Les tests
  du périmètre base doivent le régler explicitement : un test en autoflush par
  défaut a déjà masqué un défaut de production dans ce dépôt, deux fois.
- **Le doute favorise toujours la publication.** Une panne d'arbitrage ne pose
  jamais de lien. Un verdict manuel prime sur tout rapprochement automatique.
- `uv run pytest` (1811 au départ) et `uv run ruff check .` verts.
- Commentaires et commits en français ; messages de commit **sans accents**,
  terminés par `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Task 1 — Tout article devient l'observation d'un événement

Aujourd'hui `events.attacher(db, article, representant)` n'est appelé que depuis
`cluster.lier`, donc seul un article qui vient d'être lié comme doublon entre dans
un événement.

Il faut un second point d'entrée : **un article sans événement en reçoit un**,
avec une observation unique. La bonne nouvelle est que la mécanique difficile
existe déjà — `attacher` sait fusionner les événements de plusieurs membres d'un
groupe (`_fusionner`). Donc si chaque article naît avec son propre événement, un
rapprochement ultérieur FUSIONNE les deux, et il n'y a rien de neuf à écrire pour
ce cas.

Où l'appeler : dans `app/pipeline/run.py`, **après le scoring** (`score`) et avant
`record` — le rôle de la source et la plateforme sont connus dès l'ingestion, mais
la pertinence, qui décide de la purge (Task 2), ne l'est qu'après le scoring.
Bornage obligatoire, même esprit que `illustration_max_par_run` : un plafond par
passage, sinon le premier passage partirait sur des centaines d'écritures.

Exigences :
- idempotent (un article déjà observé n'est jamais observé deux fois) ;
- les champs dénormalisés d'`EventObservation` sont remplis comme aujourd'hui
  (titre, url, nom de source, plateforme, rôle, dates) — c'est ce qui rend
  l'archive utile après la purge des articles ;
- un article **doublon** ne crée jamais son propre événement : il rejoint celui de
  son représentant ;
- ne lève jamais ; une panne laisse le passage continuer.

## Task 2 — Purger les événements sans valeur

Dans `app/pipeline/retention.py`, à côté de la purge des articles.

Règle, telle que l'utilisateur l'a tranchée :
- un événement à **une seule observation**, dont l'article est **sous le seuil de
  pertinence** et **n'a pas été publié** (`USED`, un `Post`, un `Creneau` engagé)
  → supprimé en même temps que son article ;
- **deux observations ou plus** → permanent, sans condition ;
- **publié** → permanent, sans condition. C'est l'archive éditoriale, même motif
  que `artist_mentions` et que l'exception `USED`.

Piège à traiter explicitement : `event_observations.article_id` est
`ON DELETE SET NULL`. Une fois l'article purgé, l'observation survit avec un
`article_id` à NULL et on ne peut plus juger sa pertinence. **La décision doit
donc être prise AVANT la purge des articles**, pas après — sinon la règle ne
s'applique jamais et la table grossit quand même.

Un test doit prouver les trois cas, et qu'un événement à deux preuves dont les
deux articles sont purgés survit avec ses observations orphelines.

## Task 3 — L'arbitrage Claude s'élargit aux paires FRAÎCHES 0.30-0.65

La machinerie existe (`app/pipeline/cluster_arbitrage.py`) : cache par paire,
verdicts manuels prioritaires, plafond de tentatives, plafond par passage,
écriture atomique, coupe-circuit. Elle n'est aujourd'hui branchée que sur la bande
douteuse **contre l'archive**. Il s'agit de l'ouvrir aux paires fraîches.

- **Plancher à 0.30**, pas 0.35 : les deux ratés purement sémantiques mesurés sont
  à 0.33, et sous 0.30 la mesure donne 0 vrai positif sur 18 paires. Le seuil suit
  les chiffres.
- **Plafond à 0.65** : au-dessus, le lien est déjà posé automatiquement.
- Réglable et **coupable** depuis `.env`, avec la valeur par défaut ci-dessus.
- Seul le **passage horaire** arbitre, jamais `run_veille` (toutes les 5 min) —
  distinction par PARAMÈTRE, comme aujourd'hui, jamais en devinant l'appelant.

**Le blocage anti-gabarit, qui est la partie qui économise le budget.** 53 % de la
bande 0.40-0.65 mesurée est un seul compte (AIM UK) répétant « The winner of *X*
at the 2026 Independent Music Awards » : un gabarit de légende, pas un signal
d'événement. Il faut écarter ces paires **avant de payer**, ce qui ramène la bande
de 323 à ~107 paires sur la fenêtre.

Le remède naïf est un piège mesuré : filtrer « même source des deux côtés »
tuerait **3 des 5 vrais ratés** (Billboard×2, Soompi×2). Ce qu'il faut détecter,
c'est le **gabarit répété d'une même source** — des n-grammes de titre qui
reviennent sur plusieurs articles du même compte —, pas l'identité de la source.

Budget attendu : ~3-4 appels/h, largement sous le plafond existant. Le mesurer et
le dire dans le rapport plutôt que de le supposer.

## Task 4 — Le rapport dit ce qui s'est passé

Les compteurs du passage horaire doivent montrer le nouveau travail : événements
créés, événements fusionnés, événements purgés, paires fraîches arbitrées, paires
écartées par l'anti-gabarit. Une panne de l'une de ces étapes rejoint
`report.problemes`, donc la notification de fin de run.

Motif de la règle, déjà payé cher dans ce dépôt : un agent a échoué chaque jour
pendant cinq semaines sans que rien ne le signale, et « 116 sources en erreur » a
longtemps été un mensonge de supervision sous lequel la vraie panne était
invisible. Un compteur qui n'est affiché nulle part est un compteur mort.

## Ce que ce plan ne fait PAS

- Aucune dépendance d'embeddings (voir plus haut : le cas qui la justifierait
  n'est pas mesuré).
- Aucune migration, aucune colonne nouvelle.
- Aucun changement de la taxonomie `event_type` (reste NULL — chantier à part).
- Aucun changement du classement ni de la publication : c'est 2C et 2D.
- Aucune baisse de `DEFAULT_THRESHOLD` : mesuré à 87 % de faux.
