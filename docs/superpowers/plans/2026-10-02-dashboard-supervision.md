# Refonte du dashboard — superviser d'abord, réparer ensuite

Demande de l'utilisateur le 2026-10-02 : « tu peux faire une grosse maj UX/UI sur
l'interface ». Audit mené avec le skill `ux-audit`, **couches L1 (code) et L2
(captures)**. L3 (parcours instrumenté) non jouée.

## La conclusion qui gouverne ce plan

> C'est une console pour un robot qui publie six fois par jour, et elle ne sait
> **rien** dire du robot.

L'audit de CLASSE (mené avant de lire notre code, comme l'exige la méthode) a
produit une grille de capacités attendues d'une console d'opérations
éditoriales. Le groupe **supervision** y est vide sur **toutes** ses lignes :
santé des sources, échéance des jetons, état de quota, historique des passages,
coût dépensé, relance d'un état terminal, vue de ce qui a réellement été publié.

Et ce n'est pas théorique. Tous les incidents de la semaine du 2026-09-26 au
2026-10-02 étaient **invisibles sur cet écran** :

| incident | ce que le dashboard en disait |
|---|---|
| crédits Anthropic épuisés deux fois (plus aucune composition) | rien |
| 46 % des créneaux manqués, 25 sur 37 sans raison | rien |
| 751 erreurs de quota Graph, 80 % des comptes plus lus | rien |
| une actu déjà publiée sur le point de repartir (« DJ Mag en boucle ») | rien — **c'est l'utilisateur qui l'a vu** |

C'est la cause première : **l'utilisateur découvre les pannes par hasard.**

## Ce qu'on ne casse pas

L'audit a relevé **quatorze comportements justes** dans la fiche de post, chacun
porteur d'un raisonnement rare. Ils sont la liste « ne régresse pas ça » :

- un contrôle désactivé imprime **toujours sa raison**, adjacente et visible ;
- un `null` s'affiche « ? », **jamais 0** (sans quoi la phase 2C se lit à l'envers) ;
- un interrupteur dont l'état n'a pas pu être lu affiche **inconnu**, jamais « éteint » ;
- un geste qui fait **deux écritures** rapporte laquelle a échoué ;
- le basculement irréversible est confirmé **côté serveur**, pas seulement dans le navigateur ;
- un créneau réclamé s'affiche « Publication en cours », pas « échec » ;
- un article purgé ne casse jamais l'éditeur ;
- une URL de vidéo ne devient jamais une `<img>` cassée ;
- quatre covers pour **un seul** lancement de Chromium ;
- un texte qui déborderait est **signalé avant** publication.

## Global Constraints

- **C'est l'outil de travail QUOTIDIEN de l'utilisateur.** Une régression se paie
  tout de suite. À chaque tâche, vérifier le rendu réel dans un navigateur, pas
  seulement les tests.
- **Aucune conclusion visuelle depuis le code.** La méthode l'interdit : la
  hiérarchie, l'espacement, la troncature et le responsive se constatent sur un
  rendu. Chaque tâche qui touche à l'apparence doit produire une capture.
- **Rien ne change dans le comportement de PUBLICATION.** Le dashboard observe et
  commande ; il ne décide pas à la place du pipeline.
