"""Adapter Instagram via `business_discovery` de l'API Graph de Meta.

Gratuit, contrairement a l'API X qu'il remplace. Contrepartie a connaitre : seuls
les comptes PROFESSIONNELS (business ou creator) sont visibles. Un artiste
emergent en profil perso -- soit une bonne part du deal-flow A&R -- n'est pas
atteignable par cette voie. Cette source COMPLETE les RSS, elle ne les remplace
pas.
"""

import datetime as dt
import logging
import re

import httpx

from app.config import settings
from app.content.media import PHOTO, VIDEO, entree
from app.sources.base import RawItem, SourceAdapter

logger = logging.getLogger(__name__)

# Une caption Instagram n'a pas de titre : c'est un bloc libre qui commence
# souvent par l'essentiel et finit en hashtags. On prend la premiere phrase.
_FIN_DE_PHRASE = re.compile(r"(?<=[.!?])\s")
TITRE_MAX = 200
TITRE_PAR_DEFAUT = "Post Instagram"

_TYPES = {"IMAGE": PHOTO, "VIDEO": VIDEO}


def titre_depuis_caption(caption: str | None) -> str:
    """La premiere phrase de la caption, a defaut sa premiere ligne.

    Un titre vide casserait la dedup par hash (url_norm + titre) et rendrait le
    dashboard illisible : d'ou un repli explicite plutot qu'une chaine vide.
    """
    texte = (caption or "").strip()
    if not texte:
        return TITRE_PAR_DEFAUT
    premiere_ligne = texte.splitlines()[0].strip()
    phrase = _FIN_DE_PHRASE.split(premiere_ligne, maxsplit=1)[0].strip()
    # Une caption qui n'est QUE des hashtags ne donne pas un titre lisible.
    if not phrase or all(mot.startswith(("#", "@")) for mot in phrase.split()):
        return TITRE_PAR_DEFAUT
    if len(phrase) > TITRE_MAX:
        return phrase[: TITRE_MAX - 3] + "..."
    return phrase


def _summary_with_metrics(caption: str | None, noeud: dict, followers: int | None) -> str | None:
    """La caption, plus les metriques d'engagement -- meme idee que
    `_summary_with_metrics` de `x_api.py` : le scoring Claude juge la hotness
    sur ces chiffres, qu'on demande deja a l'API (`like_count,comments_count,
    followers_count`) sans jamais les exploiter jusqu'ici. Un post a 8000 likes
    et un post a 3 likes du meme compte arrivaient sinon indistinguables.
    """
    likes = noeud.get("like_count")
    comments = noeud.get("comments_count")
    if likes is None and comments is None and followers is None:
        return caption or None
    parts = [f"{likes or 0} likes", f"{comments or 0} commentaires"]
    if followers is not None:
        parts.append(f"{followers} abonnes")
    bandeau = f"[Instagram : {' · '.join(parts)}]"
    return f"{caption}\n\n{bandeau}" if caption else bandeau


def _date(timestamp: str | None) -> dt.datetime | None:
    """L'horodatage Meta ("2026-09-10T14:30:00+0000") en datetime aware UTC.

    Le reste du projet stocke des datetimes AWARE en UTC (`rss.py` pose
    `tzinfo=dt.timezone.utc`, `x_api.py` garde l'offset de `fromisoformat`,
    et `articles.published_at` est un `DateTime(timezone=True)`) : rendre un
    naif ici casserait la comparaison faite par `app/pipeline/ingest.py` a
    chaque item (`TypeError: can't compare offset-naive and offset-aware
    datetimes`), et la source ne pourrait plus jamais rien inserer.
    """
    if not timestamp:
        return None
    try:
        return dt.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z").astimezone(dt.UTC)
    except ValueError:
        return None


def _date_iso(valeur: str | None) -> dt.datetime | None:
    """L'etat relu. Une valeur illisible vaut "pas d'etat" : on preferera
    reingerer des posts deja vus (la dedup les ecarte) plutot que planter."""
    if not valeur:
        return None
    try:
        return dt.datetime.fromisoformat(valeur)
    except ValueError:
        return None


