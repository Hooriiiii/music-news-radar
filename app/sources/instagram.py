"""Adapter Instagram via `business_discovery` de l'API Graph de Meta.

Gratuit, contrairement a l'API X qu'il remplace. Contrepartie a connaitre : seuls
les comptes PROFESSIONNELS (business ou creator) sont visibles. Un artiste
emergent en profil perso -- soit une bonne part du deal-flow A&R -- n'est pas
atteignable par cette voie. Cette source COMPLETE les RSS, elle ne les remplace
pas.

Jeton : `META_FB_ACCESS_TOKEN`, un jeton utilisateur FACEBOOK (EAA...). Jamais
`META_ACCESS_TOKEN`, qui est le jeton de connexion Instagram de la publication
(graph.instagram.com) et ne sait pas appeler business_discovery.
"""

import datetime as dt
import logging
import re
import unicodedata

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

# Code d'erreur Graph d'un jeton expire, revoque ou invalide.
JETON_INVALIDE = 190

# Codes Graph d'une limite d'appels atteinte (application, utilisateur, page,
# appels de l'API). Le quota est PARTAGE avec le pipeline : l'epuiser depuis la
# composition bloquerait l'ingestion.
CODES_QUOTA = frozenset({4, 17, 32, 613})

# Les champs demandes au compte cible. `children` est indispensable : un
# carrousel n'a pas de media_url propre et reviendrait vide sans lui.
CHAMPS_MEDIA = (
    "id,caption,media_type,media_url,thumbnail_url,permalink,"
    "timestamp,like_count,comments_count,"
    "children{media_type,media_url,thumbnail_url}"
)


# Header ASCII pur : httpx plante avant l'envoi sur un accent.
ENTETES = {"User-Agent": "KindaBot/1.0 (music-news-radar)"}


class QuotaGraphAtteint(RuntimeError):
    """Meta refuse l'appel pour cause de quota. Message sans jeton."""


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


def compte_depuis_url(url: str) -> str:
    """Le username cible d'une source, ecrite `ig:nom` ou en URL instagram.com.

    Fonction de module et pas seulement propriete de l'adapter : le credit d'un
    post (`app/content/credit.py`) en a besoin sans instancier d'adapter.
    """
    url = url.strip().rstrip("/")
    if url.lower().startswith("ig:"):
        return url[3:].lstrip("@").strip()
    return url.rsplit("/", 1)[-1].lstrip("@")


# Garde-fou « post quasi vide » (voir `scoring.appliquer_garde_fous`) : sous ce
# nombre de mots significatifs, une caption ne porte pas d'information.
MOTS_MIN_CAPTION = 6

# Le bandeau de metriques que `_summary_with_metrics` accole a la caption. Il doit
# pouvoir etre retire : ses chiffres ne sont pas des mots de la caption.
_BANDEAU_DEBUT = "[Instagram : "
_BANDEAU_FIN = re.compile(r"(?:^|\n\n)\[Instagram : [^\n]*\]\s*$")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_HASHTAG_MENTION = re.compile(r"[#@]\S+")


def caption_depuis_summary(summary: str | None) -> str:
    """La caption brute d'un summary construit par `_summary_with_metrics`.

    Valable seulement AVANT le scoring : `apply_score` remplace ensuite le summary
    par le resume du modele, et la caption est perdue.
    """
    if not summary:
        return ""
    return _BANDEAU_FIN.sub("", summary).strip()


def mots_significatifs(texte: str | None) -> int:
    """Les mots qui portent du sens, une fois retires URLs, hashtags, @mentions,
    emojis, ponctuation et unicode decoratif.

    NFKC d'abord : les lettres « mathematiques » (𝚖𝚊𝚍𝚎) redeviennent de l'ASCII.
    Un mot compte s'il contient au moins une lettre ou un chiffre (« I-VIEW »,
    « feat. », « D-3 » comptent pour un).
    """
    texte = unicodedata.normalize("NFKC", texte or "")
    texte = _URL.sub(" ", texte)
    texte = _HASHTAG_MENTION.sub(" ", texte)
    return sum(1 for jeton in texte.split() if any(c.isalnum() for c in jeton))


