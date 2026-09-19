"""La veille rapide : une liste courte de comptes Instagram, relue toutes les
cinq minutes.

POURQUOI (mesure du 2026-09-18, `docs/superpowers/specs/2026-09-18-etre-une-
source-pas-un-intermediaire.md`). Kinda lisait les comptes d'artistes toutes les
3 h (latence mediane 141 min) et la presse RSS en 48 min : sur le cas Charlotte
de Witte, il a donc appris l'actu par DJ Mag **39 heures avant** de la voir chez
l'artiste. C'est la definition mecanique d'un intermediaire, et la latence etait
entierement la notre -- la propagation cote Meta est quasi immediate.

POURQUOI UNE LISTE COURTE. Lire les 123 comptes toutes les 5 minutes demanderait
~1 476 appels Graph par heure, 3,4x le plafond observe en production (~430/h ont
rendu le code 4 pendant une heure, le 2026-09-17). Douze comptes en font 144.

CE QUE CE MODULE NE FAIT PAS. Ni illustration (requetes reseau bornees a 40 par
run) ni retention : ces deux etapes sont concues pour tourner une fois par heure
et restent dans `run_pipeline`. Un passage de veille ingere, regroupe, score un
petit lot et enregistre les mentions -- rien d'autre.

LE QUOTA EST PARTAGE avec le PIPELINE HORAIRE et avec la PUBLICATION
(`business_discovery` des visuels d'artistes). D'ou un plafond dur d'appels par
heure, propre a la veille, et une PAUSE PARTAGEE des que Meta rend un code de
quota (`app/pipeline/quota_graph.py`).

Ce module a longtemps porte que « le pipeline horaire n'est jamais suspendu :
sa cadence n'est pas en cause ». La production l'a dementi le 2026-09-19 : un
appel refuse pour quota consomme du quota, donc le passage horaire qui insistait
sur 123 comptes entretenait tout seul le depassement (751 refus dans le log,
jusqu'a 116 sources « en erreur » par run). La pause vaut desormais pour les
deux agents -- la publication, elle, n'est toujours pas suspendue.
"""

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.delivery.notify import notifier as notifier_par_defaut
from app.models import Source, SourceType
from app.pipeline.artists import record_mentions
from app.pipeline.cluster import link_duplicates
from app.pipeline.health import marquer_sante
from app.pipeline.ingest import IngestStats, ingest_source
from app.pipeline.quota_graph import declarer_quota, pause_en_cours
from app.pipeline.scoring import score_pending
from app.sources import get_adapter as get_adapter_par_defaut
from app.sources.instagram import QuotaGraphAtteint, compte_depuis_url, compte_surveille

logger = logging.getLogger(__name__)

# A cote du jeton, comme les autres compteurs (`sourcing_token_alert.json`,
# `publication_panne.json`) : meme dossier applicatif, hors du depot.
FICHIER_ETAT = "veille.json"


def chemin_etat() -> Path:
    return settings.meta_token_path.parent / FICHIER_ETAT


@dataclass
class EtatVeille:
    """Le compteur d'appels de l'heure en cours.

    `heure` est le debut de l'heure comptee : un passage d'une autre heure
    remet `appels` a zero. La PAUSE de quota, elle, ne vit plus ici : elle est
    partagee avec le pipeline horaire (`app/pipeline/quota_graph.py`), parce
    que le quota qu'elle protege est celui de l'app et non celui d'un agent.
    """

    heure: dt.datetime | None = None
    appels: int = 0


def _iso(valeur: dt.datetime | None) -> str | None:
    return valeur.isoformat() if valeur else None


def _datetime(valeur) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(valeur) if valeur else None
    except (TypeError, ValueError):
        return None


def lire_etat(chemin: Path) -> EtatVeille:
    """L'etat relu. Absent ou illisible vaut « rien de connu » : un agent
    planifie ne doit jamais mourir faute de fichier (meme regle que
    `app/publish/mode.py`)."""
    try:
        donnees = json.loads(chemin.read_text())
    except Exception:  # noqa: BLE001 -- absent, tronque, illisible : on repart a zero
        return EtatVeille()
    if not isinstance(donnees, dict):
        return EtatVeille()
    appels = donnees.get("appels")
    return EtatVeille(
        heure=_datetime(donnees.get("heure")),
        appels=appels if isinstance(appels, int) and appels >= 0 else 0,
    )


