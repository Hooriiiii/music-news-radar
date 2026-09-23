# KINDA FRANCE — CONTEXTE PRODUIT & TECHNIQUE

> Document de référence produit, fourni par l'utilisateur le 2026-09-23.
> Il décrit le positionnement éditorial, l'état de la Phase 1 et la trajectoire
> vers la Phase 2 (« Event Intelligence »).
>
> **Rapport avec `CLAUDE.md`** : ce document dit CE QU'ON VEUT ; `CLAUDE.md` dit
> ce que le code FAIT et POURQUOI. Quand les deux divergent, c'est ici que se lit
> l'intention, et `CLAUDE.md` qui décrit la réalité — les écarts connus au
> 2026-09-23 sont listés à la fin.

## 1. Ce qu'est Kinda

Kinda France est une application locale qui fait fonctionner automatiquement le
compte Instagram `@kindanewsfrance`.

Le système :

* collecte des actualités musicales ;
* les déduplique ;
* les score ;
* choisit les plus pertinentes ;
* génère les posts avec la direction artistique Kinda ;
* planifie les publications ;
* publie automatiquement sur Instagram.

L'objectif éditorial principal est de construire un **média français d'actualité
musicale**, principalement centré sur la musique électronique.

Le projet possède aussi un deuxième objectif :

> construire une mémoire long terme des artistes émergents pour détecter ceux qui
> commencent à monter.

Ces deux métiers sont liés mais doivent rester séparés :

1. **Média** : quelles actualités méritent d'être publiées ?
2. **A&R** : quels artistes montrent des signaux intéressants dans le temps ?

L'application tourne actuellement en local sur un Mac. La base Postgres est
hébergée chez Neon.

## 2. Positionnement éditorial

Kinda France ne doit PAS être uniquement un média consacré aux artistes
émergents. Pour attirer une audience, il doit d'abord couvrir les sujets que les
gens veulent réellement suivre.

### 2.1 Grosses actualités internationales

Artistes majeurs, grosses releases, festivals internationaux, tournées, annonces
surprises, événements culturels majeurs, actualité importante de l'industrie.

### 2.2 Très forte couverture française

Kinda étant **Kinda France**, une place importante doit être donnée aux artistes
français, DJs français, festivals français, clubs, salles, labels, scènes
locales, événements français, et nouvelles tendances qui commencent en France.

La France apporte un bonus éditorial important, mais :

> une actualité française faible ne doit pas automatiquement battre une énorme
> actualité internationale.

### 2.3 Musique électronique comme territoire principal

Techno, house, trance, drum & bass, UK garage, EDM, bass music, hard dance,
hardstyle, hardcore, ambient, electronica, electro-pop, club music, scènes
électroniques expérimentales.

### 2.4 Pop comme territoire adjacent

La pop n'est pas interdite. Mais **Kinda ne doit pas devenir un média pop
généraliste**.

La pop mérite d'être couverte lorsqu'il s'agit notamment de : très grosse
actualité internationale ; phénomène culturel massif ; artiste très important en
France ; dance-pop ; electro-pop ; hyperpop ; K-pop lorsque l'actualité est
réellement importante ; collaboration avec la scène électronique ; nouveau
mouvement esthétique ; phénomène générationnel ; tendance susceptible
d'influencer les nouvelles scènes.

### 2.5 Artistes émergents

L'émergence constitue une **différenciation forte**, pas le sujet unique du
média. Kinda doit être capable de publier une énorme actualité Fred again..,
puis un jeune producteur français qui commence à envoyer plusieurs signaux forts.

## 3. Philosophie générale

Le média doit répondre à :

> **Qu'est-ce qu'il se passe d'important dans la musique électronique et les
> scènes proches, maintenant, pour une audience française ?**

Le système A&R répond séparément à :

> **Quels artistes semblent commencer à prendre de l'importance avant que cela
> devienne évident ?**

## 4. Architecture actuelle

Le pipeline actuel fonctionne déjà et ne doit PAS être réécrit depuis zéro.

