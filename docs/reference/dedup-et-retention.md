# Dedup a deux etages, et retention

**Quand lire ce fichier** : avant de toucher a `app/pipeline/dedup.py`,
`cluster.py`, `cluster_arbitrage.py`, `cluster_resume.py`, `election.py`,
`retention.py`, ou avant de reparer un lien de doublon a la main. Il porte les
seuils de similarite et les mesures qui les fixent, les trois bandes
(certaine / douteuse arbitree par Claude / differente), le piege de
`CACHE_VERSION`, les articles LEUR PROPRE doublon, et l'ORDRE qui gouverne la
purge des evenements.

## Dédup à deux étages

1. **Par hash** (`pipeline/dedup.py`) : même url normalisée + même titre → le même
   lien vu deux fois n'entre qu'une fois. Attrape les re-fetchs, pas les reprises.
2. **Par titre, ENTRE sources** (`pipeline/cluster.py`) : la même actu couverte par
   Mixmag, DJ Mag et Tsugi produisait trois articles. On rapproche les titres
   normalisés (accents, ponctuation, traits d'union recollés, mots vides FR+EN) par
   **coefficient de recouvrement** des mots significatifs — pas Jaccard, qui
   punirait le titre traduit ou suffixé du nom du média. Seuil 0.65, fenêtre 48 h,
   minimum 2 mots partagés. Les liens se forment contre l'article vu le plus tôt ;
   puis **`elire_representants`** (`app/pipeline/election.py`, juste après, AVANT
   le scoring) promeut le membre le plus INFORMATIF — clé sur le titre et la
   source seulement (le résumé est réécrit par le scoring, il ferait osciller) :
   artistes suivis cités dans le titre, source primaire (artiste/festival/label/
   club), visuels autorisés, mots significatifs du titre, ancienneté, id. Un
   représentant engagé (`USED`, un `Post`, un `Creneau` non `LIBRE`) ne change
   jamais — il garde alors ses notes. Un représentant noté par le pré-filtre local
   dont le groupe atteint `cluster_sources_claude` (3) sources est remis à noter
   par Claude, sauf s'il est lui-même engagé. Borne :
   `cluster_reevaluations_max_par_run` (10) réélections + remises à noter par run.
   Jamais de chaîne (un saut suffit toujours). Idempotent.

   Conséquences câblées : `link_duplicates` tourne AVANT le scoring dans
   `run_pipeline` (un doublon n'est jamais scoré, donc jamais payé), et l'API
   `/articles` masque les doublons par défaut (`include_duplicates=true` pour les
   voir).

   **Une actu déjà publiée ne ressort presque jamais** (2026-09-21 ; titre
   ajusté le même jour après l'arbitrage Claude ci-dessous — la bande
   « différente » et une panne d'arbitrage laissent volontairement passer une
   reprise plutôt que de risquer d'enterrer une actu neuve). La fenêtre de 48 h
   ne comparait que des articles frais entre eux : une reprise plus tardive de
   la même actu par une autre source n'était rapprochée de rien, alors que les
   articles `USED` ne sont jamais purgés (« Rétention » ci-dessous) précisément
   pour servir d'archive éditoriale — l'archive existait, rien ne la
   consultait. `link_duplicates` élargit désormais l'ensemble des
   représentants POSSIBLES aux articles `USED` de l'archive, bornés par
   `cluster_archive_jours` (**7**, `app/config.py`) et hors de la fenêtre de 48 h
   (pas de double compte avec les articles frais). Un article archivé ne peut jamais devenir lui-même un
   doublon (seulement servir de cible), et un représentant déjà `USED` reste
   verrouillé par `elire_representants` même s'il est hors fenêtre — cette
   fonction se fonde sur la date du DOUBLON, pas celle du représentant, donc un
   groupe formé contre un article publié il y a des jours est bien réexaminé,
   mais jamais détrôné.

   **Contre l'archive, une mesure et un seuil PROPRES** (revue du 2026-09-21).
   La première version réutilisait le seuil de 0.65, éprouvé sur 48 h ; sur une
   archive d'un mois d'un feed centré sur quelques artistes récurrents, il
   enterrait des actus NEUVES, et le lien est irréversible en pratique (l'article
   sort de la file, le représentant `USED` est verrouillé, rien ne remonte).
   Paires mesurées, toutes des actus différentes : « Charlotte de Witte annonce
   un nouvel EP » / « … annonce une tournée mondiale » (1.00 : « EP » tombe, le
   premier titre est CONTENU dans le second), « Fred again.. sort un nouveau
   morceau » / « … un nouvel album » (0.75), « Les Transmusicales dévoilent leur
   programmation » / « … une nouvelle vague » (0.67). Aucun seuil de
   recouvrement n'épargne la première, qui vaut 1.00 : l'archive utilise donc
   `archive_similarity_from_tokens` — mots partagés rapportés au titre le plus
   LONG, qui compte ce qui DIFFÈRE — avec `cluster_archive_seuil` (**0.8**). Les
   trois paires tombent à 0.60 / 0.75 / 0.67, une vraie reprise traduite
   (« Dour Festival unveils its 2027 lineup » / « … dévoile son line-up 2027 »)
   reste à 0.80. La fenêtre fraîche de 48 h, elle, ne change pas. Et la fenêtre
   d'archive est passée de 30 à **7 jours** : une reprise ressort dans la
   semaine, au-delà le risque de faux positif dépasse le service rendu.

   **Un lien d'archive se VOIT** : compté à part (`ClusterStats.archive`,
   distinct de `frais` ; « dont N contre une actu déjà publiée » dans la sortie
   de `run_pipeline` et de `run_veille`), journalisé en WARNING avec les deux
   titres et la similarité, et tracé sur l'article lié dans `file_exclu_raison`
   (« doublon d'une actu deja publiee (article N, similarite 0.80) »).

   **Claude tranche la bande douteuse, entre les deux seuils** (Task 7,
   2026-09-21). `archive_similarity_from_tokens` et `title_similarity_from_tokens`
   se contredisent sur toute une bande de scores : mesuré sur 15 reprises
   plausibles d'une même actu, 11 passaient entre les mailles du seuil de 0.8,
   et aucun seuil ne peut à la fois les attraper et épargner les actus neuves
   (voir la démonstration mathématique dans `archive_similarity_from_tokens`).
   `link_duplicates` classe donc chaque rapprochement contre l'archive en
   TROIS bandes :
   - **certaine** — `archive_similarity_from_tokens` ≥ `cluster_archive_seuil`
     (0.8) → lié sans rien demander, comme avant ;
   - **douteuse** — sous ce seuil, mais `title_similarity_from_tokens` (le
     recouvrement) ≥ 0.65 → **Claude tranche** (`app/pipeline/
     cluster_arbitrage.py`), avec les DEUX titres et leurs résumés
     (`Article.summary`) ; motif `messages.parse()` identique à
     `scoring.make_claude_scorer`, modèle `settings.arbitrage_model` (Haiku 4.5,
     ~800 jetons d'entrée / ~35 de sortie mesurés, ~0,001 $/appel). Le résumé
     n'est PAS encore réécrit par le scoring à ce stade — `link_duplicates`
     tourne AVANT (voir plus haut) : c'est la description RSS brute (parfois
     l'article entier) ou la caption Instagram avec son bandeau de
     métriques. Tronqué à `scoring.RESUME_MAX_CARACTERES` (1500), comme
     `scoring.build_user_prompt` et pour la même raison : un coût d'entrée
     non maîtrisé sinon ;
   - **différente** — sous les deux seuils → aucun lien, aucun appel.

   Chaque paire (les deux ids d'article, ordre indifférent) n'est arbitrée
   qu'UNE FOIS : le verdict est mémorisé dans `cluster_arbitrage_cache_path`
   (JSON à côté du jeton, écriture atomique — même mécanique que
   `illustration_cache_path`), et relu avant tout appel.

   **Changer `CACHE_VERSION` (`app/pipeline/cluster_arbitrage.py`) désarme
   les verdicts manuels.** Un fichier d'une autre version est traité comme
   illisible, des DEUX côtés. En LECTURE, `lire_cache` rend alors un cache
   VIDE : les verdicts manuels « différent » existants **cessent de
   protéger** — l'auto-réparation ne les voit plus, et les liens qu'ils
   interdisaient se reforment dès le passage suivant, actus neuves
   enterrées sous l'archive comprises. En ÉCRITURE, `_lire_pour_ecriture`
   lève et plus aucun verdict ni compteur de tentatives ne s'écrit : chaque
   passage horaire redemande donc les mêmes paires douteuses et repaie
   jusqu'à `cluster_arbitrage_max_par_passage` appels, et
   `scripts.reparations.marquer_doublon_manuel` échoue. Supprimer le fichier « pour
   débloquer » efface les verdicts manuels pour de bon. Qui change la
   version doit donc fournir, dans le même commit, une **migration** qui
   relit l'ancien format et réécrit le nouveau en **préservant toutes les
   entrées `manuel`** (les verdicts de Claude et les tentatives peuvent, au
   pire, être rejoués) — jamais un simple changement de la constante. Une PANNE (clé
   absente, crédits épuisés, réseau mort, réponse hors schéma) n'est JAMAIS
   mémorisée comme un verdict et ne pose aucun lien : le doute favorise
   toujours la publication, la paire sera réarbitrée au passage suivant (sous
   le plafond de tentatives ci-dessous). Coût borné par passage
   (`cluster_arbitrage_max_par_passage`, 20). **Seul le passage horaire
   (`run_pipeline`, via `dedupliquer_avec_arbitrage`) arbitre** — `run_veille`
   (toutes les 5 min) appelle `link_duplicates` sans argument `arbitre`,
   distinction par PARAMÈTRE et non en devinant l'appelant. Mesuré en
   production le 2026-09-21 (352 articles frais, 14 dans l'archive) : 1 seule
   paire douteuse, 0 certaine — le volume réel est loin du plafond. Les
   compteurs `duplicates_arbitres` / `duplicates_arbitrage_pannes` du rapport
   de `run_pipeline` rendent ça visible sans fouiller les logs. Coupe-circuit :
   `cluster_arbitrage_enabled` (`.env`).

   Une réponse dont `raison` (la trace de supervision) dépasse la longueur
   attendue n'est jamais rejetée : un validateur la tronque à
   `RAISON_MAX_CARACTERES` (400), le SDK Anthropic ne garantissant cette
   longueur que côté description du schéma, pas côté validation. Chaque paire
   garde aussi un compteur de TENTATIVES distinct des verdicts
   (`Cache.tentatives`) ; passé `cluster_arbitrage_tentatives_max` (3), elle
   n'est plus jamais retentée (sans qu'aucun verdict ne soit mémorisé pour
   autant) — une panne systématique sur une paire ne doit pas consommer le
   budget de chaque passage indéfiniment. `link_duplicates` sert d'abord les
   paires JAMAIS tentées (passe 1) avant de rejouer celles déjà en échec sous
   le plafond (passe 2). Un article DIFFÉRÉ en passe 1 reste néanmoins un
   représentant POSSIBLE dès cette passe (avec un garde-fou contre
   l'auto-comparaison) : sinon un article frais plus RÉCENT qui le reprend
   usurpe sa place, et le plus ancien finit par se lier au plus récent —
   l'inverse de la règle « le représentant est l'article vu le plus tôt ».
   L'écriture du cache atomique utilise un nom de temporaire PAR PROCESSUS
   (`tempfile.mkstemp`), même mécanique que `app/pipeline/quota_graph.py` :
   un nom fixe serait écrit à deux mains par le passage horaire et
   `scripts.reparations.marquer_doublon_manuel` lancé pendant ce passage.

   **Réparer un faux positif exige un verdict MANUEL, pas seulement d'effacer
   les deux champs** — effacer `duplicate_of_id` et `file_exclu_raison` ne
   suffit pas : le passage suivant de `link_duplicates` relie l'article à
   nouveau tant qu'il reste dans la fenêtre de 48 h de sa vie publiable,
   c'est-à-dire pendant toute sa vie publiable. La réparation qui tient :
   `uv run python -m scripts.reparations.marquer_doublon_manuel --article-a ID
   --article-b ID --different --raison "..."` défait le lien existant ET pose
   un verdict manuel qui prime sur tout rapprochement automatique ultérieur —
   y compris sur la bande CERTAINE et sur une paire d'articles FRAIS (aucune
   des deux mesures de score n'échappe à cette vérification), vérifié avant
   toute mesure de score. Le même script avec `--meme-actu` confirme au
   contraire un lien qu'on veut garder quoi qu'il arrive
   (`app/pipeline/cluster_arbitrage.marquer_manuel`, qui LÈVE si l'écriture
   n'est pas confirmée par une relecture, ou si le cache existant est présent
   mais illisible — jamais un cache reconstruit à partir de rien par-dessus
   un fichier corrompu, ce qui effacerait des verdicts manuels existants ;
   `fusionner_et_ecrire` applique la même règle côté écriture automatique,
   en annulant l'écriture plutôt qu'en écrasant). Un verdict manuel posé
   PENDANT un passage — pendant que Claude, sur le même appel, répond le
   contraire — prime toujours : `link_duplicates` relit le disque juste avant
   de poser un lien tranché par Claude, et une auto-réparation tourne en tête
   de CHAQUE passage pour défaire tout lien déjà présent en base dont la
   paire porte un verdict manuel « différent », quelle que soit la façon dont
   il est apparu (course, crash entre deux écritures, version antérieure du
   code) — elle n'efface que sa propre raison d'exclusion
   (`RAISON_DOUBLON_PREFIXE`), jamais une exclusion posée ailleurs (gel ou
   composition impossibles). `scripts.reparations.marquer_doublon_manuel --different`
   applique le même filtre quand il défait un lien.

   **Deux points posent un lien vers un autre article, et chacun garde la
   règle « jamais de chaîne, un seul saut suffit toujours ».**
   - `link_duplicates.lier` (`app/pipeline/cluster.py`), pour TOUS les liens
     de la dédup (frais, certain, arbitré, manuel) : il aplatit
     IMMÉDIATEMENT toute chaîne qui en découlerait, en repointant vers la
     cible finale tout article qui pointait déjà vers l'article qu'on vient
     de lier — par construction plutôt que par un correctif localisé à une
     passe ou à une origine de lien. `db.flush()` y est explicite et
     nécessaire : `SessionLocal` tourne en `autoflush=False` en production
     (`app/db.py`), donc sans ce flush, la requête qui cherche « qui pointe
     vers cet article » ne verrait pas un lien posé plus tôt dans le MÊME
     passage. Un article ainsi repointé vers un représentant d'ARCHIVE
     (P → D → R devenu P → R) reçoit la même trace `file_exclu_raison`
     (« … via son representant D … », sans recouvrir une exclusion posée
     ailleurs) et le même compteur `archive` qu'un lien d'archive direct :
     sinon il sortirait de la file en silence.
   - `elire_representants` (`app/pipeline/election.py`), quand l'élection
     promeut un nouveau représentant : il délie l'élu et repointe vers lui
     tous les membres du groupe. Sans danger pour la règle : il ne traite
     que le groupe d'un représentant lui-même NON lié, dont tous les membres
     pointent vers lui en un saut, et jamais celui d'un représentant engagé
     — or un représentant d'archive est toujours `USED`, donc verrouillé
     (`representant_engage`). Un groupe rattaché à l'archive ne change donc
     jamais de tête par ce chemin.

   **Et cet aplatissement a fabriqué des articles LEUR PROPRE doublon**
   (2026-10-02). Mesure en production : sur 662 articles, 59 portaient un lien
   de doublon et **3 pointaient sur leur propre `id`** (21627 MBW, 21709
   Mixmag (IG), 21599 Billboard). Le journal donne la mécanique en deux lignes
   consécutives du MÊME passage : « Sia sells 50% » (21627) rattaché à « Sia
   has sold a 50% stake » (21942), puis 21942 rattaché à 21627. Les deux SENS
   du même lien ont été posés dans un seul passage ; le second `lier` a alors
   fait exactement ce que l'aplatissement lui demande — repointer vers la
   cible finale tout article qui pointait vers l'article qu'on vient de lier —
   sauf que cette cible finale ÉTAIT l'article repointé.

   Comment les deux sens peuvent être posés dans un seul passage : un article
   DIFFÉRÉ en passe 1 est ajouté à `representatives` (pour ne pas se faire
   usurper sa place) **et** il est RE-TRAITÉ en passe 2. Deux différés qui se
   répondent se lient donc l'un à l'autre.

   Le dégât est SILENCIEUX et coûte de l'actualité : un article réflexif se
   présente comme un doublon, donc `score_pending` (qui filtre
   `duplicate_of_id IS NULL`) ne le note jamais et
   `store._articles_publiables` ne le propose jamais. L'article 21599
   attendait depuis 28 h sans note et n'en aurait jamais eu — il aurait
   disparu à la purge sans que rien ne le dise.

   **Deux gardes, dont une seule suffirait mais pas aux mêmes conditions.**
   La PRINCIPALE est dans `resoudre`, là où l'information existe : un candidat
   dont `duplicate_of_id` n'est plus `None` **n'est plus un représentant**, on
   le saute. L'état mutuel ne se forme donc jamais, la bonne réponse est
   rendue (le groupe existe déjà avec l'autre à sa tête), et plus aucun
   arbitrage n'est payé pour une paire dont un côté n'est plus représentant.
   La CEINTURE est au point d'écriture : `cibles_a_repointer` retire le
   représentant lui-même de la liste à repointer, donc `lier` ne peut pas
   écrire `x.duplicate_of_id = x.id`. Preuve par mutation
   (`tests/pipeline/test_cluster_reflexif.py`) : les deux gardes retirées, le lien
   réflexif se reforme à l'identique ; la seule garde de `resoudre` retirée,
   il reste un CYCLE à deux (chacun se présente comme un doublon, donc l'actu
   disparaît tout entière) ; la seule ceinture retirée, le test unitaire de
   `cibles_a_repointer` rougit.

   Pas de contrainte `CHECK (duplicate_of_id <> id)` en base, et c'est un
   choix : elle serait la garantie la plus forte, mais la migration échouerait
   tant que les 3 lignes de production ne sont pas réparées, et un chemin non
   prévu ferait tomber le passage horaire entier au lieu de perdre un article.

   Réparer les lignes DÉJÀ en base (le correctif ne les touche pas) :
   `uv run python -m scripts.reparations.reparer_doublons_reflexifs [--apply]` — plan sans
   `--apply`, idempotent. Il efface le SEUL lien fautif : les notes ne sont
   jamais touchées (21627 garde son 45, 21709 son 65 ; 21599, jamais noté,
   sera noté par le passage suivant puisqu'il redevient notable), et
   `file_exclu_raison` n'est effacée que si la dédup l'a posée
   (`raison_de_doublon`, même filtre que l'auto-réparation d'un verdict
   manuel). AUCUN verdict manuel n'est posé : la paire n'a jamais été un faux
   rapprochement, c'est l'ÉCRITURE du lien qui était fautive — un
   « différent » interdirait pour toujours un lien légitime. Le bandeau de
   supervision compte désormais ces articles à part (voir « Bandeau de
   supervision »).

   **La bande douteuse FRAÎCHE aussi** (Phase 2B, Task 3, 2026-09-24). La
   même machinerie — cache par paire, verdicts manuels prioritaires, plafond
   de tentatives, plafond par passage, écriture atomique — s'applique
   désormais aux paires de deux articles FRAIS dont le recouvrement de titre
   est entre `cluster_arbitrage_frais_seuil` (**0.30**) et
   `DEFAULT_THRESHOLD` (0.65, où le lien est déjà posé sans rien demander).
   Le plancher est MESURÉ, pas arrondi : sur 50 paires réelles jugées le
   2026-09-24, **0 vrai positif sur les 18 paires sous 0.30**, et les deux
   seuls ratés purement sémantiques sont à 0.33 ; entre 0.33 et 0.57 la
   courbe est PLATE (22 % / 20 % / 10 %), donc aucun seuil ne les sépare —
   d'où un arbitrage et non un seuil abaissé (abaisser `DEFAULT_THRESHOLD` à
   0.40 donnait 87 % de faux rapprochements, voie fermée). Comme pour
   l'archive, c'est un **paramètre** qui ouvre la bande
   (`arbitrage_frais_min`, `None` par défaut) : `run_veille` ne le passe
   jamais, et `CLUSTER_ARBITRAGE_FRAIS_ENABLED=false` le laisse à `None`,
   ce qui rend au pipeline exactement les décisions d'avant. Un lien frais
   tranché par Claude est **visible** : compteur propre
   (`ClusterStats.frais_arbitres`) et WARNING nommant les deux articles.
   **Aucune trace n'est posée sur l'article**, et c'est une propriété, pas un
   oubli : `duplicate_of_id` est `ON DELETE SET NULL` et un représentant
   FRAIS est purgé à 2 jours, donc un `file_exclu_raison` survivrait à son
   représentant et tiendrait le doublon hors de la file pour toujours
   (`store._articles_publiables` exige `file_exclu_raison IS NULL`) — au nom
   d'un doublon d'un article qui n'existe plus. Un lien frais est
   AUTO-RÉPARANT, il l'était avant la Phase 2B et le reste. Le cas archive
   n'a pas ce défaut parce que son représentant est `USED`, donc jamais
   purgé : la propriété ne se transporte pas d'une bande à l'autre.

   Le prompt d'arbitrage **dit la vérité sur l'article de référence** : son
   en-tête annonce « DEJA PUBLIE PAR KINDA » ou « ingéré plus tôt, pas encore
   publié » selon `ArticleStatus.USED`, lu sur l'article et non reçu en
   paramètre. Reformuler ce prompt n'invalide aucun verdict mémorisé (la clé
   du cache est la paire d'ids) et ne justifie donc jamais de toucher à
   `CACHE_VERSION`.

   **La porte de candidature sur le RÉSUMÉ** (2026-09-24,
   `app/pipeline/cluster_resume.py`). Les deux bandes ci-dessus raisonnent sur
   les mots du TITRE. Mesuré sur la fenêtre réelle de 48 h (599 articles,
   170 820 paires) : de vrais mêmes-événements n'en partagent AUCUN — le record
   d'Ella Langley couvert par **4 articles Billboard**, une contrefaçon de
   vinyles chez MBW et Mixmag, une levée de fonds chez les mêmes deux. Tous
   restaient éligibles séparément, c'est-à-dire le risque de poster deux fois
   la même actu. Ce qui les attrape est le nombre **ABSOLU** de mots
   significatifs partagés des RÉSUMÉS : le recouvrement NORMALISÉ échoue, il
   sature sur les captions Instagram courtes (cinq mots significatifs font
   0,71). Seuil `cluster_arbitrage_resume_seuil` (**6**) — une borne de
   **BUDGET, pas de précision** : un seuil bas ne pose PAS de mauvais liens
   (l'arbitre a rejeté 27 faux sur 27 ; revérifié le 2026-09-24 sur 10 paires
   réelles, 10/10 correctes), il paie des appels pour rien. Conséquence assumée
   du 6 : la levée de fonds Unit1 Studio, mesurée à sh=5, n'est pas attrapée.

   Le résumé est nettoyé avant d'être compté (`jetons_de_resume`), et c'est un
   détail qui fausse en SILENCE : `Article.summary` est le résumé français du
   scoring pour un article scoré, mais la description RSS brute (balises et
   entités HTML comprises) ou une caption Instagram **suivie de son bandeau de
   métriques** pour un article non scoré — `caption_depuis_summary` le retire,
   sinon « likes » et « commentaires » rapprocheraient n'importe quels deux
   posts. Une paire dont un résumé fait moins de 3 mots significatifs est
   ignorée. Même `significant_tokens` que les titres, jamais une seconde
   tokenisation.

   Même machinerie que les autres bandes (cache par paire, verdict manuel
   prioritaire, plafond de tentatives, coupe-circuit) et même convention :
   c'est un **PARAMÈTRE** qui l'ouvre (`arbitrage_resume_min`, `None` par
   défaut), donc `run_veille` n'arbitre jamais, et
   `CLUSTER_ARBITRAGE_RESUME_ENABLED=false` rend exactement les décisions
   d'avant. **Budget SÉPARÉ** (`cluster_arbitrage_resume_max_par_passage`,
   10) : au premier passage sur une fenêtre pleine cette porte présente des
   dizaines de paires (56 mesurées) et un budget commun de 20 la laisserait
   affamer la bande ARCHIVE, la seule dont un faux négatif reposte une actu
   DÉJÀ PUBLIÉE. Lien visible : compteur propre (`ClusterStats.resumes_
   arbitres`, `duplicates_resumes_arbitres` dans le rapport) et WARNING
   nommant les deux articles ; aucune trace sur l'article, comme tout lien
   frais et pour la même raison (son représentant est purgé à 2 jours).

   **Le correctif sans lequel on ne câblait pas : la RÉCURRENCE**
   (`est_publication_recurrente`). La mesure a trouvé un faux positif que
   l'arbitre ne sait pas voir : il lie « Billboard Hot 100 Top 10 Countdown
   for Sept. 26, 2026 » aux articles sur le record d'Ella Langley, et il a
   RAISON sur les faits — même classement, même date. Mais c'est une
   **publication récurrente hebdomadaire**, pas une couverture de l'annonce :
   lier sortirait l'un des deux de la file au nom d'un format qui reviendra la
   semaine prochaine. Aucun garde-fou existant ne peut l'attraper : une
   récurrence HEBDOMADAIRE est structurellement invisible à un critère borné à
   **48 h**, où il n'y a qu'un seul Countdown. L'information est dans
   `event_observations`, qui dénormalise titre et source et **n'est jamais
   purgée**. Critère : le CADRE d'un titre est l'ensemble de ses mots
   significatifs que la MÊME source réutilise dans au moins
   `cluster_recurrence_min_titres` (**4**) autres titres archivés, répartis sur
   au moins **2 journées distinctes** ; l'article est récurrent si ce cadre
   compte au moins 2 mots et couvre `cluster_recurrence_part_min` (**0,4**) de
   son titre. Les deux exigences portent chacune la moitié du raisonnement : la
   FRÉQUENCE sépare un cadre de rédaction d'un événement, l'ÉTALEMENT SUR
   PLUSIEURS JOURS sépare une série d'une rafale (quatre articles du même jour
   sur le même record ne sont pas un format). Le 4 est mesuré : à 3, le critère
   attrapait aussi « Taylor Swift's 'The Life of a Showgirl' era isn't over
   yet. » sur les mots « taylor » et « swift » — un artiste récurrent n'est pas
   un format récurrent. Mesuré le 2026-09-24 sur les 56 paires candidates :
   **8 écartées sans rien payer** (les 7 paires du Countdown, plus une paire de
   recaps de classement), **zéro vrai positif touché** — Ella Langley ×4
   Billboard, contrefaçon MBW×Mixmag et Unit1 Studio MBW×Mixmag ont tous un
   cadre VIDE (0,00), une marge énorme et non un réglage à la limite. L'archive
   ne portait que **2 jours** au moment de la mise au point (le module Phase 2A
   est récent) : le critère est conçu pour des semaines et deviendra plus net,
   jamais moins.

   **Ce qu'il ne faut SURTOUT PAS faire, et c'est mesuré** : transposer
   `est_gabarit_de_source` au RÉSUMÉ. Les 2 seules paires qu'il écarterait sont
   deux VRAIS positifs Billboard — trois articles du même média qui couvrent le
   même record partagent forcément leurs mots porteurs, et le critère lit cette
   répétition comme un gabarit de rédaction. `est_gabarit_de_source` reste sur
   les TITRES, exactement tel qu'il est.

   Coût d'un passage à vide : **zéro appel et zéro requête** — l'archive des
   titres est chargée PARESSEUSEMENT, à la première paire qui atteint le
   garde-fou. Il reste ~60 ms de CPU sur 599 articles (11 ms de tokenisation,
   49 ms pour 179 101 intersections d'ensembles), mémorisés par article.

   Une seule exception ASSUMÉE à « rien ne change dans la publication » :
   `elire_representants` efface désormais la raison de doublon d'un article
   qu'elle promeut représentant. Le cas existe sans la bande fraîche et même
   coupe-circuit fermé (`marquer_doublon_manuel --meme-actu` sur deux
   articles frais pose `RAISON_DOUBLON_PREFIXE` sur un doublon dont le
   représentant n'est pas verrouillé) : garder la raison sortirait l'élu de
   la file pour un lien qui n'existe plus. Une raison posée ailleurs (« gel
   impossible : … ») n'est jamais effacée.

   **L'anti-gabarit, qui est la moitié du travail** (`est_gabarit_de_source`).
   Mesuré le 2026-09-24 sur la fenêtre réelle de 48 h (601 articles, 488
   paires dans la bande) : **179 des 255 paires de même source viennent d'un
   seul compte** (AIM UK), qui publie « The winner of *X* at the 2026
   Independent Music Awards » une fois par prix. C'est un gabarit de
   rédaction, pas un signal d'événement, et il faut l'écarter AVANT de payer.
   Le remède naïf — écarter toute paire de même source — est un **piège
   mesuré** : il tuerait 3 des 5 vrais ratés (deux paires Billboard, une
   paire Soompi, une même source qui couvre vraiment deux fois la même
   actu). Le critère est donc la RÉPÉTITION, jamais l'identité de la source :
   les mots que la paire PARTAGE se retrouvent-ils dans au moins
   `cluster_arbitrage_gabarit_min_articles` (**3**) articles de cette source ?
   Deux articles sur un même événement partagent des mots propres à cet
   événement (porteurs = 2) ; un gabarit ressort partout (porteurs = 18 chez
   AIM UK). La distribution mesurée est nette : 44 paires à 2 porteurs, puis
   17 à 3, 12 à 6, 60 à 18. Le critère ne s'applique qu'aux paires de **même
   source**, et c'est vérifié plutôt que supposé : l'étendre aux paires
   croisées écarte 132 paires de plus, dont « Taylor Swift Announces New
   Single *Patient Zero* » (Pitchfork) contre « Taylor Swift has updated her
   Instagram bio » (Pop Crave) — le raté sémantique même que ce chantier
   existe pour attraper. Effet mesuré sur le parcours réel de
   `link_duplicates` : 333 appels sur 48 h sans l'anti-gabarit, **162 avec**,
   soit **~3,4 appels/h** (~0,003 $/h), très loin du plafond de 20 par
   passage.

   Trois limites résiduelles, assumées : une reprise ingérée par `run_veille`
   (toutes les 5 min) reste non arbitrée — donc potentiellement publiable —
   jusqu'au prochain passage horaire, qui seul arbitre ; une paire qui échoue
   à répétition reste redemandée à chaque passage tant que
   `cluster_arbitrage_tentatives_max` n'est pas atteint, avant d'être
   abandonnée pour de bon (sans jamais poser de lien) ; et un verdict manuel
   vaut aussi entre deux articles FRAIS (hors archive), pas seulement contre
   l'archive.

   **Le retrait humain vaut pour l'actu** (2026-10-31, `app/pipeline/retrait_humain.py`,
   reproduit par test et sur un Postgres jetable, **jamais observé en production**). A
   programmé sans post, doublon B mieux classé (`cle_information`). « Ne pas publier »
   libère le créneau et exclut A ; A n'étant plus engagé, `elire_representants`
   promouvait B : la même actu, candidate, programmée. La solution tient en un invariant,
   **le représentant porte le retrait d'un de ses membres**, écrit dans
   `file_exclu_raison` : ni table, ni migration, ni verrou à défaire.
   `_articles_publiables` écarte déjà un exclu et tout doublon, et la carte du
   représentant offre « Remettre dans la file ».
   - **Liste fermée** (`RAISONS_HUMAINES`) : « Ne pas publier » et l'ancien « saute depuis
     le dashboard ». Une exclusion machine ne verrouille jamais un groupe, testé sur les
     cinq formes.
   - `elire_representants` traite un groupe retiré comme un représentant engagé : ni
     réélection, ni remise à noter, donc **aucun appel Claude de plus, plutôt moins**
     (une réélection promouvrait un doublon jamais noté). Il répare aussi une ligne
     ancienne où seul un doublon porte le retrait.
   - `lier` reporte le retrait quand un article retiré passe sous un autre (un article
     plus ancien ingéré après coup, un lien arbitré une heure plus tard) ; un doublon
     arrivé après le retrait se lie à A comme d'habitude et reste doublon.
   - `ne_pas_publier` le reporte aussi quand l'article du créneau est lui-même devenu
     doublon.
   - `purge_old_articles` le transmet aux doublons de A : `ON DELETE SET NULL` en aurait
     fait des représentants libres. **`score_pending` saute un article retiré**, sinon cet
     héritier jamais noté aurait été payé pour rien.
   - **« Remettre dans la file »** efface les retraits de tout le groupe, seuls : « gel
     impossible » reste sur son article. L'élection reprend au passage suivant.

   Le report ne recouvre aucune raison déjà posée et ne touche pas un représentant `USED`.

   **Limites, déduites du code.** Un représentant GELE ou épinglé qui reçoit le retrait par
   un lien tardif garde son créneau : la dédup ne retire rien de ce qui va partir, c'est
   l'humain qui le voit sur la ligne (un créneau PROGRAMME non épinglé est repris par
   l'arbitrage). Une fois TOUT le groupe purgé, plus rien ne se souvient du retrait : une
   reprise ingérée ensuite repart de zéro. Un verdict manuel « différent » défait le lien
   mais pas un retrait déjà reporté : « Remettre dans la file » sur la carte le lève.

## Rétention

**Exception `USED`** : un article marqué posté n'est JAMAIS purgé — c'est l'archive
éditoriale (savoir ce qui a été publié, ne pas le reposter). `REVIEWED` n'est que du
tri et ne protège pas.

On ne garde en base que les articles publiés dans les `retention_days` (**2** depuis le
2026-09-21 ; 14 auparavant) derniers
jours, par DATE DE POST (`published_at`), pas date d'ajout — `app/pipeline/retention.py`.
Purge à chaque run + skip à l'ingestion (on ne score pas un article déjà hors fenêtre).
Articles sans date de publication : filet sur `fetched_at` (seul recours possible).
Récolte X bornée à `x_backfill_days` (2) au premier fetch. Réglables via `.env`.

**La programmation à la main lit cette fenêtre sans la changer** (2026-10-31) : une option
dont la composition tomberait après la purge est refusée. Calcul : `publication.md`,
« Programmer ».

**Un article que la machine n'a PAS PU juger bénéficie d'un SURSIS**
(2026-10-08, `retention_sursis_non_juges_days`, **7 jours**). Le prédicat ne
regardait pas `relevance_score` : un article que Claude n'a jamais pu noter —
parce que le solde API était à zéro — était effacé à `retention_days` (2 jours)
exactement comme un article noté et rejeté. Cela défaisait une règle que
`CLAUDE.md` énonce explicitement (« une panne MACHINE n'exclut JAMAIS un article
de la file — crédits Anthropic épuisés »), tenue dans la file de publication (les
créneaux passent en `manque`, jamais en `exclu` : 0 article exclu sur 7 jours) et
nulle part en amont — l'article n'était pas exclu, il était EFFACÉ.

Mesure du 2026-10-08, en production : la panne de crédits commencée le 2026-10-07
à 15h25 (`credit balance` 2056 fois dans `pipeline.err.log`) a laissé **452
articles sans note** (93 du 08/10, 251 du 07/10, 108 du 06/10 — ces derniers à la
limite de purge le jour de la mesure). Sur ces 452 : **399 jamais jugés**
(`duplicate_of_id IS NULL`) et **53 doublons**. « Jamais jugé » est donc
`relevance_score IS NULL AND duplicate_of_id IS NULL` : un doublon A été jugé —
par la dédup, qui a décidé qu'il répétait une actu déjà représentée — il n'a pas
de note parce qu'il n'en a pas besoin, pas parce que la machine est tombée ; les
53 restent à `retention_days`. Un article noté par le pré-filtre local porte une
note : il est jugé, hors sursis.

**Le sursis est un PLANCHER, jamais une substitution** :
`max(retention_days, sursis_days)`, et c'est le piège principal du réglage. Les
appelants peuvent passer un `retention_days` plus long que le sursis (toute la
suite de tests passe 14) ; s'il le REMPLAÇAIT, un non-jugé tomberait de 14 jours
à 7, c'est-à-dire que le sursis RACCOURCIRAIT la conservation — l'exact inverse
de son intention, et silencieusement. Verrouillé par
`test_le_sursis_est_un_plancher_jamais_une_substitution`.

7 jours couvrent un week-end PLUS une panne. Le coût est borné : en régime normal
ce bucket est quasi vide (un article est noté dans l'heure qui suit son
ingestion), et au pire d'une panne totale de 7 jours il retient l'ingestion d'une
semaine, soit ~650 lignes à la cadence mesurée (647 articles ingérés sur 7
jours). Le sursis vit DANS `_predicat_purgeable`, donc la purge des événements le
suit par construction — sinon elle supprimerait l'événement d'un article qui
RESTE en base. Aucune migration de données : les 399 articles en attente sont
couverts d'office par le prédicat.

**Les événements sans valeur sont purgés, et l'ORDRE est l'invariant**
(2026-09-24, phase 2B Task 2, `retention.purger_evenements_sans_valeur`). Depuis
la Task 1, TOUT article devient l'observation d'un événement, même seul : sans
purge, c'est un événement permanent par article de bruit. La règle, tranchée par
l'utilisateur : **une seule** observation, article sous le seuil de pertinence
(`digest_relevance_threshold`, 60 — le seuil général du dépôt, pas un réglage de
plus ; une note absente vaut « sous le seuil ») et jamais publié → l'événement
meurt avec son article ; **deux observations ou plus** → permanent, sans
condition ; **publié** (`USED`, un `Post`, un `Creneau` engagé) → permanent,
sans condition. Cette dernière règle n'est écrite qu'à UN endroit,
`election.articles_engages` (version ensembliste : trois requêtes quel que soit
le lot), appelée aussi par `representant_engage` — deux copies qui divergeraient
feraient disparaître en silence l'archive d'une actu publiée, et un test
verrouille l'accord des deux chemins.

`event_observations.article_id` est `ON DELETE SET NULL` : une fois l'article
purgé, l'observation survit sans lui et **plus rien ne permet de juger sa
pertinence**. La décision doit donc être prise AVANT `purge_old_articles`, dans
le même passage — l'ordre est posé dans `app/pipeline/run.py` et verrouillé par
`tests/pipeline/test_retention_evenements.py` (inverser les deux appels rend ce test
rouge). Les deux étapes visent le même ensemble par CONSTRUCTION
(`_predicat_purgeable`, un seul prédicat partagé) : deux copies qui
divergeraient laisseraient des événements derrière elles, ou pire en
supprimeraient un dont l'article reste en base.

Coût d'un passage à vide : UNE requête qui rend zéro ligne (la recherche part
des ARTICLES que la purge va supprimer, jamais de la table `events`, que rien ne
borne). Un passage chargé coûte au plus quatre requêtes de plus, quel que soit
le nombre de candidats — rien n'est fait article par article. Ne lève jamais
(`rollback` en filet, `PurgeEvenementsStats.erreur` remonte dans
`report.problemes`).
