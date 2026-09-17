# Reels automatiques — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kinda publie seul, en plus de ses deux carrousels quotidiens, deux reels par jour tirés des articles les mieux notés qui portent une vidéo exploitable — avec le même modèle de sécurité que les carrousels (composition à J-1, gel à T-2h, 90 minutes de retard au plus, modes partagés, réclamation atomique, exclusions, dashboard).

**Architecture:** Les créneaux gagnent une colonne `format` (`carrousel` | `reel`, migration 0017) et des fenêtres horaires propres ; l'arbitrage tourne format par format, les reels d'abord, sans qu'un article occupe deux créneaux. Le runner existant dispatche trois outils par format : gabarit composé (`kinda_reel`), préparateur (`app/publish/preparation_reel.py` : cover, téléchargement de la vidéo, assemblage ffmpeg, sondage ffprobe, contrôle des specs Meta) et publieur (`publier_reel` : conteneur `REELS` avec `video_url` + `cover_url` derrière le tunnel éphémère existant). Un crédit `@compte` dénormalisé sur le post est obligatoire en légende. Une correction d'ingestion garde la vignette des vidéos Instagram sans `media_url`.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic, httpx (sync), Pillow, Playwright/Chromium (rendu existant), ffmpeg/ffprobe (Homebrew), `cloudflared` (Homebrew), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-17-reels-automatiques-design.md` — lire d'abord « Le risque principal : l'approvisionnement en vidéos » et « Questions ouvertes ». Ce plan applique les **défauts recommandés**.

## Global Constraints

- **Reels EN PLUS des carrousels.** Rien ne change pour les carrousels : fenêtres `08:30-11:00` et `17:00-19:30`, graine `kinda-{date}-{rang}`, `publication_creneaux = 6`, gabarit `kinda_post`, recadrage 4:5.
- Fenêtres reel (`Europe/Paris`) : **`12:00-14:00,20:30-22:30`** ; **`publication_creneaux_reel = 6`** (3 jours × 2) ; **0 désactive les reels**. Graine reel : `kinda-reel-{date}-{rang}`. Une fenêtre qui chevauche une autre (bornes incluses) lève `FenetresInvalides`.
- `creneaux.publier_a` **reste UNIQUE** tous formats confondus (index `ix_creneaux_publier_a` de 0014, inchangé).
- Arbitrage : **reels d'abord, carrousels ensuite** ; un article n'occupe **jamais** deux créneaux, quel que soit le format.
- Éligibilité reel, dans cet ordre : vidéo exploitable (`videos_exploitables`, durée ≤ `publication_reel_duree_max_s = 90`) ; catégorie de source dans **`publication_reel_categories = "artiste,festival,label,club"`** ; crédit calculable (source de type `instagram`). Candidats reel lus sur **300** articles (60 pour les carrousels).
- Crédit : `posts.credit` (`String(200)`, nullable) = `"@<compte>"` pour une source Instagram. Ligne de légende **`🎥 via @compte`** (post `kinda_reel`) ou **`📸 via @compte`** (autre gabarit), placée avant `Source : <url>`, **jamais coupée**.
- Règles Meta, citées de la documentation le 2026-09-17 : conteneur **MP4/MOV sans edit list, `moov` en tête** ; audio **AAC ≤ 48 kHz**, mono/stéréo, 128 kbps ; vidéo **H.264 ou HEVC**, progressive, **GOP fermé**, 4:2:0 ; **23 à 60 i/s** ; **1920 colonnes max** ; 9:16 recommandé ; **25 Mbps max** ; durée **3 s à 15 min** ; **300 Mo max** ; cover **JPEG ≤ 8 Mo**, sRGB ; `cover_url` l'emporte sur `thumb_offset` ; `share_to_feed` ; statuts `EXPIRED`, `ERROR`, `FINISHED`, `IN_PROGRESS`, `PUBLISHED` ; médias récupérés **en cURL sur une URL publique**.
- Bornes Kinda : durée source **3 à 90 s** (refus, jamais de coupe) ; source téléchargée **≤ 100 Mo** (`publication_reel_source_max_mo`) ; reel assemblé **≤ 100 Mo** (`publication_reel_max_mo`) et exactement **1080×1920** ; sondage d'un conteneur reel **toutes les 15 s, 600 s au plus** (`ATTENTE_REEL_PAS_S = 15.0`, `ATTENTE_REEL_MAX_S = 600.0`) ; `publication_reel_share_to_feed = True`.
- Fichiers figés d'un reel : **`[<prefixe>_reel.mp4, <prefixe>_cover.jpg]`, dans cet ordre**, dans `settings.publication_dir / <creneau.id>`. La cover part en JPEG **sans recadrage**. Le témoin de joignabilité du tunnel est **la cover**, jamais la vidéo.
- Fautes de l'article (→ `PreparationImpossible`, exclusion) : gabarit ou crédit manquant, pas de vidéo, HTTP 4xx sur la vidéo, vidéo trop lourde ou pas une vidéo, durée hors bornes, entrée illisible ou assemblage impossible, délai ffmpeg dépassé, sortie hors specs, cover débordante ou inexploitable. Pannes systémiques (→ remontent, rien n'est exclu, le format seul est suspendu pour le passage) : ffmpeg/ffprobe absents (`AssemblageIndisponible` levée par `exiger_outils`), Chromium absent, réseau ou HTTP 5xx.
- Même modèle de sécurité : réclamation GELE → ECHEC committée **avant** Meta ; mode `apercu` = conteneur créé, jamais `media_publish` ; retard > **90 minutes** → `MANQUE` ; créneau reel sans candidat **reste vide** (pas de repli carrousel).
- API : `https://graph.instagram.com/{meta_api_version}` (`v23.0`), jeton `IG…` en paramètre `access_token` — le même que les carrousels.
- Python sync, pas d'async. Tests sur SQLite in-memory (`tests/conftest.py`). **Aucun test ne sort sur Internet.** Les tests qui exécutent vraiment ffmpeg sont marqués `besoin_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason=...)`, comme `tests/test_video.py`.
- **SQLite rend des `datetime` naïfs** : toujours passer par `app.schedule.store.utc()`. Toujours écrire des dates **UTC**.
- Enums en **VARCHAR + CHECK** (`native_enum=False`). Dans une migration écrite à la main, noms de contraintes entourés de `op.f()`, **à l'intérieur des fonctions** seulement.
- Colonnes JSON : **réassigner en entier**, jamais de mutation en place.
- En-têtes HTTP en **ASCII pur**.
- Binaires Homebrew (`ffmpeg`, `ffprobe`, `cloudflared`) : `shutil.which` **puis** `EMPLACEMENTS_CONNUS` (le PATH de launchd n'a pas `/opt/homebrew/bin`).
- Commentaires en français. Messages de commit en français **sans accents**, terminés par ces deux lignes :
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
  ```
- `uv run ruff check .` et `uv run pytest` passent à la fin de chaque tâche.
- **Rien n'est publié pendant l'implémentation.** Le mode reste celui de l'utilisateur ; aucun agent ne lance `scripts.tester_publication` ni ne change le mode. **Aucun appel en direct à Meta** (quota Graph).

## Écarts assumés, et pourquoi

1. **Sondage reel 15 s / 600 s** au lieu de la recommandation Meta (1/min, 5 min au plus). L'encodage d'une vidéo est plus long que celui d'une image, et au moment du sondage le créneau est déjà réclamé : un délai trop court le ferait passer en `ECHEC` sans retour. 40 lectures au pire par reel.
2. **`publier_a` reste unique tous formats confondus** plutôt qu'un couple unique `(format, publier_a)`. Deux publications à la même minute sont à éviter de toute façon ; le refus des fenêtres qui se chevauchent rend la collision impossible et évite de toucher un index en production.
3. **Une vidéo trop longue est refusée, pas coupée.** Couper le contenu d'un artiste aggrave la question des droits et peut en trahir le sens.
4. **Le crédit s'applique aussi aux carrousels** issus d'Instagram (`📸 via @compte`). Même bonne foi, aucun coût ; obligatoire seulement pour les reels.
5. **Pas de repli carrousel sur un créneau reel vide.** Ce serait un troisième post dans la journée.
6. **Les vignettes des reels déjà ingérés ne sont pas rattrapées.** La correction d'ingestion vaut pour les posts à venir ; la dédup écarte les anciens et la purge les emporte à 14 jours.
7. **Les tests existants de la file décrivent le carrousel seul** : ils reçoivent un fixture `publication_creneaux_reel = 0` au lieu d'être réécrits ; les reels ont leurs propres fichiers de test.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `app/sources/instagram.py` (modifier) | Vidéo sans `media_url` → sa vignette en `photo` ; `compte_depuis_url()` |
| `app/models.py` (modifier) | `FormatCreneau`, `Creneau.format`, `Post.credit` |
| `alembic/versions/0017_reels.py` (créer) | Colonnes `creneaux.format` (+ CHECK) et `posts.credit` |
| `app/config.py` (modifier) | Réglages `publication_reel_*` et `publication_creneaux_reel` |
| `app/schedule/slots.py` (modifier) | Fenêtres par format, graine reel, `FenetresInvalides` |
| `app/content/credit.py` (créer) | `credit_video(article)` : `@compte` d'une source Instagram |
| `app/content/compose.py` (modifier) | `create_post` pose `credit` |
| `app/content/legende.py` (modifier) | Ligne de crédit jamais coupée |
| `app/schedule/store.py` (modifier) | Créneaux, candidats et arbitrage par format ; éligibilité reel ; offre |
| `app/content/video.py` (modifier) | `exiger_outils()`, sondage étendu (codecs, cadence, échantillonnage), `-use_editlist 0`, `-flags +cgop` |
| `app/publish/images.py` (modifier) | `en_jpeg()` : JPEG ≤ 8 Mo sans recadrage |
| `app/publish/preparation_reel.py` (créer) | Le gel d'un reel |
| `app/publish/instagram.py` (modifier) | `preparer_reel()`, sondage paramétrable |
| `app/publish/publier.py` (modifier) | `publier_reel()` |
| `app/schedule/runner.py` (modifier) | Dispatch par format, panne systémique par format, ménage |
| `scripts/run_publication.py` (modifier) | Brancher `preparer_reel` / `publier_reel` |
| `scripts/tester_publication.py` (modifier) | Essai d'un post `kinda_reel` sans publier |
| `app/schemas.py` (modifier) | `CreneauOut.format`, `OffreReelsOut` |
| `app/api/schedule.py` (modifier) | Format dans la vue, décaler par format, éligibilité au remplacement, `GET /schedule/offre-reels` |
| `app/static/dashboard.html` (modifier) | Pastille de format, ligne d'offre reels |
| `CLAUDE.md` (modifier) | Documenter les reels automatiques |
| Tests | `tests/test_sources/test_instagram.py`, `tests/test_schedule_modeles.py`, `tests/test_schedule_slots.py`, `tests/test_credit.py`, `tests/test_legende.py`, `tests/test_schedule_store_reels.py`, `tests/test_video.py`, `tests/test_publish_images.py`, `tests/test_publish_preparation_reel.py`, `tests/test_publish_instagram.py`, `tests/test_publish_publier.py`, `tests/test_schedule_runner_reels.py`, `tests/test_run_publication.py`, `tests/test_schedule_api_reels.py`, `tests/test_dashboard_mode_control.py` |

---

## Partie A — Matière et modèle

### Task 1: Garder la vignette d'une vidéo Instagram sans `media_url`

**Files:**
- Modify: `app/sources/instagram.py`
- Test: `tests/test_sources/test_instagram.py`

**Interfaces:**
- Consumes: `app.content.media.entree`, `PHOTO`, `VIDEO`
- Produces:
  - `compte_depuis_url(url: str) -> str` — le username d'une URL de source `ig:nom`, `ig:@nom` ou `https://www.instagram.com/nom/`
  - `_media(noeud)` : un nœud `VIDEO` sans `media_url` mais avec `thumbnail_url` rend `[entree("photo", thumbnail_url)]`

- [ ] **Step 1: Write the failing test**

Ajouter à la fin de `tests/test_sources/test_instagram.py` (et `compte_depuis_url` à l'import depuis `app.sources.instagram`) :

```python
def _payload(*noeuds):
    return {"business_discovery": {"media": {"data": list(noeuds)}}}


def test_un_reel_sans_media_url_garde_sa_vignette_en_photo():
    """Meta omet media_url quand le media contient de la musique protegee :
    c'est le cas de presque tous les reels d'artistes. Sans ce repli, le post
    arrivait sans aucun media -- ni cover, ni place dans la file carrousel."""
    noeud = {
        "media_type": "VIDEO",
        "media_product_type": "REELS",
        "thumbnail_url": "https://cdn.example/vignette.jpg",
        "permalink": "https://www.instagram.com/reel/CCC/",
        "timestamp": "2026-09-10T14:30:00+0000",
    }
    (item,) = make_adapter().parse(_payload(noeud))
    assert item.media == [
        {
            "type": "photo",
            "url": "https://cdn.example/vignette.jpg",
            "preview_url": None,
            "duration_ms": None,
            "width": None,
            "height": None,
        }
    ]


def test_une_video_sans_media_url_ni_vignette_ne_donne_rien():
    noeud = {"media_type": "VIDEO", "permalink": "https://www.instagram.com/reel/DDD/"}
    (item,) = make_adapter().parse(_payload(noeud))
    assert item.media == []


def test_une_video_avec_media_url_reste_une_video():
    (item,) = make_adapter().parse(_payload(PAYLOAD["business_discovery"]["media"]["data"][1]))
    assert (item.media[0]["type"], item.media[0]["url"]) == ("video", "https://cdn.example/video.mp4")


@pytest.mark.parametrize(
    "url, attendu",
    [
        ("ig:saralandry", "saralandry"),
        ("ig:@saralandry", "saralandry"),
        ("https://www.instagram.com/saralandry/", "saralandry"),
    ],
)
def test_compte_depuis_url(url, attendu):
    assert compte_depuis_url(url) == attendu
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sources/test_instagram.py -v`
Expected: FAIL — `ImportError: cannot import name 'compte_depuis_url'`

- [ ] **Step 3: Implement**

Dans `app/sources/instagram.py`, après `titre_depuis_caption`, ajouter :

```python
def compte_depuis_url(url: str) -> str:
    """Le username cible d'une source, ecrite `ig:nom` ou en URL instagram.com.

    Fonction de module et pas seulement propriete de l'adapter : le credit d'un
    post (`app/content/credit.py`) en a besoin sans instancier d'adapter.
    """
    url = url.strip().rstrip("/")
    if url.lower().startswith("ig:"):
        return url[3:].lstrip("@").strip()
    return url.rsplit("/", 1)[-1].lstrip("@")
```

Remplacer le corps de la propriété `compte` de `InstagramAdapter` par :

```python
        return compte_depuis_url(self.source.url)
```

Remplacer `_media` par :

```python
def _media(noeud: dict) -> list[dict]:
    """Les medias d'un post, carrousel aplati.

    Un carrousel porte ses enfants sous `children`, et n'a pas de `media_url`
    propre : sans cet aplatissement, un carrousel arriverait sans aucun media.

    Meta OMET `media_url` quand le media contient du contenu protege (musique) --
    documente, et constate sur 10 reels sur 10 d'un compte d'artiste. La video
    n'est alors pas telechargeable, mais sa vignette illustre encore le post :
    elle entre comme une PHOTO, ce qui la rend utilisable en cover et en
    carrousel, et jamais en reel (il n'y a pas de video a publier).
    """
    enfants = (noeud.get("children") or {}).get("data")
    if enfants:
        return [m for enfant in enfants for m in _media(enfant)]
    type_ = _TYPES.get(noeud.get("media_type"))
    url = noeud.get("media_url")
    vignette = noeud.get("thumbnail_url")
    if type_ == VIDEO and not url and vignette:
        return [entree(PHOTO, vignette)]
    if not type_ or not url:
        return []
    return [entree(type_, url, preview_url=vignette)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sources/test_instagram.py -v`
Expected: tout passe

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add app/sources/instagram.py tests/test_sources/test_instagram.py
git commit -F - <<'EOF'
fix(instagram): garder la vignette d'une video sans media_url

Meta omet media_url des medias qui contiennent de la musique protegee,
soit presque tous les reels d'artistes : 39 reels sur 59 arrivaient sans
aucun media. La vignette entre desormais comme une photo.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 2: Le format d'un créneau et le crédit d'un post

**Files:**
- Modify: `app/models.py` (enum `FormatCreneau` après `EtatCreneau` ; colonne `Post.credit` après `source_name` ; colonne `Creneau.format` après `publier_a`)
- Create: `alembic/versions/0017_reels.py`
- Test: `tests/test_schedule_modeles.py`

**Interfaces:**
- Consumes: rien
- Produces:
  - `FormatCreneau(str, enum.Enum)` : `CARROUSEL = "carrousel"`, `REEL = "reel"`
  - `Creneau.format: Mapped[FormatCreneau]`, défaut `CARROUSEL`, `server_default="carrousel"`
  - `Post.credit: Mapped[str | None]` (`String(200)`)
  - migration `0017` (down_revision `0016`), CHECK `ck_creneaux_format_creneau`

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_schedule_modeles.py` (et `FormatCreneau`, `Post` à l'import depuis `app.models`) :

```python
def test_un_creneau_est_un_carrousel_par_defaut(db_session):
    creneau = Creneau(publier_a=HEURE)
    db_session.add(creneau)
    db_session.commit()
    assert creneau.format == FormatCreneau.CARROUSEL


def test_un_creneau_reel(db_session):
    creneau = Creneau(publier_a=HEURE, format=FormatCreneau.REEL)
    db_session.add(creneau)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Creneau, creneau.id).format == FormatCreneau.REEL


def test_un_reel_et_un_carrousel_ne_partagent_pas_non_plus_la_meme_heure(db_session):
    """L'unicite de publier_a vaut tous formats confondus : deux publications a
    la meme minute sont a eviter, quel que soit leur format."""
    db_session.add_all([Creneau(publier_a=HEURE), Creneau(publier_a=HEURE, format=FormatCreneau.REEL)])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_un_post_n_a_pas_de_credit_par_defaut(db_session):
    post = Post(article_title="A", article_url="https://t/a", caption="L", slides=[])
    db_session.add(post)
    db_session.commit()
    assert post.credit is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_schedule_modeles.py -v`
Expected: FAIL — `ImportError: cannot import name 'FormatCreneau'`

- [ ] **Step 3: Write the models**

Dans `app/models.py`, après `class EtatCreneau` :

```python
class FormatCreneau(str, enum.Enum):
    """Ce qu'un creneau publie : un carrousel d'images ou un reel video.

    Le cycle de vie (LIBRE -> ... -> PUBLIE) est le meme ; seuls changent le
    gabarit compose, le preparateur du gel et le publieur.
    """

    CARROUSEL = "carrousel"
    REEL = "reel"
```

Dans `class Post`, après `source_name` :

```python
    # Le compte a crediter en legende (`@compte`), pose a la composition quand la
    # source est Instagram. Denormalise comme `source_name` : le post survit a la
    # purge de l'article, son credit aussi. Obligatoire pour un reel.
    credit: Mapped[str | None] = mapped_column(String(200))
```

Dans `class Creneau`, après `publier_a` :

```python
    format: Mapped[FormatCreneau] = mapped_column(
        Enum(
            FormatCreneau, name="format_creneau", native_enum=False, length=20,
            values_callable=_values,
        ),
        default=FormatCreneau.CARROUSEL,
        server_default="carrousel",
    )
```

- [ ] **Step 4: Write the migration**

```python
# alembic/versions/0017_reels.py
"""reels : le format d'un creneau et le credit d'un post

Les creneaux existants deviennent des carrousels (server_default), sans
backfill. `posts.credit` est nullable : NULL = pas de compte a crediter
(source non Instagram).

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# Convention du projet : VARCHAR + CHECK, jamais d'enum natif. `op.f()` reste
# dans les fonctions (un appel au niveau module leve a l'import, cf. 0015).
FORMATS = ("carrousel", "reel")


def upgrade() -> None:
    op.add_column(
        "creneaux",
        sa.Column("format", sa.String(length=20), server_default="carrousel", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_creneaux_format_creneau"), "creneaux", sa.column("format").in_(FORMATS)
    )
    op.add_column("posts", sa.Column("credit", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("posts", "credit")
    op.drop_constraint(op.f("ck_creneaux_format_creneau"), "creneaux", type_="check")
    op.drop_column("creneaux", "format")
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_schedule_modeles.py tests/test_migrations.py -v`
Expected: tout passe

- [ ] **Step 6: Vérifier la migration sur un cluster jetable**

Jamais sur Neon ni sur l'instance Postgres de l'utilisateur :

```bash
PG=/Library/PostgreSQL/15/bin
D="$(mktemp -d)"
"$PG/initdb" -D "$D" -U postgres -A trust > /dev/null
"$PG/pg_ctl" -D "$D" -o "-p 5499" -l "$D/log" start
"$PG/createdb" -p 5499 -U postgres essai
export DATABASE_URL=postgresql+psycopg://postgres@localhost:5499/essai
uv run alembic upgrade 0016
psql -p 5499 -U postgres essai -c "INSERT INTO creneaux (publier_a) VALUES ('2026-09-20 07:12+00')"
uv run alembic upgrade head
psql -p 5499 -U postgres essai -c "SELECT format FROM creneaux"
psql -p 5499 -U postgres essai -c "INSERT INTO creneaux (publier_a, format) VALUES ('2026-09-21 07:12+00', 'video')" ; echo "code $?"
uv run alembic downgrade 0016 && uv run alembic upgrade head && uv run alembic check
unset DATABASE_URL
"$PG/pg_ctl" -D "$D" stop
```

Expected : la ligne existante vaut `carrousel` ; l'insertion `video` échoue sur `ck_creneaux_format_creneau` (code non nul) ; downgrade puis upgrade passent ; `alembic check` rend « No new upgrade operations detected ». Si `psql` n'est pas dans le PATH, utiliser `"$PG/psql"`.

- [ ] **Step 7: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 8: Commit**

```bash
git add app/models.py alembic/versions/0017_reels.py tests/test_schedule_modeles.py
git commit -F - <<'EOF'
feat(modeles): format des creneaux et credit des posts (migration 0017)

Un creneau publie un carrousel ou un reel ; les creneaux existants restent
des carrousels. posts.credit porte le compte a crediter en legende.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 3: Réglages et créneaux par format

**Files:**
- Modify: `app/config.py` (après `publication_retard_max_minutes`)
- Modify: `app/schedule/slots.py`
- Modify: `.env.example` (après `# PUBLICATION_MODE=arret`)
- Test: `tests/test_schedule_slots.py`

**Interfaces:**
- Consumes: `settings.publication_fenetre_matin`, `settings.publication_fenetre_apres_midi`, `settings.publication_fuseau`
- Produces:
  - réglages : `publication_reel_fenetres: str = "12:00-14:00,20:30-22:30"`, `publication_creneaux_reel: int = 6`, `publication_reel_categories: str = "artiste,festival,label,club"`, `publication_reel_duree_min_s: float = 3.0`, `publication_reel_duree_max_s: float = 90.0`, `publication_reel_source_max_mo: int = 100`, `publication_reel_max_mo: int = 100`, `publication_reel_share_to_feed: bool = True`
  - `slots.CARROUSEL = "carrousel"`, `slots.REEL = "reel"`
  - `FenetresInvalides(ValueError)`
  - `fenetres(format_: str = CARROUSEL) -> list[str]`
  - `verifier_fenetres() -> None`
  - `horaires_du_jour(jour: dt.date, format_: str = CARROUSEL) -> list[dt.datetime]`
  - `prochains_horaires(maintenant: dt.datetime, nombre: int, format_: str = CARROUSEL) -> list[dt.datetime]`

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_schedule_slots.py` (imports : `import random`, `import pytest`, `from app.config import settings`, et `FenetresInvalides` depuis `app.schedule.slots`) :

```python
def test_les_carrousels_gardent_exactement_leurs_heures():
    """Changer la graine des carrousels deplacerait toutes les heures deja
    programmees en base."""
    jour = dt.date(2026, 9, 20)
    attendus = []
    for rang, (debut, fin) in enumerate(((dt.time(8, 30), dt.time(11, 0)), (dt.time(17, 0), dt.time(19, 30)))):
        ouverture = dt.datetime.combine(jour, debut, tzinfo=PARIS)
        minutes = int((dt.datetime.combine(jour, fin, tzinfo=PARIS) - ouverture).total_seconds() // 60)
        tirage = random.Random(f"kinda-{jour.isoformat()}-{rang}").randint(0, minutes)
        attendus.append((ouverture + dt.timedelta(minutes=tirage)).astimezone(dt.timezone.utc))
    assert horaires_du_jour(jour) == attendus


def test_deux_reels_par_jour_dans_leurs_fenetres():
    for decalage in range(40):
        jour = dt.date(2026, 9, 20) + dt.timedelta(days=decalage)
        midi, soir = horaires_du_jour(jour, "reel")
        assert dt.time(12, 0) <= _local(midi).time() <= dt.time(14, 0)
        assert dt.time(20, 30) <= _local(soir).time() <= dt.time(22, 30)


def test_le_tirage_reel_est_deterministe():
    assert horaires_du_jour(dt.date(2026, 9, 20), "reel") == horaires_du_jour(dt.date(2026, 9, 20), "reel")


def test_prochains_horaires_reel():
    maintenant = dt.datetime(2026, 9, 20, 13, 59, tzinfo=PARIS).astimezone(dt.timezone.utc)
    horaires = prochains_horaires(maintenant, 6, "reel")
    assert len(horaires) == 6 and horaires == sorted(horaires)
    assert all(h > maintenant for h in horaires)


def test_zero_creneau_reel_ne_boucle_pas(monkeypatch):
    assert prochains_horaires(dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc), 0, "reel") == []
    monkeypatch.setattr(settings, "publication_reel_fenetres", "")
    assert prochains_horaires(dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc), 6, "reel") == []


def test_une_fenetre_reel_qui_chevauche_un_carrousel_est_refusee(monkeypatch):
    """Sinon deux creneaux pourraient tomber a la meme minute : publier_a est
    unique, l'arbitrage leverait une IntegrityError obscure."""
    monkeypatch.setattr(settings, "publication_reel_fenetres", "10:30-12:00,20:30-22:30")
    with pytest.raises(FenetresInvalides, match="10:30-12:00"):
        horaires_du_jour(dt.date(2026, 9, 20), "reel")


def test_deux_fenetres_qui_se_touchent_sont_refusees(monkeypatch):
    """Bornes incluses : le tirage peut tomber sur la minute de fin."""
    monkeypatch.setattr(settings, "publication_reel_fenetres", "11:00-12:00")
    with pytest.raises(FenetresInvalides):
        horaires_du_jour(dt.date(2026, 9, 20))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_schedule_slots.py -v`
Expected: FAIL — `ImportError: cannot import name 'FenetresInvalides'`

- [ ] **Step 3: Add the settings**

Dans `app/config.py`, après `publication_retard_max_minutes` :

```python
    # --- Reels automatiques (en plus des carrousels) ---
    # Fenetres propres, heure de Paris, separees par des virgules : une fenetre =
    # un reel par jour. Elles ne doivent chevaucher ni les fenetres carrousel ni
    # entre elles (slots.FenetresInvalides) : publier_a est unique.
    publication_reel_fenetres: str = "12:00-14:00,20:30-22:30"
    # 3 jours x 2 reels. 0 desactive les reels sans toucher aux carrousels.
    publication_creneaux_reel: int = 6
    # Qui a le droit d'etre reposte en video : les comptes qui PRODUISENT l'actu.
    # Jamais un media par defaut -- ce serait une double reprise (voir « Qui parle »).
    publication_reel_categories: str = "artiste,festival,label,club"
    # Meta accepte 3 s a 15 min ; 90 s est une borne EDITORIALE. Trop longue :
    # refusee, jamais coupee.
    publication_reel_duree_min_s: float = 3.0
    publication_reel_duree_max_s: float = 90.0
    # Poids max de la video source telechargee, et du reel assemble (Meta : 300 Mo).
    publication_reel_source_max_mo: int = 100
    publication_reel_max_mo: int = 100
    # Le reel apparait aussi dans la grille du compte.
    publication_reel_share_to_feed: bool = True
```

Dans `.env.example`, après `# PUBLICATION_MODE=arret` :

```
# Reels automatiques (0 creneau = pas de reels)
# PUBLICATION_CRENEAUX_REEL=6
# PUBLICATION_REEL_FENETRES=12:00-14:00,20:30-22:30
# PUBLICATION_REEL_CATEGORIES=artiste,festival,label,club
```

- [ ] **Step 4: Implement slots**

Remplacer `app/schedule/slots.py` par :

```python
"""Les creneaux de publication : une date, des heures, tirees une fois pour toutes.

L'heure est tiree dans chaque fenetre avec une GRAINE DERIVEE DE LA DATE. C'est
la decision centrale du module : sans elle, l'agent (qui passe toutes les 10
minutes) retirerait une heure a chaque passage et elle glisserait -- un creneau
pourrait n'etre jamais atteint. Avec elle, recalculer a 9 h et a 10 h redonne le
meme 14 h 37.

Deux formats, deux jeux de fenetres. Les carrousels gardent la graine d'origine
(`kinda-...`) : en changer deplacerait les heures deja programmees en base. Les
fenetres ne doivent jamais se chevaucher : `creneaux.publier_a` est unique tous
formats confondus.

Python pur : aucune base, aucun reseau. Tout sort en UTC.
"""

import datetime as dt
import random
from zoneinfo import ZoneInfo

from app.config import settings

CARROUSEL = "carrousel"
REEL = "reel"


class FenetresInvalides(ValueError):
    """Deux fenetres se chevauchent, ou une fenetre finit avant de commencer."""


def _fenetre(texte: str) -> tuple[dt.time, dt.time]:
    debut, fin = texte.split("-")
    return dt.time.fromisoformat(debut.strip()), dt.time.fromisoformat(fin.strip())


def fenetres(format_: str = CARROUSEL) -> list[str]:
    if format_ == REEL:
        return [f.strip() for f in settings.publication_reel_fenetres.split(",") if f.strip()]
    return [settings.publication_fenetre_matin, settings.publication_fenetre_apres_midi]


def verifier_fenetres() -> None:
    bornes = [(format_, texte, *_fenetre(texte)) for format_ in (CARROUSEL, REEL) for texte in fenetres(format_)]
    for rang, (format_a, texte_a, debut_a, fin_a) in enumerate(bornes):
        if fin_a < debut_a:
            raise FenetresInvalides(f"fenetre {texte_a} ({format_a}) : la fin precede le debut")
        for format_b, texte_b, debut_b, fin_b in bornes[rang + 1:]:
            # Bornes incluses : le tirage peut tomber sur la minute de fin.
            if debut_a <= fin_b and debut_b <= fin_a:
                raise FenetresInvalides(
                    f"les fenetres {texte_a} ({format_a}) et {texte_b} ({format_b}) se chevauchent"
                )


def horaires_du_jour(jour: dt.date, format_: str = CARROUSEL) -> list[dt.datetime]:
    verifier_fenetres()
    fuseau = ZoneInfo(settings.publication_fuseau)
    graine = "kinda" if format_ == CARROUSEL else f"kinda-{format_}"
    horaires = []
    for rang, fenetre in enumerate(fenetres(format_)):
        debut, fin = _fenetre(fenetre)
        ouverture = dt.datetime.combine(jour, debut, tzinfo=fuseau)
        fermeture = dt.datetime.combine(jour, fin, tzinfo=fuseau)
        minutes = int((fermeture - ouverture).total_seconds() // 60)
        # Graine en chaine : son hachage est stable d'une execution a l'autre.
        tirage = random.Random(f"{graine}-{jour.isoformat()}-{rang}").randint(0, minutes)
        horaires.append((ouverture + dt.timedelta(minutes=tirage)).astimezone(dt.timezone.utc))
    return horaires


def prochains_horaires(maintenant: dt.datetime, nombre: int, format_: str = CARROUSEL) -> list[dt.datetime]:
    # Sans fenetre, la boucle ci-dessous ne finirait jamais.
    if nombre <= 0 or not fenetres(format_):
        return []
    jour = maintenant.astimezone(ZoneInfo(settings.publication_fuseau)).date()
    horaires: list[dt.datetime] = []
    while len(horaires) < nombre:
        horaires += [h for h in horaires_du_jour(jour, format_) if h > maintenant]
        jour += dt.timedelta(days=1)
    return horaires[:nombre]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_schedule_slots.py -v`
Expected: tout passe

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/schedule/slots.py .env.example tests/test_schedule_slots.py
git commit -F - <<'EOF'
feat(file): fenetres et creneaux propres aux reels

Deux reels par jour (12:00-14:00, 20:30-22:30), graine distincte, heures
des carrousels inchangees. Des fenetres qui se chevauchent sont refusees :
publier_a est unique tous formats confondus.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 4: Le crédit du compte source, en légende

**Files:**
- Create: `app/content/credit.py`
- Modify: `app/content/compose.py` (`create_post`)
- Modify: `app/content/legende.py`
- Test: `tests/test_credit.py`, `tests/test_legende.py`

**Interfaces:**
- Consumes: `compte_depuis_url` (tâche 1), `Post.credit` (tâche 2)
- Produces:
  - `credit_video(article) -> str | None` — `"@compte"` si `article.source.type == SourceType.INSTAGRAM`, sinon `None`
  - `create_post(...)` pose `post.credit = credit_video(article)`
  - `texte_legende(post) -> str` : ligne `🎥 via @compte` (template `kinda_reel`) ou `📸 via @compte` avant la source, jamais coupée ; lit `credit` et `template` par `getattr` (les tests passent des `SimpleNamespace`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_credit.py
"""Le compte a crediter : un reel ne part jamais sans lui."""

from types import SimpleNamespace

from app.content.compose import CoverVariante, PostDraft, create_post
from app.content.credit import credit_video
from app.models import Article, Source, SourceCategorie, SourceType


def _article(type_, url):
    return SimpleNamespace(source=SimpleNamespace(type=type_, url=url))


def test_une_source_instagram_donne_son_compte():
    assert credit_video(_article(SourceType.INSTAGRAM, "ig:saralandry")) == "@saralandry"


def test_une_source_rss_ne_donne_pas_de_credit():
    assert credit_video(_article(SourceType.RSS, "https://tsugi.fr/feed")) is None


def test_sans_source_pas_de_credit():
    assert credit_video(SimpleNamespace(source=None)) is None


def test_create_post_pose_le_credit(db_session):
    source = Source(name="Sara Landry", type=SourceType.INSTAGRAM, url="ig:saralandry",
                    categorie=SourceCategorie.ARTISTE)
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://www.instagram.com/p/AAA/", title="Live", raw_hash="c1")
    db_session.add(article)
    db_session.commit()
    variante = CoverVariante(headline="**Accroche**", title="**Titre**", subtitle="**Sous**")
    draft = PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=["#techno"])
    post = create_post(db_session, article, draft, template="kinda_reel")
    assert (post.credit, post.template) == ("@saralandry", "kinda_reel")
```

Ajouter à `tests/test_legende.py` :

```python
def test_le_credit_d_un_reel_precede_la_source():
    post = _post()
    post.credit, post.template = "@saralandry", "kinda_reel"
    assert texte_legende(post) == (
        "Une legende 🎧 ✨\n\n#electro\n\n🎥 via @saralandry\n\nSource : https://tsugi.fr/a"
    )


def test_le_credit_d_un_carrousel_a_son_propre_pictogramme():
    post = _post(hashtags=())
    post.credit, post.template = "@tsugimag", "kinda_post"
    assert "📸 via @tsugimag\n\nSource : https://tsugi.fr/a" in texte_legende(post)


def test_le_credit_n_est_jamais_coupe():
    post = _post(caption="x" * 3000)
    post.credit, post.template = "@saralandry", "kinda_reel"
    texte = texte_legende(post)
    assert len(texte) <= LEGENDE_MAX
    assert texte.endswith("🎥 via @saralandry\n\nSource : https://tsugi.fr/a")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_credit.py tests/test_legende.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.content.credit'`

- [ ] **Step 3: Implement**

```python
# app/content/credit.py
"""Le compte a crediter sous un post repris d'Instagram.

Reposter la video ou la photo d'un artiste sans le nommer serait s'en attribuer
le contenu. La mention `@compte` est le minimum de bonne foi -- et elle notifie
le compte cite, ce qui est voulu. Obligatoire pour un reel (eligibilite, gel).
"""

from app.models import SourceType
from app.sources.instagram import compte_depuis_url


def credit_video(article) -> str | None:
    source = getattr(article, "source", None)
    if source is None or source.type != SourceType.INSTAGRAM:
        return None
    compte = compte_depuis_url(source.url)
    return f"@{compte}" if compte else None
```

Dans `app/content/compose.py`, importer `from app.content.credit import credit_video` et, dans le constructeur `Post(...)` de `create_post`, après `source_name=...` :

```python
        credit=credit_video(article),
```

Remplacer `texte_legende` dans `app/content/legende.py` par :

```python
def texte_legende(post) -> str:
    hashtags = (post.hashtags or [])[:HASHTAGS_MAX]
    parties = [post.caption]
    if hashtags:
        parties.append(" ".join(hashtags))
    credit = getattr(post, "credit", None)
    if credit:
        pictogramme = "🎥" if getattr(post, "template", None) == "kinda_reel" else "📸"
        parties.append(f"{pictogramme} via {credit}")
    parties.append(f"Source : {post.article_url}")
    texte = "\n\n".join(parties)
    if len(texte) <= LEGENDE_MAX:
        return texte
    # On coupe la legende elle-meme, jamais le credit ni la source : citer le
    # compte et l'editeur est le minimum de bonne foi quand on reprend leurs medias.
    fin = "\n\n".join(parties[1:])
    place = LEGENDE_MAX - len(fin) - len("\n\n") - len("…")
    return post.caption[: max(place, 0)].rstrip() + "…" + "\n\n" + fin
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_credit.py tests/test_legende.py tests/test_compose.py -v`
Expected: tout passe

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add app/content/credit.py app/content/compose.py app/content/legende.py tests/test_credit.py tests/test_legende.py
git commit -F - <<'EOF'
feat(legende): crediter le compte Instagram source

Le post porte le compte a crediter ; la legende ajoute « via @compte »
avant la source, jamais coupe.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 5: La file par format

**Files:**
- Modify: `app/schedule/store.py` (remplacement complet)
- Modify: `tests/test_schedule_store.py`, `tests/test_schedule_api.py`, `tests/test_schedule_runner.py` (fixture carrousel seul)
- Test: `tests/test_schedule_store_reels.py`

**Interfaces:**
- Consumes: `FormatCreneau` (tâche 2), `prochains_horaires(..., format_)` (tâche 3), `credit_video` (tâche 4), `videos_exploitables`, `queue.arbitrer` (inchangé)
- Produces:
  - `TEMPLATES: dict[FormatCreneau, str]` = `{CARROUSEL: "kinda_post", REEL: "kinda_reel"}`
  - `ORDRE_ARBITRAGE = (FormatCreneau.REEL, FormatCreneau.CARROUSEL)`
  - `nombre_de_creneaux(format_: FormatCreneau) -> int`
  - `synchroniser_creneaux(db, maintenant, format_: FormatCreneau = FormatCreneau.CARROUSEL) -> list[Creneau]`
  - `categories_reel() -> set[str]`, `duree_max_reel_ms() -> int`
  - `raison_ineligible_reel(article) -> str | None`
  - `candidats_eligibles(db, maintenant, format_: FormatCreneau = FormatCreneau.CARROUSEL) -> list[Candidat]`
  - `post_a_reprendre(db, article_id: int, template: str = "kinda_post") -> int | None`
  - `appliquer_arbitrage(db, maintenant) -> list[Creneau]` (tous formats, triés par heure)
  - `offre_reels(db, maintenant) -> dict` = `{"avec_video": int, "eligibles": int, "fraicheur_jours": int}`
  - `utc`, `VERROUILLES`, `MODIFIABLES` inchangés

- [ ] **Step 1: Isoler les tests existants sur le carrousel**

Ces trois fichiers décrivent la file carrousel seule ; sans ce réglage, les créneaux reel s'intercalent dans les listes et les comptes. Ajouter en tête de `tests/test_schedule_store.py`, `tests/test_schedule_api.py` et `tests/test_schedule_runner.py`, après les imports (ajouter `import pytest` et `from app.config import settings` là où ils manquent) :

```python
@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    # Ces tests decrivent la file carrousel seule ; les reels ont leurs propres fichiers.
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
```

Run: `uv run pytest tests/test_schedule_store.py tests/test_schedule_api.py tests/test_schedule_runner.py -q`
Expected: tout passe (le code n'a pas encore changé).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_schedule_store_reels.py
"""La file des reels : eligibilite, creneaux propres, pas de double emploi."""

import datetime as dt

from app.config import settings
from app.content.media import entree
from app.models import (
    Article, Creneau, EtatCreneau, FormatCreneau, Post, Source, SourceCategorie, SourceType,
)
from app.schedule.slots import prochains_horaires
from app.schedule.store import (
    appliquer_arbitrage,
    candidats_eligibles,
    offre_reels,
    post_a_reprendre,
    raison_ineligible_reel,
    synchroniser_creneaux,
    utc,
)

MAINTENANT = dt.datetime(2026, 9, 20, 4, 0, tzinfo=dt.timezone.utc)


def _source(db, nom="saralandry", categorie=SourceCategorie.ARTISTE, type_=SourceType.INSTAGRAM):
    url = f"ig:{nom}" if type_ == SourceType.INSTAGRAM else f"https://{nom}/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, n, relevance=80, video=True, photo=False, duree_ms=None):
    media = []
    if video:
        media.append(entree("video", f"https://cdn/{n}.mp4", preview_url=f"https://cdn/{n}.jpg", duration_ms=duree_ms))
    if photo:
        media.append(entree("photo", f"https://img/{n}.jpg"))
    article = Article(source_id=source.id, url=f"https://t/{n}", title=f"Actu {n}", raw_hash=f"h{n}",
                      relevance_score=relevance, hotness_score=20,
                      published_at=MAINTENANT - dt.timedelta(hours=2), media=media)
    db.add(article)
    db.commit()
    return article


def _post(db, article, template):
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                caption="L", slides=[{"kind": "cover"}], template=template)
    db.add(post)
    db.commit()
    return post


def test_les_creneaux_reel_suivent_leurs_propres_fenetres(db_session):
    creneaux = synchroniser_creneaux(db_session, MAINTENANT, FormatCreneau.REEL)
    assert [utc(c.publier_a) for c in creneaux] == prochains_horaires(MAINTENANT, 6, "reel")
    assert {c.format for c in creneaux} == {FormatCreneau.REEL}


def test_zero_creneau_reel_desactive_les_reels(db_session, monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert len(creneaux) == 6
    assert {c.format for c in creneaux} == {FormatCreneau.CARROUSEL}


def test_eligibilite_reel(db_session):
    artiste = _source(db_session)
    media = _source(db_session, "djmag", SourceCategorie.MEDIA)
    rss = _source(db_session, "tsugi.fr", SourceCategorie.ARTISTE, SourceType.RSS)
    assert raison_ineligible_reel(_article(db_session, artiste, 1)) is None
    assert "media" in raison_ineligible_reel(_article(db_session, media, 2))
    assert raison_ineligible_reel(_article(db_session, artiste, 3, video=False, photo=True)) == "aucune video exploitable"
    assert raison_ineligible_reel(_article(db_session, artiste, 4, duree_ms=120_000)) == "aucune video exploitable"
    assert "credit" in raison_ineligible_reel(_article(db_session, rss, 5))


def test_seuls_les_articles_eligibles_sont_candidats_reel(db_session):
    artiste = _source(db_session)
    bon = _article(db_session, artiste, 1)
    _article(db_session, artiste, 2, video=False, photo=True)
    _article(db_session, _source(db_session, "djmag", SourceCategorie.MEDIA), 3)
    assert [c.article_id for c in candidats_eligibles(db_session, MAINTENANT, FormatCreneau.REEL)] == [bon.id]


def test_un_article_video_va_en_reel_et_jamais_aussi_en_carrousel(db_session):
    video = _article(db_session, _source(db_session), 1, relevance=95)
    photo = _article(db_session, _source(db_session, "tsugi", SourceCategorie.MEDIA), 2, video=False, photo=True)
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    reels = [c.article_id for c in creneaux if c.format == FormatCreneau.REEL]
    carrousels = [c.article_id for c in creneaux if c.format == FormatCreneau.CARROUSEL]
    assert reels.count(video.id) == 1 and video.id not in carrousels
    assert photo.id in carrousels
    assert [utc(c.publier_a) for c in creneaux] == sorted(utc(c.publier_a) for c in creneaux)


def test_un_carrousel_epingle_garde_son_article_hors_des_reels(db_session):
    video = _article(db_session, _source(db_session), 1)
    db_session.add(Creneau(publier_a=MAINTENANT + dt.timedelta(hours=1, minutes=7), article_id=video.id,
                           etat=EtatCreneau.PROGRAMME, epingle=True))
    db_session.commit()
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert all(c.article_id is None for c in creneaux if c.format == FormatCreneau.REEL)


def test_deux_passages_donnent_la_meme_file(db_session):
    artiste = _source(db_session)
    for n in range(3):
        _article(db_session, artiste, n, relevance=70 + n, photo=True)
    premiere = [(c.format, c.article_id) for c in appliquer_arbitrage(db_session, MAINTENANT)]
    seconde = [(c.format, c.article_id) for c in appliquer_arbitrage(db_session, MAINTENANT)]
    assert premiere == seconde
    assert db_session.query(Creneau).count() == 12


def test_post_a_reprendre_filtre_le_gabarit(db_session):
    article = _article(db_session, _source(db_session), 1)
    _post(db_session, article, "kinda_post")
    assert post_a_reprendre(db_session, article.id, "kinda_reel") is None
    reel = _post(db_session, article, "kinda_reel")
    assert post_a_reprendre(db_session, article.id, "kinda_reel") == reel.id


def test_un_brouillon_carrousel_ne_donne_pas_d_avance_en_reel(db_session):
    article = _article(db_session, _source(db_session), 1)
    _post(db_session, article, "kinda_post")
    (candidat,) = candidats_eligibles(db_session, MAINTENANT, FormatCreneau.REEL)
    assert candidat.compose is False


def test_offre_reels(db_session):
    artiste = _source(db_session)
    _article(db_session, artiste, 1)
    _article(db_session, _source(db_session, "djmag", SourceCategorie.MEDIA), 2)
    _article(db_session, artiste, 3, video=False, photo=True)
    assert offre_reels(db_session, MAINTENANT) == {"avec_video": 2, "eligibles": 1, "fraicheur_jours": 3}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_schedule_store_reels.py -v`
Expected: FAIL — `ImportError: cannot import name 'offre_reels'`

- [ ] **Step 4: Implement**

Remplacer `app/schedule/store.py` par :

```python
"""Le lien entre l'arbitrage, en Python pur, et la base.

Trois gestes, pour chaque format : tenir a jour les creneaux futurs, lister les
articles publiables, et ecrire le resultat de l'arbitrage. Toute date lue en base
passe par `utc()` : SQLite rend des datetime naifs, et comparer un naif a un
aware leve une erreur.

Deux formats, une regle : un article n'occupe JAMAIS deux creneaux. Les reels
sont arbitres d'abord -- la matiere video est rare, la matiere photo abondante --
et les carrousels piochent dans le reste.
"""

import datetime as dt

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.credit import credit_video
from app.content.media import videos_exploitables
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau, Post, PostStatus
from app.schedule.queue import Candidat, Place, arbitrer
from app.schedule.slots import prochains_horaires

# Un creneau dans l'un de ces etats ne bouge plus.
VERROUILLES = frozenset({
    EtatCreneau.GELE, EtatCreneau.PUBLIE, EtatCreneau.APERCU, EtatCreneau.ECHEC, EtatCreneau.MANQUE,
})
MODIFIABLES = frozenset({EtatCreneau.LIBRE, EtatCreneau.PROGRAMME})

TEMPLATES = {FormatCreneau.CARROUSEL: "kinda_post", FormatCreneau.REEL: "kinda_reel"}
ORDRE_ARBITRAGE = (FormatCreneau.REEL, FormatCreneau.CARROUSEL)
# Les videos sont rares dans le haut du classement : on lit plus loin avant le
# filtre Python pour ne pas rater un reel classe 70e.
LIMITE_CARROUSEL = 60
LIMITE_REEL = 300


def utc(moment: dt.datetime | None) -> dt.datetime | None:
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.timezone.utc)


def nombre_de_creneaux(format_: FormatCreneau) -> int:
    if format_ == FormatCreneau.REEL:
        return settings.publication_creneaux_reel
    return settings.publication_creneaux


def _futurs(format_: FormatCreneau, maintenant: dt.datetime):
    return select(Creneau).where(Creneau.publier_a > maintenant, Creneau.format == format_)


def synchroniser_creneaux(
    db: Session, maintenant: dt.datetime, format_: FormatCreneau = FormatCreneau.CARROUSEL
) -> list[Creneau]:
    attendus = prochains_horaires(maintenant, nombre_de_creneaux(format_), format_.value)
    existants = {utc(c.publier_a): c for c in db.scalars(_futurs(format_, maintenant))}
    for heure in attendus:
        if heure not in existants:
            db.add(Creneau(publier_a=heure, format=format_))
    # Un creneau futur qui n'est plus dans la grille (fenetres changees en
    # config, reels desactives) disparait, sauf s'il est epingle ou deja engage.
    for heure, creneau in existants.items():
        if heure not in attendus and creneau.etat in MODIFIABLES and not creneau.epingle:
            db.delete(creneau)
    db.commit()
    return list(db.scalars(_futurs(format_, maintenant).order_by(Creneau.publier_a)))


def _a_une_image(article: Article) -> bool:
    # Sans image, la cover partirait sur le fond de secours : pas publiable.
    return any(
        (m.get("type") == "photo" and m.get("url")) or m.get("preview_url")
        for m in article.media or []
    )


def categories_reel() -> set[str]:
    return {c.strip() for c in settings.publication_reel_categories.split(",") if c.strip()}


def duree_max_reel_ms() -> int:
    return int(settings.publication_reel_duree_max_s * 1000)


def raison_ineligible_reel(article: Article) -> str | None:
    """None si l'article peut partir en reel, sinon la raison, affichable."""
    if not videos_exploitables(article.media, duree_max_reel_ms()):
        return "aucune video exploitable"
    source = article.source
    categorie = source.categorie.value if source is not None and source.categorie is not None else None
    if categorie not in categories_reel():
        autorisees = ", ".join(sorted(categories_reel())) or "aucune"
        return f"compte de categorie {categorie or 'inconnue'} : reels reserves a {autorisees}"
    if credit_video(article) is None:
        return "credit impossible : la video ne vient pas d'un compte Instagram"
    return None


def _articles_publiables(db: Session, maintenant: dt.datetime, limite: int) -> list[Article]:
    depuis = maintenant - dt.timedelta(days=settings.publication_fraicheur_jours)
    return db.scalars(
        select(Article)
        .where(Article.relevance_score >= settings.publication_min_relevance)
        .where(Article.duplicate_of_id.is_(None))
        .where(Article.status != ArticleStatus.USED)
        .where(Article.file_exclu_raison.is_(None))
        # Un article deja parti (pour de vrai ou en apercu), quel que soit le
        # format, fait avancer la file : pas de reel d'un article deja publie en
        # carrousel, et le mode apercu ne reprend pas le meme article.
        .where(
            ~Article.id.in_(
                select(Creneau.article_id).where(
                    Creneau.etat.in_([EtatCreneau.APERCU, EtatCreneau.PUBLIE]),
                    Creneau.article_id.is_not(None),
                )
            )
        )
        .where(func.coalesce(Article.published_at, Article.fetched_at) >= depuis)
        .order_by(Article.relevance_score.desc(), Article.id)
        .limit(limite)
    ).all()


def candidats_eligibles(
    db: Session, maintenant: dt.datetime, format_: FormatCreneau = FormatCreneau.CARROUSEL
) -> list[Candidat]:
    reel = format_ == FormatCreneau.REEL
    articles = _articles_publiables(db, maintenant, LIMITE_REEL if reel else LIMITE_CARROUSEL)
    # L'avance « deja compose » ne vaut que pour un brouillon du bon gabarit.
    composes = set(
        db.scalars(
            select(Post.article_id).where(
                Post.status != PostStatus.PUBLISHED,
                Post.article_id.is_not(None),
                Post.template == TEMPLATES[format_],
            )
        )
    )
    garde = (lambda a: raison_ineligible_reel(a) is None) if reel else _a_une_image
    return [
        Candidat(a.id, a.relevance_score, a.hotness_score or 0, utc(a.published_at or a.fetched_at), a.id in composes)
        for a in articles
        if garde(a)
    ]


def post_a_reprendre(db: Session, article_id: int, template: str = "kinda_post") -> int | None:
    return db.scalar(
        select(Post.id)
        .where(Post.article_id == article_id, Post.status != PostStatus.PUBLISHED, Post.template == template)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(1)
    )


def _tenus_ailleurs(db: Session, maintenant: dt.datetime, format_: FormatCreneau) -> set[int]:
    return set(
        db.scalars(
            select(Creneau.article_id).where(
                Creneau.article_id.is_not(None),
                or_(
                    # Un creneau passe mais encore dans le delai de retard garde
                    # son article, quel que soit son format.
                    and_(
                        Creneau.publier_a <= maintenant,
                        Creneau.etat.in_([EtatCreneau.PROGRAMME, EtatCreneau.GELE]),
                    ),
                    # L'autre format ne cede pas un article epingle ou deja engage.
                    and_(
                        Creneau.format != format_,
                        Creneau.publier_a > maintenant,
                        or_(Creneau.epingle.is_(True), Creneau.etat.in_(VERROUILLES)),
                    ),
                ),
            )
        )
    )


def appliquer_arbitrage(db: Session, maintenant: dt.datetime) -> list[Creneau]:
    tous: list[Creneau] = []
    places_ce_passage: set[int] = set()
    for format_ in ORDRE_ARBITRAGE:
        creneaux = synchroniser_creneaux(db, maintenant, format_)
        occupes = _tenus_ailleurs(db, maintenant, format_) | places_ce_passage
        candidats = [c for c in candidats_eligibles(db, maintenant, format_) if c.article_id not in occupes]
        places = [
            Place(utc(c.publier_a), c.article_id, c.epingle or c.etat in VERROUILLES) for c in creneaux
        ]
        affectation = arbitrer(places, candidats, maintenant)
        for creneau in creneaux:
            if creneau.epingle or creneau.etat in VERROUILLES:
                continue
            article_id = affectation.get(utc(creneau.publier_a))
            if article_id != creneau.article_id:
                creneau.article_id = article_id
                creneau.post_id = post_a_reprendre(db, article_id, TEMPLATES[format_]) if article_id else None
            creneau.etat = EtatCreneau.PROGRAMME if article_id else EtatCreneau.LIBRE
        db.commit()
        places_ce_passage |= {c.article_id for c in creneaux if c.article_id is not None}
        tous += creneaux
    return sorted(tous, key=lambda c: utc(c.publier_a))


def offre_reels(db: Session, maintenant: dt.datetime) -> dict:
    """Combien d'articles de la fenetre de fraicheur portent une video, et combien
    passent toutes les regles reel. C'est la mesure qui dira si deux reels par
    jour sont tenables (Meta omet la video de la plupart des reels d'artistes)."""
    articles = _articles_publiables(db, maintenant, LIMITE_REEL)
    avec_video = [a for a in articles if videos_exploitables(a.media, duree_max_reel_ms())]
    return {
        "avec_video": len(avec_video),
        "eligibles": sum(1 for a in avec_video if raison_ineligible_reel(a) is None),
        "fraicheur_jours": settings.publication_fraicheur_jours,
    }
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_schedule_store_reels.py tests/test_schedule_store.py tests/test_schedule_api.py tests/test_schedule_runner.py -v`
Expected: tout passe

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/schedule/store.py tests/test_schedule_store_reels.py tests/test_schedule_store.py tests/test_schedule_api.py tests/test_schedule_runner.py
git commit -F - <<'EOF'
feat(file): arbitrage par format, reels d'abord, sans double emploi

Creneaux, candidats et brouillons repris par format. Un article video
eligible (compte artiste, festival, label ou club, credit calculable)
part en reel ; les carrousels piochent dans le reste. offre_reels mesure
la matiere disponible.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

## Partie B — Figer et publier un reel

### Task 6: Un MP4 aux specs de Meta, une cover sans recadrage

**Files:**
- Modify: `app/content/video.py`
- Modify: `app/publish/images.py`
- Test: `tests/test_video.py`, `tests/test_publish_images.py`

**Interfaces:**
- Consumes: rien de neuf
- Produces:
  - `video.exiger_outils() -> None` — lève `AssemblageIndisponible` si `ffmpeg` ou `ffprobe` manque
  - `video.sonder(video: bytes) -> dict` : clés existantes (`width`, `height`, `duration`, `has_audio`) **plus** `video_codec: str | None`, `audio_codec: str | None`, `sample_rate: int | None`, `fps: float`
  - `video._cadence(texte: str | None) -> float` — `"30/1"` → `30.0`, illisible → `0.0`
  - `assembler_reel` écrit sans edit list (`-use_editlist 0`) et en GOP fermé (`-flags +cgop`)
  - `images.en_jpeg(octets: bytes, poids_max: int = POIDS_MAX) -> bytes` — JPEG RGB, dimensions conservées ; `ImageInexploitable` si illisible ou trop lourd

- [ ] **Step 1: Write the failing tests**

Ajouter à `tests/test_video.py` (et `import app.content.video as video_mod`, `_cadence`, `exiger_outils` à l'import) :

```python
def test_cadence():
    assert _cadence("30/1") == 30.0
    assert _cadence("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert _cadence("0/0") == 0.0
    assert _cadence(None) == 0.0


def test_exiger_outils_sans_ffprobe(monkeypatch):
    monkeypatch.setattr(video_mod.shutil, "which", lambda nom: "/bin/ffmpeg" if nom == "ffmpeg" else None)
    monkeypatch.setattr(video_mod, "EMPLACEMENTS_CONNUS", ())
    with pytest.raises(AssemblageIndisponible, match="ffprobe"):
        exiger_outils()


def _boite(octets, nom):
    """Le contenu d'une boite MP4 de premier niveau, ou None."""
    position = 0
    while position + 8 <= len(octets):
        taille = int.from_bytes(octets[position:position + 4], "big")
        if octets[position + 4:position + 8] == nom:
            return octets[position:position + taille]
        if taille < 8:
            return None
        position += taille
    return None


@besoin_ffmpeg
def test_le_reel_respecte_les_specs_de_meta(tmp_path):
    """Specs Meta du 2026-09-17 : H.264/HEVC, AAC <= 48 kHz, 23-60 i/s, moov en
    tete, AUCUNE edit list. ffmpeg en ecrit une par defaut (amorcage AAC)."""
    sortie = assembler_reel(_cover(tmp_path), _video(tmp_path, 1280, 720, secondes=3))
    infos = sonder(sortie)
    assert (infos["video_codec"], infos["audio_codec"]) == ("h264", "aac")
    assert infos["sample_rate"] <= 48000
    assert 23 <= infos["fps"] <= 60
    moov = _boite(sortie, b"moov")
    assert moov is not None and sortie.find(b"moov") < sortie.find(b"mdat")
    assert b"elst" not in moov
```

Ajouter à `tests/test_publish_images.py` (et `en_jpeg` à l'import) :

```python
def test_la_cover_d_un_reel_garde_son_9_16():
    """Meta prend lui-meme le 9:16 central pour l'onglet Reels : pas de recadrage."""
    assert _dimensions(en_jpeg(_image(1080, 1920))) == ("JPEG", (1080, 1920), "RGB")


def test_en_jpeg_aplatit_la_transparence():
    format_, _, mode = _dimensions(en_jpeg(_image(1080, 1920, mode="RGBA")))
    assert (format_, mode) == ("JPEG", "RGB")


def test_en_jpeg_refuse_une_image_illisible():
    with pytest.raises(ImageInexploitable):
        en_jpeg(b"pas une image")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_video.py tests/test_publish_images.py -v`
Expected: FAIL — `ImportError: cannot import name '_cadence'`

- [ ] **Step 3: Implement video.py**

Après `_exiger_ffmpeg`, ajouter :

```python
def exiger_outils() -> None:
    """ffmpeg ET ffprobe, avant tout travail. Leur absence est une panne de la
    machine, pas un defaut de l'article : l'appelant doit pouvoir la distinguer
    d'une video illisible, qui leve la meme exception plus tard."""
    _exiger_ffmpeg("ffmpeg")
    _exiger_ffmpeg("ffprobe")


def _cadence(texte: str | None) -> float:
    """`avg_frame_rate` de ffprobe (« 30000/1001 ») en images par seconde."""
    try:
        numerateur, denominateur = (texte or "0/0").split("/")
        return float(numerateur) / float(denominateur) if float(denominateur) else 0.0
    except ValueError:
        return 0.0
```

Dans `sonder`, remplacer le `return` final par :

```python
    flux_audio = next((f for f in donnees["streams"] if f["codec_type"] == "audio"), None)
    return {
        "width": flux_video["width"],
        "height": flux_video["height"],
        "duration": float(donnees["format"].get("duration", 0)),
        "has_audio": flux_audio is not None,
        # Ce que Meta controle : codecs, echantillonnage, cadence.
        "video_codec": flux_video.get("codec_name"),
        "audio_codec": flux_audio.get("codec_name") if flux_audio else None,
        "sample_rate": int(flux_audio["sample_rate"]) if flux_audio and flux_audio.get("sample_rate") else None,
        "fps": _cadence(flux_video.get("avg_frame_rate")),
    }
```

Dans `assembler_reel`, remplacer la ligne `"-movflags", "+faststart",` par :

```python
            # GOP ferme et pas d'edit list : deux exigences ecrites de Meta pour
            # les reels (2026-09-17). ffmpeg ecrit une edit list par defaut pour
            # compenser l'amorcage AAC.
            "-flags", "+cgop",
            "-use_editlist", "0",
            "-movflags", "+faststart",
```

- [ ] **Step 4: Implement images.py**

Remplacer `normaliser` par ces trois fonctions, puis ajouter `en_jpeg` :

```python
def _ouvrir_rgb(octets: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(octets))
        image.load()
    except Exception as exc:  # noqa: BLE001 -- Pillow leve des types tres varies
        raise ImageInexploitable(f"image illisible : {exc}") from exc
    # Une photo de telephone porte souvent son orientation en EXIF : sans ca,
    # elle partirait couchee.
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        rgba = image.convert("RGBA")
        fond = Image.new("RGB", rgba.size, FOND)
        fond.paste(rgba, mask=rgba.getchannel("A"))
        image = fond
    return image


def _jpeg_borne(image: Image.Image, poids_max: int) -> bytes:
    for qualite in QUALITES:
        tampon = io.BytesIO()
        image.save(tampon, "JPEG", quality=qualite, optimize=True)
        if tampon.tell() <= poids_max:
            return tampon.getvalue()
    raise ImageInexploitable(f"plus de {poids_max // (1024 * 1024)} Mo meme compressee")


def normaliser(octets: bytes, ratio_min: float = RATIO_MIN) -> bytes:
    image = _ouvrir_rgb(octets)
    largeur, hauteur = image.size

    ratio = largeur / hauteur
    if ratio < ratio_min:
        # int() et pas round() : arrondir vers le haut donnerait une hauteur a
        # peine trop grande, donc un ratio a peine sous le minimum.
        nouvelle_hauteur = int(largeur / ratio_min)
        haut = (hauteur - nouvelle_hauteur) // 2
        image = image.crop((0, haut, largeur, haut + nouvelle_hauteur))
    elif ratio > RATIO_MAX:
        nouvelle_largeur = int(hauteur * RATIO_MAX)
        gauche = (largeur - nouvelle_largeur) // 2
        image = image.crop((gauche, 0, gauche + nouvelle_largeur, hauteur))

    # Verifier la largeur minimale APRES le recadrage de ratio, qui peut
    # l'avoir diminuee.
    if image.width < LARGEUR_MIN:
        raise ImageInexploitable(f"trop petite apres recadrage : {image.width} px de large, minimum {LARGEUR_MIN}")

    if image.width > LARGEUR_MAX:
        nouvelle_hauteur = round(image.height * LARGEUR_MAX / image.width)
        image = image.resize((LARGEUR_MAX, nouvelle_hauteur), Image.LANCZOS)

    return _jpeg_borne(image, POIDS_MAX)


def en_jpeg(octets: bytes, poids_max: int = POIDS_MAX) -> bytes:
    """La cover d'un reel : JPEG RGB de 8 Mo au plus, SANS recadrage.

    Les regles 4:5 a 1,91:1 ne valent que pour les images du fil. Pour un reel,
    Meta prend lui-meme le rectangle 9:16 central (onglet Reels) et le carre
    central (fil) : la cover kinda_reel, deja en 1080x1920, part telle quelle.
    """
    return _jpeg_borne(_ouvrir_rgb(octets), poids_max)
```

Compléter la docstring du module d'une ligne : « `en_jpeg` sert la cover d'un reel, qui échappe aux règles de proportions du fil. »

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_video.py tests/test_publish_images.py tests/test_export.py -v`
Expected: tout passe (les tests `besoin_ffmpeg` sont sautés si ffmpeg est absent — le signaler dans le compte rendu de tâche)

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/content/video.py app/publish/images.py tests/test_video.py tests/test_publish_images.py
git commit -F - <<'EOF'
feat(reel): MP4 aux specs Meta et cover JPEG sans recadrage

Assemblage sans edit list et en GOP ferme ; le sondage rend codecs,
echantillonnage et cadence pour controler la sortie avant publication.
exiger_outils distingue ffmpeg absent d'une video illisible.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---
### Task 7: Le gel d'un reel

**Files:**
- Create: `app/publish/preparation_reel.py`
- Test: `tests/test_publish_preparation_reel.py`

**Interfaces:**
- Consumes: `render_post` (existant), `exiger_outils`, `sonder`, `assembler_reel`, `FORMAT_REEL`, `AssemblageIndisponible` (tâche 6), `en_jpeg`, `ImageInexploitable` (tâche 6), `PreparationImpossible` (existant), `videos_exploitables`, réglages `publication_reel_*` (tâche 3)
- Produces:
  - `url_de_la_video(post, article) -> str | None`
  - `telecharger_video(url: str, plafond: int, *, client: httpx.Client | None = None) -> bytes` — 4xx, trop lourde ou pas une vidéo → `PreparationImpossible` ; réseau ou 5xx → l'exception httpx remonte
  - `verifier_source(infos: dict) -> None`, `verifier_sortie(infos: dict, poids: int) -> None` — `PreparationImpossible` si hors bornes
  - `preparer_reel(post, article, dossier: Path, *, rendre=render_post, telecharger=telecharger_video, sonder=sonder, assembler=assembler_reel, verifier_outils=exiger_outils) -> list[str]` — rend `[<prefixe>_reel.mp4, <prefixe>_cover.jpg]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_publish_preparation_reel.py
"""Le gel d'un reel : cover, video, assemblage, controle des specs Meta."""

import io
import shutil
import subprocess
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app.content.render import RenduSlide
from app.content.video import AssemblageIndisponible
from app.publish.preparation import PreparationImpossible
from app.publish.preparation_reel import preparer_reel, telecharger_video, url_de_la_video

besoin_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent -- assemblage non testable ici")

SOURCE = {"width": 1080, "height": 1920, "duration": 12.0, "has_audio": True, "video_codec": "vp9",
          "audio_codec": "aac", "sample_rate": 44100, "fps": 30.0}
SORTIE = {**SOURCE, "video_codec": "h264", "duration": 12.03}


def _png(largeur=1080, hauteur=1920):
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (10, 20, 30)).save(tampon, "PNG")
    return tampon.getvalue()


def _rendre(overflow=False):
    def rendre(slides, template):
        rendre.appels.append(template)
        return [RenduSlide(png=_png(), scale=1.0, lines={}, overflow=overflow)]

    rendre.appels = []
    return rendre


def _post(template="kinda_reel", credit="@saralandry", *medias):
    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**", "image_url": None}
    return SimpleNamespace(template=template, credit=credit, slides=[cover, *medias])


def _article(*medias):
    return SimpleNamespace(media=list(medias))


VIDEO = {"type": "video", "url": "https://cdn/v.mp4", "preview_url": None, "duration_ms": None,
         "width": None, "height": None}


def _outils(source=SOURCE, sortie=SORTIE):
    def sonder(octets):
        return source if octets == b"source" else sortie

    return {
        "rendre": _rendre(),
        "telecharger": lambda url, plafond: b"source",
        "sonder": sonder,
        "assembler": lambda cover, video: b"reel-mp4",
        "verifier_outils": lambda: None,
    }


def test_un_reel_fige_deux_fichiers_video_puis_cover(tmp_path):
    outils = _outils()
    noms = preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)
    assert [nom.rsplit("_", 1)[1] for nom in noms] == ["reel.mp4", "cover.jpg"]
    assert (tmp_path / noms[0]).read_bytes() == b"reel-mp4"
    cover = Image.open(io.BytesIO((tmp_path / noms[1]).read_bytes()))
    assert (cover.format, cover.size) == ("JPEG", (1080, 1920))
    assert outils["rendre"].appels == ["kinda_reel"]


def test_le_prefixe_ne_se_devine_pas(tmp_path):
    premier = preparer_reel(_post(), _article(VIDEO), tmp_path / "a", **_outils())
    second = preparer_reel(_post(), _article(VIDEO), tmp_path / "b", **_outils())
    assert premier[0] != second[0]


def test_un_gel_repete_efface_les_anciens_fichiers(tmp_path):
    preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils())
    noms = preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils())
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(noms)


def test_la_video_d_une_slide_prime_sur_celle_de_l_article():
    slide = {"kind": "media", "type": "video", "url": "https://cdn/choisie.mp4"}
    assert url_de_la_video(_post("kinda_reel", "@x", slide), _article(VIDEO)) == "https://cdn/choisie.mp4"
    assert url_de_la_video(_post(), _article(VIDEO)) == "https://cdn/v.mp4"
    assert url_de_la_video(_post(), None) is None


@pytest.mark.parametrize(
    "post, article, motif",
    [
        (_post(template="kinda_post"), _article(VIDEO), "gabarit"),
        (_post(credit=None), _article(VIDEO), "credit"),
        (_post(), _article(), "aucune video"),
    ],
)
def test_les_fautes_de_l_article(tmp_path, post, article, motif):
    with pytest.raises(PreparationImpossible, match=motif):
        preparer_reel(post, article, tmp_path, **_outils())


def test_une_cover_qui_deborde_est_une_faute(tmp_path):
    outils = {**_outils(), "rendre": _rendre(overflow=True)}
    with pytest.raises(PreparationImpossible, match="deborde"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)


@pytest.mark.parametrize("duree, motif", [(2.0, "trop courte"), (95.0, "trop longue")])
def test_une_duree_hors_bornes_est_refusee(tmp_path, duree, motif):
    with pytest.raises(PreparationImpossible, match=motif):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils(source={**SOURCE, "duration": duree}))


def test_une_sortie_hors_specs_est_refusee_avant_meta(tmp_path):
    with pytest.raises(PreparationImpossible, match="cadence"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils(sortie={**SORTIE, "fps": 20.0}))


def test_une_video_illisible_est_une_faute(tmp_path):
    outils = _outils()

    def illisible(octets):
        raise AssemblageIndisponible("video illisible : moov atom not found")

    outils["sonder"] = illisible
    with pytest.raises(PreparationImpossible, match="illisible"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)


def test_un_assemblage_trop_long_est_une_faute(tmp_path):
    outils = _outils()

    def trop_long(cover, video):
        raise subprocess.TimeoutExpired("ffmpeg", 300)

    outils["assembler"] = trop_long
    with pytest.raises(PreparationImpossible, match="delai"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)


def test_ffmpeg_absent_est_une_panne_systemique(tmp_path):
    """Pas d'exclusion : la faute est a la machine, pas a l'article."""
    outils = _outils()

    def absent():
        raise AssemblageIndisponible("ffmpeg est introuvable")

    outils["verifier_outils"] = absent
    with pytest.raises(AssemblageIndisponible):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)


# --- Telechargement -------------------------------------------------------------


def _client(gestionnaire):
    return httpx.Client(transport=httpx.MockTransport(gestionnaire))


def test_telecharger_rend_les_octets():
    client = _client(lambda requete: httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"mp4"))
    assert telecharger_video("https://cdn/v.mp4", 1000, client=client) == b"mp4"


def test_un_lien_expire_est_une_faute_de_l_article():
    """Les media_url du CDN Instagram sont signees et expirent en quelques jours."""
    client = _client(lambda requete: httpx.Response(403))
    with pytest.raises(PreparationImpossible, match="403"):
        telecharger_video("https://cdn/v.mp4", 1000, client=client)


def test_une_panne_du_serveur_est_systemique():
    client = _client(lambda requete: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        telecharger_video("https://cdn/v.mp4", 1000, client=client)


def test_une_video_trop_lourde_est_refusee_en_cours_de_lecture():
    client = _client(lambda requete: httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"x" * 2000))
    with pytest.raises(PreparationImpossible, match="trop lourde"):
        telecharger_video("https://cdn/v.mp4", 1000, client=client)


def test_une_page_html_n_est_pas_une_video():
    client = _client(lambda requete: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"))
    with pytest.raises(PreparationImpossible, match="pas une video"):
        telecharger_video("https://cdn/v.mp4", 1000, client=client)


# --- Chaine reelle, ffmpeg compris ------------------------------------------------


@besoin_ffmpeg
def test_la_chaine_complete_avec_ffmpeg(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
        capture_output=True, check=True,
    )
    octets = source.read_bytes()
    noms = preparer_reel(_post(), _article(VIDEO), tmp_path / "fige", rendre=_rendre(),
                         telecharger=lambda url, plafond: octets)
    assert (tmp_path / "fige" / noms[0]).stat().st_size > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_publish_preparation_reel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.publish.preparation_reel'`

- [ ] **Step 3: Implement**

```python
# app/publish/preparation_reel.py
"""Le gel d'un reel : tout ce que la publication enverra, fige sur disque a T-2h.

Meme contrat que `preparation.py` pour les carrousels : une faute de l'ARTICLE
leve `PreparationImpossible` (le runner l'exclut), une panne de la MACHINE
remonte telle quelle (rien n'est exclu, le gel est retente au passage suivant).

La video est telechargee ICI et pas a l'ingestion : les media_url du CDN
Instagram sont signees et expirent en quelques jours. A T-2h, un lien mort est
une exclusion propre, visible au dashboard deux heures avant l'heure.

Ordre des fichiers figes : la video, puis la cover (`cover_url` chez Meta).
"""

import secrets
import subprocess
from pathlib import Path

import httpx

from app.config import settings
from app.content.media import VIDEO, videos_exploitables
from app.content.render import _USER_AGENT, render_post
from app.content.video import FORMAT_REEL, AssemblageIndisponible, assembler_reel, exiger_outils, sonder
from app.publish.images import ImageInexploitable, en_jpeg
from app.publish.preparation import PreparationImpossible

MO = 1024 * 1024
DELAI_TELECHARGEMENT_S = 120.0
CODECS_VIDEO = ("h264", "hevc")
CADENCE_MIN, CADENCE_MAX = 23.0, 60.0
ECHANTILLONNAGE_MAX = 48_000


def url_de_la_video(post, article) -> str | None:
    for slide in post.slides[1:]:
        if slide.get("kind") == "media" and slide.get("type") == VIDEO and slide.get("url"):
            return slide["url"]
    if article is not None:
        videos = videos_exploitables(article.media, int(settings.publication_reel_duree_max_s * 1000))
        if videos:
            return videos[0]["url"]
    return None


def telecharger_video(url: str, plafond: int, *, client: httpx.Client | None = None) -> bytes:
    propre = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=DELAI_TELECHARGEMENT_S)
    try:
        with client.stream("GET", url, headers={"User-Agent": _USER_AGENT}) as reponse:
            if 400 <= reponse.status_code < 500:
                raise PreparationImpossible(
                    f"video indisponible (HTTP {reponse.status_code}) : lien expire ou retire"
                )
            # 5xx : HTTPStatusError remonte, panne systemique retentee.
            reponse.raise_for_status()
            type_ = reponse.headers.get("content-type", "").split(";")[0].strip().lower()
            if type_ and not type_.startswith("video/") and type_ != "application/octet-stream":
                raise PreparationImpossible(f"le lien n'est pas une video ({type_})")
            octets = bytearray()
            for morceau in reponse.iter_bytes():
                octets.extend(morceau)
                # En cours de lecture : Content-Length peut manquer ou mentir.
                if len(octets) > plafond:
                    raise PreparationImpossible(f"video source trop lourde : plus de {plafond / MO:.0f} Mo")
            return bytes(octets)
    finally:
        if propre:
            client.close()


def verifier_source(infos: dict) -> None:
    duree = infos["duration"]
    if duree < settings.publication_reel_duree_min_s:
        raise PreparationImpossible(
            f"video trop courte : {duree:.1f} s, minimum {settings.publication_reel_duree_min_s:.0f} s"
        )
    if duree > settings.publication_reel_duree_max_s:
        # Refusee, jamais coupee : couper le contenu d'un artiste en trahirait le sens.
        raise PreparationImpossible(
            f"video trop longue : {duree:.0f} s, maximum {settings.publication_reel_duree_max_s:.0f} s"
        )


def verifier_sortie(infos: dict, poids: int) -> None:
    """Les specs Meta du 2026-09-17, controlees AVANT la reclamation : un refus
    decouvert chez Meta ferait passer le creneau en ECHEC sans retour."""
    problemes = []
    if (infos["width"], infos["height"]) != FORMAT_REEL:
        problemes.append(f"dimensions {infos['width']}x{infos['height']}")
    if infos.get("video_codec") not in CODECS_VIDEO:
        problemes.append(f"codec video {infos.get('video_codec')}")
    if infos.get("audio_codec") != "aac":
        problemes.append(f"codec audio {infos.get('audio_codec')}")
    if (infos.get("sample_rate") or 0) > ECHANTILLONNAGE_MAX:
        problemes.append(f"echantillonnage {infos.get('sample_rate')} Hz")
    if not CADENCE_MIN <= infos.get("fps", 0.0) <= CADENCE_MAX:
        problemes.append(f"cadence {infos.get('fps')} i/s")
    if infos["duration"] < settings.publication_reel_duree_min_s:
        problemes.append(f"duree {infos['duration']:.1f} s")
    if poids > settings.publication_reel_max_mo * MO:
        problemes.append(f"poids {poids / MO:.0f} Mo")
    if problemes:
        raise PreparationImpossible("reel hors specifications Meta : " + ", ".join(problemes))


def preparer_reel(
    post,
    article,
    dossier: Path,
    *,
    rendre=render_post,
    telecharger=telecharger_video,
    sonder=sonder,
    assembler=assembler_reel,
    verifier_outils=exiger_outils,
) -> list[str]:
    if post.template != "kinda_reel":
        raise PreparationImpossible(f"le post est au gabarit {post.template}, pas kinda_reel")
    if not getattr(post, "credit", None):
        raise PreparationImpossible("credit du compte source manquant : un reel ne part jamais sans")
    url = url_de_la_video(post, article)
    if url is None:
        raise PreparationImpossible("aucune video exploitable")
    # AssemblageIndisponible ICI = ffmpeg ou ffprobe absent : panne de la
    # machine, elle remonte telle quelle et n'exclut rien.
    verifier_outils()

    dossier.mkdir(parents=True, exist_ok=True)
    for ancien in [*dossier.glob("*.mp4"), *dossier.glob("*.jpg")]:
        ancien.unlink()

    # RenduIndisponible (Chromium absent) remonte aussi telle quelle.
    rendus = rendre(post.slides, "kinda_reel")
    if not rendus:
        raise PreparationImpossible("la cover n'a pas pu etre rendue")
    if rendus[0].overflow:
        raise PreparationImpossible("accroche trop longue : la cover deborde")

    source = telecharger(url, settings.publication_reel_source_max_mo * MO)
    try:
        verifier_source(sonder(source))
        reel = assembler(rendus[0].png, source)
        infos = sonder(reel)
    # Les outils sont presents (verifie plus haut) : un echec ici vient de l'entree.
    except AssemblageIndisponible as exc:
        raise PreparationImpossible(f"video inexploitable : {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PreparationImpossible(f"assemblage abandonne : delai de {exc.timeout:.0f} s depasse") from exc
    verifier_sortie(infos, len(reel))

    try:
        cover = en_jpeg(rendus[0].png)
    except ImageInexploitable as exc:
        raise PreparationImpossible(f"cover inexploitable : {exc}") from exc

    # Prefixe aleatoire : les fichiers seront publics quelques minutes.
    prefixe = secrets.token_urlsafe(12)
    noms = [f"{prefixe}_reel.mp4", f"{prefixe}_cover.jpg"]
    (dossier / noms[0]).write_bytes(reel)
    (dossier / noms[1]).write_bytes(cover)
    return noms
```

Note : `secrets.token_urlsafe` peut contenir `_` ; le test `test_un_reel_fige_deux_fichiers_video_puis_cover` coupe donc sur le DERNIER `_` (`rsplit("_", 1)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_publish_preparation_reel.py -v`
Expected: tout passe (le test `besoin_ffmpeg` est sauté si ffmpeg est absent)

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add app/publish/preparation_reel.py tests/test_publish_preparation_reel.py
git commit -F - <<'EOF'
feat(publication): gel d'un reel

Cover kinda_reel, telechargement de la video, assemblage ffmpeg, sondage
de la source et de la sortie contre les specs Meta. Faute de l'article et
panne de la machine restent distinguees.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 8: Le conteneur REELS chez Meta

**Files:**
- Modify: `app/publish/instagram.py`
- Test: `tests/test_publish_instagram.py`

**Interfaces:**
- Consumes: `ClientInstagram` (existant)
- Produces:
  - `ATTENTE_REEL_PAS_S = 15.0`, `ATTENTE_REEL_MAX_S = 600.0`
  - `ClientInstagram._attendre(conteneur_id: str, pas: float | None = None, maximum: float | None = None) -> None` — `None` = constantes carrousel, lues à l'appel
  - `ClientInstagram.preparer_reel(video_url: str, cover_url: str, legende: str, *, share_to_feed: bool = True) -> str` — **ne publie pas**

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_publish_instagram.py` :

```python
def test_un_reel_donne_un_conteneur_reels_avec_cover_explicite():
    http = FauxHttp()
    conteneur = _client(http).preparer_reel("https://t/r.mp4", "https://t/c.jpg", "Legende")
    assert conteneur == "c1"
    methode, chemin, params = http.appels[0]
    assert (methode, chemin) == ("POST", "17841/media")
    assert params["media_type"] == "REELS"
    assert (params["video_url"], params["cover_url"]) == ("https://t/r.mp4", "https://t/c.jpg")
    assert (params["caption"], params["share_to_feed"]) == ("Legende", "true")
    assert "thumb_offset" not in params
    assert not any(c.endswith("media_publish") for _, c, _ in http.appels), "preparer ne publie pas"


def test_share_to_feed_peut_etre_coupe():
    http = FauxHttp()
    _client(http).preparer_reel("https://t/r.mp4", "https://t/c.jpg", "L", share_to_feed=False)
    assert http.appels[0][2]["share_to_feed"] == "false"


def test_un_reel_se_sonde_toutes_les_15_secondes():
    """L'encodage d'une video est plus long que celui d'une image."""
    pauses = []
    http = FauxHttp(statuts=("IN_PROGRESS", "IN_PROGRESS", "FINISHED"))
    ClientInstagram("j", "17841", http=http, version="v23.0", dormir=pauses.append).preparer_reel(
        "https://t/r.mp4", "https://t/c.jpg", "L"
    )
    assert pauses == [15.0, 15.0]


def test_un_reel_qui_ne_finit_jamais_leve_apres_le_delai_reel(monkeypatch):
    monkeypatch.setattr(mod, "ATTENTE_REEL_MAX_S", 30.0)
    pauses = []
    client = ClientInstagram("j", "17841", http=FauxHttp(statuts=("IN_PROGRESS",)), version="v23.0",
                             dormir=pauses.append)
    with pytest.raises(ErreurInstagram, match="IN_PROGRESS"):
        client.preparer_reel("https://t/r.mp4", "https://t/c.jpg", "L")
    assert len(pauses) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_publish_instagram.py -v`
Expected: FAIL — `AttributeError: 'ClientInstagram' object has no attribute 'preparer_reel'`

- [ ] **Step 3: Implement**

Dans `app/publish/instagram.py`, après `ATTENTE_MAX_S` :

```python
# Un reel s'encode plus longtemps qu'une image. Meta recommande une lecture par
# minute pendant 5 minutes au plus ; on sonde plus serre et plus longtemps parce
# qu'a ce stade le creneau est deja reclame : un delai trop court le ferait
# passer en ECHEC sans retour. 40 lectures au pire.
ATTENTE_REEL_PAS_S = 15.0
ATTENTE_REEL_MAX_S = 600.0
```

Remplacer `_attendre` par (les constantes sont lues À L'APPEL, pour que `monkeypatch` les atteigne) :

```python
    def _attendre(self, conteneur_id: str, pas: float | None = None, maximum: float | None = None) -> None:
        pas = ATTENTE_PAS_S if pas is None else pas
        maximum = ATTENTE_MAX_S if maximum is None else maximum
        ecoule = 0.0
        while True:
            statut = self._appel("GET", conteneur_id, {"fields": "status_code,status"})
            code = statut.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise ErreurInstagram(f"conteneur {conteneur_id} en {code} : {statut.get('status', '')}")
            if ecoule >= maximum:
                raise ErreurInstagram(f"conteneur {conteneur_id} toujours {code} apres {maximum:.0f} s")
            self.dormir(pas)
            ecoule += pas
```

Après `preparer_carrousel` :

```python
    def preparer_reel(self, video_url: str, cover_url: str, legende: str, *, share_to_feed: bool = True) -> str:
        # cover_url explicite plutot que thumb_offset : la cover est deja la
        # premiere frame, mais Instagram reencode et peut la perdre. Meta retient
        # cover_url quand les deux sont fournis.
        conteneur = self._conteneur(
            media_type="REELS",
            video_url=video_url,
            cover_url=cover_url,
            caption=legende,
            share_to_feed="true" if share_to_feed else "false",
        )
        self._attendre(conteneur, pas=ATTENTE_REEL_PAS_S, maximum=ATTENTE_REEL_MAX_S)
        return conteneur
```

Compléter la docstring du module : « `preparer_reel` suit le même contrat pour un conteneur `REELS`. »

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_publish_instagram.py -v`
Expected: tout passe, y compris `test_un_conteneur_qui_ne_finit_jamais_leve` (constante lue à l'appel)

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add app/publish/instagram.py tests/test_publish_instagram.py
git commit -F - <<'EOF'
feat(instagram): conteneur REELS avec cover explicite

video_url, cover_url, legende et share_to_feed ; sondage toutes les 15 s
pendant 10 minutes au plus. Ne publie pas.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 9: Publier un reel figé

**Files:**
- Modify: `app/publish/publier.py`
- Test: `tests/test_publish_publier.py`

**Interfaces:**
- Consumes: `ClientInstagram.preparer_reel` (tâche 8), `hosting.heberger` (existant), `settings.publication_reel_share_to_feed` (tâche 3)
- Produces:
  - `publier_reel(dossier: Path, noms: list[str], legende: str, publier_vraiment: bool, *, client=None, heberger=None) -> str` — identifiant du conteneur en aperçu, du média publié sinon

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_publish_publier.py` (et `publier_reel` à l'import) :

```python
class FauxClientReel(FauxClient):
    def preparer_reel(self, video_url, cover_url, legende, *, share_to_feed=True):
        self.journal.append(("preparer_reel", video_url, cover_url, legende, share_to_feed))
        return "conteneur-reel"


def test_un_reel_en_apercu_s_arrete_au_conteneur_et_prend_la_cover_pour_temoin(tmp_path):
    """Le temoin est telecharge en entier pour verifier le tunnel : pas la video."""
    journal = []
    noms = _fichiers(tmp_path, "r_reel.mp4", "r_cover.jpg")
    resultat = publier_reel(tmp_path, noms, "L", False, client=FauxClientReel(journal), heberger=_heberger(journal))
    assert resultat == "conteneur-reel"
    assert journal == [
        ("ouvre", "r_cover.jpg"),
        ("preparer_reel", "https://t.trycloudflare.com/r_reel.mp4", "https://t.trycloudflare.com/r_cover.jpg", "L", True),
        ("ferme",),
    ]


def test_un_reel_en_mode_reel_est_publie_avant_la_fermeture_du_tunnel(tmp_path):
    journal = []
    noms = _fichiers(tmp_path, "r_reel.mp4", "r_cover.jpg")
    assert publier_reel(tmp_path, noms, "L", True, client=FauxClientReel(journal), heberger=_heberger(journal)) == "media1"
    assert [e[0] for e in journal] == ["ouvre", "preparer_reel", "publier", "ferme"]


def test_un_reel_exige_exactement_video_et_cover(tmp_path):
    noms = _fichiers(tmp_path, "r_reel.mp4")
    with pytest.raises(ErreurInstagram, match="deux fichiers"):
        publier_reel(tmp_path, noms, "L", False, client=FauxClientReel([]), heberger=_heberger([]))


def test_un_reel_dont_un_fichier_manque_n_ouvre_pas_le_tunnel(tmp_path):
    journal = []
    _fichiers(tmp_path, "r_reel.mp4")
    with pytest.raises(ErreurInstagram, match="introuvables"):
        publier_reel(tmp_path, ["r_reel.mp4", "r_cover.jpg"], "L", False,
                     client=FauxClientReel(journal), heberger=_heberger(journal))
    assert journal == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_publish_publier.py -v`
Expected: FAIL — `ImportError: cannot import name 'publier_reel'`

- [ ] **Step 3: Implement**

Dans `app/publish/publier.py`, extraire la vérification des fichiers et ajouter `publier_reel` :

```python
def _verifier_fichiers(dossier: Path, noms: list[str]) -> None:
    if not noms:
        raise ErreurInstagram("rien a publier : aucun fichier fige")
    manquants = [nom for nom in noms if not (dossier / nom).exists()]
    if manquants:
        raise ErreurInstagram(f"fichiers figes introuvables : {', '.join(manquants)}")
```

Dans `publier_fichiers`, remplacer les deux vérifications en tête par `_verifier_fichiers(dossier, noms)`. Puis :

```python
def publier_reel(
    dossier: Path,
    noms: list[str],
    legende: str,
    publier_vraiment: bool,
    *,
    client=None,
    heberger=None,
) -> str:
    """Un reel fige : la video puis la cover, dans cet ordre (preparation_reel)."""
    if len(noms) != 2:
        raise ErreurInstagram(f"un reel fige compte deux fichiers (video, cover), pas {len(noms)}")
    _verifier_fichiers(dossier, noms)
    video, cover = noms
    client = client or client_par_defaut()
    heberger = heberger or hosting.heberger
    # Temoin = la cover : `attendre_joignable` la telecharge en entier pour
    # verifier le tunnel, inutile d'y faire transiter la video.
    with heberger(dossier, cover) as base:
        conteneur = client.preparer_reel(
            f"{base}/{video}",
            f"{base}/{cover}",
            legende,
            share_to_feed=settings.publication_reel_share_to_feed,
        )
        if not publier_vraiment:
            return conteneur
        return client.publier(conteneur)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_publish_publier.py -v`
Expected: tout passe

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add app/publish/publier.py tests/test_publish_publier.py
git commit -F - <<'EOF'
feat(publication): publier un reel fige derriere le tunnel ephemere

Video et cover servies le temps de la publication, la cover sert de temoin
de joignabilite ; media_publish seulement en mode reel.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

## Partie C — Brancher les reels dans la file

### Task 10: Le runner, format par format

**Files:**
- Modify: `app/schedule/runner.py`
- Test: `tests/test_schedule_runner_reels.py`

**Interfaces:**
- Consumes: `FormatCreneau` (tâche 2), `TEMPLATES`, `raison_ineligible_reel`, `post_a_reprendre(..., template)` (tâche 5), `create_post(..., template=)` (existant), `AssemblageIndisponible` (existant)
- Produces:
  - `tourner(db, maintenant, *, mode, composer, preparer, publier, notifier, horloge=None, preparer_reel=None, publier_reel=None) -> RapportPublication`
  - `_menage(db) -> None` — supprime les sous-dossiers numériques de `publication_dir` dont le créneau n'est plus `GELE`
  - `_geler(db, maintenant, preparateurs: dict, notifier, rapport) -> bool`
  - `_publier(db, maintenant, mode, publieurs: dict, notifier, rapport, horloge) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schedule_runner_reels.py
"""Le passage de l'agent avec des creneaux reel, sur des outils factices."""

import datetime as dt

import pytest

from app.config import settings
from app.content.compose import CoverVariante, PostDraft
from app.content.video import AssemblageIndisponible
from app.models import (
    Article, Creneau, EtatCreneau, FormatCreneau, Post, Source, SourceCategorie, SourceType,
)
from app.schedule.runner import tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import utc

BASE = dt.datetime(2026, 9, 20, 4, 0, tzinfo=dt.timezone.utc)
(R0,) = prochains_horaires(BASE, 1, "reel")


def _draft():
    variante = CoverVariante(headline="**Accroche**", title="**Titre**", subtitle="**Sous**")
    return PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=["#techno"])


class Outils:
    def __init__(self):
        self.composes, self.prepares, self.prepares_reel = [], [], []
        self.publies, self.publies_reel, self.legendes, self.notifications = [], [], [], []
        self.erreur_preparation_reel = None

    def composer(self, article):
        self.composes.append(article.id)
        return _draft()

    def preparer(self, post, article, dossier):
        self.prepares.append(post.id)
        return ["p_01.jpg"]

    def preparer_reel(self, post, article, dossier):
        self.prepares_reel.append(post.id)
        if self.erreur_preparation_reel:
            raise self.erreur_preparation_reel
        return ["r_reel.mp4", "r_cover.jpg"]

    def publier(self, dossier, noms, legende, publier_vraiment):
        self.publies.append((noms, publier_vraiment))
        return "ig42"

    def publier_reel(self, dossier, noms, legende, publier_vraiment):
        self.publies_reel.append((noms, publier_vraiment))
        self.legendes.append(legende)
        return "ig-reel"

    def notifier(self, titre, message):
        self.notifications.append(message)

    def tourner(self, db, maintenant, mode="reel", avec_reels=True):
        return tourner(
            db, maintenant, mode=mode, composer=self.composer, preparer=self.preparer,
            publier=self.publier, notifier=self.notifier, horloge=lambda: maintenant,
            preparer_reel=self.preparer_reel if avec_reels else None,
            publier_reel=self.publier_reel if avec_reels else None,
        )


@pytest.fixture()
def video(db_session):
    source = Source(name="Sara Landry", type=SourceType.INSTAGRAM, url="ig:saralandry",
                    categorie=SourceCategorie.ARTISTE)
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://www.instagram.com/p/AAA/", title="Le live",
                      raw_hash="v1", relevance_score=90, hotness_score=40,
                      published_at=BASE - dt.timedelta(hours=1),
                      media=[{"type": "video", "url": "https://cdn/v.mp4", "preview_url": "https://cdn/v.jpg"}])
    db_session.add(article)
    db_session.commit()
    return article


def _photo(db):
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed", categorie=SourceCategorie.MEDIA)
    db.add(source)
    db.commit()
    article = Article(source_id=source.id, url="https://t/1", title="Actu", raw_hash="p1", relevance_score=80,
                      hotness_score=20, published_at=BASE - dt.timedelta(hours=1),
                      media=[{"type": "photo", "url": "https://img/1.jpg"}])
    db.add(article)
    db.commit()
    return article


def _post(db, article, template, credit=None):
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                caption="Legende", slides=[{"kind": "cover"}], template=template, credit=credit)
    db.add(post)
    db.commit()
    return post


def _epingle(db, heure, format_, article, post, etat=EtatCreneau.PROGRAMME, fichiers=None):
    creneau = Creneau(publier_a=heure, format=format_, article_id=article.id, post_id=post.id,
                      etat=etat, epingle=True, fichiers=fichiers)
    db.add(creneau)
    db.commit()
    return creneau


def test_la_chaine_complete_d_un_reel(db_session, video):
    outils = Outils()
    outils.tourner(db_session, R0 - dt.timedelta(hours=3))
    creneau = next(c for c in db_session.query(Creneau).all() if utc(c.publier_a) == R0)
    assert (creneau.format, creneau.article_id) == (FormatCreneau.REEL, video.id)
    post = db_session.get(Post, creneau.post_id)
    assert (post.template, post.credit) == ("kinda_reel", "@saralandry")
    carrousels = [c for c in db_session.query(Creneau).all() if c.format == FormatCreneau.CARROUSEL]
    assert all(c.article_id != video.id for c in carrousels)

    outils.tourner(db_session, R0 - dt.timedelta(hours=1))
    assert (creneau.etat, creneau.fichiers) == (EtatCreneau.GELE, ["r_reel.mp4", "r_cover.jpg"])
    assert (outils.prepares_reel, outils.prepares) == ([post.id], [])

    rapport = outils.tourner(db_session, R0 + dt.timedelta(minutes=20))
    assert outils.publies_reel == [(["r_reel.mp4", "r_cover.jpg"], True)]
    assert (creneau.etat, creneau.ig_media_id, rapport.publies) == (EtatCreneau.PUBLIE, "ig-reel", 1)
    assert "🎥 via @saralandry" in outils.legendes[0]


def test_un_reel_en_apercu_n_est_pas_publie(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    creneau = _epingle(db_session, BASE - dt.timedelta(minutes=10), FormatCreneau.REEL, video, post,
                       etat=EtatCreneau.GELE, fichiers=["r_reel.mp4", "r_cover.jpg"])
    outils = Outils()
    outils.tourner(db_session, BASE, mode="apercu")
    assert outils.publies_reel == [(["r_reel.mp4", "r_cover.jpg"], False)]
    assert creneau.etat == EtatCreneau.APERCU


def test_ffmpeg_absent_n_exclut_rien_et_n_empeche_pas_le_gel_des_carrousels(db_session, video):
    photo = _photo(db_session)
    carrousel = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.CARROUSEL, photo,
                         _post(db_session, photo, "kinda_post"))
    reel = _epingle(db_session, BASE + dt.timedelta(hours=1, minutes=30), FormatCreneau.REEL, video,
                    _post(db_session, video, "kinda_reel", "@saralandry"))
    outils = Outils()
    outils.erreur_preparation_reel = AssemblageIndisponible("ffmpeg est introuvable")
    outils.tourner(db_session, BASE)
    assert carrousel.etat == EtatCreneau.GELE
    assert reel.etat == EtatCreneau.PROGRAMME and "ffmpeg" in reel.erreur
    db_session.refresh(video)
    assert video.file_exclu_raison is None
    assert len(outils.notifications) == 1


def test_un_post_au_mauvais_gabarit_n_est_pas_gele_ni_exclu(db_session, video):
    reel = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.REEL, video,
                    _post(db_session, video, "kinda_post", "@saralandry"))
    outils = Outils()
    outils.tourner(db_session, BASE)
    assert reel.etat == EtatCreneau.PROGRAMME and "gabarit" in reel.erreur
    assert outils.prepares_reel == []
    db_session.refresh(video)
    assert video.file_exclu_raison is None


