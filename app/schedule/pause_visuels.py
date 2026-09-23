"""Mise a l'ecart TEMPORAIRE d'un article saute faute de visuel de qualite.

POURQUOI (tour de correction 1/5 sur la Tache 5, mesure sur 120 passages
simules). Le saut (`compose.VisuelIntrouvable`) libere le creneau et relance
l'arbitrage DANS LE MEME PASSAGE (`app/schedule/runner.py::_composer`,
`exclusions = True`) -- mais l'arbitrage classe par score et l'article sauteur
reste ELIGIBLE. S'il a le meilleur score, il revient sur le creneau le plus
proche, saute a nouveau, et ainsi de suite jusqu'a MANQUE, puis glisse au
creneau suivant : mesure, un seul article a 95 de pertinence sans visuel face
a quatre bons articles a 67-70 a fait perdre TOUTE LA JOURNEE de publication
(3 creneaux en MANQUE, zero post, 120 sauts, un 4e creneau deja repris par le
meme article).

Ce n'est PAS une exclusion : la Tache 5 l'interdit explicitement, l'article
peut avoir un visuel demain (nouvelle publication du compte source, recherche
web qui ressort du cache). Une pause TEMPORAIRE la respecte tout en arretant
le cycle : l'article sort des candidats le temps que quelque chose ait une
chance raisonnable d'avoir change (`visuel_saute_pause_minutes`, justifie dans
`app/config.py`), puis redevient eligible comme n'importe quel autre.

TOUR DE CORRECTION 2/5 -- le meme defaut existe pour un REPORT persistant.
Mesure : le meme scenario (un article au meilleur score, mais dont une
reserve leve `VisuelDiffere` a CHAQUE passage au lieu d'un saut) donne 1
publication, 3 MANQUE -- `runner._composer` fait `continue` sur
`VisuelsDifferes` sans jamais liberer le creneau ni mettre l'article a
l'ecart, donc il campe dessus jusqu'a MANQUE. Un report reste le bon geste
(retenter EST la reponse a une panne de transport), mais il ne peut pas etre
ETERNEL : `signaler_report` compte les reports CONSECUTIFS et, au-dela de
`visuel_differe_max_reports` (6 par defaut), convertit en la MEME mise a
l'ecart qu'un saut -- ce nombre est cale sur la pause de quota Graph (60 min,
`veille_pause_quota_minutes`) rapportee a la cadence de publication (10 min) :
6 passages laissent a une panne de quota le temps de se resorber TOUTE SEULE
avant d'abandonner. `reinitialiser_report` efface le compteur des qu'une
composition reussit pour l'article : seule une SUITE ininterrompue de reports
compte.

MEME MOTIF que `app/pipeline/quota_graph.py` (fichier d'etat a cote du jeton,
ecriture atomique, temporaire nomme par processus) : plusieurs entrees, une
par article en pause ou en report, purgees des qu'elles sont echues --
l'archive ne grossit jamais. Aucune migration : rien de ce fichier ne vit en
base.

ECRITURES CONCURRENTES (tour de correction 2/5, point 5) : mesure par la
revue -- A lit l'etat, B ecrit la pause de l'article 2, A ecrit la sienne (sur
l'article 1) a partir de son instantane PERIME (sans l'article 2), qui
disparait. `_fusionner_et_ecrire` relit le disque A L'INSTANT DE L'ECRITURE et
fusionne, exactement comme `app.pipeline.cluster_arbitrage.fusionner_et_ecrire`
(Tache 7) : un second agent (aujourd'hui, seul `run_publication` ecrit ici ;
demain, un autre point d'entree) ne doit jamais voir sa pause effacee par une
reecriture a partir d'un instantane pris plus tot dans l'appel.

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


@dataclass(frozen=True)
class Etat:
    """L'etat persiste : les pauses en cours, et les compteurs de reports
    CONSECUTIFS (tour de correction 2/5) -- un seul fichier, une seule
    fusion, sur le meme modele que `cluster_arbitrage.Cache` (entrees +
    tentatives)."""

    pauses: dict[int, dt.datetime] = field(default_factory=dict)
    reports: dict[int, int] = field(default_factory=dict)


def _pause_valide(maintenant: dt.datetime, jusqu_a: dt.datetime) -> bool:
    """Vrai si la pause tient encore. Ne leve JAMAIS (tour de correction 2/5,
    point 4) : un horodatage NAIF sur le disque (corruption, bug amont)
    compare a un `maintenant` conscient du fuseau leve un `TypeError` --
    inatteignable aujourd'hui, mais ce module est appele depuis
    `store._articles_publiables`, donc depuis l'arbitrage, `candidats_
    eligibles` ET `offre_reels` : une exception ici ferait tomber les trois.
    Une comparaison ratee vaut « pause perimee » -- retenter un peu tot est
    sans commune mesure avec un planificateur a l'arret."""
    try:
        return maintenant < jusqu_a
    except TypeError:
        logger.warning("horodatage de pause illisible (comparaison impossible) : ignoree")
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
    reports: dict[int, int] = {}
    for article_id, valeur in (donnees.get("reports") or {}).items():
        try:
            reports[int(article_id)] = int(valeur)
        except (TypeError, ValueError):
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
                    "reports": {str(aid): n for aid, n in etat.reports.items()},
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
    nouveau_report: tuple[int, int | None] | None = None,
) -> None:
    """Relit le disque A L'INSTANT DE L'ECRITURE et fusionne -- jamais a partir
    d'un instantane pris plus tot dans l'appel (voir le docstring du module,
    tour de correction 2/5, point 5). `nouveau_report=(id, None)` efface le
    compteur de cet article (`reinitialiser_report`)."""
    etat = _lire(chemin)
    pauses = {aid: t for aid, t in etat.pauses.items() if _pause_valide(maintenant, t)}
    reports = dict(etat.reports)
    if nouvelle_pause is not None:
        article_id, fin = nouvelle_pause
        pauses[article_id] = fin
        reports.pop(article_id, None)  # une pause rend le compteur de reports obsolete
    if nouveau_report is not None:
        article_id, compte = nouveau_report
        if compte is None:
            reports.pop(article_id, None)
        else:
            reports[article_id] = compte
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
    """Compte un REPORT (`VisuelsDifferes`) de plus pour cet article. Au-dela
    de `seuil` (defaut `visuel_differe_max_reports`) reports CONSECUTIFS,
    convertit en la MEME mise a l'ecart temporaire qu'un saut -- un report qui
    ne guerit jamais rejoue exactement le defaut C-1 (tour de correction 2/5,
    point 1) : l'article campe sur son creneau jusqu'a MANQUE au lieu de le
    liberer pour un autre.

    Rend True si l'article vient de basculer en pause (l'appelant doit alors
    LIBERER le creneau, comme pour un saut) ; False s'il reste un simple
    report (le creneau garde l'article, il retentera au passage suivant).
    """
    chemin = chemin or chemin_etat()
    seuil = seuil if seuil is not None else max(1, settings.visuel_differe_max_reports)
    compte = _lire(chemin).reports.get(article_id, 0) + 1
    if compte >= seuil:
        marquer_saute(article_id, maintenant, chemin=chemin)
        return True
    _fusionner_et_ecrire(chemin, maintenant, nouveau_report=(article_id, compte))
    return False


def reinitialiser_report(article_id: int, maintenant: dt.datetime, *, chemin: Path | None = None) -> None:
    """Efface le compteur de reports consecutifs : appele des qu'une
    composition reussit pour cet article -- seule une SUITE ininterrompue de
    reports doit compter, un succes la casse."""
    chemin = chemin or chemin_etat()
    _fusionner_et_ecrire(chemin, maintenant, nouveau_report=(article_id, None))
