"""Installe les agents launchd du socle local de Kinda.

Usage :
    uv run python -m scripts.install_agents            # affiche le plan
    uv run python -m scripts.install_agents --apply    # installe (tache suivante)

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
import os
import plistlib
import shutil
import subprocess
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

# newsyslog est la rotation native de macOS. Sa configuration vit dans /etc, donc
# hors de portee de ce script sans sudo -- on affiche le fichier et la commande
# plutot que de reclamer les droits administrateur pour trois lignes.
CONF_ROTATION = Path("/etc/newsyslog.d/music-news-radar.conf")
ROTATION_GENERATIONS = 5
ROTATION_TAILLE_KO = 5000
BASES_LOG = ("ingest", "dashboard")


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
            # 127.0.0.1, jamais 0.0.0.0 : dashboard local, exposer au reseau de
            # la maison n'apporte rien et ouvre une surface pour rien.
            "--host", "127.0.0.1",
            "--port", str(PORT_DASHBOARD),
            # launchd ne fait aucune rotation de logs, et le dashboard interroge
            # sa propre API en boucle : sans ces deux flags, 43 Mo se sont deja
            # accumules ainsi en cinq semaines.
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
    a_liberer = 0
    if chemins.logs.is_dir():
        a_liberer = sum(f.stat().st_size for f in chemins.logs.glob("*.log"))
    if a_liberer:
        print(f"\n--apply tronquera les logs existants ({a_liberer:,} octets à libérer).")
    print("\nRelancer avec --apply pour appliquer.")
    afficher_rotation(chemins)


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


def _supprimer_plist(fichier: Path, label: str) -> None:
    """N'affiche « retiré » que si quelque chose a reellement disparu : sur un
    re-run propre, l'agent est deja absent et le dire quand meme induirait en
    erreur la personne qui lit cette sortie pour savoir si la migration a fait
    quelque chose."""
    try:
        fichier.unlink()
        print(f"  - retiré : {label}")
    except FileNotFoundError:
        print(f"  - déjà absent : {label}")


def _retirer_obsoletes(chemins: Chemins, executer) -> None:
    """Le PREMIER travail de l'installation. Decharger ne suffit pas : un plist
    laisse dans LaunchAgents est recharge a la prochaine ouverture de session."""
    for label in LABELS_OBSOLETES:
        _bootout(label, executer)
        _supprimer_plist(chemins.launch_agents / f"{label}.plist", label)


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

    libere = tronquer_logs(chemins)
    if libere:
        print(f"\nLogs existants tronqués ({libere:,} octets libérés).")
    afficher_rotation(chemins)


def desinstaller(chemins: Chemins, executer=_executer) -> None:
    for label in (LABEL_INGEST, LABEL_DASHBOARD):
        _bootout(label, executer)
        _supprimer_plist(chemins.launch_agents / f"{label}.plist", label)


def statut(chemins: Chemins, executer=_executer) -> None:
    # Pas de tolere_echec ici : contrairement a bootout, il n'y a aucun cas ou
    # `launchctl list` echoue legitimement. Si ca echoue, on veut le savoir --
    # taire l'echec afficherait "tout est absent" a la place de la verite.
    sortie = executer(["launchctl", "list"])
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
    afficher_rotation(chemins)


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


if __name__ == "__main__":
    main()
