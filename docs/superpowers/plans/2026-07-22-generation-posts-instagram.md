# Génération de posts Instagram — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformer un article scoré en un post carrousel Instagram prêt à publier — PNG 1080×1440 au template Kinda, plus la légende — depuis un bouton du dashboard.

**Architecture:** Trois unités indépendantes derrière une table `posts` : `compose.py` (article → JSON de slides via Claude), `render.py` (JSON + gabarit HTML → PNG via Chromium headless), `api/posts.py` (file éditoriale). Le template Figma est extrait UNE FOIS en HTML/CSS versionné ; Figma n'est jamais appelé à l'exécution.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, anthropic SDK, Jinja2, Playwright (Chromium), uv.

**Spec de référence :** `docs/superpowers/specs/2026-07-22-generation-posts-instagram-design.md`

## Global Constraints

- Gestionnaire de paquets : `uv`. Toute commande passe par `uv run`.
- Lint : `uv run ruff check .` doit passer. `line-length = 100`, `target-version = "py311"`.
- Tests : `uv run pytest`. Base de test = SQLite in-memory via les fixtures de `tests/conftest.py`.
- Style SQLAlchemy 2.0 : `Mapped` / `mapped_column`. Enums stockés en VARCHAR + CHECK (`native_enum=False`, `values_callable=_values`), jamais d'enum natif Postgres.
- JSONB via `JSONVariant` (déjà défini dans `app/models.py`).
- **Réassigner toujours un champ JSON en entier** (`post.slides = [...]`), jamais de mutation in place : SQLAlchemy ne détecte pas la mutation et ne persiste pas.
- Dans les migrations, entourer les noms de contraintes avec `op.f()` sinon la naming convention de `app/db.py` double le préfixe.
- Les commentaires et les chaînes destinées à l'utilisateur sont en **français**. Les messages de commit sont en français sans accents.
- Les en-têtes HTTP doivent être en **ASCII pur** (httpx plante avant l'envoi sur un accent).
- Format de slide figé : **1080 × 1440**.
- Nom du template : `kinda_post`.

## Ordonnancement pour exécution parallèle

Les tâches sont groupées en vagues. À l'intérieur d'une vague, les tâches sont **indépendantes** : fichiers disjoints, aucune dépendance d'import entre elles. Chaque vague attend la fin de la précédente.

| Vague | Tâches | Parallélisme | Pourquoi cette frontière |
|---|---|---|---|
| 0 | Tâche 1 | 1 agent | Le modèle `Post` et la migration sont importés par presque tout le reste |
| 1 | Tâches 2, 3, 4 | **3 agents** | Parser de surlignage (pur), composition Claude, extraction Figma — aucun fichier commun |
| 2 | Tâches 5, 6 | **2 agents** | Le rendu consomme les tâches 2 et 4 ; l'API consomme la tâche 1 et la signature de `render_post`, entièrement spécifiée ci-dessous |
| 3 | Tâches 7, 8 | **2 agents** | Dashboard et rétention/docs ne se touchent pas |

Fichiers touchés par vague, pour vérifier l'absence de collision :

- Vague 1 — T2 : `app/content/highlight.py`, `tests/test_highlight.py`. T3 : `app/content/compose.py`, `app/config.py`, `tests/test_compose.py`. T4 : `app/content/templates/kinda_post/*`, `app/content/assets/*`.
- Vague 2 — T5 : `app/content/render.py`, `tests/test_render.py`, `pyproject.toml`. T6 : `app/api/posts.py`, `app/schemas.py`, `app/main.py`, `tests/test_posts_api.py`.
- Vague 3 — T7 : `app/static/dashboard.html`. T8 : `tests/test_retention.py`, `CLAUDE.md`, `README.md`.

**Note pour l'orchestrateur :** la tâche 4 (extraction Figma) est la plus longue et la plus incertaine — la lancer en premier dans la vague 1.

---

### Task 1: Table `posts` — modèle, migration, statuts

**Files:**
- Modify: `app/models.py` (ajouter à la fin, avant ou après `Digest`)
- Create: `alembic/versions/0008_add_posts.py`
- Test: `tests/test_posts_model.py`

**Interfaces:**
- Consumes: `Base`, `JSONVariant`, `_values` de `app/models.py`.
- Produces:
  - `app.models.PostStatus` — enum `DRAFT="draft"`, `APPROVED="approved"`, `PUBLISHED="published"`
  - `app.models.Post` — colonnes : `id: int`, `article_id: int | None`, `article_title: str`, `article_url: str`, `source_name: str | None`, `status: PostStatus`, `template: str`, `caption: str`, `hashtags: list | None`, `slides: list`, `created_at: dt.datetime`, `published_at: dt.datetime | None`

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_posts_model.py` :

```python
import datetime as dt

from app.models import Article, Post, PostStatus, Source, SourceType


def _src(db):
    src = Source(name="Tsugi", type=SourceType.RSS, url="https://tsugi.fr/feed")
    db.add(src)
    db.commit()
    return src


def test_post_defaults(db_session):
    """Un post nait en brouillon, sur le template Kinda, sans date de publication."""
    src = _src(db_session)
    article = Article(
        source_id=src.id,
        url="https://tsugi.fr/a",
        title="Les Vieilles Charrues devoilent leur line-up",
        raw_hash="h1",
        published_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(article)
    db_session.commit()

    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=src.name,
        caption="Le line-up est tombe.",
        slides=[{"headline": "**Vieilles Charrues** : le line-up", "image_url": None}],
    )
    db_session.add(post)
    db_session.commit()

    assert post.status == PostStatus.DRAFT
    assert post.template == "kinda_post"
    assert post.published_at is None
    assert post.created_at is not None
    assert post.slides[0]["headline"].startswith("**Vieilles Charrues**")


def test_post_survit_a_la_suppression_de_l_article(db_session):
    """Les champs denormalises rendent le post lisible sans son article."""
    src = _src(db_session)
    article = Article(source_id=src.id, url="https://tsugi.fr/b", title="Titre", raw_hash="h2")
    db_session.add(article)
    db_session.commit()

    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=src.name,
        caption="c",
        slides=[],
    )
    db_session.add(post)
    db_session.commit()

    db_session.delete(article)
    db_session.commit()

    survivant = db_session.query(Post).one()
    assert survivant.article_title == "Titre"
    assert survivant.article_url == "https://tsugi.fr/b"
    assert survivant.source_name == "Tsugi"
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_posts_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'Post' from 'app.models'`

- [ ] **Step 3: Ajouter l'enum et le modèle**

Dans `app/models.py`, ajouter `PostStatus` juste après la classe `ArticleStatus` :

```python
class PostStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"
```

Puis, à la fin du fichier, la table :

```python
class Post(Base):
    """Un post Instagram genere a partir d'un article.

    Table separee et non colonnes sur `articles` pour deux raisons : un article
    peut donner plusieurs tentatives de post, et surtout les articles sont purges
    a 14 jours alors qu'un post publie est une archive permanente. Meme motif que
    `artist_mentions` : FK en SET NULL et champs DENORMALISES, pour que le post
    reste lisible apres la purge de son article.
    """

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    # Denormalise : survit a la purge de l'article
    article_title: Mapped[str] = mapped_column(Text)
    article_url: Mapped[str] = mapped_column(String(2000))
    source_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[PostStatus] = mapped_column(
        Enum(
            PostStatus, name="post_status", native_enum=False, length=20,
            values_callable=_values,
        ),
        default=PostStatus.DRAFT,
        server_default="draft",
        index=True,
    )
    template: Mapped[str] = mapped_column(
        String(100), default="kinda_post", server_default="kinda_post"
    )
    caption: Mapped[str] = mapped_column(Text)
    hashtags: Mapped[list | None] = mapped_column(JSONVariant)
    # Liste ordonnee : [{"headline": "... **surligne** ...", "image_url": "..."}]
    # Reassigner la liste ENTIERE, jamais de mutation in place
    slides: Mapped[list] = mapped_column(JSONVariant, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il passe**

Run: `uv run pytest tests/test_posts_model.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Écrire la migration**

Créer `alembic/versions/0008_add_posts.py` :

```python
"""add posts : la file editoriale des carrousels Instagram

Les articles sont purges a 14 jours ; un post publie est une archive permanente.
D'ou la FK en SET NULL et les champs denormalises (titre, url, source).

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("article_title", sa.Text(), nullable=False),
        sa.Column("article_url", sa.String(length=2000), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column(
            "template", sa.String(length=100), server_default="kinda_post", nullable=False
        ),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("hashtags", sa.JSON(), nullable=True),
        sa.Column("slides", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_posts")),
        # SET NULL et pas CASCADE : la purge des articles ne doit jamais effacer
        # l'archive editoriale, seulement le lien vers l'article disparu
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name=op.f("fk_posts_articles_article_id"), ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'published')", name=op.f("ck_posts_post_status")
        ),
    )
    op.create_index(op.f("ix_posts_article_id"), "posts", ["article_id"])
    op.create_index(op.f("ix_posts_status"), "posts", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_posts_status"), table_name="posts")
    op.drop_index(op.f("ix_posts_article_id"), table_name="posts")
    op.drop_table("posts")
```

- [ ] **Step 6: Vérifier la parité modèle ↔ migration**

Run: `uv run alembic upgrade head && uv run alembic check`
Expected: `No new upgrade operations detected.`

Si `alembic check` signale une différence sur `hashtags`/`slides`, c'est que le type JSON diverge : dans la migration, remplacer `sa.JSON()` par `postgresql.JSONB(astext_type=sa.Text())` (importer `from sqlalchemy.dialects import postgresql`) et relancer.

Si aucune base Postgres n'est disponible, noter que l'étape est à refaire et continuer.

- [ ] **Step 7: Lint et commit**

```bash
uv run ruff check .
uv run pytest
git add app/models.py alembic/versions/0008_add_posts.py tests/test_posts_model.py
git commit -m "feat(posts): table posts, archive editoriale qui survit a la purge"
```

---

### Task 2: Parser de surlignage `**...**`

**Files:**
- Create: `app/content/__init__.py` (vide)
- Create: `app/content/highlight.py`
- Test: `tests/test_highlight.py`

**Interfaces:**
- Consumes: rien (fonction pure, aucune dépendance projet).
- Produces:
  - `app.content.highlight.MARKER_RE` — `re.Pattern`
  - `app.content.highlight.validate_headline(text: str) -> None` — lève `ValueError` si les marqueurs `**` sont impairs ou si aucun segment n'est surligné
  - `app.content.highlight.to_html(text: str) -> str` — échappe le HTML puis convertit `**x**` en `<mark>x</mark>`

Le surlignage rose est la signature visuelle de Kinda. Dans Figma ce sont des rectangles posés à la main derrière des mots ; ici c'est un balisage porté par le texte, que le CSS reflow tout seul.

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_highlight.py` :

```python
import pytest

from app.content.highlight import to_html, validate_headline


def test_to_html_convertit_les_segments_surlignes():
    resultat = to_html("Nico Moreno et **c'est ce week-end** aux **Vieilles Charrues**")
    assert resultat == (
        "Nico Moreno et <mark>c&#x27;est ce week-end</mark> "
        "aux <mark>Vieilles Charrues</mark>"
    )


def test_to_html_echappe_le_html_avant_de_baliser():
    """Le titre vient d'un flux RSS : il ne doit jamais injecter de balise."""
    resultat = to_html("**<script>alert(1)</script>** en tournee")
    assert "<script>" not in resultat
    assert resultat.startswith("<mark>&lt;script&gt;")


def test_validate_refuse_les_marqueurs_impairs():
    with pytest.raises(ValueError, match="apparies"):
        validate_headline("Un titre **sans fermeture")


def test_validate_refuse_une_accroche_sans_surlignage():
    """Une accroche sans surlignage perd l'identite visuelle du compte."""
    with pytest.raises(ValueError, match="surligne"):
        validate_headline("Un titre parfaitement plat")


def test_validate_accepte_une_accroche_correcte():
    validate_headline("Le retour de **Daft Punk** ?")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_highlight.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.content'`

- [ ] **Step 3: Écrire l'implémentation**

Créer `app/content/__init__.py` (fichier vide).

Créer `app/content/highlight.py` :

```python
"""Balisage du surlignage rose des accroches Kinda.

Dans le template Figma, les aplats roses sont des rectangles positionnes a la main
derriere des segments de texte -- ce qui oblige a tout repositionner des que
l'accroche change de longueur. Ici l'information est portee par le texte lui-meme
(`**segment**`) et le CSS la reflow tout seul.
"""

import html
import re

MARKER_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def validate_headline(text: str) -> None:
    """Leve ValueError si le balisage est invalide."""
    if text.count("**") % 2 != 0:
        raise ValueError("Marqueurs ** non apparies dans l'accroche")
    if MARKER_RE.search(text) is None:
        raise ValueError("L'accroche ne surligne aucun segment")


def to_html(text: str) -> str:
    """Echappe le texte puis convertit `**x**` en `<mark>x</mark>`.

    L'echappement vient EN PREMIER : le texte provient de flux externes, il ne doit
    jamais pouvoir injecter de balise. Les `**` survivent a l'echappement.
    """
    escaped = html.escape(text)
    return MARKER_RE.sub(lambda m: f"<mark>{m.group(1)}</mark>", escaped)
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il passe**

Run: `uv run pytest tests/test_highlight.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint et commit**

```bash
uv run ruff check .
git add app/content/__init__.py app/content/highlight.py tests/test_highlight.py
git commit -m "feat(posts): balisage du surlignage rose des accroches"
```

---

### Task 3: Composition éditoriale (article → slides + légende)

**Files:**
- Create: `app/content/compose.py`
- Modify: `app/config.py` (ajouter `composer_model`)
- Test: `tests/test_compose.py`

**Interfaces:**
- Consumes: `app.models.Article`, `app.models.Post` (Tâche 1) ; `app.content.highlight.validate_headline` (Tâche 2). **Si la tâche 2 n'est pas encore fusionnée**, cette tâche ne doit PAS créer `highlight.py` — importer et laisser le test échouer n'est pas une option : les deux tâches sont dans la même vague et le module sera là au moment du run de vague. En cas de conflit, réexécuter les tests après la vague.
- Produces:
  - `app.content.compose.Slide` — modèle Pydantic, champ `headline: str`
  - `app.content.compose.PostDraft` — `slides: list[Slide]`, `caption: str`, `hashtags: list[str]`
  - `app.content.compose.SYSTEM_PROMPT` — str
  - `app.content.compose.build_user_prompt(article: Article) -> str`
  - `app.content.compose.make_claude_composer(client=None)` → `composer(article: Article) -> PostDraft`
  - `app.content.compose.create_post(db: Session, article: Article, draft: PostDraft, template: str = "kinda_post") -> Post`

**Décision de conception à respecter :** `Slide` en sortie du modèle ne contient **que** `headline`. Les URL d'images ne sont pas demandées à Claude — un modèle qui produit des URL les invente. C'est `create_post` qui remplit `image_url` depuis `article.media_urls`.

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_compose.py` :

```python
import datetime as dt

import pytest
from pydantic import ValidationError

from app.content.compose import PostDraft, Slide, build_user_prompt, create_post
from app.models import Article, PostStatus, Source, SourceType


def _article(db):
    src = Source(name="Tsugi", type=SourceType.RSS, url="https://tsugi.fr/feed")
    db.add(src)
    db.commit()
    article = Article(
        source_id=src.id,
        url="https://tsugi.fr/vieilles-charrues",
        title="Les Vieilles Charrues devoilent leur line-up electro",
        summary="Nico Moreno, Mosimann et Cassius sont annonces.",
        raw_hash="h1",
        published_at=dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc),
        media_urls=["https://tsugi.fr/photo.jpg"],
    )
    db.add(article)
    db.commit()
    return article


