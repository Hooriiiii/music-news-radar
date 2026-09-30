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

`velocity` et `nouveaute` ont rejoint ce module en Phase 2C (Tasks 1 et 2),
avec leur propre mesure -- voir plus bas. Elles gardent la regle du module :
l'appelant fournit les dates et la similarite, ce module ne lit jamais la base
(la lecture vit dans `app/pipeline/event_archive.py`).
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


# --- Velocite (Phase 2C, Task 1) --------------------------------------------
#
# LE CHAMP DE DATE EST `published_at`, JAMAIS `observed_at`. Mesure du
# 2026-09-30 sur les 205 evenements multi-preuves de la base de production :
# l'etalement des dates d'INGESTION a une mediane de 1,68 h et 44 % des
# evenements y tiennent en moins d'une heure -- ce chiffre mesure la cadence
# HORAIRE de notre propre pipeline, pas la propagation de l'actu. L'etalement
# des dates de PUBLICATION a une mediane de 10,69 h et decrit la realite.
# Se tromper de champ ne fait echouer aucun calcul : il rend une metrique qui
# a l'air de marcher et qui ne parle que de nous. D'ou le nom explicite du
# parametre (`dates_publication`) et le test-sentinelle sur
# `event_archive.dates_de_publication`, SEUL endroit ou ce champ est choisi.
#
# PLANCHER D'ETALEMENT. Le plan ne demandait `None` que pour un etalement NUL
# (deux articles publies a la meme seconde : le taux serait infini). Mesure :
# cette regle est necessaire mais PAS suffisante, et la laisser seule livre
# une feature qui classe du bruit en tete. Taux n/etalement sur les donnees
# reelles, les six premiers :
#
#     7200,0/h  n=4  etalement 0,00 h  'yunglean2001 - Boiler Room: London'
#      276,9/h  n=2  etalement 0,01 h  'CMU Selects - the last week s standout releases'
#      276,9/h  n=2  etalement 0,01 h  'Stray Kids 2027 Season s Greetings'
#      240,0/h  n=2  etalement 0,01 h  'In KORG Prophecy, anyone know what settings...'
#      109,1/h  n=2  etalement 0,02 h  'Lucky You! Hilary Duff s Lucky Me Tour...'
#       61,5/h  n=2  etalement 0,03 h  'Watch Kacey Musgraves Sing Dolly Parton s...'
#
# Aucune de ces actus n'explose : ce sont des publications SIMULTANEES (un
# carrousel Instagram, une liste hebdomadaire postee d'un bloc). 53 des 205
# evenements multi-preuves tiennent en moins d'une heure, et 25 d'entre eux ne
# viennent que d'UNE SEULE source. Au-dessus d'une heure le classement
# redevient lisible (Queens of the Stone Age, Boys Noize annonce un album,
# Jennie x Tame Impala no 1 -- deux a trois sources distinctes, 1 a 3 h
# d'ecart). D'ou `None` sous une heure d'etalement : sous ce plancher, le
# denominateur mesure la granularite des horodatages, pas la propagation.
VELOCITE_ETALEMENT_MIN_HEURES = 1.0


def etalement_heures(dates_publication) -> float | None:
    """L'etalement, en heures, des dates de publication DATEES d'un evenement.

    `None` sous deux dates : un etalement sur un point n'existe pas. Les
    `None` de la liste sont ignores (une observation sans date de publication
    ne doit JAMAIS etre remplacee par sa date d'ingestion -- ce serait
    reintroduire notre cadence dans la mesure).

    Melanger datetimes aware et naive leve, comme partout ailleurs dans ce
    module : une confusion de fuseau ne doit jamais etre avalee en silence.
    """
    datees = [d for d in dates_publication if d is not None]
    if len(datees) < 2:
        return None
    return (max(datees) - min(datees)).total_seconds() / 3600.0


def velocity(
    dates_publication,
    *,
    etalement_min_heures: float = VELOCITE_ETALEMENT_MIN_HEURES,
) -> float | None:
    """Le TAUX : preuves par heure sur l'etalement des publications.

    `None` -- jamais `0.0` -- sous deux preuves datees, ou quand l'etalement
    est nul ou plus court que `etalement_min_heures` (voir la mesure
    ci-dessus). Zero voudrait dire « mesure a zero » ; ici la matiere manque.

    Le numerateur est `n - 1`, le nombre de preuves arrivees APRES la
    premiere, et non `n`. La premiere preuve OUVRE la fenetre, elle ne tombe
    pas dedans : la compter fait mecaniquement gagner les paires (n=2), dont
    le taux vaudrait alors 2/etalement. Verifie sur les donnees reelles --
    avec `n`, les six premieres places sont six paires ; avec `n - 1`,
    l'evenement de tete redevient « Boards of Canada - Hi Scores » (3 preuves,
    3 sources distinctes, 2,4 h) devant ces paires. Les deux formules sont
    monotones l'une dans l'autre A n CONSTANT ; elles ne different que dans la
    comparaison ENTRE evenements, qui est precisement l'usage prevu (2D).

    Ce que ce taux NE dit PAS : rien sur la DIVERSITE des sources. 46 des 152
    evenements mesurables ne viennent que d'une source (un compte qui poste
    plusieurs fois). C'est le role de `corroboration`, et 2D devra croiser les
    deux plutot que classer sur l'un seul.
    """
    etalement = etalement_heures(dates_publication)
    if etalement is None or etalement <= 0 or etalement < etalement_min_heures:
        return None
    n = len([d for d in dates_publication if d is not None])
    return (n - 1) / etalement


