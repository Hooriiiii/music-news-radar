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
    chemins_par_defaut,
    desinstaller,
    installer,
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


def test_chemins_par_defaut_resout_la_racine_du_vrai_depot():
    """Seul endroit avec de l'arithmetique de chemins (`__file__` remonte de deux
    crans) : un decalage d'un cran donnerait aux agents le mauvais
    WorkingDirectory, exactement ce que generer les plists doit eliminer. Pas de
    chemin absolu code en dur ici : on verifie la forme d'un vrai depot."""
    depot = chemins_par_defaut().depot

    assert (depot / "pyproject.toml").is_file()
    assert (depot / "scripts").is_dir()


def test_chemins_par_defaut_echoue_fort_si_uv_est_absent(monkeypatch):
    """Un `None` silencieux finirait serialise dans un plist comme la chaine
    "None", et launchd echouerait au chargement avec quelque chose d'inutile."""
    monkeypatch.setattr("scripts.install_agents.shutil.which", lambda _: None)

    with pytest.raises(SystemExit, match="uv"):
        chemins_par_defaut()


def test_les_labels_obsoletes_sont_ceux_de_juillet():
    """Ces deux agents tournent encore. Le premier executait run_pipeline toutes
    les 30 min -- scoring et alertes comprises -- en parallele de GitHub Actions."""
    assert LABELS_OBSOLETES == ("com.musicnewsradar.pipeline", "com.musicnewsradar.digest")
    assert LABEL_INGEST not in LABELS_OBSOLETES
    assert LABEL_DASHBOARD not in LABELS_OBSOLETES


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
