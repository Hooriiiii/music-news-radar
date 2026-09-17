"""Reevaluer les actus recentes enterrees par l'ancienne notation.

Motif : le 2026-09-17, la BO de GTA VI (Fred again..) a ete perdue -- pre-filtre
local a 15-25, decote des medias a 45-59, representant de doublon mal choisi.
Les nouvelles regles valent pour les articles a venir ; ce script rattrape les
jours recents.

Sans --apply : le PLAN seul, aucune ecriture, aucun appel Claude. Avec --apply :
1. election du representant le plus informatif sur la fenetre ;
2. notation des articles en attente (representants promus ou remis a noter) ;
3. re-notation par Claude, sans pre-filtre, des articles du plan.

Le plan, sur --jours jours (non doublons, non publies, non exclus, deja notes) :
- notes par le pre-filtre local et (groupe >= 3 sources ou artiste suivi cite) ;
- notes par Claude, compte media, relevance 40-59, et (artiste suivi cite ou
  relevance >= 50 ou groupe >= 2 sources) : les victimes probables de la decote,
  sans re-noter toute la presse.

Usage :
    uv run python -m scripts.reevaluer_actus                    # plan
    uv run python -m scripts.reevaluer_actus --apply [--jours 3] [--limite 100]
"""

import argparse
import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import SessionLocal, attendre_base
from app.models import Article, ArticleStatus, ScoredBy, SourceCategorie
from app.pipeline.comptes_artistes import construire_index
from app.pipeline.election import ElectionStats, elire_representants, tailles_clusters
from app.pipeline.scoring import apply_score, appliquer_garde_fous, make_claude_scorer, score_pending
from app.sources.instagram import TITRE_PAR_DEFAUT

RAISON_CLUSTER = "cluster"
RAISON_ARTISTE = "artiste_suivi"
RAISON_MEDIA = "media_decote"
NOTE_MEDIA_MIN = 40
NOTE_MEDIA_PROCHE = 50


@dataclass
class Reevaluation:
    article_id: int
    source: str
    titre: str
    avant: int
    raison: str
    artistes: int
    taille: int
    apres: int | None = None


def planifier(db: Session, maintenant: dt.datetime, *, jours: int = 3, limite: int = 100) -> list[Reevaluation]:
    depuis = maintenant - dt.timedelta(days=jours)
    articles = db.scalars(
        select(Article)
        .options(selectinload(Article.source))
        .where(
            Article.duplicate_of_id.is_(None),
            Article.status != ArticleStatus.USED,
            Article.file_exclu_raison.is_(None),
            Article.relevance_score.is_not(None),
            Article.title != TITRE_PAR_DEFAUT,
            func.coalesce(Article.published_at, Article.fetched_at) >= depuis,
        )
        .order_by(Article.id)
    ).all()
    index = construire_index(db)
    tailles = tailles_clusters(db, [a.id for a in articles])
    plan = []
    for article in articles:
        artistes = len(
            index.cites(
                textes=[article.title, article.summary],
                noms=article.mentioned_artists or [],
                exclure_source_id=article.source_id,
            )
        )
        taille = tailles.get(article.id, 1)
        source = article.source
        raison = None
        if article.scored_by == ScoredBy.LOCAL:
            if taille >= settings.cluster_sources_claude:
                raison = RAISON_CLUSTER
            elif artistes:
                raison = RAISON_ARTISTE
        elif (
            article.scored_by == ScoredBy.CLAUDE
            and source is not None
            and source.categorie == SourceCategorie.MEDIA
            and NOTE_MEDIA_MIN <= article.relevance_score < settings.publication_min_relevance
            and (artistes or article.relevance_score >= NOTE_MEDIA_PROCHE or taille >= 2)
        ):
            raison = RAISON_MEDIA
        if raison is not None:
            plan.append(Reevaluation(article.id, source.name if source else "?", article.title,
                                     article.relevance_score, raison, artistes, taille))
    plan.sort(key=lambda r: (-r.artistes, -r.taille, -r.avant, r.article_id))
    return plan[:limite]


def reevaluer(
    db: Session,
    maintenant: dt.datetime,
    *,
    jours: int = 3,
    limite: int = 100,
    appliquer: bool = False,
    scorer=None,
    noter_en_attente=score_pending,
) -> tuple[ElectionStats, list[Reevaluation]]:
    election = elire_representants(
        db, construire_index(db), window_hours=jours * 24, limite=limite,
        maintenant=maintenant, appliquer=appliquer,
    )
    # APRES l'election (les representants remis a noter sortent du plan : leur
    # note est nulle) et AVANT la notation des en-attente (un article ne doit pas
    # etre paye deux fois).
    plan = planifier(db, maintenant, jours=jours, limite=limite)
    if not appliquer:
        return election, plan
    scorer = scorer or make_claude_scorer()
    noter_en_attente(db, scorer=scorer)
    for reevaluation in plan:
        article = db.get(Article, reevaluation.article_id)
        try:
            apply_score(article, appliquer_garde_fous(article, scorer(article)))
            article.scored_by = ScoredBy.CLAUDE
            db.commit()
            reevaluation.apres = article.relevance_score
        except Exception as exc:  # noqa: BLE001 -- un echec n'arrete pas les autres
            db.rollback()
            print(f"  ! #{reevaluation.article_id} : {exc}")
    return election, plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jours", type=int, default=3)
    parser.add_argument("--limite", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    attendre_base()
    with SessionLocal() as db:
        election, plan = reevaluer(
            db, dt.datetime.now(dt.timezone.utc), jours=args.jours, limite=args.limite, appliquer=args.apply
        )
    print(f"Representants : {election.reelus} change(s), {election.verrouilles} verrouille(s), "
          f"{election.a_rescorer} remis a noter")
    for ancien, nouveau in election.changements:
        print(f"  - #{ancien} -> #{nouveau}")
    verbe = "re-note(s)" if args.apply else "a re-noter par Claude"
    print(f"{len(plan)} article(s) {verbe} (au plus {args.limite} appels)")
    for r in plan:
        apres = f" -> {r.apres}" if r.apres is not None else ""
        print(f"  - #{r.article_id} [{r.source}] {r.raison} rel {r.avant}{apres} "
              f"(artistes suivis {r.artistes}, sources {r.taille}) : {r.titre[:70]}")
    if not args.apply:
        print("\nPlan calcule AVANT election : les representants peuvent changer a l'application.")
        if plan or election.changements:
            print("Relancer avec --apply pour appliquer.")


if __name__ == "__main__":
    main()
