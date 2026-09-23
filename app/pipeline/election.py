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
from app.pipeline.cluster import DEFAULT_WINDOW_HOURS, significant_tokens
from app.pipeline.comptes_artistes import IndexComptes
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


def representant_engage(db: Session, article: Article) -> bool:
    """Publie, compose, ou porte par un creneau : il ne change plus."""
    if article.status == ArticleStatus.USED:
        return True
    if db.scalar(select(Post.id).where(Post.article_id == article.id).limit(1)) is not None:
        return True
    return (
        db.scalar(
            select(Creneau.id)
            .where(Creneau.article_id == article.id, Creneau.etat != EtatCreneau.LIBRE)
            .limit(1)
        )
        is not None
    )


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
        engage = representant_engage(db, actuel)
        if meilleur.id != actuel.id:
            if engage:
                stats.verrouilles += 1
                meilleur = actuel
            else:
                stats.reelus += 1
                stats.changements.append((actuel.id, meilleur.id))
                if appliquer:
                    meilleur.duplicate_of_id = None
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
