import datetime as dt
import logging

import httpx

from app.config import settings
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
                    media_urls=self._media_urls(item),
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

    def _media_urls(self, item: dict) -> list[str]:
        media = (item.get("extendedEntities") or {}).get("media") or item.get("media") or []
        urls = []
        for entry in media:
            if isinstance(entry, dict):
                url = entry.get("media_url_https") or entry.get("url")
                if url:
                    urls.append(url)
            elif isinstance(entry, str):
                urls.append(entry)
        return urls

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
