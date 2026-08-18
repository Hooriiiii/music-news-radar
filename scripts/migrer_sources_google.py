"""Remplace les sources Google News par des équivalents à URL directe.

Pourquoi : un lien Google News ne mène pas à l'article mais à la page de
consentement de Google. Vérifié en vrai, y compris avec un navigateur complet et
le cookie de consentement — c'est irrésoluble. Conséquences : l'article ne peut
pas être illustré (pas d'og:image sur une page de consentement) et un post
citerait Google au lieu de l'éditeur.

Deux remplacements, de nature différente :
  - Mixmag est un ÉDITEUR : il retrouve son flux direct, que le passage au
    tout-local rend de nouveau accessible (Cloudflare ne renvoyait 403 que depuis
    les runners Azure).
  - les trois autres sont des RECHERCHES thématiques, sans équivalent direct :
    elles passent sur Bing, qui porte l'URL réelle dans son paramètre `url=`.

Usage :
    uv run python -m scripts.migrer_sources_google            # affiche le plan
    uv run python -m scripts.migrer_sources_google --apply
"""

import argparse
import urllib.parse as up

from app.db import SessionLocal, attendre_base
from app.models import Source

MIXMAG_DIRECT = "https://mixmag.net/rss.xml"


def bing_rss(requete: str) -> str:
    """Recherche d'actualité Bing en RSS, marché français."""
    return (
        "https://www.bing.com/news/search?"
        f"q={up.quote(requete)}&format=RSS&setmkt=fr-FR"
    )


# Bing ne comprend pas l'opérateur OR de Google : « A OR B » y rend ZÉRO
# résultat, ce qui éteindrait la source en silence. Les requêtes concernées sont
# donc réécrites à la main, et le nombre de résultats a été vérifié.
REECRITURES = {
    "festival musique écoresponsable OR musique climat": "festival musique écoresponsable",
}


def plan(db) -> list[tuple[Source, str, str]]:
    """(source, nouvelle_url, motif) pour chaque source à migrer."""
    a_migrer = []
    for source in db.query(Source).filter(Source.url.like("%news.google.com%")).all():
        requete = up.parse_qs(up.urlparse(source.url).query).get("q", [""])[0]
        if "site:mixmag.net" in requete:
            # Une source Mixmag directe existe peut-être déjà, désactivée à
            # l'époque où Cloudflare renvoyait 403 aux runners Azure. La migrer
            # sans regarder créerait un doublon qui interrogerait le même flux
            # deux fois par run.
            deja = (
                db.query(Source)
                .filter(Source.url == MIXMAG_DIRECT, Source.id != source.id)
                .first()
            )
            motif = "flux direct de l'éditeur"
            if deja is not None:
                motif += f" — doublon de « {deja.name} » (id={deja.id}), qui sera désactivée"
            a_migrer.append((source, MIXMAG_DIRECT, motif))
        elif requete:
            reecrite = REECRITURES.get(requete, requete)
            suffixe = f" (réécrite depuis « {requete} »)" if reecrite != requete else ""
            a_migrer.append((source, bing_rss(reecrite), f"recherche Bing : {reecrite}{suffixe}"))
    return a_migrer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        a_migrer = plan(db)
        if not a_migrer:
            print("Aucune source Google News à migrer.")
            return

        for source, url, motif in a_migrer:
            print(f"  {source.name}")
            print(f"    {motif}")
            print(f"    -> {url}")

        if not args.apply:
            print(f"\n{len(a_migrer)} source(s). Relancer avec --apply pour appliquer.")
            return

        for source, url, _ in a_migrer:
            # Désactiver l'éventuel doublon AVANT de poser la nouvelle URL.
            for autre in db.query(Source).filter(Source.url == url, Source.id != source.id):
                autre.active = False
            source.url = url
            # Le nom porte « Google News » : il deviendrait faux.
            source.name = source.name.replace("(via Google News)", "(RSS)")
            source.name = source.name.replace("(Google News)", "(Bing News)")
            # `state` peut porter un curseur propre à l'ancien flux.
            source.state = None
        db.commit()
        print(f"\n{len(a_migrer)} source(s) migrée(s).")


if __name__ == "__main__":
    main()
