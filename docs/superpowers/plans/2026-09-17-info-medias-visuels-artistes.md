# L'info des médias, les visuels des artistes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Une grosse actu relayée par des médias (GTA VI × Fred again..) n'est plus perdue : la presse n'est plus décotée à la notation, le pré-filtre local n'écarte jamais une actu couverte par au moins trois sources ni un article qui cite un artiste suivi, le représentant d'un groupe de doublons est l'article le plus informatif ; et les visuels des comptes Instagram médias ne sont plus jamais publiés — on les remplace par les posts des artistes cités (en base, sinon lus à la composition), crédités « 📸 @artiste », faute de quoi l'article reste au dashboard mais sort de la file.

**Architecture:** Partie A (notation) : `appliquer_garde_fous` perd le plafond média, `actu_majeure` disparaît (jamais persisté, aucune migration) ; un index des comptes d'artistes suivis (`app/pipeline/comptes_artistes.py`) sert à la cascade (`ContexteScoring` dans `score_pending`) et à une élection du représentant (`app/pipeline/election.py`, après `link_duplicates`, bornée) ; `scripts/reevaluer_actus.py` rattrape les actus récentes. Partie B (visuels) : `app/content/visuels.py` (politique, actifs, cache local), `actifs_artistes.py` (posts d'artistes en base + heuristique), `lecture_artistes.py` (`business_discovery` en direct, bornée), `choix_visuels.py` (le choix, avant l'appel Claude) ; `posts.assets` (migration 0018) porte le choix, `posts.credit` les handles. Partie C : éligibilité visuelle dans la file et le runner, API et dashboard.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic, httpx (sync), Pydantic, Playwright/Chromium (rendu existant), pytest, uv.

**Vérification du plan (2026-09-17)** : le code et les tests des tâches 1 à 13 ont été appliqués tels qu'écrits sur une copie jetable du dépôt — `uv run ruff check .` propre, `uv run pytest` 1108 tests verts ; la migration 0018 a passé `upgrade`, `alembic check` et `downgrade 0017` sur un cluster Postgres jetable. Les modifications de tests existants nécessaires (garde-fous, prompt, tête de migration, crédit « via », fixtures Instagram média) sont listées dans les tâches concernées.

**Spec:** `docs/superpowers/specs/2026-09-17-info-medias-visuels-artistes-design.md` — lire d'abord « Mesures », « 1.4 Le représentant le plus informatif », « 2.3 Heuristique », « 2.4 Lecture des comptes » et « Questions ouvertes » (les défauts proposés sont appliqués ici).

## Global Constraints

- **Aucun appel réseau dans les tests** : ni Meta, ni Anthropic, ni téléchargement, ni yt-dlp. Lecteur Graph, scorer, téléchargeur injectés. `conftest.py` redirige `meta_token_path`, `publication_mode_path`, `publication_dir` et (tâche 2) **`visuels_cache_dir`** dans `tmp_path`.
- **Notation** : plus aucune décote ni plafond selon `sources.categorie`. Garde-fou 1 inchangé : post Instagram quasi vide → relevance et hotness ≤ **29**, emergence intacte, jamais envoyé à Claude. `ArticleScore` n'a **plus** de champ `actu_majeure` ; une réponse qui le porte encore reste valide (Pydantic ignore les clés inconnues).
- **Cascade** : seuil du pré-filtre inchangé (`local_prefilter_threshold = 45`, strict). Sous le seuil, l'article part quand même chez Claude si le groupe dont il est représentant compte **≥ `cluster_sources_claude = 3` `source_id` distincts** (représentant compris) — raison `cluster` —, ou s'il cite un compte d'artiste suivi (hors sa propre source) — raison `artiste_suivi`. Le post quasi vide reste local dans tous les cas.
- **Clé d'artiste** `cle_artiste(nom)` : parenthèses retirées (`re.compile(r"\s*\([^)]*\)")`), `@` initial retiré, puis `app.pipeline.cluster.normalize_title`. Longueur minimale **4**. Entrées de l'index : sources `instagram` + `artiste` + actives (clé du nom, clé du handle `compte_depuis_url`), puis fiches `artists` liées (`source_instagram_id`), qui **écrasent**. Citation : nom rendu par un modèle → clé exacte ; texte libre → clés d'**au moins deux mots** en mots entiers dans le texte normalisé, et `@handle` (insensible à la casse, suivi d'aucun `[\w.]`) ; jamais une clé d'un mot dans le texte libre.
- **Élection** (`elire_representants`) : fenêtre **48 h** (`cluster.DEFAULT_WINDOW_HOURS`) sur la date des DOUBLONS ; clé d'information, la plus grande gagne : `(artistes suivis cités dans le titre, source primaire artiste/festival/label/club, visuels autorisés, len(significant_tokens(titre)), -horodatage, -id)`. Verrou : `USED`, un `Post`, ou un `Creneau` non `LIBRE` sur le représentant actuel → pas de changement. Remise à noter d'un représentant `local` de groupe ≥ 3 sources dont le titre ≠ « Post Instagram ». Borne **`cluster_reevaluations_max_par_run = 10`** (réélections + remises à noter). `appliquer=False` ne modifie rien.
- **Visuels interdits** : `origine_interdite(source)` ⇔ `source.type == instagram` **et** `source.categorie == media`. Tout le reste est autorisé (RSS, Reddit, Instagram artiste/festival/label/club, catégorie nulle).
- **Actif** = entrée média complète (`type, url, preview_url, duration_ms, width, height`) + `origine` (`article` | `artiste`), `compte` (`@handle` | `None`), `permalink`, `article_id`, `publie_le` (ISO | `None`), `fichier` (`file://…` | `None`). `posts.assets` : liste (réassigner en entier) ; `NULL` = post antérieur. `posts.credit` : handles distincts des actifs séparés par une espace, ou `None`.
- **Heuristique** : candidats = posts des comptes cités seulement ; fenêtre dure `[date article − visuels_artiste_fenetre_jours (10), maintenant]` ; « sur le sujet » = recouvrement ≥ 1 entre `significant_tokens(titre article) − tokens(nom) − tokens(handle)` et `significant_tokens(titre + résumé du post)` ; hors sujet accepté seulement à ≤ `visuels_artiste_generique_jours (5)` de la date de l'article ; tri `(hors sujet, -recouvrement, |écart de date|, -article_id)`. Carrousel : ≤ `visuels_max_photos (3)` images copiées en local. Reel : première vidéo (directe 3-90 s ou durée inconnue, sinon récupérable yt-dlp), jamais copiée.
- **Lecture Graph (b)** : carrousel seulement, à la composition seulement, si aucun actif **sur le sujet** en base ; compte lu par le pipeline depuis < `instagram_intervalle_heures` → pas d'appel ; ≤ `visuels_lectures_max_par_passage (2)` par lecteur (neuf à chaque passage), ≤ `visuels_lectures_max_par_jour (24)` ; cache par handle `visuels_cache_lectures_heures (6)` ; codes Graph **4, 17, 32, 613** → pause `visuels_pause_quota_minutes (60)` ; état `visuels_lectures.json` dans `settings.meta_token_path.parent`, écrit atomiquement ; jamais le jeton dans un message ; posts lus **jamais insérés** en base.
- **Cache d'images** : `visuels_cache_dir` (défaut `~/Library/Application Support/Kinda/visuels`), nom = 32 premiers hex du SHA-256 de l'URL + extension ; lecture seulement d'un `file://` résolu **dans** ce dossier ; ménage > `visuels_cache_jours (7)`.
- **Composition** : le choix des visuels précède l'appel payé à Claude. `AucunVisuel` → exclusion « composition impossible : … » ; `VisuelsDifferes` → raison « composition differee : … » sur le créneau, ni exclusion ni notification. Brouillon repris seulement s'il est conforme (`assets` non NULL, ou origine autorisée).
- **Légende** : `📸 <credit>` (`🎥` pour `kinda_reel`), **sans « via »** ; `Source : <nom du média sans parenthèses> · <url>`, ou `Source : <url>` sans nom ; crédit et source jamais coupés.
- **File** : `raison_sans_visuel(article, index=None)` sans réseau remplace `_a_une_image` comme garde carrousel ; reel : chemin propre inchangé, sinon vidéo d'artiste en base.
- Python sync. Tests SQLite in-memory. **SQLite rend des `datetime` naïfs** : comparer via `app.schedule.store.utc()` (ou un `_utc` local dans les modules que `store` importe, pour éviter un cycle).
- Enums VARCHAR + CHECK ; `op.f()` **dans** les fonctions de migration uniquement ; JSON réassigné en entier ; en-têtes HTTP ASCII.
- Imports sans cycle : `store` importe `actifs_artistes`, `visuels`, `comptes_artistes` ; **jamais** `choix_visuels`. `comptes_artistes` importe `cluster` ; `election` importe `cluster` et `comptes_artistes`. `visuels` n'importe que `config`, `media`, `models`.
- Commentaires en français. Messages de commit en français **sans accents**, terminés par :
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
  ```
- `uv run ruff check .` et `uv run pytest` passent à la fin de chaque tâche.
- **Rien n'est publié, rien n'est re-noté en production pendant l'implémentation** : aucun agent ne lance `scripts.reevaluer_actus --apply`, `scripts.tester_publication`, ne change le mode, ni n'appelle Meta ; lectures de la base de production interdites hors SELECT explicitement demandé.

## Écarts assumés, et pourquoi

1. **`link_duplicates` ne change pas** : les liens se forment toujours contre le plus ancien, l'élection est une étape séparée. Changer la liaison elle-même rendrait le rattachement dépendant de l'ordre d'arrivée et casserait l'idempotence testée.
2. **Clé d'information sur le titre et la source seulement** : le résumé est réécrit par le scoring, une clé qui le lirait ferait osciller le représentant d'un run à l'autre.
3. **Seuil en sources distinctes**, pas en articles : deux posts du même compte ne font pas une actu « couverte ».
4. **Une clé d'artiste d'un seul mot n'est jamais cherchée en texte libre** (« Justice », « Future ») : elle ne cite que par la liste d'artistes d'un modèle ou `@handle`.
5. **Pas de lecture Graph pour un reel** : l'éligibilité d'un reel se décide à l'arbitrage, sans réseau ; la vidéo d'artiste vient de la base.
6. **Visuels propres non copiés en local** : comportement actuel conservé (le gel télécharge) ; seuls les actifs d'artistes, choisis ~22 h avant le gel sur des URL CDN signées, sont copiés.
7. **Composition manuelle sans visuel autorisée** (post sans fond) : la décision 7 ne vise que la file.
8. **Plan de rattrapage filtré** (média 40-59 ET artiste suivi, ≥ 50 ou groupe ≥ 2 sources) plutôt que tout re-noter (226 articles sur 3 jours).
9. **« Source : nom · url »** : le nom pour le lecteur, l'URL pour la traçabilité (question ouverte 1, défaut).

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `app/pipeline/scoring.py` (modifier) | Plus de plafond média, plus d'`actu_majeure`, règle 5 réécrite ; `ContexteScoring` et règles R2/R3 dans `score_pending` |
| `app/pipeline/prefilter.py` (modifier) | `CONSIGNE_PREFILTRE` sans décote |
| `scripts/appliquer_garde_fous.py` (modifier) | Docstring et message sans plafond média |
| `app/config.py` (modifier) | Réglages `cluster_*` et `visuels_*` |
| `app/models.py` (modifier) | `Post.assets`, `Artist.source_instagram_id` |
| `alembic/versions/0018_visuels_artistes.py` (créer) | Les deux colonnes |
| `tests/conftest.py` (modifier) | `visuels_cache_dir` dans `tmp_path` |
| `app/pipeline/comptes_artistes.py` (créer) | `cle_artiste`, `CompteArtiste`, `IndexComptes`, `construire_index`, `lier_artistes_comptes` |
| `app/pipeline/election.py` (créer) | `tailles_clusters`, `cle_information`, `representant_engage`, `remettre_a_noter`, `elire_representants` |
| `app/pipeline/run.py` (modifier) | Élection après la dédup, liaison des artistes après les mentions |
| `scripts/reevaluer_actus.py` (créer) | Rattrapage : plan, `--apply` |
| `app/content/visuels.py` (créer) | Politique, actifs, crédit, cache local |
| `app/content/credit.py`, `compose.py`, `render.py` (modifier) | Origine interdite, fond, lecture du cache |
| `app/publish/preparation.py`, `preparation_reel.py` (modifier) | Photos autorisées, garde du fond, porteur de la vidéo |
| `app/content/actifs_artistes.py` (créer) | `PostArtiste`, `Retenu`, `medias_utilisables`, `recouvrement`, `classer`, `posts_en_base` |
| `app/sources/instagram.py` (modifier) | `CHAMPS_MEDIA`, `url_business_discovery`, `lire_compte`, `items_depuis_payload`, `QuotaGraphAtteint` |
| `app/content/lecture_artistes.py` (créer) | `LecteurComptes`, `LectureDifferee` |
| `app/content/choix_visuels.py` (créer) | `ChoixVisuels`, `AucunVisuel`, `VisuelsDifferes`, `choisir_visuels` |
| `app/content/legende.py` (modifier) | Crédit sans « via », source nommée |
| `app/schedule/store.py` (modifier) | `raison_reel_propre`, `raison_sans_visuel`, `raison_ineligible_reel` étendue, `etiquette_visuel` |
| `app/schedule/runner.py` (modifier) | Choix avant composition, brouillons non conformes, ménage du cache |
| `scripts/run_publication.py` (modifier) | `choix_du_passage()` |
| `app/api/posts.py`, `articles.py`, `artists.py`, `schedule.py`, `app/schemas.py` (modifier) | Visuels exposés, lien artiste ↔ compte, refus manuel |
| `app/static/dashboard.html` (modifier) | Pastille d'origine, actifs retenus, médias interdits grisés |
| `CLAUDE.md` (modifier) | Documenter |

---

## Partie A — Notation et regroupement

### Task 1: Plus de décote ni de plafond pour les médias

**Files:**
- Modify: `app/pipeline/scoring.py`, `app/pipeline/prefilter.py`, `scripts/appliquer_garde_fous.py`
- Test: `tests/pipeline/test_garde_fous.py`, `tests/pipeline/test_scoring.py`

**Interfaces:**
- Consumes: rien de nouveau
- Produces:
  - `ArticleScore` **sans** `actu_majeure`
  - `appliquer_garde_fous(article: Article, score: ArticleScore) -> ArticleScore` — garde-fou 1 seulement
  - `PLAFOND_MEDIA` **supprimé** ; `PLAFOND_POST_VIDE = 29` inchangé
  - `CONSIGNE_PREFILTRE` réécrite (contient toujours « 45 »)

- [ ] **Step 1: Write the failing tests**

Dans `tests/pipeline/test_garde_fous.py`, **supprimer** toute la section qui commence à `# --- Garde-fou 2 : plafond media, sauf actu majeure ---` et se termine après `test_le_prompt_definit_actu_majeure` (constantes `GUETTAPEN_EVOLUTION`, `GUETTAPEN_RIPOST` comprises), ainsi que les trois tests `test_media_note_par_claude_est_plafonne`, `test_le_plafond_media_ne_fait_pas_ecarter_par_le_prefiltre` et `test_note_locale_sous_le_seuil_est_aussi_plafonnee`. Mettre à la place de la section supprimée :

```python
# --- Plus de plafond media (decision du 2026-09-17) ---
# L'actu GTA VI x Fred again.. a ete enterree a 45-59 : la decote et le plafond
# punissaient l'information selon le canal. Le plagiat portait sur les visuels et
# la formulation, traites ailleurs.

GUETTAPEN_MENTAL = (
    "LA SANTÉ MENTALE DE PETIT BISCUIT AU PLUS BAS 😔\n\nLe producteur francais se confie."
)


@pytest.mark.parametrize(
    "categorie",
    [SourceCategorie.MEDIA, SourceCategorie.ARTISTE, SourceCategorie.FESTIVAL, None],
)
def test_la_categorie_ne_plafonne_plus_la_note(categorie):
    a = article(GUETTAPEN_MENTAL, categorie=categorie)
    resultat = appliquer_garde_fous(a, note(68, hotness=70, emergence=33))
    assert (resultat.relevance, resultat.hotness, resultat.emergence) == (68, 70, 33)


def test_la_presse_rss_garde_sa_note():
    a = article("Titre de presse", type_=SourceType.RSS, categorie=SourceCategorie.MEDIA)
    assert appliquer_garde_fous(a, note(68)).relevance == 68


def test_un_post_vide_de_media_reste_plafonne():
    a = article("🚨🚨", categorie=SourceCategorie.MEDIA)
    assert appliquer_garde_fous(a, note(80)).relevance == 29


def test_le_prompt_ne_decote_plus_les_medias():
    from app.pipeline.scoring import SYSTEM_PROMPT

    assert "PLAGIAT" not in SYSTEM_PROMPT
    assert "plafonne" not in SYSTEM_PROMPT
    assert "actu_majeure" not in SYSTEM_PROMPT
    assert "AUCUNE décote" in SYSTEM_PROMPT


def test_actu_majeure_n_existe_plus_dans_le_schema():
    assert "actu_majeure" not in ArticleScore.model_json_schema()["properties"]


def test_une_reponse_qui_porte_encore_actu_majeure_reste_valide():
    """Un pre-filtre local charge avec l'ancien schema peut encore l'envoyer."""
    brut = (
        '{"relevance": 70, "hotness": 50, "emergence": 10, "category": "announcement",'
        ' "summary": "R.", "artists": [], "actu_majeure": true}'
    )
    assert ArticleScore.model_validate_json(brut).relevance == 70
```

Et à la place des trois tests `score_pending` supprimés :

```python
def test_media_note_par_claude_garde_sa_note(db_session):
    a = _persister(db_session, GUETTAPEN_MENTAL, categorie=SourceCategorie.MEDIA)
    score_pending(db_session, scorer=lambda x: note(68, hotness=70))
    db_session.refresh(a)
    assert (a.relevance_score, a.hotness_score) == (68, 70)
    assert a.scored_by == ScoredBy.CLAUDE


def test_note_locale_d_un_media_n_est_plus_plafonnee(db_session):
    a = _persister(db_session, GUETTAPEN_MENTAL, categorie=SourceCategorie.MEDIA)
    score_pending(db_session, scorer=scorer_interdit, prefilter=lambda x: note(30, hotness=90))
    db_session.refresh(a)
    assert (a.relevance_score, a.hotness_score) == (30, 90)
```

Retirer `from app.config import settings` des imports de `tests/pipeline/test_garde_fous.py` (plus utilisé : ruff F401).

Dans `tests/pipeline/test_scoring.py`, `test_system_prompt_priorise_les_comptes_d_artistes`, remplacer

```python
    assert "plagiat" in lowered
    assert "filet de sécurité" in lowered
```

par

```python
    # 2026-09-17 : l'info d'un media n'est plus decotee (le plagiat portait sur les visuels)
    assert "plagiat" not in lowered
    assert "aucune décote" in lowered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_garde_fous.py tests/pipeline/test_scoring.py -v`
Expected: FAIL — `test_la_categorie_ne_plafonne_plus_la_note[SourceCategorie.MEDIA]` (59 != 68), `test_le_prompt_ne_decote_plus_les_medias`, `test_system_prompt_priorise_les_comptes_d_artistes`, `test_actu_majeure_n_existe_plus_dans_le_schema`, `test_media_note_par_claude_garde_sa_note`, `test_note_locale_d_un_media_n_est_plus_plafonnee`.

- [ ] **Step 3: Implement scoring.py**

Dans `ArticleScore`, supprimer les quatre lignes du champ `actu_majeure` (commentaire compris).

Dans `SYSTEM_PROMPT`, remplacer tout le point 5 (de `5. QUI PARLE compte autant que ce qui est dit.` jusqu'à `   - inconnu ou autre : juge sur le contenu seul.` inclus) par :

```text
5. QUI PARLE. Chaque contenu indique son « Type de compte » et son « Format ». \
Ces règles portent sur relevance (et hotness), jamais sur emergence :
   - artiste : l'artiste qui annonce SA sortie, SA date, SON projet est une source \
PRIMAIRE. Note-la selon la grille.
   - media (Guettapen, Tsugi, Mixmag, DJ Mag, Electro News, Pop Crave, Konbini, \
Booska-P, AllTime EDM, Billboard, FIP...) : Kinda reprend l'INFORMATION, jamais la \
formulation ni les visuels du média. Note l'information elle-même, exactement comme \
si elle venait de la source primaire : AUCUNE décote parce qu'elle arrive par un \
média. Une actu reprise par plusieurs médias est souvent une GROSSE actu.
   - festival / label / club : une annonce OFFICIELLE (line-up, dates, billetterie, \
annulation, sortie) est une source primaire et garde sa note ; la promo courante \
(rappel de billetterie, compte à rebours, visuel d'ambiance) vaut 40-55.
   - inconnu ou autre : juge sur le contenu seul.
```

Dans la liste des sorties, supprimer les trois lignes `- actu_majeure (booléen) : voir la règle 5 — …` jusqu'à `… ; sinon false.`

Remplacer le bloc des garde-fous (de `# --- Garde-fous déterministes` jusqu'à la fin de `appliquer_garde_fous`) par :

```python
# --- Garde-fou déterministe ------------------------------------------------------
# Mesuré le 2026-09-17 sur données réelles : Haiku applique la règle 6 de façon
# inégale (IVE « [📸] » 65, LE SSERAFIM « feat. » 72). Le code tranche donc après
# le modèle, sur tous les chemins de scoring. Le plafond des comptes media a été
# retiré le même jour : il enterrait des grosses actus (GTA VI × Fred again..).
PLAFOND_POST_VIDE = 29


def post_quasi_vide(article: Article) -> bool:
    """Un post Instagram dont la légende ne porte pas d'information.

    Lit la caption dans `article.summary` : à appeler AVANT `apply_score`, qui
    remplace le summary par le résumé du modèle. Sans caption connue, seul le
    titre par défaut de l'adapter (« Post Instagram ») fait foi.
    """
    source = article.source
    if source is None or source.type != SourceType.INSTAGRAM:
        return False
    if article.summary is None:
        return article.title == TITRE_PAR_DEFAUT
    return caption_quasi_vide(caption_depuis_summary(article.summary))


def appliquer_garde_fous(article: Article, score: ArticleScore) -> ArticleScore:
    """Post Instagram quasi vide -> relevance et hotness plafonnées à 29, quel que
    soit le compte. L'axe emergence n'est jamais touché. Fonction pure : rend une
    copie."""
    if not post_quasi_vide(article):
        return score
    return score.model_copy(
        update={
            "relevance": min(score.relevance, PLAFOND_POST_VIDE),
            "hotness": min(score.hotness, PLAFOND_POST_VIDE),
        }
    )
```

Dans `score_pending`, remplacer le commentaire

```python
            # La décision d'écarter se prend sur la note BRUTE : le plafond media
            # (59) est au-dessus du seuil et ne doit jamais faire écarter un article.
```

par

```python
            # La décision d'écarter se prend sur la note BRUTE, avant garde-fou.
```

Retirer `SourceCategorie` de l'import `from app.models import …` s'il n'est plus utilisé (ruff le signale).

- [ ] **Step 4: Implement prefilter.py and the maintenance script**

Dans `app/pipeline/prefilter.py`, remplacer dans la docstring du module la phrase `Une seule consigne s'y ajoute (`CONSIGNE_PREFILTRE`) : elle empêche la décote des médias de rendre le pré-filtre plus sévère que Claude.` par `Une seule consigne s'y ajoute (`CONSIGNE_PREFILTRE`) : elle empêche le 4B, plus brutal, d'écarter ce que Claude garderait.` ; puis remplacer le commentaire et la constante :

```python
# La SEULE addition au prompt du scoring. Le pré-filtre n'a qu'un rôle : ne jamais
# écarter ce que Claude garderait à 60+. Appliquée avec la brutalité d'un 4B, la
# règle 6 (posts sans information) ferait passer sous le seuil (45) des contenus
# qui portent un fait. On la réserve donc aux posts réellement vides.
CONSIGNE_PREFILTRE = """

CONSIGNE PROPRE AU PRÉ-FILTRE. Tu fais un premier tri avant un juge plus fin. \
Ne descends sous 45 que pour le bruit évident, l'actu hors ligne qui ne relève \
d'aucun axe prioritaire et les posts réellement vides (règle 6 : légende absente, \
emojis ou hashtags seuls). Un contenu qui porte un fait (nom connu, date, sortie, \
événement, chiffre) vaut 45 ou plus, quel que soit le type de compte.\
"""
```

Dans `scripts/appliquer_garde_fous.py`, supprimer de la docstring le paragraphe `Le plafond MEDIA (59) n'est PAS re-applique : … par le garde-fou « post vide ».` et, dans `main()`, les deux lignes `print("\nPlafond media (59) non re-applique : …")`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_garde_fous.py tests/pipeline/test_prefilter.py tests/pipeline/test_scoring.py tests/scripts/test_appliquer_garde_fous_script.py -v`
Expected: PASS

- [ ] **Step 6: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/pipeline/scoring.py app/pipeline/prefilter.py scripts/appliquer_garde_fous.py tests/pipeline/test_garde_fous.py tests/pipeline/test_scoring.py
git commit -m "$(cat <<'EOF'
feat(scoring): plus de decote ni de plafond pour les comptes media

L'info peut venir des medias (decision du 2026-09-17, actu GTA VI perdue a
45-59). actu_majeure retire : jamais persiste, plus aucun consommateur.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

### Task 2: Migration 0018, modèles et réglages

**Files:**
- Create: `alembic/versions/0018_visuels_artistes.py`
- Modify: `app/models.py`, `app/config.py`, `tests/conftest.py`, `tests/socle/test_migrations.py`
- Test: `tests/socle/test_modeles_visuels.py` (créer)

**Interfaces:**
- Produces:
  - `Post.assets: Mapped[list | None]` (JSONVariant)
  - `Artist.source_instagram_id: Mapped[int | None]` (FK `sources.id`, `ON DELETE SET NULL`, index)
  - Réglages : `cluster_sources_claude: int = 3`, `cluster_reevaluations_max_par_run: int = 10`, `visuels_artiste_fenetre_jours: int = 10`, `visuels_artiste_generique_jours: int = 5`, `visuels_max_photos: int = 3`, `visuels_lectures_max_par_passage: int = 2`, `visuels_lectures_max_par_jour: int = 24`, `visuels_cache_lectures_heures: int = 6`, `visuels_pause_quota_minutes: int = 60`, `visuels_cache_dir: Path`, `visuels_cache_jours: int = 7`

- [ ] **Step 1: Write the failing tests**

Créer `tests/socle/test_modeles_visuels.py` :

```python
"""Ou vit le choix des visuels : posts.assets, et le lien artiste -> compte suivi."""

from app.config import settings
from app.models import Artist, Post, Source, SourceCategorie, SourceType


def test_un_post_garde_ses_actifs(db_session):
    actifs = [{"type": "photo", "url": "https://cdn/a.jpg", "origine": "artiste", "compte": "@fredagain"}]
    post = Post(article_title="T", article_url="https://t/1", caption="L", slides=[], assets=actifs)
    db_session.add(post)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Post, post.id).assets == actifs


def test_un_post_sans_actifs_vaut_null(db_session):
    post = Post(article_title="T", article_url="https://t/1", caption="L", slides=[])
    db_session.add(post)
    db_session.commit()
    assert db_session.get(Post, post.id).assets is None


def test_une_fiche_artiste_se_lie_a_un_compte_suivi(db_session):
    import datetime as dt

    source = Source(name="Fred again.. (IG)", type=SourceType.INSTAGRAM, url="ig:fredagain",
                    categorie=SourceCategorie.ARTISTE)
    db_session.add(source)
    db_session.commit()
    maintenant = dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc)
    artist = Artist(name="Fred again..", slug="fred again..", first_seen_at=maintenant,
                    last_seen_at=maintenant, source_instagram_id=source.id)
    db_session.add(artist)
    db_session.commit()
    assert db_session.get(Artist, artist.id).source_instagram_id == source.id


