from app.sources.rss import RssAdapter


class RedditRssAdapter(RssAdapter):
    """Flux RSS natifs de Reddit (Atom) : URL du subreddit + suffixe .rss.

    Reddit limite agressivement les User-Agents génériques (429) — il faut un
    UA descriptif au format qu'ils recommandent.
    """

    user_agent = "macos:music-news-radar:v0.1 (electronic music news watcher)"
    request_delay = 5.0

    @property
    def feed_url(self) -> str:
        url = self.source.url.rstrip("/")
        if not url.endswith(".rss"):
            url += ".rss"
        return url
