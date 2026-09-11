# Source Instagram (chantier A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingérer les posts de comptes Instagram cibles via `business_discovery` comme n'importe quelle autre source, et éteindre les 100 sources X mortes qui noient la supervision sous des erreurs 402.

**Architecture :** Un adapter de plus dans la couche pluggable `app/sources/`. Le contrat `fetch() -> list[RawItem]` ne change pas, donc dédup, clustering, scoring, illustrations et mentions A&R fonctionnent sans modification. L'adapter est coupé en deux comme `RssAdapter` : `fetch()` porte le réseau et l'état, `parse()` est une fonction pure testable sur fixtures — aucun test ne sort sur le réseau.

**Tech Stack :** Python 3.14, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), httpx (sync), Alembic, pytest, uv, pydantic-settings.

**Spec :** `docs/superpowers/specs/2026-09-11-publication-meta-design.md` (chantier A)

## Global Constraints

- Python sync, pas d'async : volumes faibles, tout le pipeline est synchrone.
- Enums stockés en **VARCHAR + CHECK** (`native_enum=False`), jamais d'enum natif Postgres.
- Dans les migrations écrites à la main, entourer les noms de contraintes CHECK avec `op.f()`, sinon la naming convention de `app/db.py` redouble le préfixe.
- `source.state` est un JSONB : le **réassigner en entier**, jamais de mutation in place, sinon SQLAlchemy ne persiste pas.
- Les headers HTTP doivent être **ASCII pur** — pas d'accents, httpx plante avant d'envoyer.
- Tests sur SQLite in-memory (fixtures dans `tests/conftest.py`).
- Les médias sont des dicts à **six clés toujours présentes**, construits par `app.content.media.entree()` — jamais à la main.
- Commentaires et messages de commit en français, **sans accents dans les messages de commit** (convention observée dans l'historique).
- Lint : `uv run ruff check .` doit passer.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `app/sources/instagram.py` (créer) | L'adapter : `parse()` pur + `fetch()` réseau/état |
| `app/models.py` (modifier, l.27-32) | `SourceType.INSTAGRAM` |
| `app/sources/__init__.py` (modifier) | Entrée au registry |
| `app/config.py` (modifier) | `meta_access_token`, `meta_ig_user_id`, `meta_api_version`, `instagram_max_items` |
| `alembic/versions/0014_source_instagram.py` (créer) | Élargir le CHECK `ck_sources_source_type` |
| `scripts/desactiver_sources_x.py` (créer) | Éteindre les sources X mortes, idempotent, réversible |
| `tests/test_sources/test_instagram.py` (créer) | Fixtures JSON, aucun réseau |
| `tests/test_desactiver_sources_x.py` (créer) | Le script sur base SQLite |

---

### Task 1: Éteindre les sources X mortes

Indépendante de tout le reste et livrable seule. Chaque run produit aujourd'hui ~1000 erreurs 402 qui noient `diagnostiquer()` — la supervision doit redevenir lisible avant qu'on lui ajoute des sources.

**Files:**
- Create: `scripts/desactiver_sources_x.py`
- Test: `tests/test_desactiver_sources_x.py`

**Interfaces:**
- Consumes: `app.models.Source`, `app.models.SourceType` (existants)
- Produces: `desactiver_sources_x(db: Session, *, appliquer: bool = False) -> list[str]` — rend les noms des sources touchées (ou qui le seraient en mode plan)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_desactiver_sources_x.py
from app.models import Source, SourceType
from scripts.desactiver_sources_x import desactiver_sources_x


def _source(db, nom, type_, active=True):
    s = Source(name=nom, type=type_, url=f"https://exemple/{nom}", active=active)
    db.add(s)
    db.commit()
    return s


def test_plan_ne_touche_rien(db_session):
    x = _source(db_session, "David Guetta", SourceType.X)
    touchees = desactiver_sources_x(db_session)
    assert touchees == ["David Guetta"]
    assert x.active is True


def test_appliquer_desactive_les_sources_x(db_session):
    x = _source(db_session, "David Guetta", SourceType.X)
    desactiver_sources_x(db_session, appliquer=True)
    db_session.refresh(x)
    assert x.active is False


def test_ne_touche_pas_les_autres_types(db_session):
    rss = _source(db_session, "Tsugi", SourceType.RSS)
    desactiver_sources_x(db_session, appliquer=True)
    db_session.refresh(rss)
    assert rss.active is True


def test_idempotent_une_source_deja_eteinte_n_est_pas_listee(db_session):
    _source(db_session, "Drake", SourceType.X, active=False)
    assert desactiver_sources_x(db_session) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desactiver_sources_x.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.desactiver_sources_x'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/desactiver_sources_x.py
"""Eteindre les sources X, devenues inutilisables.

La facturation developer.x.com n'est plus active : les 100 sources X renvoient
toutes 402 Payment Required, soit un millier d'erreurs par run. Le probleme n'est
pas le cout de ces appels (ils echouent avant d'etre factures) mais le BRUIT :
`diagnostiquer()` remonte 86 sources en erreur a chaque passage, ce qui rend la
supervision inutile au moment precis ou on s'apprete a lui confier la publication.

Reversible : on desactive, on ne supprime pas. Le `state` de chaque source garde
son since_id, donc rallumer la facturation suffirait a reprendre ou on s'est
arrete.
"""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, attendre_base
from app.models import Source, SourceType


def desactiver_sources_x(db: Session, *, appliquer: bool = False) -> list[str]:
    """Rend les noms des sources X encore actives, et les eteint si `appliquer`."""
    sources = db.scalars(
        select(Source).where(Source.type == SourceType.X, Source.active.is_(True))
    ).all()
    if appliquer:
        for source in sources:
            source.active = False
        db.commit()
    return [s.name for s in sources]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="eteindre pour de bon")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        noms = desactiver_sources_x(db, appliquer=args.apply)

    verbe = "desactivees" if args.apply else "a desactiver"
    print(f"{len(noms)} source(s) X {verbe}")
    for nom in noms[:10]:
        print(f"  - {nom}")
    if len(noms) > 10:
        print(f"  ... et {len(noms) - 10} autres")
    if not args.apply and noms:
        print("\nRelancer avec --apply pour appliquer.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_desactiver_sources_x.py -v`
Expected: 4 passed

- [ ] **Step 5: Vérifier le plan sur la vraie base, sans appliquer**

Run: `uv run python -m scripts.desactiver_sources_x`
Expected: affiche « 100 source(s) X a desactiver » suivi de dix noms. **Ne pas passer `--apply` ici** — c'est une modification de la base de l'utilisateur, elle lui revient.

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add scripts/desactiver_sources_x.py tests/test_desactiver_sources_x.py
git commit -m "feat(sources): eteindre les sources X, la facturation X n'est plus active"
```

---

### Task 2: Le type de source `instagram`

Le type doit exister en base et au registry avant que l'adapter serve à quoi que ce soit. Petite tâche, mais elle porte une migration : un reviewer peut la rejeter seule.

**Files:**
- Modify: `app/models.py:27-32` (`SourceType`)
- Modify: `app/sources/__init__.py` (registry)
- Create: `alembic/versions/0014_source_instagram.py`
- Test: `tests/test_sources/test_registry.py` (ajouter un cas)

**Interfaces:**
- Produces: `SourceType.INSTAGRAM == "instagram"`, et `ADAPTERS[SourceType.INSTAGRAM] is InstagramAdapter`

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_sources/test_registry.py` :

```python
def test_le_registry_connait_instagram():
    from app.models import Source, SourceType
    from app.sources import get_adapter
    from app.sources.instagram import InstagramAdapter

    source = Source(id=1, name="Tsugi IG", type=SourceType.INSTAGRAM, url="ig:tsugimag")
    assert isinstance(get_adapter(source), InstagramAdapter)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sources/test_registry.py::test_le_registry_connait_instagram -v`
Expected: FAIL — `AttributeError: INSTAGRAM` ou `ModuleNotFoundError: app.sources.instagram`

- [ ] **Step 3: Créer le squelette de l'adapter**

Le registry ne peut pas pointer vers une classe inexistante. Le corps arrive en Task 3.

```python
# app/sources/instagram.py
"""Adapter Instagram via `business_discovery` de l'API Graph de Meta.

Gratuit, contrairement a l'API X qu'il remplace. Contrepartie a connaitre : seuls
les comptes PROFESSIONNELS (business ou creator) sont visibles. Un artiste
emergent en profil perso -- soit une bonne part du deal-flow A&R -- n'est pas
atteignable par cette voie. Cette source COMPLETE les RSS, elle ne les remplace
pas.
"""

from app.sources.base import RawItem, SourceAdapter


class InstagramAdapter(SourceAdapter):
    def fetch(self) -> list[RawItem]:
        raise NotImplementedError
```

- [ ] **Step 4: Ajouter le type et l'entrée au registry**

Dans `app/models.py`, ajouter à `SourceType` :

```python
    INSTAGRAM = "instagram"
```

Dans `app/sources/__init__.py` : importer `InstagramAdapter`, l'ajouter à `ADAPTERS` sous `SourceType.INSTAGRAM`, et à `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_sources/test_registry.py -v`
Expected: PASS

- [ ] **Step 6: Écrire la migration**

```python
# alembic/versions/0014_source_instagram.py
"""source_instagram : elargir les types de source acceptes

Le CHECK sur `sources.type` enumere les valeurs en dur (convention du projet :
VARCHAR + CHECK, jamais d'enum natif Postgres). Ajouter une valeur a l'enum Python
ne suffit donc pas -- sans cette migration, l'INSERT d'une source Instagram est
rejete par la base.

Postgres ne sait pas modifier un CHECK : on le supprime et on le recree.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# `op.f()` est indispensable : sans lui, la naming convention de `app/db.py`
# redouble le prefixe et produit `ck_sources_ck_sources_source_type`.
CONTRAINTE = op.f("ck_sources_source_type")

AVANT = ("rss", "reddit_rss", "bluesky", "x")
APRES = ("rss", "reddit_rss", "bluesky", "x", "instagram")


def _poser(valeurs):
    op.create_check_constraint(CONTRAINTE, "sources", sa.column("type").in_(valeurs))


def upgrade() -> None:
    op.drop_constraint(CONTRAINTE, "sources", type_="check")
    _poser(APRES)


def downgrade() -> None:
    # Les sources Instagram violeraient le CHECK restaure : les eteindre d'abord.
    op.execute("DELETE FROM sources WHERE type = 'instagram'")
    op.drop_constraint(CONTRAINTE, "sources", type_="check")
    _poser(AVANT)
```

- [ ] **Step 7: Vérifier la migration sur un cluster jetable**

La base Postgres locale est celle de l'utilisateur : ne pas la migrer à l'aveugle. `CLAUDE.md` documente la procédure du cluster jetable.

```bash
/Library/PostgreSQL/15/bin/initdb -D /tmp/pg_test_0014 -U postgres
/Library/PostgreSQL/15/bin/pg_ctl -D /tmp/pg_test_0014 -o "-p 55432" -l /tmp/pg_test_0014.log start
/Library/PostgreSQL/15/bin/createdb -p 55432 -U postgres music_news_radar
DATABASE_URL="postgresql+psycopg://postgres@localhost:55432/music_news_radar" uv run alembic upgrade head
DATABASE_URL="postgresql+psycopg://postgres@localhost:55432/music_news_radar" uv run alembic downgrade 0013
DATABASE_URL="postgresql+psycopg://postgres@localhost:55432/music_news_radar" uv run alembic upgrade head
/Library/PostgreSQL/15/bin/pg_ctl -D /tmp/pg_test_0014 stop
```

Expected: `upgrade` puis `downgrade` puis `upgrade` sans erreur — l'aller-retour prouve que `downgrade` est correct.

- [ ] **Step 8: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest`
Expected: tout passe

- [ ] **Step 9: Commit**

```bash
git add app/models.py app/sources/__init__.py app/sources/instagram.py alembic/versions/0014_source_instagram.py tests/test_sources/test_registry.py
git commit -m "feat(sources): le type de source instagram, migration du CHECK incluse"
```

---

### Task 3: `parse()` — la réponse Meta devient des `RawItem`

Le cœur du chantier, et la seule partie où il y a des décisions. Pure : pas de réseau, pas de base. Tout se teste sur fixtures.

**Files:**
- Modify: `app/sources/instagram.py`
- Create: `tests/test_sources/test_instagram.py`

**Interfaces:**
- Consumes: `app.content.media.entree`, `PHOTO`, `VIDEO` ; `app.sources.base.RawItem`
- Produces:
  - `InstagramAdapter.compte -> str` (le username extrait de `source.url`)
  - `InstagramAdapter.parse(payload: dict) -> list[RawItem]`
  - `InstagramAdapter.titre_depuis_caption(caption: str | None) -> str` (module-level helper `titre_depuis_caption` réutilisable)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sources/test_instagram.py
import datetime as dt

import pytest

from app.models import Source, SourceType
from app.sources.instagram import InstagramAdapter, titre_depuis_caption

# Forme reelle d'une reponse business_discovery : le compte cible est imbrique
# sous la cle `business_discovery` de NOTRE compte.
PAYLOAD = {
    "business_discovery": {
        "username": "tsugimag",
        "followers_count": 49000,
        "media": {
            "data": [
                {
                    "id": "1",
                    "caption": "Laurent Garnier annonce une tournee. Dates et billets en bio.",
                    "media_type": "IMAGE",
                    "media_url": "https://cdn.example/photo.jpg",
                    "permalink": "https://www.instagram.com/p/AAA/",
                    "timestamp": "2026-09-10T14:30:00+0000",
                    "like_count": 1200,
                    "comments_count": 30,
                },
                {
                    "id": "2",
                    "caption": "Le live de Sara Landry hier soir",
                    "media_type": "VIDEO",
                    "media_url": "https://cdn.example/video.mp4",
                    "thumbnail_url": "https://cdn.example/vignette.jpg",
                    "permalink": "https://www.instagram.com/reel/BBB/",
                    "timestamp": "2026-09-09T22:05:00+0000",
                    "like_count": 8000,
                    "comments_count": 210,
                },
            ]
        },
    }
}


def make_adapter(url="ig:tsugimag"):
    source = Source(id=1, name="Tsugi IG", type=SourceType.INSTAGRAM, url=url)
    return InstagramAdapter(source)


@pytest.mark.parametrize(
    "url, attendu",
    [
        ("ig:tsugimag", "tsugimag"),
        ("ig:@tsugimag", "tsugimag"),
        ("IG:TsugiMag", "TsugiMag"),
        ("  ig:tsugimag  ", "tsugimag"),
        ("https://www.instagram.com/tsugimag/", "tsugimag"),
    ],
)
def test_le_compte_se_lit_sous_plusieurs_formes(url, attendu):
    assert make_adapter(url).compte == attendu


def test_parse_rend_un_item_par_media():
    items = make_adapter().parse(PAYLOAD)
    assert len(items) == 2


def test_l_url_est_le_permalink_pas_le_media():
    """Le permalink est stable et citable ; media_url est une URL CDN signee qui
    expire. C'est le permalink qui doit servir de cle de dedup."""
    items = make_adapter().parse(PAYLOAD)
    assert items[0].url == "https://www.instagram.com/p/AAA/"


def test_le_titre_est_la_premiere_phrase_de_la_caption():
    items = make_adapter().parse(PAYLOAD)
    assert items[0].title == "Laurent Garnier annonce une tournee."


def test_le_summary_garde_la_caption_entiere():
    items = make_adapter().parse(PAYLOAD)
    assert items[0].summary.endswith("Dates et billets en bio.")


def test_la_date_est_en_utc_naive():
    items = make_adapter().parse(PAYLOAD)
    assert items[0].published_at == dt.datetime(2026, 9, 10, 14, 30)
    assert items[0].published_at.tzinfo is None


def test_une_photo_donne_un_media_type_photo():
    media = make_adapter().parse(PAYLOAD)[0].media
    assert media == [
        {
            "type": "photo",
            "url": "https://cdn.example/photo.jpg",
            "preview_url": None,
            "duration_ms": None,
            "width": None,
            "height": None,
        }
    ]


def test_une_video_porte_son_type_et_sa_vignette():
    """Le type `video` est ce qui rend le format REELS decidable en aval --
    contrairement a X, Instagram le donne directement."""
    media = make_adapter().parse(PAYLOAD)[1].media
    assert media[0]["type"] == "video"
    assert media[0]["url"] == "https://cdn.example/video.mp4"
    assert media[0]["preview_url"] == "https://cdn.example/vignette.jpg"


def test_un_carrousel_rend_tous_ses_enfants():
    payload = {
        "business_discovery": {
            "media": {
                "data": [
                    {
                        "id": "3",
                        "caption": "Le line-up complet",
                        "media_type": "CAROUSEL_ALBUM",
                        "permalink": "https://www.instagram.com/p/CCC/",
                        "timestamp": "2026-09-08T10:00:00+0000",
                        "children": {
                            "data": [
                                {"media_type": "IMAGE", "media_url": "https://cdn.example/1.jpg"},
                                {
                                    "media_type": "VIDEO",
                                    "media_url": "https://cdn.example/2.mp4",
                                    "thumbnail_url": "https://cdn.example/2.jpg",
                                },
                            ]
                        },
                    }
                ]
            }
        }
    }
    media = make_adapter().parse(payload)[0].media
    assert [m["type"] for m in media] == ["photo", "video"]


def test_un_post_sans_permalink_est_ignore():
    """Sans URL, pas de cle de dedup : l'article serait ingere a chaque run."""
    payload = {
        "business_discovery": {
            "media": {"data": [{"id": "4", "caption": "x", "media_type": "IMAGE"}]}
        }
    }
    assert make_adapter().parse(payload) == []


def test_un_payload_sans_business_discovery_rend_une_liste_vide():
    """Cas du compte non professionnel : Meta ne renvoie pas la cle."""
    assert make_adapter().parse({}) == []


@pytest.mark.parametrize(
    "caption, attendu",
    [
        ("Une phrase. Une autre.", "Une phrase."),
        ("Pas de point final", "Pas de point final"),
        ("Ligne une\nLigne deux", "Ligne une"),
        ("", "Post Instagram"),
        (None, "Post Instagram"),
        ("#techno #rave", "Post Instagram"),
        ("A" * 400, "A" * 197 + "..."),
    ],
)
def test_titre_depuis_caption(caption, attendu):
    assert titre_depuis_caption(caption) == attendu
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sources/test_instagram.py -v`
Expected: FAIL — `ImportError: cannot import name 'titre_depuis_caption'`

- [ ] **Step 3: Write minimal implementation**

Remplacer le contenu de `app/sources/instagram.py` (en gardant le docstring de module de la Task 2) :

```python
import datetime as dt
import re

from app.content.media import PHOTO, VIDEO, entree
from app.sources.base import RawItem, SourceAdapter

# Une caption Instagram n'a pas de titre : c'est un bloc libre qui commence
# souvent par l'essentiel et finit en hashtags. On prend la premiere phrase.
_FIN_DE_PHRASE = re.compile(r"(?<=[.!?])\s")
TITRE_MAX = 200
TITRE_PAR_DEFAUT = "Post Instagram"

_TYPES = {"IMAGE": PHOTO, "VIDEO": VIDEO}


def titre_depuis_caption(caption: str | None) -> str:
    """La premiere phrase de la caption, a defaut sa premiere ligne.

    Un titre vide casserait la dedup par hash (url_norm + titre) et rendrait le
    dashboard illisible : d'ou un repli explicite plutot qu'une chaine vide.
    """
    texte = (caption or "").strip()
    if not texte:
        return TITRE_PAR_DEFAUT
    premiere_ligne = texte.splitlines()[0].strip()
    phrase = _FIN_DE_PHRASE.split(premiere_ligne, maxsplit=1)[0].strip()
    # Une caption qui n'est QUE des hashtags ne donne pas un titre lisible.
    if not phrase or all(mot.startswith(("#", "@")) for mot in phrase.split()):
        return TITRE_PAR_DEFAUT
    if len(phrase) > TITRE_MAX:
        return phrase[: TITRE_MAX - 3] + "..."
    return phrase


def _date(timestamp: str | None) -> dt.datetime | None:
    """L'horodatage Meta ("2026-09-10T14:30:00+0000") en UTC naif.

    Le reste du projet stocke des datetimes naifs en UTC ; laisser passer un
    datetime aware provoquerait des comparaisons impossibles cote retention.
    """
    if not timestamp:
        return None
    try:
        return (
            dt.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z")
            .astimezone(dt.UTC)
            .replace(tzinfo=None)
        )
    except ValueError:
        return None


def _media(noeud: dict) -> list[dict]:
    """Les medias d'un post, carrousel aplati.

    Un carrousel porte ses enfants sous `children`, et n'a pas de `media_url`
    propre : sans cet aplatissement, un carrousel arriverait sans aucun media.
    """
    enfants = (noeud.get("children") or {}).get("data")
    if enfants:
        return [m for enfant in enfants for m in _media(enfant)]
    type_ = _TYPES.get(noeud.get("media_type"))
    url = noeud.get("media_url")
    if not type_ or not url:
        return []
    return [entree(type_, url, preview_url=noeud.get("thumbnail_url"))]


class InstagramAdapter(SourceAdapter):
    @property
    def compte(self) -> str:
        """Le username cible, ecrit `ig:nom` ou en URL instagram.com complete."""
        url = self.source.url.strip().rstrip("/")
        if url.lower().startswith("ig:"):
            return url[3:].lstrip("@").strip()
        return url.rsplit("/", 1)[-1].lstrip("@")

    def parse(self, payload: dict) -> list[RawItem]:
        decouverte = payload.get("business_discovery") or {}
        items = []
        for noeud in (decouverte.get("media") or {}).get("data", []):
            permalink = noeud.get("permalink")
            # Sans permalink il n'y a pas de cle de dedup : l'item serait
            # reingere a chaque run.
            if not permalink:
                continue
            caption = noeud.get("caption")
            items.append(
                RawItem(
                    url=permalink,
                    title=titre_depuis_caption(caption),
                    summary=caption or None,
                    published_at=_date(noeud.get("timestamp")),
                    media=_media(noeud),
                )
            )
        return items

    def fetch(self) -> list[RawItem]:
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sources/test_instagram.py -v`
Expected: tous les tests passent (18 cas avec les paramétrages)

- [ ] **Step 5: Lint**

Run: `uv run ruff check .`
Expected: pas d'erreur

- [ ] **Step 6: Commit**

```bash
git add app/sources/instagram.py tests/test_sources/test_instagram.py
git commit -m "feat(instagram): la reponse business_discovery devient des RawItem"
```

---

### Task 4: `fetch()` — réseau, état et garde-fous

**Files:**
- Modify: `app/sources/instagram.py`
- Modify: `app/config.py`
- Modify: `tests/test_sources/test_instagram.py`

**Interfaces:**
- Consumes: `parse()` de la Task 3 ; `settings.meta_access_token`, `settings.meta_ig_user_id`, `settings.meta_api_version`, `settings.instagram_max_items`
- Produces: `InstagramAdapter.fetch() -> list[RawItem]` ; `InstagramAdapter.construire_url() -> tuple[str, dict]` (url, params) — séparée pour être testable sans réseau ; `source.state["dernier_post"]` (ISO 8601)

- [ ] **Step 1: Ajouter la config**

Dans `app/config.py`, à la suite du bloc X :

```python
    # --- Meta / Instagram (business_discovery) ---
    # Gratuit, contrairement a X. Un seul token et un seul IG user id servent
    # TOUTES les sources Instagram : l'API interroge les comptes cibles a travers
    # le notre.
    meta_access_token: str | None = None
    meta_ig_user_id: str | None = None
    # Epinglee : une version d'API Graph reste servie environ deux ans, et une
    # montee de version doit etre un choix, jamais une surprise un matin.
    meta_api_version: str = "v21.0"
    # Nombre de posts recents lus par compte et par run. L'appel est gratuit, mais
    # il consomme le quota Graph de l'app (~200 appels/heure) : inutile d'aller
    # chercher loin a chaque passage horaire.
    instagram_max_items: int = 12
```

Ajouter les deux clés à `.env.example` avec un commentaire d'une ligne.

- [ ] **Step 2: Write the failing test**

Ajouter à `tests/test_sources/test_instagram.py` :

```python
class FauxReponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_l_url_cible_le_compte_et_demande_les_bons_champs(monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "17841400000000000")
    url, params = make_adapter().construire_url()

    assert url.endswith("/17841400000000000")
    assert "business_discovery.username(tsugimag)" in params["fields"]
    for champ in ("permalink", "caption", "timestamp", "media_url", "media_type"):
        assert champ in params["fields"]
    assert params["access_token"] == "TOK"


def test_le_carrousel_est_demande_explicitement(monkeypatch):
    """Sans `children`, un carrousel revient sans aucun media."""
    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    _, params = make_adapter().construire_url()
    assert "children" in params["fields"]


def test_fetch_sans_token_dit_quoi_faire(monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", None)
    with pytest.raises(RuntimeError, match="META_ACCESS_TOKEN"):
        make_adapter().fetch()


def test_fetch_note_le_dernier_post_vu(monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    adapter = make_adapter()
    monkeypatch.setattr(adapter, "_appeler", lambda: PAYLOAD)

    adapter.fetch()
    assert adapter.source.state["dernier_post"] == "2026-09-10T14:30:00"


def test_fetch_ne_rend_que_les_posts_plus_recents_que_l_etat(monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    adapter = make_adapter()
    adapter.source.state = {"dernier_post": "2026-09-09T22:05:00"}
    monkeypatch.setattr(adapter, "_appeler", lambda: PAYLOAD)

    items = adapter.fetch()
    assert [i.url for i in items] == ["https://www.instagram.com/p/AAA/"]


def test_l_etat_est_reassigne_en_entier(monkeypatch):
    """Piege SQLAlchemy documente : muter un JSONB en place ne persiste pas."""
    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    adapter = make_adapter()
    etat_initial = {"autre": "valeur"}
    adapter.source.state = etat_initial
    monkeypatch.setattr(adapter, "_appeler", lambda: PAYLOAD)

    adapter.fetch()
    assert adapter.source.state is not etat_initial
    assert adapter.source.state["autre"] == "valeur"
```

Ajouter `from app.config import settings` aux imports du fichier de test.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_sources/test_instagram.py -v -k "url or fetch or etat"`
Expected: FAIL — `AttributeError: 'InstagramAdapter' object has no attribute 'construire_url'`

- [ ] **Step 4: Write minimal implementation**

Ajouter en tête de `app/sources/instagram.py` : `import logging`, `import httpx`, `from app.config import settings`, et `logger = logging.getLogger(__name__)`.

Puis, dans `InstagramAdapter` :

```python
    timeout = 30.0

    # Les champs demandes au compte cible. `children` est indispensable : un
    # carrousel n'a pas de media_url propre et reviendrait vide sans lui.
    CHAMPS_MEDIA = (
        "id,caption,media_type,media_url,thumbnail_url,permalink,"
        "timestamp,like_count,comments_count,"
        "children{media_type,media_url,thumbnail_url}"
    )

    def construire_url(self) -> tuple[str, dict]:
        """L'appel business_discovery : notre compte, le leur en parametre.

        Meta n'expose pas de "lire le compte X" ; on lit NOTRE noeud utilisateur
        en lui demandant le champ `business_discovery` d'un autre compte.
        """
        base = f"https://graph.facebook.com/{settings.meta_api_version}"
        champs = (
            f"business_discovery.username({self.compte})"
            f"{{username,followers_count,media.limit({settings.instagram_max_items})"
            f"{{{self.CHAMPS_MEDIA}}}}}"
        )
        return (
            f"{base}/{settings.meta_ig_user_id}",
            {"fields": champs, "access_token": settings.meta_access_token},
        )

    def _appeler(self) -> dict:
        url, params = self.construire_url()
        # Header ASCII pur : httpx plante avant l'envoi sur un accent.
        entetes = {"User-Agent": "KindaBot/1.0 (music-news-radar)"}
        with httpx.Client(timeout=self.timeout, headers=entetes) as client:
            reponse = client.get(url, params=params)
            reponse.raise_for_status()
            return reponse.json()

    def fetch(self) -> list[RawItem]:
        if not settings.meta_access_token or not settings.meta_ig_user_id:
            raise RuntimeError(
                "META_ACCESS_TOKEN / META_IG_USER_ID manquants dans .env — "
                "token de Page a creer sur developers.facebook.com"
            )
        etat = dict(self.source.state or {})
        depuis = _date_iso(etat.get("dernier_post"))

        items = self.parse(self._appeler())
        if depuis is not None:
            items = [i for i in items if i.published_at is None or i.published_at > depuis]

        dates = [i.published_at for i in items if i.published_at]
        if dates:
            # Reassignation ENTIERE : muter le dict en place ne persiste pas.
            etat["dernier_post"] = max(dates).isoformat()
            self.source.state = etat
        return items
```

Et le petit lecteur d'état, à côté de `_date` :

```python
def _date_iso(valeur: str | None) -> dt.datetime | None:
    """L'etat relu. Une valeur illisible vaut "pas d'etat" : on preferera
    reingerer des posts deja vus (la dedup les ecarte) plutot que planter."""
    if not valeur:
        return None
    try:
        return dt.datetime.fromisoformat(valeur)
    except ValueError:
        return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_sources/test_instagram.py -v`
Expected: tout passe

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/sources/instagram.py app/config.py .env.example tests/test_sources/test_instagram.py
git commit -m "feat(instagram): fetch business_discovery, etat par date du dernier post"
```

---

### Task 5: Ajouter une source Instagram en ligne de commande, et documenter

**Files:**
- Modify: `CLAUDE.md`
- Test: `tests/test_sources/test_instagram.py` (un cas de bout en bout sur la base)

**Interfaces:**
- Consumes: tout ce qui précède
- Produces: `uv run python -m scripts.add_source --type instagram --url ig:<compte>` fonctionnel

- [ ] **Step 1: Constater que `add_source.py` n'a rien à changer**

`scripts/add_source.py:19` dérive ses choix de l'enum :

```python
parser.add_argument("--type", required=True, choices=[t.value for t in SourceType])
```

`instagram` est donc déjà accepté depuis la Task 2. Le vérifier :

Run: `uv run python -m scripts.add_source --help`
Expected: `instagram` apparaît dans les choix de `--type`

C'est exactement la promesse de la couche pluggable décrite dans `CLAUDE.md` (« ajouter une source d'un type déjà supporté = un simple INSERT ») ; la vérifier ici est plus utile que de la supposer.