def test_les_reglages_ont_les_defauts_du_design():
    assert settings.cluster_sources_claude == 3
    assert settings.cluster_reevaluations_max_par_run == 10
    assert (settings.visuels_artiste_fenetre_jours, settings.visuels_artiste_generique_jours) == (10, 5)
    assert settings.visuels_max_photos == 3
    assert (settings.visuels_lectures_max_par_passage, settings.visuels_lectures_max_par_jour) == (2, 24)
    assert settings.visuels_pause_quota_minutes == 60


def test_le_cache_des_tests_n_est_jamais_le_vrai(tmp_path):
    assert "Application Support" not in str(settings.visuels_cache_dir)
```

- [ ] **Step 2: Run tests to verify they fail**

Dans `tests/socle/test_migrations.py`, remplacer `test_la_tete_est_reels` par :

```python
def test_la_tete_est_visuels_artistes():
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    assert script.get_current_head() == "0018"
```

Run: `uv run pytest tests/socle/test_modeles_visuels.py tests/socle/test_migrations.py -v`
Expected: FAIL — `TypeError: 'assets' is an invalid keyword argument for Post`, `AttributeError: 'Settings' object has no attribute 'cluster_sources_claude'`, `assert '0017' == '0018'`.

- [ ] **Step 3: Implement models, config, conftest, migration**

Dans `app/models.py`, classe `Artist`, après `tracked` :

```python
    # Le compte Instagram suivi de cet artiste, quand le nom ne suffit pas a le
    # retrouver (« Guy-Man » et son compte). Pose automatiquement par
    # `lier_artistes_comptes`, corrigeable au dashboard (PATCH /artists/{id}).
    source_instagram_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
```

Classe `Post`, après `credit` :

```python
    # Les visuels retenus a la composition, dans l'ordre (le premier fait le fond
    # de cover) : entrees medias completes + origine (article | artiste), compte,
    # permalink, article_id, publie_le, fichier (copie locale). NULL = post
    # anterieur a la regle des visuels (2026-09-17) : on relit alors les medias de
    # l'article, filtres. Reassigner la liste ENTIERE, jamais de mutation in place.
    assets: Mapped[list | None] = mapped_column(JSONVariant)
```

Dans `app/config.py`, après `illustration_max_par_run` :

```python
    # --- Regroupement des doublons et cascade (2026-09-17) ---
    # Une actu couverte par au moins N sources DISTINCTES passe toujours chez
    # Claude, meme sous le seuil du pre-filtre local.
    cluster_sources_claude: int = 3
    # Borne des reelections de representant et des remises a noter par run : au
    # plus un appel Claude chacune.
    cluster_reevaluations_max_par_run: int = 10

    # --- Visuels d'artistes (jamais ceux des comptes Instagram media) ---
    # Un post d'artiste plus ancien que N jours avant l'article n'est jamais pris.
    visuels_artiste_fenetre_jours: int = 10
    # Hors sujet (un portrait, un live), un post n'est accepte qu'a N jours de l'actu.
    visuels_artiste_generique_jours: int = 5
    visuels_max_photos: int = 3
    # business_discovery en direct, a la composition : le quota Graph est partage
    # avec le pipeline et a deja rendu le code 4 pendant une heure.
    visuels_lectures_max_par_passage: int = 2
    visuels_lectures_max_par_jour: int = 24
    visuels_cache_lectures_heures: int = 6
    visuels_pause_quota_minutes: int = 60
    # Copies locales des images d'artistes : les URL du CDN Instagram expirent.
    visuels_cache_dir: Path = Path.home() / "Library/Application Support/Kinda/visuels"
    visuels_cache_jours: int = 7
```

Dans `tests/conftest.py`, fixture `_pas_de_fichiers_applicatifs_reels`, ajouter après `publication_dir` :

```python
    monkeypatch.setattr(settings, "visuels_cache_dir", applicatif / "visuels")
```

Créer `alembic/versions/0018_visuels_artistes.py` :

```python
"""visuels d'artistes : posts.assets et artists.source_instagram_id

`posts.assets` NULL = post anterieur a la regle des visuels : lecture historique.
`artists.source_instagram_id` NULL = fiche non liee a un compte suivi.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Meme forme que `posts.variants` (0011) : JSONB en Postgres.
    op.add_column("posts", sa.Column("assets", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("artists", sa.Column("source_instagram_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_artists_sources_source_instagram_id"),
        "artists",
        "sources",
        ["source_instagram_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_artists_source_instagram_id"), "artists", ["source_instagram_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_artists_source_instagram_id"), table_name="artists")
    op.drop_constraint(op.f("fk_artists_sources_source_instagram_id"), "artists", type_="foreignkey")
    op.drop_column("artists", "source_instagram_id")
    op.drop_column("posts", "assets")
```

Le nom de la FK suit `NAMING_CONVENTION` (`fk_%(table_name)s_%(referred_table_name)s_%(column_0_name)s`) : `alembic check` doit rester muet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/socle/test_modeles_visuels.py tests/socle/test_migrations.py -v`
Expected: PASS

- [ ] **Step 5: Verify the migration on a disposable cluster (optional, never on Neon)**

```bash
D=$(mktemp -d /tmp/pg.XXXX) && /Library/PostgreSQL/15/bin/initdb -D "$D" -U postgres -A trust >/dev/null \
  && /Library/PostgreSQL/15/bin/pg_ctl -D "$D" -o "-p 5499 -k $D" -l "$D/log" start && sleep 2 \
  && /Library/PostgreSQL/15/bin/createdb -p 5499 -U postgres kinda_test \
  && DATABASE_URL=postgresql+psycopg://postgres@localhost:5499/kinda_test uv run alembic upgrade head \
  && DATABASE_URL=postgresql+psycopg://postgres@localhost:5499/kinda_test uv run alembic check \
  && DATABASE_URL=postgresql+psycopg://postgres@localhost:5499/kinda_test uv run alembic downgrade 0017 \
  ; /Library/PostgreSQL/15/bin/pg_ctl -D "$D" stop; rm -rf "$D"
```
Expected: `No new upgrade operations detected.` puis descente sans erreur. (Le socket Unix doit vivre dans un chemin court, d'où `-k $D` sous `/tmp` : au-delà de 103 octets Postgres refuse de démarrer.)

- [ ] **Step 6: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add alembic/versions/0018_visuels_artistes.py app/models.py app/config.py tests/conftest.py tests/socle/test_modeles_visuels.py tests/socle/test_migrations.py
git commit -m "$(cat <<'EOF'
feat(modeles): posts.assets et lien artiste -> compte suivi (migration 0018)

Reglages du regroupement (cluster_*) et des visuels d'artistes (visuels_*).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

### Task 3: L'index des comptes d'artistes suivis

**Files:**
- Create: `app/pipeline/comptes_artistes.py`
- Test: `tests/pipeline/test_comptes_artistes.py`

**Interfaces:**
- Consumes: `app.pipeline.cluster.normalize_title`, `app.sources.instagram.compte_depuis_url`, `Source`, `Artist`
- Produces:
  - `CLE_MIN = 4`
  - `cle_artiste(nom: str | None) -> str`
  - `@dataclass(frozen=True) CompteArtiste(source_id: int, handle: str, nom: str, dernier_appel: dt.datetime | None)`
  - `@dataclass IndexComptes(comptes: dict[int, CompteArtiste], par_cle: dict[str, CompteArtiste])` avec `cites(self, textes: Iterable[str | None] = (), noms: Iterable[str] = (), exclure_source_id: int | None = None) -> list[CompteArtiste]` (ordre des `source_id`)
  - `construire_index(db: Session, avec_liens: bool = True) -> IndexComptes` — `avec_liens=False` : sources seules
  - `lier_artistes_comptes(db: Session) -> int` — compare aux clés des SOURCES seules, jamais aux liens déjà posés

- [ ] **Step 1: Write the failing tests**

Créer `tests/pipeline/test_comptes_artistes.py` :

```python
"""Qui est un « artiste suivi » : le nom d'une source Instagram d'artiste, son
handle, ou une fiche artists liee a la main."""

import datetime as dt

import pytest

from app.models import Artist, Source, SourceCategorie, SourceType
from app.pipeline.comptes_artistes import cle_artiste, construire_index, lier_artistes_comptes

MAINTENANT = dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.timezone.utc)


def _compte(db, nom, handle, categorie=SourceCategorie.ARTISTE, active=True, state=None):
    source = Source(name=nom, type=SourceType.INSTAGRAM, url=f"ig:{handle}", categorie=categorie,
                    active=active, state=state)
    db.add(source)
    db.commit()
    return source


@pytest.mark.parametrize(
    "nom, cle",
    [
        ("Fred again.. (IG)", "fred again"),
        ("DJ Snake (FR) (IG)", "dj snake"),
        ("@amelie_lens", "amelie lens"),
        ("Guy-Man", "guyman"),
        ("Amélie Lens", "amelie lens"),
        (None, ""),
    ],
)
def test_cle_artiste(nom, cle):
    assert cle_artiste(nom) == cle


def test_un_titre_de_presse_cite_un_artiste_suivi_de_deux_mots(db_session):
    fred = _compte(db_session, "Fred again.. (IG)", "fredagainagainagainagainagain")
    index = construire_index(db_session)
    titre = "GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more"
    assert [c.source_id for c in index.cites(textes=[titre])] == [fred.id]


def test_un_nom_rendu_par_un_modele_cite_meme_d_un_seul_mot(db_session):
    anyma = _compte(db_session, "Anyma (IG)", "anyma")
    index = construire_index(db_session)
    assert index.cites(textes=["Anyma au Sphere de Las Vegas"]) == []
    assert [c.source_id for c in index.cites(noms=["Anyma"])] == [anyma.id]


def test_un_handle_mentionne_cite(db_session):
    anyma = _compte(db_session, "Anyma (IG)", "anyma")
    index = construire_index(db_session)
    assert [c.source_id for c in index.cites(textes=["Merci @Anyma pour hier"])] == [anyma.id]
    assert index.cites(textes=["Merci @anyma_fanclub"]) == []


def test_pas_de_citation_par_morceau_de_mot(db_session):
    _compte(db_session, "Black Coffee (IG)", "realblackcoffee")
    index = construire_index(db_session)
    assert index.cites(textes=["Blackcoffee lovers unite"]) == []


def test_seuls_les_comptes_d_artistes_actifs_comptent(db_session):
    _compte(db_session, "Guettapen (IG)", "guettapen", categorie=SourceCategorie.MEDIA)
    _compte(db_session, "Peggy Gou (IG)", "peggygou", active=False)
    index = construire_index(db_session)
    assert index.cites(textes=["Guettapen annonce", "Peggy Gou en tournee"]) == []


def test_la_source_de_l_article_peut_etre_exclue(db_session):
    fred = _compte(db_session, "Fred again.. (IG)", "fredagain")
    index = construire_index(db_session)
    assert index.cites(noms=["Fred again.."], exclure_source_id=fred.id) == []


def test_une_fiche_artiste_liee_ajoute_un_alias(db_session):
    thomas = _compte(db_session, "Thomas Bangalter (IG)", "thomasbangalter")
    db_session.add(Artist(name="Guy-Manuel", slug="guy-manuel", first_seen_at=MAINTENANT,
                          last_seen_at=MAINTENANT, source_instagram_id=thomas.id))
    db_session.commit()
    index = construire_index(db_session)
    assert [c.source_id for c in index.cites(noms=["Guy-Manuel"])] == [thomas.id]


def test_le_dernier_appel_du_pipeline_est_lu(db_session):
    _compte(db_session, "Fred again.. (IG)", "fredagain", state={"dernier_appel": MAINTENANT.isoformat()})
    (compte,) = construire_index(db_session).comptes.values()
    assert compte.dernier_appel == MAINTENANT
    assert compte.handle == "fredagain"
    assert compte.nom == "Fred again.."


def test_lier_artistes_comptes_par_le_nom_sans_ecraser(db_session):
    fred = _compte(db_session, "Fred again.. (IG)", "fredagain")
    autre = _compte(db_session, "Autre (IG)", "autre")
    libre = Artist(name="Fred Again..", slug="fred again..", first_seen_at=MAINTENANT, last_seen_at=MAINTENANT)
    deja = Artist(name="Fred again", slug="fred again", first_seen_at=MAINTENANT, last_seen_at=MAINTENANT,
                  source_instagram_id=autre.id)
    inconnu = Artist(name="Inconnu Total", slug="inconnu total", first_seen_at=MAINTENANT, last_seen_at=MAINTENANT)
    db_session.add_all([libre, deja, inconnu])
    db_session.commit()

    assert lier_artistes_comptes(db_session) == 1
    assert lier_artistes_comptes(db_session) == 0
    assert (libre.source_instagram_id, deja.source_instagram_id, inconnu.source_instagram_id) == (
        fred.id, autre.id, None,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_comptes_artistes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pipeline.comptes_artistes'`

- [ ] **Step 3: Implement comptes_artistes.py**

```python
"""Les comptes Instagram d'artistes que Kinda suit, et qui les cite.

Deux usages, une seule definition : la cascade de scoring (un article qui cite un
artiste suivi n'est jamais ecarte par le pre-filtre) et les visuels (on va
chercher l'image chez l'artiste cite, jamais chez le media qui relaie).

Un nom d'un seul mot (« Anyma », « Justice ») n'est jamais cherche dans le texte
libre : il collisionne avec des mots courants. Il cite seulement par la liste
d'artistes d'un modele ou sous la forme `@handle`.
"""

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Artist, Source, SourceCategorie, SourceType
from app.pipeline.cluster import normalize_title
from app.sources.instagram import compte_depuis_url

CLE_MIN = 4
# « Fred again.. (IG) », « DJ Snake (FR) (IG) » : les suffixes de nommage des sources.
_PARENTHESES = re.compile(r"\s*\([^)]*\)")


def cle_artiste(nom: str | None) -> str:
    """La forme comparable d'un nom ou d'un handle : sans suffixe entre
    parentheses, sans @, puis la normalisation des titres de la dedup."""
    if not nom:
        return ""
    return normalize_title(_PARENTHESES.sub("", nom).strip().lstrip("@"))


@dataclass(frozen=True)
class CompteArtiste:
    source_id: int
    handle: str
    nom: str
    # Derniere lecture du compte par le pipeline : sous l'intervalle, la base est
    # a jour et une lecture en direct serait du quota Graph pour rien.
    dernier_appel: dt.datetime | None


def _date(valeur) -> dt.datetime | None:
    if not valeur:
        return None
    try:
        moment = dt.datetime.fromisoformat(valeur)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


@dataclass
class IndexComptes:
    comptes: dict[int, CompteArtiste]
    par_cle: dict[str, CompteArtiste]

    def cites(
        self,
        textes: Iterable[str | None] = (),
        noms: Iterable[str] = (),
        exclure_source_id: int | None = None,
    ) -> list[CompteArtiste]:
        trouves: dict[int, CompteArtiste] = {}
        for nom in noms:
            compte = self.par_cle.get(cle_artiste(nom))
            if compte is not None:
                trouves[compte.source_id] = compte
        for texte in textes:
            if not texte:
                continue
            normalise = f" {normalize_title(texte)} "
            for cle, compte in self.par_cle.items():
                if " " in cle and f" {cle} " in normalise:
                    trouves[compte.source_id] = compte
            for compte in self.comptes.values():
                if re.search(rf"@{re.escape(compte.handle)}(?![\w.])", texte, re.IGNORECASE):
                    trouves[compte.source_id] = compte
        trouves.pop(exclure_source_id, None)
        return [trouves[source_id] for source_id in sorted(trouves)]


def construire_index(db: Session, avec_liens: bool = True) -> IndexComptes:
    sources = db.scalars(
        select(Source)
        .where(
            Source.type == SourceType.INSTAGRAM,
            Source.categorie == SourceCategorie.ARTISTE,
            Source.active.is_(True),
        )
        .order_by(Source.id)
    ).all()
    comptes: dict[int, CompteArtiste] = {}
    par_cle: dict[str, CompteArtiste] = {}
    for source in sources:
        handle = compte_depuis_url(source.url)
        compte = CompteArtiste(
            source_id=source.id,
            handle=handle,
            nom=_PARENTHESES.sub("", source.name).strip(),
            dernier_appel=_date((source.state or {}).get("dernier_appel")),
        )
        comptes[source.id] = compte
        for cle in (cle_artiste(source.name), cle_artiste(handle)):
            if len(cle) >= CLE_MIN:
                par_cle.setdefault(cle, compte)
    if not avec_liens:
        return IndexComptes(comptes, par_cle)
    # Les liens poses sur les fiches artistes priment : c'est la correction manuelle.
    liens = db.execute(
        select(Artist.name, Artist.source_instagram_id).where(Artist.source_instagram_id.is_not(None))
    )
    for nom, source_id in liens:
        cle = cle_artiste(nom)
        if source_id in comptes and len(cle) >= CLE_MIN:
            par_cle[cle] = comptes[source_id]
    return IndexComptes(comptes, par_cle)


def lier_artistes_comptes(db: Session) -> int:
    """Lie chaque fiche artiste non liee dont le nom correspond a un compte suivi.
    Jamais d'ecrasement d'un lien existant. Idempotent. Les cles viennent des
    SOURCES seules : un lien manuel pose sur une autre fiche du meme nom ne doit
    pas se propager."""
    index = construire_index(db, avec_liens=False)
    lies = 0
    for artist in db.scalars(select(Artist).where(Artist.source_instagram_id.is_(None))):
        compte = index.par_cle.get(cle_artiste(artist.name))
        if compte is not None:
            artist.source_instagram_id = compte.source_id
            lies += 1
    db.commit()
    return lies
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_comptes_artistes.py -v`
Expected: PASS

- [ ] **Step 5: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/pipeline/comptes_artistes.py tests/pipeline/test_comptes_artistes.py
git commit -m "$(cat <<'EOF'
feat(artistes): index des comptes Instagram d'artistes suivis

Cle de nom normalisee, citations par nom de modele, texte libre (deux mots au
moins) ou @handle ; liaison automatique des fiches artists.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---
### Task 4: Le représentant le plus informatif, et la réévaluation bornée

**Files:**
- Create: `app/pipeline/election.py`, `app/content/visuels.py` (première pierre : `origine_interdite` seule, complétée en tâche 7)
- Test: `tests/pipeline/test_election.py`

**Interfaces:**
- Consumes: `IndexComptes.cites`, `cluster.significant_tokens`, `cluster.DEFAULT_WINDOW_HOURS`, `settings.cluster_sources_claude`, `settings.cluster_reevaluations_max_par_run`, `instagram.TITRE_PAR_DEFAUT`
- Produces:
  - `app.content.visuels.origine_interdite(source) -> bool`
  - `election.CATEGORIES_PRIMAIRES: frozenset[SourceCategorie]`
  - `election.tailles_clusters(db: Session, ids: Iterable[int]) -> dict[int, int]` — sources distinctes par représentant (≥ 1)
  - `election.cle_information(article: Article, index: IndexComptes) -> tuple`
  - `election.representant_engage(db: Session, article: Article) -> bool`
  - `election.remettre_a_noter(article: Article) -> None`
  - `@dataclass election.ElectionStats(reelus: int = 0, a_rescorer: int = 0, verrouilles: int = 0, changements: list[tuple[int, int]] = [])`
  - `election.elire_representants(db: Session, index: IndexComptes, *, window_hours: int = DEFAULT_WINDOW_HOURS, limite: int | None = None, maintenant: dt.datetime | None = None, appliquer: bool = True) -> ElectionStats`

- [ ] **Step 1: Write the failing tests**

Créer `tests/pipeline/test_election.py` :

```python
"""Le representant d'un groupe de doublons : le plus informatif, plus seulement le
plus ancien. Cas reel du 2026-09-17 : DJ Mag masque derriere Mixmag."""

import datetime as dt
import itertools

import pytest

from app.config import settings
from app.content.visuels import origine_interdite
from app.models import (
    Article, ArticleStatus, Creneau, EtatCreneau, Post, ScoredBy, Source, SourceCategorie, SourceType,
)
from app.pipeline.comptes_artistes import construire_index
from app.pipeline.election import (
    cle_information,
    elire_representants,
    representant_engage,
    tailles_clusters,
)

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 17, 18, 0, tzinfo=UTC)
_numeros = itertools.count()

MIXMAG = "Fred again.., PinkPantheress and more teased for GTA 6 soundtrack"
DJMAG = "GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more"


def _source(db, nom, categorie=SourceCategorie.MEDIA, type_=SourceType.RSS, handle=None):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{nom}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, *, heures=2, rep=None, relevance=None, scored_by=None, status=ArticleStatus.NEW):
    n = next(_numeros)
    moment = MAINTENANT - dt.timedelta(hours=heures)
    article = Article(
        source_id=source.id, url=f"https://a.example/{n}", title=titre, raw_hash=f"e{n:063d}",
        published_at=moment, fetched_at=moment, duplicate_of_id=rep.id if rep else None,
        relevance_score=relevance, scored_by=scored_by, status=status,
    )
    db.add(article)
    db.commit()
    return article


@pytest.fixture()
def fred(db_session):
    return _source(db_session, "Fred again.. (IG)", SourceCategorie.ARTISTE, SourceType.INSTAGRAM,
                   "fredagainagainagainagainagain")


def test_origine_interdite_ne_vise_que_les_comptes_instagram_media():
    def source(type_, categorie):
        return Source(name="x", type=type_, url="x", categorie=categorie)

    assert origine_interdite(source(SourceType.INSTAGRAM, SourceCategorie.MEDIA))
    assert not origine_interdite(source(SourceType.RSS, SourceCategorie.MEDIA))
    assert not origine_interdite(source(SourceType.INSTAGRAM, SourceCategorie.ARTISTE))
    assert not origine_interdite(source(SourceType.INSTAGRAM, None))
    assert not origine_interdite(None)


def test_la_taille_se_compte_en_sources_distinctes(db_session):
    a, b = _source(db_session, "a"), _source(db_session, "b")
    rep = _article(db_session, a, "Actu", heures=5)
    _article(db_session, a, "Actu bis", rep=rep)
    _article(db_session, b, "Actu ter", rep=rep)
    seul = _article(db_session, b, "Autre")
    assert tailles_clusters(db_session, [rep.id, seul.id]) == {rep.id: 2, seul.id: 1}


def test_dj_mag_plus_informatif_devient_representant(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6,
                      relevance=59, scored_by=ScoredBy.CLAUDE)
    djmag = _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag)

    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)

    assert (stats.reelus, stats.changements) == (1, [(mixmag.id, djmag.id)])
    assert djmag.duplicate_of_id is None
    assert mixmag.duplicate_of_id == djmag.id


def test_l_election_est_idempotente(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6)
    _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag)
    index = construire_index(db_session)
    elire_representants(db_session, index, maintenant=MAINTENANT)
    assert elire_representants(db_session, index, maintenant=MAINTENANT).reelus == 0


def test_jamais_de_chaine_apres_reelection(db_session, fred):
    rep = _article(db_session, _source(db_session, "a"), "GTA soundtrack news", heures=8)
    moyen = _article(db_session, _source(db_session, "b"), "GTA VI soundtrack news again today", rep=rep)
    meilleur = _article(db_session, _source(db_session, "c"), DJMAG, rep=rep)
    elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)
    assert (rep.duplicate_of_id, moyen.duplicate_of_id, meilleur.duplicate_of_id) == (
        meilleur.id, meilleur.id, None,
    )


def test_une_source_primaire_passe_devant_un_media_plus_bavard(db_session):
    media = _article(db_session, _source(db_session, "presse"),
                     "Festival Astropolis devoile enfin toute la programmation complete", heures=6)
    festival = _source(db_session, "Astropolis (IG)", SourceCategorie.FESTIVAL, SourceType.INSTAGRAM, "astropolis")
    officiel = _article(db_session, festival, "Astropolis programmation devoilee", rep=media)
    elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)
    assert officiel.duplicate_of_id is None


def test_un_compte_media_instagram_cede_a_la_presse_a_egalite(db_session):
    ig = _source(db_session, "Guettapen (IG)", SourceCategorie.MEDIA, SourceType.INSTAGRAM, "guettapen")
    post = _article(db_session, ig, "Astropolis annonce sa programmation", heures=6)
    presse = _article(db_session, _source(db_session, "tsugi"), "Astropolis annonce programmation", rep=post)
    index = construire_index(db_session)
    assert cle_information(presse, index)[2] is True
    assert cle_information(post, index)[2] is False
    elire_representants(db_session, index, maintenant=MAINTENANT)
    assert presse.duplicate_of_id is None


@pytest.mark.parametrize("engagement", ["used", "post", "creneau"])
def test_un_representant_engage_ne_change_jamais(db_session, fred, engagement):
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6,
                      status=ArticleStatus.USED if engagement == "used" else ArticleStatus.NEW)
    djmag = _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag)
    if engagement == "post":
        db_session.add(Post(article_id=mixmag.id, article_title="t", article_url="u", caption="c", slides=[]))
    if engagement == "creneau":
        db_session.add(Creneau(publier_a=MAINTENANT + dt.timedelta(hours=3), article_id=mixmag.id,
                               etat=EtatCreneau.PROGRAMME))
    db_session.commit()
    assert representant_engage(db_session, mixmag)

    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)

    assert (stats.reelus, stats.verrouilles) == (0, 1)
    assert djmag.duplicate_of_id == mixmag.id


def test_un_representant_local_d_un_groupe_de_trois_sources_est_remis_a_noter(db_session):
    rep = _article(db_session, _source(db_session, "a"), "Actu commune longue et precise", heures=8,
                   relevance=25, scored_by=ScoredBy.LOCAL)
    rep.mentioned_artists = ["X"]
    db_session.commit()
    _article(db_session, _source(db_session, "b"), "Actu commune", rep=rep)
    _article(db_session, _source(db_session, "c"), "Actu commune", rep=rep)

    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)

    assert stats.a_rescorer == 1
    assert (rep.relevance_score, rep.scored_by, rep.mentioned_artists) == (None, None, None)


@pytest.mark.parametrize("scored_by, sources", [(ScoredBy.LOCAL, 2), (ScoredBy.CLAUDE, 3)])
def test_pas_de_remise_a_noter_hors_regle(db_session, scored_by, sources):
    noms = ["a", "b", "c"]
    rep = _article(db_session, _source(db_session, noms[0]), "Actu commune longue et precise", heures=8,
                   relevance=25, scored_by=scored_by)
    for i in range(2):
        source = _source(db_session, noms[1 + i]) if i < sources - 1 else rep.source
        _article(db_session, source, "Actu commune", rep=rep)
    assert elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT).a_rescorer == 0
    assert rep.relevance_score == 25


def test_un_plan_sans_appliquer_ne_touche_a_rien(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6)
    djmag = _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag)
    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT, appliquer=False)
    db_session.expire_all()
    assert stats.changements == [(mixmag.id, djmag.id)]
    assert db_session.get(Article, djmag.id).duplicate_of_id == mixmag.id


def test_la_borne_par_run(db_session, fred):
    for n in range(3):
        rep = _article(db_session, _source(db_session, f"m{n}"), MIXMAG, heures=6)
        _article(db_session, _source(db_session, f"d{n}"), DJMAG, heures=3, rep=rep)
    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT, limite=2)
    assert stats.reelus == 2


def test_les_doublons_hors_fenetre_sont_laisses(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=100)
    djmag = _article(db_session, _source(db_session, "djmag"), DJMAG, heures=90, rep=mixmag)
    elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)
    assert djmag.duplicate_of_id == mixmag.id


def test_le_reglage_par_defaut_borne_la_run(db_session, fred, monkeypatch):
    monkeypatch.setattr(settings, "cluster_reevaluations_max_par_run", 0)
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6)
    _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag)
    assert elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT).reelus == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_election.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.content.visuels'`

- [ ] **Step 3: Implement visuels.py (first piece)**

Créer `app/content/visuels.py` :

```python
"""Quels visuels Kinda a le droit d'utiliser, et d'ou ils viennent.