- **La supervision ne doit RIEN coûter.** Pas d'appel payé, pas d'appel réseau
  vers Meta ou Anthropic à chaque affichage : la santé se lit dans ce qui est
  déjà écrit (`sources.state`, les fichiers d'état à côté du jeton, les logs).
- **`autoflush=False`** est le réglage de production ; `flush()` explicite si on
  écrit puis relit. Ce piège a mordu ce dépôt trois fois.
- Commits en français, messages **sans accents**, terminés par
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Task 1 — Le bandeau de supervision

**La tâche principale**, et celle qui règle la cause première.

Un bandeau permanent en tête de page, visible quel que soit le panneau, qui
répond à une seule question : **« la machine tourne-t-elle, et qu'a-t-elle fait
en mon nom ? »**

Ce qu'il doit porter, par ordre de gravité constatée cette semaine :

1. **Les crédits / la dernière composition.** Deux pannes de crédits en une
   semaine, invisibles. Une composition qui échoue est déjà consignée ; le
   signal existe, personne ne l'affiche.
2. **L'échéance des deux jetons Meta.** `META_FB_ACCESS_TOKEN` se renouvelle **à
   la main** tous les 60 jours ; l'alerte existe en notification macOS, nulle
   part à l'écran.
3. **La santé des sources.** `sources.state.last_error` est écrit à chaque
   ingestion et n'est **pas même exposé par l'API** (`SourceOut` ne porte que
   nom/type/url/genre/catégorie/actif). À ajouter côté schéma.
4. **L'état de quota et les pauses** : `quota_graph.json`, `ytdlp_etat.json`,
   `visuel_saute.json` — tous à côté du jeton, tous lisibles sans coût.
5. **Le dernier passage** : quand, combien d'articles, combien de sources en
   erreur.
6. **Les créneaux manqués du jour**, agrégés — aujourd'hui noyés dans une liste
   chronologique.

Règles de conception, tirées des forces à préserver : une valeur qu'on n'a pas
pu lire s'affiche **inconnue**, jamais « tout va bien » ; chaque voyant porte sa
**raison** en clair ; aucun appel payé.

## Task 2 — Les quatre défauts qui rompent la confiance

1. **Une légende tapée est détruite** par six gestes de la même fiche (choisir
   une variante, changer de format, valider un fond, cocher un média, approuver,
   marquer publié) et à la fermeture. Rien n'est persisté. *Remède : ne plus
   reconstruire toute la fiche, ou réinjecter le tampon ; et une garde à la
   fermeture.*
2. **La grille échoue en « Chargement… » permanent.** La boucle de pagination
   n'a aucun rattrapage, et l'amorçage non plus : une base lente rend l'écran
   d'accueil indistinguable d'un chargement. *Remède : état d'erreur terminal
   avec une **relance**, comme les trois autres panneaux — qui n'en ont pas non
   plus.*
3. **Changer la catégorie d'une source est une écriture muette** : la réponse
   n'est jamais lue. Or ce champ pilote les règles 5-6 du scoring, l'éligibilité
   reel et la règle « jamais les visuels d'un compte média ».
4. **« 🚫 Sauter » est irréversible, sans confirmation, et rien ne lève
   l'exclusion** — aucun endpoint ne remet `file_exclu_raison` à NULL, et le
   champ n'est même pas exposé. Pendant que supprimer un brouillon, lui, demande
   confirmation. *Remède : confirmation, exposition du champ, pastille sur la
   carte, et une action « remettre dans la file ».*

## Task 3 — Ce qui ne s'affiche pas

Constats de la couche **visuelle**, non négociables car constatés à l'écran :

- **Le panneau Événements est illisible** : `.event-head` est un flex où le titre
  a `flex: 1` (base 0) pendant que les sept badges revendiquent leur largeur
  intrinsèque — le titre est écrasé à zéro, les métadonnées tombent **un mot par
  ligne**. En fenêtre étroite le titre **disparaît**. Le remède est une
  **composition** (le titre sa ligne, les métriques la leur), pas une rustine.
- **Le responsive n'existe pas** : aucune media query, et à 820 px la barre
  éclate en quatre rangées.
- **Des affordances sans libellé** : 📌/📍, ✏️, ⏬, 🚫 sur les créneaux, ↩ sur
  les cartes.
- **Trois textes mentent** : « toutes les 2 h » (le passage est **horaire**),
  « 14 jours de rétention » (elle est à **2** depuis le 2026-09-21), et une
  explication du fond Kinda qui décrit un comportement **retiré**. À dériver de
  la config plutôt qu'à écrire en dur.

## Task 4 — Navigation, filtres, et liens entre panneaux

- **Navigation et filtrage partagent une classe, une rangée, un style.** Ce n'est
  **pas** un cas de suppression : les chips de panneau ont une portée
  *application*, les filtres une portée *grille*. La méthode est formelle — la
  bonne réponse est de l'exprimer en **composition** (une rangée de navigation
  distincte et titrée), jamais en fusionnant. Ça clôt du même coup le défaut où
  changer un tri pendant qu'un panneau est ouvert **réécrit le compteur** et
  relance une pagination inutile.
- **Les panneaux ne se parlent pas** : `article #123`, `post 45`, `IG 17894…`
  sont du texte inerte. Chaque renvoi est un copier-coller, sur un outil
  quotidien. Les rendre cliquables.
- **La ligne de créneau ne hiérarchise pas** : « (créneau libre) » occupe la
  position de titre sur six lignes sur dix, pendant que la raison d'un échec est
  tassée en gris.

## Ce que ce plan ne fait PAS

- Pas de vue calendrier, pas de glisser-déposer, pas de métriques Instagram en
  retour : hors périmètre, à mesurer d'abord.
- Pas de panneau Artistes (l'axe A&R est exposé par l'API et absent de l'UI —
  vrai manque, mais un chantier à part).
- Aucun changement du pipeline ni de la publication.
