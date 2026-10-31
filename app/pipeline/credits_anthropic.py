"""Le solde Anthropic, consigne pour le bandeau, sans appel a l'affichage.

POURQUOI. Constat du 2026-10-31 : plus de credits, `pipeline.log` ecrit « crédits API
épuisés — le scoring est arrêté » et 1246 erreurs, pendant que le voyant Composition
dit en ATTENTION « scoring interrompu (crédits, clé API ou réseau) ». Le diagnostic
existait (`health.diagnostiquer`) mais ne vivait que dans le rapport du run, et la
seule trace lisible par le bandeau, `creneaux.erreur`, demande qu'une composition ait
ete tentee -- rien n'en tente quand aucun creneau n'attend.

CE QUI COMPTE COMME SIGNAL. Le refus, par sa signature (`SIGNATURE_CREDITS`) ; le
succes, par une reponse de Claude. Une coupure reseau, une cle invalide ou une sortie
mal formee ne disent RIEN du solde : elles ne touchent pas au fichier. C'est ce qui
garde le voyant de crier « crédits épuisés » sur une panne reseau passagere.

DEUX DATES, PAS UN BOOLEEN. Trois agents ecrivent (pipeline, veille, publication) et
peuvent se croiser : un `epuise` serait au dernier qui ecrit, pas au dernier qui a
appele. Chacun relit le fichier a l'instant d'ecrire et ne fait qu'avancer SA date ;
le plus recent des deux evenements tranche, et le voyant s'eteint seul au premier
succes. Ecriture atomique, temporaire nomme par processus, comme tous les etats a cote
du jeton. Aucune ecriture en lecture, et elle ne leve jamais : un disque plein ne vaut
ni une note ni une composition.
"""

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.pipeline.health import SIGNATURE_CREDITS

logger = logging.getLogger(__name__)

FICHIER_ETAT = "credits_anthropic.json"
REFUS = "refus_le"
SUCCES = "succes_le"


def chemin_etat() -> Path:
    return settings.meta_token_path.parent / FICHIER_ETAT


def est_refus_de_credits(erreur) -> bool:
    return SIGNATURE_CREDITS in str(erreur).lower()


@dataclass(frozen=True)
class EtatCredits:
    refus_le: dt.datetime | None
    succes_le: dt.datetime | None

    @property
    def epuises(self) -> bool:
        if self.refus_le is None:
            return False
        return self.succes_le is None or self.refus_le > self.succes_le


def _date(valeur) -> dt.datetime | None:
    if valeur is None:
        return None
    moment = dt.datetime.fromisoformat(valeur)  # ValueError, TypeError : a l'appelant
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.timezone.utc)


def lire_etat(donnees: dict) -> EtatCredits:
    """Leve ValueError sur un contenu qui ne dit rien : c'est au lecteur de l'afficher
    INCONNU. Un objet vide n'est pas « aucun refus », c'est « aucune mesure »."""
    try:
        etat = EtatCredits(_date(donnees.get(REFUS)), _date(donnees.get(SUCCES)))
    except TypeError as exc:
        raise ValueError("date illisible") from exc
    if etat.refus_le is None and etat.succes_le is None:
        raise ValueError("aucun appel consigne")
    return etat


def _consigner(champ: str, moment: dt.datetime | None) -> bool:
    moment = moment or dt.datetime.now(dt.timezone.utc)
    temporaire: Path | None = None
    try:
        chemin = chemin_etat()
        chemin.parent.mkdir(parents=True, exist_ok=True)
        try:
            lues = json.loads(chemin.read_text())
            if not isinstance(lues, dict):
                lues = {}
        except (OSError, ValueError):
            lues = {}  # absent ou corrompu : cette ecriture le repare
        donnees = {cle: lues[cle] for cle in (REFUS, SUCCES) if cle in lues}
        try:
            existant = _date(donnees.get(champ))
        except (TypeError, ValueError):
            existant = None
        if existant is None or moment > existant:
            donnees[champ] = moment.isoformat()
        temporaire = chemin.with_name(f"{chemin.name}.{os.getpid()}.tmp")
        temporaire.write_text(json.dumps(donnees))
        os.replace(temporaire, chemin)
        return True
    except Exception:  # noqa: BLE001 -- une trace perdue ne vaut pas une note perdue
        logger.exception("etat des credits non consigne")
        if temporaire is not None:
            try:
                temporaire.unlink(missing_ok=True)
            except OSError:
                logger.exception("temporaire d'etat des credits non efface")
        return False


def consigner_refus(moment: dt.datetime | None = None) -> bool:
    return _consigner(REFUS, moment)


def consigner_succes(moment: dt.datetime | None = None) -> bool:
    return _consigner(SUCCES, moment)
