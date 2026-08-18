import datetime as dt
import html
import re
import time
from urllib.parse import parse_qs, urlparse

import feedparser
import httpx

from app.content.media import PHOTO, entree
from app.sources.base import RawItem, SourceAdapter


# Un pixel de suivi passerait pour un media valide dans tout le reste du
# pipeline -- et finirait en fond de cover.
_MINIATURE_MAX = 32
_IMG = re.compile(r"<img\b([^>]*)>", re.IGNORECASE)
_SRC = re.compile(r"""\bsrc=["']([^"']+)""", re.IGNORECASE)
_DIMENSION = re.compile(r"""\b(?:width|height)=["']?(\d+)""", re.IGNORECASE)


# Les recherches d'actualite passent par un redirecteur. Bing porte l'URL reelle
# dans son parametre `url=` -- contrairement a Google News, dont le lien est
# opaque et mene a une page de consentement, irresoluble meme avec un navigateur
# et le cookie de consentement (verifie). C'est ce qui a fait remplacer les
# sources Google par Bing.
#
# Sans ce decodage, `articles.url` pointerait vers le redirecteur : pas d'og:image
# donc pas d'illustration, et un post citerait Bing au lieu d'Ouest-France.
_REDIRECTEURS = ("bing.com/news/apiclick",)


def _url_reelle(lien: str | None) -> str | None:
    if not lien or not any(r in lien for r in _REDIRECTEURS):
        return lien
    cible = parse_qs(urlparse(lien).query).get("url", [None])[0]
    # Mieux vaut le lien de redirection qu'aucun lien : l'article existe, il sera
    # simplement mal illustre. Et on refuse ce qui n'est pas http(s) -- une
    # relative ou une tentative d'injection.
    if cible and cible.startswith(("http://", "https://")):
        return cible
    return lien


def _html_de(entry) -> str:
    """Le HTML d'une entree : `summary` et `content` peuvent porter l'image, et
    les flux ne s'accordent pas sur lequel des deux ils remplissent."""
    morceaux = [entry.get("summary") or ""]
    morceaux += [c.get("value") or "" for c in entry.get("content") or []]
    return "".join(morceaux)


def _images_du_html(html: str) -> list[str]:
    trouvees = []
    for attributs in _IMG.findall(html):
        src = _SRC.search(attributs)
        if not src:
            continue
        dimensions = [int(d) for d in _DIMENSION.findall(attributs)]
        if dimensions and min(dimensions) <= _MINIATURE_MAX:
            continue
        trouvees.append(src.group(1))
    return trouvees


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", text)).split())


class RssAdapter(SourceAdapter):
    """Adapter générique pour flux RSS/Atom (feedparser)."""

    # ASCII uniquement : les headers HTTP n'acceptent pas les accents.
    # Format "Mozilla/5.0 (compatible; ...)" : convention des bots légitimes,
    # mieux acceptée par les règles Cloudflare que les UA exotiques.
    user_agent = ("Mozilla/5.0 (compatible; music-news-radar/0.1; "
                  "+https://github.com/Hooriiiii/music-news-radar)")
    timeout = 20.0
    # Attente avant chaque requête, en secondes -- pour les hôtes qui rate-limitent
    request_delay = 0.0

    @property
    def feed_url(self) -> str:
        return self.source.url

    def fetch(self) -> list[RawItem]:
        return self.parse(self._download())

    def _download(self) -> bytes:
        if self.request_delay:
            time.sleep(self.request_delay)
        response = httpx.get(
            self.feed_url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.content

    def parse(self, raw: bytes) -> list[RawItem]:
        feed = feedparser.parse(raw)
        items = []
        for entry in feed.entries:
            url = _url_reelle(entry.get("link"))
            title = (entry.get("title") or "").strip()
            if not url or not title:
                continue
            items.append(
                RawItem(
                    url=url,
                    title=title,
                    summary=self._summary(entry),
                    published_at=self._published_at(entry),
                    media=self._media(entry),
                )
            )
        return items

    def _summary(self, entry) -> str | None:
        text = entry.get("summary") or ""
        if not text and entry.get("content"):
            text = entry.content[0].get("value", "")
        return _strip_html(text) or None

    def _published_at(self, entry) -> dt.datetime | None:
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if not parsed:
            return None
        return dt.datetime(*parsed[:6], tzinfo=dt.timezone.utc)

    def _media(self, entry) -> list[dict]:
        """Un flux RSS ne publie que des photos : les pieces jointes sont deja
        filtrees sur image/, et media_content d'un flux musical ne porte pas de
        fichier video. D'ou le type PHOTO fixe, sans inference."""
        urls = []
        for media in entry.get("media_content", []) + entry.get("media_thumbnail", []):
            if media.get("url"):
                urls.append(media["url"])
        for enclosure in entry.get("enclosures", []):
            if enclosure.get("type", "").startswith("image/") and enclosure.get("href"):
                urls.append(enclosure["href"])
        # En DERNIER, donc de moindre priorite : un <img> du corps peut etre un
        # logo, une pub ou un mouchard, la ou media_content est choisi par
        # l'editeur. Mesure sur 12 flux reels : 12 % d'articles illustres en
        # plus (Bandcamp Daily, XLR8R n'exposent RIEN d'autre).
        urls.extend(_images_du_html(_html_de(entry)))
        seen = set()
        deduped = [u for u in urls if not (u in seen or seen.add(u))]
        return [entree(PHOTO, u) for u in deduped]
