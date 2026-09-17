"""Le mode de publication choisi depuis le dashboard.

Trois modes (voir `app/config.py::publication_mode`) : `arret`, `apercu`, `reel`.
Choisir un mode publie public et irreversible (`reel`) est une decision de
l'utilisateur, pas un reglage fige au demarrage -- le dashboard doit pouvoir le
changer sans toucher `.env` ni relancer l'agent. Le choix vit dans un petit
fichier JSON a cote du jeton Instagram (meme dossier applicatif, hors du
depot) ; `.env` (`settings.publication_mode`) reste la valeur de repli tant
qu'aucun choix n'a ete fait depuis le dashboard, ou si le fichier est absent,
illisible ou invalide -- jamais d'exception qui arreterait l'agent planifie.
"""

import datetime as dt
import json
import os
from pathlib import Path
from typing import Literal

from app.config import settings

Mode = Literal["arret", "apercu", "reel"]
MODES_VALIDES: tuple[Mode, ...] = ("arret", "apercu", "reel")


def _chemin_par_defaut() -> Path:
    return settings.publication_mode_path


def _mode_du_fichier(chemin: Path) -> Mode | None:
    try:
        donnees = json.loads(chemin.read_text())
        mode = donnees["mode"]
    except Exception:  # noqa: BLE001 -- absent, illisible, corrompu : jamais fatal
        return None
    return mode if mode in MODES_VALIDES else None


def mode_effectif(chemin: Path | None = None) -> str:
    """Le mode a appliquer : celui du fichier dashboard, sinon celui de `.env`."""
    chemin = chemin or _chemin_par_defaut()
    return _mode_du_fichier(chemin) or settings.publication_mode


def source_du_mode(chemin: Path | None = None) -> Literal["dashboard", "env"]:
    """D'ou vient le mode effectif : un choix fait au dashboard, ou le repli `.env`."""
    chemin = chemin or _chemin_par_defaut()
    return "dashboard" if _mode_du_fichier(chemin) is not None else "env"


def enregistrer_mode(mode: Mode, maintenant: dt.datetime, chemin: Path | None = None) -> None:
    """Ecrit le mode choisi, atomiquement (fichier temporaire + remplacement)."""
    chemin = chemin or _chemin_par_defaut()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    donnees = {"mode": mode, "change_le": maintenant.isoformat()}
    provisoire = chemin.with_suffix(chemin.suffix + ".tmp")
    provisoire.write_text(json.dumps(donnees))
    os.replace(provisoire, chemin)
