# Socle local — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Faire du Mac un second ordonnanceur proprement cloisonné — ingestion locale toutes les 30 min depuis l'IP résidentielle, dashboard debout en permanence — en supprimant les deux agents de juillet qui font double emploi.

**Architecture:** Deux ordonnanceurs séparés **par script** et non par configuration : le cloud garde `scripts.run_pipeline` (ingestion + scoring + alertes + purge) toutes les 2 h ; le Mac exécute `scripts.run_ingest`, qui ne fait que de l'ingestion. L'installation passe par un module Python unique qui construit les plists avec `plistlib` — valides par construction, testables sans appeler `launchctl`.

**Tech Stack:** Python 3.11+ / uv, `plistlib` et `subprocess` (bibliothèque standard), launchd, `newsyslog`, SQLAlchemy 2.0, pytest.

**Spec:** `docs/superpowers/specs/2026-08-09-socle-local-design.md`

## Global Constraints

- Toutes les commandes passent par `uv run` (jamais `python` ou `pytest` nus).
- Lint : `uv run ruff check .` doit passer. `line-length = 100`, `target-version = "py311"`.
- **Commentaires et docstrings en français, SANS accents** — le style établi de `app/` et `scripts/`. Les chaînes affichées à l'utilisateur peuvent être accentuées (voir `scripts/seed_fr_sources.py`).
- Les commentaires expliquent le **pourquoi**, pas le quoi.
- **Idiome des scripts de ce projet :** sans `--apply`, on affiche le plan ; avec, on écrit. Copié de `scripts/seed_fr_sources.py`.
- **Aucune commande ne doit toucher la base de données** pendant l'implémentation : `DATABASE_URL` pointe sur l'instance Neon de production. Les tests utilisent des doubles injectés, jamais l'engine réel.
- **Aucun `launchctl` ni écriture dans `~/Library/LaunchAgents` sans accord explicite de l'utilisateur** — cela modifie ses agents de session. Les tests écrivent dans `tmp_path`.
- Commit après chaque tâche, message en français, préfixe `feat(deploy):` / `fix(deploy):` / `docs(deploy):`.

## Valeurs de référence

| Valeur | Décidée | Motif |
|---|---|---|
| Cadence de l'ingestion locale | **1800 s** (30 min) | Valeur de l'agent d'origine ; coût nul (requêtes HTTP, `since_id` partagé) |
| Port du dashboard | **8000**, sur `127.0.0.1` | Dashboard local ; `0.0.0.0` ouvrirait une surface pour rien |
| `ThrottleInterval` du dashboard | **60 s** | `KeepAlive` face à un port durablement occupé boucle toutes les 10 s et inonde les logs |
| Labels créés | `com.musicnewsradar.ingest`, `com.musicnewsradar.dashboard` | |
| Labels à supprimer | `com.musicnewsradar.pipeline`, `com.musicnewsradar.digest` | Agents du 5 juillet, toujours chargés |
| Rotation | 5 Mo, 5 générations, compression bzip2 | 43 Mo accumulés en cinq semaines |

---

## File Structure

| Fichier | Responsabilité |
|---|---|
| `app/db.py` (modifier) | Ajoute `attendre_base()` — préambule réseau. C'est ici parce que ce module possède déjà l'engine. |
| `scripts/run_ingest.py` (modifier) | Appelle le préambule avant d'ingérer. |
| `scripts/run_pipeline.py` (modifier) | Idem. |
| `scripts/install_agents.py` (créer) | Point d'entrée unique : construction des plists, plan, installation, statut, désinstallation. |
| `deploy/com.musicnewsradar.pipeline.plist` (supprimer) | Remplacé par la génération. Codait les chemins en dur. |
| `deploy/com.musicnewsradar.digest.plist` (supprimer) | Agent abandonné (pas de mail). |
| `deploy/install_launchd.sh` (supprimer) | Remplacé par `scripts/install_agents.py`. |
| `CLAUDE.md` (modifier) | Deux corrections factuelles + les nouvelles commandes. |
| `tests/test_db_attente.py` (créer) | Le préambule réseau, sans base. |
| `tests/test_install_agents.py` (créer) | Les plists, la migration, le plan — sans `launchctl`. |

---

## Task 1: le préambule réseau

Le constat qui justifie cette tâche : l'agent `digest` échoue **chaque jour depuis juillet** sur `failed to resolve host … .neon.tech`. Il part à 8h30, souvent juste après le réveil du Mac, avant que la résolution DNS réponde. L'agent d'ingestion aura `RunAtLoad` et partira lui aussi au réveil — sans préambule, un run sur deux sera perdu pour rien.

**Files:**
- Modify: `app/db.py`
- Modify: `scripts/run_ingest.py`
- Modify: `scripts/run_pipeline.py`
- Test: `tests/test_db_attente.py`

**Interfaces:**
- Consumes: rien.
- Produces:
  - `BaseInjoignable(RuntimeError)`
  - `attendre_base(essais: int = 5, delai: float = 6.0, dormir=time.sleep, connecter=None) -> None`
  - Constantes `ATTENTE_ESSAIS = 5`, `ATTENTE_DELAI = 6.0`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_attente.py`:

```python
"""Le preambule reseau des points d'entree planifies.

Motif concret : l'agent launchd `digest` echouait chaque jour depuis juillet sur
un « failed to resolve host ... .neon.tech ». Il part a 8h30, souvent juste apres
le reveil du Mac, avant que la resolution DNS reponde. L'agent d'ingestion aura
RunAtLoad et partira lui aussi au reveil.

Aucun test ici ne touche la base : `connecter` et `dormir` sont injectes.
"""

import pytest
from sqlalchemy.exc import OperationalError

from app.db import BaseInjoignable, attendre_base