Decision de l'utilisateur du 2026-09-17 : l'INFO peut venir d'un compte d'actu,
ses images et videos JAMAIS (watermarks : reposter le visuel d'un media
concurrent est un plagiat visible). Les photos de presse des flux RSS
(og:image, media_content) restent autorisees.
"""

from app.models import SourceCategorie, SourceType


def origine_interdite(source) -> bool:
    """Vrai pour un compte Instagram de categorie media, et lui seul. `getattr` :
    des appelants passent des sources partielles (SimpleNamespace sans categorie)."""
    return (
        source is not None
        and getattr(source, "type", None) == SourceType.INSTAGRAM
        and getattr(source, "categorie", None) == SourceCategorie.MEDIA
    )
```

- [ ] **Step 4: Implement election.py**

```python
"""Le representant d'un groupe de doublons : l'article le plus informatif.

`link_duplicates` rattache toujours au plus ancien, ce qui garde la liaison
stable. Mais le plus ancien n'est pas le meilleur : le 2026-09-17, « GTA VI full
soundtrack announced featuring Fred again.. » (DJ Mag) etait masque derriere un
Mixmag plus vague, et l'actu n'a jamais atteint la file.

La cle d'information ne lit que le TITRE et la SOURCE, jamais le resume : le
scoring reecrit le resume, une cle qui le lirait ferait osciller le representant
d'un run a l'autre.

Cout borne : au plus `cluster_reevaluations_max_par_run` reelections et remises a
noter par run, chacune au plus un appel Claude.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.visuels import origine_interdite
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, ScoredBy, SourceCategorie
from app.pipeline.cluster import DEFAULT_WINDOW_HOURS, significant_tokens
from app.pipeline.comptes_artistes import IndexComptes
from app.sources.instagram import TITRE_PAR_DEFAUT

CATEGORIES_PRIMAIRES = frozenset(
    {SourceCategorie.ARTISTE, SourceCategorie.FESTIVAL, SourceCategorie.LABEL, SourceCategorie.CLUB}
)


def _utc(moment: dt.datetime | None) -> dt.datetime | None:
    # SQLite rend des datetime naifs.
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.timezone.utc)


def tailles_clusters(db: Session, ids: Iterable[int]) -> dict[int, int]:
    """Pour chaque article, le nombre de SOURCES distinctes de son groupe (lui
    compris). Deux posts du meme compte ne font pas une actu « couverte »."""
    ids = list(ids)
    if not ids:
        return {}
    sources: dict[int, set[int]] = {i: set() for i in ids}
    lignes = db.execute(
        select(Article.id, Article.source_id, Article.duplicate_of_id).where(
            or_(Article.id.in_(ids), Article.duplicate_of_id.in_(ids))
        )
    )
    for article_id, source_id, representant in lignes:
        cle = representant if representant in sources else article_id
        if cle in sources:
            sources[cle].add(source_id)
    return {i: max(1, len(s)) for i, s in sources.items()}


def cle_information(article: Article, index: IndexComptes) -> tuple:
    """La plus grande gagne. Ordre total : artistes suivis cites dans le titre,
    source primaire, visuels autorises, mots significatifs du titre, anciennete,
    id."""
    source = article.source
    vu = _utc(article.published_at or article.fetched_at)
    return (
        len(index.cites(textes=[article.title])),
        source is not None and source.categorie in CATEGORIES_PRIMAIRES,
        not origine_interdite(source),
        len(significant_tokens(article.title)),
        -vu.timestamp() if vu is not None else 0.0,
        -article.id,
    )


def representant_engage(db: Session, article: Article) -> bool:
    """Publie, compose, ou porte par un creneau : il ne change plus."""
    if article.status == ArticleStatus.USED:
        return True
    if db.scalar(select(Post.id).where(Post.article_id == article.id).limit(1)) is not None:
        return True
    return (
        db.scalar(
            select(Creneau.id)
            .where(Creneau.article_id == article.id, Creneau.etat != EtatCreneau.LIBRE)
            .limit(1)
        )
        is not None
    )


def remettre_a_noter(article: Article) -> None:
    """Le prochain `score_pending` le reprend. Le resume est garde : la caption
    d'origine est deja perdue, c'est tout ce qui reste a juger."""
    article.relevance_score = None
    article.hotness_score = None
    article.emergence_score = None
    article.category = None
    article.imprint = None
    article.mentioned_artists = None
    article.scored_by = None


@dataclass
class ElectionStats:
    reelus: int = 0
    a_rescorer: int = 0
    verrouilles: int = 0
    # (ancien representant, nouveau), dans l'ordre de traitement.
    changements: list[tuple[int, int]] = field(default_factory=list)


def elire_representants(
    db: Session,
    index: IndexComptes,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    limite: int | None = None,
    maintenant: dt.datetime | None = None,
    appliquer: bool = True,
) -> ElectionStats:
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    limite = settings.cluster_reevaluations_max_par_run if limite is None else limite
    depuis = maintenant - dt.timedelta(hours=window_hours)
    vu = func.coalesce(Article.published_at, Article.fetched_at)
    representants = sorted(
        set(
            db.scalars(
                select(Article.duplicate_of_id).where(Article.duplicate_of_id.is_not(None), vu >= depuis)
            )
        )
    )
    stats = ElectionStats()
    for representant_id in representants:
        if stats.reelus + stats.a_rescorer >= limite:
            break
        actuel = db.get(Article, representant_id)
        if actuel is None or actuel.duplicate_of_id is not None:
            continue
        doublons = db.scalars(
            select(Article).where(Article.duplicate_of_id == representant_id).order_by(Article.id)
        ).all()
        membres = [actuel, *doublons]
        meilleur = max(membres, key=lambda a: cle_information(a, index))
        if meilleur.id != actuel.id:
            if representant_engage(db, actuel):
                stats.verrouilles += 1
                meilleur = actuel
            else:
                stats.reelus += 1
                stats.changements.append((actuel.id, meilleur.id))
                if appliquer:
                    meilleur.duplicate_of_id = None
                    for membre in membres:
                        if membre.id != meilleur.id:
                            membre.duplicate_of_id = meilleur.id
        sources = len({membre.source_id for membre in membres})
        if (
            meilleur.scored_by == ScoredBy.LOCAL
            and sources >= settings.cluster_sources_claude
            and meilleur.title != TITRE_PAR_DEFAUT
        ):
            stats.a_rescorer += 1
            if appliquer:
                remettre_a_noter(meilleur)
        if appliquer:
            db.commit()
    return stats
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_election.py tests/pipeline/test_cluster.py -v`
Expected: PASS

- [ ] **Step 6: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/pipeline/election.py app/content/visuels.py tests/pipeline/test_election.py
git commit -m "$(cat <<'EOF'
feat(dedup): elire le representant le plus informatif d'un groupe

Cle sur le titre et la source (artistes suivis, source primaire, visuels
autorises, mots), verrou sur un representant engage, remise a noter bornee
d'un representant local de groupe >= 3 sources.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

### Task 5: Le pré-filtre n'écarte jamais un groupe de 3 sources ni un artiste suivi

**Files:**
- Modify: `app/pipeline/scoring.py`, `app/pipeline/run.py`
- Test: `tests/pipeline/test_cascade_forcee.py` (créer), `tests/pipeline/test_pipeline.py`

**Interfaces:**
- Consumes: `construire_index`, `IndexComptes.cites`, `tailles_clusters`, `elire_representants`, `lier_artistes_comptes`
- Produces:
  - `scoring.RAISON_CLUSTER = "cluster"`, `scoring.RAISON_ARTISTE = "artiste_suivi"`
  - `@dataclass scoring.ContexteScoring(index: IndexComptes, tailles: dict[int, int])` avec `raison_claude(self, article: Article, note: ArticleScore | None) -> str | None`
  - `scoring.construire_contexte(db: Session, articles: list[Article]) -> ContexteScoring`
  - `score_pending(db, scorer=None, limit=None, prefilter=None, contexte: ContexteScoring | None = None) -> ScoringStats`
  - `ScoringStats.forces_cluster: int = 0`, `ScoringStats.forces_artiste: int = 0`
  - `run.reevaluer_clusters(db: Session) -> ElectionStats`
  - `run_pipeline(..., elire=reevaluer_clusters, lier_artistes=lier_artistes_comptes)` ; `PipelineReport.election: ElectionStats | None = None`, `PipelineReport.artistes_lies: int = 0`

- [ ] **Step 1: Write the failing tests**

Créer `tests/pipeline/test_cascade_forcee.py` :

```python
"""Sous le seuil du pre-filtre, deux cas partent quand meme chez Claude : une actu
couverte par au moins trois sources, et un article qui cite un artiste suivi.
Cas reel du 2026-09-17 : Billboard 15, Pitchfork 25 sur la BO de GTA VI."""

import itertools

from app.models import Article, ScoredBy, Source, SourceCategorie, SourceType
from app.pipeline.scoring import ArticleScore, Category, score_pending

_numeros = itertools.count()


def note(relevance, artists=()):
    return ArticleScore(relevance=relevance, hotness=relevance, emergence=10,
                        category=Category.ANNOUNCEMENT, summary="Resume.", artists=list(artists))


def _source(db, nom, type_=SourceType.RSS, categorie=SourceCategorie.MEDIA, handle=None):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{nom}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, summary=None, rep=None, relevance=None):
    n = next(_numeros)
    article = Article(source_id=source.id, url=f"https://a.example/{n}", title=titre, summary=summary,
                      raw_hash=f"c{n:063d}", duplicate_of_id=rep.id if rep else None, relevance_score=relevance)
    db.add(article)
    db.commit()
    return article


class Claude:
    def __init__(self):
        self.appels = []

    def __call__(self, article):
        self.appels.append(article.id)
        return note(70)


def test_un_groupe_de_trois_sources_passe_chez_claude(db_session):
    rep = _article(db_session, _source(db_session, "billboard"), "GTA VI The Album will feature 34 tracks")
    _article(db_session, _source(db_session, "pitchfork"), "GTA VI tracks", rep=rep, relevance=40)
    _article(db_session, _source(db_session, "stereogum"), "GTA VI songs", rep=rep, relevance=40)
    claude = Claude()

    stats = score_pending(db_session, scorer=claude, prefilter=lambda a: note(15))

    assert claude.appels == [rep.id]
    assert (stats.scored, stats.forces_cluster, stats.prefiltered) == (1, 1, 0)
    assert rep.scored_by == ScoredBy.CLAUDE


def test_trois_articles_de_deux_sources_ne_suffisent_pas(db_session):
    billboard = _source(db_session, "billboard")
    rep = _article(db_session, billboard, "Actu")
    _article(db_session, billboard, "Actu bis", rep=rep, relevance=40)
    _article(db_session, _source(db_session, "pitchfork"), "Actu ter", rep=rep, relevance=40)
    claude = Claude()
    stats = score_pending(db_session, scorer=claude, prefilter=lambda a: note(15))
    assert claude.appels == []
    assert stats.prefiltered == 1


def test_un_titre_qui_cite_un_artiste_suivi_passe_chez_claude(db_session):
    _source(db_session, "Fred again.. (IG)", SourceType.INSTAGRAM, SourceCategorie.ARTISTE,
            "fredagainagainagainagainagain")
    article = _article(db_session, _source(db_session, "djmag"),
                       "GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more")
    claude = Claude()
    stats = score_pending(db_session, scorer=claude, prefilter=lambda a: note(25))
    assert claude.appels == [article.id]
    assert stats.forces_artiste == 1


def test_un_nom_d_un_mot_rendu_par_le_prefiltre_suffit(db_session):
    _source(db_session, "Anyma (IG)", SourceType.INSTAGRAM, SourceCategorie.ARTISTE, "anyma")
    article = _article(db_session, _source(db_session, "presse"), "Anyma au Sphere pour dix dates")
    claude = Claude()
    score_pending(db_session, scorer=claude, prefilter=lambda a: note(25, artists=["Anyma"]))
    assert claude.appels == [article.id]


def test_sans_artiste_suivi_ni_groupe_le_prefiltre_ecarte(db_session):
    _source(db_session, "Anyma (IG)", SourceType.INSTAGRAM, SourceCategorie.ARTISTE, "anyma")
    _article(db_session, _source(db_session, "presse"), "Anyma au Sphere pour dix dates")
    claude = Claude()
    stats = score_pending(db_session, scorer=claude, prefilter=lambda a: note(25))
    assert claude.appels == []
    assert (stats.prefiltered, stats.forces_artiste) == (1, 0)


def test_le_propre_post_de_l_artiste_ne_force_pas(db_session):
    fred = _source(db_session, "Fred again.. (IG)", SourceType.INSTAGRAM, SourceCategorie.ARTISTE, "fredagain")
    _article(db_session, fred, "Fred again.. merci Paris",
             summary="Fred again.. merci Paris pour ces deux soirees incroyables a Bercy\n\n[Instagram : 1 likes]")
    claude = Claude()
    score_pending(db_session, scorer=claude, prefilter=lambda a: note(20, artists=["Fred again.."]))
    assert claude.appels == []


def test_un_post_quasi_vide_qui_cite_un_artiste_reste_local(db_session):
    _source(db_session, "Fred again.. (IG)", SourceType.INSTAGRAM, SourceCategorie.ARTISTE, "fredagain")
    club = _source(db_session, "Club (IG)", SourceType.INSTAGRAM, SourceCategorie.CLUB, "club")
    article = _article(db_session, club, "@fredagain 🔥", summary="@fredagain 🔥\n\n[Instagram : 3 likes]")
    claude = Claude()
    stats = score_pending(db_session, scorer=claude, prefilter=lambda a: note(20))
    assert claude.appels == []
    assert stats.quasi_vides == 1
    assert article.scored_by == ScoredBy.LOCAL


def test_au_dessus_du_seuil_rien_n_est_compte_comme_force(db_session):
    rep = _article(db_session, _source(db_session, "a"), "Actu")
    _article(db_session, _source(db_session, "b"), "Actu", rep=rep, relevance=40)
    _article(db_session, _source(db_session, "c"), "Actu", rep=rep, relevance=40)
    stats = score_pending(db_session, scorer=Claude(), prefilter=lambda a: note(60))
    assert (stats.scored, stats.forces_cluster) == (1, 0)
```

Ajouter à `tests/pipeline/test_pipeline.py` (et `from app.pipeline.election import ElectionStats`) :

```python
def test_run_pipeline_elit_avant_le_scoring_et_lie_les_artistes_apres(db_session, full_config):
    calls = []
    report = run_pipeline(
        db_session,
        ingest=lambda db: calls.append("ingest") or [],
        cluster=lambda db: calls.append("cluster") or 0,
        elire=lambda db: calls.append("elire") or ElectionStats(reelus=1),
        score=lambda db: calls.append("score") or ScoringStats(),
        record=lambda db: calls.append("record") or 0,
        lier_artistes=lambda db: calls.append("lier") or 2,
        alert=lambda db: FakeAlertStats(),
    )
    assert calls == ["ingest", "cluster", "elire", "score", "record", "lier"]
    assert report.election.reelus == 1
    assert report.artistes_lies == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_cascade_forcee.py tests/pipeline/test_pipeline.py -v`
Expected: FAIL — `AttributeError: 'ScoringStats' object has no attribute 'forces_cluster'` et `TypeError: run_pipeline() got an unexpected keyword argument 'elire'`.

- [ ] **Step 3: Implement scoring.py**

Imports à ajouter :

```python
from dataclasses import dataclass, field

from app.pipeline.comptes_artistes import IndexComptes, construire_index
from app.pipeline.election import tailles_clusters
```

(`dataclass` est déjà importé : n'ajouter que `field`.)

Dans `ScoringStats`, après `quasi_vides` :

```python
    # Notes locales sous le seuil envoyées quand même à Claude (2026-09-17) :
    # groupe d'au moins N sources, ou artiste suivi cité.
    forces_cluster: int = 0
    forces_artiste: int = 0
```

Avant `_note_du_prefiltre` :

```python
RAISON_CLUSTER = "cluster"
RAISON_ARTISTE = "artiste_suivi"


@dataclass
class ContexteScoring:
    """Ce que la cascade doit savoir d'un run entier, calculé une fois : les
    comptes d'artistes suivis et la taille (en sources) des groupes de doublons."""

    index: IndexComptes
    tailles: dict[int, int] = field(default_factory=dict)

    def raison_claude(self, article: Article, note: ArticleScore | None) -> str | None:
        """Pourquoi un article noté sous le seuil doit quand même aller chez
        Claude, ou None. L'actu GTA VI x Fred again.. a été écartée à 15 et 25."""
        if self.tailles.get(article.id, 1) >= settings.cluster_sources_claude:
            return RAISON_CLUSTER
        noms = [*(note.artists if note is not None else []), *(article.mentioned_artists or [])]
        if self.index.cites(
            textes=[article.title, article.summary], noms=noms, exclure_source_id=article.source_id
        ):
            return RAISON_ARTISTE
        return None


def construire_contexte(db: Session, articles: list[Article]) -> ContexteScoring:
    return ContexteScoring(construire_index(db), tailles_clusters(db, [a.id for a in articles]))
```

Dans `score_pending`, ajouter le paramètre `contexte: ContexteScoring | None = None` ; après `articles = db.scalars(stmt).all()` :

```python
    if contexte is None:
        contexte = construire_contexte(db, articles)
```

Remplacer le corps du `try` de la boucle par :

```python
            note = _note_du_prefiltre(prefilter, article)
            # Post quasi vide : Claude n'aurait rien à juger. La note locale si
            # elle existe (pour son emergence), plafonnée ; sinon une note minimale.
            if post_quasi_vide(article):
                score = note if note is not None else score_minimal(article)
                apply_score(article, appliquer_garde_fous(article, score))
                article.scored_by = ScoredBy.LOCAL
                stats.prefiltered += 1
                stats.quasi_vides += 1
                db.commit()
                continue
            # Seuil STRICT : une note égale au seuil passe chez Claude. La décision
            # d'écarter se prend sur la note BRUTE, avant garde-fou.
            sous_le_seuil = note is not None and note.relevance < settings.local_prefilter_threshold
            raison = contexte.raison_claude(article, note) if sous_le_seuil else None
            if sous_le_seuil and raison is None:
                apply_score(article, appliquer_garde_fous(article, note))
                article.scored_by = ScoredBy.LOCAL
                stats.prefiltered += 1
            else:
                apply_score(article, appliquer_garde_fous(article, scorer(article)))
                article.scored_by = ScoredBy.CLAUDE
                stats.scored += 1
                if raison == RAISON_CLUSTER:
                    stats.forces_cluster += 1
                elif raison == RAISON_ARTISTE:
                    stats.forces_artiste += 1
            db.commit()
```

`build_user_prompt` et `apply_score` sont inchangés. Vérifier qu'aucun import circulaire n'apparaît : `uv run python -c "import app.pipeline.prefilter, app.pipeline.run"`.

- [ ] **Step 4: Implement run.py**

Imports :

```python
from app.pipeline.comptes_artistes import construire_index, lier_artistes_comptes
from app.pipeline.election import ElectionStats, elire_representants
```

Avant `run_pipeline` :

```python
def reevaluer_clusters(db: Session) -> ElectionStats:
    """Le representant le plus informatif de chaque groupe recent (48 h)."""
    return elire_representants(db, construire_index(db))
```

`PipelineReport`, après `duplicates_linked` :

```python
    election: ElectionStats | None = None
```

et après `mentions_recorded` :

```python
    artistes_lies: int = 0
```

Signature : ajouter `elire=reevaluer_clusters,` après `cluster=link_duplicates,` et `lier_artistes=lier_artistes_comptes,` après `record=record_mentions,`. Dans le corps :

```python
    report.duplicates_linked = cluster(db)
    # AVANT le scoring : un doublon promu representant est note dans le meme run,
    # et un representant local de groupe >= 3 sources repasse chez Claude.
    report.election = elire(db)
```

et après `report.mentions_recorded = record(db)` :

```python
    # Apres les mentions : une fiche artiste nouvelle se lie a son compte suivi,
    # l'index du run suivant la connait.
    report.artistes_lies = lier_artistes(db)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_cascade_forcee.py tests/pipeline/test_pipeline.py tests/pipeline/test_garde_fous.py tests/pipeline/test_scoring.py -v`
Expected: PASS

- [ ] **Step 6: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/pipeline/scoring.py app/pipeline/run.py tests/pipeline/test_cascade_forcee.py tests/pipeline/test_pipeline.py
git commit -m "$(cat <<'EOF'
feat(cascade): jamais d'ecart local pour un groupe de 3 sources ou un artiste suivi

Election du representant avant le scoring, liaison des fiches artistes apres les
mentions. Compteurs forces_cluster et forces_artiste.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

### Task 6: Rattraper les actus récentes (`scripts/reevaluer_actus.py`)

**Files:**
- Create: `scripts/reevaluer_actus.py`
- Test: `tests/scripts/test_reevaluer_actus.py`

**Interfaces:**
- Consumes: `elire_representants`, `tailles_clusters`, `construire_index`, `score_pending`, `apply_score`, `appliquer_garde_fous`, `make_claude_scorer`
- Produces:
  - `RAISON_CLUSTER = "cluster"`, `RAISON_ARTISTE = "artiste_suivi"`, `RAISON_MEDIA = "media_decote"`, `NOTE_MEDIA_MIN = 40`, `NOTE_MEDIA_PROCHE = 50`
  - `@dataclass Reevaluation(article_id: int, source: str, titre: str, avant: int, raison: str, artistes: int, taille: int, apres: int | None = None)`
  - `planifier(db: Session, maintenant: dt.datetime, *, jours: int = 3, limite: int = 100) -> list[Reevaluation]`
  - `reevaluer(db: Session, maintenant: dt.datetime, *, jours: int = 3, limite: int = 100, appliquer: bool = False, scorer=None, noter_en_attente=score_pending) -> tuple[ElectionStats, list[Reevaluation]]`
  - `main() -> None` (`--jours`, `--limite`, `--apply`)

- [ ] **Step 1: Write the failing tests**

Créer `tests/scripts/test_reevaluer_actus.py` :

```python
"""Rattraper les actus enterrees par l'ancienne decote et le pre-filtre.
Plan par defaut, aucune ecriture ; --apply re-note par Claude."""

import datetime as dt
import itertools

import pytest

import scripts.reevaluer_actus as script
from app.models import Article, ArticleStatus, ScoredBy, Source, SourceCategorie, SourceType
from app.pipeline.scoring import ArticleScore, Category
from app.sources.instagram import TITRE_PAR_DEFAUT

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
_numeros = itertools.count()


def _source(db, nom, categorie=SourceCategorie.MEDIA, type_=SourceType.RSS, handle=None):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{nom}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, relevance, scored_by=ScoredBy.CLAUDE, *, heures=5, rep=None, **champs):
    n = next(_numeros)
    moment = MAINTENANT - dt.timedelta(hours=heures)
    article = Article(source_id=source.id, url=f"https://a.example/{n}", title=titre, raw_hash=f"r{n:063d}",
                      published_at=moment, fetched_at=moment, relevance_score=relevance, scored_by=scored_by,
                      duplicate_of_id=rep.id if rep else None, **champs)
    db.add(article)
    db.commit()
    return article


@pytest.fixture()
def fred(db_session):
    return _source(db_session, "Fred again.. (IG)", SourceCategorie.ARTISTE, SourceType.INSTAGRAM, "fredagain")


def _interdit(article):
    raise AssertionError("aucun appel Claude en mode plan")


def test_le_plan_retient_les_victimes_probables(db_session, fred):
    presse = _source(db_session, "presse")
    proche = _article(db_session, presse, "Actu media proche du seuil", 55)
    cite = _article(db_session, presse, "GTA VI soundtrack featuring Fred again.. and more", 45)
    _article(db_session, presse, "Actu media quelconque", 45)
    local_cite = _article(db_session, presse, "Fred again.. annonce une date", 20, ScoredBy.LOCAL)
    rep_local = _article(db_session, presse, "Actu reprise partout", 20, ScoredBy.LOCAL)
    _article(db_session, _source(db_session, "b"), "Actu reprise", None, None, rep=rep_local)
    _article(db_session, _source(db_session, "c"), "Actu reprise", None, None, rep=rep_local)
    artiste = _source(db_session, "Artiste (IG)", SourceCategorie.ARTISTE, SourceType.INSTAGRAM, "artiste")
    _article(db_session, artiste, "Post d'artiste moyen", 45)
    _article(db_session, presse, "Deja publie Fred again..", 55, status=ArticleStatus.USED)
    _article(db_session, presse, "Trop vieux Fred again..", 55, heures=24 * 4)
    _article(db_session, presse, TITRE_PAR_DEFAUT, 20, ScoredBy.LOCAL)
    _article(db_session, presse, "Exclu Fred again..", 55, file_exclu_raison="saute depuis le dashboard")

    plan = script.planifier(db_session, MAINTENANT)

    assert {(r.article_id, r.raison) for r in plan} == {
        (proche.id, script.RAISON_MEDIA),
        (cite.id, script.RAISON_MEDIA),
        (local_cite.id, script.RAISON_ARTISTE),
        (rep_local.id, script.RAISON_CLUSTER),
    }
    # Artistes suivis d'abord, puis la taille du groupe.
    assert plan[0].artistes == 1


