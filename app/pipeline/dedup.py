import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref_src"}


def normalize_url(url: str) -> str:
    """Forme canonique d'une URL pour la dédup : deux liens vers le même article
    doivent produire la même chaîne (tracking, fragment, slash final, casse)."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc.removesuffix(":80")
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc.removesuffix(":443")
    path = parts.path.rstrip("/")
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.startswith("utm_") and key not in _TRACKING_PARAMS
    ]
    return urlunsplit((scheme, netloc, path, urlencode(query_pairs), ""))


def compute_raw_hash(url: str, title: str) -> str:
    """Hash de dédup (sha256 hex, 64 caractères) sur url normalisée + titre normalisé."""
    normalized_title = " ".join(title.lower().split())
    payload = f"{normalize_url(url)}\n{normalized_title}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
