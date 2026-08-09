# Covers en 4 variantes, médias typés, POST ou REELS — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un clic sur une actu produit 4 covers rendues parmi lesquelles choisir, puis un POST (cover + médias de l'actu) ou un REEL (cover + vidéo) exportable en zip prêt à publier.

**Architecture:** Les médias d'un article deviennent **typés** (`photo` / `video` / `gif`, avec URL exploitable, durée et dimensions), ce qui rend la question « peut-on faire un reel ? » décidable. Un post devient une liste de slides déclarant leur nature : `kind: "cover"` est **rendu** par Chromium, `kind: "media"` est **laissé passer** vers l'export. Un seul appel Claude produit les 4 variantes ; un seul Chromium rend les 4 covers.

**Tech Stack:** Python 3.11+ / uv, SQLAlchemy 2.0 + Alembic, Pydantic v2, Anthropic SDK (`messages.parse`), Playwright (Chromium), httpx, FastAPI, JS vanilla dans `dashboard.html`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-09-cover-variantes-et-reels-design.md`

## Global Constraints

- Toutes les commandes passent par `uv run` (jamais `python` ou `pytest` nus).
- Lint : `uv run ruff check .` doit passer. `line-length = 100`, `target-version = "py311"`.
- **Commentaires et docstrings en français, SANS accents** — le style établi de `app/`. Les chaînes affichées à l'utilisateur, `dashboard.html` et les fichiers de documentation (`CLAUDE.md`, `REFERENCE.md`) sont accentués.
- Les commentaires expliquent le **pourquoi**, pas le quoi.
- SQLAlchemy 2.0 (`Mapped` / `mapped_column`). Colonnes JSON via `JSONVariant`.
- **Réassigner une colonne JSON en entier**, jamais de mutation in place, sinon SQLAlchemy ne persiste pas.
- Dans les migrations écrites à la main, entourer les noms de contraintes avec `op.f()`.
- En-têtes HTTP en **ASCII pur** (httpx plante avant l'envoi sinon).
- **Ne jamais lancer `alembic upgrade` sur la base de l'utilisateur.** `DATABASE_URL` pointe sur Neon en production. Pour éprouver une migration, monter un cluster jetable (voir « Vérifier une migration » plus bas).
- Chaque tâche laisse **la suite verte**. Une tâche qui casserait la suite doit être fusionnée avec celle qui la répare.
- Commit après chaque tâche, message en français, préfixe `feat(posts):` / `feat(sources):` / `fix(...)` / `docs(...)`.

## Vérifier une migration sans toucher Neon

Procédure éprouvée le 2026-08-09 (le chemin de socket Unix du scratchpad dépasse la limite de 103 octets, d'où `-k /tmp`) :

```bash
D=/tmp/pgplan
rm -rf "$D" && /Library/PostgreSQL/15/bin/initdb -D "$D" -U postgres -A trust
/Library/PostgreSQL/15/bin/pg_ctl -D "$D" -o "-p 55432 -k /tmp -h 127.0.0.1" -l "$D/log" -w start
/Library/PostgreSQL/15/bin/psql -h 127.0.0.1 -p 55432 -U postgres -c "CREATE DATABASE ci_test;"

export DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:55432/ci_test"
uv run alembic upgrade head && uv run alembic check && uv run alembic downgrade base

/Library/PostgreSQL/15/bin/pg_ctl -D "$D" -m fast stop && rm -rf "$D"
```

Le job `schema` de `ci.yml` fait exactement cela sur chaque push. Une migration qui passe en local y passera.

## Valeurs de référence

| Valeur | Décidée | Motif |
|---|---|---|
| Nombre de variantes | **4** | Demande de l'utilisateur |
| Ce qui varie | **le titre seul**, fond constant | On compare des formulations, pas des images |
| Durée max reel | **90 s** | Limite Instagram |
| Durée max slide de carrousel | **60 s** | Limite Instagram |
| Plafond de téléchargement par média | **80 Mo** | Une vidéo de 90 s pèse 10–30 Mo ; 80 Mo laisse de la marge sans exposer à un fichier de 2 Go |
| Délai de téléchargement | **120 s** | Une vidéo n'est pas une vignette |
| Longueur max d'un titre de cover | **110** (`HEADLINE_MAX`, existant) | Contrainte du gabarit, déjà calibrée |

---

## File Structure

| Fichier | Responsabilité |
|---|---|
| `app/sources/base.py` (modifier) | `RawItem.media: list[dict]` remplace `media_urls`. |
| `app/sources/rss.py` (modifier) | Émet des médias typés (photo uniquement, c'est tout ce qu'un RSS donne). |
| `app/sources/x_api.py` (modifier) | Demande `type,variants,duration_ms,width,height` ; extrait l'URL MP4. |
| `app/sources/x_apify.py` (modifier) | Idem, depuis la forme Apify. |
| `app/models.py` (modifier) | `Article.media`, `Post.variants`. |
| `app/pipeline/ingest.py` (modifier) | Passe `item.media`. |
| `alembic/versions/0010_typed_media.py` (créer) | `media_urls` → `media`, avec transformation des lignes. |
| `alembic/versions/0011_post_variants.py` (créer) | `posts.variants` + `kind` sur les slides existantes. |
| `app/content/media.py` (créer) | Le prédicat « vidéo exploitable » et les limites Instagram. **Zéro dépendance.** |
| `app/content/compose.py` (modifier) | 4 variantes × 3 textes, légende commune. |
| `app/content/render.py` (modifier) | Ne rend que `kind: "cover"` ; `render_covers()`. |
| `app/content/export.py` (créer) | Téléchargement borné des médias + assemblage du zip. |
| `app/schemas.py` (modifier) | `ArticleOut.media`, `PostOut.variants`, `PostUpdate.template` / `variante_retenue`. |
| `app/api/articles.py` (modifier) | Filtre `has_media` sur la nouvelle colonne. |
| `app/api/posts.py` (modifier) | Génération, rendu des covers, sélection, bascule, suppression, export. |
| `app/static/dashboard.html` (modifier) | Les 4 covers, le switch, le sélecteur de médias. |
| `CLAUDE.md`, `app/content/templates/kinda_post/REFERENCE.md` (modifier) | Documentation. |
| `tests/test_media.py` (créer) | Le prédicat, sans réseau ni base. |
| `tests/test_export.py` (créer) | Le zip et les échecs de téléchargement. |
| Tests existants (modifier) | 7 fichiers touchent `media_urls` : `test_compose`, `test_ingest`, `test_api`, `test_sources/test_{rss,reddit_rss,x_api,x_apify}`. |

---

## Task 1: les médias deviennent typés, de bout en bout

**Une seule tâche** parce que renommer `media_urls` casse simultanément les adapters, l'ingestion, les schémas, l'API, le dashboard et 7 fichiers de tests. Les séparer laisserait la suite rouge entre deux commits.

**Files:**
- Modify: `app/sources/base.py`, `app/sources/rss.py`, `app/sources/x_api.py`, `app/sources/x_apify.py`, `app/models.py`, `app/pipeline/ingest.py`, `app/schemas.py`, `app/api/articles.py`, `app/content/compose.py`, `app/static/dashboard.html`
- Create: `alembic/versions/0010_typed_media.py`, `app/content/media.py`, `tests/test_media.py`
- Test: les 7 fichiers listés ci-dessus

**Interfaces:**
- Produces:
  - `RawItem.media: list[dict]` (remplace `media_urls`)
  - `Article.media: Mapped[list | None]`
  - `app/content/media.py` : `PHOTO = "photo"`, `VIDEO = "video"`, `GIF = "gif"`, `DUREE_MAX_REEL_MS = 90_000`, `DUREE_MAX_SLIDE_MS = 60_000`, `videos_exploitables(media) -> list[dict]`, `raison_pas_de_reel(media) -> str | None`

- [ ] **Step 1: Write the failing tests for the predicate**

Create `tests/test_media.py`:

```python
"""Le predicat « video exploitable », sans reseau ni base.

Il decide si le switch REELS apparait. Une video non exploitable doit dire
POURQUOI : un bouton grise sans explication est une enigme, et les trois causes
(pas de video, pas d'URL directe, trop longue) ne se depannent pas pareil.
"""

