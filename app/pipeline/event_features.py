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
import re
import unicodedata
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

    - `similarite_max is None` (aucun jeton significatif dans le titre :
      rien n'a ete compare) rend `None` AVANT tout le reste ;
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
    # LA COMPARAISON N'A PAS EU LIEU (`None`) N'EST PAS « RIEN TROUVE »
    # (`0.0`). `event_archive.similarite_max` distingue soigneusement les
    # deux ; les confondre ici retournait la contrainte cardinale du plan --
    # la valeur non calculable devenait la valeur MAXIMALE. Mesure du tour de
    # correction 2 : 8 evenements ont un titre sans jeton significatif et 5
    # recevaient 100.0 (« AIR FT. », « IVE EP », « . », un titre d'emojis),
    # donc « actu parfaitement neuve » en tete de n'importe quel classement.
    # Population faible aujourd'hui, mais structurellement peuplee des titres
    # generiques Instagram et des titres en ecriture non latine.
    if similarite_max is None:
        return None
    s = max(0.0, min(1.0, similarite_max))
    if s >= seuil_reprise:
        return 0.0
    if s > seuil_doute:
        return 100.0 * (seuil_reprise - s) / (seuil_reprise - seuil_doute)
    if profondeur_archive_heures is None or profondeur_archive_heures < horizon_heures:
        return None
    return 100.0


# --- Pertinence France (Phase 2C, Task 3) -----------------------------------
#
# Le compte est FRANCAIS NATIONAL (CLAUDE.md). Cette feature sert a ne pas
# noyer la scene FR sous l'actu internationale AU MOMENT DU CLASSEMENT (2D).
# Elle ne se fond JAMAIS dans `relevance` : une note repond a « cette
# information est-elle bonne ? », pas a « parle-t-elle de chez nous ? ».
#
# DETERMINISTE ET GRATUIT, decision prise en amont : aucun champ ajoute au
# prompt de scoring (il faudrait re-noter tout le flux), aucun appel de
# modele. Il n'existe aucune colonne « pays » sur les sources -- verifie.
#
# LE SIGNAL RETENU, ET CE QUI A ETE ESSAYE PUIS REJETE. Mesure du 2026-09-30
# sur les 901 evenements de la base de production, titres lus un par un.
#
# (a) LEXIQUE France dans le TITRE -- villes, regions, salles, festivals,
#     artistes et labels francais. 44 evenements sur 901 (4,9 %). A la
#     lecture : aucun faux positif dur. Les deux cas les plus discutables
#     concernent bien la France (« Macklemore ... concerts in London, Dublin
#     and Paris », « David Guetta announces biggest London show ») : un
#     artiste francais ou une date parisienne SONT de l'actu de la scene FR.
#     Une dizaine de titres Konbini/Booska-P (mode, sport, cinema en France)
#     sont FR et hors ligne editoriale -- c'est `relevance` qui les baisse,
#     pas cette feature, et les melanger est exactement l'erreur a eviter.
#
# (b) SOURCE PRIMAIRE francaise -- un compte d'artiste, de festival, de label
#     ou de club francais. +30 evenements (3,3 %), aucun faux positif : pour
#     une source PRIMAIRE, le compte EST le sujet (meme doctrine que
#     `legende.sans_mentions_de_source`, ou `source.name` est le sujet de
#     l'actu des que la source n'est pas un media). C'est aussi la seule
#     facon honnete de contourner le piege du depot -- « un nom de source
#     n'est pas un pays » vaut pour les MEDIAS : Tsugi est francais et couvre
#     l'international, donc aucune source `media` n'entre dans cette liste.
#     Note plus faible que (a) : 5 des 30 parlent d'une activite a l'etranger
#     (Madeon a Tomorrowland Brasil, Polo & Pan a New York) -- l'acteur est
#     francais, l'evenement non.
#
# (c) LANGUE du titre -- REJETE, et c'est la mesure qui l'a tranche. 65
#     evenements sont ecrits en francais SANS aucun marqueur de (a) ni (b).
#     Lus un par un : au moins 15 ont un sujet ouvertement etranger (« LA
#     BELGE A REGALE SA VILLE NATALE », « QUAND HYSTA EMMENE LE HARDCORE EN
#     CROATIE », « DJ et producteur argentin ... », « LE NEERLANDAIS SORT
#     ... », Charlotte de Witte a Gand, les Sex Pistols, Stevie Wonder), et
#     la moitie du reste est indecidable (« Pour ou contre cette nouvelle
#     regle ? »). Ce tiers-la aurait DOUBLE la population classee FR en y
#     versant de l'actu internationale : c'est precisement le piege annonce
#     (Guettapen et Electro News ecrivent en francais sur toute la planete).
#     La langue mesure QUI RACONTE, pas DE QUI ON PARLE. Elle n'entre donc
#     pas dans la note -- ni ponderee, ni en tie-break.
#
# RAPPEL DE CALIBRAGE. Faux negatifs estimes sur un echantillon de 70
# evenements non classes, lus un par un : 2 a 4 (une soiree parisienne
# annoncee sans nommer la ville, « Avec Young Marco, Bambounou et Belaria »,
# un Flash Rap de Booska-P), soit un rappel d'environ 2/3 pour une precision
# proche de 1. C'est le compromis voulu : cette note sert a REMONTER ce qui
# est surement francais, pas a prouver qu'un evenement ne l'est pas.
#
# CE QUE 0.0 VEUT DIRE : « aucun marqueur France trouve », jamais « prouve
# etranger ». Avec un rappel de 2/3, un tiers des evenements francais dort
# a 0. Un classement 2D doit s'en servir comme d'un BONUS, jamais comme
# d'un filtre excluant.
#
# ENTRETIEN : ces listes sont editoriales, pas techniques. Les elargir est
# sans danger tant qu'on evite les mots qui existent aussi en anglais -- le
# prototype a perdu six evenements a cause de « air » (« fair use », « Ca a
# l'air »), et « justice » (le label francais) est volontairement absent du
# lexique de titre pour la meme raison : il est couvert par (b), ou le nom
# de compte ne peut pas se confondre avec un mot courant.
#
# --- Les LIEUX manquaient, et un post publie l'a prouve (2026-10-04) --------
#
# Post 164 du 03/10, « Les forfaits 7 jours pour Tomorrowland Winter sont en
# vente » : le festival se tient a l'ALPE D'HUEZ, en Isere. La legende le dit
# (« Direction les sommets d'Alpe d'Huez »), l'affiche publiee en slide
# l'ecrit en capitales (« ALPE D'HUEZ, FRANCE »), et `marqueurs_france`
# rendait l'ensemble VIDE. Le lexique connaissait des artistes et neuf
# METROPOLES ; or la scene francaise se joue aussi dans des stations, des
# salles et des festivals qui ne sont pas des metropoles.
#
# MESURE DU 2026-10-04 sur 3 122 textes REELS de la base (1 980 textes de
# variantes de cover, 168 legendes, titres et resumes d'articles) :
#
# - la liste d'alors declenche sur 278 textes (8,9 %) et sa PRECISION tient :
#   les 32 marqueurs declenches, relus un par un, ne produisent aucun faux
#   positif dur -- les deux cas les plus faibles (« Rihanna commente un
#   incident a Paris », « Madeon a San Francisco ») sont deja ceux que la
#   note (a) et la note (b) documentent comme volontairement gardes ;
# - 15 candidats nouveaux declenchent sur de la VRAIE actu francaise, zero
#   faux positif : ils sont ajoutes ci-dessous (marques « mesure ») ;
# - et 10 candidats ont ete ESSAYES PUIS REJETES sur leurs faux positifs
#   mesures. Ils sont listes ici parce que c'est la seule trace qui empeche
#   de les rajouter demain :
#
#     « angele »       -> declenche sur « Los ANGELEs » (8 textes, post 18,
#                         John Summit a Los Angeles). Le piege de « air »,
#                         a l'identique. Angele est de plus BELGE.
#     « rone »         -> « VeRONE », « dRONE », « DRONEflower », « drones »
#                         (6 textes sur 7).
#     « crecy » nu     -> « seCRECY » (Secrecy, groupe londonien). Seul
#                         « etienne de crecy » entier est garde.
#     « concrete »     -> « Dancing Alone on the Concrete » (Stef Chura,
#                         americaine). Mot anglais courant.
#     « jacques »      -> « Jacques Brel », qui est belge. Prenom.
#     « sebastian »    -> prenom ; et l'actu reelle qu'il portait ici est une
#                         collaboration d'Angele, donc belge.
#     « francophone »  -> « en Belgique FRANCOPHONE » (post 148). Un pays
#                         francophone n'est pas la France -- exactement le
#                         piege que la note (c) decrit pour la langue.
#     « cercle »       -> « les CERCLES underground ». Le label Cercle reste
#                         couvert par (b), ou un nom de compte ne se confond
#                         avec aucun mot courant.
#     « because music »-> le label apparait pour Logic1000 (australienne) :
#                         un label francais ne francise pas son catalogue.
#     « air »          -> deja retire par le prototype, et la base le redit :
#                         « AIR (Part 3 of 3) » est un titre de Bicep.
#
#   « nice », « tours », « angers », « nancy », « pau », « metz » et « sete »
#   ne declenchent sur AUCUN de ces 3 122 textes : les garder dehors ne coute
#   donc rien de mesurable, et les faire entrer rapporterait zero pour un
#   risque certain. La regle ne change pas.

# Villes, regions, gentiles. Un mot n'entre ici QUE s'il ne peut pas etre lu
# comme un mot anglais courant : ni « nice », ni « tours », ni « angers », ni
# « nancy » (prenom) -- « nancy jazz » est dans les entites, qui sont
# cherchees en sous-chaine. Meme regle pour les departements et les regions :
# « var » est un mot de code, « jura » est aussi suisse, « reunion » est un
# mot anglais, « basque » est aussi espagnol -- aucun des quatre n'entre.
LIEUX_FRANCE = frozenset({
    "france", "francais", "francaise", "francaises", "hexagone",
    "paris", "parisien", "parisienne", "parisiens", "parisiennes",
    "lyon", "lyonnais", "marseille", "marseillais", "bordeaux", "lille",
    "toulouse", "nantes", "rennes", "rennais", "brest", "strasbourg",
    "montpellier", "grenoble", "reims", "rouen", "dijon", "caen", "metz",
    "amiens", "toulon", "carcassonne", "bourges", "tourcoing", "nimes",
    "bretagne", "breton", "bretonne", "normandie", "corse", "provence",
    "alsace", "occitanie",
    # Mesure : declenchent sur de la vraie actu FR de la base, zero faux
    # positif. « creteil » : un blocus de lycee remonte par Booska-P -- FR et
    # hors ligne editoriale, c'est `relevance` qui le baisse, pas cette note.
    "creteil", "havre",
    # Mesure : ne declenchent sur AUCUN des 3 122 textes. Zero faux positif
    # mesure, zero gain mesure : ajoutes pour la couverture (une station de
    # ski ou une prefecture n'avait aucune chance d'etre vue), jamais sur la
    # foi d'un exemple invente. A relire si l'un d'eux se met a mentir.
    # (les noms a trait d'union sont dans les ENTITES : `_mots` coupe sur le
    # trait d'union, donc « saint-etienne » n'y serait jamais un seul mot.)
    "perpignan", "besancon", "orleans",
    "avignon", "annecy", "lorient", "quimper", "vannes", "biarritz",
    "mulhouse", "poitiers", "versailles", "nanterre", "montreuil", "pantin",
    "aubervilliers", "limoges", "villeurbanne", "chambery", "troyes",
    "angouleme", "niort", "chartres", "bayonne", "colmar",
    "lillois", "nantais", "toulousain", "bordelais", "strasbourgeois",
    "grenoblois", "dijonnais", "rouennais", "brestois", "nicois", "malouin",
    "isere", "savoie", "finistere", "morbihan", "gironde", "herault",
    "vosges", "landes", "vendee", "camargue", "cevennes", "aquitaine",
    "auvergne", "ardeche", "hexagonal", "hexagonale", "tricolore",
})

# Festivals, salles, clubs, labels et artistes francais. Cherches en
# SOUS-CHAINE sur le titre normalise (plusieurs sont en plusieurs mots, et
# « #RexClub » n'a pas d'espace).
ENTITES_FRANCE = frozenset({
    "hellfest", "solidays", "transmusicales", "trans musicales", "astropolis",
    "marsatac", "nuits sonores", "rock en seine", "we love green",
    "peacock society", "rex club", "rexclub", "vieilles charrues", "garorock",
    "eurockeennes", "printemps de bourges", "cabaret vert", "main square",
    "interceltique", "positive education", "ed banger", "ninkasi", "trabendo",
    "elysee montmartre", "la cigale", "olympia", "accor arena", "salle pleyel",
    "bataclan", "macki music", "plages electroniques", "lons electronic",
    "nancy jazz", "techno parade", "weather festival", "kompromat",
    "polo & pan", "folamour", "laurent garnier", "agoria", "breakbot",
    "dj snake", "gesaffelstein", "kavinsky", "kungs", "the blaze",
    "bob sinclar", "david guetta", "daft punk", "vladimir cauchemar",
    "worakls", "madeon", "christine and the queens", "oklou", "teki latex",
    "stromae", "tour eiffel", "chatelet",
    # --- Mesure du 2026-10-04 : declenchent sur de la vraie actu FR, zero
    # faux positif sur les 3 122 textes de la base.
    #
    # LES LIEUX, ce qui manquait. « alpe d'huez » est le cas d'origine
    # (post 164). « tomorrowland winter » est une marque BELGE, et elle entre
    # pourtant : ce n'est pas le nom d'un groupe mais celui d'un EVENEMENT, et
    # cet evenement se tient a l'Alpe d'Huez a chacune de ses editions --
    # exactement le meme raisonnement que pour un festival francais. Le
    # « tomorrowland » nu, lui, reste dehors (Boom, en Belgique) : c'est la
    # recherche en sous-chaine du nom COMPLET qui fait la difference.
    # « alpe d huez » : la forme sans apostrophe existe aussi dans les captions
    # et dans les titres translitteres, et ne peut rien d'autre.
    "alpe d'huez", "alpe d huez", "tomorrowland winter", "delta festival",
    "saint-etienne", "clermont-ferrand",
    # DES ARTISTES ET UNE LOI. « guy-man » couvre aussi « Guy-Manuel » ;
    # « homem-christo » est garde en plus parce qu'un titre peut ne donner que
    # le nom de famille. « ripost » est la loi francaise sur les free parties
    # -- une variante de cover reduite au seul mot « RIPOST » ne portait sinon
    # aucun marqueur.
    "guy-man", "homem-christo", "etienne de crecy", "petit biscuit",
    "ripost", "gazo", "ninho",
    # --- Mesure : ne declenchent sur AUCUN des 3 122 textes. Zero faux
    # positif mesure, zero gain mesure -- ajoutes pour la couverture, dans la
    # meme logique que les lieux ci-dessus.
    # Le filtre reste le MEME que pour « air » : un marqueur ne doit pas
    # pouvoir se lire comme une tournure courante. Sont donc restes dehors,
    # bien qu'etant de vrais noms de lieux francais, « bout du monde » (« au
    # bout du monde »), « les escales » (« les escales de la tournee »),
    # « panoramas », « petit bain », « le bikini », « le grand mix » (une
    # phrase sur un mix !), « bon entendeur » (« a bon entendeur ») et
    # « busy p » (« busy promoting »).
    "francofolies", "jazz a vienne", "nuits de fourviere", "musilac",
    "nordik impakt", "scopitone", "motocultor", "family piknik",
    "jazz in marciac", "stade de france", "paris la defense arena",
    "la gaite lyrique", "gare des mines", "le trianon", "point ephemere",
    "cabaret sauvage", "glazart", "le transbordeur", "stereolux",
    "la laiterie", "l'aeronef", "le chabada",
    "ile-de-france", "hauts-de-france", "nouvelle-aquitaine", "rhone-alpes",
    "pays de la loire", "franche-comte", "cote d'azur", "haute-savoie",
    "val d'isere", "les deux alpes", "courchevel", "avoriaz", "la clusaz",
    "mr oizo", "l'imperatrice", "martin solveig", "irene dresel",
    "ofenbach", "sacem", "teknival",
})

# Comptes PRIMAIRES francais (artiste / festival / label / club), normalises
# par `_nom_de_source`. Aucun media ici, et c'est la regle : le nom d'un
# media ne dit rien du pays de son sujet.
SOURCES_PRIMAIRES_FRANCE = frozenset({
    "astropolis", "marsatac", "nuits sonores", "transmusicales",
    "rock en seine", "solidays", "we love green", "peacock society",
    "rex club", "ninkasi / heat lyon", "positive education", "ed banger",
    "cercle", "kompromat", "polo & pan", "folamour", "laurent garnier",
    "agoria", "bob sinclar", "david guetta", "dj snake (fr)", "justice (fr)",
    "madeon (fr)", "breakbot (fr)", "vladimir cauchemar (fr)",
    "worakls (fr)", "trym",
})

FRANCE_MARQUEUR_TITRE = 100.0
FRANCE_SOURCE_PRIMAIRE = 70.0


def _sans_accents(texte: str) -> str:
    decompose = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in decompose if not unicodedata.combining(c)).lower()