def test_draft_refuse_moins_de_deux_slides():
    with pytest.raises(ValidationError):
        PostDraft(slides=[Slide(headline="**Un** seul")], caption="c", hashtags=[])


def test_draft_refuse_plus_de_six_slides():
    slides = [Slide(headline=f"**Slide** {i}") for i in range(7)]
    with pytest.raises(ValidationError):
        PostDraft(slides=slides, caption="c", hashtags=[])


def test_slide_refuse_une_accroche_sans_surlignage():
    with pytest.raises(ValidationError):
        Slide(headline="Une accroche parfaitement plate")


def test_slide_refuse_une_accroche_trop_longue():
    with pytest.raises(ValidationError):
        Slide(headline="**A** " + "x" * 120)


def test_build_user_prompt_contient_le_titre_et_la_source(db_session):
    article = _article(db_session)
    prompt = build_user_prompt(article)
    assert "Vieilles Charrues" in prompt
    assert "Tsugi" in prompt


def test_create_post_denormalise_et_pose_l_image_de_l_article(db_session):
    """La premiere slide herite de l'image de l'article ; les autres restent vides
    tant que l'utilisateur n'en a pas choisi une."""
    article = _article(db_session)
    draft = PostDraft(
        slides=[
            Slide(headline="**Vieilles Charrues** : le line-up"),
            Slide(headline="Rendez-vous **ce week-end**"),
        ],
        caption="Le line-up electro est tombe.",
        hashtags=["#vieillescharrues", "#electro"],
    )

    post = create_post(db_session, article, draft)

    assert post.status == PostStatus.DRAFT
    assert post.article_id == article.id
    assert post.article_title == article.title
    assert post.article_url == article.url
    assert post.source_name == "Tsugi"
    assert post.caption == "Le line-up electro est tombe."
    assert post.hashtags == ["#vieillescharrues", "#electro"]
    assert len(post.slides) == 2
    assert post.slides[0]["image_url"] == "https://tsugi.fr/photo.jpg"
    assert post.slides[1]["image_url"] is None
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_compose.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.content.compose'`

