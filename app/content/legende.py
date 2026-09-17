"""Le texte publie sous un post : legende aeree, credit, hashtags.

Commun a l'export zip et a la publication automatique -- un seul endroit ou
respecter les limites de Meta : 2200 caracteres (marge a 2150) et 30 hashtags.

Plus de ligne « Source : <url> » (2026-09-17, demande de l'utilisateur apres le
premier post publie) : le credit du compte reste, lui, obligatoire.
"""

import re

from app.content.emojis import EMOJI_CLASSE

LEGENDE_MAX = 2200
# Marge sous la limite de Meta, en points de code sur TOUT le texte publie :
# Meta ne documente pas son unite de compte (emojis composes, retours a la ligne).
LEGENDE_MARGE = 2150
# Place gardee a la legende quand credit et hashtags debordent a eux seuls.
LEGENDE_RESERVE = 500
HASHTAGS_MAX = 30
PHRASES_PAR_PARAGRAPHE = 2
# Un point qui suit ces mots ne termine pas la phrase (« feat. Travis Scott »).
ABREVIATIONS = frozenset({"feat", "ft", "vol", "dr", "st", "mr", "mme", "no", "vs"})
_INITIALES = re.compile(r"(?:[A-Z]\.)+")
# Une ligne de liste (« 📅 24.09 », « - Zenith ») garde son retour simple.
_LIGNE_DE_LISTE = re.compile(rf"(?:{EMOJI_CLASSE}|[-•*–·])")

# Fin de phrase : ponctuation forte (et un guillemet fermant), suivie des emojis
# qui la ponctuent -- ils restent avec leur phrase --, puis un espace et une
# phrase qui COMMENCE (majuscule, chiffre, guillemet). Des points de
# suspension suivis d'une minuscule (« Cassius... et c'est ») ne coupent pas.
_FIN_DE_PHRASE = re.compile(
    # Possessif : les emojis de fin sont pris en entier, jamais laisses a la phrase suivante.
    rf"[.!?…]+[»\"')]?(?:\s*{EMOJI_CLASSE}+)*+(?=\s+[A-ZÀ-ÖØ-Þ0-9«\"])"
)


def _abreviation(texte: str, fin: re.Match) -> bool:
    """Le point de cette fin de phrase clot-il une abreviation (« J. », « U.S. »,
    « feat. ») plutot qu'une phrase ?"""
    if not fin.group().startswith(".") or fin.group().startswith(".."):
        return False
    mot = re.search(r"(\S*)$", texte[: fin.start() + 1]).group(1).lstrip("(«\"'")
    return bool(_INITIALES.fullmatch(mot)) or mot[:-1].lower() in ABREVIATIONS


def _lignes(texte: str) -> str:
    lignes = [ligne.strip() for ligne in texte.split("\n") if ligne.strip()]
    sortie = lignes[0]
    for ligne in lignes[1:]:
        sortie += ("\n" if _LIGNE_DE_LISTE.match(ligne) else "\n\n") + ligne
    return sortie


def aerer_legende(caption: str) -> str:
    """Filet de lisibilite : un bloc compact devient des paragraphes courts.

    Le prompt demande deja 2 a 4 paragraphes separes par une ligne vide ; une
    legende qui en porte une est laissee telle quelle. Sinon, des retours a la
    ligne simples deviennent des lignes vides (sauf devant une ligne de liste,
    emoji ou puce), et un bloc de plus de deux phrases
    est coupe en paragraphes de deux phrases.
    """
    texte = caption.strip()
    if "\n\n" in texte:
        return texte
    if "\n" in texte:
        return _lignes(texte)
    phrases, debut = [], 0
    for fin in _FIN_DE_PHRASE.finditer(texte):
        if _abreviation(texte, fin):
            continue
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
    credit = getattr(post, "credit", None)
    ligne_credit = None
    if credit:
        pictogramme = "🎥" if getattr(post, "template", None) == "kinda_reel" else "📸"
        ligne_credit = f"{pictogramme} via {credit}"

    def fin_du_texte(tags) -> str:
        # Credit puis hashtags, en dernier bloc a part.
        blocs = [bloc for bloc in (ligne_credit, " ".join(tags)) if bloc]
        return "".join(f"\n\n{bloc}" for bloc in blocs)

    fin = fin_du_texte(hashtags)
    if len(legende) + len(fin) <= LEGENDE_MARGE:
        return legende + fin
    # Credit et hashtags trop longs a eux seuls : des hashtags partent, par la
    # fin. Le credit, jamais -- citer le compte est le minimum de bonne foi.
    reserve = min(len(legende), LEGENDE_RESERVE)
    while hashtags and len(fin) + reserve > LEGENDE_MARGE:
        hashtags = hashtags[:-1]
        fin = fin_du_texte(hashtags)
    if len(legende) + len(fin) <= LEGENDE_MARGE:
        return legende + fin
    # Puis on coupe la legende elle-meme, paragraphes compris.
    place = LEGENDE_MARGE - len(fin) - len("…")
    return legende[: max(place, 0)].rstrip() + "…" + fin
