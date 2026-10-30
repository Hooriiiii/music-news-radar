"""L'heure de fin du dernier passage de l'agent de publication.

POURQUOI. Aucun voyant ne mesurait cet agent : « Dernier passage » lit l'ingestion,
et MANQUE n'est ecrit que par lui. Il pouvait donc mourir sous un bandeau vert.

ECRIT A LA FIN DU PASSAGE, jamais avant : rien n'est sur le disque tant que la
publication tourne, donc cette ecriture ne peut ni la retarder ni l'empecher. Et
elle ne leve JAMAIS -- un disque plein ne vaut pas un passage en echec. Une
ecriture ratee laisse simplement le fichier vieillir, et c'est le bandeau qui le
dira.

UN SEUL ECRIVAIN, et le fichier decrit UN passage : launchd ne chevauche pas deux
instances, donc pas de relecture-fusion, le dernier passage remplace le precedent.
Reste le passage lance a la main qui croiserait l'agent : ecriture atomique,
temporaire nomme par processus, comme tous les etats a cote du jeton. Le lecteur
est `app/supervision.py`, avec son `lire_json` a trois issues.
"""

import datetime as dt
import json
import logging
import os
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# A cote du jeton : meme dossier applicatif, hors du depot.
FICHIER_PASSAGE = "publication_passage.json"


def consigner_passage(fin_le: dt.datetime, mode: str | None, panne: str | None = None) -> bool:
    """Ecrit la fin du passage. Rend False sur un echec, sans jamais lever.

    `panne` s'affiche au bandeau : n'y mettre que des motifs ecrits ici ou un TYPE
    d'exception, jamais le message -- il peut porter une URL avec son jeton.
    """
    temporaire: Path | None = None
    try:
        chemin = settings.meta_token_path.parent / FICHIER_PASSAGE
        chemin.parent.mkdir(parents=True, exist_ok=True)
        temporaire = chemin.with_name(f"{chemin.name}.{os.getpid()}.tmp")
        temporaire.write_text(json.dumps({"fin_le": fin_le.isoformat(), "mode": mode, "panne": panne}))
        os.replace(temporaire, chemin)
        return True
    except Exception:  # noqa: BLE001 -- une trace perdue ne vaut pas un passage perdu
        logger.exception("passage de publication non consigne")
        if temporaire is not None:
            try:
                temporaire.unlink(missing_ok=True)
            except OSError:
                logger.exception("temporaire de passage non efface")
        return False
