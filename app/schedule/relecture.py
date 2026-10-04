"""La fenetre de relecture humaine : ce qui va partir, et l'annonce qu'il part.

POURQUOI. Trois defauts reels, reperes A L'OEIL sur le compte publie en deux
jours et invisibles de toute la suite de tests : un logo Wikipedia recadre en
derniere slide, un reel de 4 secondes sans rapport avec l'actu, une annonce
« sort ce soir » publiee deux jours apres la sortie. La demande de l'utilisateur
est donc une FENETRE pour verifier et corriger -- « certains elements sont
difficilement detectables par l'app », et c'est juste.

LA MESURE QUI GOUVERNE CE MODULE (2026-10-04, base vivante). On a d'abord
cherche cette fenetre au GEL, puisque c'est la que tout est fige :

| ecart gel -> heure prevue | n=85 creneaux reellement geles |
|---|---|
| mediane | 105 min |
| p25 | 33 min |
| <= 30 min | 18 (21 %) |
| <= 5 min | 12 (14 %) |

Un creneau sur sept est donc gele a moins de cinq minutes de son depart (passage
rattrape au reveil du Mac) : une fenetre accrochee au gel n'existerait pas pour
lui. Alors que le POST, lui, existe BIEN plus tot -- ecart composition -> heure
prevue, n=111 creneaux portant un post : mediane **737 min** (12 h), p25 258 min,
et seulement 9 sur 111 sous 2 h.

D'ou la regle de conception : **la fenetre s'ouvre a la COMPOSITION, pas au gel**.
A la composition le creneau est PROGRAMME, donc deja librement editable -- rien a
deverrouiller. Le gel n'est que le DERNIER APPEL. Ce qui manquait n'etait donc
pas du temps : c'etait (1) que personne ne previenne, (2) que rien ne montre ce
qui va PARTIR, et (3) que le gel ferme la porte pour ses deux dernieres heures.

CE MODULE NE RETIENT RIEN. L'app reste AUTOMATIQUE : si personne ne relit, ca
publie a l'heure. La relecture est une OPTION, jamais une condition -- un post
qui attendrait une validation qui ne vient pas ressemble a un post en cours, et
le taux de publication est deja a moins de la moitie. Aucune fonction d'ici n'est
lue par `_publier`, et un test-sentinelle
(`tests/test_relecture.py::test_la_relecture_n_est_jamais_une_condition_de_publication`)
echoue si quelqu'un l'y branche.

L'ANNONCE est une notification macOS -- le seul canal du projet (ni mail ni
Discord : CLAUDE.md est explicite, « ne pas re-proposer SMTP », et les alertes
Discord sont debranchees). Au plus UNE par creneau, l'etat dans
`relecture.json` a cote du jeton, meme mecanique que `publication_panne.json` et
`quota_graph.json` : ecriture atomique, temporaire nomme par processus, purge des
entrees echues a chaque ecriture, AUCUNE ecriture en lecture.
"""

import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Sentinelle posee dans `creneaux.erreur` quand l'utilisateur ROUVRE un creneau
# gele pour le corriger. Meme procede que `runner.RAISON_RECLAMATION` : un marqueur
# lisible dans un champ libre, relu par un autre module pour decider d'un
# comportement -- ici, qu'un gel qui echoue apres une edition MANUELLE libere le
# creneau au lieu d'exclure l'article a vie. Sans elle, une accroche rallongee a
# la main d'un caractere de trop (cover qui deborde -> PreparationImpossible)
# brulerait l'actu : exactement ce que « ne perds pas le post » interdit.
RAISON_RELECTURE = "rouvert pour relecture"


def _fichier() -> Path:
    # A cote du jeton, comme tous les etats du projet : hors du depot, et dans le
    # dossier que l'agent planifie sait ecrire.
    return settings.meta_token_path.parent / "relecture.json"