def _mots(texte: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", _sans_accents(texte)))


def _nom_de_source(nom: str) -> str:
    """Le nom d'une source sans son suffixe de canal : « Astropolis (IG) » ->
    « astropolis ». Le suffixe de PAYS, lui, est garde (« madeon (fr) ») : il
    distingue le compte francais d'un homonyme, exactement comme dans la
    base."""
    return re.sub(r"\s*\((ig|rss)\)\s*$", "", _sans_accents(nom).strip()).strip()


def marqueurs_france(titre: str) -> frozenset[str]:
    """Les marqueurs France trouves dans UN TEXTE (vide si aucun).

    Le parametre s'appelle `titre` parce que c'est son usage d'origine (le
    titre d'un evenement), mais la fonction ne lit qu'un texte : l'appelant
    est libre de lui passer plus large, et `scripts/diagnostic/revue_posts.py` lui passe
    desormais l'accroche PLUS la legende. La mesure du 2026-10-04 justifie ce
    choix : sur les 55 posts publies, l'accroche seule portait un marqueur 8
    fois, l'accroche et la legende 12 fois -- l'information est souvent dans
    la legende, qui est la seule a nommer le lieu (« Direction les sommets
    d'Alpe d'Huez », « la plage du Prado a Marseille »). Le contre-coup est
    reel et il faut le savoir : la legende est ecrite par un modele, donc elle
    peut porter un marqueur FAUX -- post 119, « son wagon parisien » pour Lot
    Radio, qui est a Brooklyn. Ce faux positif vient du TEXTE PUBLIE, pas du
    lexique, et il est utile de le voir.

    Rendre l'ENSEMBLE et non un booleen n'est pas de la coquetterie : c'est
    ce qui rend une mesure relisible -- on voit QUEL mot a declenche, donc on
    voit le faux positif (c'est ainsi que « air » a ete retire)."""
    normalise = _sans_accents(titre)
    trouves = set(_mots(titre) & LIEUX_FRANCE)
    trouves.update(e for e in ENTITES_FRANCE if e in normalise)
    return frozenset(trouves)


def source_primaire_francaise(noms_sources_primaires) -> bool:
    """Vrai si l'un des comptes PRIMAIRES de l'evenement est francais.

    L'appelant ne passe QUE des sources primaires (role `primary`) : une
    source media n'a rien a faire ici, voir la note (c) ci-dessus."""
    return any(_nom_de_source(n) in SOURCES_PRIMAIRES_FRANCE for n in noms_sources_primaires if n)


def pertinence_france(
    *,
    titre: str,
    noms_sources_primaires=(),
) -> float | None:
    """100 = marqueur France dans le titre. 70 = compte primaire francais.
    0 = aucun marqueur trouve. `None` = rien a lire.

    `None` -- jamais 0 -- quand le titre ne porte AUCUN mot exploitable (« . »,
    « Post Instagram » reduit a deux mots generiques, une legende d'emojis) ET
    qu'aucun compte primaire n'est connu : on n'a alors rien mesure du tout,
    et le dire vaut mieux que de rendre un zero qui se lirait « regarde, pas
    francais ».
    """
    marqueurs = marqueurs_france(titre)
    if marqueurs:
        return FRANCE_MARQUEUR_TITRE
    noms = [n for n in noms_sources_primaires if n]
    if source_primaire_francaise(noms):
        return FRANCE_SOURCE_PRIMAIRE
    if not noms and not _mots(titre):
        return None
    return 0.0


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
    pertinence_france: float | None = None


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
    titre: str = "",
    noms_sources_primaires=(),
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

    `titre` et `noms_sources_primaires` alimentent la pertinence France
    (Task 3). `noms_sources_primaires` ne doit contenir QUE des sources de
    role `primary` -- une source media n'y dit rien du pays du sujet. Titre
    vide et liste vide rendent `None`, pas 0 : rien n'a ete mesure.
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
        pertinence_france=pertinence_france(
            titre=titre, noms_sources_primaires=noms_sources_primaires
        ),
    )
