"""Mise a l'ecart TEMPORAIRE d'un article faute de visuel de qualite.

Un article dont le fond de cover ne trouve rien de publiable (`compose.
VisuelIntrouvable`) libere son creneau et relance l'arbitrage DANS LE MEME
PASSAGE (`app/schedule/runner.py::_composer`, `exclusions = True`) -- mais
l'arbitrage classe par score et l'article reste ELIGIBLE. S'il a le meilleur
score, il revient sur le creneau le plus proche, echoue a nouveau, et ainsi de
suite jusqu'a MANQUE, puis glisse au creneau suivant : mesure, un seul
article a 95 de pertinence sans visuel face a quatre bons articles a 67-70
a fait perdre toute la publication du jour (creneaux en MANQUE, zero post).

Ce n'est PAS une exclusion : l'article peut avoir un visuel demain (nouvelle
publication du compte source, recherche web qui ressort du cache). Une pause
TEMPORAIRE respecte ca tout en arretant le cycle : l'article sort des
candidats le temps que quelque chose ait une chance raisonnable d'avoir
change (`visuel_saute_pause_minutes`, justifie dans `app/config.py`), puis
redevient eligible comme n'importe quel autre.

Le meme defaut existe pour un REPORT (`VisuelsDifferes`, panne de transport
dans une reserve) qui ne guerit jamais : sans borne, `runner._composer`
continue simplement sur le creneau en cours au lieu de le liberer, qui campe
dessus jusqu'a MANQUE -- mesure sur le meme scenario : 1 publication sur 4,
3 MANQUE, 0 mise a l'ecart. Un report reste le bon geste (retenter EST la
reponse a une panne de transport), mais il ne peut pas etre ETERNEL :
`signaler_report` compte les reports CONSECUTIFS d'un article et, au-dela de
`visuel_differe_max_reports`, l'appelant peut le faire rejoindre la MEME mise
a l'ecart qu'un article sans visuel. `reinitialiser_report` efface le
compteur des qu'une composition reussit pour l'article : seule une SUITE
ininterrompue de reports compte, et `RapportPublication.reportes` (distinct
de `sautes`) rend ce phenomene visible au log meme quand il reste sous le
seuil.

INVARIANT D'ORDRE, partage avec la ceinture de publication
(`runner._publier`) : un creneau ne se libere qu'apres une ecriture
CONDITIONNELLE (`_liberer`, via `_ecrire_si`) qui peut echouer si le
dashboard est passe entre-temps. La mise a l'ecart d'un article ne doit donc
JAMAIS etre posee avant de savoir si son creneau a reellement ete libere --
sinon un article prend une pause de plusieurs heures pour un creneau qu'on
n'a meme pas reussi a lui retirer. Ce module lui-meme ne connait pas les
creneaux (il ne fait que compter et mettre a l'ecart sur demande) : c'est
l'appelant (`runner._composer`, `runner._publier`) qui doit liberer D'ABORD
et n'appeler `marquer_saute` / ne compter le report comme converti QU'APRES.

MEME MOTIF que `app/pipeline/quota_graph.py` (fichier d'etat a cote du jeton,
ecriture atomique, temporaire nomme par processus) : plusieurs entrees, une
par article en pause ou en report, purgees des qu'elles sont echues -- les
pauses par leur echeance, les compteurs de reports par leur ANCIENNETE (un
compteur jamais mis a jour n'est plus une suite consecutive de quoi que ce
soit ; mesure : sans cette purge, des dizaines de compteurs abandonnes
restaient sur le disque indefiniment). Aucune migration : rien de ce fichier
ne vit en base.

ECRITURES CONCURRENTES : `_fusionner_et_ecrire` relit le disque A L'INSTANT
DE L'ECRITURE et fusionne, exactement comme
`app.pipeline.cluster_arbitrage.fusionner_et_ecrire` -- un second agent
(aujourd'hui, seul `run_publication` ecrit ici ; demain, un autre point
d'entree) ne doit jamais voir sa pause effacee par une reecriture a partir
d'un instantane pris plus tot dans l'appel (mesure par la revue : A lit
l'etat, B ecrit la pause de l'article 2, A ecrit la sienne sur l'article 1 a
partir de son instantane PERIME, qui efface celle de B).

POINT D'ENTREE UNIQUE (`marquer_saute`) : la Tache 6 deplacera une partie des
sauts au GEL (T-2h, apres l'echec de la super-resolution) et devra pouvoir
l'appeler exactement de la meme facon -- ne pas dupliquer ce mecanisme
ailleurs.
"""