- [ ] **Step 3: Ajouter le réglage du modèle**

Dans `app/config.py`, sous le bloc « Scoring Claude (étape 4) », ajouter :

```python
    # Composition des posts : contrairement au scoring (volume eleve, Haiku pour
    # le cout), la composition tourne quelques fois par jour a la demande et sa
    # qualite se voit directement sur le feed -- on paie un modele plus fort.
    composer_model: str = "claude-sonnet-5"
```

- [ ] **Step 4: Écrire l'implémentation**

Créer `app/content/compose.py` :

```python
"""Article score -> post carrousel Instagram (slides + legende).

Meme forme que `app/pipeline/scoring.py` : structured outputs via `messages.parse()`,
schema Pydantic re-valide en sortie. Les bornes vivent dans le SCHEMA et pas dans le
prompt : si le modele produit huit slides ou une accroche trop longue, la validation
echoue et l'appel est retente.
"""

import logging

import anthropic
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.content.highlight import validate_headline
from app.models import Article, Post

logger = logging.getLogger(__name__)

HEADLINE_MAX = 110


class Slide(BaseModel):
    """Une slide du carrousel. Le template ne comporte QU'UNE zone de texte : pas
    de corps de texte, pas de type de slide. `image_url` n'est pas demande au
    modele -- un modele a qui l'on demande une URL l'invente."""

    headline: str = Field(max_length=HEADLINE_MAX)

    @field_validator("headline")
    @classmethod
    def _balisage_valide(cls, value: str) -> str:
        validate_headline(value)
        return value


class PostDraft(BaseModel):
    slides: list[Slide] = Field(min_length=2, max_length=6)
    caption: str = Field(max_length=2000)
    hashtags: list[str] = Field(default_factory=list, max_length=15)


SYSTEM_PROMPT = """\
Tu ecris les posts Instagram de KINDA, un compte francais d'actualite musicale \
POP / ELECTRO, a destination d'un public francophone NATIONAL.

Kinda couvre la scene electro francaise dans son ensemble (Paris, Lyon, Marseille, \
Bordeaux, Lille, Toulouse, Nantes, Rennes, Brest), les festivals a toutes les \
echelles, et les grosses actus pop et K-pop. Ce n'est PAS un compte regional : la \
Bretagne est un angle recurrent, jamais le sujet principal.

Tu produis un CARROUSEL a partir d'un article. Regles :

1. NOMBRE DE SLIDES : entre 2 et 6, selon la matiere reellement disponible dans \
l'article. Une breve de trois lignes fait 2 slides. N'INVENTE JAMAIS de contenu \
pour remplir une slide de plus : un carrousel court et dense vaut mieux qu'un \
carrousel delaye.

2. ACCROCHE (`headline`) : une phrase par slide, 110 caracteres maximum, en \
francais. C'est le SEUL texte affiche sur l'image. Ton direct, parle, sans \
ponctuation d'article de presse. La premiere slide accroche et donne l'info \
principale ; les suivantes deroulent le detail ; la derniere donne le rendez-vous \
ou l'invitation (date, lieu, sortie, lien en bio).

3. SURLIGNAGE : dans chaque accroche, entoure de ** les segments a mettre en \
avant -- ils seront surlignes en rose, c'est la signature visuelle du compte. \
Surligne ce qui accroche l'oeil : le nom d'artiste, le lieu, la date, le chiffre \
qui frappe. UN A DEUX segments par accroche, jamais plus, sinon l'effet se perd. \
CHAQUE accroche doit contenir au moins un segment surligne.
Exemple d'accroche correcte :
Nico Moreno, Mosimann, Cassius... et **c'est ce week-end** aux **Vieilles Charrues**

4. LEGENDE (`caption`) : le texte sous le post, en francais, 3 a 6 lignes. Elle \
developpe ce que les slides annoncent, cite la source de l'info, et se termine par \
une question ou une invitation a reagir. Pas de hashtags dans la legende.

5. HASHTAGS : 5 a 12, en minuscules, pertinents et specifiques (nom d'artiste, nom \
de festival, ville, genre). Evite les hashtags fourre-tout du type #music ou #love.

N'invente aucun fait qui ne figure pas dans l'article : ni date, ni lieu, ni nom.\
"""


def build_user_prompt(article: Article) -> str:
    parts = [f"Titre : {article.title}"]
    if article.summary:
        parts.append(f"Resume : {article.summary[:2000]}")
    if article.source is not None:
        parts.append(f"Source : {article.source.name}")
    if article.published_at is not None:
        parts.append(f"Publie le : {article.published_at:%d/%m/%Y}")
    if article.category:
        parts.append(f"Categorie : {article.category}")
    if article.mentioned_artists:
        parts.append(f"Artistes cites : {', '.join(article.mentioned_artists)}")
    parts.append(f"URL : {article.url}")
    return "\n".join(parts)


def make_claude_composer(client: anthropic.Anthropic | None = None):
    """Compositeur par defaut : un appel API par post, sortie structuree validee."""
    if client is None:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def composer(article: Article) -> PostDraft:
        response = client.messages.parse(
            model=settings.composer_model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(article)}],
            output_format=PostDraft,
        )
        return response.parsed_output

    return composer


def create_post(
    db: Session, article: Article, draft: PostDraft, template: str = "kinda_post"
) -> Post:
    """Persiste un brouillon. Les champs sont DENORMALISES exprès : l'article sera
    purge a 14 jours, le post doit rester lisible sans lui."""
    premiere_image = None
    if article.media_urls:
        premiere_image = article.media_urls[0]

    slides = [
        {"headline": slide.headline, "image_url": premiere_image if index == 0 else None}
        for index, slide in enumerate(draft.slides)
    ]

    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=article.source.name if article.source is not None else None,
        template=template,
        caption=draft.caption,
        hashtags=draft.hashtags or None,
        slides=slides,
    )
    db.add(post)
    db.commit()
    logger.info("post %s cree pour l'article %s", post.id, article.id)
    return post
```