def _echoue(fois: int):
    """Rend un `connecter` qui echoue les `fois` premiers appels puis reussit."""
    restant = {"n": fois}

    def connecter() -> None:
        if restant["n"] > 0:
            restant["n"] -= 1
            raise OperationalError("SELECT 1", {}, Exception("nom d'hote non resolu"))

    return connecter


def test_rend_la_main_immediatement_si_la_base_repond():
    sommeils: list[float] = []

    attendre_base(connecter=_echoue(0), dormir=sommeils.append)

    assert sommeils == []


def test_reessaie_puis_reussit():
    """Le cas du reveil : les premiers essais echouent, le reseau arrive."""
    sommeils: list[float] = []

    attendre_base(essais=5, delai=6.0, connecter=_echoue(2), dormir=sommeils.append)

    # Deux echecs -> deux attentes, pas une de plus : on ne dort pas apres le
    # succes, ni apres le dernier essai.
    assert sommeils == [6.0, 6.0]


def test_leve_apres_le_dernier_essai_sans_dormir_une_fois_de_trop():
    sommeils: list[float] = []

    with pytest.raises(BaseInjoignable, match="3 essais"):
        attendre_base(essais=3, delai=1.0, connecter=_echoue(99), dormir=sommeils.append)

    assert sommeils == [1.0, 1.0]


def test_conserve_la_cause_dorigine():
    """Un « nom d'hote non resolu » et un « mot de passe refuse » ne se
    depannent pas pareil : la cause doit remonter."""
    with pytest.raises(BaseInjoignable) as info:
        attendre_base(essais=1, connecter=_echoue(99), dormir=lambda _: None)

    assert isinstance(info.value.__cause__, OperationalError)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_db_attente.py -v`
Expected: FAIL — `ImportError: cannot import name 'BaseInjoignable' from 'app.db'`

- [ ] **Step 3: Implement in `app/db.py`**

Add the imports at the top of `app/db.py`:

```python
import logging
import time

from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)
```

And after the `SessionLocal` definition:

```python
# Un job launchd part au reveil du Mac, parfois avant que la resolution DNS
# reponde. L'agent `digest` echouait ainsi chaque matin depuis juillet sur un
# « failed to resolve host ... .neon.tech » : quelques essais espaces valent
# mieux qu'un run perdu, et un echec explicite vaut mieux qu'une trace psycopg.
ATTENTE_ESSAIS = 5
ATTENTE_DELAI = 6.0


class BaseInjoignable(RuntimeError):
    """Levee quand la base reste injoignable apres tous les essais."""


def _ping() -> None:
    with engine.connect() as connexion:
        connexion.execute(text("SELECT 1"))


def attendre_base(
    essais: int = ATTENTE_ESSAIS,
    delai: float = ATTENTE_DELAI,
    dormir=time.sleep,
    connecter=None,
) -> None:
    """Attend que la base reponde, ou leve BaseInjoignable.

    `connecter` et `dormir` sont injectables : les tests ne touchent donc ni la
    base ni l'horloge. C'est la seule raison pour laquelle cette fonction, qui
    n'a qu'un usage en production, en accepte deux.
    """
    connecter = connecter or _ping
    derniere: Exception | None = None
    for essai in range(1, essais + 1):
        try:
            connecter()
            return
        except OperationalError as exc:
            derniere = exc
            if essai < essais:
                logger.warning(
                    "base injoignable (essai %d/%d), nouvel essai dans %.0f s",
                    essai,
                    essais,
                    delai,
                )
                dormir(delai)
    raise BaseInjoignable(
        f"base injoignable apres {essais} essais espaces de {delai:.0f} s"
    ) from derniere
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db_attente.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire it into the two scheduled entry points**

In `scripts/run_ingest.py`, replace the body of `main()`:

```python
def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Avant toute chose : ce script est lance par launchd, parfois au reveil du
    # Mac avant que le DNS reponde.
    try:
        attendre_base()
    except BaseInjoignable as exc:
        print(f"[ERREUR] {exc}")
        raise SystemExit(1) from exc

    with SessionLocal() as db:
        all_stats = run_ingest(db)
```

and its import line:

```python
from app.db import BaseInjoignable, SessionLocal, attendre_base
```

Apply the identical treatment to `scripts/run_pipeline.py` — it runs on the GitHub runner where DNS is up, but it is also runnable by hand on a Mac that has just woken, and the cost is one call.

Note: `raise SystemExit(1)` and not a bare `return` — launchd records the exit code, and `--status` reads it. A silent success on an unreachable database would hide the failure.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS. Baseline is **249 passing**; this task adds 4.

- [ ] **Step 7: Commit**

```bash
git add app/db.py scripts/run_ingest.py scripts/run_pipeline.py tests/test_db_attente.py
git commit -m "feat(deploy): preambule reseau avant les runs planifies

L'agent launchd `digest` echoue chaque jour depuis juillet, et pas a cause
du mail : « failed to resolve host ... .neon.tech ». Il part a 8h30, souvent
juste apres le reveil du Mac, avant que la resolution DNS reponde.

L'agent d'ingestion qui arrive aura RunAtLoad et partira lui aussi au
reveil. attendre_base() fait quelques SELECT 1 espaces avant de rendre la
main, et leve une erreur lisible plutot qu'une trace psycopg.

Sortie en code 1 et non retour silencieux : launchd enregistre le code, et
c'est ce que --status lira."
```

---

## Task 2: construction des plists

Aucun effet de bord dans cette tâche : on construit des dictionnaires et on affiche un plan. C'est la partie entièrement testable.

**Files:**
- Create: `scripts/install_agents.py`
- Test: `tests/test_install_agents.py`

