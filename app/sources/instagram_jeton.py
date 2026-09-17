"""Surveiller l'expiration du jeton de la source Instagram (META_FB_ACCESS_TOKEN).

Le jeton de PUBLICATION (connexion Instagram) se rafraichit tout seul
(`app/publish/jeton.py`). Celui du SOURCING est un jeton utilisateur de connexion
Facebook : le prolonger sans intervention demanderait le secret de l'app, que le
projet ne detient pas. Son renouvellement est donc manuel, et ce module sert a ce
qu'il ne soit jamais oublie : une notification macOS, au plus une par jour, a
sept jours de l'echeance.

Le jeton n'apparait jamais dans un message ni dans un log : il voyage en
parametre d'URL, et une exception reseau peut porter cette URL.
"""

import datetime as dt
import json
import logging

import httpx

from app.config import settings
from app.delivery.notify import notifier as notifier_par_defaut

logger = logging.getLogger(__name__)

FICHIER_ALERTE = "sourcing_token_alert.json"
SEUIL_ALERTE = dt.timedelta(days=7)
JETON_INVALIDE = 190

RENOUVELER = (
    "Le renouveler : developers.facebook.com/tools/explorer → Generate Access Token "
    "→ ⓘ → outil de débogage → « Étendre le jeton d'accès » → remplacer "
    "META_FB_ACCESS_TOKEN dans .env."
)


class JetonSourcingInvalide(RuntimeError):
    """Meta dit le jeton expire, revoque ou invalide."""


def expiration_jeton_sourcing(http=httpx) -> dt.datetime | None:
    """L'echeance du jeton selon `debug_token`, None s'il n'expire jamais.

    Leve `JetonSourcingInvalide` si Meta le refuse (code 190 ou `is_valid`
    faux), `RuntimeError` pour toute autre reponse inexploitable.
    """
    jeton = settings.meta_fb_access_token
    reponse = http.get(
        f"https://graph.facebook.com/{settings.meta_api_version}/debug_token",
        params={"input_token": jeton, "access_token": jeton},
        timeout=30,
    )
    try:
        donnees = reponse.json() or {}
    except ValueError:
        donnees = {}
    erreur = donnees.get("error") or {}
    if erreur.get("code") == JETON_INVALIDE:
        raise JetonSourcingInvalide("jeton de sourcing refuse par Meta (code 190)")
    data = donnees.get("data")
    if reponse.status_code >= 400 or not isinstance(data, dict):
        raise RuntimeError(
            f"debug_token inexploitable (HTTP {reponse.status_code}, code {erreur.get('code')})"
        )
    if data.get("is_valid") is False:
        raise JetonSourcingInvalide("jeton de sourcing invalide selon debug_token")
    expire = int(data.get("expires_at") or 0)
    if not expire:
        return None
    return dt.datetime.fromtimestamp(expire, dt.UTC)


def alerte_jeton_sourcing(expire_le: dt.datetime | None, maintenant: dt.datetime) -> str | None:
    """Le message a notifier, ou None tant que l'echeance est loin."""
    if expire_le is None:
        return None
    restant = expire_le - maintenant
    if restant <= dt.timedelta(0):
        return f"Jeton Facebook de la source Instagram expiré : les sources Instagram sont en panne. {RENOUVELER}"
    if restant > SEUIL_ALERTE:
        return None
    jours = max(1, round(restant / dt.timedelta(days=1)))
    return (
        f"Jeton Facebook de la source Instagram : expire dans {jours} jour(s), "
        f"le {expire_le:%d/%m/%Y}. {RENOUVELER}"
    )


def _deja_alerte_le(chemin) -> dt.date | None:
    try:
        return dt.date.fromisoformat(json.loads(chemin.read_text())["alerte_envoyee_le"])
    except Exception:  # noqa: BLE001 -- absent ou illisible : on previent
        return None


def surveiller_jeton_sourcing(
    maintenant: dt.datetime, notifier=notifier_par_defaut, http=httpx
) -> str | None:
    """Verifie l'echeance, notifie au plus une fois par jour. Ne leve jamais.

    Rend le message d'alerte courant (meme deja notifie aujourd'hui), None sinon.
    """
    if not settings.meta_fb_access_token:
        return None
    try:
        expire_le = expiration_jeton_sourcing(http=http)
    except JetonSourcingInvalide:
        expire_le = maintenant
    except Exception as exc:  # noqa: BLE001 -- reseau, JSON : jamais fatal
        # Le TYPE seulement : le texte d'une exception httpx porte l'URL, donc le jeton.
        logger.warning("Echeance du jeton de sourcing Instagram illisible (%s)", type(exc).__name__)
        return None

    alerte = alerte_jeton_sourcing(expire_le, maintenant)
    if alerte is None:
        return None
    chemin = settings.meta_token_path.parent / FICHIER_ALERTE
    if _deja_alerte_le(chemin) == maintenant.date():
        return alerte
    try:
        notifier("Kinda", alerte)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps({"alerte_envoyee_le": maintenant.date().isoformat()}))
    except Exception:  # noqa: BLE001 -- le canal d'alerte ne casse jamais le run
        logger.exception("Notification d'echeance du jeton de sourcing impossible")
    return alerte
