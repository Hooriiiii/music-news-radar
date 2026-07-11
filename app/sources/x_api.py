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

    def __init__(self, source):
        super().__init__(source)
        # Peuplé par le pipeline (run_ingest) pour les sources radar : la liste
        # des artistes du moment. L'adapter reste agnostique de la base.
        self.radar_artists: list[str] = []

    @property
    def handle(self) -> str:
        url = self.source.url.strip().rstrip("/")
        handle = url.rsplit("/", 1)[-1] if "/" in url else url
        return handle.lstrip("@")

    @property
    def is_search(self) -> bool:
        """La source est-elle une recherche (hashtags) plutôt qu'un profil ?

        Profil : URL x.com/twitter.com, @handle ou handle nu.
        Recherche : préfixe "search:"/"ugc:", ou présence de # / d'espaces.
        """
        url = self.source.url.strip()
        if url.lower().startswith(("search:", "ugc:", "radar:")):
            return True
        if "://" in url or url.startswith("@"):
            return False
        return "#" in url or " " in url

    @property
    def is_radar(self) -> bool:
        """Source radar : la requête est construite dynamiquement à partir des
        artistes du moment (self.radar_artists), pas d'une url figée."""
        return self.source.url.strip().lower().startswith("radar:")

    @property
    def is_ugc(self) -> bool:
        """Mode UGC : on veut du contenu brut filmé par des fans, pas des clips
        d'agrégateurs -- la sélection exige un compte de taille modeste."""
        return self.source.url.strip().lower().startswith("ugc:")

    def fetch(self) -> list[RawItem]:
        if not settings.x_bearer_token:
            raise RuntimeError(
                "X_BEARER_TOKEN manquante dans .env — token à créer sur developer.x.com"
            )
        state = dict(self.source.state or {})

        if self.is_search:
            # Radar sans artiste (démarrage à froid) : aucune requête à lancer,
            # on ne paye rien
            if self.is_radar and not self.build_search_query():
                logger.info("Radar X %s sauté (aucun artiste du moment)", self.source.name)
                return []
            # Volume potentiellement énorme sur un hashtag populaire : throttle
            # pour plafonner le coût, quel que soit le rythme du pipeline
            if self._recently_fetched(state):
                logger.info("Recherche X %s sautée (fetch < %dh)", self.source.name,
                            settings.x_min_fetch_interval_hours)
                return []
            payload = self._get("/tweets/search/recent", params=self.build_search_params(state))
            items = self.parse_search(payload)
            new_state = {**state}
        else:
            user_id = state.get("user_id") or self._lookup_user_id()
            payload = self._get(f"/users/{user_id}/tweets", params=self.build_params(state))
            items = self.parse(payload)
            new_state = {**state, "user_id": user_id}

        new_state["last_run_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        newest_id = (payload.get("meta") or {}).get("newest_id")
        if newest_id:
            new_state["since_id"] = newest_id
        # réassignation complète : nécessaire pour que SQLAlchemy détecte le changement
        self.source.state = new_state
        return items

    def build_search_query(self) -> str:
        """Query de recherche avec le média OBLIGATOIRE (exigence éditoriale :
        chaque actu doit être exploitable en template photo/vidéo) et le bruit
        exclu — le filtrage se fait côté X, on ne paye pas les tweets écartés.

        Radar : la query est construite à partir des artistes du moment, avec
        has:videos (on cherche des vidéos live). Sans artiste -> query vide."""
        if self.is_radar:
            if not self.radar_artists:
                return ""
            names = " OR ".join(f'"{a}"' for a in self.radar_artists)
            base = f"({names})"
            for operator in ("has:videos", "-is:retweet", "-is:reply"):
                base += f" {operator}"
            return base

        query = self.source.url.strip()
        for prefix in ("search:", "ugc:"):
            if query.lower().startswith(prefix):
                query = query[len(prefix):].strip()
                break
        base = f"({query})" if " OR " in query and not query.startswith("(") else query
        for operator in ("has:media", "-is:retweet", "-is:reply"):
            if operator not in base:
                base += f" {operator}"
        return base

    def build_search_params(self, state: dict) -> dict:
        params = {
            "query": self.build_search_query(),
            "max_results": settings.x_search_max_results,
            # relevancy : le classement engagement de X -- les max_results tweets
            # qu'on paye sont les plus pertinents de la fenêtre, pas les derniers
            "sort_order": "relevancy",
            "tweet.fields": "created_at,public_metrics",
            "expansions": "attachments.media_keys,author_id",
            "media.fields": "url,preview_image_url",
            "user.fields": "username,verified,public_metrics",
        }
        if state.get("since_id"):
            params["since_id"] = state["since_id"]
        return params

    def parse_search(self, payload: dict) -> list[RawItem]:
        """Comme parse(), mais multi-auteurs, média strictement obligatoire et
        sélection par popularité : compte vérifié OU engagement suffisant."""
        users = {
            user["id"]: user
            for user in (payload.get("includes") or {}).get("users", [])
            if user.get("id")
        }
        media_by_key = {
            media["media_key"]: media
            for media in (payload.get("includes") or {}).get("media", [])
            if media.get("media_key")
        }
        items = []
        for tweet in payload.get("data") or []:
            tweet_id = tweet.get("id")
            text = (tweet.get("text") or "").strip()
            if not tweet_id or not text:
                continue
            media_urls = []
            for key in (tweet.get("attachments") or {}).get("media_keys", []):
                media = media_by_key.get(key)
                if media:
                    url = media.get("url") or media.get("preview_image_url")
                    if url:
                        media_urls.append(url)
            if not media_urls:
                continue  # média obligatoire en mode recherche
            if self.is_radar and not self._mentions_radar_artist(text):
                continue  # anti-spam : la vidéo doit vraiment parler de l'artiste
            author = users.get(tweet.get("author_id")) or {}
            if not self._passes_popularity(tweet, author):
                continue
            username = author.get("username") or "i"  # x.com/i/status/ marche toujours
            single_line = " ".join(text.split())
            title = f"@{username}: {single_line}"
            if len(title) > 200:
                title = title[:197] + "..."
            published_at = None
            if tweet.get("created_at"):
                published_at = dt.datetime.fromisoformat(
                    tweet["created_at"].replace("Z", "+00:00")
                )
            items.append(
                RawItem(
                    url=f"https://x.com/{username}/status/{tweet_id}",
                    title=title,
                    summary=self._summary_with_metrics(text, tweet, author),
                    published_at=published_at,
                    media_urls=media_urls,
                )
            )
        return items

    def _mentions_radar_artist(self, text: str) -> bool:
        lowered = text.lower()
        return any(artist.lower() in lowered for artist in self.radar_artists)

    def _passes_popularity(self, tweet: dict, author: dict) -> bool:
        metrics = tweet.get("public_metrics") or {}
        engaged = (
            metrics.get("like_count", 0) >= settings.x_search_min_likes
            or metrics.get("reply_count", 0) >= settings.x_search_min_replies
        )
        if self.is_ugc:
            # UGC : moment qui décolle organiquement, posté par un compte
            # modeste (un gros compte = média/agrégateur, même viral)
            followers = (author.get("public_metrics") or {}).get("followers_count", 0)
            return engaged and followers <= settings.x_ugc_max_followers
        return bool(author.get("verified")) or engaged

    def _summary_with_metrics(self, text: str, tweet: dict, author: dict) -> str:
        """Les métriques sont données à voir au scoring Claude (elles nourrissent
        le jugement de hotness) puis remplacées par son résumé éditorial."""
        metrics = tweet.get("public_metrics") or {}
        parts = [f"{metrics.get('like_count', 0)} likes",
                 f"{metrics.get('reply_count', 0)} réponses",
                 f"{metrics.get('retweet_count', 0)} reposts"]
        followers = (author.get("public_metrics") or {}).get("followers_count")
        if followers is not None:
            parts.append(f"{followers} abonnés")
        badge = "compte vérifié" if author.get("verified") else "compte non vérifié"
        kind = " · clip UGC filmé par un fan" if self.is_ugc else ""
        return f"{text}\n\n[X : {' · '.join(parts)} · {badge}{kind}]"

    def _recently_fetched(self, state: dict) -> bool:
        last_run = state.get("last_run_at")
        if not last_run:
            return False
        try:
            last_run_at = dt.datetime.fromisoformat(last_run)
        except ValueError:
            return False
        interval = dt.timedelta(hours=settings.x_min_fetch_interval_hours)
        return dt.datetime.now(dt.timezone.utc) - last_run_at < interval

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