- [ ] **Step 5: Lancer le test pour vérifier qu'il passe**

Run: `uv run pytest tests/test_compose.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Lint et commit**

```bash
uv run ruff check .
git add app/content/compose.py app/config.py tests/test_compose.py
git commit -m "feat(posts): composition editoriale d'un carrousel a partir d'un article"
```

---

### Task 4: Extraction du template Figma en HTML/CSS

**Files:**
- Create: `app/content/templates/kinda_post/slide.html.j2`
- Create: `app/content/templates/kinda_post/style.css`
- Create: `app/content/assets/logo.png` (ou `.svg`)
- Create: `app/content/assets/fallback.jpg`
- Create: `app/content/templates/kinda_post/REFERENCE.md`

**Interfaces:**
- Consumes: le fichier Figma `kAqCOUyZgvR025RuICJuPW`, frame `234:8` (« TEMPLATE »).
- Produces: un gabarit Jinja rendu par la Tâche 5, dont le **contrat de variables est figé ci-dessous**. La Tâche 5 est écrite contre ce contrat — ne pas le modifier.

**Contrat du gabarit — non négociable :**

| Variable Jinja | Type | Contenu |
|---|---|---|
| `css` | str | contenu de `style.css`, à injecter dans un `<style>` |
| `image_data_uri` | str | photo de fond, déjà en `data:image/...;base64,...` |
| `logo_data_uri` | str | logo Kinda, déjà en data URI |
| `headline_html` | Markup | accroche déjà échappée, avec des `<mark>` |
| `headline_scale` | float | multiplicateur de taille de police (1.0, 0.9, 0.8) |

Le gabarit DOIT produire :
- un élément racine de 1080×1440 px exactement ;
- un élément `id="headline"` contenant `headline_html` — la Tâche 5 mesure sa hauteur via `document.getElementById('headline')` ;
- une taille de police d'accroche calculée avec `headline_scale`, par exemple `font-size: calc(var(--headline-size) * {{ headline_scale }})`.

`style.css` DOIT être autonome : polices incluses en `@font-face` avec le `.woff2` **encodé en base64 dans le fichier**, aucune référence à une URL externe ni à une police système. Une police système ferait diverger le rendu d'une machine à l'autre, silencieusement, par des césures et des débordements — pas par une erreur.

- [ ] **Step 1: Invoquer la skill Figma et lire le design**

Invoquer la skill `figma:figma-design-to-code` (obligatoire avant `get_design_context`), puis :

```
get_design_context(fileKey="kAqCOUyZgvR025RuICJuPW", nodeId="234:8")
```

Relever et noter : famille et graisse de la police de l'accroche, taille en px, interlignage, couleur du texte, couleur du surlignage rose, `padding` du surlignage, couleurs et positions d'arrêt du dégradé sombre, position et taille du logo.

Structure connue de la frame (relevée le 2026-07-22) :

```
frame 234:8 "TEMPLATE" 1080x1440
  234:9  / 234:10  photo de fond, plein cadre
  234:28 / 234:29  rectangles du degrade sombre, y=623, hauteur 817
  234:12 "Frame 3"  logo, x=77 y=87 (KINDA + "news")
  234:31 / 237:41 / 234:32  rectangles roses de surlignage (a NE PAS reproduire
                            en dur : ils deviennent des <mark> qui suivent le texte)
  234:34  le bloc de texte de l'accroche
  234:35 "Guideline" (masque) : marges 77px lateral, 85px haut/bas
```

- [ ] **Step 2: Exporter le PNG de référence**

```
get_screenshot(fileKey="kAqCOUyZgvR025RuICJuPW", nodeId="234:8", maxDimension=1440)
```

Télécharger le PNG et le conserver hors du repo (dossier scratchpad) : il sert de référence visuelle aux étapes 5-6.

- [ ] **Step 3: Extraire les assets**

Exporter le logo (node `234:12`) en PNG transparent 2× via `get_screenshot`, l'enregistrer dans `app/content/assets/logo.png`.

Créer `app/content/assets/fallback.jpg` : une image de secours 1080×1440 utilisée quand la photo de l'article est introuvable. Un aplat sombre aux couleurs de Kinda suffit — il ne doit jamais être joli au point qu'on oublie de le remplacer.

Récupérer le fichier `.woff2` de la police relevée à l'étape 1 et l'encoder :

```bash
python -c "import base64,pathlib;print(base64.b64encode(pathlib.Path('chemin/vers/police.woff2').read_bytes()).decode())" > /tmp/font.b64
```

- [ ] **Step 4: Écrire le gabarit**

Créer `app/content/templates/kinda_post/slide.html.j2` :

```html
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<style>{{ css }}</style>
</head>
<body>
  <div id="slide">
    <img id="photo" src="{{ image_data_uri }}" alt="">
    <div id="scrim"></div>
    <img id="logo" src="{{ logo_data_uri }}" alt="">
    <div id="headline" style="--scale: {{ headline_scale }}">{{ headline_html }}</div>
  </div>
</body>
</html>
```

Créer `app/content/templates/kinda_post/style.css` avec les valeurs relevées à l'étape 1. Squelette à compléter (les valeurs `/* Figma */` sont à remplacer par les vraies) :

```css
@font-face {
  font-family: "KindaDisplay";
  font-weight: 700;
  src: url(data:font/woff2;base64,COLLER_LE_BASE64_ICI) format("woff2");
}

* { margin: 0; padding: 0; box-sizing: border-box; }
body { width: 1080px; height: 1440px; overflow: hidden; }

#slide {
  position: relative;
  width: 1080px;
  height: 1440px;
  overflow: hidden;
  background: #000;
}

#photo {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* Degrade sombre : rend l'accroche lisible sur n'importe quelle photo */
#scrim {
  position: absolute;
  left: 0;
  top: 623px;      /* Figma */
  width: 1080px;
  height: 817px;   /* Figma */
  background: linear-gradient(/* Figma */ to bottom, rgba(0,0,0,0), rgba(0,0,0,0.85));
}

#logo {
  position: absolute;
  right: 77px;     /* marge du Guideline */
  top: 87px;       /* marge du Guideline */
  width: 150px;    /* Figma */
}

#headline {
  --headline-size: 64px;  /* Figma */
  position: absolute;
  left: 77px;
  right: 77px;
  bottom: 85px;
  font-family: "KindaDisplay", sans-serif;
  font-weight: 700;
  font-size: calc(var(--headline-size) * var(--scale));
  line-height: 1.18;      /* Figma */
  color: #fff;            /* Figma */
}

