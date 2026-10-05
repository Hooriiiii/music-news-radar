"""Sort l'etat reel du systeme, formate pour la page Notion KINDA NEWS FRANCE.

L'utilisateur a demande que l'espace Notion soit tenu a jour regulierement. Le
bloc de chiffres de la page d'accueil est ce qui vieillit le plus vite et le
plus SILENCIEUSEMENT : une doc qui date est pire que pas de doc, parce qu'on la
croit. Ce script le regenere en une commande, pour qu'aucun chiffre ne soit
jamais recopie de memoire.

    uv run python -m scripts.diagnostic.etat_notion

Lecture seule : aucune ecriture en base, aucun appel reseau hors Postgres.

Piege a connaitre, paye une fois : les statuts sont des enums Python, pas des
chaines. Comparer `Article.status == "used"` rend ZERO sans erreur -- le compte
parait juste et il est faux. On agrege donc par `group_by` et on lit les enums,
plutot que de tester une egalite qui peut mentir en silence.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    Article,
    ArticleStatus,
    Artist,
    ArtistMention,
    Creneau,
    EtatCreneau,
    Event,
    EventObservation,
    Post,
    PostStatus,
    Source,
    SourceCategorie,
    SourceType,
)


def _compter_par(db, colonne, modele) -> dict:
    """Agrege par une colonne d'enum et rend {valeur: n}, zeros compris."""
    return dict(db.execute(select(colonne, func.count(modele.id)).group_by(colonne)).all())


def collecter(db) -> dict:
    """Rend les chiffres de l'etat du systeme. Aucune ecriture."""
    un = lambda requete: db.scalar(requete) or 0  # noqa: E731

    types = _compter_par(db, Source.type, Source)
    types_actifs = dict(
        db.execute(
            select(Source.type, func.count(Source.id))
            .where(Source.active.is_(True))
            .group_by(Source.type)
        ).all()
    )
    statuts_posts = _compter_par(db, Post.status, Post)
    statuts_articles = _compter_par(db, Article.status, Article)
    etats_creneaux = _compter_par(db, Creneau.etat, Creneau)

    return {
        "sources_actives": un(select(func.count(Source.id)).where(Source.active.is_(True))),
        "sources_total": un(select(func.count(Source.id))),
        "sources_par_type": {t: types_actifs.get(t, 0) for t in types},
        "articles": un(select(func.count(Article.id))),
        "articles_used": statuts_articles.get(ArticleStatus.USED, 0),
        "posts": un(select(func.count(Post.id))),
        "posts_publies": statuts_posts.get(PostStatus.PUBLISHED, 0),
        "creneaux_publies": etats_creneaux.get(EtatCreneau.PUBLIE, 0),
        "creneaux_manques": etats_creneaux.get(EtatCreneau.MANQUE, 0),
        "creneaux_programmes": etats_creneaux.get(EtatCreneau.PROGRAMME, 0),
        "artistes": un(select(func.count(Artist.id))),
        # CE QUE LE PIPELINE APPELLE « ARTISTE SUIVI », et ce qu'il n'appelle
        # pas ainsi. L'exception au pre-filtre (`scoring.ContexteScoring`) et la
        # reserve de visuels (`content/choix_visuels.py`) lisent toutes deux
        # `comptes_artistes.IndexComptes`, c'est-a-dire les SOURCES Instagram
        # actives de categorie artiste. Elles ne lisent JAMAIS `Artist.tracked`,
        # qui est ecrit par `PATCH /artists/{id}` et lu par personne. Ce script
        # affichait « 0 artiste suivi sur 4 684 : les chemins qui en dependent
        # ne se declenchent jamais » -- une alerte FAUSSE, qui comptait
        # `tracked` : 88 comptes etaient en place et ces chemins tournaient.
        "comptes_artistes": un(
            select(func.count(Source.id)).where(
                Source.type == SourceType.INSTAGRAM,
                Source.categorie == SourceCategorie.ARTISTE,
                Source.active.is_(True),
            )
        ),
        # Les fiches `artists` rattachees a l'un de ces comptes
        # (`lier_artistes_comptes`) : ce qui permet de reconnaitre un artiste
        # deja suivi quand son nom seul ne suffit pas.
        "artistes_lies": un(select(func.count(Artist.id)).where(Artist.source_instagram_id.is_not(None))),
        "citations": un(select(func.count(ArtistMention.id))),
        "events": un(select(func.count(Event.id))),
        "observations": un(select(func.count(EventObservation.id))),
        "articles_observes": un(select(func.count(func.distinct(EventObservation.article_id)))),
    }


