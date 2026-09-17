"""Le texte publie sous un post : legende, hashtags, source.

Commun a l'export zip et a la publication automatique -- un seul endroit ou
respecter les limites de Meta : 2200 caracteres et 30 hashtags.
"""

LEGENDE_MAX = 2200
HASHTAGS_MAX = 30


def texte_legende(post) -> str:
    hashtags = (post.hashtags or [])[:HASHTAGS_MAX]
    parties = [post.caption]
    if hashtags:
        parties.append(" ".join(hashtags))
    credit = getattr(post, "credit", None)
    if credit:
        pictogramme = "🎥" if getattr(post, "template", None) == "kinda_reel" else "📸"
        parties.append(f"{pictogramme} via {credit}")
    parties.append(f"Source : {post.article_url}")
    texte = "\n\n".join(parties)
    if len(texte) <= LEGENDE_MAX:
        return texte
    # On coupe la legende elle-meme, jamais le credit ni la source : citer le
    # compte et l'editeur est le minimum de bonne foi quand on reprend leurs medias.
    fin = "\n\n".join(parties[1:])
    place = LEGENDE_MAX - len(fin) - len("\n\n") - len("…")
    return post.caption[: max(place, 0)].rstrip() + "…" + "\n\n" + fin