from app.content.media import (
    DUREE_MAX_REEL_MS,
    raison_pas_de_reel,
    videos_exploitables,
)


def _video(**kwargs) -> dict:
    base = {
        "type": "video",
        "url": "https://video.twimg.com/vid.mp4",
        "duration_ms": 31_000,
        "width": 1280,
        "height": 720,
    }
    return {**base, **kwargs}


def _photo() -> dict:
    return {"type": "photo", "url": "https://pbs.twimg.com/photo.jpg"}


def test_une_video_courte_avec_url_directe_est_exploitable():
    assert videos_exploitables([_photo(), _video()]) == [_video()]
    assert raison_pas_de_reel([_video()]) is None


def test_aucun_media_ou_photos_seules_ne_donnent_pas_de_reel():
    assert videos_exploitables([]) == []
    assert videos_exploitables([_photo()]) == []
    assert "aucune video" in raison_pas_de_reel([_photo()])
    assert "aucune video" in raison_pas_de_reel([])


def test_une_video_sans_url_directe_nest_pas_exploitable():
    """Cas d'un RSS qui ne donne qu'un lien de page : on ne peut rien telecharger."""
    sans_url = _video(url=None)

    assert videos_exploitables([sans_url]) == []
    assert "URL directe" in raison_pas_de_reel([sans_url])


def test_une_video_trop_longue_nest_pas_exploitable_et_la_raison_dit_la_duree():
    trop_longue = _video(duration_ms=240_000)

    assert videos_exploitables([trop_longue]) == []
    raison = raison_pas_de_reel([trop_longue])
    assert "90" in raison and "240" in raison or "4 min" in raison


def test_une_duree_inconnue_passe():
    """Une source qui ne donne pas la duree ne doit pas faire perdre la video : on
    prefere proposer le reel et laisser l'utilisateur constater, plutot que de
    masquer le switch sur une donnee absente."""
    assert videos_exploitables([_video(duration_ms=None)]) == [_video(duration_ms=None)]


def test_la_borne_de_duree_est_inclusive():
    assert videos_exploitables([_video(duration_ms=DUREE_MAX_REEL_MS)])
    assert not videos_exploitables([_video(duration_ms=DUREE_MAX_REEL_MS + 1)])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_media.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.content.media'`

- [ ] **Step 3: Implement `app/content/media.py`**

```python
"""Ce qu'on peut faire des medias d'un article : le predicat « video exploitable ».

Zero dependance -- ni base, ni reseau, ni Chromium. Ce module repond a une seule
question, et c'est elle qui decide si le switch REELS apparait dans le dashboard.

Un tweet video etait jusqu'ici indistinguable d'un tweet photo : l'API X ne renvoie
`url` que pour les photos et rend une VIGNETTE pour une video. D'ou les medias
types, dont ce module est le consommateur principal.
"""

PHOTO = "photo"
VIDEO = "video"
GIF = "gif"

# Limites Instagram. Un reel accepte plus long qu'une slide de carrousel, d'ou deux
# valeurs et non une.
DUREE_MAX_REEL_MS = 90_000
DUREE_MAX_SLIDE_MS = 60_000


def videos_exploitables(media: list[dict] | None, duree_max_ms: int = DUREE_MAX_REEL_MS):
    """Les videos qu'on peut reellement publier, dans l'ordre des medias.

    Trois conditions : le type, une URL DIRECTE (sans elle il n'y a rien a
    telecharger -- cas d'un RSS qui ne donne qu'un lien de page), et une duree sous
    la limite. Une duree INCONNUE passe : mieux vaut proposer le reel et laisser
    l'utilisateur constater que masquer le switch sur une donnee absente.
    """
    retenues = []
    for entree in media or []:
        if entree.get("type") != VIDEO or not entree.get("url"):
            continue
        duree = entree.get("duration_ms")
        if duree is not None and duree > duree_max_ms:
            continue
        retenues.append(entree)
    return retenues


def raison_pas_de_reel(media: list[dict] | None) -> str | None:
    """Rend None si un reel est possible, sinon la raison, affichable telle quelle.

    Le dashboard grise le switch REELS avec ce texte : les trois causes ne se
    depannent pas pareil (changer de source, attendre une autre actu, rien a faire),
    donc un bouton grise muet ne suffit pas.
    """
    if videos_exploitables(media):
        return None
    videos = [e for e in media or [] if e.get("type") == VIDEO]
    if not videos:
        return "aucune video dans cette actu"
    if not any(e.get("url") for e in videos):
        return "pas d'URL directe vers la video (la source ne donne qu'un lien de page)"
    duree = min(
        (e["duration_ms"] for e in videos if e.get("duration_ms") is not None),
        default=None,
    )
    if duree is not None:
        return (
            f"video trop longue : {duree // 1000} s, "
            f"maximum {DUREE_MAX_REEL_MS // 1000} s pour un reel"
        )
    return "aucune video exploitable"  # pragma: no cover -- filet
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_media.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Rename the field in `RawItem` and the three adapters**

`app/sources/base.py` — replace the field:

```python
    # Medias TYPES et non une liste d'URL : une video et sa vignette ne se
    # distinguaient pas, ce qui rendait le format REELS indecidable.
    # {"type": "photo"|"video"|"gif", "url": str,
    #  "preview_url": str | None, "duration_ms": int | None,
    #  "width": int | None, "height": int | None}
    media: list[dict] = field(default_factory=list)
