# Phase 2A — l'ÉVÉNEMENT devient une entité, sans rien changer à la publication

Demande de l'utilisateur le 2026-09-23 : « applique les ajustements de la phase 2
dans une nouvelle branche ». La roadmap (`docs/contexte-produit.md` §37, et le
blueprint PDF) découpe la Phase 2 en 2A → 2E et répète « **pas de big rewrite** ».
Ce plan ne couvre que **2A**.

## Pourquoi maintenant, et ce que ça débloque

Le système raisonne POST par POST. Une même annonce vue chez l'artiste, chez son
label, dans Mixmag et sur Reddit produit aujourd'hui **quatre doublons qu'on
jette**. Le blueprint le dit autrement, et c'est la bonne façon de le voir :

> Ce ne sont pas quatre contenus inutiles. C'est **un événement avec quatre
> preuves, trois sources indépendantes, et une vitesse de propagation**.

Tout ce que la Phase 2 veut mesurer — vitesse, corroboration, nouveauté, qui a
publié en premier — est **déjà dans nos données** et part à la poubelle à chaque
passage. 2A ne calcule encore rien de neuf pour la publication : elle **arrête de
jeter**.

Bénéfice de coût immédiat, cité par le blueprint : « 12 articles sur la même
annonce = 1 appel lourd, pas 12 ». On le fait déjà via la dédup ; l'événement le
rend explicite et mesurable.

## Global Constraints

- **Rien ne change dans le comportement de publication.** Aucune note, aucun
  créneau, aucun post ne doit bouger. 2A est une couche d'OBSERVATION posée à côté
  de ce qui tourne. Un test doit le prouver : le pipeline complet, avant et après,
  produit les mêmes décisions sur les mêmes données.
- **La migration est ÉCRITE mais PAS APPLIQUÉE.** La base de production est chez
  Neon et l'utilisateur doit donner son accord. Vérifie-la sur un cluster jetable
  (`/Library/PostgreSQL/15/bin/initdb` sur un autre port, voir `CLAUDE.md`), jamais
  sur la sienne.
- `uv run pytest` (1717 au départ) et `uv run ruff check .` verts. `uv run alembic
  check` doit passer (parité modèles ↔ migrations).
- Les tests des chemins de base tournent en `autoflush=False` : c'est le réglage de
  production (`app/db.py:40`), et un test en autoflush par défaut a déjà masqué un
  défaut de production dans ce dépôt.
- Convention du dépôt : SQLAlchemy 2.0 (`Mapped` / `mapped_column`), enums en
  VARCHAR + CHECK (`native_enum=False`), JSONB via `JSONVariant`, `op.f()` **jamais
  au niveau module** d'une migration (voir la sentinelle `tests/test_migrations.py`).