def enregistrer_etat(chemin: Path, etat: EtatVeille) -> None:
    """Ecriture ATOMIQUE : 288 passages par jour finiraient par en interrompre
    un, et un JSON tronque ferait repartir le compteur a zero -- donc rouvrirait
    le budget en plein depassement."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_suffix(".tmp")
    temporaire.write_text(json.dumps({"heure": _iso(etat.heure), "appels": etat.appels}))
    os.replace(temporaire, chemin)


def source_surveillee(source: Source) -> bool:
    """Une source Instagram dont le compte est sur la liste. JAMAIS une autre :
    la veille n'existe que pour `business_discovery`, et lire un flux RSS a
    cette cadence ferait bannir l'editeur."""
    return source.type == SourceType.INSTAGRAM and compte_surveille(
        compte_depuis_url(source.url or "")
    )


def sources_surveillees(db: Session) -> list[Source]:
    sources = db.scalars(
        select(Source).where(Source.active, Source.type == SourceType.INSTAGRAM)
    )
    return [s for s in sources if source_surveillee(s)]


@dataclass
class RapportVeille:
    ingest: list[IngestStats] = field(default_factory=list)
    doublons: int = 0
    scoring: object | None = None
    mentions: int = 0
    # Appels Graph reellement passes pendant ce passage.
    appels: int = 0
    # Le plafond horaire a coupe la liste avant la fin.
    plafond_atteint: bool = False
    # Meta a rendu un code de quota, ou une pause etait deja en cours.
    en_pause: bool = False

    @property
    def nouveaux(self) -> int:
        return sum(s.new for s in self.ingest)

    @property
    def erreurs(self) -> list[str]:
        return [f"{s.source_name}: {s.error}" for s in self.ingest if s.error]


def _budget(etat: EtatVeille, maintenant: dt.datetime) -> int:
    """Ce qui reste du plafond horaire. Le compteur repart a chaque heure."""
    heure = maintenant.replace(minute=0, second=0, microsecond=0)
    if etat.heure != heure:
        etat.heure = heure
        etat.appels = 0
    return max(0, settings.veille_appels_max_par_heure - etat.appels)


def run_veille(
    db: Session,
    maintenant: dt.datetime,
    get_adapter=get_adapter_par_defaut,
    cluster=link_duplicates,
    score=score_pending,
    record=record_mentions,
    notify=notifier_par_defaut,
) -> RapportVeille:
    """Un passage de veille : ingestion des comptes surveilles, dedup, scoring
    borne, mentions. Ne leve pas sur une source cassee.
    """
    rapport = RapportVeille()
    # Sans jeton, l'adapter Instagram rend zero item SANS appeler Meta : compter
    # ces passages comme des appels ferait sauter le plafond horaire en vingt
    # minutes, pour rien. Meme silence que les alertes sans webhook.
    if not settings.meta_fb_access_token or not settings.meta_ig_user_id:
        logger.info("Veille rapide sautee : META_FB_ACCESS_TOKEN ou META_IG_USER_ID absent")
        return rapport
    # La pause est PARTAGEE : un code de quota encaisse par le passage horaire
    # vaut aussi pour la veille, et reciproquement. C'est le meme seau.
    if pause_en_cours(maintenant) is not None:
        rapport.en_pause = True
        return rapport

    chemin = chemin_etat()
    etat = lire_etat(chemin)
    budget = _budget(etat, maintenant)
    for source in sources_surveillees(db):
        if budget <= 0:
            rapport.plafond_atteint = True
            break
        try:
            adapter = get_adapter(source)
            # Un compte dont l'espacement n'est pas echu n'appelle pas Meta : il
            # ne consomme donc pas de budget. On l'ecarte avant de compter.
            if not adapter.a_lire(maintenant):
                continue
            budget -= 1
            etat.appels += 1
            rapport.appels += 1
            stats = ingest_source(db, source, adapter=adapter)
        except QuotaGraphAtteint as exc:
            # On s'ARRETE au premier code de quota : insister aggraverait le
            # depassement, et le quota est partage avec la publication.
            db.rollback()
            rapport.en_pause = True
            declarer_quota(maintenant, str(exc), "veille rapide", notify=notify)
            break
        except Exception as exc:  # noqa: BLE001 -- une source cassee n'arrete pas le passage
            logger.exception("Veille : ingestion echouee pour %s", source.name)
            db.rollback()
            stats = IngestStats(source_id=source.id, source_name=source.name, error=str(exc))
        marquer_sante(source, stats)
        db.commit()
        rapport.ingest.append(stats)

    enregistrer_etat(chemin, etat)

    # Rien de neuf : pas une requete de plus. 288 passages par jour.
    if rapport.nouveaux:
        rapport.doublons = cluster(db)
        if settings.anthropic_api_key:
            rapport.scoring = score(db, limit=settings.veille_scoring_max)
        rapport.mentions = record(db)
    return rapport