```

`app/sources/rss.py` — `_media_urls` devient `_media`, et rend des dicts de type `photo` **uniquement**. C'est fidèle à ce qu'un flux RSS publie : les pièces jointes sont déjà filtrées sur `image/`, et `media_content` d'un RSS musical ne contient pas de fichier vidéo. Conserver la déduplication par URL.

`app/sources/x_api.py` — deux endroits (`media.fields` apparaît lignes ~135 et ~251). Demander :

```python
"media.fields": "type,url,preview_image_url,variants,duration_ms,width,height",
```

et remplacer l'extraction par une méthode partagée :

```python
    # Plafond de debit : `variants` liste plusieurs MP4, du plus leger au plus lourd.
    # Le plus haut debit est parfois enorme pour un gain invisible sur Instagram, qui
    # re-encode de toute facon.
    _DEBIT_MAX = 5_000_000

    def _media_depuis(self, media: dict) -> dict | None:
        """Normalise un objet media de l'API v2 vers la forme typee du pipeline."""
        type_ = media.get("type")
        if type_ == "photo":
            url = media.get("url")
            return {"type": PHOTO, "url": url, "width": media.get("width"),
                    "height": media.get("height")} if url else None
        if type_ in ("video", "animated_gif"):
            # `url` n'existe PAS pour une video : c'est `variants` qui porte les MP4,
            # et `preview_image_url` la vignette. Prendre `preview_image_url` comme
            # url, comme le faisait l'ancien code, revenait a traiter une video
            # comme une photo.
            mp4 = [
                v for v in (media.get("variants") or [])
                if v.get("content_type") == "video/mp4" and v.get("url")
            ]
            sous_plafond = [v for v in mp4 if (v.get("bit_rate") or 0) <= self._DEBIT_MAX]
            meilleur = max(sous_plafond or mp4, key=lambda v: v.get("bit_rate") or 0, default=None)
            return {
                "type": VIDEO if type_ == "video" else GIF,
                "url": meilleur["url"] if meilleur else None,
                "preview_url": media.get("preview_image_url"),
                "duration_ms": media.get("duration_ms"),
                "width": media.get("width"),
                "height": media.get("height"),
            }
        return None
```

Attention : le mode recherche exige un média (`if not media_urls: continue`). La condition devient `if not medias: continue` — **une vidéo sans MP4 utilisable garde son entrée** (avec `url: None`), parce que sa vignette reste affichable et que l'article garde son intérêt éditorial ; c'est `videos_exploitables` qui tranchera sur le reel.

`app/sources/x_apify.py` — même forme depuis la structure Apify : `entry.get("type")` y vaut `photo` / `video` / `animated_gif`, et les URL vidéo sont dans `video_info.variants`. Si la forme diffère de ce que le test attend, **corriger le test d'après la vraie structure**, pas l'inverse : cet adapter est une alternative non utilisée en production, et une extraction fausse y serait invisible.

- [ ] **Step 6: Rename the column, write migration 0010**

`app/models.py`, classe `Article` :

```python
    # Medias TYPES (photo / video / gif) avec URL exploitable, duree et dimensions.
    # Remplace l'ancienne `media_urls: list[str]`, dans laquelle un tweet video
    # stockait sa VIGNETTE -- indistinguable d'une photo, donc le format REELS
    # etait indecidable. Reassigner la liste ENTIERE, jamais de mutation in place.
    media: Mapped[list | None] = mapped_column(JSONVariant)
