"""Installe les agents launchd du socle local de Kinda.

Usage :
    uv run python -m scripts.install_agents            # affiche le plan
    uv run python -m scripts.install_agents --apply    # installe (tache suivante)

UN SEUL ordonnanceur depuis le passage au tout-local : launchd, sur le Mac.

  com.musicnewsradar.pipeline   scripts.run_pipeline, toutes les heures
        ingestion + dedup + scoring Haiku + mentions + purge.
        GitHub Actions ne planifie plus rien : sa seule raison d'etre etait
        l'alerte Discord temps reel (un runner ne dort jamais), et les alertes
        sont coupees. Gain immediat : depuis l'IP residentielle, Mixmag ne
        renvoie plus 403 (Cloudflare) ni Reddit 429 -- ces blocages etaient
        documentes depuis les runners Azure.

  com.musicnewsradar.dashboard  uvicorn en permanence, pour que le dashboard
        soit debout sur localhost:8000.

  com.musicnewsradar.publication  scripts.run_publication, toutes les 10 min
        la file de publication Instagram. Sort immediatement en mode `arret`,
        le defaut (`PUBLICATION_MODE` dans .env).

  com.musicnewsradar.veille     scripts.run_veille, toutes les 5 minutes
        la VEILLE RAPIDE : les douze comptes de `VEILLE_COMPTES` seulement,
        ingeres, regroupes et scores par petits lots. Le reste des ~123 comptes
        continue de passer au rythme horaire du pipeline.

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
import time
from dataclasses import dataclass
from pathlib import Path

# Le label reprend le nom `pipeline` -- celui de l'agent de juillet, retire en
# aout puis ressuscite ici -- parce que c'est de nouveau exactement ce qu'il
# fait. Le renommage depuis `ingest` n'est donc pas cosmetique : c'est LE piege
# de ce changement. Un agent launchd est identifie par son label, pas par son
# contenu ; laisser `com.musicnewsradar.ingest` charge a cote du nouveau ferait
# tourner DEUX ordonnanceurs et doublerait toute l'ingestion. D'ou son passage
# dans LABELS_OBSOLETES ci-dessous, traite par --apply comme par --uninstall.
LABEL_PIPELINE = "com.musicnewsradar.pipeline"
LABEL_DASHBOARD = "com.musicnewsradar.dashboard"
LABEL_PUBLICATION = "com.musicnewsradar.publication"
LABEL_VEILLE = "com.musicnewsradar.veille"

# Agents a decharger : `ingest` est l'agent d'ingestion seule du socle d'aout,
# remplace par celui-ci ; `digest` date du 5 juillet et echouait chaque jour.
# Les retirer est le PREMIER travail de ce script -- sans cela le double emploi
# survit a l'installation.
LABELS_OBSOLETES = ("com.musicnewsradar.ingest", "com.musicnewsradar.digest")

# Une heure. Le run score desormais a chaque passage (appels Claude payes) et
# plus rien n'a besoin d'etre temps reel : les alertes Discord sont coupees.
INTERVALLE_PIPELINE = 3600

# Dix minutes : c'est la precision de l'heure de publication. L'agent ne coute
# rien a ce rythme -- il ne score pas, et ne compose qu'a J-1.
INTERVALLE_PUBLICATION = 600

# Cinq minutes : la cadence de la VEILLE RAPIDE. Elle ne lit que les comptes de
# `VEILLE_COMPTES` (douze), soit 144 appels Graph par heure -- loin des ~430/h qui
# ont rendu le code 4 le 2026-09-17, et le seul moyen mesure de passer de 141 min
# de latence a quelques minutes sur les comptes qui comptent.
INTERVALLE_VEILLE = 300
PORT_DASHBOARD = 8000
# KeepAlive face a un port durablement occupe boucle : sans ce plancher, launchd
# reessaie toutes les 10 s et remplit les logs.
THROTTLE_DASHBOARD = 60

# launchd ne fournit qu'un PATH minimal. `uv` est appele en absolu, mais ce qu'il
# lance ensuite peut avoir besoin du reste.
PATH_SYSTEME = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

ROTATION_GENERATIONS = 5
ROTATION_TAILLE_KO = 5000
BASES_LOG = ("pipeline", "dashboard", "publication", "veille")


@dataclass(frozen=True)
class Chemins:
    """Les chemins resolus a l'installation, jamais ecrits en dur."""

    depot: Path
    uv: Path
    logs: Path
    launch_agents: Path
    # newsyslog est la rotation native de macOS. Sa configuration vit dans /etc,
    # donc hors de portee de ce script sans sudo -- on affiche le fichier et la
    # commande plutot que de reclamer les droits administrateur pour trois
    # lignes. Champ plutot que constante module : les tests visent un tmp_path,
    # jamais le vrai /etc, sinon `.exists()` lit l'hote et deverse la conf de
    # rotation dans stdout des qu'elle est absente -- ce qui a fait passer
    # l'assertion sur le code de sortie de statut() a vide (voir tests).
    conf_rotation: Path