def _media(noeud: dict) -> list[dict]:
    """Les medias d'un post, carrousel aplati.

    Un carrousel porte ses enfants sous `children`, et n'a pas de `media_url`
    propre : sans cet aplatissement, un carrousel arriverait sans aucun media.
    """
    enfants = (noeud.get("children") or {}).get("data")
    if enfants:
        return [m for enfant in enfants for m in _media(enfant)]
    type_ = _TYPES.get(noeud.get("media_type"))
    url = noeud.get("media_url")
    if not type_ or not url:
        return []
    return [entree(type_, url, preview_url=noeud.get("thumbnail_url"))]


class InstagramAdapter(SourceAdapter):
    timeout = 30.0

    # Les champs demandes au compte cible. `children` est indispensable : un
    # carrousel n'a pas de media_url propre et reviendrait vide sans lui.
    CHAMPS_MEDIA = (
        "id,caption,media_type,media_url,thumbnail_url,permalink,"
        "timestamp,like_count,comments_count,"
        "children{media_type,media_url,thumbnail_url}"
    )

    @property
    def compte(self) -> str:
        """Le username cible, ecrit `ig:nom` ou en URL instagram.com complete."""
        url = self.source.url.strip().rstrip("/")
        if url.lower().startswith("ig:"):
            return url[3:].lstrip("@").strip()
        return url.rsplit("/", 1)[-1].lstrip("@")

    def parse(self, payload: dict) -> list[RawItem]:
        decouverte = payload.get("business_discovery") or {}
        followers = decouverte.get("followers_count")
        items = []
        for noeud in (decouverte.get("media") or {}).get("data", []):
            permalink = noeud.get("permalink")
            # Sans permalink il n'y a pas de cle de dedup : l'item serait
            # reingere a chaque run.
            if not permalink:
                continue
            caption = noeud.get("caption")
            items.append(
                RawItem(
                    url=permalink,
                    title=titre_depuis_caption(caption),
                    summary=_summary_with_metrics(caption, noeud, followers),
                    published_at=_date(noeud.get("timestamp")),
                    media=_media(noeud),
                )
            )
        return items

    def construire_url(self) -> tuple[str, dict]:
        """L'appel business_discovery : notre compte, le leur en parametre.

        Meta n'expose pas de "lire le compte X" ; on lit NOTRE noeud utilisateur
        en lui demandant le champ `business_discovery` d'un autre compte.
        """
        base = f"https://graph.facebook.com/{settings.meta_api_version}"
        champs = (
            f"business_discovery.username({self.compte})"
            f"{{username,followers_count,media.limit({settings.instagram_max_items})"
            f"{{{self.CHAMPS_MEDIA}}}}}"
        )
        return (
            f"{base}/{settings.meta_ig_user_id}",
            {"fields": champs, "access_token": settings.meta_access_token},
        )

    def _appeler(self) -> dict:
        url, params = self.construire_url()
        # Header ASCII pur : httpx plante avant l'envoi sur un accent.
        entetes = {"User-Agent": "KindaBot/1.0 (music-news-radar)"}
        with httpx.Client(timeout=self.timeout, headers=entetes) as client:
            reponse = client.get(url, params=params)
            reponse.raise_for_status()
            return reponse.json()

    def fetch(self) -> list[RawItem]:
        if not settings.meta_access_token or not settings.meta_ig_user_id:
            raise RuntimeError(
                "META_ACCESS_TOKEN / META_IG_USER_ID manquants dans .env — "
                "token de Page a creer sur developers.facebook.com"
            )
        etat = dict(self.source.state or {})
        depuis = _date_iso(etat.get("dernier_post"))

        items = self.parse(self._appeler())
        if depuis is not None:
            items = [i for i in items if i.published_at is None or i.published_at > depuis]

        dates = [i.published_at for i in items if i.published_at]
        if dates:
            # Reassignation ENTIERE : muter le dict en place ne persiste pas.
            etat["dernier_post"] = max(dates).isoformat()
            self.source.state = etat
        return items
