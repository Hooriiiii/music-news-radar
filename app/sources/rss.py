import datetime as dt
import html
import re
import time

import feedparser
import httpx

from app.sources.base import RawItem, SourceAdapter

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", text)).split())


class RssAdapter(SourceAdapter):
    """Adapter générique pour flux RSS/Atom (feedparser)."""

    # ASCII uniquement : les headers HTTP n'acceptent pas les accents
    user_agent = "music-news-radar/0.1 (music news aggregator)"
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
            url = entry.get("link")
            title = (entry.get("title") or "").strip()
            if not url or not title:
                continue
            items.append(
                RawItem(
                    url=url,
                    title=title,
                    summary=self._summary(entry),
                    published_at=self._published_at(entry),
                    media_urls=self._media_urls(entry),
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

    def _media_urls(self, entry) -> list[str]:
        urls = []
        for media in entry.get("media_content", []) + entry.get("media_thumbnail", []):
            if media.get("url"):
                urls.append(media["url"])
        for enclosure in entry.get("enclosures", []):
            if enclosure.get("type", "").startswith("image/") and enclosure.get("href"):
                urls.append(enclosure["href"])
        seen = set()
        return [u for u in urls if not (u in seen or seen.add(u))]