/* Le surlignage rose. box-decoration-break: clone est ce qui fait que le fond
   se redessine correctement sur CHAQUE ligne quand le segment passe a la ligne --
   sans lui, le fond est etire en un seul bloc a travers le retour a la ligne. */
#headline mark {
  background: #FF4FA3;    /* Figma */
  color: #fff;            /* Figma */
  padding: 0.04em 0.14em; /* Figma */
  box-decoration-break: clone;
  -webkit-box-decoration-break: clone;
}
```

- [ ] **Step 5: Rendre et comparer au PNG de référence**

Écrire un script jetable dans le dossier scratchpad (pas dans le repo) qui rend le gabarit avec l'accroche de référence et l'ouvre :

```python
from pathlib import Path
from jinja2 import Template
from markupsafe import Markup
from playwright.sync_api import sync_playwright

TPL = Path("app/content/templates/kinda_post")
HEADLINE = (
    "Nico Moreno, Mosimann, Cassius... et <mark>c'est ce week-end</mark> "
    "aux <mark>Vieilles Charrues</mark>"
)

html = Template((TPL / "slide.html.j2").read_text()).render(
    css=(TPL / "style.css").read_text(),
    image_data_uri="file://" + str(Path("scratchpad/photo.jpg").resolve()),
    logo_data_uri="file://" + str(Path("app/content/assets/logo.png").resolve()),
    headline_html=Markup(HEADLINE),
    headline_scale=1.0,
)
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1080, "height": 1440})
    page.set_content(html, wait_until="load")
    page.screenshot(path="scratchpad/rendu.png")
    b.close()
```

Note : ce script suppose Playwright installé (`uv run playwright install chromium`). Si la Tâche 5 n'est pas encore faite, l'installer ici : `uv add playwright && uv run playwright install chromium`.

- [ ] **Step 6: Boucler jusqu'à convergence**

Ouvrir côte à côte `scratchpad/rendu.png` et le PNG de référence Figma. Corriger `style.css` et recommencer l'étape 5 jusqu'à ce que les deux soient superposables.

Points à vérifier explicitement, dans cet ordre — ce sont ceux qui se voient sur un feed :
1. la position et la taille du bloc d'accroche (marges 77/85) ;
2. la taille de police et l'interlignage — le texte de référence doit tomber sur **trois lignes**, comme dans Figma ;
3. la couleur et le `padding` du surlignage rose, et le fait qu'il se redessine bien ligne par ligne ;
4. l'opacité et la hauteur du dégradé ;
5. la position et la taille du logo.

Cette boucle n'est pas optionnelle. Sans elle, l'extraction produit un résultat « presque juste » qui se voit immédiatement à l'œil.

- [ ] **Step 7: Documenter la provenance**

Créer `app/content/templates/kinda_post/REFERENCE.md` :

```markdown
# Template `kinda_post`

Extrait du fichier Figma `kAqCOUyZgvR025RuICJuPW` (« Kinda design »),
frame `234:8` (« TEMPLATE »), le 2026-07-22. Format 1080x1440.

Figma reste la source de verite du design ; ce dossier en est la transcription.
Apres toute refonte du template dans Figma, refaire l'extraction : lire la frame,
mettre a jour `style.css`, puis reboucler sur le diff visuel contre un export PNG
de la frame. Le gabarit ne doit JAMAIS etre modifie a l'aveugle.

Le contrat de variables du gabarit (`css`, `image_data_uri`, `logo_data_uri`,
`headline_html`, `headline_scale`) et l'element `id="headline"` sont consommes par
`app/content/render.py` : les changer casse le rendu.

Les rectangles roses de Figma ne sont PAS reproduits en dur : ils deviennent des
`<mark>` qui suivent le texte automatiquement.
```

- [ ] **Step 8: Commit**

```bash
uv run ruff check .
git add app/content/templates app/content/assets
git commit -m "feat(posts): template kinda_post extrait de Figma en HTML/CSS"
```

---

### Task 5: Moteur de rendu Playwright

**Files:**
- Create: `app/content/render.py`
- Modify: `pyproject.toml` (dépendance `playwright`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `app.content.highlight.to_html` (Tâche 2) ; les fichiers de `app/content/templates/kinda_post/` et `app/content/assets/` (Tâche 4).
- Produces:
  - `app.content.render.SLIDE_WIDTH = 1080`, `SLIDE_HEIGHT = 1440`
  - `app.content.render.MAX_HEADLINE_HEIGHT = 420`
  - `app.content.render.fetch_image_data_uri(url: str | None) -> str`
  - `app.content.render.render_post(slides: list[dict], template: str = "kinda_post") -> list[bytes]` — une entrée par slide, chaque slide est un dict `{"headline": str, "image_url": str | None}`, sortie = liste de PNG

- [ ] **Step 1: Ajouter la dépendance**

```bash
uv add playwright
uv run playwright install chromium
```

- [ ] **Step 2: Écrire le test qui échoue**

Créer `tests/test_render.py` :

```python
import struct
from pathlib import Path

import pytest

from app.content.render import SLIDE_HEIGHT, SLIDE_WIDTH, fetch_image_data_uri, render_post


def _chromium_playwright_present() -> bool:
    """Le Chromium de Playwright n'est pas sur le PATH : on cherche son cache."""
    caches = [
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    return any(c.exists() and any(c.glob("chromium-*")) for c in caches)


chromium_absent = pytest.mark.skipif(
    not _chromium_playwright_present(),
    reason="Chromium Playwright absent (CI) -- rendu non testable ici",
)


def _png_size(data: bytes) -> tuple[int, int]:
    """Lit largeur/hauteur dans l'entete IHDR d'un PNG."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_fetch_image_data_uri_retombe_sur_le_fallback_si_url_absente():
    uri = fetch_image_data_uri(None)
    assert uri.startswith("data:image/")


def test_fetch_image_data_uri_retombe_sur_le_fallback_si_url_cassee():
    """Une photo introuvable ne doit jamais faire echouer un post."""
    uri = fetch_image_data_uri("https://exemple.invalid/introuvable.jpg")
    assert uri.startswith("data:image/")


@chromium_absent
def test_render_post_produit_un_png_par_slide():
    slides = [
        {"headline": "**Vieilles Charrues** : le line-up", "image_url": None},
        {"headline": "Rendez-vous **ce week-end**", "image_url": None},
    ]
    pngs = render_post(slides)

    assert len(pngs) == 2
    for png in pngs:
        assert _png_size(png) == (SLIDE_WIDTH, SLIDE_HEIGHT)
        assert len(png) > 10_000


@chromium_absent
def test_render_post_reduit_la_police_sur_une_accroche_maximale():
    """La borne de 110 caracteres ne garantit pas l'absence de debordement :
    une accroche longue doit sortir un rendu, pas un post illisible."""
    longue = "**Nico Moreno**, Mosimann, Cassius et le collectif PACT ce week-end aux Vieilles Charrues"
    pngs = render_post([{"headline": longue, "image_url": None}])
    assert _png_size(pngs[0]) == (SLIDE_WIDTH, SLIDE_HEIGHT)
```

- [ ] **Step 3: Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.content.render'`

- [ ] **Step 4: Écrire l'implémentation**

Créer `app/content/render.py` :

```python
"""Rendu des slides en PNG par un Chromium headless.

Figma ne peut pas rendre a l'execution : son API REST est en lecture seule sur le
contenu d'un fichier, et l'ecriture passe par l'API Plugin, qui n'existe que dans
l'editeur ouvert. Le template est donc extrait une fois en HTML/CSS (voir
`templates/kinda_post/REFERENCE.md`) et rendu ici.