import datetime as dt
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# A cote du jeton, comme `quota_graph.json`, `veille.json` et
# `publication_panne.json` : meme dossier applicatif, hors du depot.
FICHIER_ETAT = "visuel_saute.json"


def chemin_etat() -> Path:
    return settings.meta_token_path.parent / FICHIER_ETAT


Report = tuple[int, dt.datetime]  # (compte, dernier signalement)


@dataclass(frozen=True)
class Etat:
    """L'etat persiste : les pauses en cours, et les compteurs de reports
    CONSECUTIFS (chacun avec l'instant de son dernier signalement, pour
    pouvoir purger les compteurs abandonnes) -- un seul fichier, une seule
    fusion, sur le meme modele que `cluster_arbitrage.Cache` (entrees +
    tentatives)."""

    pauses: dict[int, dt.datetime] = field(default_factory=dict)
    reports: dict[int, Report] = field(default_factory=dict)


def _pause_valide(maintenant: dt.datetime, jusqu_a: dt.datetime) -> bool:
    """Vrai si la pause tient encore. Ne leve JAMAIS : un horodatage NAIF sur
    le disque (corruption, bug amont) compare a un `maintenant` conscient du
    fuseau leve normalement un `TypeError` -- ce module est appele depuis
    `store._articles_publiables`, donc depuis l'arbitrage, `candidats_
    eligibles` ET `offre_reels` : une exception ici ferait tomber les trois.
    Une comparaison ratee vaut « pause perimee » -- retenter un peu tot est
    sans commune mesure avec un planificateur a l'arret."""
    try:
        return maintenant < jusqu_a
    except TypeError:
        logger.warning("horodatage de pause illisible (comparaison impossible) : ignoree")
        return False


def _report_valide(maintenant: dt.datetime, dernier: dt.datetime) -> bool:
    """Vrai si ce compteur de reports est encore une SUITE active -- meme
    garde-fou que `_pause_valide` (jamais de TypeError sur un horodatage
    naif) et meme duree que `visuel_saute_pause_minutes` : un compteur
    silencieux depuis plus longtemps que la pause qu'on poserait de toute
    facon n'a plus rien a compter."""
    limite = dt.timedelta(minutes=max(1, settings.visuel_saute_pause_minutes))
    try:
        return maintenant - dernier <= limite
    except TypeError:
        logger.warning("horodatage de report illisible (comparaison impossible) : ignore")
        return False


def _lire(chemin: Path) -> Etat:
    """L'etat connu. Jamais d'exception : un fichier absent, tronque ou hors
    schema vaut « rien de connu » -- comme `quota_graph._lire`, on prefere un
    article retente de trop a un planificateur qui meurt faute de fichier."""
    try:
        donnees = json.loads(chemin.read_text())
    except Exception:  # noqa: BLE001 -- absent, tronque, illisible
        return Etat()
    if not isinstance(donnees, dict):
        return Etat()
    pauses: dict[int, dt.datetime] = {}
    for article_id, valeur in (donnees.get("pauses") or {}).items():
        try:
            jusqu_a = dt.datetime.fromisoformat(valeur)
            pauses[int(article_id)] = jusqu_a
        except (TypeError, ValueError):
            continue
    reports: dict[int, Report] = {}
    for article_id, valeur in (donnees.get("reports") or {}).items():
        try:
            reports[int(article_id)] = (int(valeur["compte"]), dt.datetime.fromisoformat(valeur["le"]))
        except (TypeError, ValueError, KeyError):
            continue
    return Etat(pauses, reports)


