"""Rattrape l'attribution des citations deja en base, et le cliquet qu'elle a fausse.

POURQUOI. `record_mentions` recopiait la note d'emergence de l'ARTICLE sur
CHAQUE artiste cite (voir l'en-tete de `app/pipeline/artists.py`). Les 13 310
citations deja archivees portent donc `principal = NULL` et
`Artist.max_emergence` a pu etre pose par une citation de passage -- « Dance
Up-And-Comer of the Month: Tatyana Jane ... Gotten Lessons From Skrillex » a
mis Skrillex a 85.

CE QUE CE SCRIPT PEUT, ET CE QU'IL NE PEUT PAS. Le RANG de citation n'existe
que sur l'article, et la purge a 2 jours l'a efface pour la quasi-totalite de
l'archive (mesure du 2026-10-03 : 12 676 citations sur 13 310 n'ont plus
d'article). Pour celles-la, seul le TITRE -- denormalise, donc intact -- peut
encore promouvoir une citation. Une citation qui n'est ni en tete d'une liste
qu'on ne peut plus lire, ni nommee dans son titre, reste donc INCONNUE
(`principal = NULL`, lu comme principal partout) plutot que degradee a tort :
la regle du doute du depot. On ne degrade QUE ce qu'on sait secondaire.

    uv run python -m scripts.recalculer_attribution_artistes            # le plan
    uv run python -m scripts.recalculer_attribution_artistes --apply    # ecrit

Idempotent, aucun appel paye, aucun reseau hors Postgres.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from sqlalchemy import func, select

from app.db import SessionLocal, attendre_base
from app.models import Article, Artist, ArtistMention
from app.pipeline.artists import nomme_dans, est_citation_principale
from app.pipeline.cluster import normalize_title


def _rangs(db) -> dict[int, dict[str, int]]:
    """{article_id: {nom en minuscules: rang}} pour les articles encore en base.

    Les articles purges n'y sont pas : leur rang est perdu pour toujours.
    """
    rangs: dict[int, dict[str, int]] = {}
    for article_id, noms in db.execute(
        select(Article.id, Article.mentioned_artists).where(Article.mentioned_artists.is_not(None))
    ):
        rangs[article_id] = {
            (nom or "").strip().lower(): rang for rang, nom in enumerate(noms or [])
        }
    return rangs


def plan(db) -> dict:
    """Ce que le recalcul changerait, sans rien ecrire."""
    rangs = _rangs(db)
    citations = db.scalars(select(ArtistMention).order_by(ArtistMention.id)).all()
    artistes = {a.id: a for a in db.scalars(select(Artist))}

    a_poser: list[tuple[ArtistMention, bool | None]] = []
    # Le cliquet recalcule : la meilleure note parmi les citations qu'on sait
    # principales OU dont on ignore le rang. Jamais une note de passage averee.
    maxima: dict[int, int | None] = defaultdict(lambda: None)
    secondaires = 0
    rang_perdu = 0
    for citation in citations:
        noms = rangs.get(citation.article_id) if citation.article_id is not None else None
        nom_brut = artistes[citation.artist_id].name or ""
        nom = nom_brut.strip().lower()
        titre = f" {normalize_title(citation.title or '')} "
        rang = noms.get(nom) if noms else None
        autres = [autre for autre in (noms or {}) if autre != nom]
        if noms is None:
            # L'ARTICLE EST PURGE : ni le rang ni la liste des autres cites
            # n'existent plus, donc deux des trois regles de
            # `est_citation_principale` sont muettes. Seul le titre, denormalise
            # sur la citation, peut encore promouvoir. Ne PAS appeler la
            # fonction avec `autres_noms` vide : sa troisieme regle (« personne
            # d'autre n'est nomme dans le titre ») rendrait alors True sur une
            # liste vide, c'est-a-dire une connaissance qu'on n'a pas.
            rang_perdu += 1
            principal: bool | None = True if nomme_dans(nom_brut, titre) else None
        elif est_citation_principale(nom_brut, citation.title, rang, autres):
            principal = True
        else:
            principal = False
            secondaires += 1
        if principal != citation.principal:
            a_poser.append((citation, principal))
        if principal is not False and citation.emergence_score is not None:
            courant = maxima[citation.artist_id]
            maxima[citation.artist_id] = max(courant or 0, citation.emergence_score)

    cliquets = [
        (artiste, maxima[artiste_id])
        for artiste_id, artiste in artistes.items()
        if maxima[artiste_id] != artiste.max_emergence
    ]
    return {
        "citations": len(citations),
        "rang_perdu": rang_perdu,
        "secondaires": secondaires,
        "a_poser": a_poser,
        "cliquets": cliquets,
    }


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--apply", action="store_true", help="ecrit les changements")
    parseur.add_argument("--exemples", type=int, default=15, help="citations a detailler")
    args = parseur.parse_args()

    attendre_base()
    db = SessionLocal()
    try:
        p = plan(db)
        print(f"{p['citations']} citations archivees")
        print(f"  dont {p['rang_perdu']} sans article (rang de citation perdu par la purge)")
        print(f"  {p['secondaires']} citations SECONDAIRES averees")
        print(f"  {len(p['a_poser'])} valeurs de `principal` a poser")
        print(f"  {len(p['cliquets'])} fiches artistes dont `max_emergence` change")
        if p["secondaires"]:
            print("\nexemples de citations degradees :")
            vus = 0
            for citation, principal in p["a_poser"]:
                if principal is not False:
                    continue
                print(f"  - em={citation.emergence_score} | {citation.title[:90]}")
                vus += 1
                if vus >= args.exemples:
                    break
        if p["cliquets"]:
            print("\nexemples de cliquets corriges :")
            pires = sorted(
                p["cliquets"],
                key=lambda c: -((c[0].max_emergence or 0) - (c[1] or 0)),
            )
            for artiste, nouveau in pires[: args.exemples]:
                print(f"  - {artiste.name}: {artiste.max_emergence} -> {nouveau}")
        if not args.apply:
            print("\n(plan seulement -- relancer avec --apply pour ecrire)")
            return
        for citation, principal in p["a_poser"]:
            citation.principal = principal
        for artiste, nouveau in p["cliquets"]:
            artiste.max_emergence = nouveau
        db.commit()
        # Relecture de controle : une ecriture non confirmee est une ecriture
        # qui n'a pas eu lieu.
        restants = db.scalar(
            select(func.count(ArtistMention.id)).where(ArtistMention.principal.is_(False))
        )
        print(f"\necrit. {restants} citations marquees secondaires en base.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