def chemins_par_defaut() -> Chemins:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv introuvable sur le PATH — installer uv d'abord")
    return Chemins(
        depot=Path(__file__).resolve().parent.parent,
        uv=Path(uv),
        logs=Path.home() / "Library" / "Logs" / "music-news-radar",
        launch_agents=Path.home() / "Library" / "LaunchAgents",
        conf_rotation=Path("/etc/newsyslog.d/music-news-radar.conf"),
    )


def _fichiers_log(chemins: Chemins) -> list[Path]:
    return [
        chemins.logs / f"{base}{suffixe}"
        for base in BASES_LOG
        for suffixe in (".log", ".err.log")
    ]


def conf_rotation(chemins: Chemins) -> str:
    """Une ligne par fichier : newsyslog n'etend PAS les jokers dans le champ du
    nom, un `*.log` ne ferait donc tourner aucun fichier.

    Angle mort : newsyslog renomme et compresse le fichier ; le dashboard tourne
    en KeepAlive et garde son descripteur StandardOutPath ouvert pour toute sa
    vie, donc apres une rotation son flux continue d'ecrire dans l'inode renomme,
    invisible sous le nom `dashboard.log`. L'agent d'ingestion est immunise : un
    nouveau processus toutes les 30 min rouvre le chemin a chaque fois. Impact
    pratique faible (--no-access-log + --log-level warning font que le
    dashboard atteint rarement 5 Mo) -- ne pas redesigner pour ca, juste eviter
    qu'un futur lecteur perde une apres-midi sur "pourquoi dashboard.log est vide
    apres une rotation".
    """
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
            # JN : J compresse en bzip2 ; N dit qu'aucun processus ne doit etre
            # signale a la rotation. Sans N, newsyslog envoie un SIGHUP a
            # syslogd(8) par defaut (man 5 newsyslog.conf, champ
            # path_to_pid_file) -- un daemon systeme sans aucun rapport avec ce
            # projet, reveille a chaque rotation de chacun des quatre fichiers.
            f"{ROTATION_GENERATIONS} {ROTATION_TAILLE_KO} * JN"
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
    """On compare le CONTENU, pas la seule existence du fichier : le passage de
    `ingest.log` a `pipeline.log` a rendu caduque la conf deja posee, qui fait
    tourner un fichier que plus rien n'ecrit. Dire « déjà configurée » sur la
    foi d'un `.exists()` reproduirait le silence de newsyslog face a un joker."""
    attendue = conf_rotation(chemins)
    if chemins.conf_rotation.exists():
        if chemins.conf_rotation.read_text() == attendue:
            print(f"\nRotation des logs : déjà configurée ({chemins.conf_rotation}).")
            return
        etat = "obsolète (les noms de logs ont changé)"
    else:
        etat = "NON configurée"
    print(
        f"\nRotation des logs : {etat}. "
        f"{chemins.conf_rotation.parent} exige sudo — à lancer une fois :\n"
    )
    print(f"  sudo tee {chemins.conf_rotation} > /dev/null <<'EOF'")
    print(attendue, end="")
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


