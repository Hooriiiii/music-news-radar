"""Re-appliquer le garde-fou « post Instagram quasi vide » aux articles deja notes.

Aucun modele n'est appele : on plafonne relevance et hotness a 29 (emergence
intacte) sur les notes existantes, pour nettoyer la file sans rien payer.

Limite a connaitre : une fois note, un article a perdu sa caption (`apply_score`
remplace le summary par le resume du modele). Seul le TITRE reste, et il n'est
que la premiere phrase de la premiere ligne de la caption. D'ou deux niveaux :
- par defaut : titre == « Post Instagram » (caption vide ou hashtags seuls),
  seul cas certain ;
- `--titres-courts` : titre de moins de 6 mots significatifs (« [📸] »,
  « 🗂️ I-VIEW »). Probable mais pas certain -- la suite de la caption pouvait
  porter l'info. Relire le plan avant `--apply`.

Les articles publies (USED) ne sont jamais modifies : c'est l'archive.

Usage :
    uv run python -m scripts.reparations.appliquer_garde_fous                   # plan
    uv run python -m scripts.reparations.appliquer_garde_fous --titres-courts   # plan elargi
    uv run python -m scripts.reparations.appliquer_garde_fous --apply [--titres-courts]
"""

import argparse
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, attendre_base
from app.models import Article, ArticleStatus, Source, SourceType
from app.pipeline.scoring import PLAFOND_POST_VIDE
from app.sources.instagram import TITRE_PAR_DEFAUT, caption_quasi_vide


@dataclass
class Correction:
    article_id: int
    titre: str
    source: str
    avant: tuple[int, int]
    apres: tuple[int, int]


def _vise(titre: str, titres_courts: bool) -> bool:
    if titre == TITRE_PAR_DEFAUT:
        return True
    return titres_courts and caption_quasi_vide(titre)


def appliquer_garde_fous_existants(
    db: Session, *, appliquer: bool = False, titres_courts: bool = False
) -> list[Correction]:
    """Rend le plan et l'ecrit si `appliquer`. Idempotent."""
    stmt = (
        select(Article)
        .join(Source, Article.source_id == Source.id)
        .where(Source.type == SourceType.INSTAGRAM)
        .where(Article.relevance_score.is_not(None))
        .where(Article.status != ArticleStatus.USED)
        .where(
            or_(
                Article.relevance_score > PLAFOND_POST_VIDE,
                Article.hotness_score > PLAFOND_POST_VIDE,
            )
        )
        .order_by(Article.id)
    )
    plan = []
    for article in db.scalars(stmt).all():
        if not _vise(article.title, titres_courts):
            continue
        avant = (article.relevance_score, article.hotness_score or 0)
        apres = (min(avant[0], PLAFOND_POST_VIDE), min(avant[1], PLAFOND_POST_VIDE))
        plan.append(Correction(article.id, article.title, article.source.name, avant, apres))
        if appliquer:
            article.relevance_score, article.hotness_score = apres
    if appliquer:
        db.commit()
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="ecrire pour de bon")
    parser.add_argument("--titres-courts", action="store_true",
                        help="viser aussi les titres de moins de 6 mots significatifs")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        plan = appliquer_garde_fous_existants(
            db, appliquer=args.apply, titres_courts=args.titres_courts
        )

    verbe = "plafonne(s)" if args.apply else "a plafonner"
    print(f"{len(plan)} post(s) Instagram quasi vide(s) {verbe} a {PLAFOND_POST_VIDE}")
    for c in plan:
        print(f"  - #{c.article_id} [{c.source}] rel {c.avant[0]} -> {c.apres[0]}, "
              f"hot {c.avant[1]} -> {c.apres[1]} : {c.titre[:70]}")
    if not args.apply and plan:
        print("Relancer avec --apply pour appliquer.")


if __name__ == "__main__":
    main()