Ce module ne connait ni la base ni Claude : il recoit une liste de dicts.
"""

import base64
import logging
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from playwright.sync_api import sync_playwright

from app.content.highlight import to_html

logger = logging.getLogger(__name__)

SLIDE_WIDTH = 1080
SLIDE_HEIGHT = 1440
# Hauteur allouee au bloc d'accroche, au-dela on reduit la police
MAX_HEADLINE_HEIGHT = 420
# Paliers de reduction. Bornee a trois essais : au-dela le probleme n'est pas la
# taille de police mais l'accroche elle-meme.
SCALE_STEPS = (1.0, 0.9, 0.8)

TEMPLATES_DIR = Path(__file__).parent / "templates"
ASSETS_DIR = Path(__file__).parent / "assets"

_USER_AGENT = "KindaBot/1.0 (+https://github.com/kinda)"  # ASCII pur, cf. CLAUDE.md


def _data_uri(content: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")


def fetch_image_data_uri(url: str | None) -> str:
    """Telecharge l'image et l'inline en base64.

    Inliner et non referencer : une image chargee par URL rend la capture non
    deterministe (elle part parfois avant la fin du chargement). Une photo
    introuvable retombe sur l'image de secours -- un post sans photo vaut mieux
    qu'un post en echec.
    """
    if url:
        try:
            response = httpx.get(
                url, timeout=10.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
            )
            response.raise_for_status()
            mime = response.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            if mime.startswith("image/"):
                return _data_uri(response.content, mime)
            logger.warning("contenu non-image pour %s : %s", url, mime)
        except Exception as exc:  # noqa: BLE001 -- toute erreur retombe sur le fallback
            logger.warning("image non recuperee (%s) : %s", url, exc)
    return _data_uri((ASSETS_DIR / "fallback.jpg").read_bytes(), "image/jpeg")


def _logo_data_uri() -> str:
    return _data_uri((ASSETS_DIR / "logo.png").read_bytes(), "image/png")


def _render_one(page, template, css, logo_uri, image_uri, headline: str) -> bytes:
    """Rend une slide, en reduisant la police tant que l'accroche deborde."""
    for scale in SCALE_STEPS:
        document = template.render(
            css=css,
            logo_data_uri=logo_uri,
            image_data_uri=image_uri,
            headline_html=Markup(to_html(headline)),
            headline_scale=scale,
        )
        page.set_content(document, wait_until="load")
        page.evaluate("document.fonts.ready")
        height = page.evaluate(
            "document.getElementById('headline').getBoundingClientRect().height"
        )
        if height <= MAX_HEADLINE_HEIGHT or scale == SCALE_STEPS[-1]:
            if height > MAX_HEADLINE_HEIGHT:
                logger.warning("accroche encore trop longue apres reduction : %r", headline)
            return page.screenshot(type="png")
    raise RuntimeError("boucle de reduction sortie sans capture")  # pragma: no cover


def render_post(slides: list[dict], template: str = "kinda_post") -> list[bytes]:
    """Rend chaque slide en PNG 1080x1440. Un seul Chromium pour tout le post."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    jinja_template = env.get_template(f"{template}/slide.html.j2")
    css = (TEMPLATES_DIR / template / "style.css").read_text(encoding="utf-8")
    logo_uri = _logo_data_uri()

    rendus: list[bytes] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT},
                device_scale_factor=1,
            )
            for slide in slides:
                image_uri = fetch_image_data_uri(slide.get("image_url"))
                rendus.append(
                    _render_one(page, jinja_template, css, logo_uri, image_uri, slide["headline"])
                )
        finally:
            browser.close()
    return rendus
```

- [ ] **Step 5: Lancer le test pour vérifier qu'il passe**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS (4 tests). Si les deux tests de rendu sont `SKIPPED`, c'est que Chromium n'est pas installé : lancer `uv run playwright install chromium` et relancer — ils doivent passer en local.

- [ ] **Step 6: Vérification visuelle**

Rendre une slide et la regarder. Un test qui vérifie des dimensions ne dit rien de la qualité du rendu.

```bash
uv run python -c "
from pathlib import Path
from app.content.render import render_post
pngs = render_post([{'headline': \"Nico Moreno et **c'est ce week-end** aux **Vieilles Charrues**\", 'image_url': None}])
Path('/tmp/slide.png').write_bytes(pngs[0])
print('/tmp/slide.png')
"
```

Ouvrir le fichier et vérifier : le surlignage rose est présent et suit le texte, la police est celle du template (pas une police système de repli), le logo est en place.

- [ ] **Step 7: Lint et commit**

```bash
uv run ruff check .
git add app/content/render.py tests/test_render.py pyproject.toml uv.lock
git commit -m "feat(posts): rendu des slides en PNG par Chromium headless"
```

---

### Task 6: API de la file éditoriale

**Files:**
- Create: `app/api/posts.py`
- Modify: `app/schemas.py` (ajouter `PostOut`, `PostUpdate`)
- Modify: `app/main.py` (inclure le routeur)
- Test: `tests/test_posts_api.py`

**Interfaces:**
- Consumes: `app.models.Post`, `app.models.PostStatus`, `app.models.Article`, `app.models.ArticleStatus` (Tâche 1) ; `app.content.compose.make_claude_composer`, `create_post` (Tâche 3) ; `app.content.render.render_post` (Tâche 5).
- Produces: les endpoints consommés par la Tâche 7 :
  - `POST /articles/{article_id}/posts` → 201, `PostOut`
  - `GET /posts?status=&limit=&offset=` → `list[PostOut]`
  - `GET /posts/{post_id}` → `PostOut`
  - `PATCH /posts/{post_id}` → `PostOut`
  - `GET /posts/{post_id}/slides/{index}.png` → `image/png`
  - `GET /posts/{post_id}/export.zip` → `application/zip`

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_posts_api.py` :

```python
import datetime as dt

from app.content.compose import PostDraft, Slide
from app.models import Article, ArticleStatus, Post, PostStatus, Source, SourceType


def _article(db):
    src = Source(name="Tsugi", type=SourceType.RSS, url="https://tsugi.fr/feed")
    db.add(src)
    db.commit()
    article = Article(
        source_id=src.id,
        url="https://tsugi.fr/a",
        title="Les Vieilles Charrues devoilent leur line-up",
        raw_hash="h1",
        published_at=dt.datetime.now(dt.timezone.utc),
    )
    db.add(article)
    db.commit()
    return article


def _post(db, article, status=PostStatus.DRAFT):
    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name="Tsugi",
        status=status,
        caption="Une legende.",
        hashtags=["#electro"],
        slides=[{"headline": "**Vieilles Charrues** : le line-up", "image_url": None}],
    )
    db.add(post)
    db.commit()
    return post


def test_creer_un_post_depuis_un_article(client, db_session, monkeypatch):
    """La composition est mockee : on teste le cablage, pas le modele."""
    article = _article(db_session)

    def faux_composeur():
        def composer(_article):
            return PostDraft(
                slides=[
                    Slide(headline="**Vieilles Charrues** : le line-up"),
                    Slide(headline="Rendez-vous **ce week-end**"),
                ],
                caption="Le line-up est tombe.",
                hashtags=["#vieillescharrues"],
            )

        return composer

    monkeypatch.setattr("app.api.posts.make_claude_composer", faux_composeur)

    response = client.post(f"/articles/{article.id}/posts")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert len(body["slides"]) == 2
    assert body["article_title"] == article.title


def test_creer_un_post_sur_un_article_inexistant(client):
    assert client.post("/articles/9999/posts").status_code == 404


def test_lister_les_posts_par_statut(client, db_session):
    article = _article(db_session)
    _post(db_session, article, status=PostStatus.DRAFT)
    _post(db_session, article, status=PostStatus.APPROVED)

    assert len(client.get("/posts").json()) == 2
    approuves = client.get("/posts?status=approved").json()
    assert len(approuves) == 1
    assert approuves[0]["status"] == "approved"


def test_editer_la_legende_et_les_slides(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)

    response = client.patch(
        f"/posts/{post.id}",
        json={
            "caption": "Une meilleure legende.",
            "slides": [{"headline": "**Nouvelle** accroche", "image_url": "https://x/y.jpg"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["caption"] == "Une meilleure legende."
    assert body["slides"][0]["image_url"] == "https://x/y.jpg"


def test_publier_un_post_marque_l_article_comme_poste(client, db_session):
    """C'est ce qui protege l'article de la purge : USED n'est jamais purge."""
    article = _article(db_session)
    post = _post(db_session, article)

    response = client.patch(f"/posts/{post.id}", json={"status": "published"})

    assert response.status_code == 200
    assert response.json()["published_at"] is not None
    db_session.refresh(article)
    assert article.status == ArticleStatus.USED