def plist_pipeline(chemins: Chemins) -> dict:
    """Le pipeline COMPLET : ingestion + dedup + scoring + mentions + purge.
    Plus rien ne tourne dans le cloud, donc un `run_ingest` ici laisserait les
    articles ingeres sans jamais les scorer."""
    return {
        **_commun(chemins, LABEL_PIPELINE, "pipeline"),
        "ProgramArguments": [
            str(chemins.uv), "run", "python", "-m", "scripts.run_pipeline",
        ],
        "StartInterval": INTERVALLE_PIPELINE,
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


def plist_publication(chemins: Chemins) -> dict:
    """La file de publication. En mode `arret` (defaut), le script sort
    immediatement : l'installer ne publie rien tant que l'utilisateur n'a pas
    choisi `apercu` ou `reel` dans .env."""
    return {
        **_commun(chemins, LABEL_PUBLICATION, "publication"),
        "ProgramArguments": [
            str(chemins.uv), "run", "python", "-m", "scripts.run_publication",
        ],
        "StartInterval": INTERVALLE_PUBLICATION,
        "RunAtLoad": True,
    }


def plist_veille(chemins: Chemins) -> dict:
    """La veille rapide : `scripts.run_veille`, toutes les 5 minutes.

    Il n'ingere QUE les comptes surveilles et ne score que ce qu'il vient de
    ramener : ni illustration ni purge, qui restent au passage horaire. Sans
    liste de veille configuree, il ne trouve aucune source et ne coute rien.
    """
    return {
        **_commun(chemins, LABEL_VEILLE, "veille"),
        "ProgramArguments": [
            str(chemins.uv), "run", "python", "-m", "scripts.run_veille",
        ],
        "StartInterval": INTERVALLE_VEILLE,
        "RunAtLoad": True,
    }


def plists(chemins: Chemins) -> dict[str, dict]:
    return {
        LABEL_PIPELINE: plist_pipeline(chemins),
        LABEL_DASHBOARD: plist_dashboard(chemins),
        LABEL_PUBLICATION: plist_publication(chemins),
        LABEL_VEILLE: plist_veille(chemins),
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
    print("\nAgents à retirer (anciens labels, sinon ingestion en double) :")
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


# launchd decharge de facon ASYNCHRONE : `bootout` rend la main avant que le
# service soit reellement demonte. Le `bootstrap` qui suit echoue alors en
# « Bootstrap failed: 5: Input/output error » -- et comme l'ancien service est
# deja deconnecte, --apply laisse le service MORT. Observe le 2026-08-18 sur
# l'agent dashboard : localhost:8000 ne repondait plus apres un --apply reussi
# par ailleurs. On attend donc que `launchctl print` ne reponde plus.
ATTENTE_ESSAIS = 20
ATTENTE_PAUSE = 0.5


def _dormir(secondes: float) -> None:
    """Isole la pause pour que les tests n'attendent pas vraiment."""
    time.sleep(secondes)


def _attendre_dechargement(label: str, executer) -> None:
    """Bloque tant que launchd connait encore ce label.

    `launchctl print` repond tant que le service existe ; sa sortie devient vide
    une fois le demontage termine. On se fie a la SORTIE et non au code de
    retour, que `_executer` n'expose pas.
    """
    for _ in range(ATTENTE_ESSAIS):
        sortie = executer(["launchctl", "print", f"{_domaine()}/{label}"], tolere_echec=True)
        if not sortie.strip():
            return
        _dormir(ATTENTE_PAUSE)


def _bootout(label: str, executer) -> None:
    executer(["launchctl", "bootout", f"{_domaine()}/{label}"], tolere_echec=True)
    _attendre_dechargement(label, executer)


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

    # AVANT le bootstrap : l'agent du pipeline a RunAtLoad, donc bootstrap le
    # fait ecrire dans pipeline.log immediatement. Tronquer apres aurait jete ce
    # premier run et compte ses octets comme "liberes" alors qu'ils venaient
    # d'etre ecrits par l'installation elle-meme.
    libere = tronquer_logs(chemins)

    _retirer_obsoletes(chemins, executer)

    for label, contenu in plists(chemins).items():
        fichier = chemins.launch_agents / f"{label}.plist"
        # bootout avant bootstrap : sinon launchctl refuse un service deja
        # charge, et --apply ne serait pas rejouable.
        _bootout(label, executer)
        fichier.write_bytes(plistlib.dumps(contenu))
        executer(["launchctl", "bootstrap", _domaine(), str(fichier)])
        print(f"  + installé : {label}")

    if libere:
        print(f"\nLogs existants tronqués ({libere:,} octets libérés).")
    afficher_rotation(chemins)


def desinstaller(chemins: Chemins, executer=_executer) -> None:
    """Les labels obsoletes sont retires ici AUSSI : quelqu'un qui desinstalle
    sans avoir relance --apply depuis le passage au tout-local garderait sinon
    l'ancien `com.musicnewsradar.ingest` charge, en croyant tout avoir retire."""
    for label in (*plists(chemins), *LABELS_OBSOLETES):
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
    for label in plists(chemins):
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
