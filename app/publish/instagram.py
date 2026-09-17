"""Publier sur Instagram par la connexion Instagram : conteneur, attente, publication.

Trois temps, toujours les memes : creer un conteneur (Meta telecharge les images
depuis leurs URL publiques), attendre qu'il passe a FINISHED, puis appeler
`media_publish`. Le deuxieme temps n'est pas optionnel : publier un conteneur
IN_PROGRESS echoue.

`preparer_carrousel` s'arrete AVANT la publication. C'est ce qui permet le mode
`apercu` et le script d'essai : un conteneur non publie n'est pas public et
expire seul en 24 h.

`preparer_reel` suit le meme contrat pour un conteneur `REELS`.
"""

import time

import httpx

from app.config import settings

BASE = "https://graph.instagram.com"
CARROUSEL_MAX = 10
ATTENTE_PAS_S = 3.0
ATTENTE_MAX_S = 300.0

# Un reel s'encode plus longtemps qu'une image. Meta recommande une lecture par
# minute pendant 5 minutes au plus ; on sonde plus serre et plus longtemps parce
# qu'a ce stade le creneau est deja reclame : un delai trop court le ferait
# passer en ECHEC sans retour. 40 lectures au pire.
ATTENTE_REEL_PAS_S = 15.0
ATTENTE_REEL_MAX_S = 600.0


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

    def _attendre(self, conteneur_id: str, pas: float | None = None, maximum: float | None = None) -> None:
        pas = ATTENTE_PAS_S if pas is None else pas
        maximum = ATTENTE_MAX_S if maximum is None else maximum
        ecoule = 0.0
        while True:
            statut = self._appel("GET", conteneur_id, {"fields": "status_code,status"})
            code = statut.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise ErreurInstagram(f"conteneur {conteneur_id} en {code} : {statut.get('status', '')}")
            if ecoule >= maximum:
                raise ErreurInstagram(f"conteneur {conteneur_id} toujours {code} apres {maximum:.0f} s")
            self.dormir(pas)
            ecoule += pas

    def preparer_carrousel(self, elements: list, legende: str) -> str:
        """Un element est l'URL d'une image, ou `{"type": "photo"|"video", "url": ...}`.

        Un enfant VIDEO (`media_type=VIDEO`, `is_carousel_item`) s'encode chez Meta
        comme un reel : tous les enfants sont crees d'abord (l'encodage avance en
        parallele), puis chaque video est sondee jusqu'a FINISHED, au rythme des
        reels, AVANT de creer le parent -- un parent aux enfants non prets echoue.
        """
        if not elements:
            raise ErreurInstagram("aucune image a publier")
        elements = [
            {"type": "photo", "url": e} if isinstance(e, str) else e for e in elements[:CARROUSEL_MAX]
        ]
        if len(elements) == 1:
            if elements[0]["type"] == "video":
                raise ErreurInstagram("une video seule n'est pas un carrousel : publier un reel")
            conteneur = self._conteneur(image_url=elements[0]["url"], caption=legende)
        else:
            enfants, videos = [], []
            for element in elements:
                if element["type"] == "video":
                    enfant = self._conteneur(
                        media_type="VIDEO", video_url=element["url"], is_carousel_item="true"
                    )
                    videos.append(enfant)
                else:
                    enfant = self._conteneur(image_url=element["url"], is_carousel_item="true")
                enfants.append(enfant)
            for enfant in videos:
                self._attendre(enfant, pas=ATTENTE_REEL_PAS_S, maximum=ATTENTE_REEL_MAX_S)
            conteneur = self._conteneur(
                media_type="CAROUSEL", children=",".join(enfants), caption=legende
            )
        self._attendre(conteneur)
        return conteneur

    def preparer_reel(self, video_url: str, cover_url: str, legende: str, *, share_to_feed: bool = True) -> str:
        # cover_url explicite plutot que thumb_offset : la cover est deja la
        # premiere frame, mais Instagram reencode et peut la perdre. Meta retient
        # cover_url quand les deux sont fournis.
        conteneur = self._conteneur(
            media_type="REELS",
            video_url=video_url,
            cover_url=cover_url,
            caption=legende,
            share_to_feed="true" if share_to_feed else "false",
        )
        self._attendre(conteneur, pas=ATTENTE_REEL_PAS_S, maximum=ATTENTE_REEL_MAX_S)
        return conteneur

    def publier(self, conteneur_id: str) -> str:
        return self._appel("POST", f"{self.ig_user_id}/media_publish", {"creation_id": conteneur_id})["id"]
