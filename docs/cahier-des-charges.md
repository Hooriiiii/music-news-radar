# Cahier des charges — Kinda News France, v2

> **Version 2, 2026-11-12.** Remplace la v1 du même jour, qui proposait une
> refonte complète en 9 lots dans un nouveau paquet. La v1 a été relue par dix
> critiques à profils différents (architecte, SRE, product manager, rédacteur en
> chef, A&R, FinOps, juriste, data scientist, designer UX, développeur
> « avocat du diable ») ; leurs constats vérifiés sont intégrés ici, et les
> erreurs de la v1 sont listées en annexe.
>
> **Changement de cap :** on ne refait pas le système. On le **remet en route
> sous budget**, on **comble ce qui manque** (exactitude, droits, éditorial,
> audience), et on **assainit au fil de l'eau**, sans jamais affaiblir une
> protection existante.
>
> Les références de `docs/reference/` gardent les mesures qui justifient chaque
> seuil. Ce document ne les remplace pas : toute exigence qui touche un seuil
> renvoie à sa référence.

---

## 0. Urgent, avant tout le reste

| Quoi | Échéance | Pourquoi |
|---|---|---|
| ~~Étendre le jeton Meta de **collecte**~~ **Fait le 2026-11-12** | — | Remplacé par un jeton d'utilisateur système sans expiration, qui ne dépend plus du compte Facebook personnel (`docs/reference/publication.md`, « Deux jetons Meta ») |
| Décider si et quand le compte repart | — | Conditionne l'ordre des chantiers (§5) |

---

## 1. Contexte

### 1.1 Le produit

Kinda est un écosystème créatif pour artistes émergents (plugins, média, label,
événementiel, communauté). **Les plugins sont la priorité commerciale n°1
depuis le 2026-09-30** ; le média continue en parallèle. Ce dépôt porte la
brique média : le compte Instagram `@kindanewsfrance`, qui a deux rôles :

1. **Distribution** — un média d'actu musicale français, cœur électro +
   festivals, pop adjacente conditionnelle, Bretagne en bonus de départage.
2. **Détection** — le flux collecté et noté est le deal-flow du futur label
   (mémoire A&R).

Mode d'usage arrêté : **le robot publie seul ; l'humain vérifie de temps en
temps** (réordonner, retirer, retoucher une légende). Jamais de génération ni
de publication à la main.

### 1.2 État au 2026-11-12

- **Le code fonctionne et converge.** 3 036 tests verts (suite de 3 min 46) ;
  rythme de commits 42 / 74 / 268 / 84 / 5 de juillet à novembre : les derniers
  sont de petits correctifs sur un système stable.
- **Le compte ne publie plus.** Plus aucune note Claude depuis le **2026-10-07**
  (crédits épuisés, déjà manqués les 24/09 et 30/09), plus de publication
  après le 2026-10-08 ; l'utilisateur a **décidé le 2026-10-31** de le laisser à
  l'arrêt. ~1 340 articles attendent d'être notés (relevé du 2026-11-01).
- **Le vrai blocage est le coût, pas le code.** Estimation reconstituée depuis
  les logs du 18/09 au 08/10 (le journal d'usage Anthropic n'a jamais reçu de
  ligne payée, donc ce sont des ordres de grandeur, pas des mesures) :
  ~183 notes Claude par jour, ~3 posts publiés par jour, soit **~59 notes et
  ~2,3 compositions payées par post publié** ; **12 à 45 $ par mois**,
  0,13 à 0,50 $ par post, dont 50 à 75 % pour la notation.

### 1.3 Ce que la v1 avait mal lu, et qui fonde la v2

