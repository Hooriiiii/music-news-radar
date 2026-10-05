"""Remplit la duree des videos deja en base, lue dans leur propre URL.

Pourquoi : Graph ne rend aucune duree pour une video (`media_url` et
`thumbnail_url`, c'est tout), si bien que les 642 videos Instagram de la base
avaient `duration_ms` a None le 2026-09-21. Or `videos_exploitables` laisse
passer une duree INCONNUE -- choix assume : mieux vaut proposer le reel que
griser un bouton sur une donnee absente. Resultat ce jour-la : une video de
190 s est restee eligible, a fait composer un post (Claude paye), et n'a echoue
qu'au gel apres telechargement, en excluant un article Billboard qui n'avait
aucune video a lui.

La duree etait pourtant la depuis le debut : l'URL signee du CDN porte un
parametre `efg` en base64 dont le JSON contient `duration_s`. La lecture est
desormais faite a l'ingestion (`app.content.media.entree`). Ce script est le
RATTRAPAGE des lignes posees avant.

Idempotent : il n'ecrit que la ou la duree manque et ou l'URL la donne. Une
duree deja connue (sonde, API X) n'est JAMAIS ecrasee -- elle est mesuree, celle
de l'URL n'est que declarative.

Usage :
    uv run python -m scripts.reparations.remplir_durees_medias            # affiche le plan
    uv run python -m scripts.reparations.remplir_durees_medias --apply
"""

import argparse
from dataclasses import dataclass, field

from app.content.media import VIDEO, duree_depuis_url
from app.db import SessionLocal, attendre_base
from app.models import Article, Post
from app.config import settings


@dataclass
class Rapport:
    articles: int = 0
    posts: int = 0
    videos: int = 0
    hors_limite: int = 0
    exemples: list[str] = field(default_factory=list)


def _remplir_liste(valeurs) -> tuple[list, int, int]:
    """La liste avec ses durees remplies, le nombre de videos datees, et combien
    d'entre elles depassent la limite du reel.

    Rend une liste NEUVE : une colonne JSON ne persiste pas une mutation en place
    (regle du projet, cf. `sources.state`).
    """
    plafond_ms = int(settings.publication_reel_duree_max_s * 1000)
    neuves, datees, hors_limite = [], 0, 0
    for element in valeurs or []:
        # Un element d'un autre type ou une ligne mal formee est laissee telle
        # quelle : un script de maintenance ne doit pas tomber dessus.
        if not isinstance(element, dict) or element.get("type") != VIDEO:
            neuves.append(element)
            continue
        if element.get("duration_ms") is not None:
            neuves.append(element)
            continue
        duree = duree_depuis_url(element.get("url"))
        if duree is None:
            neuves.append(element)
            continue
        neuves.append({**element, "duration_ms": duree})
        datees += 1
        if duree > plafond_ms:
            hors_limite += 1
    return neuves, datees, hors_limite


def remplir_durees(db, *, appliquer: bool = False, limite_exemples: int = 5) -> Rapport:
    """Compte (et, avec `appliquer`, ecrit) les durees lisibles dans les URL.

    Deux endroits, les deux que lit la publication : `articles.media`, d'ou sort
    l'eligibilite au reel, et `posts.assets`, les actifs deja choisis -- un
    brouillon compose avant ce correctif porte la meme video sans sa duree.
    """
    rapport = Rapport()
    for article in db.query(Article).filter(Article.media.is_not(None)):
        media, datees, hors = _remplir_liste(article.media)
        if not datees:
            continue
        rapport.articles += 1
        rapport.videos += datees
        rapport.hors_limite += hors
        if len(rapport.exemples) < limite_exemples:
            suffixe = f", dont {hors} hors limite reel" if hors else ""
            rapport.exemples.append(f"article {article.id} : {datees} video(s){suffixe}")
        if appliquer:
            article.media = media

    for post in db.query(Post).filter(Post.assets.is_not(None)):
        assets, datees, hors = _remplir_liste(post.assets)
        if not datees:
            continue
        rapport.posts += 1
        rapport.videos += datees
        rapport.hors_limite += hors
        if len(rapport.exemples) < limite_exemples:
            rapport.exemples.append(f"post {post.id} : {datees} video(s)")
        if appliquer:
            post.assets = assets

    if appliquer:
        db.commit()
    return rapport


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        rapport = remplir_durees(db, appliquer=args.apply)
        if not rapport.videos:
            print("Aucune duree a remplir : rien a faire.")
            return
        for exemple in rapport.exemples:
            print(f"  {exemple}")
        resume = (
            f"{rapport.videos} video(s) datees dans {rapport.articles} article(s) "
            f"et {rapport.posts} post(s) ; {rapport.hors_limite} depassent la limite du reel"
        )
        if args.apply:
            print(f"\n{resume} : ecrites.")
        else:
            print(f"\n{resume}. Relancer avec --apply pour appliquer.")


if __name__ == "__main__":
    main()
