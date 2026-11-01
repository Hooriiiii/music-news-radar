# Scoring : deux axes, qui parle, garde-fous

**Quand lire ce fichier** : avant de toucher a `app/pipeline/scoring.py`, au
`SYSTEM_PROMPT`, au pre-filtre local, a `sources.categorie` ou a un garde-fou
deterministe. Il explique pourquoi la note est DOUBLE (`relevance` /
`emergence`) et pourquoi un axe unique detruit le signal du label, ce que la
categorie du compte change a la note, et pourquoi le code tranche APRES le
modele.

La ligne editoriale elle-meme (coeur electro, pop adjacente et conditionnelle,
quatre axes transversaux) est dans `CLAUDE.md`, section « Projet ».

## Deux axes de scoring (relevance / emergence)

Le scoring rend DEUX notes indépendantes, qui ne se compensent jamais :
- `relevance` — intérêt pour le feed Instagram (pilier « grosses actus ») ;
- `emergence` — intérêt A&R : « cet artiste mérite-t-il d'être suivi par Kinda ? »
  (pilier « artistes locaux/émergents »).

Le cas qui justifie tout : un inconnu qui sort un premier EP est du bruit pour le
feed (relevance 20) et un signal fort pour le label (emergence 75). L'ancien prompt
notait « autopromo d'inconnu » sous 40 et **détruisait donc exactement le signal dont
le label a besoin** — ne jamais revenir à un axe unique. Priorité explicite aux
artistes français / à la scène locale sur l'axe emergence.
API : `GET /articles?sort=emergence&min_emergence=...`.

**La note est juste, son ATTRIBUTION l'était pas** (2026-10-03) : la note
d'émergence d'un article était recopiée sur chaque artiste cité, donc le parrain
célèbre montait comme son filleul inconnu. Ne pas toucher au prompt pour ça — voir
« Mémoire A&R ».
## Qui parle : `sources.categorie` (décision du 2026-09-17)