def acceleration(
    dates_publication,
    *,
    etalement_min_heures: float = VELOCITE_ETALEMENT_MIN_HEURES,
) -> float | None:
    """Est-ce que ca monte encore, ou est-ce que c'etait au debut ?

    Densite de la SECONDE moitie de la fenetre comparee a celle de la
    PREMIERE, rendue sous forme d'indice borne dans [-1, +1] :

        (n2 - n1) / (n1 + n2)

    Les deux moities durent exactement le meme temps par construction, donc
    le rapport des densites se reduit a celui des effectifs -- pas de
    division par une duree qui pourrait etre nulle. +1 : tout est arrive dans
    la seconde moitie (l'actu monte). -1 est inatteignable, la premiere preuve
    ouvrant toujours la premiere moitie. Negatif : la rafale etait au debut et
    retombe -- mesure sur les donnees reelles, « Here's What Went Down at
    Olivia Rodrigo's Unraveled Tour Opener » vaut -0,64 (9 preuves puis 2),
    le profil exact d'une actu qui a explose puis s'eteint.

    `None` sous TROIS preuves datees : avec deux points il n'y a qu'un
    intervalle et rien a comparer. C'est le cas de 825 evenements sur 901 --
    seuls 76 ont trois preuves ou plus, et 68 passent aussi le plancher
    d'etalement. Le dire est plus utile que de rendre 0, qui se propagerait
    dans un classement comme une acceleration reellement mesuree a plat.

    Meme plancher d'etalement que `velocity`, et pour la meme raison : couper
    en deux une fenetre de trente secondes ne compare rien.
    """
    datees = sorted(d for d in dates_publication if d is not None)
    if len(datees) < 3:
        return None
    etalement = (datees[-1] - datees[0]).total_seconds() / 3600.0
    if etalement <= 0 or etalement < etalement_min_heures:
        return None
    milieu = datees[0] + (datees[-1] - datees[0]) / 2
    # Frontiere : une preuve exactement au milieu compte pour la SECONDE
    # moitie. Consequence a connaitre avant de lire un signe : un nombre
    # IMPAIR de preuves regulierement espacees penche de +1/n (trois preuves
    # equidistantes valent +0,33, pas 0). Le biais est fixe et s'efface quand
    # n grandit ; deux evenements de meme effectif restent comparables. Le
    # corriger en comptant la preuve mediane pour une demi-preuve de chaque
    # cote rendrait le signe moins lisible qu'il ne gagnerait en justesse.
    premiere = sum(1 for d in datees if d < milieu)
    seconde = len(datees) - premiere
    return (seconde - premiere) / len(datees)


# --- Nouveaute (Phase 2C, Task 2) -------------------------------------------
#
# « Cette actu est-elle neuve, ou une reprise de quelque chose qu'on a deja
# vu ? » La reponse est dans `event_observations`, qui n'est JAMAIS purgee et
# garde titre, source et dates -- la meme archive qui sert la garde de
# recurrence de la porte resume (`cluster_resume.charger_archive`).
#
# LE CRITERE, et pourquoi celui-la. On compare le titre de l'evenement aux
# titres archives STRICTEMENT ANTERIEURS a sa premiere preuve, avec
# `cluster.archive_similarity_from_tokens` -- la mesure deja calibree contre
# l'archive (part des mots porteurs du titre le PLUS LONG), et ses deux seuils
# deja mesures : 0,8 (« meme actu », `cluster_archive_seuil`) et 0,65 (la
# bande douteuse de la dedup). Reutiliser cette mesure plutot que d'en
# inventer une troisieme n'est pas une economie : c'est la MEME question que
# celle que la dedup pose contre l'archive, et deux mesures divergentes pour
# une seule question finiraient par se contredire.
#
# Mesure du 2026-09-30 sur les 893 evenements au titre exploitable :
# similarite maximale mediane 0,00 (447 evenements a exactement 0), 98
# evenements au-dessus de 0,65, 97 au-dessus de 0,8. Lecture des vingt
# premiers : ce sont tous de vraies reprises -- le meme article Mixmag/RA
# re-emis 24 a 159 h plus tard (« Erol Alkan to publish book... », « Calvin
# Harris sets Guinness World Record... »), ou une serie de promotion
# (« JUN 9 1st EP CHINA Poster 05 » contre « ... Poster 01 »). Un seul
# evenement tombe dans la bande 0,65-0,8 : l'interpolation ci-dessous ne
# decide presque rien aujourd'hui, elle evite seulement une marche d'escalier.
NOUVEAUTE_SEUIL_REPRISE = 0.8
NOUVEAUTE_SEUIL_DOUTE = 0.65

