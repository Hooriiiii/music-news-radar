"""Ce qu'on peut faire des medias d'un article : le predicat « video exploitable ».

Zero dependance -- ni base, ni reseau, ni Chromium. Ce module repond a une seule
question, et c'est elle qui decide si le switch REELS apparait dans le dashboard.

Un tweet video etait jusqu'ici indistinguable d'un tweet photo : l'API X ne renvoie
`url` que pour les photos et rend une VIGNETTE pour une video. D'ou les medias
types, dont ce module est le consommateur principal.
"""

from urllib.parse import urlsplit

PHOTO = "photo"
VIDEO = "video"
GIF = "gif"

# Limites Instagram. Un reel accepte plus long qu'une slide de carrousel, d'ou deux
# valeurs et non une.
DUREE_MAX_REEL_MS = 90_000
DUREE_MAX_SLIDE_MS = 60_000

# Les six cles sont TOUJOURS presentes, a None quand la source ne les fournit pas.
# Une forme uniforme evite qu'un consommateur ait a deviner lesquelles existent
# selon la source et le type -- une photo RSS et une video X doivent se lire
# pareil.
CLES = ("type", "url", "preview_url", "duration_ms", "width", "height")


def entree(type_: str, url: str | None, **extras) -> dict:
    """Une entree de media a la forme complete, champs absents a None."""
    return {"type": type_, "url": url, **{c: extras.get(c) for c in CLES[2:]}}


def videos_exploitables(media: list[dict] | None, duree_max_ms: int = DUREE_MAX_REEL_MS):
    """Les videos qu'on peut reellement publier, dans l'ordre des medias.

    Trois conditions : le type, une URL DIRECTE (sans elle il n'y a rien a
    telecharger -- cas d'un RSS qui ne donne qu'un lien de page), et une duree sous
    la limite. Une duree INCONNUE passe : mieux vaut proposer le reel et laisser
    l'utilisateur constater que masquer le switch sur une donnee absente.
    """
    retenues = []
    for entree in media or []:
        if entree.get("type") != VIDEO or not entree.get("url"):
            continue
        duree = entree.get("duration_ms")
        if duree is not None and duree > duree_max_ms:
            continue
        retenues.append(entree)
    return retenues


def raison_pas_de_reel(media: list[dict] | None) -> str | None:
    """Rend None si un reel est possible, sinon la raison, affichable telle quelle.

    Le dashboard grise le switch REELS avec ce texte : les trois causes ne se
    depannent pas pareil (changer de source, attendre une autre actu, rien a faire),
    donc un bouton grise muet ne suffit pas.
    """
    if videos_exploitables(media):
        return None
    videos = [e for e in media or [] if e.get("type") == VIDEO]
    if not videos:
        return "aucune video dans cette actu"
    if not any(e.get("url") for e in videos):
        return "pas d'URL directe vers la video (la source ne donne qu'un lien de page)"
    duree = min(
        (e["duration_ms"] for e in videos if e.get("duration_ms") is not None),
        default=None,
    )
    if duree is not None:
        return (
            f"video trop longue : {duree // 1000} s, "
            f"maximum {DUREE_MAX_REEL_MS // 1000} s pour un reel"
        )
    return "aucune video exploitable"  # pragma: no cover -- filet


def est_lien_instagram(url: str | None) -> bool:
    """Un lien dont l'HOTE est instagram.com. Pas un `in` sur la chaine :
    « https://evil.example/www.instagram.com/... » ne doit pas passer, yt-dlp
    n'est jamais lance ailleurs qu'Instagram. En https seulement."""
    if not url:
        return False
    morceaux = urlsplit(url)
    if morceaux.scheme.lower() != "https":
        return False
    hote = (morceaux.hostname or "").lower()
    return hote == "instagram.com" or hote.endswith(".instagram.com")


def lien_de_reel(url: str | None) -> bool:
    """Un permalink de reel : `/reel/<code>/` (ou `/reels/<code>/`)."""
    if not est_lien_instagram(url):
        return False
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return len(segments) >= 2 and segments[0] in ("reel", "reels")


def video_recuperable(media: list[dict] | None, permalink: str | None) -> bool:
    """Une video que yt-dlp saura recuperer sur la page du post, AU GEL.

    Distincte de `videos_exploitables` a dessein : celle-ci repond « y a-t-il un
    fichier a telecharger directement ? » (export manuel, switch REELS), celle-la
    « un reel automatique peut-il aller le chercher ? ». Meta omet `media_url`
    des reels d'artistes (musique protegee) : sans cette seconde question, ils
    seraient tous ineligibles.

    Deux indices : un permalink de reel, ou un post dont le SEUL media est une
    video sans URL. Un carrousel (`/p/` a plusieurs medias) est ecarte : yt-dlp
    rendrait une liste, sans dire quel element est la video.
    """
    if not est_lien_instagram(permalink):
        return False
    if lien_de_reel(permalink):
        return True
    media = media or []
    return len(media) == 1 and media[0].get("type") == VIDEO and not media[0].get("url")