```

Create `alembic/versions/0010_typed_media.py` :

```python
"""typed media : les medias d'un article portent leur type

L'ancienne `media_urls` etait une liste d'URL. Un tweet video y stockait sa
vignette, donc rien ne distinguait une video d'une photo et le format REELS etait
indecidable.

La transformation des lignes existantes les classe toutes en `photo`. C'est faux
pour une ancienne vignette de video -- et sans consequence : les articles sont
purges a 14 jours, et le pire cas est de ne pas proposer REELS sur un article deja
peri.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column("media", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # jsonb_array_elements_text : `media_urls` est un tableau JSON de chaines.
    op.execute(
        """
        UPDATE articles
        SET media = (
            SELECT jsonb_agg(jsonb_build_object('type', 'photo', 'url', url))
            FROM jsonb_array_elements_text(media_urls) AS url
        )
        WHERE media_urls IS NOT NULL
        """
    )
    op.drop_column("articles", "media_urls")


def downgrade() -> None:
    op.add_column(
        "articles",
        sa.Column("media_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(
        """
        UPDATE articles
        SET media_urls = (
            SELECT jsonb_agg(entree->>'url')
            FROM jsonb_array_elements(media) AS entree
            WHERE entree->>'url' IS NOT NULL
        )
        WHERE media IS NOT NULL
        """
    )
    op.drop_column("articles", "media")
```

- [ ] **Step 7: Update every consumer**

- `app/pipeline/ingest.py` : `media=item.media or None`.
- `app/schemas.py` : `ArticleOut.media: list[dict] | None`.
- `app/api/articles.py` : le filtre `has_media` porte sur `Article.media`.
- `app/content/compose.py` : `premiere_image` devient la première entrée dont `type == PHOTO` avec une `url`, sinon la `preview_url` d'une vidéo, sinon `None`. **Une vignette de vidéo est un fond de cover parfaitement valable** — c'est même le cas normal pour une actu vidéo.
- `app/static/dashboard.html` (~ligne 211) : la vignette de carte devient
  `const img = (a.media || []).map(m => m.url || m.preview_url).filter(Boolean)[0];`

- [ ] **Step 8: Update the 7 test files**

Mechanical: `media_urls=[...]` → `media=[{"type": "photo", "url": ...}]`. In `tests/test_sources/test_x_api.py`, **add** a case with a real video payload (a `variants` list with two MP4 bitrates and a `preview_image_url`) asserting that the chosen URL is the MP4 and not the thumbnail — that is the bug this task fixes, and no existing test covers it.

- [ ] **Step 9: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS. Baseline is **280**; this task adds 6 and modifies others.

- [ ] **Step 10: Verify the migration on a throwaway cluster**

Run the procedure from « Vérifier une migration sans toucher Neon ». All three commands must exit 0. **Do not run `alembic upgrade` against `DATABASE_URL` from `.env`.**

Also verify the data transformation by hand: insert a row with `media_urls = '["https://a.jpg"]'::jsonb` before `upgrade`, and check that `media` reads `[{"type": "photo", "url": "https://a.jpg"}]` after.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(sources): les medias d'un article portent leur type

Un tweet video etait indistinguable d'un tweet photo : l'API X ne renvoie
`url` que pour les photos et rend une VIGNETTE pour une video, et le champ
`type` n'etait pas stocke. Le format REELS etait donc indecidable.

media_urls (liste d'URL) devient media (liste de dicts types) avec l'URL
exploitable, la vignette, la duree et les dimensions. L'adapter X demande
desormais variants, qui porte les MP4 lisibles.

Les dimensions ne sont pas cosmetiques : un reel fait 9:16 et une video X est
souvent en 16:9. Les afficher evite de decouvrir le recadrage dans Instagram.

La migration classe les lignes existantes en photo -- faux pour une ancienne
vignette de video, sans consequence : les articles sont purges a 14 jours."
```

---

## Task 2: le compositeur rend 4 variantes

**Files:**
- Modify: `app/content/compose.py`
- Test: `tests/test_compose.py`

**Interfaces:**
- Produces:
  - `CoverVariante(BaseModel)` : `headline: str`, `title: str`, `subtitle: str`
  - `PostDraft(BaseModel)` : `variantes: list[CoverVariante]` (exactement 4), `caption: str`, `hashtags: list[str]`
  - `NB_VARIANTES = 4`

- [ ] **Step 1: Write the failing tests**

In `tests/test_compose.py`, replace the draft-shape tests with:

```python
def test_une_variante_exige_les_trois_textes_balises():
    """Chaque variante porte l'accroche du post ET le couple titre/sous-titre du
    reel : c'est ce qui rend la bascule POST/REELS instantanee, sans redemander a
    Claude. Les trois passent par la meme regle de surlignage."""
    with pytest.raises(pydantic.ValidationError):
        CoverVariante(headline="sans surlignage", title="**Titre**", subtitle="**Sous**")


def test_le_brouillon_exige_exactement_quatre_variantes():
    def variante(n):
        return CoverVariante(
            headline=f"**Accroche {n}**", title=f"**Titre {n}**", subtitle=f"**Sous {n}**"
        )

    for nombre in (3, 5):
        with pytest.raises(pydantic.ValidationError):
            PostDraft(variantes=[variante(i) for i in range(nombre)], caption="c", hashtags=[])

    draft = PostDraft(variantes=[variante(i) for i in range(4)], caption="c", hashtags=[])
    assert len(draft.variantes) == 4
```

Keep the existing retry tests (`make_claude_composer` retries on validation failure) — adapt the fake client's payload to the new shape.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_compose.py -v`
Expected: FAIL — `ImportError: cannot import name 'CoverVariante'`

- [ ] **Step 3: Replace `Slide` / `PostDraft`**

```python
NB_VARIANTES = 4
# Le sous-titre du reel vit dans une bande plus etroite que l'accroche du post :
# une borne plus courte evite de proposer un texte qui ne tiendra pas.
SUBTITLE_MAX = 70


class CoverVariante(BaseModel):
    """Une proposition de cover : les trois textes, pour les deux gabarits.

    `headline` sert au gabarit post (une zone de texte), `title` + `subtitle` au
    gabarit reel (deux bandes empilees). Porter les trois d'avance est ce qui rend
    la bascule POST/REELS instantanee -- sinon il faudrait un appel Claude par
    bascule, et l'utilisateur perdrait sa selection.

    `image_url` n'est PAS demande au modele : un modele a qui l'on demande une URL
    l'invente. Le fond vient des medias de l'article, ou de l'utilisateur.
    """

    headline: str = Field(max_length=HEADLINE_MAX)
    title: str = Field(max_length=HEADLINE_MAX)
    subtitle: str = Field(max_length=SUBTITLE_MAX)

    @field_validator("headline", "title", "subtitle")
    @classmethod
    def _balisage_valide(cls, value: str) -> str:
        validate_headline(value)
        return value


class PostDraft(BaseModel):
    variantes: list[CoverVariante] = Field(min_length=NB_VARIANTES, max_length=NB_VARIANTES)
    caption: str = Field(max_length=2000)
    hashtags: list[str] = Field(default_factory=list, max_length=15)
```

Note: `min_length == max_length == 4` contraint le nombre **côté API** via le schéma JSON, pas seulement après coup.

- [ ] **Step 4: Rewrite the prompt**

Replace rules 1–3 of `SYSTEM_PROMPT`. The prompt must state, in French without accents:

- qu'on produit **4 variantes** d'une **cover unique**, et non un carrousel : la cover est la première image, les slides suivantes seront les vraies photos et vidéos de l'actu ;
- que les 4 variantes disent **la même information** avec **quatre formulations différentes** — angle du chiffre, du nom, de la question, du rendez-vous — et non quatre informations différentes ;
- que chaque variante fournit `headline` (post), `title` **court et frappant** et `subtitle` (reel), les trois avec au moins un segment `**surligné**` ;
- que `title` porte le nom ou le chiffre, `subtitle` le contexte — c'est la structure des deux bandes de la maquette (`Grégoire Cliquet` / `au festival des vieilles Charrues`) ;
- que la légende et les hashtags sont **communs aux quatre**, puisque les variantes sont des formulations et non des angles éditoriaux distincts.

Keep rules 4 and 5 (légende, hashtags) and the closing « N'invente aucun fait ».

Raise the token budget:

```python
# Quatre variantes de trois textes + legende + hashtags. L'ancien budget etait
# calibre pour UN carrousel ; le depasser tronque le JSON, ce qui retombe sur
# l'echec de validation que la boucle de tentatives doit rattraper -- autant ne pas
# le provoquer.
COMPOSER_MAX_TOKENS = 16000
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_compose.py -v && uv run ruff check .`
Expected: PASS

- [ ] **Step 6: Try it once for real, and read what comes back**

This step is not a test — it is a judgement on prompt quality that no assertion replaces.

```bash
uv run python - <<'PY'
from app.content.compose import make_claude_composer
from app.db import SessionLocal
from app.models import Article
from sqlalchemy import select

with SessionLocal() as db:
    article = db.scalars(
        select(Article).where(Article.relevance_score >= 70).order_by(Article.id.desc())
    ).first()
    print(f"--- {article.title}\n")
    draft = make_claude_composer()(article)
    for i, v in enumerate(draft.variantes, 1):
        print(f"[{i}] post     : {v.headline}")
        print(f"    reel     : {v.title} / {v.subtitle}\n")
    print(f"legende  : {draft.caption}\nhashtags : {' '.join(draft.hashtags)}")
PY
```

This is a **read-only** query plus one paid Claude call (a few cents). Judge: are the four genuinely different formulations, or four rewordings of the same sentence? Are the `title` short enough to work as a band? Report the actual output in your report — if the four are near-identical, the prompt needs work, and that is a finding, not a detail.

- [ ] **Step 7: Commit**

```bash
git add app/content/compose.py tests/test_compose.py
git commit -m "feat(posts): le compositeur rend 4 variantes de cover

Un seul appel Claude produit quatre formulations de la MEME information --
angle du chiffre, du nom, de la question, du rendez-vous -- plutot qu'un
carrousel de slides de texte.

Chaque variante porte les TROIS textes : l'accroche du gabarit post et le
couple titre/sous-titre du gabarit reel. C'est ce qui rend la bascule
POST/REELS instantanee : sinon il faudrait un appel par bascule, et
l'utilisateur perdrait sa selection.

Legende et hashtags communs aux quatre : les variantes sont des formulations,
pas des angles editoriaux distincts."
```

---

## Task 3: un post décrit ce qui sera publié

**Files:**
- Modify: `app/models.py`, `app/content/compose.py` (`create_post`), `app/schemas.py`
- Create: `alembic/versions/0011_post_variants.py`
- Test: `tests/test_compose.py`

**Interfaces:**
- Produces:
  - `Post.variants: Mapped[dict | None]` — `{"variantes": [...], "retenue": int | None}`
  - `create_post(db, article, draft, template="kinda_post") -> Post`
  - `PostOut.variants`, `PostUpdate.template`, `PostUpdate.variante_retenue`

- [ ] **Step 1: Write the failing tests**

```python
def test_create_post_pose_les_quatre_variantes_sans_retenue(db_session):
    """La retenue est nulle a la creation : l'utilisateur n'a pas encore choisi, et
    c'est ce qui rend le brouillon reprenable sans repayer un appel Claude."""
    post = create_post(db_session, article, draft)

    assert len(post.variants["variantes"]) == 4
    assert post.variants["retenue"] is None


