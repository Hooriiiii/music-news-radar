"""Une ligne par appel Anthropic, pour que la depense soit verifiable.

POURQUOI. Avant ce module, `response.usage` n'etait lu nulle part : ni le cout d'un
poste, ni l'effet du cache, ni le poids des relances ne pouvaient se constater. Les
credits se sont epuises le 2026-10-31 sans qu'on puisse dire ou ils etaient partis.
La lecture se fait hors ligne : `scripts/diagnostic/consommation_api.py`.

APPEND, PAS RELECTURE-FUSION. Les etats a cote du jeton se reecrivent d'habitude par
un temporaire nomme par processus (`credits_anthropic`). Ici le fichier ne fait que
grandir et trois agents launchd y ecrivent : relire puis remplacer le fichier a chaque
note serait lent et ferait perdre des lignes. `O_APPEND` et UN SEUL `os.write` par
ligne : le noyau positionne chaque ecriture en fin de fichier, les lignes ne
s'ecrasent pas et ne s'entrelacent pas. Un fichier par mois, pour rester petit.

`null`, JAMAIS 0. Une valeur absente n'est pas une valeur nulle : un 0 ferait passer
un appel paye pour un appel gratuit. D'ou `consigner(poste, None, modele=...)` quand
`messages.parse()` leve sur une reponse invalide : l'appel a ete facture, on ne sait
pas combien, et la ligne le dit. Une erreur levee avant l'envoi passerait par le meme
chemin : une ligne INCONNUE de trop vaut mieux qu'un appel paye qui disparait. Un
attribut qui n'est pas un entier (MagicMock, dans les tests) vaut `null` aussi.

Elle ne leve jamais : une trace perdue ne vaut pas une note perdue.
"""

import datetime as dt
import json
import logging
import os
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

PREFIXE_FICHIER = "usage_anthropic-"


def chemin_du_mois(moment: dt.datetime) -> Path:
    return settings.meta_token_path.parent / f"{PREFIXE_FICHIER}{moment:%Y-%m}.jsonl"


def _entier(valeur) -> int | None:
    # bool herite de int : True n'est pas un nombre de tokens.
    return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) else None


def _texte(valeur) -> str | None:
    return valeur if isinstance(valeur, str) else None


def consigner(poste: str, reponse=None, modele: str | None = None) -> bool:
    """`modele` est le nom demande, celui de la table de prix ; sinon celui de la reponse."""
    try:
        moment = dt.datetime.now(dt.timezone.utc)
        usage = getattr(reponse, "usage", None)
        outils = getattr(usage, "server_tool_use", None)
        ligne = {
            "le": moment.isoformat(),
            "poste": poste,
            "modele": _texte(modele) or _texte(getattr(reponse, "model", None)),
            "input_tokens": _entier(getattr(usage, "input_tokens", None)),
            "output_tokens": _entier(getattr(usage, "output_tokens", None)),
            "cache_creation_input_tokens": _entier(
                getattr(usage, "cache_creation_input_tokens", None)
            ),
            "cache_read_input_tokens": _entier(getattr(usage, "cache_read_input_tokens", None)),
            "web_search_requests": _entier(getattr(outils, "web_search_requests", None)),
            "stop_reason": _texte(getattr(reponse, "stop_reason", None)),
            "request_id": _texte(getattr(reponse, "_request_id", None)),
        }
        octets = (json.dumps(ligne) + "\n").encode("utf-8")
        chemin = chemin_du_mois(moment)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        descripteur = os.open(chemin, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descripteur, octets)
        finally:
            os.close(descripteur)
        return True
    except Exception:  # noqa: BLE001 -- une trace perdue ne vaut pas une note perdue
        logger.exception("usage Anthropic non consigne (%s)", poste)
        return False
