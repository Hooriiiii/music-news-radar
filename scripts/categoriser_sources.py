"""Classer les sources existantes : artiste, media, festival, label, club.

Le scoring priorise les posts des comptes d'ARTISTES (actu primaire) et note plus
bas ceux des MEDIAS (les reposter serait du plagiat : ils ne servent que de filet
de securite pour les tres grosses actus). Il lui faut donc savoir qui produit
chaque source -- colonne `sources.categorie` (migration 0016).

Regles, dans l'ordre :
- Instagram : le compte est cherche dans les ensembles explicites ci-dessous ;
  tout autre compte Instagram est un artiste.
- RSS et Reddit : media (presse, agregateurs, forums).
- X : laisse a NULL (inconnu), sources eteintes et melangees (UGC, radar).

Le mapping EXPLICITE fait foi, y compris contre une valeur deja posee. Les regles
par DEFAUT (artiste, media) ne remplissent qu'une categorie vide : un classement
manuel fait depuis le dashboard n'est jamais ecrase par une supposition.

Usage :
    uv run python -m scripts.categoriser_sources           # affiche le plan
    uv run python -m scripts.categoriser_sources --apply   # ecrit
"""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, attendre_base
from app.models import Source, SourceCategorie, SourceType

MEDIAS = frozenset({
    "tsugimag", "mixmag", "electronewsfr", "guettapen", "djmagofficial", "popcrave",
    "konbini", "booska_p", "alltimeedm", "billboard", "billboardcharts", "proximity",
    "fipradio", "boilerroomtv", "cerclemusic", "aim_uk",
})
FESTIVALS = frozenset({
    "ultra", "nuits_sonores", "astropolis", "dourfestival", "transmusicales",
    "tomorrowland", "awakenings", "sonarfestival", "positiveeducation", "marsatac",
    "solidays", "welovegreen", "rockenseine", "peacocksociety",
})
# Keinemusik n'y est PAS : c'est un collectif d'artistes, donc `artiste`.
LABELS = frozenset({"drumcoderecords", "edbanger", "kompromatmusic"})
CLUBS = frozenset({"rexclub", "heat_lyon"})

EXPLICITES = (
    (MEDIAS, SourceCategorie.MEDIA),
    (FESTIVALS, SourceCategorie.FESTIVAL),
    (LABELS, SourceCategorie.LABEL),
    (CLUBS, SourceCategorie.CLUB),
)


def compte_instagram(url: str) -> str:
    """Meme lecture que l'adapter : `ig:nom` ou URL instagram.com complete."""
    url = url.strip().rstrip("/")
    if url.lower().startswith("ig:"):
        compte = url[3:]
    else:
        compte = url.rsplit("/", 1)[-1]
    return compte.lstrip("@").strip().lower()


def _explicite(type_: SourceType, url: str) -> SourceCategorie | None:
    if type_ != SourceType.INSTAGRAM:
        return None
    compte = compte_instagram(url)
    for ensemble, categorie in EXPLICITES:
        if compte in ensemble:
            return categorie
    return None


def _par_defaut(type_: SourceType) -> SourceCategorie | None:
    if type_ == SourceType.INSTAGRAM:
        return SourceCategorie.ARTISTE
    if type_ in (SourceType.RSS, SourceType.REDDIT_RSS):
        return SourceCategorie.MEDIA
    return None


def categorie_attendue(type_: SourceType, url: str) -> SourceCategorie | None:
    return _explicite(type_, url) or _par_defaut(type_)


def categoriser_sources(
    db: Session, *, appliquer: bool = False
) -> list[tuple[str, SourceCategorie | None, SourceCategorie]]:
    """Rend le plan (nom, avant, apres) et l'ecrit si `appliquer`. Idempotent."""
    plan = []
    for source in db.scalars(select(Source).order_by(Source.name)).all():
        explicite = _explicite(source.type, source.url)
        if explicite is not None:
            cible = explicite
        elif source.categorie is None:
            cible = _par_defaut(source.type)
        else:
            continue
        if cible is None or cible == source.categorie:
            continue
        plan.append((source.name, source.categorie, cible))
        if appliquer:
            source.categorie = cible
    if appliquer:
        db.commit()
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="ecrire pour de bon")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        plan = categoriser_sources(db, appliquer=args.apply)

    verbe = "classees" if args.apply else "a classer"
    print(f"{len(plan)} source(s) {verbe}")
    for nom, avant, apres in plan:
        avant_txt = avant.value if avant else "-"
        print(f"  - {nom} : {avant_txt} -> {apres.value}")
    if not args.apply and plan:
        print("\nRelancer avec --apply pour appliquer.")


if __name__ == "__main__":
    main()