def test_la_limite_coupe_apres_le_tri(db_session, fred):
    presse = _source(db_session, "presse")
    _article(db_session, presse, "Actu media proche du seuil", 58)
    cite = _article(db_session, presse, "Fred again.. et la BO de GTA VI", 45)
    assert [r.article_id for r in script.planifier(db_session, MAINTENANT, limite=1)] == [cite.id]


def test_sans_apply_rien_n_est_ecrit(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"),
                      "Fred again.., PinkPantheress and more teased for GTA 6 soundtrack", 59, heures=6)
    djmag = _article(db_session, _source(db_session, "djmag"),
                     "GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more", 45,
                     heures=3, rep=mixmag)

    election, plan = script.reevaluer(db_session, MAINTENANT, scorer=_interdit,
                                      noter_en_attente=lambda db, scorer: _interdit(None))

    db_session.expire_all()
    assert election.changements == [(mixmag.id, djmag.id)]
    assert db_session.get(Article, djmag.id).duplicate_of_id == mixmag.id
    assert db_session.get(Article, mixmag.id).relevance_score == 59
    assert [r.article_id for r in plan] == [mixmag.id]


def test_apply_elit_note_les_en_attente_puis_re_note_le_plan(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"),
                      "Fred again.., PinkPantheress and more teased for GTA 6 soundtrack", 59, heures=6)
    djmag = _article(db_session, _source(db_session, "djmag"),
                     "GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more", 45,
                     heures=3, rep=mixmag)
    en_attente = []

    def claude(article):
        return ArticleScore(relevance=74, hotness=60, emergence=5, category=Category.ANNOUNCEMENT,
                            summary="La BO de GTA VI.", artists=["Fred again.."])

    election, plan = script.reevaluer(
        db_session, MAINTENANT, appliquer=True, scorer=claude,
        noter_en_attente=lambda db, scorer: en_attente.append(scorer),
    )

    assert election.reelus == 1
    assert djmag.duplicate_of_id is None
    assert en_attente == [claude]
    assert [(r.article_id, r.apres) for r in plan] == [(djmag.id, 74)]
    assert (djmag.relevance_score, djmag.scored_by) == (74, ScoredBy.CLAUDE)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scripts/test_reevaluer_actus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.reevaluer_actus'`

- [ ] **Step 3: Implement the script**

Créer `scripts/reevaluer_actus.py` :

```python
"""Reevaluer les actus recentes enterrees par l'ancienne notation.

Motif : le 2026-09-17, la BO de GTA VI (Fred again..) a ete perdue -- pre-filtre
local a 15-25, decote des medias a 45-59, representant de doublon mal choisi.
Les nouvelles regles valent pour les articles a venir ; ce script rattrape les
jours recents.

Sans --apply : le PLAN seul, aucune ecriture, aucun appel Claude. Avec --apply :
1. election du representant le plus informatif sur la fenetre ;
2. notation des articles en attente (representants promus ou remis a noter) ;
3. re-notation par Claude, sans pre-filtre, des articles du plan.

Le plan, sur --jours jours (non doublons, non publies, non exclus, deja notes) :
- notes par le pre-filtre local et (groupe >= 3 sources ou artiste suivi cite) ;
- notes par Claude, compte media, relevance 40-59, et (artiste suivi cite ou
  relevance >= 50 ou groupe >= 2 sources) : les victimes probables de la decote,
  sans re-noter toute la presse.

Usage :
    uv run python -m scripts.reevaluer_actus                    # plan
    uv run python -m scripts.reevaluer_actus --apply [--jours 3] [--limite 100]
"""

import argparse
import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import SessionLocal, attendre_base
from app.models import Article, ArticleStatus, ScoredBy, SourceCategorie
from app.pipeline.comptes_artistes import construire_index
from app.pipeline.election import ElectionStats, elire_representants, tailles_clusters
from app.pipeline.scoring import apply_score, appliquer_garde_fous, make_claude_scorer, score_pending
from app.sources.instagram import TITRE_PAR_DEFAUT

RAISON_CLUSTER = "cluster"
RAISON_ARTISTE = "artiste_suivi"
RAISON_MEDIA = "media_decote"
NOTE_MEDIA_MIN = 40
NOTE_MEDIA_PROCHE = 50


@dataclass
class Reevaluation:
    article_id: int
    source: str
    titre: str
    avant: int
    raison: str
    artistes: int
    taille: int
    apres: int | None = None


def planifier(db: Session, maintenant: dt.datetime, *, jours: int = 3, limite: int = 100) -> list[Reevaluation]:
    depuis = maintenant - dt.timedelta(days=jours)
    articles = db.scalars(
        select(Article)
        .options(selectinload(Article.source))
        .where(
            Article.duplicate_of_id.is_(None),
            Article.status != ArticleStatus.USED,
            Article.file_exclu_raison.is_(None),
            Article.relevance_score.is_not(None),
            Article.title != TITRE_PAR_DEFAUT,
            func.coalesce(Article.published_at, Article.fetched_at) >= depuis,
        )
        .order_by(Article.id)
    ).all()
    index = construire_index(db)
    tailles = tailles_clusters(db, [a.id for a in articles])
    plan = []
    for article in articles:
        artistes = len(
            index.cites(
                textes=[article.title, article.summary],
                noms=article.mentioned_artists or [],
                exclure_source_id=article.source_id,
            )
        )
        taille = tailles.get(article.id, 1)
        source = article.source
        raison = None
        if article.scored_by == ScoredBy.LOCAL:
            if taille >= settings.cluster_sources_claude:
                raison = RAISON_CLUSTER
            elif artistes:
                raison = RAISON_ARTISTE
        elif (
            article.scored_by == ScoredBy.CLAUDE
            and source is not None
            and source.categorie == SourceCategorie.MEDIA
            and NOTE_MEDIA_MIN <= article.relevance_score < settings.publication_min_relevance
            and (artistes or article.relevance_score >= NOTE_MEDIA_PROCHE or taille >= 2)
        ):
            raison = RAISON_MEDIA
        if raison is not None:
            plan.append(Reevaluation(article.id, source.name if source else "?", article.title,
                                     article.relevance_score, raison, artistes, taille))
    plan.sort(key=lambda r: (-r.artistes, -r.taille, -r.avant, r.article_id))
    return plan[:limite]


def reevaluer(
    db: Session,
    maintenant: dt.datetime,
    *,
    jours: int = 3,
    limite: int = 100,
    appliquer: bool = False,
    scorer=None,
    noter_en_attente=score_pending,
) -> tuple[ElectionStats, list[Reevaluation]]:
    election = elire_representants(
        db, construire_index(db), window_hours=jours * 24, limite=limite,
        maintenant=maintenant, appliquer=appliquer,
    )
    # APRES l'election (les representants remis a noter sortent du plan : leur
    # note est nulle) et AVANT la notation des en-attente (un article ne doit pas
    # etre paye deux fois).
    plan = planifier(db, maintenant, jours=jours, limite=limite)
    if not appliquer:
        return election, plan
    scorer = scorer or make_claude_scorer()
    noter_en_attente(db, scorer=scorer)
    for reevaluation in plan:
        article = db.get(Article, reevaluation.article_id)
        try:
            apply_score(article, appliquer_garde_fous(article, scorer(article)))
            article.scored_by = ScoredBy.CLAUDE
            db.commit()
            reevaluation.apres = article.relevance_score
        except Exception as exc:  # noqa: BLE001 -- un echec n'arrete pas les autres
            db.rollback()
            print(f"  ! #{reevaluation.article_id} : {exc}")
    return election, plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jours", type=int, default=3)
    parser.add_argument("--limite", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    attendre_base()
    with SessionLocal() as db:
        election, plan = reevaluer(
            db, dt.datetime.now(dt.timezone.utc), jours=args.jours, limite=args.limite, appliquer=args.apply
        )
    print(f"Representants : {election.reelus} change(s), {election.verrouilles} verrouille(s), "
          f"{election.a_rescorer} remis a noter")
    for ancien, nouveau in election.changements:
        print(f"  - #{ancien} -> #{nouveau}")
    verbe = "re-note(s)" if args.apply else "a re-noter par Claude"
    print(f"{len(plan)} article(s) {verbe} (au plus {args.limite} appels)")
    for r in plan:
        apres = f" -> {r.apres}" if r.apres is not None else ""
        print(f"  - #{r.article_id} [{r.source}] {r.raison} rel {r.avant}{apres} "
              f"(artistes suivis {r.artistes}, sources {r.taille}) : {r.titre[:70]}")
    if not args.apply:
        print("\nPlan calcule AVANT election : les representants peuvent changer a l'application.")
        if plan or election.changements:
            print("Relancer avec --apply pour appliquer.")


if __name__ == "__main__":
    main()
```

Remarque sur `test_sans_apply_rien_n_est_ecrit` : en mode plan l'élection n'est pas appliquée, Mixmag reste représentant et entre dans le plan (59 ≥ 50) ; DJ Mag est un doublon et n'y entre pas.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scripts/test_reevaluer_actus.py -v`
Expected: PASS

- [ ] **Step 5: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add scripts/reevaluer_actus.py tests/scripts/test_reevaluer_actus.py
git commit -m "$(cat <<'EOF'
feat(scripts): reevaluer_actus rattrape les actus enterrees (plan, --apply)

Election, notation des en-attente, re-notation Claude des victimes probables de
la decote et du pre-filtre, bornee par --limite.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

## Partie B — Les visuels

### Task 7: Un compte Instagram média ne fournit jamais d'image

**Files:**
- Modify: `app/content/visuels.py`, `app/content/credit.py`, `app/content/compose.py`, `app/content/render.py`, `app/schedule/store.py`, `app/publish/preparation.py`
- Test: `tests/content/test_visuels.py` (créer), `tests/schedule/test_schedule_store_reels.py`

**Interfaces:**
- Consumes: `app.content.media.CLES`, `PHOTO`, `entree` ; `settings.visuels_cache_dir`
- Produces (dans `app/content/visuels.py`) :
  - `ORIGINE_ARTICLE = "article"`, `ORIGINE_ARTISTE = "artiste"`
  - `visuels_autorises(article) -> list[dict]`
  - `urls_interdites(article) -> set[str]`
  - `raison_sans_artiste(article) -> str`
  - `actif(entree_media: dict, *, origine: str, compte: str | None = None, permalink: str | None = None, article_id: int | None = None, publie_le: dt.datetime | None = None, fichier: str | None = None) -> dict`
  - `image_de(entree_media: dict) -> str | None` — `fichier`, sinon URL d'une photo, sinon `preview_url`
  - `image_de_fond(actifs: list[dict] | None) -> str | None`
  - `credit_des_actifs(actifs: list[dict] | None) -> str | None`
  - `chemin_cache(url: str, extension: str = ".jpg") -> Path`
  - `lire_fichier_cache(uri: str | None) -> bytes | None`
- Modifie :
  - `credit_video(article)` → `None` pour une origine interdite
  - `compose._fond_par_defaut(article)` et `store._a_une_image(article)` lisent `visuels_autorises`
  - `preparation.urls_des_photos(post, article)` : slides `media` dont l'URL est interdite ignorées ; `post.assets` non NULL → images des actifs (`image_de` des photos, et de tout actif qui a un `fichier`) ; sinon photos de `visuels_autorises(article)`
  - `preparation.preparer_fichiers(...)` : fond de cover interdit → `PreparationImpossible` avant tout rendu ; une URL `file://` est lue par `lire_fichier_cache`, jamais téléchargée
  - `render.fetch_image_data_uri(url)` : lit d'abord une copie locale ; un `file://` hors cache retombe sur l'image de secours

- [ ] **Step 1: Write the failing tests**

Créer `tests/content/test_visuels.py` :

```python
"""Decision du 2026-09-17 : l'info d'un compte media Instagram, jamais ses visuels.
Les photos de presse RSS restent autorisees."""

import datetime as dt
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.config import settings
from app.content.compose import CoverVariante, PostDraft, create_post
from app.content.credit import credit_video
from app.content.media import entree
from app.content.render import RenduSlide, fetch_image_data_uri
from app.content.visuels import (
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    actif,
    chemin_cache,
    credit_des_actifs,
    image_de_fond,
    lire_fichier_cache,
    raison_sans_artiste,
    urls_interdites,
    visuels_autorises,
)
from app.models import Article, Source, SourceCategorie, SourceType
from app.publish.preparation import PreparationImpossible, preparer_fichiers, urls_des_photos
from app.schedule.store import candidats_eligibles

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
PHOTO_IG = entree("photo", "https://cdn.ig/guettapen.jpg")
VIDEO_IG = entree("video", None, preview_url="https://cdn.ig/vignette.jpg")


def _source(type_=SourceType.INSTAGRAM, categorie=SourceCategorie.MEDIA, url="ig:guettapen"):
    return SimpleNamespace(type=type_, categorie=categorie, url=url)


def _article(source, *medias):
    return SimpleNamespace(source=source, media=list(medias), url="https://www.instagram.com/p/X/")


def _image(largeur=1200, hauteur=800, format_="PNG"):
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (200, 30, 90)).save(tampon, format_)
    return tampon.getvalue()


def _interdit(*args, **kwargs):
    raise AssertionError("ne doit pas etre appele")


def test_un_compte_media_instagram_n_a_aucun_visuel_autorise():
    assert visuels_autorises(_article(_source(), PHOTO_IG, VIDEO_IG)) == []
    assert urls_interdites(_article(_source(), PHOTO_IG, VIDEO_IG)) == {
        "https://cdn.ig/guettapen.jpg", "https://cdn.ig/vignette.jpg",
    }


@pytest.mark.parametrize(
    "source",
    [
        _source(SourceType.RSS, SourceCategorie.MEDIA, "https://billboard.com/feed"),
        _source(SourceType.INSTAGRAM, SourceCategorie.ARTISTE, "ig:fredagain"),
        _source(SourceType.INSTAGRAM, SourceCategorie.FESTIVAL, "ig:astropolis"),
    ],
)
def test_la_presse_et_les_comptes_primaires_gardent_leurs_visuels(source):
    assert visuels_autorises(_article(source, PHOTO_IG)) == [PHOTO_IG]
    assert urls_interdites(_article(source, PHOTO_IG)) == set()


def test_le_credit_ne_nomme_jamais_un_compte_media():
    assert credit_video(_article(_source())) is None
    assert credit_video(_article(_source(categorie=SourceCategorie.ARTISTE, url="ig:fredagain"))) == "@fredagain"


def test_raison_sans_artiste_est_lisible():
    assert "compte media Instagram" in raison_sans_artiste(_article(_source()))
    assert raison_sans_artiste(_article(_source(SourceType.RSS))) == "aucune image, et aucun artiste suivi cite"


def test_un_actif_a_la_forme_complete():
    publie = dt.datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    resultat = actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain",
                     permalink="https://www.instagram.com/p/F/", article_id=3, publie_le=publie)
    assert resultat == {
        "type": "photo", "url": "https://cdn.ig/guettapen.jpg", "preview_url": None, "duration_ms": None,
        "width": None, "height": None, "origine": "artiste", "compte": "@fredagain",
        "permalink": "https://www.instagram.com/p/F/", "article_id": 3,
        "publie_le": "2026-09-16T12:00:00+00:00", "fichier": None,
    }


def test_le_fond_prefere_la_copie_locale_puis_la_photo_puis_la_vignette():
    video = actif(VIDEO_IG, origine=ORIGINE_ARTICLE)
    photo = actif(PHOTO_IG, origine=ORIGINE_ARTICLE)
    copie = actif(PHOTO_IG, origine=ORIGINE_ARTISTE, fichier="file:///cache/a.jpg")
    assert image_de_fond([copie, photo]) == "file:///cache/a.jpg"
    assert image_de_fond([photo]) == "https://cdn.ig/guettapen.jpg"
    assert image_de_fond([video]) == "https://cdn.ig/vignette.jpg"
    assert image_de_fond([]) is None


def test_le_credit_des_actifs_liste_les_comptes_distincts_dans_l_ordre():
    actifs = [
        actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain"),
        actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@pinkpantheress"),
        actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain"),
        actif(PHOTO_IG, origine=ORIGINE_ARTICLE),
    ]
    assert credit_des_actifs(actifs) == "@fredagain @pinkpantheress"
    assert credit_des_actifs([actif(PHOTO_IG, origine=ORIGINE_ARTICLE)]) is None


def test_la_copie_locale_se_lit_dans_le_cache_seulement(tmp_path):
    chemin = chemin_cache("https://cdn.ig/fred.jpg")
    chemin.parent.mkdir(parents=True)
    chemin.write_bytes(b"jpeg")
    assert chemin.parent == settings.visuels_cache_dir
    assert lire_fichier_cache(chemin.resolve().as_uri()) == b"jpeg"

    ailleurs = tmp_path / "ailleurs.jpg"
    ailleurs.write_bytes(b"secret")
    assert lire_fichier_cache(ailleurs.resolve().as_uri()) is None
    evasion = settings.visuels_cache_dir / ".." / "ailleurs.jpg"
    assert lire_fichier_cache(f"file://{evasion}") is None
    assert lire_fichier_cache("https://cdn.ig/fred.jpg") is None
    assert lire_fichier_cache(None) is None


def test_le_rendu_lit_la_copie_locale_sans_reseau(monkeypatch):
    import app.content.render as render

    monkeypatch.setattr(render.httpx, "get", _interdit)
    chemin = chemin_cache("https://cdn.ig/fred.jpg")
    chemin.parent.mkdir(parents=True)
    chemin.write_bytes(_image(format_="JPEG"))
    assert fetch_image_data_uri(chemin.resolve().as_uri()).startswith("data:image/jpeg;base64,")


def test_le_rendu_ne_lit_pas_un_fichier_hors_cache(tmp_path, monkeypatch):
    import app.content.render as render

    monkeypatch.setattr(render.httpx, "get", _interdit)
    ailleurs = tmp_path / "ailleurs.jpg"
    ailleurs.write_bytes(_image(format_="JPEG"))
    assert fetch_image_data_uri(ailleurs.resolve().as_uri()) == fetch_image_data_uri(None)


def _persister_media_ig(db):
    source = Source(name="Guettapen (IG)", type=SourceType.INSTAGRAM, url="ig:guettapen",
                    categorie=SourceCategorie.MEDIA)
    db.add(source)
    db.commit()
    article = Article(source_id=source.id, url="https://www.instagram.com/p/G/", title="Actu relayee",
                      raw_hash="v1", relevance_score=80, hotness_score=20,
                      published_at=MAINTENANT - dt.timedelta(hours=2), media=[PHOTO_IG])
    db.add(article)
    db.commit()
    return article


def test_create_post_ne_prend_jamais_le_fond_d_un_media(db_session):
    article = _persister_media_ig(db_session)
    variante = CoverVariante(headline="**A**", title="**T**", subtitle="**S**")
    post = create_post(db_session, article, PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨"))
    assert post.slides[0]["image_url"] is None
    assert post.credit is None


def test_un_article_de_media_instagram_sans_autre_visuel_n_est_pas_candidat(db_session):
    _persister_media_ig(db_session)
    assert candidats_eligibles(db_session, MAINTENANT) == []


def test_les_photos_du_gel_ignorent_les_medias_interdits():
    article = _article(_source(), PHOTO_IG)
    post = SimpleNamespace(slides=[{"kind": "cover"}, {"kind": "media", "type": "photo", "url": PHOTO_IG["url"]}],
                           assets=None)
    assert urls_des_photos(post, article) == []


def test_les_photos_du_gel_viennent_des_actifs():
    article = _article(_source(SourceType.RSS), entree("photo", "https://presse/ignoree.jpg"))
    actifs = [
        actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain", fichier="file:///cache/fred.jpg"),
        actif(VIDEO_IG, origine=ORIGINE_ARTICLE),
        actif(entree("photo", "https://presse/photo.jpg"), origine=ORIGINE_ARTICLE),
        actif(VIDEO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain", fichier="file:///cache/vignette.jpg"),
    ]
    post = SimpleNamespace(slides=[{"kind": "cover"}], assets=actifs)
    assert urls_des_photos(post, article) == [
        "file:///cache/fred.jpg", "https://presse/photo.jpg", "file:///cache/vignette.jpg",
    ]


def test_le_gel_refuse_un_fond_tire_d_un_media(tmp_path):
    post = SimpleNamespace(slides=[{"kind": "cover", "image_url": PHOTO_IG["url"]}], assets=None)
    with pytest.raises(PreparationImpossible, match="compte media Instagram"):
        preparer_fichiers(post, _article(_source(), PHOTO_IG), tmp_path, rendre=_interdit, telecharger=_interdit)


def test_le_gel_lit_les_copies_locales_sans_telecharger(tmp_path):
    chemin = chemin_cache("https://cdn.ig/fred.jpg")
    chemin.parent.mkdir(parents=True)
    chemin.write_bytes(_image(format_="JPEG"))
    copie = actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain", fichier=chemin.resolve().as_uri())
    post = SimpleNamespace(slides=[{"kind": "cover", "image_url": copie["fichier"]}], assets=[copie])

    def rendre(slides, template):
        return [RenduSlide(png=_image(1080, 1440), scale=1.0, lines={}, overflow=False)]

    noms = preparer_fichiers(post, _article(_source()), tmp_path / "gel", rendre=rendre, telecharger=_interdit)
    assert len(noms) == 2
```

Dans `tests/schedule/test_schedule_store_reels.py`, `test_un_article_video_va_en_reel_et_jamais_aussi_en_carrousel` prenait la photo d'un compte **Instagram** média comme carrousel — désormais interdit. Garder l'intention (une photo de presse part en carrousel) en passant cette source en RSS :

```python
    photo = _article(db_session, _source(db_session, "tsugi", SourceCategorie.MEDIA, SourceType.RSS), 2,
                     video=False, photo=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/content/test_visuels.py -v`
Expected: FAIL — `ImportError: cannot import name 'ORIGINE_ARTICLE' from 'app.content.visuels'`

- [ ] **Step 3: Implement visuels.py**

Compléter `app/content/visuels.py` (garder `origine_interdite`) ; en tête :

```python
import datetime as dt
import hashlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.config import settings
from app.content.media import CLES, PHOTO
from app.models import SourceCategorie, SourceType

ORIGINE_ARTICLE = "article"
ORIGINE_ARTISTE = "artiste"
```

puis, après `origine_interdite` :

```python
def visuels_autorises(article) -> list[dict]:
    """Les medias de l'article que Kinda peut montrer : tous, sauf ceux d'un
    compte media Instagram."""
    if article is None or origine_interdite(getattr(article, "source", None)):
        return []
    return list(getattr(article, "media", None) or [])


def urls_interdites(article) -> set[str]:
    """Les URL a ne jamais publier : celles des medias d'un compte media Instagram.
    Sert aux ceintures (slides choisies au dashboard, fond d'un brouillon ancien)."""
    if article is None or not origine_interdite(getattr(article, "source", None)):
        return set()
    return {url for m in article.media or [] for url in (m.get("url"), m.get("preview_url")) if url}


def raison_sans_artiste(article) -> str:
    if origine_interdite(getattr(article, "source", None)):
        return "visuels d'un compte media Instagram interdits, et aucun artiste suivi cite"
    return "aucune image, et aucun artiste suivi cite"


def actif(
    entree_media: dict,
    *,
    origine: str,
    compte: str | None = None,
    permalink: str | None = None,
    article_id: int | None = None,
    publie_le: dt.datetime | None = None,
    fichier: str | None = None,
) -> dict:
    """Une entree media de forme complete, plus d'ou elle vient. C'est ce que
    `posts.assets` garde : le dashboard montre l'origine, la legende en tire le
    credit, le gel lit la copie locale."""
    return {
        **{cle: entree_media.get(cle) for cle in CLES},
        "origine": origine,
        "compte": compte,
        "permalink": permalink,
        "article_id": article_id,
        "publie_le": publie_le.isoformat() if publie_le else None,
        "fichier": fichier,
    }


def image_de(entree_media: dict) -> str | None:
    if entree_media.get("fichier"):
        return entree_media["fichier"]
    if entree_media.get("type") == PHOTO and entree_media.get("url"):
        return entree_media["url"]
    return entree_media.get("preview_url")


def image_de_fond(actifs: list[dict] | None) -> str | None:
    return next((image for a in actifs or [] if (image := image_de(a))), None)


def credit_des_actifs(actifs: list[dict] | None) -> str | None:
    comptes = dict.fromkeys(a["compte"] for a in actifs or [] if a.get("compte"))
    return " ".join(comptes) or None


def chemin_cache(url: str, extension: str = ".jpg") -> Path:
    """La copie locale d'une image d'artiste : les URL du CDN Instagram sont
    signees et expirent en quelques jours, la composition precede le gel de ~22 h."""
    return settings.visuels_cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()[:32]}{extension}"


def lire_fichier_cache(uri: str | None) -> bytes | None:
    """Le contenu d'un `file://` RESOLU dans le cache, sinon None. Le fond d'une
    cover est editable au dashboard : sans cette borne, il lirait n'importe quel
    fichier de la machine."""
    if not uri or not uri.startswith("file://"):
        return None
    chemin = Path(unquote(urlsplit(uri).path)).resolve()
    if not chemin.is_relative_to(settings.visuels_cache_dir.resolve()) or not chemin.is_file():
        return None
    return chemin.read_bytes()
```

- [ ] **Step 4: Wire the policy into credit, compose, store, render, preparation**

`app/content/credit.py` :

```python
from app.content.visuels import origine_interdite
from app.models import SourceType
from app.sources.instagram import compte_depuis_url


def credit_video(article) -> str | None:
    source = getattr(article, "source", None)
    # Un compte media ne fournit jamais le visuel : il n'y a rien a lui crediter.
    if source is None or source.type != SourceType.INSTAGRAM or origine_interdite(source):
        return None
    compte = compte_depuis_url(source.url)
    return f"@{compte}" if compte else None
```

`app/content/compose.py` : importer `from app.content.visuels import visuels_autorises` et, dans `_fond_par_defaut`, remplacer les deux `article.media or []` par `visuels_autorises(article)`.

`app/schedule/store.py` : importer `from app.content.visuels import visuels_autorises` et :

```python
def _a_une_image(article: Article) -> bool:
    # Sans image AUTORISEE, la cover partirait sur le fond de secours : pas
    # publiable. Un compte media Instagram n'en fournit jamais.
    return any(
        (m.get("type") == "photo" and m.get("url")) or m.get("preview_url")
        for m in visuels_autorises(article)
    )
```

`app/content/render.py` : importer `mimetypes` et `from app.content.visuels import lire_fichier_cache`, et au début de `fetch_image_data_uri` :

```python
    locale = lire_fichier_cache(url)
    if locale is not None:
        return _data_uri(locale, mimetypes.guess_type(url)[0] or "image/jpeg")
    if url and url.startswith("file://"):
        # Hors du cache ou disparue : jamais lue, l'image de secours la remplace.
        logger.warning("copie locale introuvable ou hors cache : %s", url)
        url = None
```

`app/publish/preparation.py` : importer `from app.content.visuels import image_de, lire_fichier_cache, urls_interdites, visuels_autorises`, puis :

```python
def urls_des_photos(post, article) -> list[str]:
    interdites = urls_interdites(article)
    urls = [
        slide["url"]
        for slide in post.slides[1:]
        if slide.get("kind") == "media" and slide.get("type") == "photo" and slide.get("url")
        and slide["url"] not in interdites
    ]
    actifs = getattr(post, "assets", None)
    if actifs is not None:
        # Les visuels choisis a la composition. Une vignette de video sans copie
        # locale ne fait que le fond, comme avant la regle.
        urls += [image_de(a) for a in actifs if (a.get("type") == "photo" or a.get("fichier")) and image_de(a)]
    elif article is not None:
        urls += [m["url"] for m in visuels_autorises(article) if m.get("type") == "photo" and m.get("url")]
    vues: set[str] = set()
    return [url for url in urls if not (url in vues or vues.add(url))]
```

Dans `preparer_fichiers`, juste après la purge des anciens `*.jpg` :

```python
    fond = post.slides[0].get("image_url") if post.slides else None
    if fond and fond in urls_interdites(article):
        # Brouillon compose avant la regle du 2026-09-17 : jamais publie.
        raise PreparationImpossible("fond de cover tire d'un compte media Instagram : visuel interdit")
```

et dans la boucle des photos :

```python
    for url in urls_des_photos(post, article):
        if len(images) >= CARROUSEL_MAX:
            break
        octets = lire_fichier_cache(url)
        if octets is None:
            if url.startswith("file://"):
                continue
            octets, _ = telecharger(url)
        if octets is None:
            continue
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/content/test_visuels.py tests/publish/test_publish_preparation.py tests/content/test_credit.py tests/content/test_compose.py tests/content/test_render.py tests/schedule/test_schedule_store.py -v`
Expected: PASS

- [ ] **Step 6: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/content/visuels.py app/content/credit.py app/content/compose.py app/content/render.py app/schedule/store.py app/publish/preparation.py tests/content/test_visuels.py tests/schedule/test_schedule_store_reels.py
git commit -m "$(cat <<'EOF'
feat(visuels): jamais d'image ni de video d'un compte media Instagram

Politique centralisee (visuels_autorises), actifs et credit, copie locale lue
dans le cache seulement ; ceintures au gel et au rendu.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

### Task 8: Les posts des artistes cités, et lesquels retenir

**Files:**
- Create: `app/content/actifs_artistes.py`
- Test: `tests/content/test_actifs_artistes.py`

**Interfaces:**
- Consumes: `CompteArtiste`, `cluster.significant_tokens`, `media.videos_exploitables`, `media.video_recuperable`, `media.entree`, `settings.visuels_artiste_*`, `settings.publication_reel_duree_*`, `settings.publication_fraicheur_jours`
- Produces:
  - `@dataclass PostArtiste(compte: CompteArtiste, permalink: str, titre: str, texte: str, publie_le: dt.datetime | None, media: list[dict], article_id: int | None = None)`
  - `@dataclass Retenu(post: PostArtiste, medias: list[dict], recouvrement: int)` + propriété `sur_le_sujet -> bool`
  - `medias_utilisables(media: list[dict] | None, permalink: str | None, format_: FormatCreneau) -> list[dict]`
  - `recouvrement(titre_article: str, post: PostArtiste) -> int`
  - `classer(article, posts: list[PostArtiste], *, format_: FormatCreneau, maintenant: dt.datetime) -> list[Retenu]`
  - `posts_en_base(db: Session, comptes: list[CompteArtiste], maintenant: dt.datetime) -> list[PostArtiste]`

- [ ] **Step 1: Write the failing tests**

Créer `tests/content/test_actifs_artistes.py` :

```python
"""Quel post d'artiste illustre une actu : le meme artiste, recent, sur le sujet
d'abord ; jamais un vieux post sans rapport."""