- Commentaires et commits en français, sans accents dans les messages de commit,
  terminés par `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Task 1 — Le schéma : `events`, `event_observations`

### Ce qu'il faut créer

**`events`** — l'histoire générale. Un enregistrement par actualité réelle.
- `id`, `titre` (celui du représentant élu, qui peut changer), `event_type` (NULL
  pour l'instant : la taxonomie est un chantier à part), `first_seen_at`,
  `last_seen_at`, `source_count`, `platform_count`, `independent_source_count`.
- Les compteurs sont **dénormalisés et recalculés**, jamais devinés : ils doivent
  pouvoir être reconstruits à partir des observations.

**`event_observations`** — une preuve, c'est-à-dire un article rattaché à un
événement.
- `event_id`, `article_id` (FK **`ON DELETE SET NULL`**, comme `artist_mentions` :
  la purge à 2 jours casse le lien, jamais l'histoire), `platform` (le
  `sources.type` figé au moment de l'observation), `source_id`, `source_role`
  (`primary` si le compte est artiste/festival/label/club, `secondary` si media,
  `unknown` sinon), `published_at`, `observed_at`.
- **DÉNORMALISE** ce dont les features auront besoin après la purge : titre, url,
  nom de la source. Même raisonnement que `artist_mentions`, et c'est ce qui rend
  l'archive utile au-delà de 2 jours.

**`source_group_id` sur `sources`** — le point que le blueprint appelle essentiel :

> AFP reprise par trois sites ≠ trois sources indépendantes.

Une colonne nullable sur `sources` suffit pour 2A : deux sources qui partagent un
`source_group_id` comptent pour UNE source indépendante. Personne ne la remplit
encore ; la structure doit juste exister et être respectée par le calcul de
`independent_source_count`.

### Migration

Une seule, numérotée `0019`. **Écrite, vérifiée sur un cluster jetable, PAS
appliquée sur la base de l'utilisateur.** Dis dans ton rapport la commande exacte
qu'il devra passer, et ce qu'elle fera.

### Vérification attendue

La sentinelle `tests/test_migrations.py` charge toutes les révisions sans base :
elle doit rester verte. `alembic check` doit passer. Et un test prouve qu'un
`event_observation` survit à la purge de son article, avec ses champs dénormalisés
intacts.

## Task 2 — Le rattachement : la dédup alimente les événements

### Ce qu'il faut faire

`app/pipeline/cluster.py` sait déjà rapprocher les articles et élire un
représentant. **Ne réécris pas ce mécanisme** : il a fait l'objet de quatre tours
de correction et d'un fuzzer qui a validé l'invariant « jamais de chaîne » sur
22 611 articles. Tu t'y **branches**.

- Un représentant sans événement en crée un ; chaque membre de son groupe devient
  une `event_observation`.
- Un article qui rejoint un groupe existant ajoute une observation à l'événement
  du représentant, et met à jour `last_seen_at` et les compteurs.
- **`first_seen_at` ne recule jamais** : c'est la date du fait le plus tôt, elle
  répond à « qui a publié en premier ».
- Idempotent : rejouer un passage ne duplique aucune observation. Le couple
  (`event_id`, `article_id`) est unique.
- **Les liens d'archive comptent aussi** : un article relié à un post déjà publié
  est une observation de plus sur le même événement, même si l'article original a
  été purgé.

### Le piège

`link_duplicates` est appelé par `run_pipeline` (toutes les heures) ET par
`run_veille` (toutes les 5 minutes). Les deux doivent alimenter les événements —
c'est de l'écriture locale, sans appel payé — mais vérifie le coût en requêtes : la
veille tourne 288 fois par jour.

### Vérification attendue

Le pipeline complet, sur un jeu de données réel, produit **exactement les mêmes
décisions de publication** qu'avant ce chantier. Plus : les compteurs sont
reconstructibles depuis les observations, l'idempotence tient, et `first_seen_at`
ne recule jamais.

## Task 3 — Les features déterministes, calculées en CODE

> « Le LLM comprend. Le code compte. » (blueprint)

Aucune de ces valeurs ne doit être demandée à un modèle. Elles se calculent sur
les observations, dans un module pur, testable sans base ni réseau.

- **`freshness`** : `F(t) = 100 × 2^(−t/h)`, `t` en heures depuis la première
  apparition FIABLE du fait. La demi-vie `h` dépend du type d'actu (décès,
  annulation, drama : 3 h ; annonce majeure / line-up : 8 h ; sortie /
  collaboration : 18-24 h ; festival / billetterie / industrie : 24-36 h ;
  interview / culture : 72 h). En attendant la taxonomie d'événements, prends une
  demi-vie par défaut et rends-la **réglable** ; justifie ta valeur.
- **`corroboration`** : `K = 100 × (1 − e^(−n/3))`, `n` = nombre de sources
  **indépendantes** (donc en respectant `source_group_id`). Barème de contrôle
  donné par le blueprint : 1 → 28, 2 → 49, 3 → 63, 5 → 81, 8 → 93.
- **`source_count`**, **`platform_count`**, **`independent_source_count`**.

**Ne calcule PAS `velocity` dans cette tâche** : elle demande un historique
d'observations sur 22 h que la rétention à 2 jours rend fragile, et elle mérite
son propre chantier avec sa propre mesure.

### Vérification attendue

Chaque formule testée contre les valeurs de contrôle du blueprint. Le module ne
touche ni la base ni le réseau. Et une mesure sur les données RÉELLES de
production, en lecture seule : la distribution de `corroboration` et de
`source_count` sur les groupes existants — combien d'événements ont 1, 2, 3+
sources indépendantes ? C'est le chiffre qui dira si la Phase 2 a de la matière.

## Task 4 — Le rendre VISIBLE, sans rien lui faire décider

Un mécanisme qu'on ne voit pas est un mécanisme qu'on ne corrigera jamais.

- `GET /events` (liste, tri par `last_seen_at` ou par `source_count`) et
  `GET /events/{id}` (avec ses observations).
- Un panneau au dashboard, **en lecture seule** : les événements récents, leur
  nombre de sources, leurs plateformes, leur fraîcheur, et la liste des articles
  qui les composent.

C'est cette vue qui permettra de décider, sur pièces, si le clustering d'événements
(Phase 2B) vaut le coup — plutôt que sur une intuition.

### Ce qu'il ne faut PAS faire

Aucun endpoint d'écriture, aucune décision de publication branchée sur les
événements, aucun changement de l'arbitrage. 2A observe.
