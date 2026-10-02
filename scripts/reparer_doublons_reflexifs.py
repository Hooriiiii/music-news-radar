"""Rendre leur place aux articles devenus LEUR PROPRE doublon.

Mesure en base de production le 2026-10-02 : sur 662 articles, 59 portaient un
lien de doublon et 3 pointaient sur leur propre `id` (21627 Music Business
Worldwide, 21709 Mixmag (IG), 21599 Billboard (RSS)). La cause est corrigee a
la source dans `app/pipeline/cluster.py` (voir `cibles_a_repointer` et la garde
de `resoudre`) ; ce script repare les lignes DEJA en base, que le correctif ne
touche pas.

Le degat etait silencieux : un article reflexif se presente comme un doublon,
donc `score_pending` (qui filtre `duplicate_of_id IS NULL`) ne le note jamais
et la file de publication ne le propose jamais. L'article 21599 attendait
depuis 28 h sans note et n'en aurait jamais eu -- il aurait disparu a la purge
sans que rien ne le signale.

La reparation est d'effacer le SEUL lien fautif, rien d'autre :

- `duplicate_of_id` repasse a NULL. L'article redevient le representant de son
  groupe -- et c'est bien la place qui lui revient : les articles qui pointent
  vers lui le faisaient deja avant la corruption, et il est le plus ancien des
  deux dans les trois cas mesures (« le representant est l'article vu le plus
  tot »).
- Les NOTES ne sont jamais touchees : 21627 garde son 45 et 21709 son 65. Seul
  21599, qui n'a jamais eu de note, sera note par le passage suivant --
  automatiquement, puisqu'il redevient notable.
- `file_exclu_raison` n'est effacee que si elle a ete posee par la dedup
  (`raison_de_doublon`), jamais une exclusion venue d'ailleurs (« gel
  impossible : ... ») : meme filtre que l'auto-reparation d'un verdict manuel
  dans `link_duplicates`, et pour la meme raison -- l'effacer sans condition
  remettrait dans la file un article que la publication avait ecarte pour une
  vraie raison.

Aucun verdict manuel n'est pose : la paire n'a jamais ete un faux
rapprochement, c'est l'ECRITURE du lien qui etait fautive. Poser un
« different » interdirait pour toujours un lien qui, lui, est legitime.

Idempotent : relance sans rien a faire -> plan vide.

Usage :
    uv run python -m scripts.reparer_doublons_reflexifs            # plan
    uv run python -m scripts.reparer_doublons_reflexifs --apply    # ecrit
"""

import argparse
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, attendre_base
from app.models import Article
from app.pipeline.cluster import raison_de_doublon


@dataclass
class Reparation:
    article_id: int
    titre: str
    source: str
    note: int | None
    raison_effacee: str | None
    suiveurs: int


def reparer_doublons_reflexifs(db: Session, *, appliquer: bool = False) -> list[Reparation]:
    """Rend le plan, et l'ecrit si `appliquer`. Les notes ne sont pas touchees."""
    reflexifs = db.scalars(
        select(Article).where(Article.duplicate_of_id == Article.id).order_by(Article.id)
    ).all()
    plan: list[Reparation] = []
    for article in reflexifs:
        suiveurs = db.scalar(
            select(func.count())
            .select_from(Article)
            .where(Article.duplicate_of_id == article.id)
            .where(Article.id != article.id)
        ) or 0
        effacable = raison_de_doublon(article.file_exclu_raison)
        plan.append(
            Reparation(
                article_id=article.id,
                titre=article.title,
                source=article.source.name if article.source is not None else "?",
                note=article.relevance_score,
                raison_effacee=article.file_exclu_raison if effacable else None,
                suiveurs=suiveurs,
            )
        )
        if appliquer:
            article.duplicate_of_id = None
            if effacable:
                article.file_exclu_raison = None
    if appliquer and plan:
        db.commit()
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="ecrire pour de bon")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        plan = reparer_doublons_reflexifs(db, appliquer=args.apply)

    verbe = "delie(s)" if args.apply else "a delier"
    print(f"{len(plan)} article(s) leur propre doublon {verbe}")
    for r in plan:
        note = r.note if r.note is not None else "AUCUNE (sera notee au passage suivant)"
        print(f"  - #{r.article_id} [{r.source}] note {note}, "
              f"{r.suiveurs} doublon(s) rattache(s) : {r.titre[:70]}")
        if r.raison_effacee is not None:
            print(f"      raison d'exclusion effacee : {r.raison_effacee[:120]}")
    if not args.apply and plan:
        print("Relancer avec --apply pour appliquer.")


if __name__ == "__main__":
    main()