def _lire(chemin: Path) -> dict[str, str]:
    """Les annonces deja faites, par id de creneau. Ne leve jamais : un fichier
    absent ou corrompu vaut « rien n'a ete annonce », et au pire on annonce une
    fois de trop -- l'inverse (taire une annonce) est ce qui coute."""
    try:
        donnees = json.loads(chemin.read_text())
    except Exception:  # noqa: BLE001 -- absent, tronque, illisible
        return {}
    if not isinstance(donnees, dict):
        return {}
    annonces = donnees.get("annonces")
    if not isinstance(annonces, dict):
        return {}
    return {str(cle): str(valeur) for cle, valeur in annonces.items() if isinstance(valeur, str)}


def _ecrire(chemin: Path, annonces: dict[str, str]) -> None:
    # Temporaire nomme PAR PROCESSUS (`mkstemp`) : l'agent de publication et une
    # requete du dashboard peuvent ecrire ce fichier en meme temps, et un nom fixe
    # serait ecrit a deux mains.
    chemin.parent.mkdir(parents=True, exist_ok=True)
    descripteur, provisoire = tempfile.mkstemp(dir=chemin.parent, prefix=chemin.name + ".")
    try:
        with os.fdopen(descripteur, "w") as fichier:
            json.dump({"annonces": annonces}, fichier)
        os.replace(provisoire, chemin)
    except Exception:  # noqa: BLE001 -- une annonce perdue ne vaut pas un passage perdu
        logger.exception("etat de relecture non ecrit")
        Path(provisoire).unlink(missing_ok=True)


def _purgees(annonces: dict[str, str], maintenant: dt.datetime) -> dict[str, str]:
    """Les annonces des creneaux encore d'actualite. Un creneau annonce il y a
    plus d'un jour est parti (ou manque) depuis longtemps : sans cette purge le
    fichier grossirait indefiniment, comme les compteurs de `pause_visuels`."""
    limite = maintenant - dt.timedelta(days=1)
    gardees = {}
    for cle, horodatage in annonces.items():
        try:
            quand = dt.datetime.fromisoformat(horodatage)
        except ValueError:
            continue
        if quand.tzinfo is None:
            quand = quand.replace(tzinfo=dt.timezone.utc)
        if quand > limite:
            gardees[cle] = horodatage
    return gardees


def deja_annonce(creneau_id: int, maintenant: dt.datetime) -> bool:
    return str(creneau_id) in _purgees(_lire(_fichier()), maintenant)


def marquer_annonce(creneau_id: int, maintenant: dt.datetime) -> None:
    """Note qu'on a prevenu pour ce creneau. Relit le disque A L'INSTANT DE
    L'ECRITURE et fusionne : c'est l'invariant des fichiers d'etat du projet
    (`pause_visuels._fusionner_et_ecrire`, `cluster_arbitrage.fusionner_et_ecrire`)
    -- une annonce posee par un autre processus entre notre lecture et notre
    ecriture ne doit pas se faire ecraser, sinon elle repartirait."""
    chemin = _fichier()
    annonces = _purgees(_lire(chemin), maintenant)
    annonces[str(creneau_id)] = maintenant.isoformat()
    _ecrire(chemin, annonces)


def avance() -> dt.timedelta:
    return dt.timedelta(minutes=settings.publication_relecture_avance_minutes)


def dans_la_fenetre(publier_a: dt.datetime, maintenant: dt.datetime) -> bool:
    """Vrai quand l'heure de depart est a portee d'annonce et pas encore passee.

    Pure : c'est l'horloge qui decide, jamais l'etat du gel -- voir la mesure en
    tete de module (un creneau sur sept gele a moins de cinq minutes du depart)."""
    return dt.timedelta(0) < publier_a - maintenant <= avance()


def regel_possible(publier_a: dt.datetime, maintenant: dt.datetime) -> bool:
    """Reste-t-il de quoi refaire les fichiers figes avant l'heure ?

    Rouvrir un creneau gele jette son rendu Chromium et ses telechargements ; le
    gel les refait au passage suivant de l'agent (10 min). Sous ce delai, rouvrir
    serait echanger un post PRET contre un creneau manque -- on refuse, en le
    DISANT (`app/api/schedule.py`), jamais un bouton gris et muet."""
    return publier_a - maintenant >= dt.timedelta(
        minutes=settings.publication_relecture_regel_minutes
    )
