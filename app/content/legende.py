"""Le texte publie sous un post : legende aeree, credit, hashtags.

Commun a l'export zip et a la publication automatique -- un seul endroit ou
respecter les limites de Meta : 2200 caracteres et 30 hashtags.

Plus de ligne « Source : <url> » (2026-09-17, demande de l'utilisateur apres le
premier post publie) : le credit du compte reste, lui, obligatoire.
"""

import re

from app.content.emojis import EMOJI_CLASSE

LEGENDE_MAX = 2200
HASHTAGS_MAX = 30
PHRASES_PAR_PARAGRAPHE = 2

# Fin de phrase : ponctuation forte (et un guillemet fermant), suivie des emojis
# qui la ponctuent -- ils restent avec leur phrase --, puis un espace et une
# phrase qui COMMENCE (majuscule, chiffre, guillemet). Des points de
# suspension suivis d'une minuscule (« Cassius... et c'est ») ne coupent pas.
_FIN_DE_PHRASE = re.compile(
    # Possessif : les emojis de fin sont pris en entier, jamais laisses a la phrase suivante.
    rf"[.!?…]+[»\"')]?(?:\s*{EMOJI_CLASSE}+)*+(?=\s+[A-ZÀ-ÖØ-Þ0-9«\"])"
)


def aerer_legende(caption: str) -> str:
    """Filet de lisibilite : un bloc compact devient des paragraphes courts.

    Le prompt demande deja 2 a 4 paragraphes separes par une ligne vide ; une
    legende qui en porte une est laissee telle quelle. Sinon, des retours a la
    ligne simples deviennent des lignes vides, et un bloc de plus de deux phrases
    est coupe en paragraphes de deux phrases.
    """
    texte = caption.strip()
    if "\n\n" in texte:
        return texte
    if "\n" in texte:
        return "\n\n".join(ligne.strip() for ligne in texte.split("\n") if ligne.strip())
    phrases, debut = [], 0
    for fin in _FIN_DE_PHRASE.finditer(texte):
        phrases.append(texte[debut : fin.end()].strip())
        debut = fin.end()
    phrases.append(texte[debut:].strip())
    phrases = [phrase for phrase in phrases if phrase]
    if len(phrases) <= PHRASES_PAR_PARAGRAPHE:
        return texte
    return "\n\n".join(
        " ".join(phrases[i : i + PHRASES_PAR_PARAGRAPHE])
        for i in range(0, len(phrases), PHRASES_PAR_PARAGRAPHE)
    )


def texte_legende(post) -> str:
    hashtags = (post.hashtags or [])[:HASHTAGS_MAX]
    legende = aerer_legende(post.caption)
    parties = [legende]
    credit = getattr(post, "credit", None)
    if credit:
        pictogramme = "🎥" if getattr(post, "template", None) == "kinda_reel" else "📸"
        parties.append(f"{pictogramme} via {credit}")
    # Les hashtags forment le dernier bloc, a part.
    if hashtags:
        parties.append(" ".join(hashtags))
    texte = "\n\n".join(parties)
    if len(texte) <= LEGENDE_MAX:
        return texte
    # On coupe la legende elle-meme (paragraphes compris), jamais le credit :
    # citer le compte est le minimum de bonne foi quand on reprend ses medias.
    fin = "".join(f"\n\n{partie}" for partie in parties[1:])
    place = LEGENDE_MAX - len(fin) - len("…")
    return legende[: max(place, 0)].rstrip() + "…" + fin
