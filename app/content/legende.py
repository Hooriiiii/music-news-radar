"""Le texte publie sous un post : legende, hashtags, credit.

Commun a l'export zip et a la publication automatique -- un seul endroit ou
respecter les limites de Meta : 2200 caracteres et 30 hashtags.

Plus de ligne « Source : <url> » (2026-09-17, demande de l'utilisateur apres le
premier post publie) : le credit du compte reste, lui, obligatoire.
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
    texte = "\n\n".join(parties)
    if len(texte) <= LEGENDE_MAX:
        return texte
    # On coupe la legende elle-meme, jamais le credit : citer le compte est le
    # minimum de bonne foi quand on reprend ses medias.
    fin = "".join(f"\n\n{partie}" for partie in parties[1:])
    place = LEGENDE_MAX - len(fin) - len("…")
    return post.caption[: max(place, 0)].rstrip() + "…" + fin