def test_create_post_pose_une_cover_avec_le_fond_de_larticle(db_session):
    """La slide 1 est une cover, pas un media : c'est la seule que le renderer
    rendra."""
    post = create_post(db_session, article, draft)

    (cover,) = post.slides
    assert cover["kind"] == "cover"
    assert cover["image_url"] == "https://exemple.test/photo.jpg"
    # Les trois textes sont poses des la creation, pour que la bascule de gabarit
    # n'ait rien a redemander.
    assert cover["headline"] and cover["title"] and cover["subtitle"]


def test_create_post_prend_la_vignette_dune_video_comme_fond(db_session):
    """Une actu video n'a pas de photo : sa vignette est un fond parfaitement
    valable, et c'est meme le cas normal."""
    article.media = [{"type": "video", "url": "https://v.mp4",
                      "preview_url": "https://thumb.jpg"}]

    post = create_post(db_session, article, draft)

    assert post.slides[0]["image_url"] == "https://thumb.jpg"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_compose.py -v`
Expected: FAIL — `KeyError: 'kind'` / `AttributeError: 'Post' object has no attribute 'variants'`

- [ ] **Step 3: Add the column and rewrite `create_post`**

`app/models.py`, classe `Post`, after `slides` :

```python
    # Les quatre propositions et l'indice de celle que l'utilisateur retient
    # (`retenue: null` tant qu'il n'a pas choisi). Les non retenues sont GARDEES :
    # choisir entre quatre formulations du meme sujet est du signal sur ce qui
    # plait, gratuit a collecter, et exploitable par le tri local a venir.
    # Reassigner le dict ENTIER, jamais de mutation in place.
    variants: Mapped[dict | None] = mapped_column(JSONVariant)
```

`create_post` :

```python
def _fond_par_defaut(article: Article) -> str | None:
    """La photo de l'article, sinon la vignette d'une video.

    Une actu video n'a pas de photo, et sa vignette fait un fond de cover
    parfaitement valable -- c'est meme le cas normal pour ce type d'actu.
    """
    for entree in article.media or []:
        if entree.get("type") == PHOTO and entree.get("url"):
            return entree["url"]
    for entree in article.media or []:
        if entree.get("preview_url"):
            return entree["preview_url"]
    return None


def create_post(
    db: Session, article: Article, draft: PostDraft, template: str = "kinda_post"
) -> Post:
    """Persiste un brouillon : les quatre variantes, et une cover sans retenue.

    Les champs sont DENORMALISES expres : l'article sera purge a 14 jours, le post
    doit rester lisible sans lui.
    """
    premiere = draft.variantes[0]
    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=article.source.name if article.source is not None else None,
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
                "image_url": _fond_par_defaut(article),
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

- [ ] **Step 4: Migration 0011**

```python
"""post variants : les quatre propositions de cover, et le type de chaque slide

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("variants", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Les slides existantes etaient toutes des rendus de texte : elles deviennent
    # des covers. Il n'y en a aucune en production au moment d'ecrire ceci, mais un
    # post cree entre-temps ne doit pas se retrouver avec des slides sans `kind`,
    # que le renderer ignorerait en silence.
    op.execute(
        """
        UPDATE posts
        SET slides = (
            SELECT jsonb_agg(entree || '{"kind": "cover"}'::jsonb)
            FROM jsonb_array_elements(slides) AS entree
        )
        WHERE slides IS NOT NULL
          AND jsonb_array_length(slides) > 0
          AND NOT (slides->0 ? 'kind')
        """
    )


def downgrade() -> None:
    op.drop_column("posts", "variants")
```

Note the asymmetry: `downgrade` does not strip `kind`. An extra key in a JSON blob breaks nothing, whereas a `UPDATE` over every row to remove it is risk for no gain. Comment that in the file.

- [ ] **Step 5: Schemas**

- `SlideIn` becomes a union of two shapes, or a permissive model with `kind: Literal["cover", "media"]` and optional fields per kind. **Prefer two explicit models** (`CoverIn`, `MediaIn`) with a discriminated union on `kind`: a permissive model would accept a cover with no text and fail at render time instead of at the API boundary.
- `PostOut` gains `variants: dict | None`.
- `PostUpdate` gains `template: str | None` and `variante_retenue: int | None`.

Validate `template` against `layout.GABARITS` and `variante_retenue` against the actual number of variants — a 422 at the boundary beats a `KeyError` in the renderer.

- [ ] **Step 6: Run the suite, verify the migration, commit**

Run: `uv run pytest -q && uv run ruff check .`, then the throwaway-cluster procedure.

```bash
git commit -m "feat(posts): un post declare la nature de chaque slide

slides devient une liste ou chaque entree porte son `kind` : `cover` est rendu
par Chromium, `media` est laisse passer vers l'export. C'est ce qui permet a un
post d'etre une cover generee SUIVIE des vraies photos et videos de l'actu.

Pas de nouvelle colonne de format : `posts.template` fait deja ce travail
(kinda_post / kinda_reel) et l'API l'expose.

Les quatre variantes sont gardees avec l'indice de la retenue -- le choix entre
quatre formulations du meme sujet est du signal gratuit sur ce qui plait."
```

