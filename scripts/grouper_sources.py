"""Regrouper les sources qui sont le MEME media sur plusieurs canaux.

`sources.source_group_id` existe (migration 0019) et n'est rempli nulle part :
tant qu'elle est vide, `independent_source_count` (`app/pipeline/events.py::_recalculer`)
compte chaque source pour elle-meme, meme quand deux sources ne sont que deux
canaux du meme media -- une depeche reprise en RSS et sur Instagram par le meme
titre de presse compte alors pour DEUX confirmations independantes au lieu
d'une.

Mesure sur les sources actives (2026-09-24) : quatre groupes, tous du meme
motif, un media present en RSS et sur Instagram -- Mixmag, DJ Mag, Tsugi,
Billboard.

Piege a ne jamais reintroduire (`app/pipeline/events.py::_recalculer`) : une
source SANS groupe compte sous la cle `("source", son_id)`, une source AVEC
groupe sous `("groupe", id_du_groupe)`. Si un seul membre d'une paire porte
`source_group_id`, les deux tombent sous des cles DIFFERENTES et le
regroupement ne sert a rien -- TOUS les membres d'un groupe, la reference
comprise, doivent porter la MEME valeur.

Regles, sur le modele de `scripts/categoriser_sources.py` :
- un mapping EXPLICITE fait foi -- pas de detection par similarite de nom
  (une normalisation qui retire des mots communs rapprocherait des sources
  sans rapport sur un corpus de 145 sources) ;
- la valeur du groupe est l'`id` le plus petit parmi les sources trouvees
  pour ce groupe -- deterministe et lisible en base ;
- un `source_group_id` deja pose (par ce script ou a la main) et DIFFERENT de
  la cible n'est jamais ecrase -- seules les sources sans groupe (NULL) ou
  deja alignees sur la bonne valeur sont touchees ;
- un groupe de moins de deux sources trouvees en base n'a rien a regrouper ;
- une source du mapping absente de la base est signalee, pas une erreur ;
- idempotent.

Usage :
    uv run python -m scripts.grouper_sources           # affiche le plan
    uv run python -m scripts.grouper_sources --apply    # ecrit
"""

import argparse
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, attendre_base
from app.models import Source, SourceType

# Quatre groupes seulement (mesure du 2026-09-24) : un media present en RSS
# et sur Instagram. Cle = (nom exact de la source, type). Ne pas etendre par
# un detecteur automatique -- voir le docstring du module.
GROUPES: tuple[tuple[str, frozenset[tuple[str, SourceType]]], ...] = (
    ("Mixmag", frozenset({("Mixmag", SourceType.RSS), ("Mixmag (IG)", SourceType.INSTAGRAM)})),
    ("DJ Mag", frozenset({("DJ Mag", SourceType.RSS), ("DJ Mag (IG)", SourceType.INSTAGRAM)})),
    ("Tsugi", frozenset({("Tsugi", SourceType.RSS), ("Tsugi (IG)", SourceType.INSTAGRAM)})),
    (
        "Billboard",
        frozenset({("Billboard (RSS)", SourceType.RSS), ("Billboard (IG)", SourceType.INSTAGRAM)}),
    ),
)


@dataclass(frozen=True)
class LignePlan:
    groupe: str
    source_id: int
    nom: str
    avant: int | None
    apres: int


def grouper_sources(
    db: Session, *, appliquer: bool = False
) -> tuple[list[LignePlan], list[str]]:
    """Rend (plan, avertissements) et ecrit si `appliquer`. Idempotent.

    `avertissements` couvre deux cas tolerants, jamais une erreur : un membre
    du mapping absent de la base, et un groupe deja pose differemment sur au
    moins un de ses membres (laisse intact, jamais applique a moitie)."""
    plan: list[LignePlan] = []
    absents: list[str] = []

    for nom_groupe, membres in GROUPES:
        trouvees: list[Source] = []
        for nom, type_ in membres:
            lignes = db.scalars(
                select(Source).where(Source.name == nom, Source.type == type_)
            ).all()
            if not lignes:
                absents.append(f"{nom_groupe} : « {nom} » ({type_.value}) absente de la base")
                continue
            trouvees.extend(lignes)

        if len(trouvees) < 2:
            continue

        cible = min(s.id for s in trouvees)
        conflit = any(
            s.source_group_id is not None and s.source_group_id != cible for s in trouvees
        )
        if conflit:
            # Au moins un membre porte deja un groupe different (pose a la
            # main, ou par un run precedent avec une autre reference) : on ne
            # touche a AUCUN membre. Poser la cible sur les autres membres
            # laisserait le groupe a moitie applique -- exactement le piege
            # que ce script existe pour eviter.
            absents.append(
                f"{nom_groupe} : groupe deja pose differemment sur au moins un membre, "
                "laisse en l'etat"
            )
            continue

        for source in trouvees:
            if source.source_group_id == cible:
                continue
            plan.append(
                LignePlan(
                    groupe=nom_groupe, source_id=source.id, nom=source.name,
                    avant=source.source_group_id, apres=cible,
                )
            )
            if appliquer:
                source.source_group_id = cible

    if appliquer:
        db.commit()
    return plan, absents


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
        # retention. Sans cette propagation, la colonne serait remplie et la
        # corroboration resterait fausse -- voir `scripts/rattraper_evenements.py`.
        print(
            "\nCe regroupement ne propage rien tout seul : lancer ensuite "
            "`uv run python -m scripts.rattraper_evenements --recalculer` "
            "(puis `--recalculer --apply`) pour mettre a jour les compteurs "
            "des evenements deja en base."
        )


if __name__ == "__main__":
    main()