| Constat de la v1 | Ce qui est vrai |
|---|---|
| « Modules-dieux » à découper | Les grosses fonctions (`_publier`, `_geler`, `link_duplicates`) portent la réclamation conditionnelle, la reprise après panne et une dédup difficile ; leurs sous-étapes sont déjà des fonctions à part. Les découper à l'aveugle multiplie les frontières de transaction, donc les fenêtres de double publication |
| Config de 907 lignes à éclater | ~694 lignes sont des commentaires qui portent la **mesure collée au seuil** ; ~130 réglages réels. L'éclater séparerait seuil et mesure, ce que I14 interdit |
| L'événement est une couche à construire | `events`, `event_observations` et leurs features **existent et sont alimentés à chaque passage** (`app/pipeline/run.py`). Le problème : **rien dans `app/schedule/` ni `app/publish/` ne les lit** |
| « Aucun artiste suivi, fonction inerte » | Faux. `Artist.tracked` a été supprimée (migration 0021) car personne ne la lisait. « Suivi » = les 88 sources Instagram de catégorie artiste, et ils sont **exclus** de la liste courte (`docs/reference/memoire-ar.md`) |
| La dédup = hash → titres 48 h → arbitrage | Elle a aussi l'étage d'archive (10 jours, seuil 0,8), la porte sur le résumé, les gardes anti-gabarit et anti-récurrence, chacun payé par un incident (`docs/reference/dedup-et-retention.md`) |

**Conclusion :** une reconstruction réintroduirait un par un, en production,
les ~470 cas que les tests encodent, pour un bénéfice qui n'apparaîtrait qu'à
la fin. Les vrais manques sont ailleurs (§2).

---

## 2. Objectifs

### 2.1 Objectifs mesurables

| # | Objectif | Mesure | Cible |
|---|---|---|---|
| O1 | Le compte repart pour un coût maîtrisé | coût réel par jour et par post, depuis le journal d'usage | **≤ 40 $ / mois, cible 30 $** (D2, décidé le 2026-11-12) |
| O2 | Aucune erreur de fait publiée | erreurs signalées / posts publiés | 0 sur les sujets sensibles ; journal tenu |
| O3 | Aucun contenu publié sans droit défendable | posts avec un actif au statut « à vérifier » | 0 |
| O4 | Le compte grandit | abonnés par semaine, portée moyenne par post, par format | mesurées avant reprise, suivies chaque semaine |
| O5 | Le compte est français | part des posts avec un marqueur France | mesurée ; cible à fixer (relevé récent : 0 sur 15) |
| O6 | L'A&R fait remonter de vrais émergents | précision@20 de la liste courte, étiquetée à la main chaque semaine | mesurée ; cible à fixer après 4 semaines |
| O7 | Rien ne régresse | les 3 036 tests, sentinelles comprises | 100 % verts à chaque fusion |

### 2.2 Non-objectifs (écartés explicitement)

- Réécrire le système dans un nouveau paquet, ou supprimer `app/`.
- Imposer une taille maximale de fichier ou de fonction comme critère.
- Réécrire la console dans une autre technologie.
- Passer sur un serveur distant (IP de datacenter : Mixmag répond 403, Reddit
  429, yt-dlp exposé — `docs/reference/scheduler.md`).
- Réactiver X, le digest mail, Discord.
- Trois classements FEED / BREAKING / A&R et un journal de calibration à six
  types tant qu'aucune action ni aucun volume de décisions humaines ne les
  nourrit.

---

## 3. Invariants (non négociables)

Chacun doit être porté par un test nommé d'après lui. **Aucun chantier ne peut
affaiblir un mécanisme existant qui protège l'un d'eux.**

**Publication**

| # | Invariant |
|---|---|
| I1 | **Plutôt un post manqué qu'un post publié deux fois.** La réclamation (GELE → ECHEC, raison « publication interrompue avant confirmation », sujet exclu dans le **même commit**) est **terminale** et committée avant tout appel à Meta. Les seules sorties de la réclamation sont celles où l'on sait que rien n'est parti (hébergement tombé avant le conteneur, mode `arret`, retard). Les transitions s'écrivent par UPDATE conditionnel, **sauf** l'écriture qui suit la réponse de Meta (`enregistrer` / `echec`), qui doit réussir même si un geste de la console a touché la ligne. |
| I2 | **Une panne machine n'exclut jamais un sujet.** Seule une faute du sujet l'exclut, et l'exclusion porte sur le **sujet**, pas sur le créneau, qui est libéré. En cas de doute : différer ou sauter. |
| I3 | **Le mode `reel` est une décision humaine.** Un mode absent ou illisible vaut `arret`, jamais un repli sur `.env`. |
| I4 | **La relecture est une option** pour l'ordinaire — sauf pour les sujets sensibles (I16). |
| I5 | Pas de rattrapage en rafale : au-delà de 90 min de retard, le créneau est MANQUÉ. |

**Contenu**