---

## Task 4: le renderer ne rend que les covers

**Files:**
- Modify: `app/content/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Produces: `render_covers(variantes: list[dict], image_url: str | None, template: str) -> list[RenduSlide]`
- `render_post` ignores `kind: "media"` entries.

- [ ] **Step 1: Write the failing tests**

```python
@chromium_absent
def test_render_post_ignore_les_medias_et_ne_rend_que_les_covers():
    """Un media n'est pas rendu : il part tel quel dans l'export. Le renderer doit
    donc en produire AUCUN png, pas un png vide."""
    slides = [
        {"kind": "cover", "headline": "**Accroche**", "title": "**T**",
         "subtitle": "**S**", "image_url": None},
        {"kind": "media", "type": "video", "url": "https://v.mp4"},
    ]

    rendus = render_post(slides)

    assert len(rendus) == 1


@chromium_absent
def test_render_covers_rend_les_quatre_sur_le_meme_fond():
    """Quatre covers, un seul Chromium, une seule recuperation d'image : c'est le
    lancement du navigateur et le telechargement qui coutent, pas la capture."""
    variantes = [
        {"headline": f"**Accroche {i}**", "title": f"**T{i}**", "subtitle": f"**S{i}**"}
        for i in range(4)
    ]

    rendus = render_covers(variantes, image_url=None, template="kinda_post")

    assert len(rendus) == 4
    assert all(_png_size(r.png) == (GABARIT_POST.width, GABARIT_POST.height) for r in rendus)
    # Quatre textes differents -> quatre images differentes. Un bug qui rendrait
    # quatre fois la meme variante passerait toutes les autres assertions.
    assert len({r.png for r in rendus}) == 4


@chromium_absent
def test_render_covers_utilise_le_gabarit_reel_quand_on_le_demande():
    from app.content.layout import GABARIT_REEL

    variantes = [{"headline": "**a**", "title": "**T**", "subtitle": "**S**"}]

    (rendu,) = render_covers(variantes, image_url=None, template="kinda_reel")

    assert _png_size(rendu.png) == (GABARIT_REEL.width, GABARIT_REEL.height)


def test_render_covers_signale_chromium_absent(monkeypatch):
    """Meme garde-fou que render_post : un message qui dit quoi faire, pas une
    trace au fond de Playwright."""
    import app.content.render as render_module

    def faux():
        raise Exception("Executable doesn't exist at /path/chrome")

    monkeypatch.setattr(render_module, "sync_playwright", faux)

    with pytest.raises(RenduIndisponible, match="playwright install chromium"):
        render_covers([{"headline": "**a**", "title": "**T**", "subtitle": "**S**"}], None, "kinda_post")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_covers'`

- [ ] **Step 3: Implement**

In `render_post`, filter the slides before the loop:

```python
            # Un `kind: "media"` est une photo ou une video de l'actu : elle part
            # telle quelle dans l'export, sans passer par le gabarit.
            covers = [s for s in slides if s.get("kind", "cover") == "cover"]
```

The `s.get("kind", "cover")` default keeps a slide without `kind` renderable — belt for a row the migration missed.

`render_covers` mirrors `render_post` but fetches the background **once**:

```python
def render_covers(
    variantes: list[dict], image_url: str | None, template: str = "kinda_post"
) -> list[RenduSlide]:
    """Rend les covers de plusieurs variantes sur le MEME fond.

    Un seul Chromium et une seule recuperation d'image pour les quatre : c'est le
    lancement du navigateur et le telechargement qui coutent du temps, la capture
    non. Rendre les quatre separement multiplierait par quatre le seul cout qui se
    voit a l'usage.
    """
    gabarit_ = gabarit(template)
    image_uri = fetch_image_data_uri(image_url)
    rendus: list[RenduSlide] = []
    with ExitStack() as pile:
        contexte, browser = _ouvrir_chromium()
        pile.callback(contexte.__exit__, None, None, None)
        pile.callback(browser.close)
        page = browser.new_page(
            viewport={"width": gabarit_.width, "height": gabarit_.height},
            device_scale_factor=1,
        )
        for variante in variantes:
            textes = {role: variante[role] for role in gabarit_.roles}
            rendus.append(_render_one(page, gabarit_, textes, image_uri))
    return rendus
```

Note the role mapping: `gabarit_.roles` is `{"headline": ...}` for the post and `{"title": ..., "subtitle": ...}` for the reel, so the same variant dict serves both without a conditional.

- [ ] **Step 4: Run the suite, commit**

Run: `uv run pytest -q && uv run ruff check .`

```bash
git commit -m "feat(posts): le renderer distingue ce qu'il rend de ce qu'il laisse passer

Une slide `kind: media` est une photo ou une video de l'actu : elle ne passe
pas par le gabarit, elle part telle quelle dans l'export.

render_covers rend les quatre variantes sur le MEME fond, avec un seul
Chromium et une seule recuperation d'image : c'est le lancement du navigateur
et le telechargement qui coutent, pas la capture. Les rendre separement
multiplierait par quatre le seul cout qui se voit a l'usage du soir."
```

---

## Task 5: l'export télécharge les médias

**Files:**
- Create: `app/content/export.py`, `tests/test_export.py`
- Modify: `app/api/posts.py` (`exporter_post`)

**Interfaces:**
- Produces:
  - `PLAFOND_OCTETS = 80 * 1024 * 1024`, `DELAI_S = 120.0`
  - `telecharger(url, plafond=PLAFOND_OCTETS, delai=DELAI_S, client=None) -> tuple[bytes | None, str | None]` — le contenu, ou `None` et la raison
  - `construire_zip(post, cover_pngs, textes_legende, telecharger=telecharger) -> bytes`

- [ ] **Step 1: Write the failing tests**