import datetime as dt
import itertools
from types import SimpleNamespace

from app.content.actifs_artistes import PostArtiste, classer, medias_utilisables, posts_en_base, recouvrement
from app.content.media import entree
from app.models import Article, FormatCreneau, Source, SourceCategorie, SourceType
from app.pipeline.comptes_artistes import CompteArtiste

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
FRED = CompteArtiste(source_id=7, handle="fredagainagainagainagainagain", nom="Fred again..", dernier_appel=None)
ARTICLE = SimpleNamespace(
    id=100, title="GTA VI full soundtrack announced featuring Fred again.., PinkPantheress",
    published_at=MAINTENANT - dt.timedelta(hours=5), fetched_at=None,
)
PHOTO = entree("photo", "https://cdn.ig/fred.jpg")
_numeros = itertools.count()


def _post(titre, jours, media=None, texte="", permalink=None, article_id=None):
    n = next(_numeros)
    return PostArtiste(
        compte=FRED, permalink=permalink or f"https://www.instagram.com/p/P{n}/", titre=titre, texte=texte,
        publie_le=ARTICLE.published_at - dt.timedelta(days=jours),
        media=[PHOTO] if media is None else media, article_id=article_id,
    )


def _classer(*posts, format_=FormatCreneau.CARROUSEL):
    return classer(ARTICLE, list(posts), format_=format_, maintenant=MAINTENANT)


def test_le_nom_de_l_artiste_ne_fait_pas_le_sujet():
    assert recouvrement(ARTICLE.title, _post("Fred again.. live tonight", 1)) == 0
    assert recouvrement(ARTICLE.title, _post("My track on the GTA soundtrack", 1)) == 1


def test_un_post_sur_le_sujet_passe_devant_un_portrait_plus_proche():
    portrait = _post("Paris merci", 0.1)
    sujet = _post("Soundtrack announced today", 4)
    assert [r.post for r in _classer(portrait, sujet)] == [sujet, portrait]
    assert _classer(sujet)[0].sur_le_sujet


def test_hors_sujet_accepte_a_moins_de_cinq_jours_seulement():
    proche, loin = _post("Paris merci", 3), _post("Berlin merci", 6)
    assert [r.post for r in _classer(proche, loin)] == [proche]


def test_jamais_au_dela_de_la_fenetre_meme_sur_le_sujet():
    assert _classer(_post("Soundtrack announced", 11)) == []


def test_jamais_un_post_publie_apres_maintenant():
    assert _classer(_post("Soundtrack announced", -1)) == []


def test_a_sujet_egal_le_plus_proche_de_l_actu():
    loin, proche = _post("Soundtrack announced", 3), _post("Soundtrack announced", 1)
    assert [r.post for r in _classer(loin, proche)] == [proche, loin]


def test_le_carrousel_prend_photos_et_vignettes():
    vignette = entree("video", None, preview_url="https://cdn.ig/v.jpg")
    nue = entree("video", None)
    assert medias_utilisables([PHOTO, vignette, nue], None, FormatCreneau.CARROUSEL) == [PHOTO, vignette]
    assert _classer(_post("Soundtrack announced", 1, media=[nue])) == []


def test_le_reel_prend_une_video_directe_de_duree_valable():
    directe = entree("video", "https://cdn.ig/v.mp4", duration_ms=20_000)
    courte = entree("video", "https://cdn.ig/c.mp4", duration_ms=1_000)
    assert medias_utilisables([PHOTO, courte, directe], None, FormatCreneau.REEL) == [directe]


def test_le_reel_prend_une_video_recuperable_par_yt_dlp():
    sans_url = entree("video", None, preview_url="https://cdn.ig/v.jpg")
    assert medias_utilisables([sans_url], "https://www.instagram.com/reel/R1/", FormatCreneau.REEL) == [sans_url]
    assert medias_utilisables([], "https://www.instagram.com/reel/R1/", FormatCreneau.REEL) == [entree("video", None)]


def test_le_reel_refuse_une_photo_seule():
    assert medias_utilisables([PHOTO], "https://www.instagram.com/p/P/", FormatCreneau.REEL) == []


def test_posts_en_base_des_comptes_cites_seulement(db_session):
    def source(nom, handle, categorie=SourceCategorie.ARTISTE):
        s = Source(name=nom, type=SourceType.INSTAGRAM, url=f"ig:{handle}", categorie=categorie)
        db_session.add(s)
        db_session.commit()
        return s

    fred, autre = source("Fred again.. (IG)", "fredagain"), source("Autre (IG)", "autre")
    compte = CompteArtiste(fred.id, "fredagain", "Fred again..", None)

    def article(src, n, jours, media=True):
        a = Article(source_id=src.id, url=f"https://www.instagram.com/p/B{n}/", title=f"Post {n}",
                    summary="caption", raw_hash=f"b{n}", media=[PHOTO] if media else None,
                    published_at=MAINTENANT - dt.timedelta(days=jours))
        db_session.add(a)
        db_session.commit()
        return a

    recent = article(fred, 1, 2)
    article(fred, 2, 30)
    article(fred, 3, 1, media=False)
    article(autre, 4, 1)

    (post,) = posts_en_base(db_session, [compte], MAINTENANT)
    assert (post.article_id, post.permalink, post.compte) == (recent.id, recent.url, compte)
    assert post.publie_le.tzinfo is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/content/test_actifs_artistes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.content.actifs_artistes'`

- [ ] **Step 3: Implement actifs_artistes.py**

```python
"""Les posts des artistes cites, comme visuels d'une actu relayee par un media.

Regle de pertinence (design du 2026-09-17) : le MEME artiste, un post RECENT, et
SUR LE SUJET d'abord. Un portrait recent illustre honnetement un artiste ; un
vieux post sur autre chose non -- d'ou une fenetre dure et une fenetre plus
courte pour les posts hors sujet.

Ce module ne fait aucun appel reseau : il lit la base, ou des posts qu'on lui
donne (lecture en direct, `lecture_artistes.py`).
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.media import PHOTO, VIDEO, entree, video_recuperable, videos_exploitables
from app.models import Article, FormatCreneau
from app.pipeline.cluster import significant_tokens
from app.pipeline.comptes_artistes import CompteArtiste


def _utc(moment: dt.datetime | None) -> dt.datetime | None:
    # SQLite rend des datetime naifs ; `store.utc` n'est pas importable ici (cycle).
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.timezone.utc)


@dataclass
class PostArtiste:
    compte: CompteArtiste
    permalink: str
    titre: str
    texte: str
    publie_le: dt.datetime | None
    media: list[dict]
    article_id: int | None = None


@dataclass
class Retenu:
    post: PostArtiste
    medias: list[dict]
    recouvrement: int

    @property
    def sur_le_sujet(self) -> bool:
        return self.recouvrement > 0


def medias_utilisables(media: list[dict] | None, permalink: str | None, format_: FormatCreneau) -> list[dict]:
    media = media or []
    if format_ == FormatCreneau.REEL:
        duree_min = settings.publication_reel_duree_min_s * 1000
        directes = [
            v for v in videos_exploitables(media, int(settings.publication_reel_duree_max_s * 1000))
            if v.get("duration_ms") is None or v["duration_ms"] >= duree_min
        ]
        if directes:
            return directes[:1]
        if video_recuperable(media, permalink):
            videos = [m for m in media if m.get("type") == VIDEO]
            return [videos[0] if videos else entree(VIDEO, None)]
        return []
    return [m for m in media if (m.get("type") == PHOTO and m.get("url")) or m.get("preview_url")]


def recouvrement(titre_article: str, post: PostArtiste) -> int:
    """Les mots du SUJET partages, le nom de l'artiste retire : sinon tout post de
    l'artiste « parlerait » de l'actu."""
    nom = significant_tokens(post.compte.nom) | significant_tokens(post.compte.handle)
    sujet = significant_tokens(titre_article) - nom
    return len(sujet & significant_tokens(f"{post.titre} {post.texte}"))


def classer(article, posts: list[PostArtiste], *, format_: FormatCreneau, maintenant: dt.datetime) -> list[Retenu]:
    reference = _utc(article.published_at or article.fetched_at) or maintenant
    debut = reference - dt.timedelta(days=settings.visuels_artiste_fenetre_jours)
    generique = dt.timedelta(days=settings.visuels_artiste_generique_jours)
    retenus = []
    for post in posts:
        publie = _utc(post.publie_le)
        if publie is None or publie < debut or publie > maintenant:
            continue
        if post.article_id is not None and post.article_id == article.id:
            continue
        medias = medias_utilisables(post.media, post.permalink, format_)
        if not medias:
            continue
        commun = recouvrement(article.title, post)
        if commun == 0 and abs(publie - reference) > generique:
            continue
        retenus.append(Retenu(post, medias, commun))
    retenus.sort(
        key=lambda r: (
            not r.sur_le_sujet,
            -r.recouvrement,
            abs(_utc(r.post.publie_le) - reference),
            -(r.post.article_id or 0),
        )
    )
    return retenus


def posts_en_base(db: Session, comptes: list[CompteArtiste], maintenant: dt.datetime) -> list[PostArtiste]:
    if not comptes:
        return []
    par_source = {compte.source_id: compte for compte in comptes}
    depuis = maintenant - dt.timedelta(
        days=settings.visuels_artiste_fenetre_jours + settings.publication_fraicheur_jours
    )
    vu = func.coalesce(Article.published_at, Article.fetched_at)
    articles = db.scalars(
        select(Article)
        .where(Article.source_id.in_(list(par_source)), Article.media.is_not(None), vu >= depuis)
        .order_by(Article.id)
    ).all()
    return [
        PostArtiste(
            compte=par_source[a.source_id],
            permalink=a.url,
            titre=a.title or "",
            texte=a.summary or "",
            publie_le=_utc(a.published_at or a.fetched_at),
            media=list(a.media or []),
            article_id=a.id,
        )
        for a in articles
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/content/test_actifs_artistes.py -v`
Expected: PASS

- [ ] **Step 5: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/content/actifs_artistes.py tests/content/test_actifs_artistes.py
git commit -m "$(cat <<'EOF'
feat(visuels): posts des artistes cites en base et heuristique de pertinence

Meme artiste, fenetre dure de 10 jours, sur le sujet d'abord, hors sujet a 5
jours au plus ; medias utilisables par format (reel : video directe ou
recuperable par yt-dlp).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

### Task 9: Lire un compte d'artiste à la composition, sans épuiser le quota

**Files:**
- Modify: `app/sources/instagram.py`
- Create: `app/content/lecture_artistes.py`
- Test: `tests/content/test_lecture_artistes.py` (créer), `tests/sources/test_instagram.py`

**Interfaces:**
- Consumes: `settings.meta_fb_access_token`, `settings.meta_ig_user_id`, `settings.instagram_max_items`, `settings.instagram_intervalle_heures`, `settings.visuels_*`, `PostArtiste`, `CompteArtiste`
- Produces (dans `app/sources/instagram.py`) :
  - `CHAMPS_MEDIA: str` (constante de module ; `InstagramAdapter.CHAMPS_MEDIA = CHAMPS_MEDIA`)
  - `CODES_QUOTA = frozenset({4, 17, 32, 613})`
  - `class QuotaGraphAtteint(RuntimeError)`
  - `url_business_discovery(compte: str, limite: int) -> tuple[str, dict]`
  - `lire_compte(compte: str, limite: int, *, client: httpx.Client | None = None) -> dict`
  - `items_depuis_payload(payload: dict) -> list[RawItem]`
  - `InstagramAdapter.construire_url()`, `parse()`, `_appeler()` délèguent (comportement inchangé)
- Produces (dans `app/content/lecture_artistes.py`) :
  - `FICHIER_ETAT = "visuels_lectures.json"`
  - `class LectureDifferee(RuntimeError)`
  - `class LecteurComptes` : `__init__(self, *, lire=lire_compte, chemin: Path | None = None)`, attribut `lectures_passage: int`, `__call__(self, compte: CompteArtiste, maintenant: dt.datetime) -> list[PostArtiste]`

- [ ] **Step 1: Write the failing tests**