```text
INGESTION → DEDUP HASH → DEDUP TITRE / ACTUALITÉ → ÉLECTION DU MEILLEUR
REPRÉSENTANT → SCORING → ILLUSTRATION → MÉMOIRE A&R → PURGE →
ARBITRAGE DES CRÉNEAUX → COMPOSITION → GEL → PUBLICATION
```

## 5. Sources actuelles (2026-09-23)

```text
145 sources actives : 129 Instagram, 14 RSS, 2 Reddit
Par type : 88 artistes, 32 médias, 14 festivals, 9 labels, 2 clubs
```

Philosophie à conserver :

> **aller chercher l'information au niveau de la source, pas chez les comptes qui
> repostent d'autres médias.**

L'architecture `app/sources/` est déjà pluggable (`fetch() -> list[RawItem]`) :
ajouter une source doit rester une opération locale à ce dossier.

## 6. Instagram est actuellement la source principale

Instagram joue deux rôles : l'**information** (annonces directes des artistes,
labels, festivals, clubs) et les **assets** (photo, vidéo, carousel, visuel
officiel déjà présents dans le post).

Deux contraintes connues :

* **Business Discovery** ne voit que les comptes professionnels / creators : une
  partie des artistes émergents en compte personnel reste invisible.
* **Quota Meta** partagé et vite atteint. La plupart des comptes sont interrogés
  toutes les 3 h ; 12 comptes prioritaires toutes les 5 min. Un coupe-circuit
  arrête les appels au premier refus lié au quota.

## 7. X / Twitter

L'adapter existe encore mais les sources sont désactivées (coût / facturation de
l'API). Ne pas réactiver par défaut sans vérifier la valeur ajoutée.

## 8. Déduplication

Elle intervient AVANT le scoring pour éviter scoring multiple, coût LLM inutile,
et plusieurs publications de la même actualité.

```text
URL + titre → similarité entre titres → LLM seulement pour les cas ambigus
→ élection du représentant le plus informatif
```

Fenêtre de rapprochement entre titres : 48 h. Le représentant privilégie source
primaire, richesse de l'information, présence de visuels, artistes identifiés.

## 9. Scoring actuel

Trois scores **indépendants**, à ne PAS fusionner en une moyenne unique :
`relevance` (intérêt éditorial), `emergence` (intérêt découverte / A&R),
`hotness` (urgence).

L'architecture utilise un petit modèle local Qwen, Claude Haiku pour le scoring
principal, et des garde-fous déterministes.

## 10. Phase 1 — logique actuelle

```text
POST INSTAGRAM → INGESTION → SCORING → GARDE / IGNORE →
UTILISATION DES MÉDIAS DU POST → APPLICATION DE LA DA KINDA → PUBLICATION
```

C'est volontairement simple : l'information est directement disponible, les
assets aussi, la source est souvent primaire, le délai annonce → publication est
court, et le pipeline reste simple.

## 11. Structure actuelle d'un post Kinda

```text
SLIDE 1 : cover Kinda générée (photo, logo, accroche, mots mis en évidence)
SLIDE 2+ : médias réels de l'actualité
```

Formats `1080x1350` et `1080x1920`. Composition textuelle par Sonnet, scoring par
Haiku.

## 12. Illustration actuelle

```text
1. photo de l'actualité
2. photo Instagram du compte source
3. photo d'un artiste suivi cité
4. recherche web
5. sinon on saute temporairement le post
```

Règles existantes : jamais d'affiche de line-up en fond de cover ; jamais de média
provenant d'un compte média concurrent ; refus des images nécessitant un upscale
excessif.

> **Ne pas reconstruire un Asset Finder depuis zéro.** L'objectif futur est de
> faire évoluer cette logique pour qu'elle soit liée à un `Event` plutôt qu'à un
> simple article.

## 13. Reels