# L'horizon de reprise, repris tel quel de `cluster_archive_jours` (7 jours) :
# « une reprise ressort dans la semaine, au-dela le risque de faux positif
# depasse le service rendu ». C'est aussi la profondeur d'archive exigee pour
# oser conclure a la NOUVEAUTE -- voir la dissymetrie ci-dessous.
NOUVEAUTE_HORIZON_HEURES = 7 * 24.0


def nouveaute(
    *,
    similarite_max: float | None,
    profondeur_archive_heures: float | None,
    horizon_heures: float = NOUVEAUTE_HORIZON_HEURES,
    seuil_reprise: float = NOUVEAUTE_SEUIL_REPRISE,
    seuil_doute: float = NOUVEAUTE_SEUIL_DOUTE,
) -> float | None:
    """100 = rien d'anterieur n'y ressemble. 0 = reprise d'une actu archivee.

    `similarite_max` est la plus forte similarite entre ce titre et les titres
    archives ANTERIEURS (calculee par l'appelant :
    `app/pipeline/event_archive.py`, ce module ne lit pas la base).
    `profondeur_archive_heures` est le temps que l'archive couvre REELLEMENT
    avant l'evenement juge.

    LA DISSYMETRIE, qui est le coeur du critere. L'archive est JEUNE : la
    couche evenements date du 2026-09-23 et ne portait que huit jours au
    moment ou ce code a ete ecrit. Une archive courte ne peut produire qu'un
    seul type d'erreur -- declarer NEUF ce dont elle n'a simplement pas vu
    l'original. Elle ne peut pas inventer une reprise qu'elle ne contient
    pas. Donc :

    - une reprise TROUVEE vaut, quelle que soit la profondeur : un fait
      positif n'a pas besoin de recul ;
    - une ABSENCE de reprise ne vaut 100 que si l'archive couvre au moins
      `horizon_heures` avant l'evenement ; sinon `None` -- « on ne peut pas
      conclure », jamais un 100 de confort.

    Le 2026-09-30, 355 evenements sur 901 franchissent cette profondeur ; ce
    nombre monte tout seul avec le temps, sans rien a recalibrer. Ne PAS
    durcir ce critere sur ce que l'archive montre aujourd'hui : il est concu
    pour des mois d'archive et deviendra plus net, jamais moins.

    Entre `seuil_doute` et `seuil_reprise`, interpolation lineaire de 100 a 0
    plutot qu'une marche d'escalier.
    """
    if similarite_max is not None:
        s = max(0.0, min(1.0, similarite_max))
        if s >= seuil_reprise:
            return 0.0
        if s > seuil_doute:
            return 100.0 * (seuil_reprise - s) / (seuil_reprise - seuil_doute)
    if profondeur_archive_heures is None or profondeur_archive_heures < horizon_heures:
        return None
    return 100.0


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
    # Phase 2C. `None` n'est jamais un defaut de confort : il veut dire « pas
    # assez de matiere pour mesurer », quand 0.0 voudrait dire « mesure a
    # zero ». Les confondre est exactement ce qui rend un classement faux sans
    # qu'on le voie -- 85 % des evenements n'ont qu'une preuve.
    velocity: float | None = None
    acceleration: float | None = None
    nouveaute: float | None = None


def calculer_features(
    *,
    first_seen_at: dt.datetime,
    maintenant: dt.datetime,
    source_count: int,
    platform_count: int,
    independent_source_count: int,
    event_type: str | None = None,
    demi_vie_heures: float | None = None,
    dates_publication=(),
    similarite_max: float | None = None,
    profondeur_archive_heures: float | None = None,
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

    Les trois entrees de Phase 2C sont OPTIONNELLES et leur absence rend
    `None`, pas 0 : un appelant qui ne fournit ni dates ni similarite obtient
    exactement les features de 2A. `dates_publication` sont les
    `EventObservation.published_at` de l'evenement (JAMAIS `observed_at`, voir
    la mesure plus haut) ; `similarite_max` et `profondeur_archive_heures`
    viennent de `app/pipeline/event_archive.py`.
    """
    h = demi_vie_heures if demi_vie_heures is not None else demi_vie_pour(event_type)
    return EventFeatures(
        freshness=freshness_depuis(first_seen_at, maintenant, h),
        corroboration=corroboration(independent_source_count),
        source_count=source_count,
        platform_count=platform_count,
        independent_source_count=independent_source_count,
        velocity=velocity(dates_publication),
        acceleration=acceleration(dates_publication),
        nouveaute=nouveaute(
            similarite_max=similarite_max,
            profondeur_archive_heures=profondeur_archive_heures,
        ),
    )