```python
def test_le_zip_contient_la_cover_les_medias_et_la_legende():
    ...
    noms = zipfile.ZipFile(io.BytesIO(zip_octets)).namelist()

    assert noms == ["01_cover.png", "02_photo.jpg", "03_video.mp4", "caption.txt"]


def test_un_media_qui_echoue_ne_fait_pas_echouer_le_zip():
    """Meme philosophie que l'image de secours du rendu : un export partiel vaut
    mieux qu'une erreur, surtout a 23 h. La raison et l'URL doivent survivre pour
    qu'on puisse aller chercher le media a la main."""
    def telecharger_casse(url, **kwargs):
        return None, "404 Not Found"

    archive = zipfile.ZipFile(io.BytesIO(construire_zip(..., telecharger=telecharger_casse)))

    assert "medias_manquants.txt" in archive.namelist()
    manquants = archive.read("medias_manquants.txt").decode()
    assert "https://v.mp4" in manquants and "404" in manquants
    assert "01_cover.png" in archive.namelist()  # la cover survit


def test_pas_de_fichier_manquants_quand_tout_passe():
    """Un fichier vide dans le zip ferait croire a un probleme."""
    assert "medias_manquants.txt" not in zipfile.ZipFile(...).namelist()


def test_telecharger_refuse_au_dela_du_plafond():
    """Une source qui renvoie 2 Go ne doit pas bloquer l'export. Le plafond se
    verifie EN COURS de lecture : se fier a Content-Length laisserait passer une
    reponse sans en-tete ou mensongere."""
    contenu, raison = telecharger("https://gros.mp4", plafond=1024, client=faux_client_de(2048))

    assert contenu is None
    assert "plafond" in raison


def test_telecharger_nomme_lextension_dapres_le_type_de_contenu():
    """Instagram refuse un fichier sans extension coherente."""
    ...
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL — `ModuleNotFoundError: No module named 'app.content.export'`

- [ ] **Step 3: Implement `app/content/export.py`**

Key requirements, each with its reason to be commented in the file:

- **Stream and count.** Use `client.stream("GET", url)` and accumulate chunks, aborting past `plafond`. Trusting `Content-Length` would let through a response with no header, or a lying one.
- **Extension from `Content-Type`.** Instagram rejects a file with an incoherent extension. Map `image/jpeg → .jpg`, `image/png → .png`, `video/mp4 → .mp4`, with a fallback derived from the URL path and, failing that, `.bin` plus a line in `medias_manquants.txt` explaining the file may not import.
- **Pure-ASCII `User-Agent`**, reusing `render._USER_AGENT` rather than redefining it.
- **`medias_manquants.txt` only if non-empty.** An empty file in the zip suggests a problem.
- **Numbering follows slide order**, cover first, so the zip reads in publication order.

- [ ] **Step 4: Wire it into `exporter_post`**

The endpoint renders the covers (`render_post`, which now returns only the covers) and passes the `kind: "media"` entries to `construire_zip`. Keep the `RenduIndisponible → 503` translation. Keep the ASCII `Content-Disposition`.

- [ ] **Step 5: Run the suite, commit**

```bash
git commit -m "feat(posts): l'export embarque les medias de l'actu

Le zip contient la cover rendue et les photos/videos cochees, numerotees dans
l'ordre de publication -- pret a deposer dans Instagram.

Un media qui ne se telecharge pas ne fait PAS echouer le zip : il part dans
medias_manquants.txt avec son URL et la raison. Meme philosophie que l'image de
secours du rendu : un export partiel vaut mieux qu'une erreur, surtout a 23 h.

Le plafond de taille se verifie en cours de lecture et non sur Content-Length,
qu'une reponse peut omettre ou mentir."
```

---

## Task 6: les endpoints

**Files:**
- Modify: `app/api/posts.py`, `app/schemas.py`
- Test: `tests/test_posts_api.py`

**Interfaces:**
- `POST /articles/{id}/posts` → crée le post avec 4 variantes (appel Claude, **aucun rendu**)
- `POST /posts/{id}/covers` → rend les 4 covers, rend `{"covers": ["data:image/png;base64,...", ...]}`
- `PATCH /posts/{id}` → accepte `template` et `variante_retenue` en plus de l'existant
- `DELETE /posts/{id}` → supprime un brouillon
- `GET /posts/{id}/export.zip` → inchangé de l'extérieur

- [ ] **Step 1: Write the failing tests**

```python
def test_covers_rend_quatre_data_uris(client, db_session, monkeypatch):
    """Deux endpoints distincts et non un seul : la generation coute un appel
    Claude, le rendu coute un Chromium. Recharger la page ne doit pas repayer
    Claude."""
    ...
    assert len(reponse.json()["covers"]) == 4
    assert all(u.startswith("data:image/png;base64,") for u in reponse.json()["covers"])


def test_retenir_une_variante_reecrit_la_cover(client, db_session):
    reponse = client.patch(f"/posts/{post.id}", json={"variante_retenue": 2})

    db_session.refresh(post)
    assert post.variants["retenue"] == 2
    assert post.slides[0]["headline"] == post.variants["variantes"][2]["headline"]
    assert post.slides[0]["title"] == post.variants["variantes"][2]["title"]


def test_un_indice_de_variante_hors_bornes_est_refuse(client, db_session):
    """422 a la frontiere plutot qu'un IndexError dans le renderer."""
    assert client.patch(f"/posts/{post.id}", json={"variante_retenue": 9}).status_code == 422


def test_changer_de_gabarit_conserve_la_cover_et_ses_trois_textes(client, db_session):
    """La bascule est instantanee parce que la cover porte deja les trois textes."""
    client.patch(f"/posts/{post.id}", json={"template": "kinda_reel"})

    db_session.refresh(post)
    assert post.template == "kinda_reel"
    assert post.slides[0]["title"] and post.slides[0]["subtitle"]


def test_un_gabarit_inconnu_est_refuse(client, db_session):
    assert client.patch(f"/posts/{post.id}", json={"template": "kinda_carre"}).status_code == 422


def test_un_brouillon_se_supprime(client, db_session):
    assert client.delete(f"/posts/{post.id}").status_code == 204
    assert db_session.get(Post, post.id) is None


def test_un_post_publie_ne_se_supprime_pas(client, db_session):
    """La regle « jamais purge » protege l'ARCHIVE EDITORIALE : savoir ce qui a ete
    publie, et ne pas le reposter. La frontiere est donc la publication."""
    post.status = PostStatus.PUBLISHED
    db_session.commit()

    assert client.delete(f"/posts/{post.id}").status_code == 409
    assert db_session.get(Post, post.id) is not None
```

- [ ] **Step 2: Run to verify they fail, then implement**

Two clarifications of the spec, to be recorded in the code comments:

1. **Two endpoints, not one.** Generation costs a Claude call; rendering costs a Chromium launch. Separating them means reloading the page, or changing the background URL, re-renders without re-paying Claude.
2. **Deletion is allowed on any `DRAFT`**, not only on a draft with no retained variant. The spec's wording named the no-retained case, but the rule that protects the editorial archive is **publication** — and a draft whose variant you retained then reconsidered must still be deletable. `APPROVED` and `PUBLISHED` answer 409.

- [ ] **Step 3: Run the suite, commit**

```bash
git commit -m "feat(posts): endpoints des covers, de la bascule et de la suppression

Generation et rendu sont deux endpoints distincts : la premiere coute un appel
Claude, le second un lancement de Chromium. Recharger la page ou changer l'URL
du fond re-rend sans repayer Claude.

