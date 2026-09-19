"""Ce qui doit déclencher une alerte, et ce qui ne doit surtout pas.

Ce module existe parce que TROIS pannes sont passées inaperçues : l'agent
`digest` qui échouait chaque matin depuis cinq semaines, la source
« X • hard techno FR » figée sept jours par un `since_id` hors fenêtre, et
les crédits API épuisés qui ont arrêté le scoring sans rien dire.

Le principe : ne signaler que ce qui demande une action. Une alerte qui sonne
pour rien apprend à ignorer le canal, et le prochain vrai problème passera
inaperçu comme les trois précédents.
"""

import datetime as dt
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Source

if TYPE_CHECKING:  # pragma: no cover
    # Importes SEULEMENT pour l'annotation : `ingest` importe `marquer_sante`
    # d'ici, donc un import a l'execution formerait un cycle. Ce module ne lit
    # que `.error`, `.source_name`, `.errors` -- il n'a pas besoin des classes.
    from app.pipeline.ingest import IngestStats
    from app.pipeline.scoring import ScoringStats

logger = logging.getLogger(__name__)

# Le message exact que rend l'API Anthropic quand la facturation bloque. On le
# reconnaît pour NOMMER la panne : sans ça elle passe pour une erreur réseau
# parmi d'autres, ce qui est précisément ce qui l'a rendue invisible.
CREDITS_EPUISES = "crédits API épuisés"
_SIGNATURE_CREDITS = "credit balance is too low"


def marquer_sante(source: Source, stats: "IngestStats") -> None:
    """Consigne dans `sources.state` ce que ce fetch a donné.

    Indispensable parce que les articles sont purgés à 14 jours : une source
    muette depuis trois semaines ne laisse AUCUNE trace dans `articles`, donc
    elle serait indétectable a posteriori.

    Deux précautions. On réassigne `state` en entier (SQLAlchemy ne persiste pas
    une mutation en place d'un JSONB). Et on FUSIONNE au lieu d'écraser : cet
    état porte aussi le `since_id` de l'adapter X, dont la perte ferait re-payer
    tous les tweets déjà lus.

    Une source NON APPELÉE (pause de quota Graph) ne dit rien de sa santé : ni
    succès à consigner, ni panne à signaler. On ne touche pas à son état --
    écrire un `last_ok_at` mentirait, écrire une erreur l'accuserait à tort, et
    effacer l'ancienne trace ferait oublier une source réellement morte.
    """
    if getattr(stats, "reportee", None):
        return
    etat = dict(source.state or {})
    maintenant = dt.datetime.now(dt.timezone.utc).isoformat()
    if stats.error:
        etat["last_error"] = stats.error[:500]
        etat["last_error_at"] = maintenant
    else:
        etat["last_ok_at"] = maintenant
        # Le succès efface l'erreur : la source est repartie, garder l'ancienne
        # trace ferait alerter sur un problème résolu.
        etat.pop("last_error", None)
        etat.pop("last_error_at", None)
    source.state = etat


def _muette_depuis(etat: dict) -> float | None:
    """Nombre de jours depuis le dernier succès, ou None si on ne sait pas."""
    horodatage = (etat or {}).get("last_ok_at")
    if not horodatage:
        return None
    try:
        dernier = dt.datetime.fromisoformat(horodatage)
    except ValueError:
        return None
    if dernier.tzinfo is None:
        dernier = dernier.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - dernier).total_seconds() / 86400


def diagnostiquer(
    db: Session, ingest: list["IngestStats"], scoring: "ScoringStats | None"
) -> list[str]:
    """La liste des problèmes qui méritent de déranger. Vide = tout va bien."""
    problemes: list[str] = []

    if scoring is not None and scoring.errors:
        derniere = getattr(scoring, "derniere_erreur", None) or ""
        if _SIGNATURE_CREDITS in derniere:
            problemes.append(f"{CREDITS_EPUISES} — le scoring est arrêté")
        else:
            problemes.append(f"scoring en échec sur {scoring.errors} article(s)")

    # Une source NON APPELÉE n'est pas une source en erreur. Le 18/09, la pause
    # de quota manquante a fait compter 115 refus Meta comme autant de pannes :
    # « 116 source(s) en erreur » à chaque run horaire, sous lesquelles la vraie
    # panne était invisible. La pause, elle, est notifiée une fois par
    # `quota_graph.declarer_quota` -- inutile de la répéter à chaque run.
    en_erreur = [s.source_name for s in ingest if s.error and not getattr(s, "reportee", None)]
    if en_erreur:
        problemes.append(f"{len(en_erreur)} source(s) en erreur : {', '.join(en_erreur[:3])}")

    seuil = settings.source_muette_jours
    muettes = []
    for source in db.scalars(select(Source).where(Source.active)):
        jours = _muette_depuis(source.state)
        # None = source jamais fetchée avec succès depuis que l'on consigne cet
        # état : ce n'est pas une panne, c'est une source neuve. Alerter ici
        # ferait sonner chaque ajout de source.
        if jours is not None and jours >= seuil:
            muettes.append((source.name, jours))
    if muettes:
        muettes.sort(key=lambda x: -x[1])
        noms = ", ".join(f"{nom} ({jours:.0f} j)" for nom, jours in muettes[:3])
        problemes.append(f"{len(muettes)} source(s) muette(s) depuis plus de {seuil} j : {noms}")

    return problemes