| # | Invariant |
|---|---|
| I6 | L'info d'un compte média, oui ; ses visuels, jamais. |
| I7 | Jamais un post qui ne montre rien de l'actu ; jamais d'affiche de line-up en fond de cover ; une seule affiche en slide. |
| I8 | Crédit `📸 @compte` / `🎥 @compte` obligatoire, posé par le code, jamais coupé ; le média source n'est pas nommé. |
| I9 | Un défaut de forme relance puis accepte avec avertissement. |
| I15 | **Aucun nom, date ou lieu de la cover ou de la légende qui ne soit retrouvé dans une observation du sujet.** Sinon, on diffère. |
| I16 | **Un sujet sensible** (décès, annulation, accusation, santé, conflit) ne part qu'avec deux sources indépendantes ou une source primaire, **ou** après validation humaine. Sinon il ne part pas. |
| I17 | **Aucun actif au statut « à vérifier » n'est publié.** Aucune piste son d'un tiers republiée sans droit. |

**Signal et notation**

| # | Invariant |
|---|---|
| I10 | Une valeur non lue s'affiche INCONNUE, jamais verte, jamais « 0 ». |
| I11 | `relevance` et `emergence` ne se compensent jamais ; aucun quota de mix dans `relevance`. **Un seuil de `relevance` ne dégrade jamais l'axe `emergence`.** |
| I12 | Ne jamais payer deux fois la même chose. |
| I13 | Tout seuil est accompagné de sa mesure ; aucun seuil ne change à l'œil. |

**Données**

| # | Invariant |
|---|---|
| I14 | `posts`, articles `USED`, `artists`, `artist_mentions` ne sont jamais purgés — **sauf demande d'opposition ou d'effacement d'une personne** (C4), qui l'emporte. |
| I18 | Une décision humaine prime toujours sur l'automatisation. |

---

## 4. Chantiers

Chaque chantier est livrable seul, sur `app/` tel qu'il est, avec ses tests.
Chaque exigence dit comment on la vérifie.

### C1 — Reprise sous budget

Le compte doit pouvoir repartir pour un coût connu, et s'arrêter proprement au
plafond.

- **C1-1 Journal d'usage rempli et lu.** Chaque appel Anthropic est consigné
  (poste, modèle, jetons, coût). Vérif : après une journée de reprise,
  `scripts.diagnostic.consommation_api` affiche des coûts non INCONNUS par poste.
- **C1-2 Ne noter que les candidats plausibles.** Un pré-classement en code
  (fraîcheur, nombre de sources, catégorie de source, artiste cité) choisit les
  K meilleurs par passage ; le reste attend ou expire. Un doublon n'est jamais
  noté. Vérif : nombre de notes par jour mesuré avant / après ; cible ≤ 60 / jour.
- **C1-3 Cache de prompt effectif.** Mesurer le prompt de notation avec
  `count_tokens` (gratuit) contre le minimum de cache du modèle ; poser
  `cache_control` sur la composition. Vérif : un test exige
  `cache_read_input_tokens > 0` sur le deuxième appel.
- **C1-4 Batch API** pour ce qui n'est pas pressé (notation horaire, composition
  à J-1). Seule la veille rapide reste synchrone.
- **C1-5 Choix du modèle de notation par la mesure.** Premier geste de la
  reprise : `scripts.diagnostic.mesurer_modele_notation --apply`, agents
  arrêtés (`docs/reference/scoring.md`). La mesure tranche entre Haiku 4.5 et
  5.5 sur la qualité **et** le coût, tarifs vérifiés le jour même.
- **C1-6 Plafond en dollars.** Estimé avant l'appel (depuis `max_tokens`),
  par jour, avec des enveloppes par poste et une priorité : composition des
  créneaux déjà attribués > notation du frais > arriéré. Les relances de
  composition comptent. Notification macOS à 50 % et 100 %. **Plafond dur :
  40 $ sur le mois calendaire** (D2) ; au plafond, la chaîne notation →
  publication se met en pause (C1-8) jusqu'au mois suivant, elle ne dépasse
  jamais.
- **C1-7 Reprise de l'arriéré bornée.** Du plus frais au plus ancien ; rien
  au-delà de 48 h n'est noté pour le FEED ; plafond en nombre et en dollars.
- **C1-8 Mode sans IA visible.** Sans crédits : collecte, regroupement,
  extraction d'artistes (C8-1) et console continuent ; la chaîne notation →
  publication est en pause et la supervision le dit, avec la date du dernier
  succès.