def test_sans_outils_reel_un_reel_gele_attend_sans_planter(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    creneau = _epingle(db_session, BASE - dt.timedelta(minutes=10), FormatCreneau.REEL, video, post,
                       etat=EtatCreneau.GELE, fichiers=["r_reel.mp4", "r_cover.jpg"])
    outils = Outils()
    outils.tourner(db_session, BASE, avec_reels=False)
    assert creneau.etat == EtatCreneau.GELE and "publieur" in creneau.erreur


def test_le_menage_efface_les_dossiers_des_creneaux_termines(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    termine = _epingle(db_session, BASE - dt.timedelta(hours=5), FormatCreneau.REEL, video, post,
                       etat=EtatCreneau.ECHEC)
    gele = _epingle(db_session, BASE + dt.timedelta(hours=5), FormatCreneau.REEL, video, post,
                    etat=EtatCreneau.GELE, fichiers=["r_reel.mp4", "r_cover.jpg"])
    for dossier in (str(termine.id), str(gele.id), "essais", "999999"):
        (settings.publication_dir / dossier).mkdir(parents=True)
    Outils().tourner(db_session, BASE, mode="apercu")
    restants = sorted(p.name for p in settings.publication_dir.iterdir())
    assert restants == sorted([str(gele.id), "essais"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_schedule_runner_reels.py -v`
Expected: FAIL — `TypeError: tourner() got an unexpected keyword argument 'preparer_reel'`

- [ ] **Step 3: Implement**

Dans `app/schedule/runner.py` :

Imports :

```python
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau, Post, PostStatus
from app.schedule.store import TEMPLATES, appliquer_arbitrage, post_a_reprendre, raison_ineligible_reel, utc
```

Compléter la docstring du module, après le paragraphe « Chaque etape est isolee » :

```
Deux formats, carrousel et reel, suivent le meme cycle. Seuls changent le
gabarit compose, le preparateur du gel et le publieur, choisis selon
`creneau.format`. Une panne systemique ne suspend que son format : ffmpeg
absent ne bloque pas le gel d'un carrousel.
```

Après `_effacer_dossier`, ajouter :

```python
def _menage(db) -> None:
    """Les gros fichiers d'un creneau termine. Les chemins nominaux effacent deja
    leur dossier ; le menage rattrape un crash apres reclamation ou un gel
    interrompu, qui laisseraient des dizaines de Mo de video sur le disque.
    `essais/` (script d'essai) n'est pas un nombre : jamais touche."""
    racine = settings.publication_dir
    if not racine.is_dir():
        return
    for dossier in racine.iterdir():
        if not (dossier.is_dir() and dossier.name.isdigit()):
            continue
        creneau = db.get(Creneau, int(dossier.name))
        if creneau is None or creneau.etat != EtatCreneau.GELE:
            shutil.rmtree(dossier, ignore_errors=True)
```

Dans `_composer`, remplacer le bloc qui va de `post_id = post_a_reprendre(db, article.id)` jusqu'à `post_id = create_post(db, article, draft).id` par :

```python
        template = TEMPLATES[creneau.format]
        if creneau.format == FormatCreneau.REEL:
            raison = raison_ineligible_reel(article)
            if raison is not None:
                # Pas d'exclusion : l'article reste bon pour un carrousel.
                _liberer(db, creneau, attendu, f"reel impossible : {raison}")
                continue
        post_id = post_a_reprendre(db, article.id, template)
        if post_id is None:
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
            post_id = create_post(db, article, draft, template=template).id
```

Remplacer `_geler` par :

```python
def _geler(db, maintenant: dt.datetime, preparateurs: dict, notifier, rapport: RapportPublication) -> bool:
    seuil = maintenant + dt.timedelta(hours=settings.publication_gel_heures)
    creneaux = db.scalars(
        select(Creneau)
        .where(
            Creneau.etat == EtatCreneau.PROGRAMME,
            Creneau.post_id.is_not(None),
            Creneau.publier_a <= seuil,
        )
        .order_by(Creneau.publier_a)
    ).all()
    exclusions = False
    formats_en_panne: set = set()
    for creneau in creneaux:
        if not _relire(db, creneau) or creneau.etat != EtatCreneau.PROGRAMME or creneau.post_id is None:
            continue
        if creneau.format in formats_en_panne:
            continue
        attendu = _attendu(creneau)
        preparer = preparateurs.get(creneau.format)
        if preparer is None:
            erreur = f"aucun preparateur pour le format {creneau.format.value}"
            if creneau.erreur != erreur:
                _ecrire_si(db, creneau, attendu, erreur=erreur)
            continue
        post = db.get(Post, creneau.post_id, populate_existing=True)
        if post is None:
            _liberer(db, creneau, attendu, "post supprime")
            continue
        if post.article_id != creneau.article_id:
            # Pas d'exclusion : l'article n'y est pour rien, c'est la file qui a bouge.
            erreur = "gel refuse : le post ne correspond pas a l'article du creneau"
            rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : {erreur}")
            _ecrire_si(db, creneau, attendu, erreur=erreur)
            continue
        if post.template != TEMPLATES[creneau.format]:
            # Le switch POST/REELS a ete bascule au dashboard : pas la faute de l'article.
            erreur = (
                f"gel refuse : le post est au gabarit {post.template}, "
                f"le creneau attend {TEMPLATES[creneau.format]}"
            )
            rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : {erreur}")
            _ecrire_si(db, creneau, attendu, erreur=erreur)
            continue
        article = db.get(Article, post.article_id, populate_existing=True) if post.article_id else None
        if _deja_publie(article, post):
            _liberer(db, creneau, attendu, "article deja publie")
            continue
        try:
            noms = preparer(post, article, _dossier(creneau))
        except PreparationImpossible as exc:
            _exclure(db, creneau, attendu, f"gel impossible : {exc}", rapport)
            exclusions = True
            continue
        except Exception as exc:  # noqa: BLE001 -- rendu, ffmpeg, disque, reseau : panne systemique
            # Le creneau reste PROGRAMME : retente au passage suivant, tant que le
            # delai de retard le permet. Seul son format est suspendu.
            erreur = f"gel interrompu : {exc}"[:1000]
            rapport.erreurs.append(erreur)
            _ecrire_si(db, creneau, attendu, erreur=erreur)
            _prevenir(notifier, erreur)
            formats_en_panne.add(creneau.format)
            continue
        if _ecrire_si(
            db, creneau, attendu,
            fichiers=noms, etat=EtatCreneau.GELE, gele_le=maintenant, erreur=None,
        ):
            rapport.geles += 1
    return exclusions
```

Dans `_publier`, changer la signature en
`def _publier(db, maintenant: dt.datetime, mode: str, publieurs: dict, notifier, rapport: RapportPublication, horloge) -> None:`
et insérer, juste APRÈS le bloc du garde-fou de retard (`if retard > ...: ... continue`) :

```python
        publier = publieurs.get(creneau.format)
        if publier is None:
            # Il reste GELE : il finira en MANQUE si aucun publieur n'arrive a temps.
            erreur = f"aucun publieur pour le format {creneau.format.value}"
            if creneau.erreur != erreur:
                _ecrire_si(db, creneau, attendu, erreur=erreur)
            continue
```

Remplacer `tourner` par :

```python
def tourner(
    db,
    maintenant: dt.datetime,
    *,
    mode: str,
    composer,
    preparer,
    publier,
    notifier,
    horloge=None,
    preparer_reel=None,
    publier_reel=None,
) -> RapportPublication:
    if mode not in MODES:
        raise ValueError(f"mode de publication inconnu : {mode!r}, attendu {MODES}")
    rapport = RapportPublication()
    if mode == "arret":
        return rapport
    horloge = horloge or _maintenant_reel
    preparateurs = {FormatCreneau.CARROUSEL: preparer}
    publieurs = {FormatCreneau.CARROUSEL: publier}
    if preparer_reel is not None:
        preparateurs[FormatCreneau.REEL] = preparer_reel
    if publier_reel is not None:
        publieurs[FormatCreneau.REEL] = publier_reel

    def etape(nom: str, fonction):
        # Une etape en panne ne doit pas emporter les suivantes, et surtout pas
        # la publication d'un creneau deja gele.
        try:
            # Relire la base a chaque etape : la precedente a pu durer des minutes
            # (publication), et le dashboard a pu epingler ou remplacer entre-temps.
            db.expire_all()
            return fonction()
        except Exception as exc:  # noqa: BLE001
            logger.exception("etape %s en panne", nom)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                logger.exception("rollback impossible apres l'etape %s", nom)
            message = f"etape {nom} en panne : {exc}"[:1000]
            rapport.erreurs.append(message)
            _prevenir(notifier, message)
            return None

    def publication():
        _publier(db, maintenant, mode, publieurs, notifier, rapport, horloge)

    etape("retards", lambda: _marquer_manques(db, maintenant, rapport))
    etape("menage", lambda: _menage(db))
    etape("publication", publication)
    etape("arbitrage", lambda: appliquer_arbitrage(db, maintenant))
    exclusions = bool(etape("composition", lambda: _composer(db, maintenant, composer, notifier, rapport)))
    exclusions = bool(etape("gel", lambda: _geler(db, maintenant, preparateurs, notifier, rapport))) or exclusions
    if exclusions:
        # Le creneau libere se remplit tout de suite ; sa composition viendra au
        # passage suivant.
        etape("arbitrage", lambda: appliquer_arbitrage(db, maintenant))
    etape("publication", publication)
    if rapport.manques:
        _prevenir(
            notifier,
            f"{rapport.manques} publication(s) manquee(s) : plus de "
            f"{settings.publication_retard_max_minutes} minutes de retard (Mac en veille ?)",
        )
    return rapport
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_schedule_runner_reels.py tests/test_schedule_runner.py -v`
Expected: tout passe. Si `test_la_chaine_complete_d_un_reel` échoue parce qu'un créneau carrousel du matin est aussi dans la fenêtre de gel, vérifier qu'aucun carrousel n'a d'article (le seul article est une vidéo placée en reel) — c'est l'invariant testé, pas un réglage à contourner.

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add app/schedule/runner.py tests/test_schedule_runner_reels.py
git commit -F - <<'EOF'
feat(file): le runner publie aussi des reels

Gabarit, preparateur et publieur choisis par format ; une panne systemique
ne suspend que son format ; un post au mauvais gabarit n'est pas gele ;
menage des dossiers des creneaux termines.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 11: Brancher l'agent et le script d'essai

**Files:**
- Modify: `scripts/run_publication.py`
- Modify: `scripts/tester_publication.py`
- Test: `tests/test_run_publication.py`

**Interfaces:**
- Consumes: `preparer_reel` (tâche 7), `publier_reel` (tâche 9), `tourner(..., preparer_reel=, publier_reel=)` (tâche 10)
- Produces: l'agent passe les outils reel au runner ; `tester_publication --post <ID>` essaie un post `kinda_reel` sans publier

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_run_publication.py` :

```python
def test_l_agent_branche_les_outils_des_reels(tmp_path, monkeypatch):
    recu = {}

    def passage(*args, **kwargs):
        recu.update(kwargs)
        return script.RapportPublication()

    _agent_en_panne(tmp_path, monkeypatch, passage=passage)
    script.main()
    assert recu["preparer_reel"] is script.preparer_reel
    assert recu["publier_reel"] is script.publier_reel
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_run_publication.py -v`
Expected: FAIL — `AttributeError: module 'scripts.run_publication' has no attribute 'preparer_reel'`

- [ ] **Step 3: Implement**

Dans `scripts/run_publication.py`, importer :

```python
from app.publish.preparation_reel import preparer_reel
from app.publish.publier import publier_fichiers, publier_reel
```

et compléter l'appel à `tourner` :

```python
            rapport = tourner(
                db, maintenant, mode=mode, composer=_compositeur_paresseux(),
                preparer=preparer_fichiers, publier=publier_fichiers, notifier=notifier,
                preparer_reel=preparer_reel, publier_reel=publier_reel,
            )
```

Dans `scripts/tester_publication.py`, importer `preparer_reel` et `publier_reel`, mettre à jour la docstring (« un post `kinda_reel` est assemblé en MP4 et envoyé en conteneur REELS »), et remplacer les quatre lignes qui suivent `dossier = ...` par :

```python
        legende = texte_legende(post)
        if post.template == "kinda_reel":
            noms = preparer_reel(post, article, dossier)
            print(f"reel assemble dans {dossier} : {', '.join(noms)}")
            conteneur = publier_reel(dossier, noms, legende, publier_vraiment=False)
        else:
            noms = preparer_fichiers(post, article, dossier)
            print(f"{len(noms)} image(s) figee(s) dans {dossier}")
            conteneur = publier_fichiers(dossier, noms, legende, publier_vraiment=False)
        print(f"conteneur {conteneur} FINISHED chez Meta -- rien n'a ete publie")
```

**Ne pas lancer ce script** : il crée un conteneur chez Meta. C'est une étape de mise en service de l'utilisateur.

- [ ] **Step 4: Run tests et import du script**

Run: `uv run pytest tests/test_run_publication.py -v && uv run python -c "import scripts.tester_publication"`
Expected: tout passe, l'import ne lève rien

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add scripts/run_publication.py scripts/tester_publication.py tests/test_run_publication.py
git commit -F - <<'EOF'
feat(agent): brancher le gel et la publication des reels

L'agent de publication passe les outils reel au runner ; le script d'essai
assemble un post kinda_reel et cree son conteneur sans publier.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 12: L'API de la file, format compris

**Files:**
- Modify: `app/schemas.py`
- Modify: `app/api/schedule.py`
- Test: `tests/test_schedule_api_reels.py`

**Interfaces:**
- Consumes: `FormatCreneau` (tâche 2), `TEMPLATES`, `raison_ineligible_reel`, `offre_reels`, `post_a_reprendre(..., template)` (tâche 5)
- Produces:
  - `CreneauOut.format: FormatCreneau`
  - `OffreReelsOut(avec_video: int, eligibles: int, fraicheur_jours: int)`
  - `GET /schedule/offre-reels` → `OffreReelsOut`
  - `PATCH /schedule/{id}` : remplacer sur un reel refuse un article inéligible (409, `detail` commençant par « Article inutilisable en reel : ») ; décaler reste dans le format

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schedule_api_reels.py
import datetime as dt

from app.content.media import entree
from app.models import Article, Creneau, EtatCreneau, FormatCreneau, Source, SourceCategorie, SourceType


def _maintenant():
    return dt.datetime.now(dt.timezone.utc)


def _article(db, n, video=True, categorie=SourceCategorie.ARTISTE):
    source = Source(name=f"compte{n}", type=SourceType.INSTAGRAM, url=f"ig:compte{n}", categorie=categorie)
    db.add(source)
    db.commit()
    media = [entree("video", f"https://cdn/{n}.mp4")] if video else [entree("photo", f"https://img/{n}.jpg")]
    article = Article(source_id=source.id, url=f"https://www.instagram.com/p/{n}/", title=f"Actu {n}",
                      raw_hash=f"h{n}", relevance_score=80, published_at=_maintenant(), media=media)
    db.add(article)
    db.commit()
    return article


def _creneau(db, heures, format_, article=None):
    creneau = Creneau(publier_a=_maintenant() + dt.timedelta(hours=heures), format=format_,
                      article_id=article.id if article else None,
                      etat=EtatCreneau.PROGRAMME if article else EtatCreneau.LIBRE)
    db.add(creneau)
    db.commit()
    return creneau


def test_la_file_montre_le_format(client, db_session):
    _creneau(db_session, 5, FormatCreneau.REEL)
    _creneau(db_session, 6, FormatCreneau.CARROUSEL)
    assert [v["format"] for v in client.get("/schedule").json()] == ["reel", "carrousel"]


def test_remplacer_un_reel_par_un_article_sans_video_est_refuse(client, db_session):
    photo = _article(db_session, 1, video=False)
    reel = _creneau(db_session, 5, FormatCreneau.REEL)
    reponse = client.patch(f"/schedule/{reel.id}", json={"article_id": photo.id})
    assert reponse.status_code == 409
    assert reponse.json()["detail"].startswith("Article inutilisable en reel : aucune video")


def test_remplacer_un_reel_par_un_media_est_refuse(client, db_session):
    article = _article(db_session, 1, categorie=SourceCategorie.MEDIA)
    reel = _creneau(db_session, 5, FormatCreneau.REEL)
    assert client.patch(f"/schedule/{reel.id}", json={"article_id": article.id}).status_code == 409


def test_remplacer_un_reel_par_une_video_d_artiste(client, db_session):
    article = _article(db_session, 1)
    reel = _creneau(db_session, 5, FormatCreneau.REEL)
    vue = client.patch(f"/schedule/{reel.id}", json={"article_id": article.id}).json()
    assert (vue["article_id"], vue["epingle"], vue["format"]) == (article.id, True, "reel")


def test_decaler_un_reel_saute_les_carrousels(client, db_session):
    a, b, c = _article(db_session, 1), _article(db_session, 2), _article(db_session, 3)
    premier = _creneau(db_session, 5, FormatCreneau.REEL, a)
    carrousel = _creneau(db_session, 8, FormatCreneau.CARROUSEL, b)
    suivant = _creneau(db_session, 17, FormatCreneau.REEL, c)
    vue = client.patch(f"/schedule/{premier.id}", json={"decaler": True}).json()
    assert vue["article_id"] == c.id
    db_session.refresh(suivant)
    db_session.refresh(carrousel)
    assert (suivant.article_id, carrousel.article_id) == (a.id, b.id)


def test_l_offre_de_reels(client, db_session):
    _article(db_session, 1)
    _article(db_session, 2, categorie=SourceCategorie.MEDIA)
    _article(db_session, 3, video=False)
    assert client.get("/schedule/offre-reels").json() == {"avec_video": 2, "eligibles": 1, "fraicheur_jours": 3}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_schedule_api_reels.py -v`
Expected: FAIL — `KeyError: 'format'`

- [ ] **Step 3: Implement schemas**

Dans `app/schemas.py`, importer `FormatCreneau` depuis `app.models`, ajouter `format: FormatCreneau` à `CreneauOut` juste après `publier_a`, et après `CreneauUpdate` :

```python
class OffreReelsOut(BaseModel):
    """La matiere disponible pour les reels sur la fenetre de fraicheur."""

    avec_video: int
    eligibles: int
    fraicheur_jours: int
```

- [ ] **Step 4: Implement the router**

Dans `app/api/schedule.py` :

```python
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau
from app.schedule.store import (
    MODIFIABLES,
    TEMPLATES,
    VERROUILLES,
    appliquer_arbitrage,
    offre_reels,
    post_a_reprendre,
    raison_ineligible_reel,
    utc,
)
from app.schemas import CreneauOut, CreneauUpdate, ModeOut, ModeUpdate, OffreReelsOut
```

Dans `_vue`, ajouter `format=creneau.format,` après `publier_a=...`.

Après `lister_file` :

```python
@router.get("/schedule/offre-reels", response_model=OffreReelsOut)
def lire_offre_reels(db: Session = Depends(get_db)):
    return OffreReelsOut(**offre_reels(db, _maintenant()))
```

Dans la branche `payload.article_id`, juste après le refus d'un article exclu :

```python
        if creneau.format == FormatCreneau.REEL:
            # Meme regle que l'arbitrage : la securite prime sur le choix manuel
            # (credit obligatoire, jamais un compte media).
            raison = raison_ineligible_reel(article)
            if raison is not None:
                raise HTTPException(status_code=409, detail=f"Article inutilisable en reel : {raison}")
```

et remplacer `creneau.post_id = post_a_reprendre(db, article.id)` par :

```python
        creneau.post_id = post_a_reprendre(db, article.id, TEMPLATES[creneau.format])
```

Dans la branche `decaler`, ajouter la condition de format à la requête du créneau suivant :

```python
            .where(
                Creneau.publier_a > creneau.publier_a,
                Creneau.format == creneau.format,
                Creneau.etat.in_(MODIFIABLES),
            )
```

Compléter la docstring du module : « Un créneau reel n'accepte qu'un article éligible en reel, et décaler n'échange qu'avec un créneau du même format. »

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_schedule_api_reels.py tests/test_schedule_api.py -v`
Expected: tout passe

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/schemas.py app/api/schedule.py tests/test_schedule_api_reels.py
git commit -F - <<'EOF'
feat(api): format des creneaux, regles reel et offre de videos

La file montre le format ; un creneau reel refuse un article ineligible ;
decaler reste dans le format ; GET /schedule/offre-reels mesure la matiere.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 13: Le format et l'offre dans le panneau Programmation

**Files:**
- Modify: `app/static/dashboard.html`
- Test: `tests/test_dashboard_mode_control.py`

**Interfaces:**
- Consumes: `CreneauOut.format`, `GET /schedule/offre-reels` (tâche 12)
- Produces: pastille `.format-pill` par ligne ; zone `#offre-reels` remplie par `chargerOffreReels()`

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_dashboard_mode_control.py` :

```python
def test_le_panneau_programmation_montre_le_format_et_l_offre_de_reels():
    html = DASHBOARD.read_text()
    assert 'fetch("/schedule/offre-reels")' in html
    assert 'id="offre-reels"' in html
    assert "format-pill" in html
    assert "FORMATS_CRENEAU" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dashboard_mode_control.py -v`
Expected: FAIL — `AssertionError`

- [ ] **Step 3: Implement**

CSS, après la ligne `.status-pill.manque { ... }` :

```css
  .format-pill { font-size: 11px; border-radius: 999px; padding: 2px 8px; background: #23232d; color: var(--muted); white-space: nowrap; }
  .format-pill.reel { background: #3d1f35; color: #ff9ad5; }
```

HTML, juste après `<div id="mode-explication" class="mode-explication"></div>` :

```html
  <div id="offre-reels" class="mode-explication"></div>
```

JS, après `const CRENEAUX_MODIFIABLES = ...` :

```js
const FORMATS_CRENEAU = { carrousel: "🖼 Carrousel", reel: "🎬 Reel" };

async function chargerOffreReels() {
  const zone = document.getElementById("offre-reels");
  try {
    const reponse = await fetch("/schedule/offre-reels");
    if (!reponse.ok) throw new Error(String(reponse.status));
    const offre = await reponse.json();
    zone.textContent = `Reels : ${offre.eligibles} article(s) éligible(s) sur ${offre.avec_video} avec vidéo (${offre.fraicheur_jours} derniers jours).`;
  } catch (erreur) {
    zone.textContent = "Offre de reels indisponible.";
  }
}
```

Dans `loadSchedulePanel`, appeler `chargerOffreReels();` juste après `chargerMode();`. Dans le gabarit de ligne, insérer la pastille de format avant la pastille d'état :

```js
    row.innerHTML = `
      <div class="grow"><div class="hl"></div><div class="sub"></div></div>
      <span class="format-pill"></span>
      <span class="status-pill"></span>
      <span class="slot-actions"></span>`;
```

et, juste après avoir rempli `pastille.textContent` :

```js
    const formatPill = row.querySelector(".format-pill");
    formatPill.classList.add(c.format);
    formatPill.textContent = FORMATS_CRENEAU[c.format] || c.format;
```

Pour que l'invite « Remplacer » dise ce qu'un reel accepte, remplacer la ligne du bouton ✏️ par :

```js
      bouton("✏️", "Remplacer par un autre article (identifiant)", c.format === "reel" ? "remplacer-reel" : "remplacer");
```

et le début de `modifierCreneau` par :

```js
async function modifierCreneau(id, donnees) {
  if (donnees === "remplacer" || donnees === "remplacer-reel") {
    const question = donnees === "remplacer-reel"
      ? "Identifiant de l'article (vidéo d'un compte artiste, festival, label ou club) :"
      : "Identifiant de l'article à programmer ici :";
    const saisie = prompt(question);
    if (!saisie) return;
    donnees = { article_id: Number(saisie) };
  }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dashboard_mode_control.py -v`
Expected: tout passe

- [ ] **Step 5: Vérifier le panneau à l'écran, sur une base jetable**

Même procédure que la tâche 14 du plan du 2026-09-16, sans toucher au dashboard installé sur le port 8000 :

```bash
DB="$(mktemp -d)/file.db"
DATABASE_URL="sqlite:///$DB" uv run python -c "from app.db import Base, engine; import app.models; Base.metadata.create_all(engine)"
DATABASE_URL="sqlite:///$DB" nohup uv run uvicorn app.main:app --host 127.0.0.1 --port 8010 > /tmp/dash8010.log 2>&1 &
curl -s --retry 20 --retry-delay 1 --retry-connrefused -X POST http://127.0.0.1:8010/schedule/rebuild | head -c 300
uv run python - <<'EOF'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    page = p.chromium.launch().new_page(viewport={"width": 1200, "height": 900})
    page.goto("http://127.0.0.1:8010/")
    page.click('button[data-preset="schedule"]')
    page.wait_for_selector("#schedule-list .post-row")
    page.screenshot(path="/tmp/panneau_reels.png", full_page=True)
EOF
pkill -f "uvicorn app.main:app --host 127.0.0.1 --port 8010"
```

Expected : le JSON contient 12 créneaux, `"format":"reel"` et `"format":"carrousel"` mêlés. Ouvrir `/tmp/panneau_reels.png` : douze lignes, pastilles « 🎬 Reel » et « 🖼 Carrousel », ligne « Reels : 0 article(s) éligible(s) sur 0 avec vidéo (3 derniers jours). » Ne cliquer sur aucun bouton d'action.

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/static/dashboard.html tests/test_dashboard_mode_control.py
git commit -F - <<'EOF'
feat(dashboard): format des creneaux et offre de reels

Pastille Reel ou Carrousel par creneau, et le nombre d'articles eligibles
en reel sur la fenetre de fraicheur.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 14: Documenter les reels automatiques

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: tout ce qui précède
- Produces: documentation à jour

- [ ] **Step 1: Mettre à jour « Publication automatique »**

Remplacer le premier paragraphe (« Kinda publie seul deux carrousels par jour… peut en ramener. ») par :

```markdown
Kinda publie seul **deux carrousels et deux reels par jour**, choisis dans le flux
scoré. Design : `docs/superpowers/specs/2026-09-11-publication-meta-design.md`
(révision du 2026-09-16) pour la file, et
`docs/superpowers/specs/2026-09-17-reels-automatiques-design.md` pour les reels.
```

- [ ] **Step 2: Ajouter une sous-section après « Essai sans publier »**

```markdown
**Reels automatiques (2026-09-17).** `creneaux.format` (`carrousel` | `reel`,
migration 0017) ; fenêtres reel propres (`PUBLICATION_REEL_FENETRES`, défaut
`12:00-14:00,20:30-22:30`), `PUBLICATION_CRENEAUX_REEL=0` les désactive.
`publier_a` reste unique tous formats confondus : `slots.py` refuse des fenêtres
qui se chevauchent. Arbitrage **reels d'abord**, un article n'occupe jamais deux
créneaux. Éligible en reel : vidéo avec URL directe (≤ 90 s ou durée inconnue),
compte artiste/festival/label/club (`PUBLICATION_REEL_CATEGORIES`), source
Instagram (crédit). Le gel (`app/publish/preparation_reel.py`) télécharge la
vidéo, l'assemble (`assembler_reel`, sans edit list, GOP fermé), sonde source et
sortie contre les specs Meta, et fige `<prefixe>_reel.mp4` puis
`<prefixe>_cover.jpg` ; la publication crée un conteneur `REELS` avec
`cover_url` explicite (sondage 15 s, 10 min max). Crédit `🎥 via @compte`
obligatoire (`posts.credit`), jamais coupé. ffmpeg absent = panne systémique qui
ne suspend que les reels. `GET /schedule/offre-reels` mesure la matière.
```

- [ ] **Step 3: Ajouter une contrainte apprise**

Dans « Contraintes apprises sur le terrain », après le point sur `business_discovery` :

```markdown
- **Meta omet `media_url` des médias qui contiennent du contenu protégé**
  (documenté, et constaté le 2026-09-17 : 10 reels sur 10 de `davidguetta`, 39
  reels ingérés sur 59 sans vidéo). Un reel d'artiste porte presque toujours de la
  musique : **il n'est pas téléchargeable par l'API**. `_media` garde alors une
  entrée `photo` avec la vignette (`thumbnail_url`), pour que le post reste
  illustré. Les vidéos publiables en reel viennent surtout des enfants vidéo des
  carrousels et de comptes sans musique protégée — l'offre est à surveiller au
  dashboard.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -F - <<'EOF'
docs: reels automatiques

Format des creneaux, arbitrage reels d'abord, gel ffmpeg, credit
obligatoire, et l'omission de media_url sur les reels d'artistes.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

## Mise en service — décisions de l'utilisateur

Aucune de ces étapes n'est exécutée par un agent d'implémentation. Rien n'est publié tant que le mode n'est pas `reel`.

1. **Trancher les questions ouvertes du design**, d'abord la n° 1 (approvisionnement) : sans décision contraire, les défauts recommandés s'appliquent.
2. **Appliquer la migration 0017 sur Neon** : `uv run alembic upgrade head` (vérifiée sur cluster jetable à la tâche 2, réversible par `alembic downgrade 0016`).
3. **Redémarrer le dashboard** pour charger la nouvelle API : `launchctl kickstart -k gui/$(id -u)/com.musicnewsradar.dashboard`. L'agent de publication recharge le code à chaque passage.
4. **Vérifier ffmpeg et ffprobe** : `which ffmpeg ffprobe` (sinon `brew install ffmpeg`).
5. **Essai sans publier** d'un post `kinda_reel` : `uv run python -m scripts.tester_publication --post <ID>`. Le conteneur créé chez Meta n'est pas public et expire en 24 h.
6. **Observer en `apercu`** quelques jours : créneaux reel « Programmé » → « Gelé » → « Aperçu, non publié », et la ligne d'offre de reels. Si l'offre reste à zéro, les créneaux reel restent vides sans dégât ; régler `PUBLICATION_CRENEAUX_REEL=0` pour les masquer.
7. **Publier pour de vrai** : mode `reel` au dashboard (confirmation « PUBLIER »), qui vaut pour les deux formats.
