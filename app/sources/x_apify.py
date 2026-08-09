import datetime as dt
import logging

import httpx

from app.config import settings
from app.content.media import GIF, PHOTO, VIDEO
from app.sources.base import RawItem, SourceAdapter

logger = logging.getLogger(__name__)

APIFY_RUN_URL = "https://api.apify.com/v2/actors/{actor_id}/run-sync-get-dataset-items"

# Format de date des tweets : "Sat Jul 04 10:30:00 +0000 2026"
_TWITTER_DATE_FORMAT = "%a %b %d %H:%M:%S %z %Y"


class XApifyAdapter(SourceAdapter):
    """Adapter X (Twitter) via l'actor Apify apidojo/tweet-scraper (pay-per-result).

    Source payante (~0,40 $/1000 tweets, minimum 50 tweets facturés par requête),
    donc trois garde-fous :
    - maxTotalChargeUsd : plafond dur par run, appliqué côté Apify ;
    - maxItems : borne le nombre de tweets ramenés ;
    - throttle : au plus un run par X_MIN_FETCH_INTERVAL_HOURS (l'état est dans
      sources.state), quel que soit le rythme du pipeline.
    Le filtre de date `start` (état `since`) évite de re-payer les tweets déjà vus.
    """

    timeout = 300.0  # un run synchrone Apify peut prendre plusieurs minutes

    # Meme plafond que l'adapter API officielle : un debit enorme est invisible
    # sur Instagram, qui re-encode de toute facon.
    _DEBIT_MAX = 5_000_000

    @property
    def handle(self) -> str:
        url = self.source.url.strip().rstrip("/")
        handle = url.rsplit("/", 1)[-1] if "/" in url else url
        return handle.lstrip("@")

    def fetch(self) -> list[RawItem]:
        if not settings.apify_token:
            raise RuntimeError("APIFY_TOKEN manquante dans .env — compte à créer sur apify.com")
        if self._recently_fetched():
            logger.info("Source X %s sautée (fetch < %dh)", self.source.name,
                        settings.x_min_fetch_interval_hours)
            return []
        items = self._run_actor(self.build_input())
        raw_items = self.parse(items)
        self._update_state(raw_items)
        return raw_items

    def build_input(self) -> dict:
        payload = {
            "twitterHandles": [self.handle],
            "maxItems": settings.x_max_items,
            "sort": "Latest",
        }
        since = (self.source.state or {}).get("since")
        if since:
            payload["start"] = since
        return payload

    def build_run_params(self) -> dict:
        return {
            "token": settings.apify_token,
            "maxTotalChargeUsd": settings.apify_max_charge_usd,
            "format": "json",
        }

    def _run_actor(self, payload: dict) -> list[dict]:
        response = httpx.post(
            APIFY_RUN_URL.format(actor_id=settings.apify_actor_id),
            params=self.build_run_params(),
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def parse(self, items: list[dict]) -> list[RawItem]:
        raw_items = []
        for item in items:
            url = item.get("url") or item.get("twitterUrl")
            text = (item.get("fullText") or item.get("text") or "").strip()
            if not url or not text:
                continue  # item de contrôle de l'actor ou tweet vide
            author = (item.get("author") or {}).get("userName") or self.handle
            single_line = " ".join(text.split())
            title = f"@{author}: {single_line}"
            if len(title) > 200:
                title = title[:197] + "..."
            raw_items.append(
                RawItem(
                    url=url,
                    title=title,
                    summary=text,
                    published_at=self._parse_date(item.get("createdAt")),
                    media=self._media(item),
                )
            )
        return raw_items

    def _parse_date(self, value: str | None) -> dt.datetime | None:
        if not value:
            return None
        for parser in (
            lambda v: dt.datetime.strptime(v, _TWITTER_DATE_FORMAT),
            dt.datetime.fromisoformat,
        ):
            try:
                parsed = parser(value)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc)
        logger.warning("Date de tweet illisible : %r", value)
        return None

    def _media(self, item: dict) -> list[dict]:
        """Meme forme d'entree que l'API v1.1 (extended_entities.media) : `type`
        vaut photo/video/animated_gif, et les MP4 d'une video sont dans
        `video_info.variants` -- `media_url_https` seul n'est que la vignette."""
        media = (item.get("extendedEntities") or {}).get("media") or item.get("media") or []
        medias = []
        for entry in media:
            if not isinstance(entry, dict):
                continue
            normalise = self._media_depuis(entry)
            if normalise:
                medias.append(normalise)
        return medias

    def _media_depuis(self, media: dict) -> dict | None:
        """Normalise une entree extended_entities.media vers la forme typee."""
        type_ = media.get("type")
        if type_ in (None, "photo"):
            url = media.get("media_url_https") or media.get("url")
            return {"type": PHOTO, "url": url} if url else None
        if type_ in ("video", "animated_gif"):
            variants = (media.get("video_info") or {}).get("variants") or []
            mp4 = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
            sous_plafond = [v for v in mp4 if (v.get("bitrate") or 0) <= self._DEBIT_MAX]
            meilleur = max(sous_plafond or mp4, key=lambda v: v.get("bitrate") or 0, default=None)
            return {
                "type": VIDEO if type_ == "video" else GIF,
                "url": meilleur["url"] if meilleur else None,
                "preview_url": media.get("media_url_https"),
                "duration_ms": (media.get("video_info") or {}).get("duration_millis"),
            }
        return None

    def _recently_fetched(self) -> bool:
        last_run = (self.source.state or {}).get("last_run_at")
        if not last_run:
            return False
        try:
            last_run_at = dt.datetime.fromisoformat(last_run)
        except ValueError:
            return False
        interval = dt.timedelta(hours=settings.x_min_fetch_interval_hours)
        return dt.datetime.now(dt.timezone.utc) - last_run_at < interval

    def _update_state(self, raw_items: list[RawItem]) -> None:
        state = dict(self.source.state or {})
        state["last_run_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        dates = [i.published_at for i in raw_items if i.published_at is not None]
        if dates:
            state["since"] = max(dates).strftime("%Y-%m-%d")
        # réassignation complète : nécessaire pour que SQLAlchemy détecte le changement
        self.source.state = state