**Critère d'acceptation :** une semaine en mode `apercu` à coût mesuré,
inférieur au budget fixé, sans aucun voyant faux.

### C2 — Ménage

- Supprimer `app/delivery/digest.py`, `discord.py`, `email.py`, la table
  `digests` (migration), `app/sources/x_*.py`, `app/pipeline/radar.py`
  (classement par volume, pilotait la recherche X), `scripts/livraison/`.
- Archiver les scripts de `scripts/reparations/` déjà passés.
- Aligner `AGENTS.md`, `pyproject.toml` (description) et
  `docs/contexte-produit.md` (§15 faux, repositionnement du 2026-09-30, plugins
  prioritaires) sur `CLAUDE.md`.
- Ranger les captures et PDF de la racine.
- Corriger la docstring de `app/content/recherche_web.py`, qui contredit la
  pratique actuelle.

**Critère :** suite verte ; `grep` ne trouve plus aucune référence au code
supprimé ; les trois documents disent la même chose du produit.

### C3 — Exactitude des faits

Aujourd'hui, la notation **remplace** le texte source par un résumé d'une ou
deux phrases (`app/pipeline/scoring.py`, `apply_score`), et la composition en
tire 2 à 4 paragraphes. Rien ne confronte la légende aux sources.

- **C3-1** Conserver le texte brut des observations à côté du résumé ; la
  composition lit le texte brut de **toutes** les observations du sujet.
- **C3-2** Contrôle en code (I15) : chaque nom propre, date et lieu de la
  cover et de la légende est retrouvé dans au moins une observation. Sinon,
  relance une fois puis diffère — c'est un défaut de **fond**, pas de forme.
- **C3-3** Classe « sensible » (I16) détectée à la notation ; validation
  humaine ou double source exigée.
- **C3-4** Procédure d'erratum : comment on corrige ou retire un post publié,
  sous quel délai, journalisé.

**Critère :** sur 20 posts composés en `apercu`, zéro fait introuvable dans les
sources ; un jeu de cas pièges (date de sortie passée, annulation, homonyme)
est bloqué.

### C4 — Droits et conformité

À trancher par l'utilisateur au nom de l'entité Kinda (§6, D3). Exigences
minimales proposées :

- **C4-1 Trois statuts de droits par actif**, chacun avec sa preuve (origine,
  date, message) : `publiable`, `à vérifier`, `bloqué`. Matrice écrite :
  - compte officiel de l'artiste / festival / label **sujet du post** →
    publiable avec crédit ;
  - compte média → bloqué (I6, déjà en place via `visuels_autorises` /
    `urls_interdites`) ;
  - image trouvée sur le web → à vérifier (I17), sauf liste blanche de sources
    sous licence (kits presse, Wikimedia avec attribution) ;
  - test-sentinelle qui impose la matrice.
- **C4-2 yt-dlp.** Il sert aujourd'hui à récupérer, avec leur son, des vidéos
  que Meta ne fournit pas à cause de musique protégée (`app/publish/ytdlp.py`).
  Décision : le supprimer, ou ne garder que l'image (son coupé ou son sous
  licence). Ne jamais republier tel quel ce que Meta a refusé de servir.
