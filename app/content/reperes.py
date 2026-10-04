"""Les reperes temporels RELATIFS d'un post, et le moment ou ils deviennent FAUX.

Le defaut constate en production (post 153, publie le 2026-10-04 a 10h21) : la
cover annoncait « **This Dream Of You**, le nouveau son de **Martin Garrix**,
sort a minuit » et la legende « sort ce soir a minuit ». Le brouillon avait ete
compose le 02/10 a 12h30, soit 45,9 heures plus tot, et le morceau etait sorti
dans la nuit du 2 au 3 : le compte annoncait comme a venir une sortie vieille de
deux jours. Ce n'est pas un defaut de forme comme un accent manquant, c'est un
defaut de VERACITE -- un compte d'actu qui se trompe sur « quand » perd sa
credibilite bien plus vite.

La cause est structurelle : le modele ecrit a la composition, avec l'actu sous
les yeux, et il ecrit juste A CET INSTANT. Mais le creneau glisse (un creneau
manque libere son post, qui est reattribue au suivant), et rien ne relisait les
reperes entre-temps.

CE MODULE NE MESURE PAS UN NOMBRE D'HEURES, et c'est la mesure qui l'impose.
Sur les 52 posts publies de la base :

    post 101  « demain a 18h »      compose mer 23/09 20h12, publie jeu 24/09 07h03
                                    ecart 10,8 h -- FAUX (« demain » a glisse d'un jour)
    post 115  « vendredi prochain » compose ven 25/09 20h23, publie sam 26/09 15h56
                                    ecart 19,5 h -- VRAI (meme vendredi designe)

Aucun seuil en heures ne separe ces deux cas : le plus COURT est faux et le plus
LONG est vrai. Ce qui les separe est la FRONTIERE DE CALENDRIER franchie. Un
repere est donc perime quand le RELIRE depuis la date de lecture ne designe plus
ce qu'il designait depuis la date de composition -- ni plus ni moins.

Trois ancrages, plus un cas nomme :

- `ANCRE_JOUR` (« demain », « ce soir », « minuit », « hier »...) : perime des
  que la date de lecture differe de la date de composition.
- `ANCRE_SEMAINE` (« cette semaine », « ce week-end », « la semaine
  prochaine ») : perime quand la semaine ISO change.
- `ANCRE_MOIS` (« le mois prochain ») : perime quand le mois change. Aucune
  occurrence en base, mais le classer ailleurs serait faux.
- un JOUR NOMME (« ce vendredi », « vendredi prochain ») designe une DATE, qu'on
  calcule depuis la semaine de composition : perime quand la semaine ISO change,
  ou quand cette date est deja passee.

Les dates se comptent a l'heure de PARIS (`publication_fuseau`), comme les
fenetres de publication : un post compose a 23h30 heure de Paris et relu a
00h30 a bien change de jour pour son lecteur, quoi qu'en dise UTC.
"""

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.config import settings

ANCRE_JOUR = "jour"
ANCRE_SEMAINE = "semaine"
ANCRE_MOIS = "mois"
ANCRE_JOUR_NOMME = "jour_nomme"

JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

# Mesure sur les 1 992 textes de cover et les 169 legendes en base : les deux
# seules formulations qui ressortent souvent SANS etre perissables sont
# « maintenant » (15 covers, 11 legendes -- « dispo des maintenant » decrit une
# sortie DEJA faite, qui le restera) et « bientot » / « sous peu » (5 covers,
# 2 legendes -- vague par nature, donc jamais dementi par deux jours de retard).
# Elles ne figurent pas dans cette table : un controle qui les signalerait
# relancerait sur des textes justes.
_TABLE: tuple[tuple[str, str, int | None, int], ...] = (
    # (motif, ancre, jour de la semaine designe, decalage en semaines)
    (r"apr[eè]s-demain", ANCRE_JOUR, None, 0),
    (r"demain", ANCRE_JOUR, None, 0),
    # « ce soir-la » est un rappel du PASSE (« qui etait aux platines ce soir-la »,
    # post 170), pas une annonce : il ne vieillit pas.
    (r"ce soir(?!-l[aà])", ANCRE_JOUR, None, 0),
    (r"cette nuit", ANCRE_JOUR, None, 0),
    (r"ce matin", ANCRE_JOUR, None, 0),
    (r"cet apr[eè]s-midi", ANCRE_JOUR, None, 0),
    (r"aujourd'?hui", ANCRE_JOUR, None, 0),
    (r"hier", ANCRE_JOUR, None, 0),
    (r"minuit", ANCRE_JOUR, None, 0),
    (r"tout [aà] l'heure", ANCRE_JOUR, None, 0),
    (r"dans la journ[eé]e", ANCRE_JOUR, None, 0),
    (r"[aà] l'instant", ANCRE_JOUR, None, 0),
    (
        r"dans (?:quelques|une?|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|\d+) "
        r"(?:minutes?|heures?|jours?)",
        ANCRE_JOUR, None, 0,
    ),
    (r"ce week-?end", ANCRE_SEMAINE, None, 0),
    (r"cette semaine", ANCRE_SEMAINE, None, 0),
    (r"(?:la )?semaine prochaine", ANCRE_SEMAINE, None, 1),
    (r"(?:la )?semaine derni[eè]re", ANCRE_SEMAINE, None, -1),
    (r"le week-?end prochain", ANCRE_SEMAINE, None, 1),
    (r"dans (?:quelques|une?|deux|trois|\d+) semaines", ANCRE_SEMAINE, None, 0),
    (r"(?:le )?mois prochain", ANCRE_MOIS, None, 1),
    (r"(?:le )?mois dernier", ANCRE_MOIS, None, -1),
    (r"ce mois-ci", ANCRE_MOIS, None, 0),
    *tuple((rf"ce {jour}", ANCRE_JOUR_NOMME, i, 0) for i, jour in enumerate(JOURS)),
    *tuple((rf"{jour} prochain", ANCRE_JOUR_NOMME, i, 1) for i, jour in enumerate(JOURS)),
)