**Interfaces:**
- Consumes: rien.
- Produces:
  - `Chemins(depot: Path, uv: Path, logs: Path, launch_agents: Path)` (dataclass gelée)
  - `chemins_par_defaut() -> Chemins`
  - `plist_ingest(chemins) -> dict`, `plist_dashboard(chemins) -> dict`, `plists(chemins) -> dict[str, dict]`
  - `afficher_plan(chemins) -> None`
  - Constantes : `LABEL_INGEST`, `LABEL_DASHBOARD`, `LABELS_OBSOLETES`, `INTERVALLE_INGEST = 1800`, `PORT_DASHBOARD = 8000`, `THROTTLE_DASHBOARD = 60`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_install_agents.py`:

```python
"""Les agents launchd, testes sans launchctl.

`plistlib` produit des plists valides par construction ; ce qui reste a verifier
est leur CONTENU, et c'est justement ce qu'un script bash ne permettait pas.
Aucun test ici n'appelle launchctl ni n'ecrit dans ~/Library/LaunchAgents.
"""

import plistlib
from pathlib import Path

import pytest

from scripts.install_agents import (
    INTERVALLE_INGEST,
    LABEL_DASHBOARD,
    LABEL_INGEST,
    LABELS_OBSOLETES,
    PORT_DASHBOARD,
    THROTTLE_DASHBOARD,
    Chemins,
    plist_dashboard,
    plist_ingest,
    plists,
)


@pytest.fixture()
def chemins(tmp_path: Path) -> Chemins:
    return Chemins(
        depot=tmp_path / "depot",
        uv=tmp_path / "bin" / "uv",
        logs=tmp_path / "logs",
        launch_agents=tmp_path / "LaunchAgents",
    )


def test_l_agent_d_ingestion_execute_run_ingest_et_pas_run_pipeline(chemins):
    """Tout le cloisonnement cloud/Mac tient a cette ligne. `run_pipeline`
    scorerait et alerterait en double avec GitHub Actions ; `run_ingest` ne fait
    que de l'ingestion."""
    args = plist_ingest(chemins)["ProgramArguments"]

    assert "scripts.run_ingest" in args
    assert "scripts.run_pipeline" not in args


def test_l_agent_d_ingestion_a_sa_cadence_et_part_au_demarrage(chemins):
    p = plist_ingest(chemins)

    assert p["Label"] == LABEL_INGEST
    assert p["StartInterval"] == INTERVALLE_INGEST == 1800
    assert p["RunAtLoad"] is True
    assert p["WorkingDirectory"] == str(chemins.depot)


def test_le_dashboard_n_utilise_JAMAIS_reload(chemins):
    """--reload lance un processus enfant que launchd ne suit pas, et surveille
    des fichiers pour rien sur un service permanent."""
    assert "--reload" not in plist_dashboard(chemins)["ProgramArguments"]


def test_le_dashboard_n_ecoute_que_en_local(chemins):
    """Dashboard local : 0.0.0.0 ouvrirait une surface sur le reseau de la maison
    sans rien apporter."""
    args = plist_dashboard(chemins)["ProgramArguments"]

    assert "127.0.0.1" in args
    assert "0.0.0.0" not in args
    assert str(PORT_DASHBOARD) in args


def test_le_dashboard_ne_journalise_pas_chaque_requete(chemins):
    """launchd ne fait aucune rotation, et le dashboard interroge l'API en
    boucle : 43 Mo se sont deja accumules ainsi en cinq semaines."""
    args = plist_dashboard(chemins)["ProgramArguments"]

    assert "--no-access-log" in args
    assert args[args.index("--log-level") + 1] == "warning"


def test_le_dashboard_redemarre_mais_sans_boucler(chemins):
    """KeepAlive face a un port durablement occupe reessaie toutes les 10 s par
    defaut et inonde les logs."""
    p = plist_dashboard(chemins)

    assert p["KeepAlive"] is True
    assert p["ThrottleInterval"] == THROTTLE_DASHBOARD == 60


def test_les_agents_portent_un_PATH_explicite(chemins):
    """launchd ne donne qu'un PATH minimal : `uv` est appele en absolu, mais ce
    qu'il lance ensuite peut avoir besoin du reste."""
    for p in plists(chemins).values():
        chemin = p["EnvironmentVariables"]["PATH"]
        assert str(chemins.uv.parent) in chemin
        assert "/usr/bin" in chemin


def test_les_deux_agents_journalisent_dans_le_dossier_de_logs(chemins):
    for label, p in plists(chemins).items():
        for cle in ("StandardOutPath", "StandardErrorPath"):
            assert p[cle].startswith(str(chemins.logs))


def test_les_plists_sont_serialisables_et_relisibles(chemins):
    """La raison d'etre de plistlib : pas de XML ecrit a la main, donc pas de
    plist invalide que launchd refuserait en silence."""
    for p in plists(chemins).values():
        assert plistlib.loads(plistlib.dumps(p)) == p


def test_les_labels_obsoletes_sont_ceux_de_juillet():
    """Ces deux agents tournent encore. Le premier executait run_pipeline toutes
    les 30 min -- scoring et alertes comprises -- en parallele de GitHub Actions."""
    assert LABELS_OBSOLETES == ("com.musicnewsradar.pipeline", "com.musicnewsradar.digest")
    assert LABEL_INGEST not in LABELS_OBSOLETES
    assert LABEL_DASHBOARD not in LABELS_OBSOLETES
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_install_agents.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.install_agents'`

- [ ] **Step 3: Implement the module**

Create `scripts/install_agents.py`:

```python
"""Installe les agents launchd du socle local de Kinda.

Usage :
    uv run python -m scripts.install_agents              # affiche le plan
    uv run python -m scripts.install_agents --apply      # installe
    uv run python -m scripts.install_agents --status     # etat des agents
    uv run python -m scripts.install_agents --uninstall  # retire tout

Deux ordonnanceurs cohabitent, cloisonnes PAR SCRIPT et non par configuration :

  CLOUD  GitHub Actions, 2 h -> scripts.run_pipeline
         ingestion + scoring Haiku + alertes Discord + purge
         Raison d'etre : il ne dort jamais, c'est le canal temps reel.

  MAC    launchd -> scripts.run_ingest, 30 min
         ingestion SEULE : l'IP residentielle debloque Mixmag (403 Cloudflare)
         et Reddit (429), que l'IP datacenter d'Azure fait bloquer.
         + uvicorn en permanence, pour que le dashboard soit debout.

Pourquoi Python et pas bash : `plistlib` produit des plists valides par
construction et leur CONTENU devient testable sans appeler launchctl. Les
anciens `.plist` versionnes codaient /Users/theoherve/... en dur et cassaient
silencieusement si le depot bougeait.
"""

import argparse
import plistlib
import shutil
from dataclasses import dataclass
from pathlib import Path

LABEL_INGEST = "com.musicnewsradar.ingest"
LABEL_DASHBOARD = "com.musicnewsradar.dashboard"

# Agents installes le 5 juillet et JAMAIS desinstalles. `pipeline` executait
# run_pipeline toutes les 30 min -- scoring et alertes comprises -- en parallele
# de GitHub Actions ; `digest` echouait chaque jour. Les decharger est le PREMIER
# travail de ce script : sans cela le double emploi survit a l'installation.
LABELS_OBSOLETES = ("com.musicnewsradar.pipeline", "com.musicnewsradar.digest")

INTERVALLE_INGEST = 1800
PORT_DASHBOARD = 8000
# KeepAlive face a un port durablement occupe boucle : sans ce plancher, launchd
# reessaie toutes les 10 s et remplit les logs.
THROTTLE_DASHBOARD = 60

# launchd ne fournit qu'un PATH minimal. `uv` est appele en absolu, mais ce qu'il
# lance ensuite peut avoir besoin du reste.
PATH_SYSTEME = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


@dataclass(frozen=True)
class Chemins:
    """Les chemins resolus a l'installation, jamais ecrits en dur."""

    depot: Path
    uv: Path
    logs: Path
    launch_agents: Path


def chemins_par_defaut() -> Chemins:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv introuvable sur le PATH — installer uv d'abord")
    return Chemins(
        depot=Path(__file__).resolve().parent.parent,
        uv=Path(uv),
        logs=Path.home() / "Library" / "Logs" / "music-news-radar",
        launch_agents=Path.home() / "Library" / "LaunchAgents",
    )


def _commun(chemins: Chemins, label: str, base_log: str) -> dict:
    return {
        "Label": label,
        "WorkingDirectory": str(chemins.depot),
        "EnvironmentVariables": {
            "PATH": ":".join((str(chemins.uv.parent), *PATH_SYSTEME)),
            "PYTHONUNBUFFERED": "1",
        },
        "StandardOutPath": str(chemins.logs / f"{base_log}.log"),
        "StandardErrorPath": str(chemins.logs / f"{base_log}.err.log"),
    }


def plist_ingest(chemins: Chemins) -> dict:
    """Ingestion SEULE : ni scoring, ni alertes, ni purge -- tout cela reste au
    cloud. C'est a cette ligne que tient le cloisonnement."""
    return {
        **_commun(chemins, LABEL_INGEST, "ingest"),
        "ProgramArguments": [
            str(chemins.uv), "run", "python", "-m", "scripts.run_ingest",
        ],
        "StartInterval": INTERVALLE_INGEST,
        "RunAtLoad": True,
    }


def plist_dashboard(chemins: Chemins) -> dict:
    """Sans --reload : le rechargeur lance un processus enfant que launchd ne
    suit pas, et surveille des fichiers pour rien sur un service permanent."""
    return {
        **_commun(chemins, LABEL_DASHBOARD, "dashboard"),
        "ProgramArguments": [
            str(chemins.uv), "run", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(PORT_DASHBOARD),
            "--no-access-log",
            "--log-level", "warning",
        ],
        "KeepAlive": True,
        "RunAtLoad": True,
        "ThrottleInterval": THROTTLE_DASHBOARD,
    }


def plists(chemins: Chemins) -> dict[str, dict]:
    return {
        LABEL_INGEST: plist_ingest(chemins),
        LABEL_DASHBOARD: plist_dashboard(chemins),
    }


def afficher_plan(chemins: Chemins) -> None:
    print("Agents à installer :")
    for label, contenu in plists(chemins).items():
        cadence = (
            f"toutes les {contenu['StartInterval']} s"
            if "StartInterval" in contenu
            else "en permanence (KeepAlive)"
        )
        print(f"  + {label:34s} {cadence}")
        print(f"      {' '.join(contenu['ProgramArguments'])}")
    print("\nAgents à retirer (installés le 5 juillet, toujours chargés) :")
    for label in LABELS_OBSOLETES:
        print(f"  - {label}")
    print(f"\nPlists dans : {chemins.launch_agents}")
    print(f"Logs dans    : {chemins.logs}")
    print("\nRelancer avec --apply pour appliquer.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agents launchd du socle local Kinda")
    parser.add_argument("--apply", action="store_true", help="installe réellement")
    args = parser.parse_args()

    chemins = chemins_par_defaut()
    if not args.apply:
        afficher_plan(chemins)
        return
    raise SystemExit("--apply n'est pas encore implémenté (tâche 3)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_install_agents.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Check the plan output by eye**

Run: `uv run python -m scripts.install_agents`
Expected: the two agents to install with their real resolved paths, the two obsolete ones to remove, and the invitation to re-run with `--apply`. Verify the paths point at **your** repo and **your** `uv`, not at a hardcoded `/Users/theoherve/...`.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS. Baseline after Task 1 is **253**; this task adds 10.

- [ ] **Step 7: Commit**

```bash
git add scripts/install_agents.py tests/test_install_agents.py
git commit -m "feat(deploy): construction testable des plists launchd