- [ ] **Step 2: Write the failing test**

```python
def test_une_source_instagram_se_cree_et_se_relit(db_session):
    """De bout en bout sur la base : le type passe le CHECK, l'adapter se
    resout, l'etat se persiste."""
    from app.sources import get_adapter

    source = Source(name="Tsugi IG", type=SourceType.INSTAGRAM, url="ig:tsugimag")
    db_session.add(source)
    db_session.commit()

    relue = db_session.get(Source, source.id)
    adapter = get_adapter(relue)
    assert isinstance(adapter, InstagramAdapter)
    assert adapter.compte == "tsugimag"

    adapter.source.state = {"dernier_post": "2026-09-10T14:30:00"}
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Source, source.id).state["dernier_post"] == "2026-09-10T14:30:00"
```

- [ ] **Step 3: Run test to verify it fails or passes**

Run: `uv run pytest tests/test_sources/test_instagram.py::test_une_source_instagram_se_cree_et_se_relit -v`
Expected: PASS si les tâches 2-4 sont correctes. **Un échec ici signale une régression dans une tâche précédente** — le corriger là-bas, pas ici.

- [ ] **Step 4: Documenter dans CLAUDE.md**

Trois endroits, et pas un de plus :

1. Dans « Architecture », au registry : ajouter `instagram` à la liste des types.
2. Dans « Contraintes apprises sur le terrain », une entrée :

