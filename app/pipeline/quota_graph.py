"""La pause de quota Graph, PARTAGEE par tout ce qui lit Meta a la cadence.

POURQUOI (panne mesuree en production le 2026-09-19, `pipeline.log`). Un appel
refuse pour cause de quota CONSOMME quand meme du quota. Le passage horaire
attaquait les 123 comptes Instagram, encaissait 115 refus, et ces 115 refus
alimentaient le depassement de l'heure suivante : le systeme se maintenait
lui-meme en panne. Nombre de sources en erreur par run, presque toutes en
« Meta HTTP 403 (code 4) : Application request limit reached » :

    18/09 09:05    36 sur 145        18/09 17:14    94
    18/09 10:14     0                18/09 19:42    99
    18/09 11:21    70                18/09 22:03   112
    18/09 15:08   129                18/09 23:10   116

751 erreurs de quota dans le log, et environ 80 % des comptes plus lus du tout
depuis le 18/09 vers 11 h. La veille rapide, elle, s'arretait deja au premier
code de quota -- c'est ce comportement qui est ici generalise.

POURQUOI UN ETAT PARTAGE, ET PAS UN PAR AGENT. Le quota est celui de l'APP,
pas celui d'un agent : la veille (toutes les 5 min) et le passage horaire
puisent dans le meme seau. Un agent qui vient de se prendre un code 4 sait
donc quelque chose de vrai pour l'autre, et deux pauses independantes
laisseraient le second aller taper dans le mur que le premier vient de
trouver. D'ou un fichier unique, a cote du jeton comme les autres compteurs.

CE QUI N'EST PAS SUSPENDU PAR CETTE PAUSE, et c'est deliberé : la PUBLICATION.
Un post gele qui attend son heure doit partir meme si l'ingestion est a
l'arret -- rater une publication programmee coute plus cher que de sauter une
heure d'ingestion. La lecture de visuels a la composition
(`app/content/lecture_artistes.py`) garde donc sa propre pause, plus courte.

ECRITURES CONCURRENTES. Deux agents ecrivent ce fichier, l'un toutes les
heures, l'autre toutes les cinq minutes. Trois precautions : fichier temporaire
NOMME PAR PROCESSUS puis `os.replace` (un `.tmp` partage serait ecrit a deux
mains) ; on n'ecrit QUE pour poser une pause, jamais pour en lever une (une
pause echue se reconnait a sa date, donc rien a effacer -- et rien qui puisse
effacer la pause que l'autre agent vient de poser) ; et deux pauses qui se
croisent gardent la plus TARDIVE, jamais la plus courte.
"""

import datetime as dt
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.delivery.notify import notifier as notifier_par_defaut

logger = logging.getLogger(__name__)

# A cote du jeton, comme `veille.json`, `publication_panne.json` et
# `sourcing_token_alert.json` : meme dossier applicatif, hors du depot.
FICHIER_ETAT = "quota_graph.json"


def chemin_etat() -> Path:
    return settings.meta_token_path.parent / FICHIER_ETAT


@dataclass(frozen=True)
class Pause:
    """Une pause de quota en cours. `notifiee` evite de redire a chaque passage
    un fait deja connu : la discipline du depot est une notification par pause
    (meme motif que `publication_panne.json`)."""

    jusqu_a: dt.datetime
    motif: str = ""
    declaree_par: str = ""
    notifiee: bool = False


def _datetime(valeur) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(valeur) if valeur else None
    except (TypeError, ValueError):
        return None


def _lire(chemin: Path) -> Pause | None:
    """L'etat relu, sans jamais lever : un agent planifie ne doit pas mourir
    faute de fichier (meme regle que `app/publish/mode.py`). Un fichier absent,
    tronque ou hors schema vaut « aucune pause connue » -- on prefere un appel
    de trop a une ingestion eteinte pour de bon."""
    try:
        donnees = json.loads(chemin.read_text())
    except Exception:  # noqa: BLE001 -- absent, tronque, illisible
        return None
    if not isinstance(donnees, dict):
        return None
    jusqu_a = _datetime(donnees.get("jusqu_a"))
    if jusqu_a is None:
        return None
    return Pause(
        jusqu_a=jusqu_a,
        motif=str(donnees.get("motif") or ""),
        declaree_par=str(donnees.get("declaree_par") or ""),
        notifiee=bool(donnees.get("notifiee")),
    )


def _ecrire(chemin: Path, pause: Pause) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    # Nom de temporaire propre au processus : les deux agents peuvent ecrire
    # dans la meme seconde, et un `.tmp` commun donnerait un JSON melange.
    descripteur, temporaire = tempfile.mkstemp(dir=chemin.parent, prefix=chemin.name + ".")
    try:
        with os.fdopen(descripteur, "w") as fichier:
            json.dump(
                {
                    "jusqu_a": pause.jusqu_a.isoformat(),
                    "motif": pause.motif,
                    "declaree_par": pause.declaree_par,
                    "notifiee": pause.notifiee,
                },
                fichier,
            )
        os.replace(temporaire, chemin)
    except Exception:
        Path(temporaire).unlink(missing_ok=True)
        raise


def pause_en_cours(maintenant: dt.datetime, chemin: Path | None = None) -> Pause | None:
    """La pause encore valable a cet instant, ou None.

    Une pause echue n'est pas effacee : la reconnaitre a sa date evite une
    ecriture en LECTURE, donc la course ou un agent effacerait la pause que
    l'autre vient tout juste de poser.
    """
    pause = _lire(chemin or chemin_etat())
    if pause is None or maintenant >= pause.jusqu_a:
        return None
    return pause


def declarer_quota(
    maintenant: dt.datetime,
    motif: str,
    declaree_par: str,
    *,
    notify=notifier_par_defaut,
    chemin: Path | None = None,
) -> Pause:
    """Meta vient de refuser un appel pour quota : on suspend les lectures.

    Rend la pause en vigueur. Notifie AU PLUS UNE FOIS par pause : une pause
    deja posee et deja signalee est un fait connu, et six notifications par
    heure apprennent a ignorer le canal.
    """
    chemin = chemin or chemin_etat()
    fin = maintenant + dt.timedelta(minutes=max(1, settings.veille_pause_quota_minutes))
    existante = pause_en_cours(maintenant, chemin)
    if existante is not None:
        # Le quota est toujours epuise : on garde la fin la plus TARDIVE, sinon
        # une pause courte posee par l'autre agent raccourcirait celle-ci.
        fin = max(fin, existante.jusqu_a)
    a_notifier = existante is None or not existante.notifiee
    pause = Pause(jusqu_a=fin, motif=motif[:500], declaree_par=declaree_par, notifiee=True)
    try:
        _ecrire(chemin, pause)
    except OSError:
        # Sans etat persiste, le passage suivant retapera dans le mur -- mais le
        # passage EN COURS s'arrete quand meme : c'est lui qui alimentait le
        # depassement. On ne fait donc pas echouer l'appelant pour autant.
        logger.exception("Pause de quota Graph non enregistree (%s)", chemin)
    if a_notifier:
        try:
            notify(
                "Kinda",
                f"Lectures Instagram en pause jusqu'a {fin:%H:%M} "
                f"(quota Graph atteint, {declaree_par}) : {motif}",
            )
        except Exception:  # noqa: BLE001 -- le canal d'alerte ne casse rien
            logger.exception("Notification de pause de quota impossible")
    return pause