def caption_quasi_vide(caption: str | None) -> bool:
    return mots_significatifs(caption) < MOTS_MIN_CAPTION


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
    bandeau = f"{_BANDEAU_DEBUT}{' · '.join(parts)}]"
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

    Meta OMET `media_url` quand le media contient du contenu protege (musique) --
    documente, et constate sur 10 reels sur 10 d'un compte d'artiste. La video
    reste une entree VIDEO, sans URL : c'est ce qui la garde identifiable comme
    reel, et recuperable par yt-dlp au gel (`media.video_recuperable`). Sa
    vignette, en `preview_url`, sert de fond de cover et de vignette au
    dashboard, qui lisent deja ce champ.
    """
    enfants = (noeud.get("children") or {}).get("data")
    if enfants:
        return [m for enfant in enfants for m in _media(enfant)]
    type_ = _TYPES.get(noeud.get("media_type"))
    url = noeud.get("media_url")
    vignette = noeud.get("thumbnail_url")
    if type_ == VIDEO and not url:
        return [entree(VIDEO, None, preview_url=vignette)]
    if not type_ or not url:
        return []
    return [entree(type_, url, preview_url=vignette)]


def _message_erreur(reponse: httpx.Response) -> str:
    """Une erreur Graph lisible, qui finit dans `sources.state.last_error` et
    dans la notification du run. Le jeton n'y figure jamais : l'URL de la
    requete le porte en parametre, d'ou un message construit a la main plutot
    que celui de `raise_for_status()`."""
    try:
        erreur = reponse.json().get("error") or {}
    except ValueError:
        erreur = {}
    code = erreur.get("code")
    detail = erreur.get("message") or reponse.reason_phrase
    if code == JETON_INVALIDE:
        return (
            f"META_FB_ACCESS_TOKEN expire ou invalide (Meta code 190 : {detail}) -- "
            "le renouveler dans le Graph API Explorer, puis debogueur de jeton -> "
            "« Etendre » (60 jours), et le reporter dans .env"
        )
    return f"Meta HTTP {reponse.status_code} (code {code}) : {detail}"


def _code_erreur(reponse: httpx.Response):
    try:
        return (reponse.json().get("error") or {}).get("code")
    except ValueError:
        return None


def url_business_discovery(compte: str, limite: int) -> tuple[str, dict]:
    """L'appel business_discovery : notre compte, le leur en parametre.

    Meta n'expose pas de "lire le compte X" ; on lit NOTRE noeud utilisateur en
    lui demandant le champ `business_discovery` d'un autre compte.
    """
    base = f"https://graph.facebook.com/{settings.meta_api_version}"
    champs = (
        f"business_discovery.username({compte})"
        f"{{username,followers_count,media.limit({limite})"
        f"{{{CHAMPS_MEDIA}}}}}"
    )
    return (
        f"{base}/{settings.meta_ig_user_id}",
        {"fields": champs, "access_token": settings.meta_fb_access_token},
    )


def lire_compte(compte: str, limite: int, *, client: httpx.Client | None = None) -> dict:
    """Le payload business_discovery d'un compte.

    `QuotaGraphAtteint` sur un code de quota, `RuntimeError` lisible (sans
    jeton) sur toute autre erreur Graph.
    """
    url, params = url_business_discovery(compte, limite)
    propre = client is None
    client = client or httpx.Client(timeout=30.0, headers=ENTETES)
    try:
        reponse = client.get(url, params=params)
        if reponse.is_error:
            if _code_erreur(reponse) in CODES_QUOTA:
                raise QuotaGraphAtteint(_message_erreur(reponse))
            raise RuntimeError(_message_erreur(reponse))
        return reponse.json()
    finally:
        if propre:
            client.close()


def items_depuis_payload(payload: dict) -> list[RawItem]:
    """Les items d'un payload business_discovery. Partage entre l'ingestion et
    la lecture en direct de la composition (`app/content/lecture_artistes.py`)."""
    decouverte = payload.get("business_discovery") or {}
    followers = decouverte.get("followers_count")
    items = []
    for noeud in (decouverte.get("media") or {}).get("data", []):
        permalink = noeud.get("permalink")
        # Sans permalink il n'y a pas de cle de dedup : l'item serait reingere a
        # chaque run.
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


class InstagramAdapter(SourceAdapter):
    timeout = 30.0

    CHAMPS_MEDIA = CHAMPS_MEDIA

    @property
    def compte(self) -> str:
        """Le username cible, ecrit `ig:nom` ou en URL instagram.com complete."""
        return compte_depuis_url(self.source.url)

    def parse(self, payload: dict) -> list[RawItem]:
        return items_depuis_payload(payload)

    def construire_url(self) -> tuple[str, dict]:
        return url_business_discovery(self.compte, settings.instagram_max_items)

    def _appeler(self) -> dict:
        with httpx.Client(timeout=self.timeout, headers=ENTETES) as client:
            return lire_compte(self.compte, settings.instagram_max_items, client=client)

    def a_lire(self, maintenant: dt.datetime) -> bool:
        """Le compte est-il du a ce passage ?

        Jamais lu : seulement sur son creneau (heure modulo N == id modulo N),
        pour qu'un lot de comptes ajoutes d'un coup ne parte pas dans la meme
        heure. Deja lu : quand N heures (moins un quart d'heure de marge, les
        passages launchd ne tombent pas pile) se sont ecoulees.
        """
        intervalle = max(1, settings.instagram_intervalle_heures)
        dernier = _date_iso((self.source.state or {}).get("dernier_appel"))
        if dernier is None:
            heure = int(maintenant.timestamp() // 3600)
            return heure % intervalle == (self.source.id or 0) % intervalle
        return maintenant - dernier >= dt.timedelta(hours=intervalle, minutes=-15)

    def fetch(self, maintenant: dt.datetime | None = None) -> list[RawItem]:
        # Sans jeton, la source est SAUTEE comme le sont les alertes sans
        # webhook : l'absence est un choix de configuration, pas une panne.
        if not settings.meta_fb_access_token or not settings.meta_ig_user_id:
            logger.info(
                "Source Instagram %s sautee : META_FB_ACCESS_TOKEN ou META_IG_USER_ID absent",
                self.source.name,
            )
            return []
        maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
        if not self.a_lire(maintenant):
            return []
        etat = dict(self.source.state or {})
        depuis = _date_iso(etat.get("dernier_post"))

        items = self.parse(self._appeler())
        etat["dernier_appel"] = maintenant.isoformat()
        if depuis is not None:
            items = [i for i in items if i.published_at is None or i.published_at > depuis]

        dates = [i.published_at for i in items if i.published_at]
        if dates:
            etat["dernier_post"] = max(dates).isoformat()
        # Reassignation ENTIERE : muter le dict en place ne persiste pas.
        self.source.state = etat
        return items
