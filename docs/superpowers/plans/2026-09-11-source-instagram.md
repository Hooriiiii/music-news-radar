# Nettoyage des sources X (ex-chantier A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Révisé le 2026-09-16.** Ce plan construisait aussi une source Instagram via
> `business_discovery`. Elle est **abandonnée** : l'API ne l'offre qu'avec la
> connexion Facebook, que l'utilisateur n'a pas pu mettre en place (échec de
> création du compte Facebook). Kinda passe par la connexion Instagram seule, qui
> permet de publier mais pas de lire d'autres comptes. Les anciennes tâches 2 à 5
> sont retirées ; elles restent lisibles dans le commit `89dba3d`. Voir la révision
> en tête de `docs/superpowers/specs/2026-09-11-publication-meta-design.md`.

**Goal:** Éteindre les sources X mortes (85 actives au 2026-09-16, toutes en 402 Payment Required) qui noient la supervision sous des erreurs.

**Architecture :** Un script idempotent et réversible, avec une fonction testable séparée de l'entrée en ligne de commande. Il n'affiche que le plan tant qu'on ne passe pas `--apply`.

**Tech Stack :** Python 3.14, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), pytest, uv.

**Spec :** `docs/superpowers/specs/2026-09-11-publication-meta-design.md` (chantier A, « Nettoyage joint »)

## Global Constraints

- Python sync, pas d'async : volumes faibles, tout le pipeline est synchrone.
- Tests sur SQLite in-memory (fixtures dans `tests/conftest.py`).
- Commentaires et messages de commit en français, **sans accents dans les messages de commit** (convention observée dans l'historique).
- Lint : `uv run ruff check .` doit passer.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `scripts/desactiver_sources_x.py` (créer) | Éteindre les sources X mortes, idempotent, réversible |
| `tests/scripts/test_desactiver_sources_x.py` (créer) | Le script sur base SQLite |

---

### Task 1: Éteindre les sources X mortes

Indépendante de tout le reste et livrable seule. Chaque run produit aujourd'hui ~1000 erreurs 402 qui noient `diagnostiquer()` — la supervision doit redevenir lisible avant qu'on lui ajoute des sources.

**Files:**
- Create: `scripts/desactiver_sources_x.py`
- Test: `tests/scripts/test_desactiver_sources_x.py`

**Interfaces:**
- Consumes: `app.models.Source`, `app.models.SourceType` (existants)
- Produces: `desactiver_sources_x(db: Session, *, appliquer: bool = False) -> list[str]` — rend les noms des sources touchées (ou qui le seraient en mode plan)

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_desactiver_sources_x.py
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

Run: `uv run pytest tests/scripts/test_desactiver_sources_x.py -v`
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

Run: `uv run pytest tests/scripts/test_desactiver_sources_x.py -v`
Expected: 4 passed

- [ ] **Step 5: Vérifier le plan sur la vraie base, sans appliquer**

Run: `uv run python -m scripts.desactiver_sources_x`
Expected: affiche « 85 source(s) X a desactiver » suivi de dix noms. **Ne pas passer `--apply` ici** — c'est une modification de la base de l'utilisateur, elle lui revient.

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add scripts/desactiver_sources_x.py tests/scripts/test_desactiver_sources_x.py
git commit -m "feat(sources): eteindre les sources X, la facturation X n'est plus active"
```

---

## Ce qui reste à la main de l'utilisateur

1. **Éteindre les sources X** : `uv run python -m scripts.desactiver_sources_x --apply` — modification de sa base, elle lui revient.
2. **Trancher le sort des reels** : sans X ni source Instagram, aucune vidéo n'entre plus en base. Réactiver la facturation X pour les seules sources vidéo (UGC, radar) avant l'étape 1, ou accepter une file sans reels.

Déjà fait le 2026-09-16 : app Meta « Kinda Radar » (connexion Instagram), compte `kindanewsfrance` testeur, `META_ACCESS_TOKEN` et `META_IG_USER_ID` dans `.env`, droit de publier vérifié en lecture seule.
