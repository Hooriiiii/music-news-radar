"""Remet a NULL les `event_observations.published_at` qui portent en realite
une date d'INGESTION.

Pourquoi. Jusqu'au tour de correction 1 de la Phase 2C,
`events._moment_fait` ecrivait `article.published_at or article.fetched_at`
dans l'observation : pour un article sans date de publication, la colonne
`published_at` portait notre heure d'ingestion, sans que rien ne le trace. La
velocite (`app/pipeline/event_features.py`) mesurait donc partiellement notre
propre cadence horaire -- exactement ce que la phase existe pour eviter.
L'ecriture ne replie plus ; ce script est le RATTRAPAGE des lignes posees
avant.

CE QU'IL PEUT ET NE PEUT PAS REPARER. Une observation ne dit pas elle-meme si
sa date etait un repli. On le sait seulement en relisant l'ARTICLE dont elle
vient : si `articles.published_at IS NULL`, la date de l'observation ne
pouvait etre que `fetched_at`. Mesure du 2026-09-30 : 548 observations sur
1 259 ont encore leur article en base, 79 d'entre elles sont dans ce cas.
**Pour les 711 autres, l'article a ete purge (retention de deux jours) et
l'information est PERDUE pour de bon** -- le script le dit plutot que de faire
semblant, et ne touche a aucune d'elles : sans l'article, une date de
publication legitime et une date d'ingestion deguisee sont indistinguables, et
effacer les deux detruirait de vraies mesures.

Idempotent : une seconde passe ne trouve plus rien (les lignes visees sont
alors a NULL).

Usage :
    uv run python -m scripts.reparations.nettoyer_dates_observations            # le plan
    uv run python -m scripts.reparations.nettoyer_dates_observations --apply
"""

import argparse
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, attendre_base
from app.models import Article, EventObservation


@dataclass
class Rapport:
    observations: int = 0
    corrigees: int = 0
    article_vivant: int = 0
    article_purge: int = 0
    exemples: list[str] = field(default_factory=list)


def nettoyer_dates(db: Session, *, appliquer: bool = False, limite_exemples: int = 10) -> Rapport:
    rapport = Rapport()
    lignes = db.execute(
        select(EventObservation, Article.published_at)
        .outerjoin(Article, Article.id == EventObservation.article_id)
        .where(EventObservation.published_at.is_not(None))
    ).all()
    for observation, publie_de_l_article in lignes:
        rapport.observations += 1
        if observation.article_id is None:
            # Article purge (`ON DELETE SET NULL`) : indecidable, on n'y touche
            # pas -- voir le docstring.
            rapport.article_purge += 1
            continue
        rapport.article_vivant += 1
        if publie_de_l_article is not None:
            continue
        # L'article vit et n'a AUCUNE date de publication : la date portee par
        # l'observation ne peut etre que son `fetched_at`.
        rapport.corrigees += 1
        if len(rapport.exemples) < limite_exemples:
            rapport.exemples.append(
                f"observation {observation.id} (evenement {observation.event_id}) : "
                f"{observation.published_at} -> NULL"
            )
        if appliquer:
            observation.published_at = None
    if appliquer:
        db.commit()
    return rapport


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        rapport = nettoyer_dates(db, appliquer=args.apply)
        for exemple in rapport.exemples:
            print(f"  {exemple}")
        print(
            f"\n{rapport.observations} observation(s) datee(s) : "
            f"{rapport.article_vivant} dont l'article vit encore, "
            f"{rapport.article_purge} dont l'article a ete purge "
            f"(indecidable, non touchees)."
        )
        if not rapport.corrigees:
            print("Aucune date d'ingestion deguisee reparable : rien a faire.")
            return
        if args.apply:
            print(f"{rapport.corrigees} date(s) d'ingestion remise(s) a NULL.")
        else:
            print(
                f"{rapport.corrigees} date(s) d'ingestion a remettre a NULL. "
                "Relancer avec --apply pour appliquer."
            )


if __name__ == "__main__":
    main()