- **C4-3 Retrait.** Un contact de réclamation affiché en bio ; un geste console
  « retirer un post publié » (vérifier ce que l'API Graph permet) ; l'actif et
  le compte passent en `bloqué` ; délai cible et journal.
- **C4-4 RGPD de la mémoire A&R** (3 838 artistes, personnes physiques
  identifiables par leur nom de scène, profilage, conservation indéfinie) :
  base légale écrite (intérêt légitime, avec la mise en balance), registre de
  traitement, procédure d'opposition et d'effacement qui l'emporte sur I14,
  région d'hébergement Neon vérifiée.
- **C4-5 Mentions minimales** : éditeur et contact en bio ; mention
  publicitaire dès la première monétisation ; transparence sur la rédaction
  par IA à vérifier.

**Critère :** zéro actif `à vérifier` dans les posts publiés ; procédure de
retrait testée une fois de bout en bout en `apercu`.

> Ce chapitre n'est pas un avis juridique. Les points marqués « à vérifier »
> le sont réellement ; un conseil qualifié est recommandé avant la
> monétisation.

### C5 — Article ou événement : choisir

Aujourd'hui, deux modèles sont alimentés et un seul est lu. Il faut en garder
un (décision D1, §6).

- **Option A (recommandée) :** brancher la programmation sur l'événement
  **sans réécrire la dédup**. Ajouter `creneaux.event_id` ; l'article
  représentant reste le porteur de contenu ; la composition lit les
  observations de l'événement (ce qui sert C3-1) ; l'anti-republication
  (F-55 de la v1) contrôle l'article **et** l'événement.
- **Option B :** arrêter d'alimenter `events` et `event_observations`, et
  retirer le code correspondant.

Exigences communes :
- migration Alembic numérotée après 0022, remplissage idempotent
  (plan → `--apply`), comptages avant / après ;
- les posts publiés, articles `USED` et ECHEC incertains reçoivent leur
  événement **avant** toute réouverture de la file, sinon l'historique publié
  devient republiable (I1) ;
- garder `purger_evenements_sans_valeur` et le sursis de 7 jours des articles
  jamais jugés, ré-exprimés sur l'événement.

**Critère :** test « un sujet publié via l'article A ne peut pas repartir via
l'article B du même événement ».

### C6 — Éditorial et audience

- **C6-1 Mesurer l'audience.** Remonter les Insights Meta par post (portée,
  enregistrements, partages, abonnés gagnés) — la permission
  `instagram_manage_insights` est déjà accordée. Mesurer **avant** la reprise
  pour avoir une base.
- **C6-2 Grille éditoriale écrite.** Nombre de posts par format et par jour
  (aujourd'hui 3 carrousels + 3 reels, figés dans la config sans
  justification), part visée par catégorie (festival, sortie, émergent, pop),
  règle de non-répétition (pas deux posts de suite sur le même artiste ou
  festival). Appliquée dans la **programmation**, jamais dans `relevance`
  (I11). Revue chaque mois contre C6-1.
- **C6-3 Charte rédactionnelle versionnée** : registre, tutoiement ou non,
  longueur, interdits, dix exemples réels de bons et de mauvais posts.
  Aligner le prompt de composition dessus (il parle encore de pop sans
  condition, et de « variantes parmi lesquelles l'utilisateur choisira » alors
  que personne ne choisit).
- **C6-4 Un format pour les émergents.** Aujourd'hui `emergence` ne débouche
  sur aucun post : le FEED ne voit que `relevance ≥ 60`. Exemple : un post
  hebdomadaire de 3 à 5 émergents FR, sur créneau réservé, avec ses propres
  critères.
- **C6-4 bis Maquettes de référence.** Le rendu des posts se juge contre
  les deux maquettes de la direction artistique, versionnées dans le dépôt.
  Elles fixent le logo Kinda en haut à droite, l'accroche en bas (cover de
  post) ou au centre (cover de reel), et le surlignage rose de la partie
  mise en avant.

  | Cover de post, 1080 × 1350 | Cover de reel, 1080 × 1920 |
  |---|---|
  | ![Maquette cover de post](../exemples/%5BPREVIEW%5D%20-%20Post%20information.png) | ![Maquette cover de reel](../exemples/%5BPREVIEW%5D%20-%20Cover%20Reels.png) |

  Toute évolution de la charte (C6-3) part d'une nouvelle maquette de la DA,
  ajoutée dans `exemples/`, jamais d'un réglage de gabarit fait à l'œil
  (I13).
- **C6-5 Angle France** : règles écrites pour « franciser » une actu
  internationale (date française, artiste FR au line-up).
- **C6-6 BREAKING : décision D4.** Soit un vrai circuit rapide (créneau hors
  grille, latence annonce → publication chiffrée, par exemple < 45 min, sur
  une machine éveillée), soit on renonce et on l'écrit. **Mesurer d'abord la
  latence actuelle.**
- **C6-7 Promotion des plugins : décision D5.** Si oui, un type de créneau
  « Kinda » réservé.

**Critère :** grille et charte écrites et appliquées ; tableau hebdomadaire
format × catégorie × performance disponible.

### C7 — Console : partir des parcours

La console existe et vient d'être retravaillée. On la fait évoluer par
parcours, sans changer de technologie : sortir le JS de `dashboard.html` en
modules ES sans étape de build, écran par écran, en portant les tests Chromium
existants.

