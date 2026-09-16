"""Le jeton Instagram : le lire, le rafraichir, prevenir avant qu'il meure.

Un jeton `IG` longue duree vit 60 jours. Il se prolonge par
`GET graph.instagram.com/refresh_access_token`, possible seulement pour un jeton
age d'au moins 24 h et non expire. S'il meurt, la publication s'arrete -- en
silence si rien ne previent, comme l'agent digest pendant cinq semaines. D'ou le
rafraichissement hebdomadaire et l'alerte a J-7.

Le jeton du `.env` n'est que la valeur INITIALE. Le jeton courant est ecrit dans
un fichier JSON en droits 600 : le code ne reecrit jamais `.env`.
"""

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings

URL_RAFRAICHISSEMENT = "https://graph.instagram.com/refresh_access_token"
INTERVALLE_RAFRAICHISSEMENT = dt.timedelta(days=7)
# L'agent tourne toutes les 10 minutes : sans delai apres un echec, un jeton
# mort produirait un appel refuse toutes les 10 minutes.
DELAI_APRES_ECHEC = dt.timedelta(hours=6)
SEUIL_ALERTE = dt.timedelta(days=7)


class JetonIndisponible(RuntimeError):
    """Aucun jeton du tout. Message affichable."""


@dataclass
class EtatJeton:
    access_token: str
    rafraichi_le: dt.datetime | None = None
    expire_le: dt.datetime | None = None
    echec_le: dt.datetime | None = None
    alerte_envoyee_le: dt.date | None = None


def _date_heure(valeur: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(valeur) if valeur else None


def charger(chemin: Path) -> EtatJeton:
    if chemin.exists():
        donnees = json.loads(chemin.read_text())
        alerte = donnees.get("alerte_envoyee_le")
        return EtatJeton(
            access_token=donnees["access_token"],
            rafraichi_le=_date_heure(donnees.get("rafraichi_le")),
            expire_le=_date_heure(donnees.get("expire_le")),
            echec_le=_date_heure(donnees.get("echec_le")),
            alerte_envoyee_le=dt.date.fromisoformat(alerte) if alerte else None,
        )
    if not settings.meta_access_token:
        raise JetonIndisponible(
            "META_ACCESS_TOKEN manquant dans .env : generer un jeton dans l'app Meta "
            "« Kinda Radar », API Instagram, etape « Generer des tokens d'acces »"
        )
    return EtatJeton(access_token=settings.meta_access_token.strip())


def enregistrer(chemin: Path, etat: EtatJeton) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    donnees = {
        "access_token": etat.access_token,
        "rafraichi_le": etat.rafraichi_le.isoformat() if etat.rafraichi_le else None,
        "expire_le": etat.expire_le.isoformat() if etat.expire_le else None,
        "echec_le": etat.echec_le.isoformat() if etat.echec_le else None,
        "alerte_envoyee_le": etat.alerte_envoyee_le.isoformat() if etat.alerte_envoyee_le else None,
    }
    chemin.write_text(json.dumps(donnees, indent=2))
    # Un jeton de publication donne la main sur le compte : lisible par son seul
    # proprietaire.
    os.chmod(chemin, 0o600)


def rafraichir_si_necessaire(
    chemin: Path, maintenant: dt.datetime, http=httpx
) -> tuple[EtatJeton, str | None]:
    etat = charger(chemin)
    if etat.rafraichi_le and maintenant - etat.rafraichi_le < INTERVALLE_RAFRAICHISSEMENT:
        return etat, None
    if etat.echec_le and maintenant - etat.echec_le < DELAI_APRES_ECHEC:
        return etat, None

    try:
        reponse = http.get(
            URL_RAFRAICHISSEMENT,
            params={"grant_type": "ig_refresh_token", "access_token": etat.access_token},
            timeout=30,
        )
        donnees = reponse.json()
    except Exception as exc:  # noqa: BLE001 -- reseau ou JSON : meme traitement
        donnees, reponse = {"error": {"message": str(exc)}}, None

    if reponse is None or reponse.status_code >= 400 or "access_token" not in donnees:
        message = (donnees.get("error") or {}).get("message") or "reponse inattendue"
        etat.echec_le = maintenant
        enregistrer(chemin, etat)
        return etat, f"rafraichissement du jeton Instagram refuse : {message}"

    etat = EtatJeton(
        access_token=donnees["access_token"],
        rafraichi_le=maintenant,
        expire_le=maintenant + dt.timedelta(seconds=int(donnees.get("expires_in", 0))),
        alerte_envoyee_le=etat.alerte_envoyee_le,
    )
    enregistrer(chemin, etat)
    return etat, None


def alerte_expiration(etat: EtatJeton, maintenant: dt.datetime) -> str | None:
    if etat.expire_le is None:
        return None
    reste = etat.expire_le - maintenant
    if reste > SEUIL_ALERTE:
        return None
    return f"jeton Instagram : expire dans {max(reste.days, 0)} jour(s)"