Déjà générés automatiquement : un MP4 `cover Kinda → vidéo d'origine`, assemblé
via ffmpeg. Meta ne fournit pas toujours les vidéos contenant de la musique
protégée, d'où l'usage de `yt-dlp` sur le lien public dans certains cas.
Comportement existant, à ne pas réimplémenter sans raison.

## 14. Mémoire A&R actuelle

```text
3 838 artistes, 10 727 citations
```

Les articles sont supprimés après 2 jours, mais **les artistes et leurs citations
sont conservés indéfiniment**. L'objectif n'est pas « qui est cité aujourd'hui »
mais « depuis quand, à quelle fréquence, la fréquence augmente-t-elle ».

## 15. Problème actuel A&R

Aucun artiste n'est marqué comme `suivi`, ce qui rend inactifs : l'exception de
scoring, la récupération de photo d'artiste suivi, et la shortlist A&R.

## 16. Retention

Articles non publiés : ~2 jours. Posts publiés : indéfiniment (archive éditoriale,
prévention des doublons).

Cette stratégie peut devenir limitante en Phase 2 pour reconstruire des événements
et calculer certains signaux. **Ne pas modifier immédiatement la retention globale
sans analyse.** Une meilleure solution pourrait être :

```text
RawItem temporaire · Event permanent / semi-permanent · ArtistMention permanent
```

## 17. Publication

Créneaux répartis dans la journée (carrousels et reels). Workflow
`J-1 composition → T-2h gel → T publication`. Trois modes : `arret`, `apercu`,
`reel` — le mode réel exige une confirmation explicite.

> Le système préfère perdre un post plutôt que publier un doublon. Cette
> philosophie doit être conservée.

## 18. Invariants importants

```text
jamais publier deux fois le même post
jamais utiliser les images d'un média concurrent
jamais utiliser une affiche de lineup comme fond de cover
jamais publier une cover neutre faute d'image
les pannes techniques font attendre un article, elles ne l'excluent pas
une décision humaine prime toujours sur l'automatisation
les choix visuels doivent échouer proprement
```

## 19. Philosophie coût

> **Ne jamais payer deux fois la même chose.**

Doublons non scorés ; analyses visuelles en cache ; recherches web en cache ;
opérations coûteuses tard dans le pipeline ; Instagram / RSS / Reddit quasi
gratuits ; hébergement local ; Neon en offre gratuite.

## 20. Limite principale de la Phase 1

Le système actuel est principalement **post-centric**. Or une seule information
peut être observée via Instagram artiste, Instagram label, Mixmag, Tsugi, Reddit,
YouTube, communiqué de presse.

La future évolution doit cesser de les considérer comme des doublons : ce sont
**plusieurs observations du même événement**.

## 21. Objectif Phase 2 — Event Intelligence

```text
SOURCES → RAW ITEMS → ENTITY RESOLUTION → EVENT CLUSTERING → EVENT →
EVENT FEATURES → SCORING / RANKING → PUBLICATION CANDIDATE
```

## 22. Entité `Event`

```json
{
  "id": "...",
  "title": "Fred again.. annonce un nouvel album",
  "event_type": "release_announcement",
  "artists": ["Fred again.."],
  "first_seen_at": "...",
  "last_seen_at": "...",
  "source_count": 6,
  "platform_count": 3
}
```

## 23. `EventObservation`

```json
{
  "event_id": "...",
  "raw_item_id": "...",
  "platform": "instagram",
  "source": "official_artist",
  "published_at": "...",
  "source_role": "primary"
}
```

Permet de savoir qui a publié en premier, combien de sources parlent du sujet, sur
quelles plateformes, à quelle vitesse.

## 24. Velocity

```text
sources dans la dernière heure · sources sur 6 heures · plateformes impliquées
accélération des mentions · nombre de nouvelles observations
```

Doit distinguer « actualité importante mais froide » de « actualité qui explose
actuellement ».

## 25. Nouveaux axes de scoring

Conserver `relevance`, `emergence`, `hotness`, et ajouter progressivement :

```text
editorial_fit · intrinsic_importance · freshness · velocity
source_confidence · novelty · france_relevance · audience_fit
```

