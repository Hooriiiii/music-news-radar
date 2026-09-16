"""L'arbitrage : quels articles dans quelles places. Python pur, aucune base.

A chaque passage, les places libres sont reattribuees : les meilleurs candidats
prennent les places les plus proches. Une grosse actu qui arrive prend donc la
premiere place et DECALE la precedente d'un cran ; ce qui ne tient plus dans les
places sort de la file -- « ou alors on la met jamais ».

Le score : pertinence, plus une part d'urgence (hotness), moins une penalite
d'age. Un article deja compose (appel Claude paye) garde un avantage : on ne
jette pas une composition pour quelques points.
"""

import datetime as dt
from dataclasses import dataclass

POIDS_HOTNESS = 0.25
PENALITE_PAR_JOUR = 15.0
MARGE_TITULAIRE = 15.0
AGE_INCONNU_JOURS = 1.0


@dataclass(frozen=True)
class Candidat:
    article_id: int
    relevance: int
    hotness: int
    publie_le: dt.datetime | None
    compose: bool = False


@dataclass(frozen=True)
class Place:
    publier_a: dt.datetime
    article_id: int | None
    verrouillee: bool


def score(candidat: Candidat, maintenant: dt.datetime) -> float:
    if candidat.publie_le is None:
        age_jours = AGE_INCONNU_JOURS
    else:
        age_jours = max((maintenant - candidat.publie_le).total_seconds() / 86400, 0.0)
    return candidat.relevance + POIDS_HOTNESS * candidat.hotness - PENALITE_PAR_JOUR * age_jours


def arbitrer(
    places: list[Place], candidats: list[Candidat], maintenant: dt.datetime
) -> dict[dt.datetime, int | None]:
    affectation = {p.publier_a: p.article_id for p in places if p.verrouillee}
    pris = {article_id for article_id in affectation.values() if article_id is not None}
    libres = sorted((p for p in places if not p.verrouillee), key=lambda p: p.publier_a)
    classement = sorted(
        (c for c in candidats if c.article_id not in pris),
        key=lambda c: (-(score(c, maintenant) + (MARGE_TITULAIRE if c.compose else 0.0)), c.article_id),
    )
    for rang, place in enumerate(libres):
        affectation[place.publier_a] = classement[rang].article_id if rang < len(classement) else None
    return affectation
