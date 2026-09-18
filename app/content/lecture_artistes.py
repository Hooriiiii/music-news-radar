"""Lire en direct les derniers posts d'un compte d'artiste, a la composition.

Seulement quand la base n'a aucun visuel SUR LE SUJET pour un carrousel. Le quota
Graph est partage avec le pipeline (~120 comptes toutes les 3 h) et a deja rendu
le code 4 pendant une heure (2026-09-17) : d'ou deux budgets (par passage, par
jour), un cache par compte et une pause sur le quota. Etat dans
`visuels_lectures.json`, a cote du jeton.

Les posts lus ne sont JAMAIS inseres en base : ils y declencheraient un scoring
paye.
"""

import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path

import httpx

from app.config import settings
from app.content.actifs_artistes import PostArtiste
from app.pipeline.comptes_artistes import CompteArtiste
from app.sources.instagram import QuotaGraphAtteint, items_depuis_payload, lire_compte

logger = logging.getLogger(__name__)

FICHIER_ETAT = "visuels_lectures.json"
# Au-dela, une entree de cache n'est plus relue : elle est purgee a l'ecriture.
CACHE_PURGE = dt.timedelta(hours=24)


class LectureDifferee(RuntimeError):
    """Lecture impossible MAINTENANT : pause, budget, quota, reseau. Message
    affichable, jamais le jeton."""


def _date(valeur) -> dt.datetime | None:
    if not valeur:
        return None
    try:
        return dt.datetime.fromisoformat(valeur)
    except (TypeError, ValueError):
        return None


def _en_dict(item) -> dict:
    return {
        "url": item.url,
        "title": item.title,
        "summary": item.summary,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "media": item.media,
    }


def _post(compte: CompteArtiste, item: dict) -> PostArtiste:
    return PostArtiste(
        compte=compte,
        permalink=item["url"],
        titre=item.get("title") or "",
        texte=item.get("summary") or "",
        publie_le=_date(item.get("published_at")),
        media=list(item.get("media") or []),
    )


class LecteurComptes:
    """Un lecteur par passage de l'agent : c'est lui qui porte le budget du passage."""

    def __init__(self, *, lire=lire_compte, chemin: Path | None = None):
        self.lire = lire
        self.chemin = chemin or settings.meta_token_path.parent / FICHIER_ETAT
        self.lectures_passage = 0

    def _etat(self) -> dict:
        try:
            return json.loads(self.chemin.read_text())
        except (OSError, ValueError):
            return {}

    def _ecrire(self, etat: dict, maintenant: dt.datetime) -> None:
        cache = {
            handle: entree
            for handle, entree in (etat.get("cache") or {}).items()
            if (lu := _date(entree.get("lu_le"))) is not None and maintenant - lu < CACHE_PURGE
        }
        etat = {**etat, "cache": cache}
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        descripteur, temporaire = tempfile.mkstemp(dir=self.chemin.parent, suffix=".tmp")
        with os.fdopen(descripteur, "w") as fichier:
            json.dump(etat, fichier)
        os.replace(temporaire, self.chemin)

    def __call__(self, compte: CompteArtiste, maintenant: dt.datetime) -> list[PostArtiste]:
        if not settings.meta_fb_access_token or not settings.meta_ig_user_id:
            return []
        # Jamais plus souvent que le PROPRE cache de ce lecteur : depuis que le
        # pipeline passe toutes les heures (et la veille rapide toutes les
        # 5 minutes), se fier au seul espacement d'ingestion ferait relire a la
        # composition un compte lu il y a une heure -- du quota Graph partage
        # depense pour rien, alors que le lecteur aurait de toute facon servi son
        # cache.
        fraicheur = dt.timedelta(
            hours=max(
                1,
                settings.instagram_intervalle_heures,
                settings.visuels_cache_lectures_heures,
            )
        )
        if compte.dernier_appel is not None and maintenant - compte.dernier_appel < fraicheur:
            # Le pipeline vient de lire ce compte : la base est a jour.
            return []
        etat = self._etat()
        en_cache = (etat.get("cache") or {}).get(compte.handle)
        lu_le = _date((en_cache or {}).get("lu_le"))
        if lu_le is not None and maintenant - lu_le < dt.timedelta(
            hours=settings.visuels_cache_lectures_heures
        ):
            return [_post(compte, item) for item in en_cache.get("items", [])]
        pause = _date(etat.get("pause_jusqu_a"))
        if pause is not None and maintenant < pause:
            raise LectureDifferee(
                f"lecture des comptes d'artistes en pause jusqu'a {pause:%H:%M} UTC (quota Graph)"
            )
        if self.lectures_passage >= settings.visuels_lectures_max_par_passage:
            raise LectureDifferee("budget de lectures de comptes du passage epuise")
        jour = maintenant.date().isoformat()
        du_jour = etat.get("lectures_du_jour", 0) if etat.get("jour") == jour else 0
        if du_jour >= settings.visuels_lectures_max_par_jour:
            raise LectureDifferee("budget de lectures de comptes du jour epuise")

        # Le budget est decompte AVANT l'appel : un appel parti compte, meme s'il
        # echoue -- c'est du quota consomme.
        self.lectures_passage += 1
        etat.update(jour=jour, lectures_du_jour=du_jour + 1)
        try:
            payload = self.lire(compte.handle, settings.instagram_max_items)
        except QuotaGraphAtteint as exc:
            fin = maintenant + dt.timedelta(minutes=settings.visuels_pause_quota_minutes)
            etat["pause_jusqu_a"] = fin.isoformat()
            self._ecrire(etat, maintenant)
            raise LectureDifferee(
                f"quota Graph atteint : pause de {settings.visuels_pause_quota_minutes} min"
            ) from exc
        except httpx.HTTPError as exc:
            self._ecrire(etat, maintenant)
            raise LectureDifferee(
                f"lecture de @{compte.handle} impossible ({type(exc).__name__})"
            ) from exc
        except RuntimeError as exc:
            # Compte non professionnel, renomme, jeton expire (signale ailleurs) :
            # pas de visuel de ce cote, sans bloquer la composition.
            self._ecrire(etat, maintenant)
            logger.warning("Compte @%s illisible pour les visuels : %s", compte.handle, exc)
            return []
        items = [_en_dict(item) for item in items_depuis_payload(payload)]
        etat["cache"] = {
            **(etat.get("cache") or {}),
            compte.handle: {"lu_le": maintenant.isoformat(), "items": items},
        }
        self._ecrire(etat, maintenant)
        return [_post(compte, item) for item in items]
