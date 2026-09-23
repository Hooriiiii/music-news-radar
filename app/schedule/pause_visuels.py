"""Mise a l'ecart TEMPORAIRE d'un article saute faute de visuel de qualite.

POURQUOI (tour de correction 1/5 sur la Tache 5, mesure sur 120 passages
simules). Le saut (`compose.VisuelIntrouvable`) libere le creneau et relance
l'arbitrage DANS LE MEME PASSAGE (`app/schedule/runner.py::_composer`,
`exclusions = True`) -- mais l'arbitrage classe par score et l'article sauteur
reste ELIGIBLE. S'il a le meilleur score, il revient sur le creneau le plus
proche, saute a nouveau, et ainsi de suite jusqu'a MANQUE, puis glisse au
creneau suivant : mesure, un seul article a 95 de pertinence sans visuel face
a quatre bons articles a 67-70 a fait perdre TOUTE LA JOURNEE de publication
(3 creneaux en MANQUE, zero post, 120 sauts, un 4e creneau deja repris par le
meme article).

Ce n'est PAS une exclusion : la Tache 5 l'interdit explicitement, l'article
peut avoir un visuel demain (nouvelle publication du compte source, recherche
web qui ressort du cache). Une pause TEMPORAIRE la respecte tout en arretant
le cycle : l'article sort des candidats le temps que quelque chose ait une
chance raisonnable d'avoir change (`visuel_saute_pause_minutes`, justifie dans
`app/config.py`), puis redevient eligible comme n'importe quel autre.

MEME MOTIF que `app/pipeline/quota_graph.py` (fichier d'etat a cote du jeton,
ecriture atomique, temporaire nomme par processus) : plusieurs entrees, une
par article en pause, purgees des qu'elles sont echues -- l'archive ne grossit
jamais. Aucune migration : rien de ce fichier ne vit en base.

POINT D'ENTREE UNIQUE (`marquer_saute`) : la Tache 6 deplacera une partie des
sauts au GEL (T-2h, apres l'echec de la super-resolution) et devra pouvoir
l'appeler exactement de la meme facon -- ne pas dupliquer ce mecanisme
ailleurs.
"""

import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# A cote du jeton, comme `quota_graph.json`, `veille.json` et
# `publication_panne.json` : meme dossier applicatif, hors du depot.
FICHIER_ETAT = "visuel_saute.json"


def chemin_etat() -> Path:
    return settings.meta_token_path.parent / FICHIER_ETAT


def _lire(chemin: Path) -> dict[int, dt.datetime]:
    """Les pauses connues, indexees par id d'article. Jamais d'exception : un
    fichier absent, tronque ou hors schema vaut « aucune pause connue » --
    comme `quota_graph._lire`, on prefere un article retente de trop a un
    planificateur qui meurt faute de fichier."""
    try:
        donnees = json.loads(chemin.read_text())
    except Exception:  # noqa: BLE001 -- absent, tronque, illisible
        return {}
    if not isinstance(donnees, dict):
        return {}
    pauses: dict[int, dt.datetime] = {}
    for article_id, valeur in (donnees.get("pauses") or {}).items():
        try:
            jusqu_a = dt.datetime.fromisoformat(valeur)
        except (TypeError, ValueError):
            continue
        try:
            pauses[int(article_id)] = jusqu_a
        except (TypeError, ValueError):
            continue
    return pauses


def _ecrire(chemin: Path, pauses: dict[int, dt.datetime]) -> None:
    """Ecriture ATOMIQUE, temporaire NOMME PAR PROCESSUS (`tempfile.mkstemp`) :
    le pipeline et l'agent de publication peuvent tous deux poser une pause
    dans la meme seconde, et un `.tmp` partage donnerait un JSON melange
    (meme piege que `quota_graph.py`)."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    descripteur, temporaire = tempfile.mkstemp(dir=chemin.parent, prefix=chemin.name + ".")
    try:
        with os.fdopen(descripteur, "w") as fichier:
            json.dump(
                {"pauses": {str(aid): jusqu_a.isoformat() for aid, jusqu_a in pauses.items()}},
                fichier,
            )
        os.replace(temporaire, chemin)
    except Exception:
        Path(temporaire).unlink(missing_ok=True)
        raise


def articles_en_pause(maintenant: dt.datetime, chemin: Path | None = None) -> set[int]:
    """Les ids d'article encore mis a l'ecart a cet instant -- a exclure des
    candidats (`store._articles_publiables`)."""
    pauses = _lire(chemin or chemin_etat())
    return {article_id for article_id, jusqu_a in pauses.items() if maintenant < jusqu_a}


def marquer_saute(
    article_id: int,
    maintenant: dt.datetime,
    *,
    duree: dt.timedelta | None = None,
    chemin: Path | None = None,
) -> dt.datetime:
    """Met l'article a l'ecart pour `duree` (defaut : `visuel_saute_pause_minutes`).

    Purge au passage les entrees echues -- d'autres articles que celui-ci --
    pour que le fichier ne grossisse jamais indefiniment. Rend l'instant de
    fin de la pause. Une panne d'ecriture (disque plein, permissions) ne casse
    jamais l'appelant : sans etat persiste, le prochain passage retentera
    l'article un peu plus tot que prevu, ce qui reste strictement moins grave
    que le defaut que cette pause corrige.
    """
    chemin = chemin or chemin_etat()
    duree = duree if duree is not None else dt.timedelta(minutes=max(1, settings.visuel_saute_pause_minutes))
    pauses = {aid: jusqu_a for aid, jusqu_a in _lire(chemin).items() if maintenant < jusqu_a}
    fin = maintenant + duree
    pauses[article_id] = fin
    try:
        _ecrire(chemin, pauses)
    except OSError:
        logger.exception("pause de l'article %s non enregistree (%s)", article_id, chemin)
    return fin