Les trois .plist versionnes codaient /Users/theoherve/... en dur et
cassaient silencieusement si le depot bougeait. plistlib les construit
desormais a partir de chemins resolus, et leur CONTENU devient testable
sans appeler launchctl -- ce qu'un script bash ne permettait pas.

Les tests verrouillent ce qui compte et se voit mal : que l'agent local
execute run_ingest et JAMAIS run_pipeline (sinon scoring et alertes en
double avec GitHub Actions), que le dashboard n'utilise pas --reload,
n'ecoute que 127.0.0.1, ne journalise pas chaque requete, et ne boucle pas
sur un port occupe."
```

---

## Task 3: installation, statut, désinstallation

**Files:**
- Modify: `scripts/install_agents.py`
- Delete: `deploy/com.musicnewsradar.pipeline.plist`, `deploy/com.musicnewsradar.digest.plist`, `deploy/install_launchd.sh`
- Test: `tests/test_install_agents.py`

**Interfaces:**
- Consumes: `Chemins`, `plists`, `LABELS_OBSOLETES`
- Produces:
  - `installer(chemins, executer=_executer) -> None`
  - `desinstaller(chemins, executer=_executer) -> None`
  - `statut(chemins, executer=_executer) -> None`
  - `_executer(commande: list[str], tolere_echec: bool = False) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_install_agents.py`:

```python
class FauxExecuteur:
    """Enregistre les commandes au lieu de les lancer."""

    def __init__(self, sortie: str = "") -> None:
        self.commandes: list[list[str]] = []
        self.sortie = sortie

    def __call__(self, commande: list[str], tolere_echec: bool = False) -> str:
        self.commandes.append(commande)
        return self.sortie


def _labels_de(commandes: list[list[str]], verbe: str) -> list[str]:
    """Les labels vises par un `launchctl <verbe>`, dans l'ordre."""
    trouves = []
    for commande in commandes:
        if verbe in commande:
            trouves.append(commande[-1].split("/")[-1].removesuffix(".plist"))
    return trouves


def test_installer_decharge_les_obsoletes_AVANT_de_charger_les_nouveaux(chemins):
    """L'ordre n'est pas cosmetique : l'ancien agent `pipeline` tourne encore et
    execute run_pipeline. S'il survit au bootstrap du nouveau, le double emploi
    qu'on cherche a supprimer continue."""
    executeur = FauxExecuteur()

    installer(chemins, executer=executeur)

    sortis = _labels_de(executeur.commandes, "bootout")
    charges = _labels_de(executeur.commandes, "bootstrap")
    assert set(LABELS_OBSOLETES).issubset(set(sortis))
    dernier_bootout = max(
        i for i, c in enumerate(executeur.commandes) if "bootout" in c
        and c[-1].split("/")[-1] in LABELS_OBSOLETES
    )
    premier_bootstrap = min(i for i, c in enumerate(executeur.commandes) if "bootstrap" in c)
    assert dernier_bootout < premier_bootstrap
    assert set(charges) == {LABEL_INGEST, LABEL_DASHBOARD}


def test_installer_ecrit_les_plists_et_cree_le_dossier_de_logs(chemins):
    installer(chemins, executer=FauxExecuteur())

    assert chemins.logs.is_dir()
    for label in (LABEL_INGEST, LABEL_DASHBOARD):
        fichier = chemins.launch_agents / f"{label}.plist"
        assert plistlib.loads(fichier.read_bytes())["Label"] == label


def test_installer_supprime_les_plists_obsoletes_du_dossier_systeme(chemins):
    """Decharger ne suffit pas : un plist laisse dans LaunchAgents est rechargé
    a la prochaine ouverture de session."""
    chemins.launch_agents.mkdir(parents=True)
    for label in LABELS_OBSOLETES:
        (chemins.launch_agents / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label}))

    installer(chemins, executer=FauxExecuteur())

    for label in LABELS_OBSOLETES:
        assert not (chemins.launch_agents / f"{label}.plist").exists()


def test_desinstaller_retire_les_deux_agents_et_leurs_plists(chemins):
    executeur = FauxExecuteur()
    installer(chemins, executer=executeur)
    executeur.commandes.clear()

    desinstaller(chemins, executer=executeur)

    assert set(_labels_de(executeur.commandes, "bootout")) == {LABEL_INGEST, LABEL_DASHBOARD}
    for label in (LABEL_INGEST, LABEL_DASHBOARD):
        assert not (chemins.launch_agents / f"{label}.plist").exists()


def test_installer_est_idempotent(chemins):
    """On doit pouvoir relancer --apply apres avoir change une valeur, sans
    empiler deux agents ni tomber sur « service already loaded »."""
    installer(chemins, executer=FauxExecuteur())
    installer(chemins, executer=FauxExecuteur())

    for label in (LABEL_INGEST, LABEL_DASHBOARD):
        assert (chemins.launch_agents / f"{label}.plist").exists()
```

Add `desinstaller`, `installer` to the module imports at the top of the test file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_install_agents.py -v`
Expected: FAIL — `ImportError: cannot import name 'installer'`

- [ ] **Step 3: Implement**

Add to `scripts/install_agents.py`:

```python
import os
import subprocess


def _executer(commande: list[str], tolere_echec: bool = False) -> str:
    """Lance une commande. `tolere_echec` sert aux `bootout` d'agents qui ne sont
    peut-etre pas charges -- launchctl y repond par un code non nul, et ce n'est
    pas une erreur ici."""
    resultat = subprocess.run(commande, capture_output=True, text=True)
    if resultat.returncode != 0 and not tolere_echec:
        raise SystemExit(
            f"echec de {' '.join(commande)} :\n{resultat.stderr.strip()}"
        )
    return resultat.stdout


def _domaine() -> str:
    return f"gui/{os.getuid()}"


def _bootout(label: str, executer) -> None:
    executer(["launchctl", "bootout", f"{_domaine()}/{label}"], tolere_echec=True)


def _retirer_obsoletes(chemins: Chemins, executer) -> None:
    """Le PREMIER travail de l'installation. Decharger ne suffit pas : un plist
    laisse dans LaunchAgents est recharge a la prochaine ouverture de session."""
    for label in LABELS_OBSOLETES:
        _bootout(label, executer)
        fichier = chemins.launch_agents / f"{label}.plist"
        fichier.unlink(missing_ok=True)
        print(f"  - retiré : {label}")


def installer(chemins: Chemins, executer=_executer) -> None:
    chemins.launch_agents.mkdir(parents=True, exist_ok=True)
    chemins.logs.mkdir(parents=True, exist_ok=True)

    _retirer_obsoletes(chemins, executer)

    for label, contenu in plists(chemins).items():
        fichier = chemins.launch_agents / f"{label}.plist"
        # bootout avant bootstrap : sinon launchctl refuse un service deja
        # charge, et --apply ne serait pas rejouable.
        _bootout(label, executer)
        fichier.write_bytes(plistlib.dumps(contenu))
        executer(["launchctl", "bootstrap", _domaine(), str(fichier)])
        print(f"  + installé : {label}")


def desinstaller(chemins: Chemins, executer=_executer) -> None:
    for label in (LABEL_INGEST, LABEL_DASHBOARD):
        _bootout(label, executer)
        (chemins.launch_agents / f"{label}.plist").unlink(missing_ok=True)
        print(f"  - retiré : {label}")


def statut(chemins: Chemins, executer=_executer) -> None:
    sortie = executer(["launchctl", "list"], tolere_echec=True)
    lignes = {
        colonnes[2]: colonnes
        for colonnes in (ligne.split("\t") for ligne in sortie.splitlines())
        if len(colonnes) == 3
    }
    print("Agents du socle :")
    for label in (LABEL_INGEST, LABEL_DASHBOARD):
        colonnes = lignes.get(label)
        if colonnes is None:
            print(f"  {label:34s} absent")
        else:
            pid, code, _ = colonnes
            etat = "en cours" if pid != "-" else f"au repos (dernier code {code})"
            print(f"  {label:34s} {etat}")
    survivants = [label for label in LABELS_OBSOLETES if label in lignes]
    if survivants:
        print("\nAgents obsolètes encore chargés (relancer --apply) :")
        for label in survivants:
            print(f"  ! {label}")
```

And extend `main()`:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description="Agents launchd du socle local Kinda")
    groupe = parser.add_mutually_exclusive_group()
    groupe.add_argument("--apply", action="store_true", help="installe réellement")
    groupe.add_argument("--status", action="store_true", help="état des agents")
    groupe.add_argument("--uninstall", action="store_true", help="retire les agents")
    args = parser.parse_args()

    chemins = chemins_par_defaut()
    if args.status:
        statut(chemins)
    elif args.uninstall:
        desinstaller(chemins)
    elif args.apply:
        installer(chemins)
    else:
        afficher_plan(chemins)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_install_agents.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Delete the superseded files**

```bash
git rm deploy/com.musicnewsradar.pipeline.plist deploy/com.musicnewsradar.digest.plist deploy/install_launchd.sh
```

The `deploy/` directory becomes empty; let git drop it.

- [ ] **Step 6: Check `--status` against reality**

Run: `uv run python -m scripts.install_agents --status`
Expected: the two new agents reported `absent`, and a warning naming `com.musicnewsradar.pipeline` and `com.musicnewsradar.digest` as still loaded — because they are. This is a read-only command; it changes nothing.

If the two obsolete agents are **not** reported, `launchctl list`'s output format differs from what `statut` parses — fix the parsing, do not adjust the expectation.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A scripts/install_agents.py tests/test_install_agents.py deploy/
git commit -m "feat(deploy): installation, statut et desinstallation des agents

Retirer les deux agents de juillet est le PREMIER travail de --apply, et
l'ordre est verrouille par un test : l'ancien agent `pipeline` tourne
encore et execute run_pipeline toutes les 30 min. S'il survit au bootstrap
du nouveau, le double emploi qu'on cherche a supprimer continue.

Decharger ne suffit pas : un plist laisse dans LaunchAgents est recharge a
la prochaine ouverture de session, d'ou la suppression des fichiers.

--apply est rejouable (bootout avant chaque bootstrap), et --status signale
les agents obsoletes encore charges.

Supprime les trois fichiers de deploy/ que ce script remplace."
```

---

## Task 4: rotation des logs

43 Mo se sont accumulés en cinq semaines sans rotation, et un uvicorn permanent va s'y ajouter.

**Files:**
- Modify: `scripts/install_agents.py`
- Test: `tests/test_install_agents.py`

**Interfaces:**
- Produces:
  - `CONF_ROTATION: Path` (`/etc/newsyslog.d/music-news-radar.conf`)
  - `conf_rotation(chemins) -> str`
  - `tronquer_logs(chemins) -> int` (rend le nombre d'octets libérés)
  - `afficher_rotation(chemins) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_install_agents.py`:

```python
def test_la_conf_de_rotation_cite_chaque_fichier_de_log(chemins):
    """newsyslog n'etend pas les jokers dans le champ du nom de fichier : il faut
    lister les quatre fichiers, sinon la rotation ne s'applique a aucun."""
    conf = conf_rotation(chemins)

    for base in ("ingest", "dashboard"):
        for suffixe in (".log", ".err.log"):
            assert str(chemins.logs / f"{base}{suffixe}") in conf