Certaines valeurs sont mieux calculées en **code** (freshness, velocity,
source_count, platform_count, baseline). Le LLM sert surtout à comprendre le
sujet, extraire les entités, juger l'importance sémantique, classifier, résumer,
résoudre les ambiguïtés.

## 26. Pas de score unique naïf

Éviter `score_final = moyenne(...)`. Utiliser **gates + features + ranking** :

```python
if editorial_fit < MIN_EDITORIAL_FIT:
    feed_candidate = False
```

puis classer les candidats restants sur importance, freshness, velocity, France,
novelty, confiance.

## 27. Plusieurs classements

**FEED** (quels sujets méritent une publication ?), **BREAKING** (quels sujets
nécessitent une réaction immédiate ?), **A&R** (quels artistes méritent davantage
de surveillance ?). Le même événement peut apparaître dans plusieurs classements.

## 28. Nouvelle logique A&R

L'emergence ne doit plus être déterminée uniquement par le post courant, mais
s'appuyer sur la mémoire :

```text
première mention → nouvelle release → petit média → DJ reconnu → festival
→ label → plusieurs nouvelles mentions
```

La **répétition de signaux indépendants** devient plus importante que le volume
absolu.

## 29. Features A&R futures

```text
mentions_7d · mentions_30d · growth_rate · mention_acceleration
independent_source_count · platform_count
quality_of_cosigns · quality_of_media · quality_of_bookings
first_seen_at · country · city · release_frequency
```

## 30. Utiliser des métriques relatives

Ne jamais supposer « beaucoup de likes = artiste intéressant ». Comparer un
artiste **à lui-même** (post actuel / performance habituelle, z-score,
percentile). Un artiste de 5 000 followers qui fait soudain 100 000 vues envoie un
signal plus intéressant qu'un artiste de 5 millions qui fait sa moyenne.

## 31. Futures sources de données

Instagram reste central, ne pas le remplacer. Ajouter progressivement : **RSS**
(très bon ratio coût / information) ; **YouTube** (clips, performances, sets,
annonces vidéo, interviews) ; **Reddit** comme signal communautaire, pas comme
vérité ; **newsletters / communiqués** via une adresse dédiée `press@kinda...` ;
**plateformes musicales** (SoundCloud, MusicBrainz, ListenBrainz, Beatport,
Spotify, charts publics) sans grosse dépense immédiate.

## 32. Assets en Phase 2

Conserver le système actuel, le rattacher progressivement à `Event` :

```text
EVENT → AVAILABLE ASSETS → SOURCE QUALITY → VISUAL QUALITY →
RIGHTS / ORIGIN → ASSET RANKING
```

```json
{
  "asset_source": "instagram",
  "source_account": "...",
  "source_event_id": "...",
  "asset_type": "video",
  "origin_type": "official_artist",
  "reuse_status": "unknown",
  "credit_required": true
}
```

## 33. Droits d'utilisation

> Accéder techniquement à un asset ne signifie pas posséder un droit de
> réutilisation.

Préparer l'architecture avec `approved`, `licensed`, `permission_received`,
`credit_required`, `unknown`, `blocked`. La feature peut rester partiellement
inactive au début, mais la structure de données doit permettre son usage futur.

## 34. Human feedback

Enregistrer les choix manuels : `published`, `rejected`, `priority_override`,
`category_corrected`, `artist_corrected`, `duplicate_corrected`,
`visual_corrected`. À terme, « prédiction machine vs choix humain » devient la
principale donnée de calibration.

## 35. Métriques futures

Évaluer autrement que par la distribution des scores : `Precision@K`, recall des
grosses actus, faux négatifs, latence, redondance, taux de publication.

> Sur les 20 premiers sujets proposés aujourd'hui, combien auraient réellement été
> choisis par un éditeur humain ?

## 36. Architecture cible progressive