def test_editer_un_post_inexistant(client):
    assert client.patch("/posts/9999", json={"caption": "x"}).status_code == 404


def test_slide_hors_bornes(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    assert client.get(f"/posts/{post.id}/slides/7.png").status_code == 404
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_posts_api.py -v`
Expected: FAIL — 404 sur tous les endpoints (le routeur n'existe pas)

- [ ] **Step 3: Ajouter les schémas**

Dans `app/schemas.py`, ajouter les imports nécessaires en tête (`PostStatus` à la ligne d'import de `app.models`) puis, à la fin du fichier :

```python
class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int | None
    article_title: str
    article_url: str
    source_name: str | None
    status: PostStatus
    template: str
    caption: str
    hashtags: list[str] | None
    slides: list[dict]
    created_at: dt.datetime
    published_at: dt.datetime | None


class PostUpdate(BaseModel):
    caption: str | None = None
    hashtags: list[str] | None = None
    slides: list[dict] | None = None
    status: PostStatus | None = None
```

- [ ] **Step 4: Écrire le routeur**

Créer `app/api/posts.py` :

```python
import datetime as dt
import io
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.compose import create_post, make_claude_composer
from app.content.render import render_post
from app.db import get_db
from app.models import Article, ArticleStatus, Post, PostStatus
from app.schemas import PostOut, PostUpdate

router = APIRouter(tags=["posts"])


@router.post("/articles/{article_id}/posts", response_model=PostOut, status_code=201)
def creer_post(article_id: int, db: Session = Depends(get_db)):
    """Genere un brouillon de carrousel. Un appel Claude, declenche a la main :
    le pipeline planifie ne cree jamais de post."""
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    composer = make_claude_composer()
    draft = composer(article)
    return create_post(db, article, draft)


@router.get("/posts", response_model=list[PostOut])
def lister_posts(
    status: PostStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Post)
    if status is not None:
        stmt = stmt.where(Post.status == status)
    stmt = stmt.order_by(Post.created_at.desc(), Post.id.desc())
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/posts/{post_id}", response_model=PostOut)
def lire_post(post_id: int, db: Session = Depends(get_db)):
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.patch("/posts/{post_id}", response_model=PostOut)
def modifier_post(post_id: int, payload: PostUpdate, db: Session = Depends(get_db)):
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    if payload.caption is not None:
        post.caption = payload.caption
    if payload.hashtags is not None:
        post.hashtags = payload.hashtags
    if payload.slides is not None:
        # Reassignation ENTIERE : une mutation in place ne serait pas persistee
        post.slides = payload.slides
    if payload.status is not None:
        post.status = payload.status
        if payload.status is PostStatus.PUBLISHED:
            post.published_at = dt.datetime.now(dt.timezone.utc)
            # L'article passe en USED : c'est ce qui le protege de la purge et
            # evite qu'il soit repropose plus tard
            article = db.get(Article, post.article_id) if post.article_id else None
            if article is not None:
                article.status = ArticleStatus.USED

    db.commit()
    return post


def _post_ou_404(db: Session, post_id: int) -> Post:
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.get("/posts/{post_id}/slides/{index}.png")
def rendre_slide(post_id: int, index: int, db: Session = Depends(get_db)):
    post = _post_ou_404(db, post_id)
    if index < 1 or index > len(post.slides):
        raise HTTPException(status_code=404, detail="Slide not found")
    png = render_post([post.slides[index - 1]], post.template)[0]
    return Response(content=png, media_type="image/png")


def _texte_legende(post: Post) -> str:
    parties = [post.caption]
    if post.hashtags:
        parties.append(" ".join(post.hashtags))
    parties.append(f"Source : {post.article_url}")
    return "\n\n".join(parties)


@router.get("/posts/{post_id}/export.zip")
def exporter_post(post_id: int, db: Session = Depends(get_db)):
    post = _post_ou_404(db, post_id)
    pngs = render_post(post.slides, post.template)

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for numero, png in enumerate(pngs, start=1):
            archive.writestr(f"slide_{numero:02d}.png", png)
        archive.writestr("caption.txt", _texte_legende(post))

    # En-tete en ASCII pur : un nom de fichier accentue casse l'envoi
    return Response(
        content=tampon.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="kinda_post_{post.id}.zip"'},
    )
```

- [ ] **Step 5: Brancher le routeur**

Dans `app/main.py` :

```python
from app.api import articles, artists, digests, posts, sources
```

et, après `app.include_router(digests.router)` :

```python
app.include_router(posts.router)
```

- [ ] **Step 6: Lancer les tests pour vérifier qu'ils passent**

Run: `uv run pytest tests/test_posts_api.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Lint et commit**

```bash
uv run ruff check .
uv run pytest
git add app/api/posts.py app/schemas.py app/main.py tests/test_posts_api.py
git commit -m "feat(posts): API de la file editoriale (creation, edition, export)"
```

---

### Task 7: Écran de la file éditoriale dans le dashboard

**Files:**
- Modify: `app/static/dashboard.html`

**Interfaces:**
- Consumes: les endpoints de la Tâche 6, tels que documentés dans son bloc « Produces ».
- Produces: rien pour d'autres tâches.

- [ ] **Step 1: Lire le dashboard existant**

Run: `wc -l app/static/dashboard.html` puis lire le fichier en entier.

Relever : la façon dont les articles sont listés, comment les appels `fetch` sont écrits, comment les boutons « vu »/« posté » appellent `PATCH /articles/{id}`, et le style existant. **Suivre ces conventions** — ne pas introduire de framework, ne pas restructurer la page.

- [ ] **Step 2: Ajouter le bouton de génération sur chaque article**

À côté des boutons « vu » / « posté » existants, ajouter un bouton « Générer le post » qui appelle :

```javascript
async function genererPost(articleId, bouton) {
  bouton.disabled = true;
  bouton.textContent = 'Génération…';
  try {
    const reponse = await fetch(`/articles/${articleId}/posts`, { method: 'POST' });
    if (!reponse.ok) throw new Error(await reponse.text());
    const post = await reponse.json();
    ouvrirPost(post.id);
  } catch (erreur) {
    alert('Échec de la génération : ' + erreur.message);
    bouton.disabled = false;
    bouton.textContent = 'Générer le post';
  }
}
```

Le bouton doit être désactivé pendant l'appel : la composition prend plusieurs secondes et un double clic créerait deux posts payants.

- [ ] **Step 3: Ajouter la vue « Posts »**

Ajouter un onglet ou une section « Posts » qui liste `GET /posts`, avec un filtre par statut (`draft` / `approved` / `published`). Chaque ligne montre : l'accroche de la première slide, la source, la date de création, le statut.

- [ ] **Step 4: Ajouter l'écran de détail d'un post**

Au clic sur un post, afficher :
- les aperçus des slides, via `<img src="/posts/{id}/slides/{n}.png">` — ajouter un paramètre anti-cache lors des rafraîchissements : `?v=${Date.now()}` ;
- pour chaque slide, un champ texte éditable pour l'accroche (avec un rappel visible que `**texte**` produit le surlignage rose) et un champ pour l'URL de l'image ;
- un champ texte pour la légende et un pour les hashtags ;
- un bouton « Enregistrer » qui envoie `PATCH /posts/{id}` avec les champs modifiés puis recharge les aperçus ;
- un bouton « Valider » (`status: "approved"`) et un bouton « Marquer comme publié » (`status: "published"`) ;
- un lien « Télécharger » vers `/posts/{id}/export.zip`.

Exemple d'enregistrement :

```javascript
async function enregistrerPost(postId, donnees) {
  const reponse = await fetch(`/posts/${postId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(donnees),
  });
  if (!reponse.ok) throw new Error(await reponse.text());
  return reponse.json();
}
```

- [ ] **Step 5: Vérifier à la main**

```bash
uv run uvicorn app.main:app --reload
```

Ouvrir http://localhost:8000, générer un post depuis un article, vérifier que les aperçus s'affichent, modifier une accroche, enregistrer, vérifier que l'aperçu se met à jour avec le nouveau surlignage, télécharger le zip et l'ouvrir.

- [ ] **Step 6: Commit**

```bash
git add app/static/dashboard.html
git commit -m "feat(posts): file editoriale et editeur de carrousel dans le dashboard"
```

---

### Task 8: Rétention des posts et documentation

**Files:**
- Modify: `tests/test_retention.py`
- Modify: `CLAUDE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `app.models.Post` (Tâche 1), `app.pipeline.retention.purge_old_articles` (existant).
- Produces: rien pour d'autres tâches.