def _detail_sources(par_type: dict) -> str:
    noms = {
        SourceType.INSTAGRAM: "Instagram",
        SourceType.RSS: "RSS",
        SourceType.REDDIT_RSS: "Reddit",
    }
    parts = [
        f"{n} {noms.get(t, getattr(t, 'value', t))}"
        for t, n in sorted(par_type.items(), key=lambda kv: -kv[1])
        if n
    ]
    return ", ".join(parts)


def formater(etat: dict) -> str:
    """Rend le tableau en Markdown, pret a coller dans la page Notion."""
    couverture = (
        100 * etat["articles_observes"] / etat["articles"] if etat["articles"] else 0
    )
    lignes = [
        ("sources actives", f"**{etat['sources_actives']}** ({_detail_sources(etat['sources_par_type'])})"),
        ("articles en base", f"{etat['articles']} *(retention : 2 jours)*"),
        ("posts fabriques", f"{etat['posts']} — dont **{etat['posts_publies']} publies**"),
        (
            "creneaux",
            f"{etat['creneaux_publies']} publies · **{etat['creneaux_manques']} manques** "
            f"· {etat['creneaux_programmes']} programmes",
        ),
        ("artistes en memoire", f"**{etat['artistes']}**"),
        (
            "comptes d'artistes suivis",
            f"**{etat['comptes_artistes']}** comptes Instagram actifs "
            f"— {etat['artistes_lies']} fiches artistes rattachees",
        ),
        ("citations archivees", f"**{etat['citations']}**"),
        (
            "evenements",
            f"{etat['events']}, pour {etat['observations']} observations "
            f"({couverture:.0f} % des articles)",
        ),
    ]
    sortie = ["| indicateur | au JJ/MM/AAAA |", "|---|---|"]
    sortie += [f"| {gauche} | {droite} |" for gauche, droite in lignes]

    alertes = []
    total_creneaux = etat["creneaux_publies"] + etat["creneaux_manques"]
    if total_creneaux and etat["creneaux_manques"] / total_creneaux > 0.2:
        part = 100 * etat["creneaux_manques"] / total_creneaux
        alertes.append(f"- {part:.0f} % des creneaux aboutis sont MANQUES ({etat['creneaux_manques']}).")
    if couverture < 95:
        alertes.append(f"- {100 - couverture:.0f} % des articles n'appartiennent a AUCUN evenement.")
    # L'alerte ne se declenche que sur la VRAIE condition de panne : plus aucun
    # compte d'artiste suivi. C'est ce que lisent l'exception au pre-filtre et la
    # reserve de visuels. La version precedente comptait `Artist.tracked`, que
    # personne ne lit, et criait donc en permanence sur un systeme en bonne
    # sante.
    if not etat["comptes_artistes"]:
        alertes.append(
            "- 0 compte d'artiste suivi (source Instagram active, categorie artiste) : "
            "l'exception au pre-filtre et la reserve de visuels ne se declenchent jamais."
        )
    if alertes:
        sortie += ["", "A signaler :", *alertes]
    return "\n".join(sortie)


def main() -> None:
    db = SessionLocal()
    try:
        print(formater(collecter(db)))
    finally:
        db.close()


if __name__ == "__main__":
    main()