```text
Source · SourceAccount · RawItem · Asset · Artist · ArtistMention
Event · EventObservation · EventFeatures · Score
PublicationCandidate · Post · Publication · HumanFeedback
```

## 37. Roadmap de migration

> **Pas de big rewrite.**

* **Phase 1 (aujourd'hui)** — stabiliser Instagram, RSS, Reddit, dedup, scoring,
  illustration, composition, publication, A&R.
* **Phase 1.5** — meilleure taxonomie, métadonnées source, métadonnées assets,
  feedback humain plus structuré, premier `event_cluster_id`.
* **Phase 2A** — créer `Event` et `EventObservation`, sans changer la publication.
* **Phase 2B** — faire évoluer la dedup vers de l'event clustering.
* **Phase 2C** — ajouter velocity, source_count, platform_count, first_seen,
  novelty, France relevance.
* **Phase 2D** — ranking par événement.
* **Phase 2E** — A&R vers du scoring temporel.
* **Phase 3** — seulement une fois les besoins démontrés : données payantes,
  streaming analytics, charts avancés, trend detection, personnalisation.

## 38. Règle générale de développement

1. regarder d'abord si une brique équivalente existe déjà ;
2. ne pas casser les invariants actuels ;
3. préserver le caractère pluggable des sources ;
4. préserver le fonctionnement local ;
5. minimiser les appels payants ;
6. mettre en cache tout résultat réutilisable ;
7. ne jamais payer plusieurs fois pour la même information ;
8. préférer une migration progressive à une refonte ;
9. séparer données mesurables et jugement LLM ;
10. toujours laisser les décisions humaines primer sur l'automatisation.

## 39. Vision long terme

> **Un desk éditorial automatique spécialisé dans la musique électronique et les
> nouvelles scènes pour le marché français.**

Capable de répondre à : quelles sont les actualités les plus importantes
aujourd'hui (média) ; qu'est-ce qui commence à exploser (breaking) ; qu'est-ce qui
compte pour une audience française (France) ; quels artistes accumulent des
signaux (A&R) ; quel sujet mérite le prochain créneau et avec quels assets
(publication).

## 40. Principe architectural central

```text
Aujourd'hui : POST → SCORE → PUBLICATION
Demain      : POSTS + ARTICLES + AUTRES SIGNAUX → EVENT →
              COMPRÉHENSION DU CONTEXTE → FEATURES → SCORING → RANKING →
              PUBLICATION
```

> **Kinda doit évoluer progressivement d'un système centré sur les posts vers un
> système centré sur les événements, sans jeter l'architecture qui fonctionne
> déjà.**

---

## Écarts connus entre ce document et le code, au 2026-09-23

Relevés en lisant le code et la base de production. À trancher, pas à ignorer.

1. **La pop.** Le §2.4 veut une pop CONDITIONNELLE ; le `SYSTEM_PROMPT` de
   `app/pipeline/scoring.py` porte l'inverse, en toutes lettres : « ne les
   déprioris JAMAIS ». Cette règle avait été écrite parce que le modèle
   dépriorisait la pop tout seul. Mesuré : 14 % des articles au-dessus du seuil
   de publication sont pop ou K-pop, classements K-pop hebdomadaires compris.
   **Décision de l'utilisateur le 2026-09-23 : resserrer, comme dit le §2.4.**

2. **La rétention.** Le §16 dit « ne pas modifier sans analyse » ; elle est passée
   de 14 à 2 jours le 2026-09-21, à la demande de l'utilisateur. Effet de bord
   mesuré : les réserves d'images (barreaux 2 et 3 du §12) lisent la table
   `articles` et ne voient donc presque plus rien — la recherche web porte
   désormais l'essentiel du rattrapage.

3. **`source_count` existe déjà** (§24). `cluster_sources_claude` s'en sert pour
   forcer le passage chez Claude quand une actu réunit au moins 3 sources. Le
   comptage par groupe de doublons est là ; il n'est simplement pas exposé comme
   une feature de scoring. Point de départ de la Phase 2C, pas chantier neuf.
