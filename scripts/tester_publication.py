"""Essai de bout en bout SANS publier : gel reel, tunnel reel, conteneur reel.

Rien n'est publie : le conteneur cree chez Meta n'est pas public et expire seul
en 24 h. C'est l'essai qui valide pour de vrai le format des images, le tunnel
et le jeton, avant toute publication. Pour un post `kinda_reel`, un reel est
assemble en MP4 -- video de l'API, sinon yt-dlp : l'essai consomme donc un
telechargement et partage la pause de l'agent -- et envoye en conteneur REELS.

Usage :
    uv run python -m scripts.tester_publication --post 12
"""

import argparse
import shutil

from app.config import settings
from app.content.legende import texte_legende
from app.db import BaseInjoignable, SessionLocal, attendre_base
from app.models import Article, Post
from app.publish.manifeste import normaliser
from app.publish.preparation import preparer_fichiers
from app.publish.preparation_reel import preparer_reel
from app.publish.publier import publier_fichiers, publier_reel
from app.publish.ytdlp import RecuperateurYtdlp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post", type=int, required=True)
    args = parser.parse_args()
    try:
        attendre_base()
    except BaseInjoignable as exc:
        raise SystemExit(f"[ERREUR] {exc}") from exc

    with SessionLocal() as db:
        post = db.get(Post, args.post)
        if post is None:
            raise SystemExit(f"post {args.post} introuvable")
        article = db.get(Article, post.article_id) if post.article_id else None
        dossier = settings.publication_dir / "essais" / str(post.id)
        legende = texte_legende(post)
        try:
            if post.template == "kinda_reel":
                recuperer = RecuperateurYtdlp() if settings.publication_reel_ytdlp else None
                figement = normaliser(preparer_reel(post, article, dossier, recuperer_ytdlp=recuperer))
                noms = figement.noms
                print(f"reel assemble dans {dossier} : {', '.join(noms)}")
                conteneur = publier_reel(dossier, noms, legende, publier_vraiment=False)
            else:
                figement = normaliser(preparer_fichiers(post, article, dossier))
                noms = figement.noms
                # Le manifeste du gel, affiche ici parce que c'est tout
                # l'interet d'un essai : voir ce qui part, et ce qui a ete
                # ECARTE avec sa raison -- jusqu'ici seulement journalise.
                for entree in figement.elements or []:
                    etat = "ecarte" if entree["ecarte"] else entree["fichier"]
                    raison = f" ({entree['raison']})" if entree["raison"] else ""
                    print(f"  {entree['kind']}/{entree['type']} {etat}{raison}")
                print(f"{len(noms)} image(s) figee(s) dans {dossier}")
                conteneur = publier_fichiers(dossier, noms, legende, publier_vraiment=False)
        finally:
            # Le menage de l'agent ne touche jamais `essais/` : sans ceci, chaque
            # essai de reel laisserait des dizaines de Mo de video sur le disque.
            shutil.rmtree(dossier, ignore_errors=True)
        print(f"conteneur {conteneur} FINISHED chez Meta -- rien n'a ete publie")


if __name__ == "__main__":
    main()