Ajouter à `tests/sources/test_instagram.py` (et `CODES_QUOTA`, `QuotaGraphAtteint`, `items_depuis_payload`, `lire_compte` à l'import depuis `app.sources.instagram`) :

```python
@pytest.mark.parametrize("code", sorted(CODES_QUOTA))
def test_lire_compte_reconnait_le_quota_graph(monkeypatch, code):
    monkeypatch.setattr(settings, "meta_fb_access_token", "EAAsecret")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    _client_meta(monkeypatch, 400, {"error": {"message": "Application request limit reached", "code": code}})
    with pytest.raises(QuotaGraphAtteint) as exc:
        lire_compte("fredagain", 12)
    assert "EAAsecret" not in str(exc.value)


def test_lire_compte_une_autre_erreur_n_est_pas_le_quota(monkeypatch):
    monkeypatch.setattr(settings, "meta_fb_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    _client_meta(monkeypatch, 400, {"error": {"message": "Invalid user id", "code": 110}})
    with pytest.raises(RuntimeError) as exc:
        lire_compte("inconnu", 12)
    assert not isinstance(exc.value, QuotaGraphAtteint)


def test_items_depuis_payload_est_le_parse_de_l_adapter():
    assert items_depuis_payload(PAYLOAD) == make_adapter().parse(PAYLOAD)
```

Créer `tests/content/test_lecture_artistes.py` :

```python
"""La lecture en direct d'un compte d'artiste, a la composition : bornee par
passage et par jour, en cache, en pause sur le quota Graph."""

import datetime as dt

import httpx
import pytest

from app.config import settings
from app.content.lecture_artistes import FICHIER_ETAT, LectureDifferee, LecteurComptes
from app.pipeline.comptes_artistes import CompteArtiste
from app.sources.instagram import QuotaGraphAtteint

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
PAYLOAD = {
    "business_discovery": {
        "followers_count": 10,
        "media": {"data": [{
            "id": "1", "caption": "GTA VI soundtrack out now with my track", "media_type": "IMAGE",
            "media_url": "https://cdn.ig/fred.jpg", "permalink": "https://www.instagram.com/p/FRED1/",
            "timestamp": "2026-09-17T10:00:00+0000",
        }]},
    }
}


@pytest.fixture(autouse=True)
def _jeton(monkeypatch):
    monkeypatch.setattr(settings, "meta_fb_access_token", "EAAsecret")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")


class Lecture:
    def __init__(self, reponse=PAYLOAD, erreur=None):
        self.reponse, self.erreur, self.appels = reponse, erreur, []

    def __call__(self, compte, limite):
        self.appels.append(compte)
        if self.erreur is not None:
            raise self.erreur
        return self.reponse


def _compte(handle="fredagain", dernier_appel=None, source_id=1):
    return CompteArtiste(source_id, handle, "Fred again..", dernier_appel)


def test_une_lecture_rend_les_posts_du_compte():
    lecture = Lecture()
    (post,) = LecteurComptes(lire=lecture)(_compte(), MAINTENANT)
    assert lecture.appels == ["fredagain"]
    assert (post.permalink, post.media[0]["url"], post.article_id) == (
        "https://www.instagram.com/p/FRED1/", "https://cdn.ig/fred.jpg", None,
    )
    assert post.publie_le == dt.datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
    assert post.compte == _compte()


def test_sans_jeton_aucune_lecture(monkeypatch):
    monkeypatch.setattr(settings, "meta_fb_access_token", None)
    lecture = Lecture()
    assert LecteurComptes(lire=lecture)(_compte(), MAINTENANT) == []
    assert lecture.appels == []


def test_un_compte_lu_recemment_par_le_pipeline_n_est_pas_relu():
    lecture = Lecture()
    frais = _compte(dernier_appel=MAINTENANT - dt.timedelta(hours=1))
    assert LecteurComptes(lire=lecture)(frais, MAINTENANT) == []
    assert lecture.appels == []


def test_le_cache_evite_une_seconde_lecture_pendant_six_heures():
    lecture = Lecture()
    LecteurComptes(lire=lecture)(_compte(), MAINTENANT)
    assert len(LecteurComptes(lire=lecture)(_compte(), MAINTENANT + dt.timedelta(hours=5))) == 1
    assert lecture.appels == ["fredagain"]
    LecteurComptes(lire=lecture)(_compte(), MAINTENANT + dt.timedelta(hours=7))
    assert lecture.appels == ["fredagain", "fredagain"]


def test_deux_lectures_par_passage_au_plus():
    lecture = Lecture()
    lecteur = LecteurComptes(lire=lecture)
    lecteur(_compte("a"), MAINTENANT)
    lecteur(_compte("b"), MAINTENANT)
    with pytest.raises(LectureDifferee, match="passage"):
        lecteur(_compte("c"), MAINTENANT)
    assert lecture.appels == ["a", "b"]


def test_un_budget_par_jour(monkeypatch):
    monkeypatch.setattr(settings, "visuels_lectures_max_par_jour", 1)
    lecture = Lecture()
    LecteurComptes(lire=lecture)(_compte("a"), MAINTENANT)
    with pytest.raises(LectureDifferee, match="jour"):
        LecteurComptes(lire=lecture)(_compte("b"), MAINTENANT)
    LecteurComptes(lire=lecture)(_compte("b"), MAINTENANT + dt.timedelta(days=1))
    assert lecture.appels == ["a", "b"]


def test_le_quota_graph_met_en_pause_une_heure():
    with pytest.raises(LectureDifferee, match="quota"):
        LecteurComptes(lire=Lecture(erreur=QuotaGraphAtteint("code 4")))(_compte("a"), MAINTENANT)
    lecture = Lecture()
    with pytest.raises(LectureDifferee, match="pause"):
        LecteurComptes(lire=lecture)(_compte("b"), MAINTENANT + dt.timedelta(minutes=30))
    assert lecture.appels == []
    LecteurComptes(lire=lecture)(_compte("b"), MAINTENANT + dt.timedelta(minutes=61))
    assert lecture.appels == ["b"]


def test_une_panne_reseau_differe_sans_pause():
    erreur = httpx.ConnectError("boom")
    with pytest.raises(LectureDifferee) as exc:
        LecteurComptes(lire=Lecture(erreur=erreur))(_compte("a"), MAINTENANT)
    assert "EAAsecret" not in str(exc.value)
    lecture = Lecture()
    LecteurComptes(lire=lecture)(_compte("b"), MAINTENANT)
    assert lecture.appels == ["b"]


def test_un_compte_illisible_ne_rend_rien_sans_bloquer():
    lecteur = LecteurComptes(lire=Lecture(erreur=RuntimeError("Meta HTTP 400 (code 110) : Invalid user")))
    assert lecteur(_compte(), MAINTENANT) == []


def test_l_etat_vit_a_cote_du_jeton():
    LecteurComptes(lire=Lecture())(_compte(), MAINTENANT)
    assert (settings.meta_token_path.parent / FICHIER_ETAT).is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/content/test_lecture_artistes.py tests/sources/test_instagram.py -v`
Expected: FAIL — `ImportError: cannot import name 'CODES_QUOTA'` et `ModuleNotFoundError: No module named 'app.content.lecture_artistes'`

- [ ] **Step 3: Implement instagram.py**

Après `JETON_INVALIDE = 190` :

```python
# Codes Graph d'une limite d'appels atteinte (application, utilisateur, page,
# appels de l'API). Le quota est PARTAGE avec le pipeline : l'epuiser depuis la
# composition bloquerait l'ingestion.
CODES_QUOTA = frozenset({4, 17, 32, 613})

# Les champs demandes au compte cible. `children` est indispensable : un carrousel
# n'a pas de media_url propre et reviendrait vide sans lui.
CHAMPS_MEDIA = (
    "id,caption,media_type,media_url,thumbnail_url,permalink,"
    "timestamp,like_count,comments_count,"
    "children{media_type,media_url,thumbnail_url}"
)


class QuotaGraphAtteint(RuntimeError):
    """Meta refuse l'appel pour cause de quota. Message sans jeton."""
```

Avant `class InstagramAdapter` :

```python
def _code_erreur(reponse: httpx.Response):
    try:
        return (reponse.json().get("error") or {}).get("code")
    except ValueError:
        return None


def url_business_discovery(compte: str, limite: int) -> tuple[str, dict]:
    """L'appel business_discovery : notre compte, le leur en parametre.

    Meta n'expose pas de "lire le compte X" ; on lit NOTRE noeud utilisateur en
    lui demandant le champ `business_discovery` d'un autre compte.
    """
    base = f"https://graph.facebook.com/{settings.meta_api_version}"
    champs = (
        f"business_discovery.username({compte})"
        f"{{username,followers_count,media.limit({limite})"
        f"{{{CHAMPS_MEDIA}}}}}"
    )
    return (
        f"{base}/{settings.meta_ig_user_id}",
        {"fields": champs, "access_token": settings.meta_fb_access_token},
    )


def lire_compte(compte: str, limite: int, *, client: httpx.Client | None = None) -> dict:
    """Le payload business_discovery d'un compte. `QuotaGraphAtteint` sur un code
    de quota, `RuntimeError` lisible (sans jeton) sur toute autre erreur Graph."""
    url, params = url_business_discovery(compte, limite)
    propre = client is None
    # Header ASCII pur : httpx plante avant l'envoi sur un accent.
    client = client or httpx.Client(timeout=30.0, headers={"User-Agent": "KindaBot/1.0 (music-news-radar)"})
    try:
        reponse = client.get(url, params=params)
        if reponse.is_error:
            if _code_erreur(reponse) in CODES_QUOTA:
                raise QuotaGraphAtteint(_message_erreur(reponse))
            raise RuntimeError(_message_erreur(reponse))
        return reponse.json()
    finally:
        if propre:
            client.close()


def items_depuis_payload(payload: dict) -> list[RawItem]:
    decouverte = payload.get("business_discovery") or {}
    followers = decouverte.get("followers_count")
    items = []
    for noeud in (decouverte.get("media") or {}).get("data", []):
        permalink = noeud.get("permalink")
        # Sans permalink il n'y a pas de cle de dedup : l'item serait reingere a
        # chaque run.
        if not permalink:
            continue
        caption = noeud.get("caption")
        items.append(
            RawItem(
                url=permalink,
                title=titre_depuis_caption(caption),
                summary=_summary_with_metrics(caption, noeud, followers),
                published_at=_date(noeud.get("timestamp")),
                media=_media(noeud),
            )
        )
    return items
```

Dans `InstagramAdapter` : remplacer la constante de classe par `CHAMPS_MEDIA = CHAMPS_MEDIA`, et les corps :

```python
    def parse(self, payload: dict) -> list[RawItem]:
        return items_depuis_payload(payload)

    def construire_url(self) -> tuple[str, dict]:
        return url_business_discovery(self.compte, settings.instagram_max_items)

    def _appeler(self) -> dict:
        return lire_compte(self.compte, settings.instagram_max_items, client=None)
```

- [ ] **Step 4: Implement lecture_artistes.py**

```python
"""Lire en direct les derniers posts d'un compte d'artiste, a la composition.

Seulement quand la base n'a aucun visuel SUR LE SUJET pour un carrousel. Le quota
Graph est partage avec le pipeline (~120 comptes toutes les 3 h) et a deja rendu
le code 4 pendant une heure (2026-09-17) : d'ou deux budgets (par passage, par
jour), un cache par compte et une pause sur le quota. Etat dans
`visuels_lectures.json`, a cote du jeton.

Les posts lus ne sont JAMAIS inseres en base : ils y declencheraient un scoring
paye.
"""

import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path

import httpx

from app.config import settings
from app.content.actifs_artistes import PostArtiste
from app.pipeline.comptes_artistes import CompteArtiste
from app.sources.instagram import QuotaGraphAtteint, items_depuis_payload, lire_compte

logger = logging.getLogger(__name__)

FICHIER_ETAT = "visuels_lectures.json"
# Au-dela, une entree de cache n'est plus relue : elle est purgee a l'ecriture.
CACHE_PURGE = dt.timedelta(hours=24)


class LectureDifferee(RuntimeError):
    """Lecture impossible MAINTENANT : pause, budget, quota, reseau. Message
    affichable, jamais le jeton."""


def _date(valeur) -> dt.datetime | None:
    if not valeur:
        return None
    try:
        return dt.datetime.fromisoformat(valeur)
    except (TypeError, ValueError):
        return None


def _en_dict(item) -> dict:
    return {
        "url": item.url,
        "title": item.title,
        "summary": item.summary,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "media": item.media,
    }


def _post(compte: CompteArtiste, item: dict) -> PostArtiste:
    return PostArtiste(
        compte=compte,
        permalink=item["url"],
        titre=item.get("title") or "",
        texte=item.get("summary") or "",
        publie_le=_date(item.get("published_at")),
        media=list(item.get("media") or []),
    )


class LecteurComptes:
    """Un lecteur par passage de l'agent : c'est lui qui porte le budget du passage."""

    def __init__(self, *, lire=lire_compte, chemin: Path | None = None):
        self.lire = lire
        self.chemin = chemin or settings.meta_token_path.parent / FICHIER_ETAT
        self.lectures_passage = 0

    def _etat(self) -> dict:
        try:
            return json.loads(self.chemin.read_text())
        except (OSError, ValueError):
            return {}

    def _ecrire(self, etat: dict, maintenant: dt.datetime) -> None:
        cache = {
            handle: entree
            for handle, entree in (etat.get("cache") or {}).items()
            if (lu := _date(entree.get("lu_le"))) is not None and maintenant - lu < CACHE_PURGE
        }
        etat = {**etat, "cache": cache}
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        descripteur, temporaire = tempfile.mkstemp(dir=self.chemin.parent, suffix=".tmp")
        with os.fdopen(descripteur, "w") as fichier:
            json.dump(etat, fichier)
        os.replace(temporaire, self.chemin)

    def __call__(self, compte: CompteArtiste, maintenant: dt.datetime) -> list[PostArtiste]:
        if not settings.meta_fb_access_token or not settings.meta_ig_user_id:
            return []
        intervalle = dt.timedelta(hours=max(1, settings.instagram_intervalle_heures))
        if compte.dernier_appel is not None and maintenant - compte.dernier_appel < intervalle:
            # Le pipeline vient de lire ce compte : la base est a jour.
            return []
        etat = self._etat()
        en_cache = (etat.get("cache") or {}).get(compte.handle)
        lu_le = _date((en_cache or {}).get("lu_le"))
        if lu_le is not None and maintenant - lu_le < dt.timedelta(hours=settings.visuels_cache_lectures_heures):
            return [_post(compte, item) for item in en_cache.get("items", [])]
        pause = _date(etat.get("pause_jusqu_a"))
        if pause is not None and maintenant < pause:
            raise LectureDifferee(f"lecture des comptes d'artistes en pause jusqu'a {pause:%H:%M} UTC (quota Graph)")
        if self.lectures_passage >= settings.visuels_lectures_max_par_passage:
            raise LectureDifferee("budget de lectures de comptes du passage epuise")
        jour = maintenant.date().isoformat()
        du_jour = etat.get("lectures_du_jour", 0) if etat.get("jour") == jour else 0
        if du_jour >= settings.visuels_lectures_max_par_jour:
            raise LectureDifferee("budget de lectures de comptes du jour epuise")

        self.lectures_passage += 1
        etat.update(jour=jour, lectures_du_jour=du_jour + 1)
        try:
            payload = self.lire(compte.handle, settings.instagram_max_items)
        except QuotaGraphAtteint as exc:
            fin = maintenant + dt.timedelta(minutes=settings.visuels_pause_quota_minutes)
            etat["pause_jusqu_a"] = fin.isoformat()
            self._ecrire(etat, maintenant)
            raise LectureDifferee(
                f"quota Graph atteint : pause de {settings.visuels_pause_quota_minutes} min"
            ) from exc
        except httpx.HTTPError as exc:
            self._ecrire(etat, maintenant)
            raise LectureDifferee(f"lecture de @{compte.handle} impossible ({type(exc).__name__})") from exc
        except RuntimeError as exc:
            # Compte non professionnel, renomme, jeton expire (signale ailleurs) :
            # pas de visuel de ce cote, sans bloquer la composition.
            self._ecrire(etat, maintenant)
            logger.warning("Compte @%s illisible pour les visuels : %s", compte.handle, exc)
            return []
        items = [_en_dict(item) for item in items_depuis_payload(payload)]
        etat["cache"] = {
            **(etat.get("cache") or {}),
            compte.handle: {"lu_le": maintenant.isoformat(), "items": items},
        }
        self._ecrire(etat, maintenant)
        return [_post(compte, item) for item in items]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/content/test_lecture_artistes.py tests/sources/test_instagram.py -v`
Expected: PASS

- [ ] **Step 6: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/sources/instagram.py app/content/lecture_artistes.py tests/content/test_lecture_artistes.py tests/sources/test_instagram.py
git commit -m "$(cat <<'EOF'
feat(visuels): lecture en direct des comptes d'artistes, bornee

business_discovery reutilise par la composition : 2 lectures par passage, 24 par
jour, cache de 6 h, pause d'une heure sur le quota Graph, rien en base.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

### Task 10: Choisir les visuels avant de payer la composition

**Files:**
- Create: `app/content/choix_visuels.py`
- Modify: `app/schedule/store.py` (extraire `raison_reel_propre`)
- Test: `tests/content/test_choix_visuels.py` (créer)

**Interfaces:**
- Consumes: `visuels_autorises`, `actif`, `chemin_cache`, `credit_des_actifs`, `raison_sans_artiste`, `credit_video`, `classer`, `posts_en_base`, `LectureDifferee`, `construire_index`, `export.telecharger`, `store.source_video_reel`
- Produces:
  - `store.raison_reel_propre(article: Article) -> str | None` — l'éligibilité reel d'AVANT (vidéo, catégorie, crédit) ; `raison_ineligible_reel(article)` l'appelle pour l'instant (étendue en tâche 12)
  - `class AucunVisuel(RuntimeError)`, `class VisuelsDifferes(RuntimeError)` — messages affichables
  - `@dataclass ChoixVisuels(actifs: list[dict] = [], credit: str | None = None)`
  - `choisir_visuels(db: Session, article: Article, *, format_: FormatCreneau = FormatCreneau.CARROUSEL, maintenant: dt.datetime, lecteur=None, index: IndexComptes | None = None, telecharger=telecharger_media) -> ChoixVisuels`

- [ ] **Step 1: Write the failing tests**

Créer `tests/content/test_choix_visuels.py` :

```python
"""Le choix des visuels : propres s'ils sont autorises, sinon chez les artistes
cites (base, puis lecture en direct pour un carrousel), copies en local."""

import datetime as dt
import io
import itertools

import pytest
from PIL import Image

from app.content.actifs_artistes import PostArtiste
from app.content.choix_visuels import AucunVisuel, VisuelsDifferes, choisir_visuels
from app.content.lecture_artistes import LectureDifferee
from app.content.media import entree
from app.config import settings
from app.models import Article, FormatCreneau, Source, SourceCategorie, SourceType
from app.pipeline.comptes_artistes import construire_index

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
DATE_ACTU = MAINTENANT - dt.timedelta(hours=5)
TITRE = "GTA VI soundtrack : Fred again.. au generique"
_numeros = itertools.count()


def _image():
    tampon = io.BytesIO()
    Image.new("RGB", (1200, 800), (1, 2, 3)).save(tampon, "JPEG")
    return tampon.getvalue()


def _source(db, nom, handle=None, categorie=SourceCategorie.ARTISTE, type_=SourceType.INSTAGRAM):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{nom}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre=TITRE, media=(), mentioned=None, date=DATE_ACTU, url=None):
    n = next(_numeros)
    article = Article(source_id=source.id, url=url or f"https://www.instagram.com/p/A{n}/", title=titre,
                      raw_hash=f"x{n:063d}", published_at=date, media=list(media), mentioned_artists=mentioned)
    db.add(article)
    db.commit()
    return article


@pytest.fixture()
def fred(db_session):
    return _source(db_session, "Fred again.. (IG)", "fredagain")


@pytest.fixture()
def guettapen(db_session):
    return _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)


class Telechargement:
    def __init__(self, echecs=()):
        self.urls, self.echecs = [], set(echecs)

    def __call__(self, url):
        self.urls.append(url)
        return (None, "HTTP 403") if url in self.echecs else (_image(), ".jpg")


class Lecteur:
    def __init__(self, posts=(), erreur=None):
        self.posts, self.erreur, self.appels = list(posts), erreur, []

    def __call__(self, compte, maintenant):
        self.appels.append(compte.handle)
        if self.erreur is not None:
            raise self.erreur
        return [p for p in self.posts if p.compte.source_id == compte.source_id]


def _choisir(db, article, **options):
    options.setdefault("telecharger", Telechargement())
    return choisir_visuels(db, article, maintenant=MAINTENANT, **options)


def test_la_photo_de_presse_reste_le_visuel_sans_copie(db_session):
    billboard = _source(db_session, "Billboard (RSS)", categorie=SourceCategorie.MEDIA, type_=SourceType.RSS)
    article = _article(db_session, billboard, media=[entree("photo", "https://billboard/og.jpg")])
    telechargement = Telechargement()
    choix = _choisir(db_session, article, telecharger=telechargement)
    assert [(a["origine"], a["url"], a["fichier"]) for a in choix.actifs] == [
        ("article", "https://billboard/og.jpg", None),
    ]
    assert choix.credit is None
    assert telechargement.urls == []


def test_le_post_d_un_artiste_est_credite_a_son_compte(db_session, fred):
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/fred-propre.jpg")])
    assert _choisir(db_session, article).credit == "@fredagain"


def test_un_media_instagram_prend_la_photo_de_l_artiste_cite(db_session, fred, guettapen):
    article = _article(db_session, guettapen, media=[entree("photo", "https://cdn.ig/guettapen.jpg")],
                       mentioned=["Fred again.."])
    post_fred = _article(db_session, fred, "My track on the GTA soundtrack",
                         media=[entree("photo", "https://cdn.ig/fred.jpg")], date=DATE_ACTU - dt.timedelta(days=1))
    telechargement, lecteur = Telechargement(), Lecteur()

    choix = _choisir(db_session, article, telecharger=telechargement, lecteur=lecteur)

    (visuel,) = choix.actifs
    assert (visuel["origine"], visuel["compte"], visuel["permalink"], visuel["article_id"], visuel["url"]) == (
        "artiste", "@fredagain", post_fred.url, post_fred.id, "https://cdn.ig/fred.jpg",
    )
    assert visuel["fichier"].startswith("file://")
    assert str(settings.visuels_cache_dir.resolve()) in visuel["fichier"]
    assert choix.credit == "@fredagain"
    assert telechargement.urls == ["https://cdn.ig/fred.jpg"]
    assert lecteur.appels == []


def test_sans_artiste_suivi_cite_aucun_visuel(db_session, guettapen):
    article = _article(db_session, guettapen, "Actu sans artiste", media=[entree("photo", "https://cdn.ig/g.jpg")])
    with pytest.raises(AucunVisuel, match="compte media Instagram"):
        _choisir(db_session, article)


def test_la_lecture_en_direct_quand_la_base_n_a_rien_sur_le_sujet(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    compte = construire_index(db_session).comptes[fred.id]
    en_direct = PostArtiste(compte, "https://www.instagram.com/p/LIVE/", "GTA soundtrack day", "",
                            MAINTENANT - dt.timedelta(hours=2), [entree("photo", "https://cdn.ig/live.jpg")])
    lecteur = Lecteur([en_direct])
    choix = _choisir(db_session, article, lecteur=lecteur)
    assert lecteur.appels == ["fredagain"]
    assert [(a["permalink"], a["article_id"]) for a in choix.actifs] == [("https://www.instagram.com/p/LIVE/", None)]


def test_une_lecture_differee_sans_rien_en_base_differe_la_composition(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    with pytest.raises(VisuelsDifferes, match="quota"):
        _choisir(db_session, article, lecteur=Lecteur(erreur=LectureDifferee("quota Graph atteint")))


def test_un_portrait_recent_en_base_evite_le_report(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    _article(db_session, fred, "Paris merci", media=[entree("photo", "https://cdn.ig/paris.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))
    choix = _choisir(db_session, article, lecteur=Lecteur(erreur=LectureDifferee("quota Graph atteint")))
    assert [a["url"] for a in choix.actifs] == ["https://cdn.ig/paris.jpg"]


def test_des_copies_toutes_en_echec_ne_donnent_aucun_visuel(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    _article(db_session, fred, "GTA soundtrack", media=[entree("photo", "https://cdn.ig/expire.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))
    with pytest.raises(AucunVisuel):
        _choisir(db_session, article, telecharger=Telechargement(echecs={"https://cdn.ig/expire.jpg"}))


def test_trois_images_au_plus(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    _article(db_session, fred, "GTA soundtrack", date=DATE_ACTU - dt.timedelta(days=1),
             media=[entree("photo", f"https://cdn.ig/{n}.jpg") for n in range(5)])
    assert len(_choisir(db_session, article).actifs) == 3


def test_un_reel_prend_la_video_de_l_artiste_sans_la_copier(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."],
                       media=[entree("video", "https://cdn.ig/guettapen.mp4", duration_ms=20_000)])
    reel = _article(db_session, fred, "GTA soundtrack", url="https://www.instagram.com/reel/FRED/",
                    media=[entree("video", None, preview_url="https://cdn.ig/v.jpg")],
                    date=DATE_ACTU - dt.timedelta(days=1))
    telechargement, lecteur = Telechargement(), Lecteur()

    choix = _choisir(db_session, article, format_=FormatCreneau.REEL, telecharger=telechargement, lecteur=lecteur)

    (video,) = choix.actifs
    assert (video["type"], video["url"], video["permalink"], video["compte"]) == (
        "video", None, reel.url, "@fredagain",
    )
    assert choix.credit == "@fredagain"
    assert telechargement.urls == [] and lecteur.appels == []


def test_un_reel_sans_video_d_artiste_n_a_aucun_visuel(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    _article(db_session, fred, "GTA soundtrack", media=[entree("photo", "https://cdn.ig/p.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))
    with pytest.raises(AucunVisuel):
        _choisir(db_session, article, format_=FormatCreneau.REEL)


def test_un_reel_propre_garde_sa_video(db_session, fred):
    video = entree("video", "https://cdn.ig/fred.mp4", duration_ms=20_000)
    article = _article(db_session, fred, media=[video])
    choix = _choisir(db_session, article, format_=FormatCreneau.REEL)
    assert [(a["origine"], a["url"], a["permalink"]) for a in choix.actifs] == [
        ("article", "https://cdn.ig/fred.mp4", article.url),
    ]
    assert choix.credit == "@fredagain"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/content/test_choix_visuels.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.content.choix_visuels'`

- [ ] **Step 3: Extract `raison_reel_propre` in store.py**

```python
def raison_reel_propre(article: Article) -> str | None:
    """L'eligibilite reel par les medias de l'article lui-meme : video, categorie
    du compte, credit. None si elle est acquise."""
    return _raison_ineligible_reel(article, source_video_reel(article))


def raison_ineligible_reel(article: Article) -> str | None:
    """None si l'article peut partir en reel, sinon la raison, affichable."""
    return raison_reel_propre(article)
```

- [ ] **Step 4: Implement choix_visuels.py**

```python
"""Choisir les visuels d'un post, AVANT de payer sa composition.

Dans l'ordre (design du 2026-09-17) :
1. les medias de l'article, s'ils sont autorises (photo de presse, post d'un
   compte d'artiste, de festival, de label, de club) ;
2. sinon les posts des artistes SUIVIS cites : en base, puis -- carrousel
   seulement, sans rien sur le sujet en base -- lus en direct, avec un budget.

Les images d'artistes sont COPIEES en local : les URL du CDN Instagram sont
signees et expirent, et le gel vient ~22 h apres la composition. Une video de
reel ne l'est pas : le gel la telecharge (API) ou la recupere (yt-dlp).

Deux issues sans visuel : `AucunVisuel` (l'article n'en a pas, il sort de la
file) et `VisuelsDifferes` (la lecture en direct attend son budget ou le quota :
on reessaie au passage suivant, rien n'est exclu).
"""

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import settings
from app.content.actifs_artistes import Retenu, classer, posts_en_base
from app.content.credit import credit_video
from app.content.export import telecharger as telecharger_media
from app.content.lecture_artistes import LectureDifferee
from app.content.media import PHOTO, VIDEO, entree, videos_exploitables
from app.content.visuels import (
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    actif,
    chemin_cache,
    credit_des_actifs,
    raison_sans_artiste,
    visuels_autorises,
)
from app.models import Article, FormatCreneau
from app.pipeline.comptes_artistes import IndexComptes, construire_index
from app.schedule.store import raison_reel_propre, source_video_reel

logger = logging.getLogger(__name__)

EXTENSIONS_IMAGE = (".jpg", ".jpeg", ".png", ".webp", ".gif")


class AucunVisuel(RuntimeError):
    """Aucun visuel autorise pour cet article. Message affichable."""


class VisuelsDifferes(RuntimeError):
    """Les visuels ne peuvent pas etre choisis MAINTENANT. Message affichable."""


@dataclass
class ChoixVisuels:
    actifs: list[dict] = field(default_factory=list)
    credit: str | None = None


def _propres(article: Article, format_: FormatCreneau) -> ChoixVisuels | None:
    medias = visuels_autorises(article)
    compte = credit_video(article)

    def en_actif(entree_media: dict) -> dict:
        return actif(entree_media, origine=ORIGINE_ARTICLE, compte=compte, permalink=article.url,
                     article_id=article.id)

    if format_ == FormatCreneau.REEL:
        if raison_reel_propre(article) is not None:
            return None
        if source_video_reel(article) == "api":
            duree_min = settings.publication_reel_duree_min_s * 1000
            video = next(
                v for v in videos_exploitables(medias, int(settings.publication_reel_duree_max_s * 1000))
                if v.get("duration_ms") is None or v["duration_ms"] >= duree_min
            )
        else:
            video = next((m for m in medias if m.get("type") == VIDEO), entree(VIDEO, None))
        return ChoixVisuels([en_actif(video)], compte)

    photos = [m for m in medias if m.get("type") == PHOTO and m.get("url")]
    if photos:
        return ChoixVisuels([en_actif(m) for m in photos], compte)
    vignette = next((m for m in medias if m.get("preview_url")), None)
    if vignette is not None:
        return ChoixVisuels([en_actif(vignette)], compte)
    return None


def _en_actif_artiste(retenu: Retenu, entree_media: dict, fichier: str | None = None) -> dict:
    return actif(
        entree_media,
        origine=ORIGINE_ARTISTE,
        compte=f"@{retenu.post.compte.handle}",
        permalink=retenu.post.permalink,
        article_id=retenu.post.article_id,
        publie_le=retenu.post.publie_le,
        fichier=fichier,
    )


def _copier_images(retenus: list[Retenu], telecharger) -> list[dict]:
    actifs: list[dict] = []
    vues: set[str] = set()
    for retenu in retenus:
        for entree_media in retenu.medias:
            if len(actifs) >= settings.visuels_max_photos:
                return actifs
            url = entree_media.get("url") if entree_media.get("type") == PHOTO else entree_media.get("preview_url")
            if not url or url in vues:
                continue
            vues.add(url)
            octets, extension = telecharger(url)
            if octets is None:
                logger.info("Visuel d'artiste inaccessible (%s) : %s", url, extension)
                continue
            chemin = chemin_cache(url, extension if extension in EXTENSIONS_IMAGE else ".jpg")
            chemin.parent.mkdir(parents=True, exist_ok=True)
            chemin.write_bytes(octets)
            actifs.append(_en_actif_artiste(retenu, entree_media, chemin.resolve().as_uri()))
    return actifs


def choisir_visuels(
    db: Session,
    article: Article,
    *,
    format_: FormatCreneau = FormatCreneau.CARROUSEL,
    maintenant: dt.datetime,
    lecteur=None,
    index: IndexComptes | None = None,
    telecharger=telecharger_media,
) -> ChoixVisuels:
    propres = _propres(article, format_)
    if propres is not None:
        return propres

    index = index if index is not None else construire_index(db)
    comptes = index.cites(
        textes=[article.title, article.summary],
        noms=article.mentioned_artists or [],
        exclure_source_id=article.source_id,
    )
    if not comptes:
        raise AucunVisuel(
            raison_reel_propre(article) if format_ == FormatCreneau.REEL else raison_sans_artiste(article)
        )

    posts = posts_en_base(db, comptes, maintenant)
    retenus = classer(article, posts, format_=format_, maintenant=maintenant)

    if format_ == FormatCreneau.REEL:
        # Jamais de lecture en direct pour un reel : son eligibilite s'est decidee
        # a l'arbitrage, sans reseau, sur la base.
        if not retenus:
            raise AucunVisuel("aucune video recente des artistes suivis cites")
        video = _en_actif_artiste(retenus[0], retenus[0].medias[0])
        return ChoixVisuels([video], video["compte"])

    differe: LectureDifferee | None = None
    if lecteur is not None and not any(r.sur_le_sujet for r in retenus):
        lus = []
        for compte in comptes:
            try:
                lus += lecteur(compte, maintenant)
            except LectureDifferee as exc:
                differe = exc
                break
        connus = {p.permalink for p in posts}
        nouveaux = [p for p in lus if p.permalink not in connus]
        if nouveaux:
            retenus = classer(article, [*posts, *nouveaux], format_=format_, maintenant=maintenant)

    actifs = _copier_images(retenus, telecharger)
    if actifs:
        return ChoixVisuels(actifs, credit_des_actifs(actifs))
    if differe is not None:
        raise VisuelsDifferes(str(differe)) from differe
    raise AucunVisuel("aucun visuel recent des artistes suivis cites (hors fenetre, hors sujet ou inaccessible)")
```

Vérifier l'absence de cycle : `uv run python -c "import app.content.choix_visuels, app.schedule.runner"`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/content/test_choix_visuels.py tests/schedule/test_schedule_store_reels.py -v`
Expected: PASS

- [ ] **Step 6: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/content/choix_visuels.py app/schedule/store.py tests/content/test_choix_visuels.py
git commit -m "$(cat <<'EOF'
feat(visuels): choisir les visuels avant la composition

Propres s'ils sont autorises, sinon posts des artistes suivis cites (base, puis
lecture en direct pour un carrousel), images copiees en local ; AucunVisuel ou
VisuelsDifferes.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

### Task 11: Le post porte ses visuels : crédit « 📸 @artiste », source nommée, vidéo d'artiste au gel

**Files:**
- Modify: `app/content/compose.py`, `app/content/legende.py`, `app/publish/preparation_reel.py`, `app/schemas.py`
- Test: `tests/content/test_compose.py`, `tests/content/test_legende.py`, `tests/publish/test_publish_preparation_reel.py`, `tests/schedule/test_schedule_runner_reels.py`