def test_la_conf_de_rotation_plafonne_la_taille(chemins):
    conf = conf_rotation(chemins)
    ligne = next(l for l in conf.splitlines() if not l.startswith("#") and l.strip())
    champs = ligne.split()

    # nom, proprietaire:groupe, mode, generations, taille en Ko, quand, drapeaux
    assert champs[3] == "5"        # 5 generations conservees
    assert champs[4] == "5000"     # 5 Mo
    assert "J" in champs[6]        # compression bzip2


def test_tronquer_les_logs_libere_la_place_sans_supprimer_les_fichiers(chemins, tmp_path):
    """Tronquer et non supprimer : launchd tient un descripteur ouvert sur ces
    fichiers, et le supprimer sous ses pieds ferait ecrire dans le vide."""
    chemins.logs.mkdir(parents=True)
    gros = chemins.logs / "ingest.err.log"
    gros.write_text("x" * 4096)

    libere = tronquer_logs(chemins)

    assert libere == 4096
    assert gros.exists()
    assert gros.stat().st_size == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_install_agents.py -v`
Expected: FAIL — `ImportError: cannot import name 'conf_rotation'`

- [ ] **Step 3: Implement**

Add to `scripts/install_agents.py`:

```python
# newsyslog est la rotation native de macOS. Sa configuration vit dans /etc, donc
# hors de portee de ce script sans sudo -- on affiche le fichier et la commande
# plutot que de reclamer les droits administrateur pour trois lignes.
CONF_ROTATION = Path("/etc/newsyslog.d/music-news-radar.conf")
ROTATION_GENERATIONS = 5
ROTATION_TAILLE_KO = 5000
BASES_LOG = ("ingest", "dashboard")


def _fichiers_log(chemins: Chemins) -> list[Path]:
    return [
        chemins.logs / f"{base}{suffixe}"
        for base in BASES_LOG
        for suffixe in (".log", ".err.log")
    ]


def conf_rotation(chemins: Chemins) -> str:
    """Une ligne par fichier : newsyslog n'etend PAS les jokers dans le champ du
    nom, un `*.log` ne ferait donc tourner aucun fichier."""
    proprietaire = f"{Path.home().name}:staff"
    lignes = [
        "# Rotation des logs du socle local Kinda.",
        "# Sans elle : 43 Mo accumules en cinq semaines, et un uvicorn permanent",
        "# qui s'y ajoute. Installe par scripts/install_agents.py (voir sa sortie).",
        "# fichier                                   proprietaire mode nb taille quand drapeaux",
    ]
    for fichier in _fichiers_log(chemins):
        lignes.append(
            f"{fichier} {proprietaire} 644 "
            f"{ROTATION_GENERATIONS} {ROTATION_TAILLE_KO} * J"
        )
    return "\n".join(lignes) + "\n"


def tronquer_logs(chemins: Chemins) -> int:
    """Vide les logs existants et rend le nombre d'octets liberes.

    Tronquer et non supprimer : launchd tient un descripteur ouvert sur ces
    fichiers, et les supprimer sous ses pieds ferait ecrire dans le vide.
    """
    libere = 0
    if not chemins.logs.is_dir():
        return 0
    for fichier in chemins.logs.glob("*.log"):
        libere += fichier.stat().st_size
        with fichier.open("w"):
            pass
    return libere


def afficher_rotation(chemins: Chemins) -> None:
    if CONF_ROTATION.exists():
        print(f"\nRotation des logs : déjà configurée ({CONF_ROTATION}).")
        return
    print(
        f"\nRotation des logs : NON configurée. À lancer une fois, "
        f"{CONF_ROTATION.parent} demandant sudo :\n"
    )
    print(f"  sudo tee {CONF_ROTATION} > /dev/null <<'EOF'")
    print(conf_rotation(chemins), end="")
    print("EOF")
```

Wire it in: `installer()` calls `tronquer_logs()` (reporting the freed bytes) then `afficher_rotation()` at the end; `statut()` calls `afficher_rotation()` too; `afficher_plan()` mentions that `--apply` will truncate the logs and how many bytes that frees.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_install_agents.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Check the emitted configuration by eye**

Run: `uv run python -m scripts.install_agents`
Expected: the plan, plus the `sudo tee` block with four lines naming your real log files. **Do not run the sudo command yet** — Task 5 covers installation with the user's consent.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/install_agents.py tests/test_install_agents.py
git commit -m "feat(deploy): rotation des logs et troncature a l'installation

43 Mo se sont accumules en cinq semaines sans rotation, et un uvicorn
permanent allait s'y ajouter. La configuration passe par newsyslog, natif
macOS.

Une ligne par fichier et non un joker : newsyslog n'etend PAS les jokers
dans le champ du nom, un *.log ne ferait tourner aucun fichier.

/etc/newsyslog.d demandant sudo, le script affiche le fichier et la
commande plutot que de reclamer les droits administrateur pour trois
lignes ; --status dit si la rotation est en place.

--apply tronque les logs existants au lieu de les supprimer : launchd tient
un descripteur ouvert dessus."
```

---

## Task 5: documentation, puis installation réelle

**Files:**
- Modify: `CLAUDE.md`
- Test: aucun (documentation et opération manuelle)

- [ ] **Step 1: Correct the two false statements in `CLAUDE.md`**

**(a)** In the « Architecture » section, this line is factually wrong:

