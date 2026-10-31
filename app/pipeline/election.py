"""Le representant d'un groupe de doublons : l'article le plus informatif.

`link_duplicates` rattache toujours au plus ancien, ce qui garde la liaison
stable. Mais le plus ancien n'est pas le meilleur : le 2026-09-17, « GTA VI full
soundtrack announced featuring Fred again.. » (DJ Mag) etait masque derriere un
Mixmag plus vague, et l'actu n'a jamais atteint la file.

La cle d'information ne lit que le TITRE et la SOURCE, jamais le resume : le
scoring reecrit le resume, une cle qui le lirait ferait osciller le representant
d'un run a l'autre.

Cout borne : au plus `cluster_reevaluations_max_par_run` reelections et remises a
noter par run, chacune au plus un appel Claude.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.visuels import origine_interdite
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, ScoredBy, SourceCategorie
from app.pipeline import events
from app.pipeline.cluster import (
    DEFAULT_WINDOW_HOURS,
    raison_de_doublon,
    significant_tokens,
)
from app.pipeline.comptes_artistes import IndexComptes
from app.pipeline.retrait_humain import reporter_sur_le_representant, retrait_humain
from app.sources.instagram import TITRE_PAR_DEFAUT

CATEGORIES_PRIMAIRES = frozenset(
    {SourceCategorie.ARTISTE, SourceCategorie.FESTIVAL, SourceCategorie.LABEL, SourceCategorie.CLUB}
)


def _utc(moment: dt.datetime | None) -> dt.datetime | None:
    # SQLite rend des datetime naifs.
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.timezone.utc)


def tailles_clusters(db: Session, ids: Iterable[int]) -> dict[int, int]:
    """Pour chaque article, le nombre de SOURCES distinctes de son groupe (lui
    compris). Deux posts du meme compte ne font pas une actu « couverte »."""
    ids = list(ids)
    if not ids:
        return {}
    sources: dict[int, set[int]] = {i: set() for i in ids}
    lignes = db.execute(
        select(Article.id, Article.source_id, Article.duplicate_of_id).where(
            or_(Article.id.in_(ids), Article.duplicate_of_id.in_(ids))
        )
    )
    for article_id, source_id, representant in lignes:
        cle = representant if representant in sources else article_id
        if cle in sources:
            sources[cle].add(source_id)
    return {i: max(1, len(s)) for i, s in sources.items()}


def cle_information(article: Article, index: IndexComptes) -> tuple:
    """La plus grande gagne. Ordre total : artistes suivis cites dans le titre,
    source primaire, visuels autorises, mots significatifs du titre, anciennete,
    id."""
    source = article.source
    vu = _utc(article.published_at or article.fetched_at)
    return (
        len(index.cites(textes=[article.title])),
        source is not None and source.categorie in CATEGORIES_PRIMAIRES,
        not origine_interdite(source),
        len(significant_tokens(article.title)),
        -vu.timestamp() if vu is not None else 0.0,
        -article.id,
    )


def articles_engages(db: Session, ids: Iterable[int | None]) -> set[int]:
    """Parmi `ids`, ceux qui sont ENGAGES : publies (`USED`), composes (un
    `Post`), ou portes par un `Creneau` non `LIBRE`.

    **Le point unique ou cette regle est ecrite**, et c'est deliberement une
    fonction ENSEMBLISTE (trois requetes, quel que soit le nombre d'ids) :
    `retention.purger_evenements_sans_valeur` doit juger des centaines
    d'articles en un passage, et une copie du predicat la-bas deciderait de la
    suppression DEFINITIVE d'un evenement. Le jour ou les deux copies
    divergeraient, ce n'est pas un comportement bizarre qu'on obtiendrait :
    c'est l'archive editoriale d'une actu publiee qui disparait en silence.

    Un id `None` (article jamais flushe) est ignore, jamais engage.
    """
    connus = {identifiant for identifiant in ids if identifiant is not None}
    if not connus:
        return set()
    engages = set(
        db.scalars(
            select(Article.id).where(Article.id.in_(connus), Article.status == ArticleStatus.USED)
        )
    )
    engages.update(db.scalars(select(Post.article_id).where(Post.article_id.in_(connus))))
    engages.update(
        db.scalars(
            select(Creneau.article_id).where(
                Creneau.article_id.in_(connus), Creneau.etat != EtatCreneau.LIBRE
            )
        )
    )
    return engages


def representant_engage(db: Session, article: Article) -> bool:
    """Publie, compose, ou porte par un creneau : il ne change plus.

    Le cas `USED` est tranche SUR L'OBJET avant toute requete : c'est un
    raccourci (il ne peut jamais contredire `articles_engages`, qui le couvre
    aussi) et surtout la seule lecture qui vaut quand le statut vient d'etre
    change dans la session sans avoir ete flushe -- `SessionLocal` tourne en
    `autoflush=False`. Le reste de la regle n'existe qu'une fois, dans
    `articles_engages` ; `tests/pipeline/test_retention_evenements.py` verrouille
    l'accord des deux chemins sur les trois formes d'engagement.
    """
    if article.status == ArticleStatus.USED:
        return True
    return article.id in articles_engages(db, (article.id,))


def remettre_a_noter(article: Article) -> None:
    """Le prochain `score_pending` le reprend. Le resume est garde : la caption
    d'origine est deja perdue, c'est tout ce qui reste a juger."""
    article.relevance_score = None
    article.hotness_score = None
    article.emergence_score = None
    article.category = None
    article.imprint = None
    article.mentioned_artists = None
    article.scored_by = None


@dataclass
class ElectionStats:
    reelus: int = 0
    a_rescorer: int = 0
    verrouilles: int = 0
    # (ancien representant, nouveau), dans l'ordre de traitement.
    changements: list[tuple[int, int]] = field(default_factory=list)


def elire_representants(
    db: Session,
    index: IndexComptes,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    limite: int | None = None,
    maintenant: dt.datetime | None = None,
    appliquer: bool = True,
) -> ElectionStats:
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    limite = settings.cluster_reevaluations_max_par_run if limite is None else limite
    depuis = maintenant - dt.timedelta(hours=window_hours)
    vu = func.coalesce(Article.published_at, Article.fetched_at)
    representants = sorted(
        set(
            db.scalars(
                select(Article.duplicate_of_id).where(Article.duplicate_of_id.is_not(None), vu >= depuis)
            )
        )
    )
    stats = ElectionStats()

    def budget() -> bool:
        # Verifie avant CHAQUE action comptee : la borne n'est jamais depassee.
        return stats.reelus + stats.a_rescorer < limite

    for representant_id in representants:
        if not budget():
            break
        actuel = db.get(Article, representant_id)
        if actuel is None or actuel.duplicate_of_id is not None:
            continue
        doublons = db.scalars(
            select(Article).where(Article.duplicate_of_id == representant_id).order_by(Article.id)
        ).all()
        membres = [actuel, *doublons]
        meilleur = max(membres, key=lambda a: cle_information(a, index))
        # Retiree a la main, l'actu ne change pas de tete : promouvoir un doublon la
        # republierait, et le ferait noter pour rien. Meme traitement qu'un representant
        # engage : ni reelection, ni remise a noter, donc aucun appel Claude. Le verrou
        # n'existe que par la colonne : « Remettre dans la file » le leve en l'effacant.
        retraits = [membre.file_exclu_raison for membre in membres if retrait_humain(membre.file_exclu_raison)]
        if retraits and appliquer:
            reporter_sur_le_representant(actuel, retraits[0])
        engage = bool(retraits) or representant_engage(db, actuel)
        if meilleur.id != actuel.id:
            if engage:
                stats.verrouilles += 1
                meilleur = actuel
            else:
                stats.reelus += 1
                stats.changements.append((actuel.id, meilleur.id))
                if appliquer:
                    meilleur.duplicate_of_id = None
                    # Un doublon promu representant ne garde pas la trace
                    # d'exclusion que la dedup lui avait posee (Phase 2B,
                    # Task 3 : un lien frais tranche par Claude en pose une) --
                    # sinon l'elu sortirait de la file de publication pour une
                    # raison qui n'a plus cours. Une raison posee AILLEURS
                    # (« gel impossible : ... ») n'est jamais effacee : meme
                    # filtre que l'auto-reparation d'un verdict manuel.
                    if raison_de_doublon(meilleur.file_exclu_raison):
                        meilleur.file_exclu_raison = None
                    for membre in membres:
                        if membre.id != meilleur.id:
                            membre.duplicate_of_id = meilleur.id
                    # Phase 2A : le titre de l'evenement suit son representant
                    # elu. Ne cree ni ne fusionne jamais rien -- l'evenement du
                    # groupe existe deja (`events.attacher`, appele depuis
                    # `cluster.lier`), il ne bouge pas quand le representant
                    # change, seul son titre affiche est mis a jour.
                    events.retitrer(db, meilleur)
        sources = len({membre.source_id for membre in membres})
        # Un representant engage garde ses notes : les effacer le ferait passer sous
        # le seuil de la file, et un creneau programme ou epingle perdrait son article.
        if (
            meilleur.scored_by == ScoredBy.LOCAL
            and sources >= settings.cluster_sources_claude
            and meilleur.title != TITRE_PAR_DEFAUT
            and budget()
            and not (engage if meilleur.id == actuel.id else representant_engage(db, meilleur))
        ):
            stats.a_rescorer += 1
            if appliquer:
                remettre_a_noter(meilleur)
        if appliquer:
            db.commit()
    return stats
