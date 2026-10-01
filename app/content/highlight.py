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


# --- Une enumeration se surligne en entier, ou pas du tout ------------------

# Post publie le 2026-10-01 : « **Angele**, **Caroline Polachek** et SebastiAn
# s'unissent sur "Love Triangle" ». Deux des trois artistes surlignes, le
# troisieme non -- rien ne justifie la difference, ca se lit comme un oubli.
#
# Le defaut n'est PAS le nombre de segments surlignes. Mesure sur les 1 704
# textes de variantes en base : 483 en portent DEUX, et la plupart le meritent
# (« **Bicep** referme le triptyque **AIR** » : l'artiste et
# l'oeuvre). Interdire le second surlignage casserait 28 % des textes, dont la
# majorite sont bons. Ce qui est fautif, c'est l'INCOHERENCE a l'interieur d'une
# ENUMERATION : plusieurs elements de meme nature, dont certains sont surlignes
# et d'autres non.
SEPARATEUR_RE = re.compile(r"\s*(?:,|;|:|\bet\b)\s*")
COORDINATION_RE = re.compile(r"\bet\b")
# Mots qui prolongent un nom propre sans le rompre (« Charlotte de Witte »,
# « Death From Above »). Un nom ne se TERMINE jamais par l'un d'eux.
PARTICULES = {
    "de", "du", "des", "la", "le", "les", "van", "von", "da", "di", "of",
    "the", "and", "dj", "feat.", "ft.",
}
MOT_RE = re.compile(r"[^\W\d_][\w'’\-\.]*", re.UNICODE)
# Au-dela, ce n'est plus un nom mais une phrase.
MOTS_PAR_NOM = 4
# Trois elements au moins. A deux, la mesure donne des faux positifs francs --
# « **Palacio**, Mexico » (une salle et sa ville), « **Atomium**, Bruxelles »,
# « Adam Beyer et **Drumcode** » (un artiste et son label) : ce sont des couples
# de nature DIFFERENTE, ou le surlignage dissymetrique est un choix. A trois, la
# repetition de la meme nature ne fait plus de doute. Mesure : 18 textes
# signales a deux (dont 9 legitimes), 5 a trois (dont 0 legitime).
ENUMERATION_MIN = 3


def _aplatir(texte: str) -> tuple[str, list[bool]]:
    """Le texte sans ses `**`, et un drapeau « surligne » par caractere."""
    morceaux: list[str] = []
    surligne: list[bool] = []
    curseur = 0
    for marque in MARKER_RE.finditer(texte):
        morceaux.append(texte[curseur : marque.start()])
        surligne.extend([False] * (marque.start() - curseur))
        morceaux.append(marque.group(1))
        surligne.extend([True] * len(marque.group(1)))
        curseur = marque.end()
    morceaux.append(texte[curseur:])
    surligne.extend([False] * (len(texte) - curseur))
    return "".join(morceaux), surligne


def _nom_en_tete(plat: str, debut: int, fin: int) -> tuple[int, int] | None:
    """Les bornes du nom propre qui OUVRE ce segment, ou None.

    La tete et non le segment entier, parce que le dernier element d'une
    enumeration porte la suite de la phrase (« ... et SebastiAn s'unissent sur
    "Love Triangle" »)."""
    pris: list[re.Match[str]] = []
    for mot in MOT_RE.finditer(plat):
        if mot.start() < debut:
            continue
        if mot.start() >= fin or len(pris) >= MOTS_PAR_NOM:
            break
        texte_du_mot = mot.group(0)
        if texte_du_mot[0].isupper():
            pris.append(mot)
        elif pris and texte_du_mot.lower() in PARTICULES:
            pris.append(mot)
        else:
            break
    while pris and pris[-1].group(0).lower() in PARTICULES:
        pris.pop()
    if not pris:
        return None
    return pris[0].start(), pris[-1].end()


def _nom_seul(plat: str, debut: int, fin: int, nom: tuple[int, int]) -> bool:
    """Vrai si le segment ne contient RIEN d'autre que ce nom (ponctuation et
    guillemets mis a part). C'est ce qui separe un element d'enumeration d'un
    debut de phrase qui commence par une majuscule : « C'est officiel »,
    « En 1996 », « Trois noms sur un meme titre »."""
    reste = plat[debut : nom[0]] + plat[nom[1] : fin]
    return not any(c.isalnum() for c in reste)


def enumerations_surlignees(texte: str) -> list[tuple[list[str], list[bool]]]:
    """Les enumerations de noms propres du texte, et leur surlignage.

    Un element est un segment separe par une virgule, un deux-points ou « et »,
    reduit a un nom propre. Deux assouplissements, chacun paye d'une mesure :
      - le DERNIER element peut porter la suite de la phrase (sans quoi
        « ... et SebastiAn s'unissent » ne serait pas vu) -- mais un segment qui
        ne se reduit pas a un nom ne peut jamais OUVRIR une enumeration, sinon
        « C'est officiel, **"Run"** de **Amelie Lens** et **Angele** » en
        fabrique une de toutes pieces ;
      - « et X » FERME l'enumeration : sans cette regle, « entre Londres, Miami
        et Liverpool, **Mele** sort son album » rattache Mele a la liste des
        villes et signale une incoherence qui n'existe pas.
    """
    plat, surligne = _aplatir(texte)
    segments: list[tuple[int, int, bool]] = []
    position, coordonne = 0, False
    for separateur in SEPARATEUR_RE.finditer(plat):
        segments.append((position, separateur.start(), coordonne))
        coordonne = COORDINATION_RE.search(separateur.group(0)) is not None
        position = separateur.end()
    segments.append((position, len(plat), coordonne))

    trouvees: list[list[tuple[int, int]]] = []
    courante: list[tuple[int, int]] = []

    def clore() -> None:
        nonlocal courante
        if len(courante) >= ENUMERATION_MIN:
            trouvees.append(courante)
        courante = []

    for debut, fin, apres_coordination in segments:
        nom = _nom_en_tete(plat, debut, fin)
        if nom is None:
            clore()
            continue
        if not _nom_seul(plat, debut, fin, nom):
            if courante:
                courante.append(nom)
            clore()
            continue
        courante.append(nom)
        if apres_coordination:
            clore()
    clore()
    return [
        (
            [plat[debut:fin] for debut, fin in enumeration],
            [any(surligne[debut:fin]) for debut, fin in enumeration],
        )
        for enumeration in trouvees
    ]


def enumeration_incoherente(texte: str) -> tuple[list[str], list[bool]] | None:
    """La premiere enumeration dont une partie seulement est surlignee, ou None."""
    for noms, etats in enumerations_surlignees(texte):
        if any(etats) and not all(etats):
            return noms, etats
    return None
