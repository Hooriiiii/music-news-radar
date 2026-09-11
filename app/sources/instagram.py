"""Adapter Instagram via `business_discovery` de l'API Graph de Meta.

Gratuit, contrairement a l'API X qu'il remplace. Contrepartie a connaitre : seuls
les comptes PROFESSIONNELS (business ou creator) sont visibles. Un artiste
emergent en profil perso -- soit une bonne part du deal-flow A&R -- n'est pas
atteignable par cette voie. Cette source COMPLETE les RSS, elle ne les remplace
pas.
"""

import datetime as dt
import re

from app.content.media import PHOTO, VIDEO, entree
from app.sources.base import RawItem, SourceAdapter

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


def _date(timestamp: str | None) -> dt.datetime | None:
    """L'horodatage Meta ("2026-09-10T14:30:00+0000") en UTC naif.

    Le reste du projet stocke des datetimes naifs en UTC ; laisser passer un
    datetime aware provoquerait des comparaisons impossibles cote retention.
    """
    if not timestamp:
        return None
    try:
        return (
            dt.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z")
            .astimezone(dt.UTC)
            .replace(tzinfo=None)
        )
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
    @property
    def compte(self) -> str:
        """Le username cible, ecrit `ig:nom` ou en URL instagram.com complete."""
        url = self.source.url.strip().rstrip("/")
        if url.lower().startswith("ig:"):
            return url[3:].lstrip("@").strip()
        return url.rsplit("/", 1)[-1].lstrip("@")

    def parse(self, payload: dict) -> list[RawItem]:
        decouverte = payload.get("business_discovery") or {}
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
                    summary=caption or None,
                    published_at=_date(noeud.get("timestamp")),
                    media=_media(noeud),
                )
            )
        return items

    def fetch(self) -> list[RawItem]:
        raise NotImplementedError
