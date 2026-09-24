"""Regrouper les sources qui sont le MEME media sur plusieurs canaux.

La logique (mapping explicite, pose des `source_group_id`) vit dans
`app.pipeline.source_groups` -- ce script n'est plus qu'un point d'entree en
ligne de commande, sur le meme motif que `scripts/rattraper_evenements.py`
(voir le docstring de ce module pour le pourquoi du deplacement, tour de
correction 1, 2026-09-24). `GROUPES`, `LignePlan` et `grouper_sources` sont
RE-EXPORTES ici pour que ce script et ses tests existants continuent de les
importer depuis `scripts.grouper_sources` sans rien changer.

Usage :
    uv run python -m scripts.grouper_sources           # affiche le plan
    uv run python -m scripts.grouper_sources --apply    # ecrit
"""

import argparse

from app.db import SessionLocal, attendre_base
from app.pipeline.source_groups import GROUPES, LignePlan, grouper_sources  # noqa: F401


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="ecrire pour de bon")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        plan, absents = grouper_sources(db, appliquer=args.apply)

    verbe = "regroupee(s)" if args.apply else "a regrouper"
    print(f"{len(plan)} source(s) {verbe}")
    for ligne in plan:
        avant_txt = ligne.avant if ligne.avant is not None else "-"
        print(f"  - [{ligne.groupe}] {ligne.nom} (id={ligne.source_id}) : "
              f"{avant_txt} -> {ligne.apres}")

    if absents:
        print(f"\n{len(absents)} avertissement(s) :")
        for msg in absents:
            print(f"  - {msg}")

    if not args.apply and plan:
        print("\nRelancer avec --apply pour appliquer.")

    if plan:
        # `sources.source_group_id` ne recalcule rien tout seul : les
        # compteurs (`independent_source_count` en tete) restent STOCKES sur
        # `events` jusqu'a ce qu'un nouveau membre declenche `attacher`, ce
        # qui n'arrive pas pour les evenements deja sortis de leur fenetre de
        # retention. Le passage horaire propage desormais lui-meme ce
        # regroupement (`app.pipeline.source_groups.regrouper_sources_et_propager`) ;
        # ce message ne concerne que ce script lance a la main.
        print(
            "\nCe regroupement ne propage rien tout seul : lancer ensuite "
            "`uv run python -m scripts.rattraper_evenements --recalculer` "
            "(puis `--recalculer --apply`) pour mettre a jour les compteurs "
            "des evenements deja en base."
        )


if __name__ == "__main__":
    main()
