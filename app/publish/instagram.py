"""Publier sur Instagram par la connexion Instagram : conteneur, attente, publication.

Trois temps, toujours les memes : creer un conteneur (Meta telecharge les images
depuis leurs URL publiques), attendre qu'il passe a FINISHED, puis appeler
`media_publish`. Le deuxieme temps n'est pas optionnel : publier un conteneur
IN_PROGRESS echoue.

`preparer_carrousel` s'arrete AVANT la publication. C'est ce qui permet le mode
`apercu` et le script d'essai : un conteneur non publie n'est pas public et
expire seul en 24 h.
"""

import time

import httpx

from app.config import settings

BASE = "https://graph.instagram.com"
CARROUSEL_MAX = 10
ATTENTE_PAS_S = 3.0
ATTENTE_MAX_S = 300.0


class ErreurInstagram(RuntimeError):
    """Refus ou echec cote Meta. Message affichable."""


class ClientInstagram:
    def __init__(
        self,
        jeton: str,
        ig_user_id: str,
        *,
        http=None,
        dormir=time.sleep,
        version: str | None = None,
    ):
        self.jeton = jeton
        self.ig_user_id = ig_user_id
        self.http = http or httpx.Client(timeout=60)
        self.dormir = dormir
        self.base = f"{BASE}/{version or settings.meta_api_version}"

    def _appel(self, methode: str, chemin: str, params: dict) -> dict:
        reponse = self.http.request(
            methode, f"{self.base}/{chemin}", params={**params, "access_token": self.jeton}
        )
        try:
            donnees = reponse.json()
        except ValueError as exc:
            raise ErreurInstagram(f"{chemin} : reponse illisible (HTTP {reponse.status_code})") from exc
        if reponse.status_code >= 400 or "error" in donnees:
            erreur = donnees.get("error") or {}
            # error_user_msg est le texte destine a un humain quand Meta le fournit.
            detail = erreur.get("error_user_msg") or erreur.get("message") or f"HTTP {reponse.status_code}"
            raise ErreurInstagram(f"{chemin} : {detail}")
        return donnees

    def _conteneur(self, **params) -> str:
        return self._appel("POST", f"{self.ig_user_id}/media", params)["id"]

    def _attendre(self, conteneur_id: str) -> None:
        ecoule = 0.0
        while True:
            statut = self._appel("GET", conteneur_id, {"fields": "status_code,status"})
            code = statut.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise ErreurInstagram(f"conteneur {conteneur_id} en {code} : {statut.get('status', '')}")
            if ecoule >= ATTENTE_MAX_S:
                raise ErreurInstagram(
                    f"conteneur {conteneur_id} toujours {code} apres {ATTENTE_MAX_S:.0f} s"
                )
            self.dormir(ATTENTE_PAS_S)
            ecoule += ATTENTE_PAS_S

    def preparer_carrousel(self, urls: list[str], legende: str) -> str:
        if not urls:
            raise ErreurInstagram("aucune image a publier")
        urls = urls[:CARROUSEL_MAX]
        if len(urls) == 1:
            conteneur = self._conteneur(image_url=urls[0], caption=legende)
        else:
            enfants = [self._conteneur(image_url=url, is_carousel_item="true") for url in urls]
            conteneur = self._conteneur(
                media_type="CAROUSEL", children=",".join(enfants), caption=legende
            )
        self._attendre(conteneur)
        return conteneur

    def publier(self, conteneur_id: str) -> str:
        return self._appel("POST", f"{self.ig_user_id}/media_publish", {"creation_id": conteneur_id})["id"]
