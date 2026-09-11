"""Eteindre les sources X, devenues inutilisables.

La facturation developer.x.com n'est plus active : les 100 sources X renvoient
toutes 402 Payment Required, soit un millier d'erreurs par run. Le probleme n'est
pas le cout de ces appels (ils echouent avant d'etre factures) mais le BRUIT :
`diagnostiquer()` remonte 86 sources en erreur a chaque passage, ce qui rend la
supervision inutile au moment precis ou on s'apprete a lui confier la publication.

Reversible : on desactive, on ne supprime pas. Le `state` de chaque source garde
son since_id, donc rallumer la facturation suffirait a reprendre ou on s'est
arrete.
"""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, attendre_base
from app.models import Source, SourceType


def desactiver_sources_x(db: Session, *, appliquer: bool = False) -> list[str]:
    """Rend les noms des sources X encore actives, et les eteint si `appliquer`."""
    sources = db.scalars(
        select(Source).where(Source.type == SourceType.X, Source.active.is_(True))
    ).all()
    if appliquer:
        for source in sources:
            source.active = False
        db.commit()
    return [s.name for s in sources]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="eteindre pour de bon")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        noms = desactiver_sources_x(db, appliquer=args.apply)

    verbe = "desactivees" if args.apply else "a desactiver"
    print(f"{len(noms)} source(s) X {verbe}")
    for nom in noms[:10]:
        print(f"  - {nom}")
    if len(noms) > 10:
        print(f"  ... et {len(noms) - 10} autres")
    if not args.apply and noms:
        print("\nRelancer avec --apply pour appliquer.")


if __name__ == "__main__":
    main()