- [ ] **Step 1: Écrire le test de survie à la purge**

Ajouter à `tests/test_retention.py` :

```python
def test_les_posts_survivent_a_la_purge_des_articles(db_session):
    """Le point de tout l'exercice : l'article est ephemere, le post publie est
    l'archive editoriale. Les champs denormalises le rendent lisible sans lui.

    Note SQLite : les cles etrangeres ne sont pas appliquees par defaut, donc on
    verifie la SURVIE de la ligne et l'integrite des champs denormalises, pas la
    mise a NULL de `article_id` (garantie par le ON DELETE SET NULL en Postgres).
    """
    from app.models import Post

    src = Source(name="Tsugi", type=SourceType.RSS, url="https://tsugi.fr/feed")
    db_session.add(src)
    db_session.commit()
    vieux = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)
    article = Article(
        source_id=src.id,
        url="https://tsugi.fr/vieux",
        title="Une vieille actu",
        raw_hash="vieux",
        published_at=vieux,
    )
    db_session.add(article)
    db_session.commit()

    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=src.name,
        caption="Legende archivee",
        slides=[{"headline": "**Une** archive", "image_url": None}],
    )
    db_session.add(post)
    db_session.commit()

    purge_old_articles(db_session, retention_days=14)

    assert db_session.query(Article).count() == 0
    survivant = db_session.query(Post).one()
    assert survivant.article_title == "Une vieille actu"
    assert survivant.caption == "Legende archivee"
```

Vérifier en tête de `tests/test_retention.py` que `Source`, `SourceType`, `Article`, `dt` sont bien importés ; ajouter ce qui manque.

- [ ] **Step 2: Lancer le test**

Run: `uv run pytest tests/test_retention.py -v`
Expected: PASS

- [ ] **Step 3: Documenter dans CLAUDE.md**

Dans la section « Commandes », ajouter :

```markdown
- Rendu des posts : nécessite Chromium Playwright en local
  (`uv run playwright install chromium`). Le pipeline cron ne génère AUCUN post —
  la génération est déclenchée à la main depuis le dashboard.
```

Ajouter une section après « Mémoire A&R » :

```markdown
## Génération de posts (`app/content/`)

Le compte d'actu ne s'arrête plus au flux scoré : un article peut devenir un
**carrousel Instagram** prêt à publier. Trois unités indépendantes —
`compose.py` (article → slides + légende, via Claude), `render.py` (slides → PNG
1080×1440, via Chromium headless), `api/posts.py` (file éditoriale).

**Figma n'est jamais appelé à l'exécution.** Son API REST est en lecture seule sur
le contenu d'un fichier ; l'écriture passe par l'API Plugin, qui n'existe que dans
l'éditeur ouvert. Le template est donc extrait UNE FOIS en HTML/CSS dans
`app/content/templates/kinda_post/` — voir le `REFERENCE.md` de ce dossier pour la
procédure de ré-extraction après une refonte du design.

Le **surlignage rose** est porté par le texte lui-même : `**segment**` dans une
accroche devient un `<mark>` que le CSS reflow tout seul. Dans Figma ce sont des
rectangles à repositionner à la main — le rendu HTML est ici plus robuste que
l'original.

Les posts ne sont **jamais purgés** (archive éditoriale, même motif que
`artist_mentions`) et publier un post bascule son article en `USED`, ce qui le
protège aussi.

Le modèle de composition est `composer_model` (défaut `claude-sonnet-5`), plus fort
que celui du scoring : la composition tourne quelques fois par jour et sa qualité
se voit directement sur le feed.
```

- [ ] **Step 4: Documenter dans README.md**

Ajouter une section décrivant le flux utilisateur : ouvrir le dashboard, cliquer « Générer le post » sur un article, relire et corriger les accroches (avec la convention `**surligné**`), remplacer l'image si besoin, valider, télécharger le zip, publier sur Instagram. Mentionner le prérequis `uv run playwright install chromium`.

- [ ] **Step 5: Vérification complète et commit**

```bash
uv run ruff check .
uv run pytest
git add tests/test_retention.py CLAUDE.md README.md
git commit -m "feat(posts): retention des posts + documentation de la brique contenu"
```

---

## Vérification finale (après la vague 3)

- [ ] `uv run pytest` — toute la suite passe
- [ ] `uv run ruff check .` — aucun avertissement
- [ ] `uv run alembic upgrade head && uv run alembic check` — « No new upgrade operations detected. »
- [ ] Bout en bout à la main : lancer le dashboard, générer un post depuis un vrai article, vérifier le rendu à l'œil contre le PNG de référence Figma, exporter le zip
- [ ] Vérifier que `uv run python -m scripts.run_pipeline` ne déclenche **aucune** génération de post

## Hors périmètre — ne pas implémenter

- Les **reels** (le template Reels existe dans le fichier Figma, il sera l'entrée d'un plan séparé)
- La **publication automatique** vers Instagram (API Graph, compte pro, revue Meta)
- La **génération d'images par IA**
- L'**upload de fichiers** : le remplacement d'image se fait par URL

## Réserves connues, non techniques

- **Licence de la police** — l'embarquer en `.woff2` dans le repo suppose que sa licence l'autorise. À vérifier avant la Tâche 4 ; sinon, choisir une police libre approchante.
- **Droits des photos** — la reprise automatique de l'image d'un article place le post en zone grise. Le crédit de la source dans la légende est un usage, pas une autorisation ; le remplacement manuel de l'image existe pour ces cas.
