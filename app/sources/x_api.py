import datetime as dt
import logging

import httpx

from app.config import settings
from app.sources.base import RawItem, SourceAdapter

logger = logging.getLogger(__name__)

API_BASE = "https://api.x.com/2"


class XApiAdapter(SourceAdapter):
    """Adapter X via l'API officielle v2 en mode pay-per-use (~0,005 $/tweet lu).

    Le since_id natif de l'API garantit qu'un tweet n'est lu (et payé) qu'une
    seule fois : chaque run ne ramène que les tweets plus récents que le dernier
    vu, et un run sans nouveauté ne coûte rien. L'user_id est résolu une fois
    puis caché dans sources.state.
    """

    timeout = 30.0

    @property
    def handle(self) -> str:
        url = self.source.url.strip().rstrip("/")
        handle = url.rsplit("/", 1)[-1] if "/" in url else url
        return handle.lstrip("@")

    def fetch(self) -> list[RawItem]:
        if not settings.x_bearer_token:
            raise RuntimeError(
                "X_BEARER_TOKEN manquante dans .env — token à créer sur developer.x.com"
            )
        state = dict(self.source.state or {})
        user_id = state.get("user_id") or self._lookup_user_id()

        payload = self._get(f"/users/{user_id}/tweets", params=self.build_params(state))
        items = self.parse(payload)

        new_state = {
            **state,
            "user_id": user_id,
            "last_run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        newest_id = (payload.get("meta") or {}).get("newest_id")
        if newest_id:
            new_state["since_id"] = newest_id
        # réassignation complète : nécessaire pour que SQLAlchemy détecte le changement
        self.source.state = new_state
        return items

    def build_params(self, state: dict) -> dict:
        params = {
            "max_results": settings.x_max_items,
            "exclude": "retweets,replies",
            "tweet.fields": "created_at",
            "expansions": "attachments.media_keys",
            "media.fields": "url,preview_image_url",
        }
        if state.get("since_id"):
            params["since_id"] = state["since_id"]
        return params

    def parse(self, payload: dict) -> list[RawItem]:
        tweets = payload.get("data") or []
        media_by_key = {
            media["media_key"]: media
            for media in (payload.get("includes") or {}).get("media", [])
            if media.get("media_key")
        }
        handle = self.handle
        items = []
        for tweet in tweets:
            tweet_id = tweet.get("id")
            text = (tweet.get("text") or "").strip()
            if not tweet_id or not text:
                continue
            single_line = " ".join(text.split())
            title = f"@{handle}: {single_line}"
            if len(title) > 200:
                title = title[:197] + "..."
            media_urls = []
            for key in (tweet.get("attachments") or {}).get("media_keys", []):
                media = media_by_key.get(key)
                if media:
                    url = media.get("url") or media.get("preview_image_url")
                    if url:
                        media_urls.append(url)
            published_at = None
            if tweet.get("created_at"):
                published_at = dt.datetime.fromisoformat(
                    tweet["created_at"].replace("Z", "+00:00")
                )
            items.append(
                RawItem(
                    url=f"https://x.com/{handle}/status/{tweet_id}",
                    title=title,
                    summary=text,
                    published_at=published_at,
                    media_urls=media_urls,
                )
            )
        return items

    def _lookup_user_id(self) -> str:
        payload = self._get(f"/users/by/username/{self.handle}", params={})
        user_id = (payload.get("data") or {}).get("id")
        if not user_id:
            raise RuntimeError(f"Compte X introuvable : @{self.handle} ({payload})")
        return user_id

    def _get(self, path: str, params: dict) -> dict:
        response = httpx.get(
            f"{API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {settings.x_bearer_token}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
