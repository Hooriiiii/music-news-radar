"""Les creneaux de publication : une date, deux heures, tirees une fois pour toutes.

L'heure est tiree dans chaque fenetre avec une GRAINE DERIVEE DE LA DATE. C'est
la decision centrale du module : sans elle, l'agent (qui passe toutes les 10
minutes) retirerait une heure a chaque passage et elle glisserait -- un creneau
pourrait n'etre jamais atteint. Avec elle, recalculer a 9 h et a 10 h redonne le
meme 14 h 37.

Python pur : aucune base, aucun reseau. Tout sort en UTC.
"""

import datetime as dt
import random
from zoneinfo import ZoneInfo

from app.config import settings


def _fenetre(texte: str) -> tuple[dt.time, dt.time]:
    debut, fin = texte.split("-")
    return dt.time.fromisoformat(debut), dt.time.fromisoformat(fin)


def horaires_du_jour(jour: dt.date) -> list[dt.datetime]:
    fuseau = ZoneInfo(settings.publication_fuseau)
    horaires = []
    fenetres = (settings.publication_fenetre_matin, settings.publication_fenetre_apres_midi)
    for rang, fenetre in enumerate(fenetres):
        debut, fin = _fenetre(fenetre)
        ouverture = dt.datetime.combine(jour, debut, tzinfo=fuseau)
        fermeture = dt.datetime.combine(jour, fin, tzinfo=fuseau)
        minutes = int((fermeture - ouverture).total_seconds() // 60)
        # Graine en chaine : son hachage est stable d'une execution a l'autre.
        tirage = random.Random(f"kinda-{jour.isoformat()}-{rang}").randint(0, minutes)
        horaires.append((ouverture + dt.timedelta(minutes=tirage)).astimezone(dt.timezone.utc))
    return horaires


def prochains_horaires(maintenant: dt.datetime, nombre: int) -> list[dt.datetime]:
    jour = maintenant.astimezone(ZoneInfo(settings.publication_fuseau)).date()
    horaires: list[dt.datetime] = []
    while len(horaires) < nombre:
        horaires += [h for h in horaires_du_jour(jour) if h > maintenant]
        jour += dt.timedelta(days=1)
    return horaires[:nombre]