- **Parcours A — Coup d'œil** (le plus fréquent, 30 s, souvent sur
  téléphone) : à l'ouverture, une phrase-verdict (« tout part », « N posts
  attendent un avis », « rien ne part : crédits / agent / mode », ou INCONNU),
  puis les 3 prochains départs avec vignette, heure et état. Zéro clic si tout
  va bien. Quand rien ne peut partir, ce verdict remplace le planning.
- **Parcours B — Corriger avant le départ** : la notification nomme le
  créneau et l'heure et mène à sa fiche, avec l'aperçu au format Instagram
  (carrousel, légende coupée à « … plus »). Trois sorties : modifier la
  légende, échanger ou retirer, ne rien faire. Chaque geste s'annule sur place
  jusqu'à 20 min avant le départ. ≤ 3 clics depuis la notification.
- **Parcours C — Retour après une absence** : un fil « depuis ta dernière
  visite » : ce qui est parti (avec le lien), ce qui a sauté et pourquoi
  (panne ou machine éteinte), ce que le robot a remplacé. Chaque ligne rouge
  porte une seule action, ou dit « rien à faire ».
- **Téléphone : décision D6.** Accès distant authentifié, ou pas de mobile,
  assumé par écrit. Si oui : écran d'arrivée et fiche lisibles à 390 px.
- **Notifications** : anti-rebond (un rouge qui dure N passages, une seule
  notification par incident).
- **Simplifications** : modes de publication et sources vers Réglages ; le
  flux d'actus et son tri « vu / à traiter » retirés (inutiles en mode
  automatique) ; l'événement et ses observations affichés dans la fiche du
  créneau (« pourquoi ce sujet »), pas dans un onglet ; le détail sur la
  fenêtre où les posts existent (aujourd'hui, demain), un simple compte
  au-delà.

**Critère :** les trois parcours passent en test Chromium.

### C8 — A&R

- **C8-1 Extraire les artistes sans dépendre de la notation.** Aujourd'hui,
  une citation n'existe que si la notation payante a tourné : sans crédits,
  la mémoire A&R ne reçoit rien, et l'arriéré expiré ne produira jamais de
  citation. L'extraction d'entités passe par le modèle local (ou un appel
  bon marché), **sans fenêtre de fraîcheur**.
- **C8-2 Ne pas laisser le pré-filtre étouffer l'émergence.** Les articles
  écartés sur `relevance` gardent l'`emergence` du modèle local, alors que
  c'est le cas typique (« premier EP, relevance 20, emergence 75 »). Envoyer
  aussi à Claude, sous plafond, ceux dont l'émergence locale ≥ 50 (seuil à
  mesurer, I13).
- **C8-3 Identité des artistes.** Table d'alias, fusion et séparation
  journalisées, identifiant externe (MusicBrainz ou Spotify) dès qu'il est
  connu. Doublons constatés : Boys Noize, oklou, Cwtch.
- **C8-4 Garder les acquis du classement** : émergence moyenne des citations
  principales, `CITATIONS_MIN_DEFAUT = 3`, `est_citation_principale`, exclusion
  des comptes suivis ; sentinelle « aucune star dans le top 15 ».
- **C8-5 Indicateurs** : compter les sources par **groupe** ; rétablir
  `country`, `city`, `quality_of_bookings`, `release_frequency`
  (`contexte-produit.md` §29) ; filtre France. Pas de z-score sur soi-même ni
  de « qualité des cosignatures » sans données ni référentiel mesuré.
- **C8-6 Mesurer la réussite** : une photo de la liste courte chaque semaine,
  jamais purgée ; chaque nom étiqueté à la main (vrai émergent, déjà installé,
  bruit, pas un artiste) ; précision@20 et part de « déjà signés ».
- **C8-7 Cycle de vie A&R** : repéré → écouté → contacté → écarté → signé,
  avec la raison. C'est la seule vérité terrain possible.
- **Biais à écrire noir sur blanc** : Business Discovery ne voit pas les
  comptes personnels, ceux des vrais débutants ; tout le reste passe par un
  média, un festival ou un label, donc par quelqu'un de déjà repéré. Piste :
  une entrée directe (formulaire de démo, adresse dédiée).

**Critère :** quatre semaines de listes étiquetées ; précision@20 publiée.

