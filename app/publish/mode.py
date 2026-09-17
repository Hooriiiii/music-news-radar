"""Le mode de publication choisi depuis le dashboard.

Trois modes (voir `app/config.py::publication_mode`) : `arret`, `apercu`, `reel`.
Choisir un mode publie public et irreversible (`reel`) est une decision de
l'utilisateur, pas un reglage fige au demarrage -- le dashboard doit pouvoir le
changer sans toucher `.env` ni relancer l'agent. Le choix vit dans un petit
fichier JSON a cote du jeton Instagram (meme dossier applicatif, hors du
depot) ; `.env` (`settings.publication_mode`) reste la valeur de repli tant
qu'aucun choix n'a ete fait depuis le dashboard, ou si le fichier est absent,
illisible ou invalide -- jamais d'exception qui arreterait l'agent planifie.

Le meme fichier porte `reels_actifs`, le coupe-circuit des reels automatiques ;
chaque ecriture preserve la cle de l'autre reglage.
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


def _lire(chemin: Path) -> dict:
    try:
        donnees = json.loads(chemin.read_text())
    except Exception:  # noqa: BLE001 -- absent, illisible, corrompu : jamais fatal
        return {}
    return donnees if isinstance(donnees, dict) else {}


def _ecrire(chemin: Path, donnees: dict) -> None:
    """Atomiquement : fichier temporaire, puis remplacement."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    provisoire = chemin.with_suffix(chemin.suffix + ".tmp")
    provisoire.write_text(json.dumps(donnees))
    os.replace(provisoire, chemin)


def _mode_du_fichier(chemin: Path) -> Mode | None:
    mode = _lire(chemin).get("mode")
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
    """Ecrit le mode choisi, sans toucher `reels_actifs`."""
    chemin = chemin or _chemin_par_defaut()
    _ecrire(chemin, {**_lire(chemin), "mode": mode, "change_le": maintenant.isoformat()})


def reels_actifs(chemin: Path | None = None) -> bool:
    """Les reels automatiques tournent-ils ? Seulement si le dashboard les a
    EXPLICITEMENT allumes : cle absente, fichier illisible ou valeur invalide
    valent False. Une fusion ou un fichier perdu ne doit jamais publier de reel
    tout seul, meme en mode `reel`. Le mode (arret/apercu/reel) reste le
    garde-fou de la publication, ce reglage n'est que l'interrupteur des reels."""
    return _lire(chemin or _chemin_par_defaut()).get("reels_actifs") is True


def enregistrer_reels(actifs: bool, maintenant: dt.datetime, chemin: Path | None = None) -> None:
    """Ecrit l'interrupteur des reels, sans toucher le mode."""
    chemin = chemin or _chemin_par_defaut()
    _ecrire(chemin, {**_lire(chemin), "reels_actifs": actifs, "reels_change_le": maintenant.isoformat()})
