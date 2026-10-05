"""Reparer un faux positif de doublon d'archive -- ou confirmer un lien.

La procedure documentee jusqu'au 2026-09-21 (« effacer `duplicate_of_id` et
`file_exclu_raison` ») est FAUSSE : le passage suivant de `link_duplicates`
relie l'article a nouveau tant qu'il reste dans la fenetre de 48 h de sa vie
publiable -- ce que la re-revue de la Task 7 a prouve sur donnees reelles. Ce
script pose en plus un verdict MANUEL par PAIRE (`app/pipeline/
cluster_arbitrage.py`), qui prime desormais sur tout rapprochement automatique
ulterieur, y compris la bande "certaine" (`archive_similarity_from_tokens` >=
`cluster_archive_seuil`) : c'est ce qui rend la reparation reelle.

Deux usages :
- `--different` : le nouvel article n'est PAS un doublon de l'article publie.
  Le lien est defait (si l'article a garde le sien) et la paire ne sera plus
  jamais rapprochee, quel que soit le score futur.
- `--meme-actu` : confirme que deux articles sont bien la meme actu (utile
  pour figer une paire douteuse sans repayer l'arbitrage, ou pour re-lier une
  paire defaite par erreur).

Usage :
    uv run python -m scripts.reparations.marquer_doublon_manuel --article-a 123 --article-b 456 \\
        --different --raison "deux annonces distinctes, meme artiste"
    uv run python -m scripts.reparations.marquer_doublon_manuel --article-a 123 --article-b 456 \\
        --meme-actu --raison "meme sortie, deux sources"
"""

import argparse

from sqlalchemy.orm import Session

from app.db import SessionLocal, attendre_base
from app.models import Article
from app.pipeline.cluster import RAISON_DOUBLON_PREFIXE
from app.pipeline.cluster_arbitrage import marquer_manuel


def appliquer(
    db: Session,
    article_a_id: int,
    article_b_id: int,
    *,
    meme_actu: bool,
    raison: str,
) -> str:
    """Pose le verdict manuel et, si "different", defait le lien existant
    entre les deux articles (dans un sens ou dans l'autre). Rend un message
    lisible pour l'utilisateur du script."""
    article_a = db.get(Article, article_a_id)
    article_b = db.get(Article, article_b_id)
    if article_a is None or article_b is None:
        manquant = article_a_id if article_a is None else article_b_id
        raise SystemExit(f"article {manquant} introuvable")

    marquer_manuel(article_a_id, article_b_id, meme_actu, raison)

    message = f"verdict manuel enregistre : {article_a_id} / {article_b_id} -> "
    message += "meme actu" if meme_actu else "actus differentes"

    if not meme_actu:
        defait = []
        for article, id_article, id_autre in (
            (article_a, article_a_id, article_b_id),
            (article_b, article_b_id, article_a_id),
        ):
            if article.duplicate_of_id != id_autre:
                continue
            article.duplicate_of_id = None
            # N'efface QUE la raison posee par la dedup (meme filtre que
            # l'auto-reparation de `link_duplicates`) : une exclusion posee
            # par la publication ou la composition (« gel impossible : … »)
            # n'a rien a voir avec ce lien. L'effacer sans condition --
            # comportement jusqu'au 2026-09-21 -- remettait dans la file un
            # article ecarte pour une vraie raison.
            if (article.file_exclu_raison or "").startswith(RAISON_DOUBLON_PREFIXE):
                article.file_exclu_raison = None
            defait.append(id_article)
        if defait:
            db.commit()
            message += f" (lien defait sur {defait})"
    return message


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--article-a", type=int, required=True)
    parser.add_argument("--article-b", type=int, required=True)
    groupe = parser.add_mutually_exclusive_group(required=True)
    groupe.add_argument("--different", action="store_true", help="actus differentes, jamais reliees")
    groupe.add_argument("--meme-actu", action="store_true", help="confirme que c'est la meme actu")
    parser.add_argument("--raison", required=True, help="pourquoi -- garde la trace de la decision")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        try:
            message = appliquer(
                db,
                args.article_a,
                args.article_b,
                meme_actu=args.meme_actu,
                raison=args.raison,
            )
        except RuntimeError as exc:
            # `marquer_manuel` leve AVANT toute ecriture en base (voir
            # `appliquer`) : rien n'a ete delie ici, l'ordre le garantit.
            print(f"[ERREUR] verdict manuel non enregistre, rien touche en base : {exc}")
            raise SystemExit(1) from exc
    print(message)


if __name__ == "__main__":
    main()