### C9 — Assainissement au fil de l'eau

Règles, pas un lot :

- **On ne découpe un module que quand une fonctionnalité le touche**, tests
  de protection en place avant le découpage. On juge la complexité, pas le
  nombre de lignes.
- **État partagé : en base, une table typée à la fois**, en commençant par
  ceux que plusieurs agents écrivent (`publication*.json`, `quota_graph.json`).
  Pas de table clé / valeur générique, qui recréerait les pertes d'écriture de
  la relecture-fusion. Écritures par mise à jour atomique ou colonne de
  version ; `lock_timeout` et `statement_timeout` posés ; aucun verrou tenu
  pendant un appel réseau. Restent hors de Neon : le battement de cœur des
  agents et la trace « base injoignable », sinon la console ne distingue plus
  « agent mort » de « base morte ». Les caches de résultats payants
  (`cluster_arbitrage.json`, `illustrations*.json`, `visuels_web.json`) sont
  conservés et migrés, jamais jetés.
- **Tests sur Postgres jetable** pour tout ce qui touche à I1 (concurrence,
  injection de panne à chaque point de coupure), à côté de la suite SQLite.
- **Un seul publieur** : verrou consultatif Postgres, et un test qui rougit si
  deux agents de publication sont chargés (piège du label launchd déjà payé,
  `docs/reference/scheduler.md`).
- **Transactions longues** : le gel et la composition tiennent aujourd'hui une
  transaction pendant des minutes (`docs/reference/contraintes-terrain.md`) ;
  à corriger dès qu'on y touche.
- **Réconciliation avec Meta** : quand l'issue d'une publication est
  incertaine, lire les médias récents du compte plutôt que d'exclure à
  l'aveugle.

---

## 5. Ordre proposé

```text
0. Jeton de collecte (avant le 16/11)
1. C2 Ménage                         — sans risque, rend le reste plus lisible
2. C1 Reprise sous budget            — lève le vrai blocage
   C8-1 Extraction d'artistes        — en même temps : la mémoire A&R repart sans crédits
3. C3 Exactitude + C4 Droits         — avant toute publication réelle
4. C5 Article / événement            — sert C3-1 (lire toutes les observations)
5. C6-1 Mesure d'audience            — avant la reprise, pour avoir une base
   → Reprise en mode réel (décision de l'utilisateur, confirmation « PUBLIER »)
6. C6 Éditorial, C7 Console, C8 A&R  — par incréments, chacun livrable seul
C9 en continu.
```

Rien ne publie en réel avant la fin de l'étape 5. Chaque étape se termine par
une suite verte et la référence de domaine mise à jour.

---

## 6. Décisions à trancher

| # | Question | Recommandation |
|---|---|---|
| D0 | Combien de temps accorder au média, vu la priorité plugins ? | Un plafond écrit (par exemple 1 jour par semaine), et un critère d'abandon |
| D1 | Article ou événement (C5) ? | Option A, sans réécrire la dédup |
| D2 | Budget mensuel IA ? | **Décidé le 2026-11-12 : 40 $ / mois au maximum, cible 30 $** (≈ 1 $ / jour en moyenne). Source : 100 $ d'API par mois via Claude Max, partagés avec d'autres projets. Les mesures de non-régression comptent dans ce plafond. Répartition indicative : composition 60 %, notation 30 %, divers 10 %, à corriger après la première semaine mesurée |
| D3 | Niveau de risque juridique accepté, au nom de Kinda (C4) ? | Les exigences minimales de C4 ; yt-dlp supprimé ou réduit à l'image |
| D4 | BREAKING réel ou abandonné (C6-6) ? | Mesurer la latence actuelle, puis décider |
| D5 | Le compte promeut-il les plugins (C6-7) ? | Oui, un créneau réservé, dès que le premier plugin existe |
| D6 | Console accessible depuis le téléphone (C7) ? | Oui, si un accès authentifié simple est possible ; sinon l'écrire |
| D7 | Part de pop visée (~10-15 %) dans la grille (C6-2) ? | Oui, dans la programmation, jamais dans `relevance` |

---

## 7. Critères de succès globaux

1. Le compte publie à nouveau, à un coût mensuel mesuré inférieur au budget.
2. Aucune double publication ; les tests de concurrence et d'injection de
   panne passent sur Postgres.
