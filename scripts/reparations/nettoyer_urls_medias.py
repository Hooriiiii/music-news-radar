"""Desechappe les entites HTML des URL de medias deja en base.

Pourquoi : une URL qui traverse du HTML (un `<img src>` de flux RSS, un
`<meta og:image>`) en ressort avec `&amp;` a la place de `&`. Telle quelle, une
URL signee du CDN Instagram (`_nc_cat`, `ccb`, `oh`, `oe`) est refusee : le reel
du 2026-09-17 20:24 (post 29, article 17974) est parti sur le FOND DE SECOURS du
gabarit, Chromium ayant recu une erreur et `fetch_image_data_uri` etant retombe
en silence sur `fallback.jpg`.

Le nettoyage est desormais fait a l'ingestion (`app.content.media.entree`) et
re-verifie a l'usage (rendu, export, gel). Ce script est le RATTRAPAGE des lignes
posees avant : sans lui, un post deja compose repartirait avec son `&amp;`.

Une seule passe suffit : idempotent, il ne touche que ce qui change.

Usage :
    uv run python -m scripts.reparations.nettoyer_urls_medias            # affiche le plan
    uv run python -m scripts.reparations.nettoyer_urls_medias --apply
"""

import argparse
from dataclasses import dataclass

from app.content.urls import url_propre
from app.db import SessionLocal, attendre_base
from app.models import Article, Post

# Les cles qui portent une URL, par endroit. `image_url` est le fond d'une slide
# de cover, `fichier` est deliberement absent : c'est un `file://` local, pose par
# le cache des visuels, qui n'a jamais traverse de HTML.
CLES_MEDIA = ("url", "preview_url")
CLES_SLIDE = ("url", "preview_url", "image_url")


@dataclass
class Rapport:
    articles: int = 0
    posts: int = 0
    urls: int = 0
    exemples: list[str] = None

    def __post_init__(self):
        self.exemples = []


def _nettoyer_liste(valeurs, cles: tuple[str, ...]) -> tuple[list, int]:
    """La liste avec ses URL desechappees, et le nombre d'URL corrigees.

    Rend une liste NEUVE : les colonnes JSON ne persistent pas une mutation en
    place (regle du projet, cf. `sources.state`).
    """
    neuves, corrigees = [], 0
    for element in valeurs or []:
        if not isinstance(element, dict):
            neuves.append(element)
            continue
        neuf = dict(element)
        for cle in cles:
            valeur = neuf.get(cle)
            # Un champ absent, a None ou d'un autre type est laisse tel quel : un
            # script de maintenance ne doit pas tomber sur une ligne mal formee.
            if not isinstance(valeur, str):
                continue
            propre = url_propre(valeur)
            if propre and propre != valeur:
                neuf[cle] = propre
                corrigees += 1
        neuves.append(neuf)
    return neuves, corrigees


def nettoyer_urls(db, *, appliquer: bool = False, limite_exemples: int = 5) -> Rapport:
    """Compte (et, avec `appliquer`, corrige) les URL echappees des medias.

    Trois endroits : `articles.media`, `posts.slides` et `posts.assets` -- les
    trois nourrissent la publication (fond de cover, slides du carrousel, copies
    d'actifs).
    """
    rapport = Rapport()
    for article in db.query(Article).filter(Article.media.is_not(None)):
        media, corrigees = _nettoyer_liste(article.media, CLES_MEDIA)
        if not corrigees:
            continue
        rapport.articles += 1
        rapport.urls += corrigees
        if len(rapport.exemples) < limite_exemples:
            rapport.exemples.append(f"article {article.id} : {corrigees} URL")
        if appliquer:
            article.media = media

    for post in db.query(Post):
        slides, c_slides = _nettoyer_liste(post.slides, CLES_SLIDE)
        assets, c_assets = _nettoyer_liste(post.assets, CLES_MEDIA)
        corrigees = c_slides + c_assets
        if not corrigees:
            continue
        rapport.posts += 1
        rapport.urls += corrigees
        if len(rapport.exemples) < limite_exemples:
            rapport.exemples.append(f"post {post.id} : {corrigees} URL")
        if appliquer:
            if c_slides:
                post.slides = slides
            if c_assets:
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
        rapport = nettoyer_urls(db, appliquer=args.apply)
        if not rapport.urls:
            print("Aucune URL echappee : rien a faire.")
            return
        for exemple in rapport.exemples:
            print(f"  {exemple}")
        resume = (
            f"{rapport.urls} URL dans {rapport.articles} article(s) "
            f"et {rapport.posts} post(s)"
        )
        if args.apply:
            print(f"\n{resume} : nettoyees.")
        else:
            print(f"\n{resume}. Relancer avec --apply pour appliquer.")


if __name__ == "__main__":
    main()