def _ecrire(chemin: Path, etat: Etat) -> None:
    """Ecriture ATOMIQUE, temporaire NOMME PAR PROCESSUS (`tempfile.mkstemp`) :
    un `.tmp` partage entre deux ecrivains donnerait un JSON melange (meme
    piege que `quota_graph.py`)."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    descripteur, temporaire = tempfile.mkstemp(dir=chemin.parent, prefix=chemin.name + ".")
    try:
        with os.fdopen(descripteur, "w") as fichier:
            json.dump(
                {
                    "pauses": {str(aid): t.isoformat() for aid, t in etat.pauses.items()},
                    "reports": {
                        str(aid): {"compte": compte, "le": dernier.isoformat()}
                        for aid, (compte, dernier) in etat.reports.items()
                    },
                },
                fichier,
            )
        os.replace(temporaire, chemin)
    except Exception:
        Path(temporaire).unlink(missing_ok=True)
        raise


def _fusionner_et_ecrire(
    chemin: Path,
    maintenant: dt.datetime,
    *,
    nouvelle_pause: tuple[int, dt.datetime] | None = None,
    nouveau_report: tuple[int, Report | None] | None = None,
) -> None:
    """Relit le disque A L'INSTANT DE L'ECRITURE et fusionne -- jamais a
    partir d'un instantane pris plus tot dans l'appel (voir le docstring du
    module). Purge au passage les pauses echues ET les compteurs de reports
    devenus trop anciens (`_report_valide`). `nouveau_report=(id, None)`
    efface le compteur de cet article (`reinitialiser_report`)."""
    etat = _lire(chemin)
    pauses = {aid: t for aid, t in etat.pauses.items() if _pause_valide(maintenant, t)}
    reports = {
        aid: report for aid, report in etat.reports.items() if _report_valide(maintenant, report[1])
    }
    if nouvelle_pause is not None:
        article_id, fin = nouvelle_pause
        pauses[article_id] = fin
        reports.pop(article_id, None)  # une pause rend le compteur de reports obsolete
    if nouveau_report is not None:
        article_id, report = nouveau_report
        if report is None:
            reports.pop(article_id, None)
        else:
            reports[article_id] = report
    try:
        _ecrire(chemin, Etat(pauses, reports))
    except OSError:
        logger.exception("etat de mise a l'ecart non enregistre (%s)", chemin)


def articles_en_pause(maintenant: dt.datetime, chemin: Path | None = None) -> set[int]:
    """Les ids d'article encore mis a l'ecart a cet instant -- a exclure des
    candidats (`store._articles_publiables`)."""
    etat = _lire(chemin or chemin_etat())
    return {article_id for article_id, jusqu_a in etat.pauses.items() if _pause_valide(maintenant, jusqu_a)}


def marquer_saute(
    article_id: int,
    maintenant: dt.datetime,
    *,
    duree: dt.timedelta | None = None,
    chemin: Path | None = None,
) -> dt.datetime:
    """Met l'article a l'ecart pour `duree` (defaut : `visuel_saute_pause_minutes`).

    Purge au passage les entrees echues -- d'autres articles que celui-ci --
    pour que le fichier ne grossisse jamais indefiniment. Rend l'instant de
    fin de la pause. Une panne d'ecriture (disque plein, permissions) ne casse
    jamais l'appelant : sans etat persiste, le prochain passage retentera
    l'article un peu plus tot que prevu, ce qui reste strictement moins grave
    que le defaut que cette pause corrige.

    L'appelant doit poser cette mise a l'ecart SEULEMENT apres avoir
    reellement liberer le creneau concerne -- voir l'invariant d'ordre dans
    le docstring du module.
    """
    chemin = chemin or chemin_etat()
    duree = duree if duree is not None else dt.timedelta(minutes=max(1, settings.visuel_saute_pause_minutes))
    fin = maintenant + duree
    _fusionner_et_ecrire(chemin, maintenant, nouvelle_pause=(article_id, fin))
    return fin


def signaler_report(
    article_id: int,
    maintenant: dt.datetime,
    *,
    seuil: int | None = None,
    chemin: Path | None = None,
) -> bool:
    """Compte un REPORT (`VisuelsDifferes`) de plus pour cet article et rend
    True si `seuil` (defaut `visuel_differe_max_reports`) reports CONSECUTIFS
    sont atteints -- un report qui ne guerit jamais rejoue exactement le
    defaut qu'une mise a l'ecart corrige : l'article campe sur son creneau
    jusqu'a MANQUE au lieu de le liberer pour un autre.

    Ne pose PAS elle-meme la mise a l'ecart : contrairement a une version
    anterieure, cette fonction se contente de compter et de repondre. C'est a
    l'APPELANT de liberer le creneau et de n'appeler `marquer_saute` que si
    cette liberation reussit (meme invariant d'ordre que `runner._publier`,
    voir le docstring du module) -- sinon un article prend une pause pour un
    creneau qu'on n'a pas reussi a lui retirer.
    """
    chemin = chemin or chemin_etat()
    seuil = seuil if seuil is not None else max(1, settings.visuel_differe_max_reports)
    connu = _lire(chemin).reports.get(article_id)
    compte = (connu[0] if connu is not None and _report_valide(maintenant, connu[1]) else 0) + 1
    _fusionner_et_ecrire(chemin, maintenant, nouveau_report=(article_id, (compte, maintenant)))
    return compte >= seuil


def reinitialiser_report(article_id: int, maintenant: dt.datetime, *, chemin: Path | None = None) -> None:
    """Efface le compteur de reports consecutifs : appele des qu'une
    composition reussit pour cet article -- seule une SUITE ininterrompue de
    reports doit compter, un succes la casse."""
    chemin = chemin or chemin_etat()
    _fusionner_et_ecrire(chemin, maintenant, nouveau_report=(article_id, None))
