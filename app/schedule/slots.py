"""Les creneaux de publication : une date, des heures, tirees une fois pour toutes.

L'heure est tiree dans chaque fenetre avec une GRAINE DERIVEE DE LA DATE. C'est
la decision centrale du module : sans elle, l'agent (qui passe toutes les 10
minutes) retirerait une heure a chaque passage et elle glisserait -- un creneau
pourrait n'etre jamais atteint. Avec elle, recalculer a 9 h et a 10 h redonne le
meme 14 h 37.

Deux formats, deux jeux de fenetres. Les carrousels gardent la graine d'origine
(`kinda-...`) : en changer deplacerait les heures deja programmees en base. Les
fenetres ne doivent jamais se chevaucher : `creneaux.publier_a` est unique tous
formats confondus.

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


def fenetres(format_: str = CARROUSEL) -> list[str]:
    if format_ == REEL:
        return [f.strip() for f in settings.publication_reel_fenetres.split(",") if f.strip()]
    return [settings.publication_fenetre_matin, settings.publication_fenetre_apres_midi]


def verifier_fenetres() -> None:
    bornes = [(format_, texte, *_fenetre(texte)) for format_ in (CARROUSEL, REEL) for texte in fenetres(format_)]
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
    verifier_fenetres()
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
