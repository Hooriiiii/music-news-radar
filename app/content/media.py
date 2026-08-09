"""Ce qu'on peut faire des medias d'un article : le predicat « video exploitable ».

Zero dependance -- ni base, ni reseau, ni Chromium. Ce module repond a une seule
question, et c'est elle qui decide si le switch REELS apparait dans le dashboard.

Un tweet video etait jusqu'ici indistinguable d'un tweet photo : l'API X ne renvoie
`url` que pour les photos et rend une VIGNETTE pour une video. D'ou les medias
types, dont ce module est le consommateur principal.
"""

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