# « aujourd'hui » a un second sens, ATEMPOREL (« de nos jours »), qui ne vieillit
# pas : « le morceau ressurgit aujourd'hui sous une nouvelle forme » (posts 29,
# 34, 35, 36, 37), « Aujourd'hui, des indices laissent entendre un retour »
# (posts 77, 98). Mesure : dans une LEGENDE -- de la prose narrative -- 8 des 10
# occurrences sont ce sens-la ; dans une COVER -- un texte d'annonce de quelques
# mots -- les 6 occurrences sont datees (« aujourd'hui a 14h », « dispo des
# aujourd'hui »), zero faux positif. D'ou un mot ecarte de la prose et garde sur
# la cover, sur le precedent de `probleme_accents` / `probleme_accents_de_cover` :
# deux textes de nature differente n'appellent pas le meme critere.
AMBIGUS_EN_PROSE = frozenset({r"aujourd'?hui"})

_COMPILES = tuple(
    (re.compile(rf"\b(?:{motif})\b", re.IGNORECASE), motif, ancre, jour, semaines)
    for motif, ancre, jour, semaines in _TABLE
)


@dataclass(frozen=True)
class Repere:
    """Un repere temporel relatif trouve dans un texte."""

    expression: str
    ancre: str
    jour: int | None = None
    semaines: int = 0


def repere_relatif(texte: str | None, *, prose: bool = False) -> Repere | None:
    """Le premier repere relatif du texte, ou None.

    `prose=True` pour une legende : les formulations ambigues (`AMBIGUS_EN_PROSE`)
    y sont ecartees, mesure a l'appui.

    « Premier » veut dire le plus a GAUCHE, pas le premier de la table : sans ca
    l'ordre de la table deciderait quel repere d'un texte qui en porte deux est
    rapporte, et le message de relance pointerait un mot que l'auteur du texte ne
    lirait pas en premier.
    """
    if not texte:
        return None
    trouve: tuple[int, Repere] | None = None
    for regex, motif, ancre, jour, semaines in _COMPILES:
        if prose and motif in AMBIGUS_EN_PROSE:
            continue
        correspondance = regex.search(texte)
        if correspondance is None:
            continue
        depart = correspondance.start()
        if trouve is None or depart < trouve[0]:
            trouve = (depart, Repere(correspondance.group(0), ancre, jour, semaines))
    return None if trouve is None else trouve[1]


def _jour_local(moment: dt.datetime) -> dt.date:
    """La date a l'heure de Paris, qu'on recoive un horodatage aware ou naif.

    Un horodatage naif sur le disque ou en base (SQLite des tests, colonne sans
    fuseau) ne doit jamais lever ici : ce module est lu par le gel, dont une
    exception coute un creneau. On le suppose alors deja local.
    """
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(ZoneInfo(settings.publication_fuseau)).date()


def repere_perime(repere: Repere, compose_le: dt.datetime, lu_le: dt.datetime) -> bool:
    """Vrai si `repere`, ecrit le `compose_le`, ne dit plus la verite le `lu_le`.

    Jamais vrai quand la lecture precede l'ecriture (horloge qui recule, horodatage
    corrompu) : le doute profite a la publication, comme partout ailleurs dans la
    file -- on ne recompose pas un post sur une date qu'on ne comprend pas.
    """
    ecrit = _jour_local(compose_le)
    lu = _jour_local(lu_le)
    if lu <= ecrit:
        return False
    if repere.ancre == ANCRE_JOUR:
        return True
    if repere.ancre == ANCRE_MOIS:
        return (ecrit.year, ecrit.month) != (lu.year, lu.month)
    if repere.ancre == ANCRE_SEMAINE:
        return ecrit.isocalendar()[:2] != lu.isocalendar()[:2]
    # Jour nomme : il designe une DATE, calculee depuis la semaine d'ecriture.
    if ecrit.isocalendar()[:2] != lu.isocalendar()[:2]:
        return True
    assert repere.jour is not None  # garanti par la table pour ANCRE_JOUR_NOMME
    designee = ecrit + dt.timedelta(days=repere.jour - ecrit.weekday(), weeks=repere.semaines)
    return lu > designee
