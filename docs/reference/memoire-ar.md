# Memoire A&R : tables `artists` / `artist_mentions`

**Quand lire ce fichier** : avant de toucher a `app/pipeline/artists.py`, a
`rising_artists`, a l'attribution d'une note d'emergence, ou avant de construire
quoi que ce soit qui CLASSE des artistes. Il contient la demonstration que le
signal etait FAUX (un panneau A&R qui proposait de signer Taylor Swift) et les
trois correctifs qui le reparent.

La restitution a l'ecran est dans `dashboard.md`, section « Le panneau
Artistes ».

## Mémoire A&R : tables `artists` / `artist_mentions`

Les articles sont purgés à 2 jours ; **les artistes et leurs citations, jamais**.
Ce qui intéresse un label n'est pas « qui est cité aujourd'hui » mais « depuis quand,
à quelle fréquence, est-ce que ça accélère » — cette courbe est l'actif de Kinda.
`app/pipeline/artists.py` : `record_mentions()` (idempotent, tourne après le scoring
dans `run_pipeline`) et `rising_artists()` (shortlist A&R). Les mentions sont
DÉNORMALISÉES (titre, url, scores) et le FK vers l'article est `ON DELETE SET NULL` :
la purge casse le lien, jamais l'historique. API : `GET /artists`,
`GET /artists/{id}/mentions`, `PATCH /artists/{id}` (le lien vers le compte
Instagram suivi, et rien d'autre depuis la migration 0021 — voir plus bas).
**Panneau Artistes du dashboard** (2026-10-04) : la restitution, voir « Le
dashboard » plus bas.

**Le signal était FAUX, et silencieusement** (réparé le 2026-10-03). `rising_artists`
rendait Stray Kids, aespa, Charli XCX, BTS, **Taylor Swift**, Ed Sheeran, Madonna :
un panneau A&R construit là-dessus proposait de signer Taylor Swift. Deux défauts
distincts, de tailles très inégales — tout est mesuré dans l'en-tête de
`app/pipeline/artists.py`, qui est la référence.

1. **Le CLASSEMENT faisait tout le dégât.** Le tri était le VOLUME de citations,
   ce qui remonte mécaniquement les artistes les plus couverts — l'inverse de « on
   commence à en parler » —, et le départage `max_emergence`, un CLIQUET qu'une
   seule note de parrainage remplit à vie (250 artistes à 60 ou plus, dont 15 avec
   une moyenne sous 20 : BTS, 24 citations, max 75, **moyenne 3,1**). On classe
   désormais sur l'**émergence MOYENNE des citations principales de la fenêtre**,
   puis sur l'accélération, puis sur le volume. `rising_artists` rend un
   `SignalArtiste` (citations, citations principales, moyenne, `velocity` et
   `acceleration` empruntées à `app/pipeline/event_features.py` — les formules déjà
   calibrées de la couche événements, rien n'est réinventé) plutôt qu'un `Artist`
   nu : un panneau de tri ne peut pas reconstituer ce qu'un score composite a
   écrasé. Plancher `CITATIONS_MIN_DEFAUT` = **3** : sans lui les huit premiers
   sont huit artistes cités UNE fois, tous à 75 ex æquo.
   Nouvelle tête de liste sur les vraies données : Surusinghe, FRÈJUS, G2G,
   UNIIQU3, Ahadadream, Jennifer Loveless, Eoin DJ — **zéro star installée**,
   y compris sous `min_emergence=60`.

2. **« DÉJÀ SUIVI » N'EST PAS UNE DÉCOUVERTE.** `rising_artists(exclure_suivis=True)`
   (le défaut ; `?inclure_suivis=true` les ramène) écarte les artistes dont
   `Artist.source_instagram_id` désigne un compte suivi : la question « mérite-t-il
   d'être suivi par Kinda ? » est alors déjà RÉPONDUE — un fait, pas un jugement de
   notoriété. C'est exactement la population que `min_emergence=60` remontait
   (Skrillex 85, David Guetta 75, Lady Gaga 60, Tiësto 60 : 18 des 409 citations à
   60 ou plus).

3. **L'ATTRIBUTION, réelle mais marginale — et il faut le dire.** `record_mentions`
   recopiait la note d'émergence de l'ARTICLE sur CHAQUE artiste cité : un article
   à 75 sur un jeune talent parrainé faisait monter le parrain déjà célèbre
   exactement comme l'inconnu qu'il parraine. `est_citation_principale` tranche
   désormais (rang 0, ou nom dans le titre, ou **aucun autre cité nommé dans le
   titre**) et seule une citation principale fait monter `max_emergence` ;
   `artist_mentions.principal` (migration **0020**, `NULL` = inconnu et vaut
   PRINCIPAL partout — règle du doute) **persiste la décision à l'enregistrement**,
   parce que le rang n'existe que sur l'article et que la purge à 2 jours l'efface.
   Mais la mesure interdit de lui prêter plus que ce qu'elle fait : **155 citations
   sur 5 178** (3 %) de la fenêtre sont détectées secondaires, et le correctif ne
   change qu'**UNE ligne du top 15**, parce que 4 544 des 5 178 citations ont déjà
   perdu leur article. C'est un correctif de qualité d'ARCHIVE, pour les mois à
   venir, pas un correctif de classement.

   **La troisième règle est un garde-fou trouvé par la MESURE, pas par le
   raisonnement**, et sans elle le remède détruisait ce qu'il protège : le premier
   jet, qui ne regardait que le rang et le titre, dégradait 14 des 15 artistes de
   « Les dates de nos artistes en octobre ! » (techno FR, émergence 65) et 10 des
   11 d'une sélection Fip — des line-ups d'artistes tous réellement émergents,
   c'est-à-dire la meilleure matière A&R de la base. Un titre qui ne nomme personne
   ne désigne pas de sujet, donc ne relègue personne. Corollaire : **ne jamais**
   diluer la note par le NOMBRE d'artistes cités, pour la même raison.

   Ce que l'attribution ne résout PAS : un parrain **nommé dans le titre** reste
   principal (« Dance Up-And-Comer of the Month: Tatyana Jane … Gotten Lessons
   From Skrillex » laisse Skrillex à 85). C'est le point 2 qui l'écarte — les deux
   étages se complètent, aucun ne prétend suffire.

Rattrapage de l'archive (13 310 citations), à passer une fois après la migration :
`uv run python -m scripts.reparations.recalculer_attribution_artistes [--apply]` (sans
`--apply` : le plan ; idempotent, aucun appel payé). Plan mesuré le 2026-10-03 :
47 citations secondaires avérées, 27 fiches dont `max_emergence` change, et
12 801 citations sans article dont le rang est perdu pour toujours — elles restent
`NULL`, jamais dégradées à tort.

**`Artist.tracked` a été SUPPRIMÉE** (migration **0021**, 2026-10-04). C'était une
colonne morte : écrite par `PATCH /artists/{id}`, lue par PERSONNE — aucune lecture
dans `app/`, aucune dans `scripts/`, aucune requête qui la filtre ou la trie
(vérifié exhaustivement avant de descendre). Ce que le pipeline appelle « artiste
suivi » — l'exception au pré-filtre (`scoring.ContexteScoring`) et la réserve de
visuels (`content/choix_visuels.py`) — est `comptes_artistes.IndexComptes`,
c'est-à-dire les **sources Instagram actives de catégorie artiste** (88 en
production), jamais `tracked`.

Elle avait déjà coûté : `scripts/diagnostic/etat_notion.py` la comptait pour conclure « 0
artiste suivi sur 4 684 : les chemins qui en dépendent ne se déclenchent jamais »
— FAUX, relayé à l'utilisateur puis écrit dans sa documentation. Une alerte fausse
est pire que pas d'alerte : elle fait chercher la panne ailleurs. Une colonne
qu'on peut confondre avec une vraie est pire qu'une colonne absente, d'où la
suppression plutôt qu'un commentaire de plus.

Ce qui RESTE est le geste utile : `artists.source_instagram_id` (migration 0018),
qui rattache une fiche à son compte suivi quand le nom ne suffit pas. C'est
désormais le SEUL champ que `PATCH /artists/{id}` accepte, et le corps est
`extra="forbid"` : un appelant qui enverrait encore `tracked` reçoit un **422**
plutôt qu'un 200 silencieux où rien n'est écrit — c'est précisément le succès
silencieux qui a permis à cette colonne de vivre sans lecteur.

Le `downgrade` recrée la colonne avec son défaut `false`, donc un suivi posé à la
main avant la migration ne revient pas. Sans conséquence mesurée : `PATCH` n'a
jamais été appelé depuis une interface (le panneau Artistes n'existait pas), la
colonne ne portait que des `false`.

Positionnement vs le marché : inutile de concurrencer Chartmetric/Viberate/Soundcharts
sur les signaux plateformes (vélocité TikTok, ajouts playlist). Le signal propre de
Kinda est AMONT : de qui la presse électro FR, Reddit et l'UGC parlent avant que les
chiffres de streaming ne bougent.
