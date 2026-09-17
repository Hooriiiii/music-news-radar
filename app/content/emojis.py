"""Reperage des emojis, partage par la composition et la legende publiee."""

import re

# Detection par PLAGES Unicode (les blocs qui portent les proprietes
# Emoji_Presentation / Extended_Pictographic), et non par liste d'emojis en dur :
# une liste serait perimee a la version d'Unicode suivante. Le module `regex`, seul
# a exposer `\p{Extended_Pictographic}`, n'est pas une dependance du projet.
# Les plages sont choisies pour ne PAS attraper ce que les accroches contiennent
# deja legitimement : lettres accentuees (bloc latin), apostrophes typographiques,
# points de suspension, guillemets, degre, et les marqueurs `**` du surlignage.
EMOJI_CLASSE = (
    "["
    "\U0001f000-\U0001faff"  # pictogrammes, visages, objets, transports, drapeaux
    "\U00002600-\U000027bf"  # symboles divers et dingbats (coeur, etoile, soleil)
    "\U00002b00-\U00002bff"  # fleches et formes geometriques a rendu emoji
    "\U0000fe0f"  # selecteur de variation : force le rendu emoji du caractere precedent
    "\U0000200d"  # liaison des sequences composees (famille, drapeaux regionaux)
    "]"
)
EMOJI_RE = re.compile(EMOJI_CLASSE)