**Interfaces:**
- Consumes: `ChoixVisuels`, `image_de_fond`, `urls_interdites`, `origine_interdite`, `media.CLES`
- Produces:
  - `create_post(db, article, draft, template="kinda_post", visuels: ChoixVisuels | None = None) -> Post` — avec `visuels` : `assets = visuels.actifs`, `credit = visuels.credit`, fond = `image_de_fond(actifs)` ; sans : comportement actuel (`assets` NULL)
  - `legende.nom_du_media(nom: str | None) -> str | None`
  - `texte_legende(post)` : `📸 <credit>` / `🎥 <credit>` sans « via » ; `Source : <nom> · <url>` ou `Source : <url>`
  - `preparation_reel._porteur_video(post, article)` — l'actif vidéo de `post.assets`, sinon l'article (jamais un article d'origine interdite), sinon `None`
  - `source_de_la_video(post, article)` : slides `media` vidéo non interdites, puis le porteur
  - `PostOut.assets: list[dict] | None = None`, `PostOut.credit: str | None = None`

- [ ] **Step 1: Write the failing tests**

Dans `tests/content/test_legende.py`, remplacer les trois tests du crédit (`test_le_credit_d_un_reel_precede_la_source`, `test_le_credit_d_un_carrousel_a_son_propre_pictogramme`, `test_le_credit_n_est_jamais_coupe`) par :

```python
def test_le_credit_d_un_reel_precede_la_source():
    post = _post()
    post.credit, post.template = "@saralandry", "kinda_reel"
    assert texte_legende(post) == (
        "Une legende 🎧 ✨\n\n#electro\n\n🎥 @saralandry\n\nSource : https://tsugi.fr/a"
    )


def test_le_credit_d_un_carrousel_nomme_tous_les_artistes():
    post = _post(hashtags=())
    post.credit, post.template = "@fredagain @pinkpantheress", "kinda_post"
    assert "📸 @fredagain @pinkpantheress\n\nSource : https://tsugi.fr/a" in texte_legende(post)


def test_la_source_nomme_le_media_d_ou_vient_l_info():
    post = _post(hashtags=(), url="https://www.instagram.com/p/G/")
    post.source_name, post.credit, post.template = "Booska-P (IG)", "@fredagain", "kinda_post"
    assert texte_legende(post).endswith("📸 @fredagain\n\nSource : Booska-P · https://www.instagram.com/p/G/")


def test_le_credit_et_la_source_ne_sont_jamais_coupes():
    post = _post(caption="x" * 3000)
    post.source_name, post.credit, post.template = "Billboard (RSS)", "@saralandry", "kinda_reel"
    texte = texte_legende(post)
    assert len(texte) <= LEGENDE_MAX
    assert texte.endswith("🎥 @saralandry\n\nSource : Billboard · https://tsugi.fr/a")


def test_nom_du_media():
    from app.content.legende import nom_du_media

    assert nom_du_media("Billboard (RSS)") == "Billboard"
    assert nom_du_media("DJ Snake (FR) (IG)") == "DJ Snake"
    assert nom_du_media("") is None
    assert nom_du_media(None) is None
```

Ajouter à `tests/content/test_compose.py` (et `from app.content.choix_visuels import ChoixVisuels`, `from app.content.visuels import actif`, plus `from app.content.media import entree` s'il n'y est pas déjà) :

```python
def test_create_post_garde_les_visuels_choisis(db_session):
    article = _article(db_session)
    choisi = actif(entree("photo", "https://cdn.ig/fred.jpg"), origine="artiste", compte="@fredagain",
                   permalink="https://www.instagram.com/p/F/", fichier="file:///cache/fred.jpg")
    post = create_post(db_session, article, _draft(), visuels=ChoixVisuels([choisi], "@fredagain"))
    assert post.assets == [choisi]
    assert post.credit == "@fredagain"
    assert post.slides[0]["image_url"] == "file:///cache/fred.jpg"


def test_create_post_sans_visuel_choisi_laisse_la_cover_sans_fond(db_session):
    article = _article(db_session)
    article.media = [entree("photo", "https://tsugi.fr/photo.jpg")]
    db_session.commit()
    post = create_post(db_session, article, _draft(), visuels=ChoixVisuels())
    assert (post.assets, post.credit, post.slides[0]["image_url"]) == ([], None, None)


def test_create_post_sans_choix_reste_historique(db_session):
    post = create_post(db_session, _article(db_session), _draft())
    assert post.assets is None
```

Ajouter à `tests/publish/test_publish_preparation_reel.py` (et `from app.content.visuels import actif`, `from app.models import SourceCategorie, SourceType`) :

```python
REEL_FRED = "https://www.instagram.com/reel/FRED/"


def _post_avec_actif(video):
    post = _post()
    post.assets = [actif(video, origine="artiste", compte="@fredagain", permalink=REEL_FRED)]
    return post


def _article_media_ig(*medias):
    source = SimpleNamespace(type=SourceType.INSTAGRAM, categorie=SourceCategorie.MEDIA, url="ig:guettapen")
    return SimpleNamespace(media=list(medias), url="https://www.instagram.com/reel/GUET/", source=source)


def test_la_video_d_un_artiste_vient_de_son_permalink():
    assert source_de_la_video(_post_avec_actif(SANS_URL), _article_media_ig(VIDEO)) == (YTDLP, REEL_FRED)
    assert source_de_la_video(_post_avec_actif(VIDEO), _article_media_ig()) == (API, "https://cdn/v.mp4")


def test_jamais_la_video_d_un_compte_media():
    slide = {"kind": "media", "type": "video", "url": "https://cdn/v.mp4"}
    assert source_de_la_video(_post("kinda_reel", "@x", slide), _article_media_ig(VIDEO)) is None
    assert source_de_la_video(_post(), _article_media_ig(VIDEO)) is None


def test_un_lien_d_artiste_expire_retombe_sur_son_permalink(tmp_path):
    recuperer = Recuperer()
    outils = {**_outils(), "telecharger": _expire}
    preparer_reel(_post_avec_actif(VIDEO), _article_media_ig(), tmp_path, recuperer_ytdlp=recuperer, **outils)
    assert [appel[0] for appel in recuperer.appels] == [REEL_FRED]
```

(`_expire` existe déjà plus bas dans ce fichier : placer ces tests après sa définition.)

Dans `tests/schedule/test_schedule_runner_reels.py`, `test_la_chaine_complete_d_un_reel`, remplacer `assert "🎥 via @saralandry" in outils.legendes[0]` par `assert "🎥 @saralandry" in outils.legendes[0]`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/content/test_legende.py tests/content/test_compose.py tests/publish/test_publish_preparation_reel.py -v`
Expected: FAIL — « via » encore présent, `TypeError: create_post() got an unexpected keyword argument 'visuels'`, `source_de_la_video` rend `(API, ...)` pour une vidéo de compte média.

- [ ] **Step 3: Implement compose.py and schemas.py**

`app/content/compose.py` : importer `from app.content.visuels import image_de_fond, visuels_autorises` (ce dernier déjà là depuis la tâche 7). Signature et corps de `create_post` :

```python
def create_post(
    db: Session, article: Article, draft: PostDraft, template: str = "kinda_post", visuels=None
) -> Post:
    """Persiste un brouillon : les quatre variantes, une cover sans retenue, et les
    visuels choisis (`ChoixVisuels`, cf. `choix_visuels.py`).

    Sans `visuels` (appels anterieurs a la regle, tests) : le fond vient des medias
    AUTORISES de l'article et `assets` reste NULL. Les champs sont DENORMALISES
    expres : l'article sera purge a 14 jours, le post doit rester lisible sans lui.
    """
    premiere = draft.variantes[0]
    if visuels is not None:
        actifs, credit, fond = list(visuels.actifs), visuels.credit, image_de_fond(visuels.actifs)
    else:
        actifs, credit, fond = None, credit_video(article), _fond_par_defaut(article)
    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=article.source.name if article.source is not None else None,
        credit=credit,
        assets=actifs,
        template=template,
        caption=draft.caption,
        hashtags=draft.hashtags or None,
        # La cover affiche la variante 1 en attendant un choix, pour que le rendu
        # soit possible immediatement ; `variants.retenue` reste null.
        slides=[
            {
                "kind": "cover",
                "headline": premiere.headline,
                "title": premiere.title,
                "subtitle": premiere.subtitle,
                "image_url": fond,
            }
        ],
        variants={
            "variantes": [v.model_dump() for v in draft.variantes],
            "retenue": None,
        },
    )
    db.add(post)
    db.commit()
    logger.info("post %s cree pour l'article %s", post.id, article.id)
    return post
```

`app/schemas.py`, classe `PostOut`, après `source_name` :

```python
    # Les visuels retenus (origine, compte, permalink, copie locale) et le credit
    # qui en decoule. NULL = post anterieur a la regle des visuels.
    assets: list[dict] | None = None
    credit: str | None = None
```

- [ ] **Step 4: Implement legende.py**

```python
"""Le texte publie sous un post : legende, hashtags, credit, source.

Commun a l'export zip et a la publication automatique -- un seul endroit ou
respecter les limites de Meta : 2200 caracteres et 30 hashtags.

Deux mentions distinctes (decision du 2026-09-17) : le CREDIT nomme le
proprietaire des visuels (« 📸 @artiste »), la SOURCE nomme le media d'ou vient
l'information.
"""

import re

LEGENDE_MAX = 2200
HASHTAGS_MAX = 30
# « Billboard (RSS) », « Booska-P (IG) » : suffixes de nommage des sources.
_PARENTHESES = re.compile(r"\s*\([^)]*\)")


def nom_du_media(nom: str | None) -> str | None:
    return _PARENTHESES.sub("", nom or "").strip() or None


def texte_legende(post) -> str:
    hashtags = (post.hashtags or [])[:HASHTAGS_MAX]
    parties = [post.caption]
    if hashtags:
        parties.append(" ".join(hashtags))
    credit = getattr(post, "credit", None)
    if credit:
        pictogramme = "🎥" if getattr(post, "template", None) == "kinda_reel" else "📸"
        parties.append(f"{pictogramme} {credit}")
    nom = nom_du_media(getattr(post, "source_name", None))
    parties.append(f"Source : {nom} · {post.article_url}" if nom else f"Source : {post.article_url}")
    texte = "\n\n".join(parties)
    if len(texte) <= LEGENDE_MAX:
        return texte
    # On coupe la legende elle-meme, jamais le credit ni la source : citer le
    # compte et l'editeur est le minimum de bonne foi quand on reprend leurs medias.
    fin = "\n\n".join(parties[1:])
    place = LEGENDE_MAX - len(fin) - len("\n\n") - len("…")
    return post.caption[: max(place, 0)].rstrip() + "…" + "\n\n" + fin
```

Note : `SimpleNamespace` des tests existants n'a pas `source_name` → `getattr(..., None)` → URL seule, les attentes existantes sans crédit restent vraies.

- [ ] **Step 5: Implement preparation_reel.py**

Importer `from types import SimpleNamespace`, `from app.content.media import CLES` (compléter l'import existant) et `from app.content.visuels import origine_interdite, urls_interdites`. Remplacer `source_de_la_video` par :

```python
def _porteur_video(post, article):
    """Ce qui porte la video du reel : l'actif video choisi a la composition (le
    post d'un artiste cite), sinon l'article -- jamais un compte media Instagram."""
    for choisi in getattr(post, "assets", None) or []:
        if choisi.get("type") == VIDEO:
            return SimpleNamespace(url=choisi.get("permalink"), media=[{cle: choisi.get(cle) for cle in CLES}])
    if article is None or origine_interdite(getattr(article, "source", None)):
        return None
    return article


def source_de_la_video(post, article) -> tuple[str, str] | None:
    interdites = urls_interdites(article)
    for slide in post.slides[1:]:
        if (
            slide.get("kind") == "media" and slide.get("type") == VIDEO and slide.get("url")
            and slide["url"] not in interdites
        ):
            return API, slide["url"]
    porteur = _porteur_video(post, article)
    if porteur is None:
        return None
    videos = videos_exploitables(porteur.media, int(settings.publication_reel_duree_max_s * 1000))
    if videos:
        return API, videos[0]["url"]
    if video_recuperable(porteur.media, porteur.url):
        return YTDLP, porteur.url
    return None
```

Dans `preparer_reel`, remplacer l'appel

```python
    source = _video_source(*origine, article, dossier, plafond, telecharger, recuperer_ytdlp)
```

par

```python
    # Le repli yt-dlp d'un lien expire vise le permalink du PORTEUR : le post de
    # l'artiste quand la video vient de lui.
    porteur = _porteur_video(post, article) or article
    source = _video_source(*origine, porteur, dossier, plafond, telecharger, recuperer_ytdlp)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/content/test_legende.py tests/content/test_compose.py tests/publish/test_publish_preparation_reel.py tests/content/test_credit.py tests/api/test_posts_api.py tests/content/test_export.py tests/schedule/test_schedule_runner_reels.py -v`
Expected: PASS

- [ ] **Step 7: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/content/compose.py app/content/legende.py app/publish/preparation_reel.py app/schemas.py tests/content/test_legende.py tests/content/test_compose.py tests/publish/test_publish_preparation_reel.py tests/schedule/test_schedule_runner_reels.py
git commit -m "$(cat <<'EOF'
feat(post): visuels portes par le post, credit sans via, source nommee

create_post garde les actifs choisis ; legende « 📸 @artiste » et « Source :
media · url » ; le gel d'un reel prend la video de l'artiste, jamais celle d'un
compte media.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

## Partie C — La file, l'API, le dashboard

### Task 12: La file et le runner : pas de visuel autorisé, pas de publication

**Files:**
- Modify: `app/schedule/store.py`, `app/schedule/runner.py`, `scripts/run_publication.py`, `app/api/posts.py`
- Test: `tests/schedule/test_file_visuels.py` (créer)

**Interfaces:**
- Consumes: `construire_index`, `classer`, `posts_en_base`, `raison_sans_artiste`, `choisir_visuels`, `AucunVisuel`, `VisuelsDifferes`, `ChoixVisuels`, `LecteurComptes`, `origine_interdite`
- Produces:
  - `store.comptes_cites(article: Article, index: IndexComptes) -> list[CompteArtiste]`
  - `store.raison_sans_visuel(article: Article, index: IndexComptes | None = None) -> str | None` — sans réseau
  - `store.raison_ineligible_reel(article: Article, *, index: IndexComptes | None = None, maintenant: dt.datetime | None = None) -> str | None` — chemin propre, sinon vidéo d'artiste en base
  - `candidats_eligibles` : garde carrousel = `raison_sans_visuel` ; garde reel = `raison_ineligible_reel(a, index=, maintenant=)` ; index construit une fois
  - `runner.tourner(..., choisir=None)` — `choisir(db, article, format_, maintenant) -> ChoixVisuels` ; défaut : `choisir_visuels` sans lecteur
  - `runner._menage` supprime aussi les copies de `visuels_cache_dir` de plus de `visuels_cache_jours`
  - `run_publication.choix_du_passage() -> Callable` — un `LecteurComptes` neuf par appel
  - `POST /articles/{id}/posts` : `AucunVisuel` → post sans visuel (`assets == []`) ; `VisuelsDifferes` → 503

- [ ] **Step 1: Write the failing tests**

Créer `tests/schedule/test_file_visuels.py` :

```python
"""Sans visuel autorise, un article reste au dashboard mais n'entre pas dans la
file ; le choix des visuels precede la composition payee."""

import datetime as dt
import itertools
import os

import pytest

import scripts.run_publication as script
from app.config import settings
from app.content.choix_visuels import ChoixVisuels, VisuelsDifferes
from app.content.compose import CoverVariante, PostDraft
from app.content.media import entree
from app.content.visuels import actif, chemin_cache
from app.models import Article, Creneau, EtatCreneau, FormatCreneau, Post, Source, SourceCategorie, SourceType
from app.schedule.runner import _menage, tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import candidats_eligibles, raison_ineligible_reel, raison_sans_visuel

UTC = dt.timezone.utc
BASE = dt.datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
H0 = prochains_horaires(BASE, 2)[0]
_numeros = itertools.count()


@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)


def _source(db, nom, handle, categorie):
    source = Source(name=nom, type=SourceType.INSTAGRAM, url=f"ig:{handle}", categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre="GTA VI soundtrack : Fred again.. au generique", media=None, mentioned=None,
             url=None, heures=2):
    n = next(_numeros)
    article = Article(source_id=source.id, url=url or f"https://www.instagram.com/p/F{n}/", title=titre,
                      raw_hash=f"f{n:063d}", relevance_score=85, hotness_score=30,
                      published_at=BASE - dt.timedelta(hours=heures), media=media, mentioned_artists=mentioned)
    db.add(article)
    db.commit()
    return article


@pytest.fixture()
def fred(db_session):
    return _source(db_session, "Fred again.. (IG)", "fredagain", SourceCategorie.ARTISTE)


@pytest.fixture()
def guettapen(db_session):
    return _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)


def _draft():
    variante = CoverVariante(headline="**Accroche**", title="**Titre**", subtitle="**Sous**")
    return PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=["#electro"])


class Outils:
    def __init__(self):
        self.composes, self.notifications = [], []

    def composer(self, article):
        self.composes.append(article.id)
        return _draft()

    def tourner(self, db, maintenant=BASE, choisir=None):
        return tourner(
            db, maintenant, mode="apercu", composer=self.composer,
            preparer=lambda post, article, dossier: ["p_01.jpg"],
            publier=lambda dossier, noms, legende, vraiment: "ig42",
            notifier=lambda titre, message: self.notifications.append(message),
            horloge=lambda: maintenant, choisir=choisir,
        )


def _epingler(db, article):
    creneau = Creneau(publier_a=H0, article_id=article.id, etat=EtatCreneau.PROGRAMME, epingle=True)
    db.add(creneau)
    db.commit()
    return creneau


def test_raison_sans_visuel(db_session, fred, guettapen):
    sans = _article(db_session, guettapen, "Actu sans artiste", media=[entree("photo", "https://cdn.ig/g.jpg")])
    avec = _article(db_session, guettapen, media=[entree("photo", "https://cdn.ig/g.jpg")], mentioned=["Fred again.."])
    propre = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    assert raison_sans_visuel(sans) == "visuels d'un compte media Instagram interdits, et aucun artiste suivi cite"
    assert raison_sans_visuel(avec) is None
    assert raison_sans_visuel(propre) is None
    assert [c.article_id for c in candidats_eligibles(db_session, BASE)] == [avec.id, propre.id]


def test_un_reel_de_media_devient_eligible_par_la_video_d_un_artiste(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."],
                       media=[entree("video", "https://cdn.ig/g.mp4", duration_ms=20_000)])
    assert "categorie media" in raison_ineligible_reel(article, maintenant=BASE)
    _article(db_session, fred, "GTA soundtrack", url="https://www.instagram.com/reel/FRED/",
             media=[entree("video", None, preview_url="https://cdn.ig/v.jpg")], heures=20)
    assert raison_ineligible_reel(article, maintenant=BASE) is None


def test_un_media_sans_visuel_n_est_jamais_programme(db_session, guettapen):
    _article(db_session, guettapen, "Actu sans artiste", media=[entree("photo", "https://cdn.ig/g.jpg")])
    outils = Outils()
    outils.tourner(db_session)
    assert outils.composes == []
    assert all(c.article_id is None for c in db_session.query(Creneau).all())


def test_un_article_epingle_sans_visuel_est_exclu_sans_composition(db_session, guettapen):
    article = _article(db_session, guettapen, "Actu sans artiste", media=[entree("photo", "https://cdn.ig/g.jpg")])
    creneau = _epingler(db_session, article)
    outils = Outils()
    outils.tourner(db_session)
    db_session.refresh(article)
    db_session.refresh(creneau)
    assert outils.composes == []
    assert article.file_exclu_raison.startswith("composition impossible : visuels d'un compte media Instagram")
    assert (creneau.article_id, creneau.etat) == (None, EtatCreneau.LIBRE)


def test_des_visuels_differes_attendent_sans_exclure(db_session, fred):
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    creneau = _epingler(db_session, article)

    def differer(db, article, format_, maintenant):
        raise VisuelsDifferes("quota Graph atteint")

    outils = Outils()
    outils.tourner(db_session, choisir=differer)
    db_session.refresh(article)
    db_session.refresh(creneau)
    assert outils.composes == [] and outils.notifications == []
    assert article.file_exclu_raison is None
    assert creneau.erreur == "composition differee : quota Graph atteint"
    assert creneau.article_id == article.id


def test_les_visuels_choisis_sont_gardes_sur_le_post(db_session, fred):
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    _epingler(db_session, article)
    choisi = actif(entree("photo", "https://cdn.ig/x.jpg"), origine="artiste", compte="@fredagain")
    formats = []

    def choisir(db, article, format_, maintenant):
        formats.append(format_)
        return ChoixVisuels([choisi], "@fredagain")

    Outils().tourner(db_session, choisir=choisir)
    (post,) = db_session.query(Post).all()
    assert (post.assets, post.credit, formats) == ([choisi], "@fredagain", [FormatCreneau.CARROUSEL])


def test_un_brouillon_anterieur_au_fond_interdit_n_est_pas_repris(db_session, fred, guettapen):
    article = _article(db_session, guettapen, media=[entree("photo", "https://cdn.ig/g.jpg")], mentioned=["Fred again.."])
    ancien = Post(article_id=article.id, article_title=article.title, article_url=article.url, caption="L",
                  slides=[{"kind": "cover", "image_url": "https://cdn.ig/g.jpg"}], template="kinda_post")
    db_session.add(ancien)
    db_session.commit()
    creneau = _epingler(db_session, article)
    choisi = actif(entree("photo", "https://cdn.ig/fred.jpg"), origine="artiste", compte="@fredagain")
    outils = Outils()
    outils.tourner(db_session, choisir=lambda db, a, f, m: ChoixVisuels([choisi], "@fredagain"))
    db_session.refresh(creneau)
    assert outils.composes == [article.id]
    assert creneau.post_id not in (None, ancien.id)


def test_le_menage_retire_les_vieilles_copies(db_session):
    vieille, recente = chemin_cache("https://cdn/vieille.jpg"), chemin_cache("https://cdn/recente.jpg")
    vieille.parent.mkdir(parents=True)
    vieille.write_bytes(b"x")
    recente.write_bytes(b"x")
    ancien = (BASE - dt.timedelta(days=8)).timestamp()
    os.utime(vieille, (ancien, ancien))
    recent = (BASE - dt.timedelta(days=1)).timestamp()
    os.utime(recente, (recent, recent))
    _menage(db_session, BASE)
    assert (vieille.exists(), recente.exists()) == (False, True)


def test_un_lecteur_neuf_par_passage(monkeypatch):
    crees = []

    class FauxLecteur:
        def __init__(self):
            crees.append(self)

    monkeypatch.setattr(script, "LecteurComptes", FauxLecteur)
    premier, second = script.choix_du_passage(), script.choix_du_passage()
    assert callable(premier) and callable(second)
    assert len(crees) == 2


def test_le_dashboard_compose_un_post_sans_visuel_autorise(client, db_session, guettapen, monkeypatch):
    article = _article(db_session, guettapen, "Actu sans artiste", media=[entree("photo", "https://cdn.ig/g.jpg")])
    monkeypatch.setattr("app.api.posts.make_claude_composer", lambda: lambda a: _draft())
    reponse = client.post(f"/articles/{article.id}/posts")
    assert reponse.status_code == 201
    corps = reponse.json()
    assert (corps["assets"], corps["credit"], corps["slides"][0]["image_url"]) == ([], None, None)


def test_le_dashboard_signale_des_visuels_differes(client, db_session, fred, monkeypatch):
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])

    def differer(*args, **kwargs):
        raise VisuelsDifferes("quota Graph atteint")

    monkeypatch.setattr("app.api.posts.choisir_visuels", differer)
    monkeypatch.setattr("app.api.posts.make_claude_composer", lambda: lambda a: _draft())
    reponse = client.post(f"/articles/{article.id}/posts")
    assert reponse.status_code == 503
    assert "quota" in reponse.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/schedule/test_file_visuels.py -v`
Expected: FAIL — `ImportError: cannot import name 'raison_sans_visuel' from 'app.schedule.store'`

- [ ] **Step 3: Implement store.py**

Imports à ajouter :

```python
from sqlalchemy.orm import Session, object_session, selectinload

from app.content.actifs_artistes import classer, posts_en_base
from app.content.visuels import raison_sans_artiste, visuels_autorises
from app.pipeline.comptes_artistes import CompteArtiste, IndexComptes, construire_index
```

Après `_a_une_image` :

```python
def comptes_cites(article: Article, index: IndexComptes) -> list[CompteArtiste]:
    return index.cites(
        textes=[article.title, article.summary],
        noms=article.mentioned_artists or [],
        exclure_source_id=article.source_id,
    )


def raison_sans_visuel(article: Article, index: IndexComptes | None = None) -> str | None:
    """None si l'article aura un visuel autorise, sinon la raison, affichable.

    Sans reseau : un artiste suivi cite suffit, son visuel sera cherche a la
    composition (en base, puis en direct). Si rien n'est trouve alors, l'article
    sera exclu avec la raison precise.
    """
    if _a_une_image(article):
        return None
    if index is None:
        db = object_session(article)
        if db is None:
            return raison_sans_artiste(article)
        index = construire_index(db)
    if comptes_cites(article, index):
        return None
    return raison_sans_artiste(article)
```

Remplacer `raison_ineligible_reel` (tâche 10) par :

```python
def raison_ineligible_reel(
    article: Article, *, index: IndexComptes | None = None, maintenant: dt.datetime | None = None
) -> str | None:
    """None si l'article peut partir en reel, sinon la raison, affichable.

    Le chemin propre d'abord (sa video, son compte). A defaut, la video d'un
    artiste suivi cite, EN BASE seulement : l'arbitrage ne lit jamais le reseau.
    """
    raison = raison_reel_propre(article)
    if raison is None:
        return None
    db = object_session(article)
    if db is None:
        return raison
    index = index if index is not None else construire_index(db)
    comptes = comptes_cites(article, index)
    if not comptes:
        return raison
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    if classer(article, posts_en_base(db, comptes, maintenant), format_=FormatCreneau.REEL, maintenant=maintenant):
        return None
    return raison
```

Dans `candidats_eligibles`, remplacer la ligne `garde = ...` par :

```python
    index = construire_index(db)
    if reel:
        def garde(a):
            return raison_ineligible_reel(a, index=index, maintenant=maintenant) is None
    else:
        def garde(a):
            return raison_sans_visuel(a, index) is None
```

- [ ] **Step 4: Implement runner.py**

Imports :

```python
from app.content.choix_visuels import AucunVisuel, VisuelsDifferes, choisir_visuels
from app.content.visuels import origine_interdite
```

Avant `_marquer_manques` :

```python
def _choix_sans_lecture(db, article, format_, maintenant):
    # Par defaut, aucune lecture Graph : l'agent planifie passe un lecteur borne
    # (scripts/run_publication.py), les tests et les appels directs n'en ont pas.
    return choisir_visuels(db, article, format_=format_, maintenant=maintenant)


def _menage_visuels(maintenant: dt.datetime) -> None:
    racine = settings.visuels_cache_dir
    if not racine.is_dir():
        return
    limite = (maintenant - dt.timedelta(days=settings.visuels_cache_jours)).timestamp()
    for fichier in racine.iterdir():
        if fichier.is_file() and fichier.stat().st_mtime < limite:
            fichier.unlink(missing_ok=True)
```

Au début de `_menage`, avant `racine = settings.publication_dir` :

```python
    _menage_visuels(maintenant)
```

`_composer` prend un paramètre supplémentaire `choisir` (signature : `_composer(db, maintenant, composer, notifier, rapport, formats, choisir)`). Remplacer `raison = raison_ineligible_reel(article)` par `raison = raison_ineligible_reel(article, maintenant=maintenant)` (dans `_composer` ET dans `_geler`). Remplacer le bloc de reprise et de composition :

```python
        post_id = post_a_reprendre(db, article.id, template)
        if post_id is not None:
            brouillon = db.get(Post, post_id, populate_existing=True)
            if brouillon.assets is None and origine_interdite(article.source):
                # Compose avant la regle du 2026-09-17 : son fond vient d'un compte
                # media. Un nouveau brouillon est compose avec des visuels autorises.
                post_id = None
            else:
                _completer_credit(db, brouillon, article)
        if post_id is None:
            # Les visuels AVANT l'appel paye : pas de composition pour un post qui
            # ne partirait pas.
            try:
                visuels = choisir(db, article, creneau.format, maintenant)
            except VisuelsDifferes as exc:
                erreur = f"composition differee : {exc}"[:1000]
                if creneau.erreur != erreur:
                    _ecrire_si(db, creneau, attendu, erreur=erreur)
                continue
            except AucunVisuel as exc:
                _exclure(db, creneau, attendu, f"composition impossible : {exc}", rapport)
                exclusions = True
                continue
            try:
                draft = composer(article)
            except CompositionError as exc:
                _exclure(db, creneau, attendu, f"composition impossible : {exc}", rapport)
                exclusions = True
                continue
            except Exception as exc:  # noqa: BLE001 -- credits, reseau : panne systemique
                erreur = f"composition interrompue : {exc}"[:1000]
                rapport.erreurs.append(erreur)
                _ecrire_si(db, creneau, attendu, erreur=erreur)
                _prevenir(notifier, erreur)
                # Inutile d'enchainer les appels sur une panne qui touchera tous les articles.
                break
            post_id = create_post(db, article, draft, template=template, visuels=visuels).id
            rapport.composes += 1
```

Une panne inattendue de `choisir` (disque, base) n'est ni `AucunVisuel` ni `VisuelsDifferes` : elle remonte à `etape("composition", ...)`, qui la consigne et la notifie — comportement voulu.

`tourner` : ajouter le paramètre `choisir=None` après `lire_mode=None`, puis `choisir = choisir or _choix_sans_lecture` et passer `choisir` à `_composer` dans l'appel de l'étape `composition`.

- [ ] **Step 5: Implement run_publication.py and api/posts.py**

`scripts/run_publication.py` : importer `from app.content.choix_visuels import choisir_visuels` et `from app.content.lecture_artistes import LecteurComptes`, ajouter :

```python
def choix_du_passage():
    """Le choix des visuels d'UN passage : un lecteur Graph neuf, qui porte le
    budget de lectures du passage (comme le recuperateur yt-dlp)."""
    lecteur = LecteurComptes()

    def choisir(db, article, format_, maintenant):
        return choisir_visuels(db, article, format_=format_, maintenant=maintenant, lecteur=lecteur)

    return choisir