Le feed priorise les posts des COMPTES D'ARTISTES (électro, musiques dures — hard
techno, hardstyle, hardcore —, pop à tendance électro) : un artiste qui annonce sa
sortie est l'actu primaire. **Depuis le 2026-09-17, l'INFO d'un compte MÉDIA
(Guettapen, Tsugi, Mixmag, DJ Mag, Billboard, Booska-P…) n'est plus décotée** :
la décote de 15-20 points et le plafond à 59 ont enterré la BO de GTA VI (Fred
again..) à 45-59, jamais atteinte à cause d'un représentant de doublon mal choisi
(voir « Dédup à deux étages »). Kinda reprend l'information, jamais la
formulation ni les VISUELS du média (voir « Visuels : l'info des médias, les
images des artistes »). Festivals/labels/clubs : annonce officielle = source
primaire, promo courante 40-55. Un post SANS INFORMATION (légende vide — titre
« Post Instagram » —, emojis/hashtags seuls, selfie, « link in bio », teaser
d'idole sans fait) note sous 30 quel que soit le compte. L'axe emergence n'est
pas touché.

Mécanique : colonne `sources.categorie` (VARCHAR + CHECK, migration 0016, NULL =
inconnu) ; `build_user_prompt` passe « Format : » (post Instagram, article de
presse, post Reddit, tweet) et « Type de compte : » ; règles 5 et 6 du
`SYSTEM_PROMPT`. Éditable au dashboard (panneau Sources), via `PATCH /sources/{id}`
et `add_source --categorie`.

**Pré-filtre local** : même prompt, plus `CONSIGNE_PREFILTRE` — ne descendre sous
45 que pour le bruit évident, l'actu hors ligne et les posts réellement vides :
un contenu qui porte un fait vaut 45 ou plus, quel que soit le compte. **Deux cas
ne sont jamais écartés du passage à Claude**, même sous 45 en local : une actu
dont le groupe de doublons réunit au moins `cluster_sources_claude` (3) sources
distinctes (raison `cluster`), et un article qui cite un artiste suivi (raison
`artiste_suivi`) — `ContexteScoring` (`app/pipeline/scoring.py`), compteurs
`forces_cluster` / `forces_artiste` dans `ScoringStats`. Le post quasi vide
(garde-fou 1 ci-dessous) reste local dans tous les cas : aucune des deux
exceptions ne le fait passer à Claude.

Classement des sources existantes : `uv run python -m scripts.sources.categoriser_sources
[--apply]` (sans option : le plan). Mapping explicite des comptes Instagram connus
(médias, festivals, labels, clubs — Keinemusik est un collectif, donc artiste),
tout autre compte Instagram = artiste, RSS/Reddit = média, X = NULL. Le mapping
explicite fait foi ; les défauts ne remplissent qu'une catégorie vide, pour ne
jamais écraser un classement manuel. Idempotent. Conséquence assumée : les flux RSS
de presse passent en `media` et notent donc plus bas qu'avant.

**Garde-fous déterministes (2026-09-17).** Le prompt ne suffit pas : mesuré le jour
même sur données réelles, Haiku applique les règles 5 et 6 de façon inégale — IVE
« [📸] » 65, « 🗂️ I-VIEW » 65, LE SSERAFIM « feat. » 72, et des Guettapen
ordinaires au-dessus du plafond (« LA SANTÉ MENTALE DE PETIT BISCUIT AU PLUS BAS »
68, « LE NÉERLANDAIS REVIENT SUR SON ÉVOLUTION » 70), alors que « LA LOI RIPOST MET
EN DANGER LA SCÈNE ÉLECTRONIQUE FRANÇAISE » (75) doit rester haut. Le code tranche
donc APRÈS le modèle, sur tous les chemins (Claude et pré-filtre local), dans
`scoring.appliquer_garde_fous(article, score)` (pure, rend une copie) :
1. **Post Instagram quasi vide** (`post_quasi_vide`) : moins de 6 mots
   significatifs dans la caption → relevance et hotness plafonnées à 29, emergence
   intacte. Un mot compte s'il contient une lettre ou un chiffre, après NFKC (les
   lettres « mathématiques » 𝚖𝚊𝚍𝚎 redeviennent ASCII) et retrait des URLs,
   hashtags et @mentions (`instagram.mots_significatifs`). La caption est relue
   dans `article.summary` sans le bandeau de métriques
   (`caption_depuis_summary`) — donc AVANT `apply_score`, qui écrase le summary.
   Sans caption connue, seul le titre « Post Instagram » fait foi.
   Coût : un post quasi vide n'est **jamais envoyé à Claude**. Note locale du
   pré-filtre si elle existe (plafonnée, son emergence est gardée), sinon note
   minimale (10/10/0, `other`, la caption en résumé) ; `scored_by = local`,
   compté dans `prefiltered` et `quasi_vides`. `actu_majeure` a été retiré du
   schéma le 2026-09-17 (jamais persisté, aucune migration) : plus de décote
   média à lever, donc plus de champ pour ça — `ArticleScore` l'ignore
   silencieusement si un ancien appel ou une réponse en cache le porte encore
   (Pydantic ignore les clés inconnues).

Nettoyage de la file sans rien payer : `uv run python -m scripts.reparations.appliquer_garde_fous
[--titres-courts] [--apply]`. Ne re-applique QUE le garde-fou 1. Une note déjà
posée a perdu sa caption : par défaut seul le titre « Post Instagram » est visé
(certain) ; `--titres-courts` vise aussi les titres de moins de 6 mots (probable
seulement — le titre n'est que la première phrase). Les articles USED ne sont
jamais touchés.

- Rattraper les actus récentes enterrées par l'ancienne notation (décote média,
  représentant de doublon mal élu) :
  `uv run python -m scripts.reparations.reevaluer_actus [--jours 3] [--limite 100] [--apply]`
  (sans `--apply` : le plan, aucune écriture ni appel Claude ; avec : élection
  des représentants sur la fenêtre, notation des articles mis en attente par
  cette élection, puis re-notation Claude — sans pré-filtre — du plan : articles
  notés en local dont le groupe atteint 3 sources ou qui citent un artiste
  suivi, et articles déjà notés par Claude en compte média entre 40 et 59 quand
  un artiste suivi est cité, la note dépasse 50, ou le groupe atteint 2 sources).
  Coût : l'élection garde le plafond d'un run ordinaire
  (`cluster_reevaluations_max_par_run`, 10) et chacune des deux phases payées est
  bornée par `--limite` — au pire `10 + 2 × --limite` appels, et le budget
  réellement dépensé est affiché par phase à la fin.
- Lier une fiche artiste à son compte suivi quand le nom ne suffit pas :
  `PATCH /artists/{id}` avec `{"source_instagram_id": <id de la source>}`
- Réparer un faux positif (ou confirmer un lien) de doublon d'archive :
  `uv run python -m scripts.reparations.marquer_doublon_manuel --article-a ID --article-b ID
  (--different|--meme-actu) --raison "..."` — voir « Dédup à deux étages » pour
  pourquoi effacer `duplicate_of_id`/`file_exclu_raison` seul ne suffit pas.
