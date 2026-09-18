"""Les creneaux de publication : une date, des heures, tirees une fois pour toutes.

L'heure est tiree dans chaque fenetre avec une GRAINE DERIVEE DE LA DATE. C'est
la decision centrale du module : sans elle, l'agent (qui passe toutes les 10
minutes) retirerait une heure a chaque passage et elle glisserait -- un creneau
pourrait n'etre jamais atteint. Avec elle, recalculer a 9 h et a 10 h redonne le
meme 14 h 37.

Deux formats, deux jeux de fenetres, decrits de la meme facon : une liste
separee par des virgules, une fenetre = une publication par jour. Les carrousels
gardent la graine d'origine (`kinda-...`) : en changer deplacerait les heures
deja programmees en base. Les fenetres ne doivent jamais se chevaucher :
`creneaux.publier_a` est unique tous formats confondus.

Python pur : aucune base, aucun reseau. Tout sort en UTC.
"""

import datetime as dt
import random
from zoneinfo import ZoneInfo

from app.config import settings

CARROUSEL = "carrousel"
REEL = "reel"


class FenetresInvalides(ValueError):
    """Deux fenetres se chevauchent, ou une fenetre finit avant de commencer."""


def _fenetre(texte: str) -> tuple[dt.time, dt.time]:
    debut, fin = texte.split("-")
    return dt.time.fromisoformat(debut.strip()), dt.time.fromisoformat(fin.strip())


def _liste(texte: str | None) -> list[str]:
    return [f.strip() for f in (texte or "").split(",") if f.strip()]


def fenetres(format_: str = CARROUSEL) -> list[str]:
    """Les fenetres d'un format, dans l'ordre de la journee.

    Les deux formats ont la meme forme : une liste separee par des virgules,
    donc passer de deux a trois posts par jour ne touche pas ce module.
    """
    if format_ == REEL:
        return _liste(settings.publication_reel_fenetres)
    carrousel = _liste(settings.publication_fenetres)
    if carrousel:
        return carrousel
    # Repli : un `.env` anterieur a la generalisation ne connait que le couple
    # matin / apres-midi. On ne l'ignore que si une liste a ete donnee.
    return _liste(settings.publication_fenetre_matin) + _liste(settings.publication_fenetre_apres_midi)


def _formats_verifies(format_: str | None) -> tuple[str, ...]:
    """Seuls les formats ACTIFS sont verifies : des fenetres reel fautives ne
    doivent jamais empecher de programmer un carrousel. Le carrousel se verifie
    seul ; le reel, s'il a des creneaux, contre lui-meme et les carrousels."""
    if format_ == CARROUSEL or settings.publication_creneaux_reel <= 0:
        return (CARROUSEL,)
    return (CARROUSEL, REEL)


def verifier_fenetres(format_: str | None = None) -> None:
    bornes = []
    for format_verifie in _formats_verifies(format_):
        for texte in fenetres(format_verifie):
            try:
                bornes.append((format_verifie, texte, *_fenetre(texte)))
            except ValueError as exc:
                raise FenetresInvalides(f"fenetre {texte!r} ({format_verifie}) illisible, attendu HH:MM-HH:MM") from exc
    for rang, (format_a, texte_a, debut_a, fin_a) in enumerate(bornes):
        if fin_a < debut_a:
            raise FenetresInvalides(f"fenetre {texte_a} ({format_a}) : la fin precede le debut")
        for format_b, texte_b, debut_b, fin_b in bornes[rang + 1:]:
            # Bornes incluses : le tirage peut tomber sur la minute de fin.
            if debut_a <= fin_b and debut_b <= fin_a:
                raise FenetresInvalides(
                    f"les fenetres {texte_a} ({format_a}) et {texte_b} ({format_b}) se chevauchent"
                )


def horaires_du_jour(jour: dt.date, format_: str = CARROUSEL) -> list[dt.datetime]:
    verifier_fenetres(format_)
    fuseau = ZoneInfo(settings.publication_fuseau)
    graine = "kinda" if format_ == CARROUSEL else f"kinda-{format_}"
    horaires = []
    for rang, fenetre in enumerate(fenetres(format_)):
        debut, fin = _fenetre(fenetre)
        ouverture = dt.datetime.combine(jour, debut, tzinfo=fuseau)
        fermeture = dt.datetime.combine(jour, fin, tzinfo=fuseau)
        minutes = int((fermeture - ouverture).total_seconds() // 60)
        # Graine en chaine : son hachage est stable d'une execution a l'autre.
        tirage = random.Random(f"{graine}-{jour.isoformat()}-{rang}").randint(0, minutes)
        horaires.append((ouverture + dt.timedelta(minutes=tirage)).astimezone(dt.timezone.utc))
    return horaires


def prochains_horaires(maintenant: dt.datetime, nombre: int, format_: str = CARROUSEL) -> list[dt.datetime]:
    # Sans fenetre, la boucle ci-dessous ne finirait jamais.
    if nombre <= 0 or not fenetres(format_):
        return []
    jour = maintenant.astimezone(ZoneInfo(settings.publication_fuseau)).date()
    horaires: list[dt.datetime] = []
    while len(horaires) < nombre:
        horaires += [h for h in horaires_du_jour(jour, format_) if h > maintenant]
        jour += dt.timedelta(days=1)
    return horaires[:nombre]