Le gabarit et l'indice de variante sont valides A LA FRONTIERE : un 422 vaut
mieux qu'un KeyError dans le renderer.

Un brouillon se supprime, un post publie non : la regle « jamais purge »
protege l'archive editoriale, donc la frontiere est la publication."
```

---

## Task 7: le dashboard

**Files:**
- Modify: `app/static/dashboard.html`

Aucun test automatisé : ce fichier est du JS sans outillage de test. La vérification est manuelle et prescrite ci-dessous.

- [ ] **Step 1: Build the selection view**

Au clic sur « Générer », après la réponse de `POST /articles/{id}/posts` puis de `POST /posts/{id}/covers` :

- les **4 covers** en grille, cliquables, celle retenue visuellement marquée ;
- le **switch POST / REELS** ; REELS désactivé porte `title` **et** un texte visible avec `raison_pas_de_reel` — un `disabled` muet est une énigme ;
- le **champ URL du fond**, prérempli ; sa validation relance `POST /posts/{id}/covers` (et rien d'autre) ;
- la **liste des médias** de l'actu à cocher, chacun avec son type, ses dimensions et sa vignette ; un média non exploitable comme vidéo reste cochable comme média de carrousel s'il tient sous 60 s ;
- le bouton **Supprimer** sur un brouillon.

Réutiliser les conventions du fichier : `status-pill`, `hl-hint`, `escapeHtml`, la gestion d'erreur par `alert`.

- [ ] **Step 2: Verify by hand, and say what you saw**

Chromium Playwright est installé et l'agent uvicorn tourne sur `127.0.0.1:8000`. Vérifier, en le rapportant explicitement :

1. une actu **sans vidéo** : REELS désactivé, avec la raison lisible ;
2. une actu **avec vidéo** : le switch fonctionne, et la cover change de format sans nouvel appel Claude ;
3. **changer l'URL du fond** re-rend les 4 covers ;
4. **retenir une variante** puis recharger la page : la retenue est conservée ;
5. **supprimer un brouillon** le retire de la file.

Si l'automatisation du navigateur n'est pas disponible, le dire **franchement** et lister ce qui reste non vérifié, plutôt que d'affirmer avoir cliqué.

- [ ] **Step 3: Commit**

---

## Task 8: la documentation

**Files:**
- Modify: `CLAUDE.md`, `app/content/templates/kinda_post/REFERENCE.md`

- [ ] **Step 1: `CLAUDE.md`, section « Génération de posts »**

La section décrit un carrousel de slides de texte — ce n'est plus l'objet produit. Réécrire pour dire : cover générée + médias réels, 4 variantes, POST ou REELS selon la vidéo, et **pourquoi** les médias sont typés (l'API X rend une vignette pour une vidéo, donc le format était indécidable).

Ajouter aux « Contraintes apprises sur le terrain » :
- `media.fields=url,preview_image_url` ne suffit pas : `variants` porte les MP4, et sans `type` une vidéo est indistinguable d'une photo ;
- un flux RSS ne publie pas de fichier vidéo, seulement un lien de page — les reels viendront de X ou de rien.

- [ ] **Step 2: `REFERENCE.md`**

Documenter que le gabarit sert désormais à rendre **une cover**, pas un carrousel, et que les clés d'une slide `kind: "cover"` doivent correspondre aux rôles du gabarit (contrat déjà documenté, à réaffirmer avec le nouveau vocabulaire).

- [ ] **Step 3: Commit**

---

## Self-Review

**Spec coverage:**

| Exigence de la spec | Tâche |
|---|---|
| Médias typés (`type`, URL, vignette, durée, dimensions) | 1 |
| `media.fields` étendu, MP4 depuis `variants` | 1 |
| Migration indolore (lignes existantes en `photo`) | 1 |
| Définition de « vidéo exploitable » + raison affichable | 1 |
| 4 variantes, titre seul, fond constant | 2 |
| Chaque variante porte les 3 textes | 2 |
| Légende et hashtags communs | 2 |
| `slides` avec `kind`, `posts.template` comme format | 3 |
| Variantes conservées + indice de retenue | 3 |
| Fond = photo de l'article, sinon vignette de vidéo | 3 |
| Renderer : cover seulement | 4 |
| 4 covers, un Chromium, une récupération d'image | 4 |
| Export : cover + médias téléchargés | 5 |
| `medias_manquants.txt`, plafond, délai | 5 |
| Génération / rendu / bascule / retenue / suppression | 6 |
| Switch REELS avec la raison affichée | 1 (le texte) + 7 (l'affichage) |
| Champ URL du fond, re-rend les covers | 7 |
| Sélecteur de médias avec type et dimensions | 7 |
| Brouillon supprimable, publié protégé | 6 |
| Documentation | 8 |

Aucune exigence sans tâche.

**Hors périmètre, conformément à la spec :** file quotidienne de 5 posts, feedback écrit avec motif, modèle local, auto-gestion des sources, montage ou recadrage vidéo.

**Placeholder scan:** les seules étapes non déterministes sont la 6 de la tâche 2 (juger la qualité du prompt sur une vraie sortie) et la 2 de la tâche 7 (vérification manuelle du dashboard). Toutes deux fournissent le script ou la liste de contrôle, et exigent un rapport de ce qui a été observé — pas un « à ajuster ».

**Type consistency:** une entrée de média est partout `{"type", "url", "preview_url", "duration_ms", "width", "height"}` ; `PHOTO`/`VIDEO`/`GIF` viennent de `app.content.media` et de nulle part ailleurs ; une variante est `{"headline", "title", "subtitle"}` ; `Post.variants` est `{"variantes": [...], "retenue": int | None}` ; `render_covers` et `render_post` rendent tous deux `list[RenduSlide]`.

**Point de vigilance pour l'exécutant :** la tâche 1 est la plus large du plan (renommage traversant 7 fichiers de tests). Elle est indivisible : la scinder laisserait la suite rouge entre deux commits, ce qui priverait la revue par tâche de tout sens. Si elle paraît trop grosse en cours de route, le signaler plutôt que de committer un état intermédiaire cassé.

**Dette connexe, hors périmètre mais à ne pas perdre :** rien ne vérifie au démarrage de l'agent uvicorn que le schéma et les modèles concordent. Le job `schema` de la CI (ajouté le 2026-08-09) attrape la divergence avant le merge, ce qui couvre le cas de ce plan — mais un `alembic upgrade` oublié sur Neon produirait encore des 500 silencieux jusqu'à ce qu'on ouvre le dashboard. Ce plan ajoute **deux** migrations : les appliquer à Neon fait partie de la mise en service, pas de l'implémentation.