```markdown
- **`business_discovery` ne voit que les comptes PROFESSIONNELS** (business ou
  creator). Un artiste emergent en profil perso — soit une bonne part du deal-flow
  A&R — est invisible de cette API. La source Instagram COMPLETE les RSS, elle ne
  les remplace pas. Et les `media_url` du CDN Instagram sont SIGNEES et EXPIRENT
  en quelques jours : stocker l'URL puis la servir plus tard donne un 404
  silencieux, d'ou le telechargement de l'asset au moment du gel (chantier C).
- **Les sources X sont eteintes** (2026-09-11) : la facturation developer.x.com
  n'est plus active, les 100 sources renvoyaient 402 a chaque run et noyaient la
  supervision. `scripts/desactiver_sources_x.py` les eteint sans les supprimer —
  leur `since_id` est conserve.
```

3. Dans « Commandes », la ligne d'ajout de source : étendre `--type rss|reddit_rss` en `--type rss|reddit_rss|instagram`.

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md tests/test_sources/test_instagram.py
git commit -m "feat(instagram): ajout de source en ligne de commande et documentation"
```

---

## Ce qui reste à la main de l'utilisateur

Aucune de ces étapes n'est exécutable par un agent, et le chantier n'est pas *en service* tant qu'elles ne sont pas faites :

1. **Créer l'app Meta** sur developers.facebook.com, lier le compte Instagram professionnel Kinda à une Page Facebook, et générer un **token de Page longue durée**. Renseigner `META_ACCESS_TOKEN` et `META_IG_USER_ID` dans `.env`.
2. **Choisir les comptes cibles** et les ajouter (`--type instagram --url ig:<compte>`). Ils doivent être professionnels.
3. **Appliquer la migration** : `uv run alembic upgrade head`.
4. **Éteindre les sources X** : `uv run python -m scripts.desactiver_sources_x --apply`.
5. **Recharger les crédits Anthropic** — sans scoring, une source de plus ne fait qu'ajouter des articles non notés au stock de 1419 déjà en attente.
