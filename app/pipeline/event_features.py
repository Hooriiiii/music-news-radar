"""Features deterministes d'un `Event` (Phase 2A, Task 3).

> « Le LLM comprend. Le code compte. » (blueprint)

Aucune des valeurs calculees ici n'est demandee a un modele : ce sont des
formules fermees appliquees aux observations deja stockees par la Task 2
(`app/pipeline/events.py`). Module PUR -- aucun import de `app.db`, `app.models`
ou `app.config`, aucune requete, aucune horloge implicite : l'instant de calcul
est TOUJOURS un parametre (`maintenant`), sinon la fraicheur ne serait pas
testable de facon deterministe.

`source_count`, `platform_count`, `independent_source_count` ne sont PAS
recalcules ici -- ce module les LIT (Task 2 les tient a jour, recalcules
depuis les `EventObservation` a chaque ecriture, jamais devines). Les
redemander ici dupliquerait une logique deja correcte et testee.

`velocity` n'est PAS calculee dans cette tache : elle demande un historique
d'observations sur 22 h que la retention a 2 jours rend fragile (voir le plan
Phase 2A, Task 3) -- elle merite son propre chantier et sa propre mesure.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

# --- Demi-vie de la fraicheur -----------------------------------------------
#
# `event_type` (`app/models.py::Event.event_type`) est TOUJOURS NULL a ce stade
# (la taxonomie d'evenements -- deces/annulation, annonce/line-up, sortie,
# festival/industrie, interview/culture -- est un chantier a part, voir le
# plan Phase 2A). En attendant, une demi-vie par defaut, REGLABLE via le
# parametre `demi_vie_heures` de chaque fonction publique.
#
# Justification de 24.0 h, mesuree sur le mix REEL du feed de production
# (2026-09-24, `select category, count(*) from articles group by category`) :
#
#     other            210   (non mappable a une demi-vie du blueprint)
#     new_release       71   -> 18-24 h (sortie/collaboration)
#     announcement      69   ->    8 h (annonce majeure/line-up)
#     industry          61   -> 24-36 h (industrie)
#     event_festival    57   -> 24-36 h (festival/billetterie)
#     culture           57   ->   72 h (interview/culture)
#     NULL              49   (non mappable)
#     drama             30   ->    3 h (drama)
#     rip                8   ->    3 h (deces)
#     gear_tech          5   -> ~24-36 h (assimile a industry)
#
# Sur les 358 articles dont la categorie se rattache a une demi-vie du
# blueprint (en ecartant `other`/NULL, non mappables sans la taxonomie), la
# moyenne ponderee vaut ~27.8 h. 24.0 h est le nombre rond le plus proche qui
# reste a l'interieur de la fourchette du plus gros bloc classifiable
# (new_release + industry + event_festival + gear_tech = 194 articles,
# 18-36 h) sans etre tire vers le haut par `culture` (72 h, un seul type) ni
# vers le bas par `drama`/`rip` (3 h, les evenements les plus rares du mix).
# A REVISER des que `event_type` existe : chaque type portera alors sa propre
# demi-vie plutot que ce defaut unique.
DEMI_VIE_DEFAUT_HEURES = 24.0

# Esquisse de mapping pour quand la taxonomie d'evenements existera (voir la
# justification ci-dessus) -- non branchee nulle part aujourd'hui puisque
# `event_type` est toujours NULL, mais ecrite ici pour que `demi_vie_pour` ait
# une base a etendre sans que quelqu'un doive redecouvrir le bareme du
# blueprint.
DEMI_VIES_PAR_TYPE: dict[str, float] = {
    "deces": 3.0,
    "annulation": 3.0,
    "drama": 3.0,
    "annonce_majeure": 8.0,
    "line_up": 8.0,
    "sortie": 21.0,
    "collaboration": 21.0,
    "festival": 30.0,
    "billetterie": 30.0,
    "industrie": 30.0,
    "interview": 72.0,
    "culture": 72.0,
}


def demi_vie_pour(event_type: str | None, defaut: float = DEMI_VIE_DEFAUT_HEURES) -> float:
    """La demi-vie a utiliser pour ce type d'evenement, ou `defaut` si le type
    est absent ou inconnu du mapping -- jamais une exception, un type qui
    n'existe pas encore dans le mapping doit degrader vers le defaut plutot
    que casser le calcul."""
    if event_type is None:
        return defaut
    return DEMI_VIES_PAR_TYPE.get(event_type, defaut)


def freshness(heures_ecoulees: float, demi_vie_heures: float = DEMI_VIE_DEFAUT_HEURES) -> float:
    """F(t) = 100 x 2^(-t/h), t en heures depuis la premiere apparition
    fiable du fait.

    Controles du blueprint avec h = 8 : t=1 -> ~92, t=8 -> 50, t=24 -> 12.5.

    `heures_ecoulees` negatif (horloge legerement en avance sur
    `first_seen_at`, ou appel avec `maintenant` avant `first_seen_at`) est
    ramene a 0 plutot que de rendre une fraicheur superieure a 100 -- un fait
    ne peut pas etre "plus frais que neuf".
    """
    if demi_vie_heures <= 0:
        raise ValueError("demi_vie_heures doit etre strictement positive")
    t = max(heures_ecoulees, 0.0)
    return 100.0 * (2.0 ** (-t / demi_vie_heures))


def freshness_depuis(
    first_seen_at: dt.datetime,
    maintenant: dt.datetime,
    demi_vie_heures: float = DEMI_VIE_DEFAUT_HEURES,
) -> float:
    """Meme formule que `freshness`, a partir de deux datetimes plutot que
    d'un nombre d'heures deja calcule -- c'est l'entree que `calculer_features`
    utilise. Les deux datetimes doivent etre du meme type (aware ou naive) ;
    la soustraction leve sinon, ce qui est le comportement voulu (une
    confusion aware/naive ne doit jamais etre avalee en silence)."""
    delta = maintenant - first_seen_at
    return freshness(delta.total_seconds() / 3600.0, demi_vie_heures)


def corroboration(n_sources_independantes: int) -> float:
    """K = 100 x (1 - e^(-n/3)), n = nombre de sources INDEPENDANTES (donc
    deja respectueux de `sources.source_group_id`, cense etre porte par
    `independent_source_count` -- ce module ne le recalcule pas).

    Controles du blueprint : n=1 -> 28, n=2 -> 49, n=3 -> 63, n=5 -> 81,
    n=8 -> 93.

    `n_sources_independantes` negatif est ramene a 0 (K=0) plutot que de
    rendre une valeur negative issue de la formule -- ne devrait jamais
    arriver (un compteur d'observations ne peut pas etre negatif) mais un
    defaut de donnees ne doit pas produire un score hors bornes."""
    n = max(n_sources_independantes, 0)
    return 100.0 * (1.0 - math.exp(-n / 3.0))


@dataclass(frozen=True)
class EventFeatures:
    """Le paquet de features deterministes d'un evenement, a un instant donne.

    `source_count` / `platform_count` / `independent_source_count` sont
    reportes TELS QUELS depuis l'appelant (deja calcules par
    `app/pipeline/events.py::_recalculer`) -- jamais redevines ici.
    """

    freshness: float
    corroboration: float
    source_count: int
    platform_count: int
    independent_source_count: int


def calculer_features(
    *,
    first_seen_at: dt.datetime,
    maintenant: dt.datetime,
    source_count: int,
    platform_count: int,
    independent_source_count: int,
    event_type: str | None = None,
    demi_vie_heures: float | None = None,
) -> EventFeatures:
    """Assemble les features d'un evenement a partir de ses champs deja
    connus (ceux d'un `Event`, Task 1/2) et de l'instant de calcul.

    Ne prend jamais de session ni d'objet ORM : les valeurs entrent par
    parametres nommes, pour que ce module reste testable sans base ni
    horloge implicite -- c'est l'appelant (Task 4, ou un futur job de
    scoring) qui lit l'`Event` et lui passe ses champs.

    `demi_vie_heures` : si absent, resolue depuis `event_type` via
    `demi_vie_pour` (donc le defaut documente plus haut tant que la
    taxonomie n'existe pas).
    """
    h = demi_vie_heures if demi_vie_heures is not None else demi_vie_pour(event_type)
    return EventFeatures(
        freshness=freshness_depuis(first_seen_at, maintenant, h),
        corroboration=corroboration(independent_source_count),
        source_count=source_count,
        platform_count=platform_count,
        independent_source_count=independent_source_count,
    )
