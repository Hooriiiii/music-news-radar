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
