"""Ajoute le socle de sources FRANÇAISES de Kinda (idempotent, par URL).

Kinda est un compte FR national : la couverture de la scène française ne peut pas
reposer sur la seule presse anglophone. Les trois premières sources sont les médias
électro FR de référence (Electro News, Guettapen, Hands Up) -- ce sont les
concurrents directs, et c'est précisément pour ça qu'ils sont utiles en veille :
ils sortent les annonces d'événements FR avant tout le monde. Kinda ne les reposte
pas, il repère l'actu et remonte à la source.

Volontairement ÉCARTÉS : les flux généralistes de la presse régionale
(ouest-france.fr/rss-en-continu.xml, letelegramme.fr/rss.xml). Ils fonctionnent mais
mélangent tout : ~95 % des items sont hors musique et chacun coûterait un appel de
scoring. Les requêtes Google News ciblées ci-dessous rendent le même service pour la
maille locale, sans le bruit.

Usage :
    uv run python -m scripts.seed_fr_sources            # affiche le plan
    uv run python -m scripts.seed_fr_sources --apply    # écrit en base
"""

import argparse

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Source, SourceType

# (nom, type, url, genre)
# Le genre pilote le radar maison : seules les sources en electronic/pop/new_music/
# music alimentent la recherche d'artistes (cf. settings.radar_source_genres). Les
# sources multi-genres reçoivent donc un genre HORS de cette liste, exprès.
FR_SOURCES: list[tuple[str, str, str, str]] = [
    # -- médias électro FR : le socle national --
    ("Electro News", "rss", "https://electro-news.eu/feed/", "electronic"),
    ("Guettapen", "rss", "https://www.guettapen.com/feed/", "electronic"),
    ("Hands Up Electro", "rss", "https://handsupelectro.fr/feed/", "electronic"),
    # -- culture club / société : angles écolo, féminisme, nuit (hors radar) --
    ("Manifesto XXI", "rss", "https://manifesto-21.com/feed", "culture"),
    # -- actu musicale FR généraliste (multi-genres -> hors radar) --
    ("FIP Actualites", "rss", "https://www.radiofrance.fr/fip/rss", "music_fr"),
    # -- maille locale et thématique via Google News --
    (
        "Google News - festivals electro FR",
        "rss",
        "https://news.google.com/rss/search?q=festival+%C3%A9lectro+France+line-up"
        "&hl=fr&gl=FR&ceid=FR:fr",
        "festivals",
    ),
    (
        "Google News - clubs et soirees FR",
        "rss",
        "https://news.google.com/rss/search?q=%22musique+%C3%A9lectronique%22+club"
        "+OR+festival+France&hl=fr&gl=FR&ceid=FR:fr",
        "festivals",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed des sources FR de Kinda")
    parser.add_argument("--apply", action="store_true", help="écrit réellement en base")
    args = parser.parse_args()

    with SessionLocal() as db:
        existing_urls = set(db.scalars(select(Source.url)))
        to_add = [s for s in FR_SOURCES if s[2] not in existing_urls]
        for name, _type, url, genre in FR_SOURCES:
            mark = "=" if url in existing_urls else "+"
            print(f"  {mark} {name:38s} [{genre}] {url}")
        if not args.apply:
            print(f"\n{len(to_add)} source(s) à ajouter. Relancer avec --apply pour écrire.")
            return

        for name, type_, url, genre in to_add:
            db.add(Source(name=name, type=SourceType(type_), url=url, genre=genre))
        db.commit()
        print(f"\n{len(to_add)} source(s) ajoutée(s).")


if __name__ == "__main__":
    main()