- Délier les articles devenus LEUR PROPRE doublon (3 lignes mesurées en
  production le 2026-10-02) : `uv run python -m
  scripts.reparations.reparer_doublons_reflexifs [--apply]` (sans `--apply` : le plan ;
  idempotent ; les notes ne sont jamais touchées) — voir « Dédup à deux
  étages » pour la cause et les deux gardes qui l'empêchent de revenir.

## Ordre de notation et arret au premier refus (2026-11-01)

`score_pending` note du **plus recent au plus ancien** (`fetched_at` puis `id`,
decroissants). Avant, apres une panne, la veille (20 articles par passage) et le
pipeline notaient l'arriere vieux avant le frais, alors que seul le frais peut donner un
post. Pas de famine : seule la veille passe une limite, et le passage horaire prend le
reste. La purge ne depend pas de cet ordre : un article laisse de cote reste « jamais
juge », sous le sursis de 7 jours. Effet sur le bandeau : voir `articles_jamais_atteints_par_le_scoring`
dans `dashboard.md`, le faux negatif sur l'arriere ancien s'est elargi.

Le **premier refus de credits arrete le passage**, apres consignation : chaque article
pris faisait un appel refuse, 4 000 a 14 600 par jour dans les logs. `errors` vaut alors
souvent 1, et `health.diagnostiquer` nomme quand meme « crédits API épuisés », car il lit
`derniere_erreur`. Une coupure ou une autre erreur n'arrete rien. Contrepartie : les
notes locales restantes du passage (pre-filtre, post quasi vide) attendent aussi la
recharge. `reevaluer_actus.py` arrete son plan de la meme facon, car il appelle Claude
sans passer par `score_pending`.

## Modele de notation : cache, decouplage, mesure avant tout passage a 5.5

**Le defaut reste `claude-haiku-4-5` tant que la mesure n'est pas faite.** Rien ci-dessous
n'est un resultat : ces lignes decrivent la procedure, aucun chiffre n'a encore ete releve.

- **Cache** : `make_claude_scorer` pose `cache_control` sur `SYSTEM_PROMPT`, quel que soit
  le modele. Sous le minimum (4 096 tokens sur Haiku 4.5, 512 sur 5.5), l'API l'ignore sans
  erreur. L'effet se lit dans `cache_read_input_tokens`, via `consommation_api`.
- **Effort** : `scoring_effort` (`low`) n'est envoye qu'aux modeles `claude-haiku-5*` : il
  provoque une erreur sur 4.5. `max_tokens` passe a 2000 : la reflexion de 5.5 le consomme.
- **Reponse sans note** : un refus de securite est une reponse 200, sans repli sur 5.5.
  `parsed_output` vide leve `ReponseSansNote`, qui nomme `stop_reason`. L'article reste
  sans note et n'est pas exclu, donc il repart au passage suivant, et chaque essai est
  facture, jusqu'a la purge. Elle compte en succes de credits : une reponse prouve le solde.
  Le SDK valide le JSON avant de rendre la main : un JSON coupe ou un refus en texte levent
  plutot `ValidationError`, sans `stop_reason`.
- **Decouplage** : l'arbitrage de doublons suit `arbitrage_model`, plus `scoring_model`.
  Changer le modele de notation ne deplace donc pas une decision mesuree sur Haiku 4.5.

**Procedure** : `uv run python -m scripts.diagnostic.mesurer_modele_notation` affiche le
plan et le cout estime ; `--apply` appelle Claude, n'ecrit jamais en base, et consigne sous
le poste `mesure_notation`. Arreter pipeline et veille avant, sinon la recharge part dans
l'arriere ; le script ne les arrete pas. Echantillon : articles NON notes (une note ecrase
le resume brut, donc on ne re-note pas a entrees egales), sans les posts quasi vides.
Trois passes par article : 4.5, 4.5, 5.5. La seconde passe 4.5 mesure le BRUIT du modele
contre lui-meme : l'ecart 4.5/5.5 se juge contre lui, pas contre un seuil a l'oeil. Tout
s'arrete, code de sortie 1, au premier refus de credits ou rejet definitif d'un modele
(HTTP 400, 401, 403, 404) : un 5.5 inaccessible ne coute que les deux passes 4.5 du premier
article. Le script ne rend pas de verdict. Le passage se fait par `SCORING_MODEL` une fois les chiffres
lus, et ils s'ecrivent ici.