3. Aucun fait introuvable dans les sources et aucun actif « à vérifier » dans
   un post publié.
4. Sans crédits, la collecte, la mémoire A&R et la console tournent, et la
   supervision dit pourquoi rien ne part.
5. Abonnés, portée et part France sont mesurés chaque semaine et pilotent la
   grille.
6. La liste courte A&R est étiquetée chaque semaine et sa précision est
   connue.
7. `CLAUDE.md`, `AGENTS.md`, `pyproject.toml` et `contexte-produit.md` disent
   la même chose.
8. Aucune protection existante n'a été retirée (les 3 036 tests passent,
   sentinelles comprises).

---

## Annexe A — Erreurs de la v1, corrigées ici

| v1 | Correction |
|---|---|
| Compte « à l'arrêt depuis le 31/10 » | Décision le 31/10 ; plus de notation depuis le 07/10 |
| 4 agents launchd | 5 : `pipeline`, `dashboard`, `publication`, `veille`, `prefiltre` (30 min) |
| ~13 fichiers JSON d'état | 17 `.json` dans `~/Library/Application Support/Kinda/` (16 d'état + le jeton), plus `publication/` et `visuels/` |
| `AGENTS.md` dit « ne les déprioris JAMAIS » | Il dit « gardent leurs scores hauts » (l. 20-22) ; le fond, une pop sans condition, reste à corriger |
| « Aucun artiste suivi, fonction inerte » (F-63) | `tracked` supprimée ; « suivi » = 88 comptes artistes, exclus de la liste courte |
| Machine à états LIBRE → ATTRIBUÉ → COMPOSÉ → GELÉ → RÉCLAMÉ → PUBLIÉ | États réels : LIBRE, PROGRAMME, GELE, PUBLIE, APERCU, ECHEC, MANQUE ; la réclamation est terminale (I1) ; DIFFÉRÉ et SAUTÉ sont des actions, pas des états ; l'exclusion porte sur le sujet |
| « Reprise sans perte après sommeil » | « Sans doublon ni rafale » : au-delà de 90 min, MANQUÉ (I5) |
| Dédup à trois étages | Plus l'archive 10 jours, la porte sur le résumé, les gardes anti-gabarit et anti-récurrence |
| Vélocité « sur 1 h et 6 h » | Taux (n − 1) / étalement avec plancher d'une heure (`event_features.py`) ; 825 événements sur 901 ont moins de 3 observations, donc vélocité presque toujours absente |
| Tous les événements permanents | `purger_evenements_sans_valeur` existe pour ne pas garder le bruit |
| Config de 907 lignes à éclater | ~694 lignes de commentaires-mesures ; ~130 réglages |
| Critères « même regroupement », « même distribution », « rendu au pixel près » | Non rejouables ou figeant les défauts ; remplacés par des jeux étiquetés (annexe B) |

## Annexe B — Protocole d'évaluation (pour C1-5, C5, C6-2, C8-6)

- **Instantanés gelés** : exporter les observations brutes avant la purge
  (au moins 6 fenêtres de 48 h, semaine et week-end), versionnés dans
  `scripts/diagnostic/`.
- **Regroupement** : ~300 paires stratifiées par bande de similarité, langue
  et plateforme, plus les 50 paires jugées le 2026-09-24 ; double annotation
  sur 20 % (κ ≥ 0,7) ; Claude sert au tri, jamais à fixer la vérité. Zéro faux
  positif sur les pièges connus (Charlotte de Witte, Fred again..,
  Transmusicales, Countdown, AIM UK).
- **Notation** : ~150 événements avec entrées brutes conservées, dont les 53
  de la mesure pop ; étiquette « je publierais oui / non » ; tolérance égale
  au bruit test-retest du modèle ; le gradient pop doit tenir (K-pop routinière
  ≈ −19, témoin électro ≈ 0).
- **File de publication** : la formule actuelle
  (`relevance + 0,25 × hotness − 15 / jour`, `app/schedule/queue.py`) est la
  référence à battre en rejeu ; latence premier signal → créneau ;
  redondance dans le top 9.
- **Pas de « précision » contre l'humain** tant qu'il intervient rarement :
  publier un taux d'intervention avec son intervalle de confiance, et
  journaliser l'exposition (ce qui a été montré, à quel rang, avec quelles
  notes et quelle version de prompt et de modèle).