```

et dans l'appel à `tourner`, après `lire_mode=mode_effectif,` : `choisir=choix_du_passage(),`.

`app/api/posts.py` : importer

```python
from app.content.choix_visuels import AucunVisuel, ChoixVisuels, VisuelsDifferes, choisir_visuels
from app.content.lecture_artistes import LecteurComptes
```

et dans `creer_post`, avant `composer = make_claude_composer()` :

```python
    # Les visuels d'abord, avec le meme budget de lecture que l'agent. Sans visuel
    # autorise, le post est quand meme compose (la regle vise la file) : fond vide,
    # a completer a la main.
    try:
        visuels = choisir_visuels(
            db, article, maintenant=dt.datetime.now(dt.timezone.utc), lecteur=LecteurComptes()
        )
    except AucunVisuel:
        visuels = ChoixVisuels()
    except VisuelsDifferes as exc:
        raise HTTPException(
            status_code=503, detail=f"visuels d'artistes momentanement indisponibles : {exc}"
        ) from exc
```

et `return create_post(db, article, draft, visuels=visuels)`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/schedule/test_file_visuels.py tests/schedule/test_schedule_runner.py tests/schedule/test_schedule_runner_reels.py tests/schedule/test_schedule_store.py tests/schedule/test_schedule_store_reels.py tests/scripts/test_run_publication.py tests/api/test_posts_api.py tests/api/test_schedule_api.py tests/api/test_schedule_api_reels.py -v`
Expected: PASS

- [ ] **Step 7: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/schedule/store.py app/schedule/runner.py scripts/run_publication.py app/api/posts.py tests/schedule/test_file_visuels.py
git commit -m "$(cat <<'EOF'
feat(file): sans visuel autorise, pas de file ; visuels choisis avant composition

raison_sans_visuel (sans reseau) garde les carrousels, reel eligible par la
video d'un artiste en base ; AucunVisuel exclut, VisuelsDifferes attend ; un
lecteur Graph neuf par passage ; menage des copies locales.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

### Task 13: API et dashboard : d'où vient le visuel, pourquoi il manque

**Files:**
- Modify: `app/schedule/store.py`, `app/schemas.py`, `app/api/articles.py`, `app/api/artists.py`, `app/api/schedule.py`, `app/static/dashboard.html`
- Test: `tests/api/test_api_visuels.py` (créer), `tests/api/test_schedule_api_reels.py`

**Interfaces:**
- Consumes: `raison_sans_visuel`, `comptes_cites`, `_a_une_image`, `origine_interdite`, `construire_index`, `compte_depuis_url`
- Produces:
  - `store.etiquette_visuel(article: Article, index: IndexComptes) -> str | None` — `"presse"`, `"@handle"`, `"artistes : @a, @b"` ou `None`
  - `ArticleOut.visuel: str | None = None`, `ArticleOut.raison_sans_visuel: str | None = None`, `ArticleOut.visuels_interdits: bool = False`
  - `ArtistOut.source_instagram_id: int | None = None`
  - `PATCH /artists/{id}` : corps `{tracked?: bool, source_instagram_id?: int | null}` ; source non Instagram `artiste` → 422
  - `PATCH /schedule/{id}` (`article_id`, créneau carrousel) : article sans visuel → 409 `Article sans visuel autorise : <raison>`
  - Dashboard : pastille `badge visuel` / `badge sans-visuel`, section « Visuels retenus », médias interdits non cochables

- [ ] **Step 1: Write the failing tests**

Créer `tests/api/test_api_visuels.py` :

```python
"""Le dashboard dit d'ou vient le visuel d'une actu, et pourquoi il manque."""

import datetime as dt
import itertools
from pathlib import Path

from app.content.media import entree
from app.models import Article, Artist, Creneau, Source, SourceCategorie, SourceType

UTC = dt.timezone.utc
DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "static" / "dashboard.html"
_numeros = itertools.count()


def _source(db, nom, handle=None, categorie=SourceCategorie.ARTISTE, type_=SourceType.INSTAGRAM):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{nom}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, media=None, mentioned=None):
    n = next(_numeros)
    article = Article(source_id=source.id, url=f"https://a.example/{n}", title=titre, raw_hash=f"d{n:063d}",
                      relevance_score=70, published_at=dt.datetime.now(UTC), media=media,
                      mentioned_artists=mentioned)
    db.add(article)
    db.commit()
    return article


def test_les_articles_disent_l_origine_de_leur_visuel(client, db_session):
    fred = _source(db_session, "Fred again.. (IG)", "fredagain")
    guettapen = _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)
    billboard = _source(db_session, "Billboard (RSS)", categorie=SourceCategorie.MEDIA, type_=SourceType.RSS)
    presse = _article(db_session, billboard, "Presse", media=[entree("photo", "https://b/og.jpg")])
    propre = _article(db_session, fred, "Post", media=[entree("photo", "https://cdn.ig/f.jpg")])
    relaye = _article(db_session, guettapen, "Relais", media=[entree("photo", "https://cdn.ig/g.jpg")],
                      mentioned=["Fred again.."])
    orphelin = _article(db_session, guettapen, "Orphelin", media=[entree("photo", "https://cdn.ig/o.jpg")])

    corps = {a["id"]: a for a in client.get("/articles").json()}

    assert (corps[presse.id]["visuel"], corps[presse.id]["visuels_interdits"]) == ("presse", False)
    assert corps[propre.id]["visuel"] == "@fredagain"
    assert (corps[relaye.id]["visuel"], corps[relaye.id]["visuels_interdits"]) == ("artistes : @fredagain", True)
    assert corps[orphelin.id]["visuel"] is None
    assert corps[orphelin.id]["raison_sans_visuel"].startswith("visuels d'un compte media Instagram interdits")
    assert client.get(f"/articles/{orphelin.id}").json()["raison_sans_visuel"] is not None


def test_lier_une_fiche_artiste_a_un_compte_suivi(client, db_session):
    fred = _source(db_session, "Fred again.. (IG)", "fredagain")
    media = _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)
    maintenant = dt.datetime.now(UTC)
    artist = Artist(name="Fred Again", slug="fred again", first_seen_at=maintenant, last_seen_at=maintenant)
    db_session.add(artist)
    db_session.commit()

    corps = client.patch(f"/artists/{artist.id}", json={"source_instagram_id": fred.id}).json()
    assert (corps["source_instagram_id"], corps["tracked"]) == (fred.id, False)
    assert client.patch(f"/artists/{artist.id}", json={"source_instagram_id": media.id}).status_code == 422
    assert client.patch(f"/artists/{artist.id}", json={"tracked": True}).json()["source_instagram_id"] == fred.id
    assert client.patch(f"/artists/{artist.id}", json={"source_instagram_id": None}).json()["source_instagram_id"] is None


def test_un_article_sans_visuel_est_refuse_sur_un_creneau_carrousel(client, db_session):
    guettapen = _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)
    article = _article(db_session, guettapen, "Orphelin", media=[entree("photo", "https://cdn.ig/o.jpg")])
    creneau = Creneau(publier_a=dt.datetime.now(UTC) + dt.timedelta(hours=5))
    db_session.add(creneau)
    db_session.commit()
    reponse = client.patch(f"/schedule/{creneau.id}", json={"article_id": article.id})
    assert reponse.status_code == 409
    assert reponse.json()["detail"].startswith("Article sans visuel autorise :")


def test_le_dashboard_montre_l_origine_des_visuels():
    """Sentinelles : pastille d'origine, actifs retenus, medias interdits grises."""
    html = DASHBOARD.read_text()
    assert 'class="badge visuel"' in html
    assert 'class="badge sans-visuel"' in html
    assert "Visuels retenus" in html
    assert 'const interdit = !!(article && article.visuels_interdits);' in html
    assert "visuel d'un compte média : interdit" in html
```

Dans `tests/api/test_schedule_api_reels.py`, le fixture `_article` donne une vidéo sans vignette : un tel article n'a jamais pu être candidat carrousel à l'arbitrage (`_a_une_image`), et le choix manuel le refuse désormais aussi. Donner une vignette à la vidéo pour garder l'intention de `test_remplacer_un_carrousel_reprend_le_brouillon_carrousel` :

```python
    media = [entree("video", f"https://cdn/{n}.mp4", preview_url=f"https://cdn/{n}.jpg")] if video else [entree("photo", f"https://img/{n}.jpg")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_api_visuels.py -v`
Expected: FAIL — `KeyError: 'visuel'`, 422 attendu, sentinelles absentes.

- [ ] **Step 3: Implement store.py and schemas.py**

`app/schedule/store.py` : importer `from app.sources.instagram import compte_depuis_url` et ajouter :

```python
def etiquette_visuel(article: Article, index: IndexComptes) -> str | None:
    """Ce que le dashboard affiche : d'ou viendra le visuel."""
    if _a_une_image(article):
        source = article.source
        if source is not None and source.type == SourceType.INSTAGRAM:
            return f"@{compte_depuis_url(source.url)}"
        return "presse"
    comptes = comptes_cites(article, index)
    if comptes:
        return "artistes : " + ", ".join(f"@{c.handle}" for c in comptes)
    return None
```

`app/schemas.py`, `ArticleOut`, après `media` :

```python
    # Calcules par l'API (pas des colonnes) : l'origine du visuel a venir, la raison
    # de son absence, et si les medias propres sont interdits (compte media IG).
    visuel: str | None = None
    raison_sans_visuel: str | None = None
    visuels_interdits: bool = False
```

`ArtistOut`, après `tracked` : `source_instagram_id: int | None = None`.

- [ ] **Step 4: Implement the endpoints**

`app/api/articles.py` : importer

```python
from sqlalchemy.orm import Session, selectinload

from app.content.visuels import origine_interdite
from app.pipeline.comptes_artistes import IndexComptes, construire_index
from app.schedule.store import etiquette_visuel, raison_sans_visuel
```

ajouter :

```python
def _avec_visuel(article: Article, index: IndexComptes) -> ArticleOut:
    sortie = ArticleOut.model_validate(article)
    sortie.visuel = etiquette_visuel(article, index)
    sortie.raison_sans_visuel = raison_sans_visuel(article, index)
    sortie.visuels_interdits = origine_interdite(article.source)
    return sortie
```

Dans `list_articles` : `stmt = select(Article).options(selectinload(Article.source))` et remplacer le `return` final par :

```python
    articles = db.scalars(stmt.limit(limit).offset(offset)).all()
    # Un index pour toute la page : les comptes suivis ne changent pas d'un article a l'autre.
    index = construire_index(db)
    return [_avec_visuel(article, index) for article in articles]
```

Dans `get_article` : `return _avec_visuel(article, construire_index(db))`.

`app/api/artists.py` : importer `Source, SourceCategorie, SourceType` depuis `app.models`, remplacer `ArtistTrackUpdate` et l'endpoint :

```python
class ArtistUpdate(BaseModel):
    tracked: bool | None = None
    source_instagram_id: int | None = None


@router.patch("/{artist_id}", response_model=ArtistOut)
def update_artist_tracking(artist_id: int, payload: ArtistUpdate, db: Session = Depends(get_db)):
    """Suivi A&R manuel, et lien vers le compte Instagram suivi de l'artiste
    (sert a la cascade de scoring et aux visuels quand le nom ne suffit pas)."""
    artist = db.get(Artist, artist_id)
    if artist is None:
        raise HTTPException(status_code=404, detail="Artist not found")
    champs = payload.model_fields_set
    if "tracked" in champs and payload.tracked is not None:
        artist.tracked = payload.tracked
    if "source_instagram_id" in champs:
        source_id = payload.source_instagram_id
        if source_id is not None:
            source = db.get(Source, source_id)
            if source is None or source.type != SourceType.INSTAGRAM or source.categorie != SourceCategorie.ARTISTE:
                raise HTTPException(
                    status_code=422,
                    detail="source_instagram_id doit designer une source Instagram de categorie artiste",
                )
        artist.source_instagram_id = source_id
    db.commit()
    db.refresh(artist)
    return artist
```

`app/api/schedule.py` : importer `raison_sans_visuel` depuis `app.schedule.store`, et remplacer le bloc `if creneau.format == FormatCreneau.REEL: ...` de l'action `article_id` par :

```python
        if creneau.format == FormatCreneau.REEL:
            # Meme regle que l'arbitrage : la securite prime sur le choix manuel
            # (credit obligatoire, jamais un compte media).
            raison = raison_ineligible_reel(article)
            if raison is not None:
                raise HTTPException(status_code=409, detail=f"Article inutilisable en reel : {raison}")
        else:
            raison = raison_sans_visuel(article)
            if raison is not None:
                raise HTTPException(status_code=409, detail=f"Article sans visuel autorise : {raison}")
```

- [ ] **Step 5: Implement the dashboard**

Dans `app/static/dashboard.html` :

1. CSS, après `.badge.hot-high { … }` :

```css
  .badge.visuel { background: #1f2d3d; color: #8ac4ff; }
  .badge.sans-visuel { background: #3d2a1f; color: #ffb38a; }
```

2. Dans `load()`, juste avant `card.innerHTML = \``, ajouter :

```js
    // D'ou viendra le visuel (presse, compte de l'actu, artistes cites) ou pourquoi
    // il n'y en a pas : un article sans visuel autorise n'entre pas dans la file.
    const visuelBadge = a.visuel
      ? `<span class="badge visuel">📸 ${escapeHtml(a.visuel)}</span>`
      : a.raison_sans_visuel
        ? `<span class="badge sans-visuel" title="${escapeHtml(a.raison_sans_visuel)}">🚫 sans visuel</span>`
        : "";
```

et dans `<div class="scores">`, après la ligne `${a.imprint ? … : ""}`, ajouter `${visuelBadge}`.

3. Dans le détail d'un post, remplacer

```js
    const desactivee = tropLongue || sansUrl;
    const raison = tropLongue
```

par

```js
    // Decision du 2026-09-17 : jamais l'image ni la video d'un compte media.
    const interdit = !!(article && article.visuels_interdits);
    const desactivee = tropLongue || sansUrl || interdit;
    const raison = interdit
      ? "visuel d'un compte média : interdit"
      : tropLongue
```

(la suite du ternaire existant reste inchangée), puis, juste avant `detailEl.innerHTML = \``, ajouter :

```js
  const actifsHtml = (post.assets || []).map((x) => {
    const vignette = x.preview_url || (x.type === "photo" ? x.url : null);
    const origine = x.origine === "artiste"
      ? `📸 ${escapeHtml(x.compte || "?")} · post du ${fmtDate(x.publie_le) || "?"}`
      : (x.compte ? `📸 ${escapeHtml(x.compte)}` : "📸 photo de presse");
    const lien = x.permalink
      ? `<a href="${escapeHtml(x.permalink)}" target="_blank" rel="noopener">voir le post</a>` : "";
    return `
    <div class="media-choice">
      ${vignette ? `<img src="${escapeHtml(vignette)}" loading="lazy" onerror="this.remove()">` : ""}
      <label>${origine}</label>
      ${lien}
    </div>`;
  }).join("");
  const actifsSection = post.assets == null ? "" : `
      <div class="section-title">Visuels retenus</div>
      <div class="media-list">${actifsHtml || '<div class="media-empty">Aucun visuel autorisé retenu : fond à poser à la main.</div>'}</div>`;
```

et insérer `${actifsSection}` juste avant `<div class="section-title">Médias de l'actu (carrousel)</div>`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_api_visuels.py tests/api/test_api.py tests/api/test_schedule_api.py tests/api/test_schedule_api_reels.py tests/dashboard/test_dashboard_mode_control.py -v`
Expected: PASS

- [ ] **Step 7: Check the dashboard by hand (optional)**

`launchctl bootout gui/$(id -u)/com.musicnewsradar.dashboard` puis `uv run uvicorn app.main:app --reload` **sur une base de test** (jamais Neon pendant l'implémentation) : une carte d'article Instagram média porte « 🚫 sans visuel » avec la raison au survol ; le détail d'un post composé montre « Visuels retenus ». Recharger l'agent ensuite : `uv run python -m scripts.install_agents --apply`.

- [ ] **Step 8: Lint, full suite, commit**

```bash
uv run ruff check . && uv run pytest -q
git add app/schedule/store.py app/schemas.py app/api/articles.py app/api/artists.py app/api/schedule.py app/static/dashboard.html tests/api/test_api_visuels.py tests/api/test_schedule_api_reels.py
git commit -m "$(cat <<'EOF'
feat(dashboard): origine du visuel, raison de son absence, lien artiste-compte

ArticleOut expose visuel, raison_sans_visuel et visuels_interdits ; PATCH
/artists lie une fiche a un compte suivi ; choix manuel refuse sans visuel ;
actifs retenus et medias interdits au detail d'un post.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

### Task 14: Documenter

**Files:**
- Modify: `CLAUDE.md`
- Test: aucun (documentation) — la suite complète doit rester verte

**Interfaces:**
- Consumes: tout ce qui précède
- Produces: `CLAUDE.md` à jour

- [ ] **Step 1: Update « Qui parle : `sources.categorie` »**

Remplacer le premier paragraphe (de « Le feed priorise les posts des COMPTES D'ARTISTES » à « l'axe emergence n'est pas touché. ») par :

```markdown
Le feed priorise les posts des COMPTES D'ARTISTES (électro, musiques dures — hard
techno, hardstyle, hardcore —, pop à tendance électro) : un artiste qui annonce sa
sortie est l'actu primaire. **Depuis le 2026-09-17, l'INFO d'un compte MÉDIA
(Guettapen, Tsugi, Mixmag, DJ Mag, Billboard, Booska-P…) n'est plus décotée** :
la décote de 15-20 points et le plafond à 59 ont enterré la BO de GTA VI (Fred
again..) à 45-59. Kinda reprend l'information, jamais la formulation ni les
VISUELS du média (voir « Visuels : l'info des médias, les images des artistes »).
Festivals/labels/clubs : annonce officielle = source primaire, promo courante
40-55. Un post SANS INFORMATION (légende vide — titre « Post Instagram » —,
emojis/hashtags seuls, selfie, « link in bio », teaser d'idole sans fait) note
sous 30 quel que soit le compte. L'axe emergence n'est pas touché.
```

Dans « Garde-fous déterministes », supprimer le point 2 (« Plafond média ») et la phrase « La décision d'écarter du pré-filtre se prend sur la note BRUTE : le plafond média (59) est au-dessus du seuil (45) et ne fait jamais écarter un article. » ; ajouter à la fin du point 1 : « `actu_majeure` a été retiré du schéma le 2026-09-17 (jamais persisté, aucune migration). » Dans « Pré-filtre local », remplacer la phrase sur la décote par : « `CONSIGNE_PREFILTRE` : ne descendre sous 45 que pour le bruit évident, l'actu hors ligne et les posts réellement vides — un contenu qui porte un fait vaut 45 ou plus, quel que soit le compte. **Deux cas ne sont jamais écartés** : une actu dont le groupe de doublons réunit au moins `cluster_sources_claude` (3) sources distinctes, et un article qui cite un artiste suivi (`ContexteScoring`, compteurs `forces_cluster` / `forces_artiste`). » Dans « Nettoyage de la file sans rien payer », supprimer la phrase sur le plafond MEDIA.

- [ ] **Step 2: Update « Dédup à deux étages »**

Remplacer « Le représentant est l'article vu le plus tôt ; les liens ne forment jamais de chaîne (un saut suffit toujours). Idempotent. » par :

```markdown
   Les liens se forment contre l'article vu le plus tôt ; puis
   **`elire_representants`** (`app/pipeline/election.py`, juste après, AVANT le
   scoring) promeut le membre le plus INFORMATIF — clé sur le titre et la source
   seulement (le résumé est réécrit par le scoring, il ferait osciller) : artistes
   suivis cités dans le titre, source primaire, visuels autorisés, mots
   significatifs, ancienneté, id. Un représentant engagé (`USED`, un post, un
   créneau non libre) ne change jamais. Un représentant noté par le pré-filtre
   local dont le groupe atteint 3 sources est remis à noter (Claude). Borne :
   `cluster_reevaluations_max_par_run` (10). Jamais de chaîne, idempotent.
```

- [ ] **Step 3: Add the « Visuels » section and the commands**

Après « Illustration des articles », ajouter :

```markdown
## Visuels : l'info des médias, les images des artistes (2026-09-17)

Décision de l'utilisateur : l'INFO peut venir d'un compte d'actu, ses images et
vidéos JAMAIS (watermarks). `app/content/visuels.py::origine_interdite` : source
`instagram` de catégorie `media`, et elle seule — les photos de presse RSS
(`og:image`, `media_content`) restent autorisées. Tout ce qui lit des médias pour
publier passe par `visuels_autorises` (fond de cover, photos du gel, vidéo d'un
reel, cases du dashboard), avec deux ceintures : un brouillon antérieur dont le
fond vient d'un média n'est pas repris, et le gel refuse un tel fond.

**Où chercher à la place** (`choix_visuels.py`, AVANT l'appel Claude de la
composition) : les comptes Instagram des artistes SUIVIS cités
(`comptes_artistes.py` : nom de la source sans parenthèses, handle, fiches
`artists` liées par `source_instagram_id` — un nom d'un seul mot n'est jamais
cherché en texte libre). (a) Leurs posts en base ; (b) pour un carrousel sans
rien SUR LE SUJET en base, lecture en direct par `business_discovery`
(`lecture_artistes.py`) : 2 par passage, 24 par jour, cache 6 h, pause d'une heure
sur les codes Graph 4/17/32/613, rien inséré en base ; (c) pour un reel, la vidéo
d'un post d'artiste en base (API, sinon yt-dlp sur SON permalink, mêmes règles).
Heuristique (`actifs_artistes.py`) : même artiste, fenêtre dure de 10 jours, sur
le sujet d'abord (mots du titre hors nom de l'artiste), hors sujet à 5 jours au
plus, 3 images au plus. Les images d'artistes sont COPIÉES dans
`visuels_cache_dir` (URL CDN signées qui expirent) et lues seulement dans ce
dossier ; ménage à 7 jours par le runner.

`posts.assets` (migration 0018) garde les actifs (origine, compte, permalink,
date, copie locale) ; `posts.credit` les handles. Légende : « 📸 @artiste »
(🎥 en reel, plus de « via ») puis « Source : <média> · <url> ».

Sans visuel autorisé, l'article reste au dashboard (pastille « 🚫 sans visuel »
et raison) mais sort de la file : `store.raison_sans_visuel` (sans réseau) garde
les carrousels ; à la composition, `AucunVisuel` exclut, `VisuelsDifferes` (quota,
budget) attend le passage suivant sans exclure.
```

Dans « Commandes », après la ligne des garde-fous, ajouter :

```markdown
- Rattraper les actus récentes enterrées par l'ancienne notation :
  `uv run python -m scripts.reevaluer_actus [--jours 3] [--limite 100] [--apply]`
  (sans `--apply` : le plan, aucune écriture ni appel Claude ; avec : élection des
  représentants, notation des en-attente, re-notation Claude du plan, au plus
  `--limite` appels)
- Lier une fiche artiste à son compte suivi quand le nom ne suffit pas :
  `PATCH /artists/{id}` avec `{"source_instagram_id": <id de la source>}`
```

- [ ] **Step 4: Verify and commit**

```bash
uv run ruff check . && uv run pytest -q
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: info des medias sans decote, representant informatif, visuels d'artistes

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
)"
```

---

## Mise en service — décisions de l'utilisateur

Aucune de ces étapes n'est exécutée par un agent d'implémentation.

1. **Mode `apercu`** au panneau Programmation avant tout (les visuels d'artistes et le crédit changent ce qui partirait).
2. **Brouillons et gels en cours** — avant la fusion, lister les créneaux engagés sur un article de compte média Instagram :
   ```sql
   SELECT c.id, c.etat, c.publier_a, a.title FROM creneaux c
   JOIN articles a ON a.id = c.article_id JOIN sources s ON s.id = a.source_id
   WHERE s.type = 'instagram' AND s.categorie = 'media' AND c.etat IN ('programme', 'gele');
   ```
   Un créneau « Gelé » de cette liste partirait avec un visuel interdit : le **sauter** au dashboard. Les « Programmé » seront recomposés (brouillon non repris).
3. **Migration 0018 sur Neon AVANT la fusion** (par l'utilisateur, depuis ce worktree) : `uv run alembic upgrade head`. Le runner, le dashboard et le pipeline lisent `posts.assets` et `artists.source_instagram_id` dès la fusion.
4. **Fusionner** `feat/visuels-artistes` dans `main`, puis **redémarrer le dashboard** : `launchctl kickstart -k gui/$(id -u)/com.musicnewsradar.dashboard`. Les agents de pipeline et de publication chargent le nouveau code à leur prochain passage.
5. **Rattrapage** : `uv run python -m scripts.reevaluer_actus` (plan), relire, puis `uv run python -m scripts.reevaluer_actus --apply` (≤ 100 appels Haiku). Vérifier la BO de GTA VI :
   ```sql
   SELECT a.id, s.name, a.relevance_score, a.scored_by, a.duplicate_of_id, left(a.title, 70)
   FROM articles a JOIN sources s ON s.id = a.source_id
   WHERE a.title ILIKE '%GTA%' ORDER BY a.id;
   ```
   Puis « Recalculer la file ».
6. **Liens artistes** : après le premier run horaire, lister les comptes suivis jamais liés et corriger à la main ceux dont le nom diffère (`PATCH /artists/{id}`) :
   ```sql
   SELECT s.id, s.name FROM sources s
   WHERE s.type = 'instagram' AND s.categorie = 'artiste' AND s.active
     AND NOT EXISTS (SELECT 1 FROM artists ar WHERE ar.source_instagram_id = s.id);
   ```
7. **Observer une semaine en `apercu`** : pastilles « 📸 presse / @artiste / artistes : … » et « 🚫 sans visuel » ; « Visuels retenus » au détail des posts (le visuel illustre-t-il vraiment l'actu ?) ; raisons « composition differee » ; `~/Library/Application Support/Kinda/visuels_lectures.json` (lectures du jour, pause) ; `x-app-usage` si une notification de quota Graph revient ; volume de la file (la presse n'est plus décotée). Ajuster au besoin `VISUELS_ARTISTE_FENETRE_JOURS`, `VISUELS_ARTISTE_GENERIQUE_JOURS`, `CLUSTER_SOURCES_CLAUDE` dans `.env`.
8. **Publier pour de vrai** : repasser en mode `reel` au dashboard (confirmation « PUBLIER »).
9. **Retour arrière** : `alembic downgrade 0017` seulement après avoir sauté les créneaux gelés dont le post porte `assets` ; les brouillons perdent leurs actifs et le gel retombe sur les médias autorisés de l'article (jamais ceux d'un compte média).
