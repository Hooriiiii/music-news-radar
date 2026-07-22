"""Balisage du surlignage rose des accroches Kinda.

Dans le template Figma, les aplats roses sont des rectangles positionnes a la main
derriere des segments de texte -- ce qui oblige a tout repositionner des que
l'accroche change de longueur. Ici l'information est portee par le texte lui-meme
(`**segment**`) et le CSS la reflow tout seul.
"""

import html
import re

MARKER_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def validate_headline(text: str) -> None:
    """Leve ValueError si le balisage est invalide."""
    if text.count("**") % 2 != 0:
        raise ValueError("Marqueurs ** non apparies dans l'accroche")
    if MARKER_RE.search(text) is None:
        raise ValueError("L'accroche ne surligne aucun segment")


def to_html(text: str) -> str:
    """Echappe le texte puis convertit `**x**` en `<mark>x</mark>`.

    L'echappement vient EN PREMIER : le texte provient de flux externes, il ne doit
    jamais pouvoir injecter de balise. Les `**` survivent a l'echappement.
    """
    escaped = html.escape(text)
    return MARKER_RE.sub(lambda m: f"<mark>{m.group(1)}</mark>", escaped)
