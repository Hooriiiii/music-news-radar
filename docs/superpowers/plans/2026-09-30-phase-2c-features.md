# Phase 2C — vélocité, nouveauté, pertinence France

Demande de l'utilisateur le 2026-09-30 : « fais la phase 2C ». La roadmap
(`docs/contexte-produit.md` §37) la définit ainsi : « ajouter velocity,
source_count, platform_count, first_seen, novelty, France relevance ».

**Trois de ces six existent déjà** depuis 2A : `source_count`,
`platform_count`, `first_seen_at` sont des colonnes d'`events`, recalculées par
`events._recalculer`. `freshness` et `corroboration` sont déjà calculées à la
lecture par `app/api/events.py`. Le neuf de 2C est donc : **vélocité**,
**nouveauté**, **pertinence France**.

## La mesure qui dicte la conception (2026-09-30, base de production)

901 événements, 1 259 observations.

| preuves par événement | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| événements | **767** | 94 | 27 | 6 | 7 |

Étalement des événements multi-preuves (n=205) :

| mesuré sur | médiane | < 1 h | > 6 h |
|---|---|---|---|
| date de **publication** | **10,69 h** | 53 | 117 |
| date d'**ingestion** | 1,68 h | 91 | 77 |

**La conclusion qui gouverne toute la phase : la vélocité se calcule sur
`published_at`, JAMAIS sur `observed_at`.** L'étalement d'ingestion (1,68 h)
mesure notre propre cadence horaire, pas la propagation de l'actu — 44 % des
événements y tiennent en moins d'une heure parce que le pipeline les a ingérés
dans le même passage. L'étalement de publication (10,69 h) mesure la réalité.
Se tromper de champ produirait une métrique qui a l'air de marcher et qui ne
décrit que nous.

**Deuxième conclusion : 85 % des événements ont UNE seule preuve.** Une vélocité
sur un point n'existe pas, et une accélération demande au moins trois points —
**76 événements** en ont. Ces features doivent donc rendre « inconnu », jamais
un zéro fabriqué qui se propagerait dans un classement comme une vraie valeur.

## Global Constraints

- **Rien ne change dans le comportement de PUBLICATION.** 2C calcule et
  expose ; c'est 2D qui classera. Un test doit le prouver.
- **Aucune feature ne ment par défaut.** Une valeur non calculable est `None`,
  jamais `0.0`. Zéro veut dire « mesuré à zéro » ; `None` veut dire « pas
  assez de matière ». Les confondre est exactement ce qui rend un classement
  faux sans qu'on le voie.
- **Pas de nouvelle dépendance, pas de fournisseur externe.** Même règle qu'en
  2B, et la mesure y a donné raison.
- **Aucun appel LLM nouveau sans mesure préalable** montrant qu'un signal
  déterministe ne suffit pas. Changer le prompt de scoring coûte une re-notation
  de tout le flux.
- `app/pipeline/event_features.py` reste **pur** : pas de session, pas de
  réseau, pas d'horloge implicite. Les valeurs entrent par paramètres nommés.
  C'est ce qui le rend testable, et la propriété à ne pas perdre.
- **`autoflush=False` est le réglage de production** (`app/db.py`) alors que
  les fixtures de test sont en `True`. Ce piège a mordu ce dépôt trois fois.
- Commits en français, messages **sans accents**, terminés par
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Task 1 — Vélocité

Dans `app/pipeline/event_features.py`, en gardant sa pureté.

- **Le taux** : preuves par heure, sur l'étalement des `published_at`. `None`
  sous deux preuves datées, ou quand l'étalement est nul (deux articles publiés
  à la même seconde : le taux serait infini, pas énorme).
- **L'accélération** : comparer la densité de la première moitié de la fenêtre
  à celle de la seconde. `None` sous trois preuves datées — c'est le cas de 825
  événements sur 901, et le dire est plus utile que de rendre 0.
- L'appelant fournit les dates ; le module ne lit jamais la base.

Le but éditorial, dans les mots du blueprint : **distinguer « actualité
importante mais froide » de « actualité qui explose maintenant »**. Une vélocité
qui ne distingue rien n'a pas d'intérêt — le vérifier sur des données réelles
fait partie de la tâche.

## Task 2 — Nouveauté

« Cette actu est-elle neuve, ou une reprise de quelque chose qu'on a déjà vu ? »

L'information existe et ne coûte rien : `event_observations` **n'est jamais
purgée** et porte titre, source et dates. C'est la même archive qui sert déjà la
garde de récurrence de la porte résumé (2B) — la relire ici est gratuit.

Attention, limite connue et à redire dans le code : cette archive est **jeune**
(la couche date du 23 septembre). Un critère calé dessus aujourd'hui se
renforcera avec le temps ; il ne faut pas le calibrer comme s'il voyait des mois.

La nouveauté est `None` quand l'archive est trop courte pour conclure.

## Task 3 — Pertinence France

**Décision prise en amont, à ne pas renégocier** : d'abord un signal
**déterministe et gratuit**, mesuré. Le modèle seulement si la mesure montre
qu'il ne suffit pas. Ajouter un champ au scoring coûterait un changement de
prompt et une re-notation de tout le flux, pour un bénéfice non démontré.

Il n'existe **aucun champ « pays »** sur les sources (vérifié). Les signaux
disponibles sont à chercher, et le choix doit être justifié par une mesure sur
données réelles : langue du titre ou du résumé, artistes cités, villes,
`sources.genre` / `sources.categorie`, nom de la source.

Le compte est **français national** (voir `CLAUDE.md`) : cette feature sert à
ne pas noyer la scène FR sous l'actu internationale au moment du classement.
Elle ne doit PAS être fondue dans `relevance` — une note répond à « cette
information est-elle bonne ? », pas à « en ai-je assez publié cette semaine ? ».

**Mesure exigée** : sur un échantillon réel, quelle proportion d'événements le
signal classe FR, et combien de faux positifs / faux négatifs à la lecture.
S'il ne sépare rien, le dire et s'arrêter là vaut mieux que de livrer une
feature décorative.

## Task 4 — Exposer, et vérifier que ça dit quelque chose

Les trois features rejoignent `app/api/events.py`, à côté de `freshness` et
`corroboration`. Le dashboard les affiche en lecture seule.

Puis la question qui compte : **sur les événements réels, ces features
distinguent-elles quoi que ce soit ?** Sortir les dix événements les plus
rapides, les plus corroborés, les plus FR — et regarder si ce sont les bons.
Un rapport honnête vaut mieux qu'un tableau de bord qui rassure.

## Ce que cette phase ne fait PAS

- Aucun classement, aucun changement de publication : c'est 2D.
- Aucune taxonomie `event_type` (reste NULL, chantier à part).
- Aucune dépendance nouvelle, aucun appel LLM nouveau sans mesure préalable.