> `run_ingest.py` (fréquent, déclenche aussi les alertes hot au moment de l'ingestion)

`run_ingest` performs ingestion only — no scoring, no alerts, no purge. The entire cloud/Mac partition of this chantier rests on that fact. Correct it, and say which entry point *does* trigger the alerts (`run_pipeline`, via `app/pipeline/run.py`).

**(b)** The « Commandes » section presents GitHub Actions as the sole scheduler and the launchd agents as an « Ancienne alternative locale ». That is now the opposite of the truth. Rewrite it to describe **two schedulers, cloisonnés par script**, with:

- the cloud: `run_pipeline` every 2 h — ingestion, scoring, alerts, purge — and why it is kept (it never sleeps; it is the real-time channel);
- the Mac: `run_ingest` every 30 min — ingestion only — and why (the residential IP unblocks Mixmag and Reddit, which the Azure datacenter IP gets blocked from), plus the permanent uvicorn agent;
- the commands: `uv run python -m scripts.install_agents [--apply|--status|--uninstall]`;
- the port conflict: the agent holds 8000, so `--uninstall` or a `launchctl bootout` is needed before a manual `uvicorn --reload`.

Keep the file's existing voice and accented documentation style. Do not restructure sections beyond these two.

- [ ] **Step 2: Add a note about the scheduled entry points' network preamble**

In the « Contraintes apprises sur le terrain » section, add the launchd-at-wake trap: a job started by `RunAtLoad` or woken by `StartInterval` can run before DNS resolves, which made the `digest` agent fail every morning for weeks; `attendre_base()` in `app/db.py` is the guard, and any new scheduled entry point must call it.

- [ ] **Step 3: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 4: Commit the documentation**

```bash
git add CLAUDE.md
git commit -m "docs: deux ordonnanceurs cloisonnes, et deux corrections factuelles

CLAUDE.md affirmait que run_ingest declenche les alertes hot. C'est faux --
run_ingest ne fait que de l'ingestion -- et tout le cloisonnement
cloud/Mac de ce chantier repose sur ce fait.

La section scheduler presentait GitHub Actions comme seul ordonnanceur et
les agents launchd comme une « ancienne alternative locale » : c'est
l'inverse de la cible.

Ajoute le piege launchd-au-reveil dans les contraintes de terrain."
```

- [ ] **Step 5: Ask the user before installing**

**Stop here and ask.** `--apply` unloads two of their session agents, writes into `~/Library/LaunchAgents`, truncates 43 MB of their logs, and starts a permanent server on port 8000. None of that is reversible by a `git revert`.

Show them the output of `uv run python -m scripts.install_agents` (the plan) and ask for explicit confirmation before running `--apply`.

- [ ] **Step 6: Install, once they have agreed**

```bash
uv run python -m scripts.install_agents --apply
uv run python -m scripts.install_agents --status
```

Expected from `--status`: both new agents present, `com.musicnewsradar.ingest` at rest with last exit code 0 (it runs immediately thanks to `RunAtLoad`), `com.musicnewsradar.dashboard` running, and no obsolete agent left.

Then the real check, which no test can make:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
```

Expected: `200`.

- [ ] **Step 7: Hand the rotation command to the user**

The `sudo tee` block printed by `--status` must be run by them — the agent cannot take sudo. Suggest they paste it into this session prefixed with `!`, which runs it here and puts the output in the conversation.

Then confirm: `uv run python -m scripts.install_agents --status` must now report the rotation as configured.

- [ ] **Step 8: Prescribe the one verification that requires a logout**

`RunAtLoad` and the agents' start at session opening cannot be verified without logging out and back in. Tell the user plainly:

> Après ta prochaine déconnexion/reconnexion (ou redémarrage), relance
> `uv run python -m scripts.install_agents --status` : les deux agents doivent
> être là sans que tu aies rien fait. C'est la seule preuve que le socle démarre
> tout seul.

Do not report this step as done. It is theirs, and it happens later.

---

## Self-Review

**Spec coverage:**

| Exigence de la spec | Tâche |
|---|---|
| Cloisonnement par script (cloud = `run_pipeline`, Mac = `run_ingest`) | 2 (verrouillé par test), 5 (documenté) |
| Agent d'ingestion 30 min, `RunAtLoad` | 2 |
| Agent dashboard permanent, sans `--reload`, `127.0.0.1`, `--no-access-log`, `KeepAlive`, `ThrottleInterval` | 2 |
| `PATH` explicite pour launchd | 2 |
| Préambule réseau | 1 |
| Suppression des agents de juillet, en premier | 3 |
| Suppression de leurs plists dans `~/Library/LaunchAgents` | 3 |
| `--apply` / `--status` / `--uninstall`, idiome du projet | 2 et 3 |
| Plists générés, plus de chemins en dur versionnés | 2, 3 (suppression des fichiers) |
| Rotation des logs + troncature | 4 |
| Friction `sudo` assumée et documentée | 4, 5 |
| Conflit de port | 3 (`--status`), 5 (documenté) |
| Corrections de `CLAUDE.md` | 5 |
| Vérification manuelle à la reconnexion | 5 |
| LM Studio non lancé | rien à faire — hors périmètre, acté dans la spec |

**Hors périmètre, conformément à la spec :** modèle local (C), file quotidienne et feedback (B), composition automatique, facturation X, empêcher la veille.

**Placeholder scan:** aucune étape ne dit « à ajuster ». Les deux points non déterministes sont l'installation réelle (tâche 5, sous accord de l'utilisateur) et la vérification à la reconnexion, tous deux avec leur critère de succès explicite.

**Type consistency:** `Chemins(depot, uv, logs, launch_agents)` partout ; `plists() -> dict[str, dict]` indexé par label ; `installer/desinstaller/statut(chemins, executer=...)` ont la même signature ; `_executer(commande, tolere_echec=False) -> str` est la seule forme que `FauxExecuteur` doit imiter.

**Point de vigilance pour l'exécutant :** la tâche 3 ajoute des fonctions au module créé en tâche 2 et supprime trois fichiers de `deploy/`. Les imports du fichier de test doivent grandir avec le module — `installer`, `desinstaller`, `conf_rotation`, `tronquer_logs` s'ajoutent au fur et à mesure, sinon la suite est rouge entre deux commits.
