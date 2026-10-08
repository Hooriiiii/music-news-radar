import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, delete, func, not_, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, ArticleStatus, Event, EventObservation
from app.pipeline import events
from app.pipeline.election import articles_engages

logger = logging.getLogger(__name__)


def _cutoff(jours: int) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=jours)


def _trop_vieux(cutoff: dt.datetime) -> ColumnElement[bool]:
    """« Hors fenetre », par DATE DE POST, avec la date d'ajout pour seul filet."""
    return or_(
        Article.published_at < cutoff,
        (Article.published_at.is_(None)) & (Article.fetched_at < cutoff),
    )


def _jamais_juge() -> ColumnElement[bool]:
    """Un article sur lequel AUCUNE machine ne s'est prononcee.

    Definition MESUREE le 2026-10-08 sur les 452 articles sans note laisses en
    production par la panne de credits Anthropic du 2026-10-07 :

    | nature                                  | effectif |
    |-----------------------------------------|----------|
    | jamais juge (`duplicate_of_id IS NULL`) |   399    |
    | doublon (`duplicate_of_id` renseigne)   |    53    |

    La clause `duplicate_of_id IS NULL` n'est donc pas decorative : un doublon
    A ete juge -- par la dedup, qui a decide qu'il repetait une actu deja
    representee. Il n'a pas de note parce qu'il n'en a pas besoin, pas parce
    que la machine est tombee. Ces 53 lignes restent a `retention_days` ; leur
    donner le sursis ferait grossir la table pour rien.

    Un article note par le pre-filtre local (`scored_by='local'`) porte une
    `relevance_score` : il est juge, donc hors sursis. C'est voulu.
    """
    return and_(
        Article.relevance_score.is_(None),
        Article.duplicate_of_id.is_(None),
    )


def _predicat_purgeable(retention_days: int, sursis_days: int) -> ColumnElement[bool]:
    """Ce qu'est « un article que la purge va supprimer », en UN seul endroit.

    `purge_old_articles` et `purger_evenements_sans_valeur` doivent viser
    exactement le meme ensemble : la seconde decide du sort d'un evenement au
    vu de l'article que la premiere s'apprete a effacer. Deux copies de ce
    predicat qui divergeraient laisseraient soit des evenements sans valeur
    derriere elles, soit -- pire -- supprimeraient l'evenement d'un article qui
    reste en base. Le SURSIS vit donc ici, pas chez les appelants.

    Deux fenetres, pas une : un article que la machine n'a pas pu juger
    beneficie d'un sursis (`_jamais_juge`). `CLAUDE.md` : « une panne MACHINE
    n'exclut JAMAIS un article de la file -- reseau, tunnel injoignable,
    credits Anthropic epuises ». La regle etait tenue dans la file de
    publication (les creneaux passent en `manque`, jamais en `exclu`) et nulle
    part en amont : l'article n'etait pas exclu, il etait EFFACE a
    `retention_days`, exactement comme un article note et rejete.
    """
    # PLANCHER, jamais substitution. Les appelants peuvent passer un
    # `retention_days` plus long que le sursis (les tests passent 14 ; une
    # fenetre de production allongee ferait de meme) : si le sursis REMPLACAIT
    # la retention, un non-juge tomberait de 14 jours a 7, c'est-a-dire que le
    # sursis RACCOURCIRAIT la conservation -- l'exact inverse de son intention,
    # et silencieusement.
    jours_non_juge = max(retention_days, sursis_days)
    cutoff_juge = _cutoff(retention_days)
    cutoff_non_juge = _cutoff(jours_non_juge)
    return and_(
        Article.status != ArticleStatus.USED,
        or_(
            and_(_jamais_juge(), _trop_vieux(cutoff_non_juge)),
            and_(not_(_jamais_juge()), _trop_vieux(cutoff_juge)),
        ),
    )


def _sursis(sursis_days: int | None) -> int:
    return settings.retention_sursis_non_juges_days if sursis_days is None else sursis_days


def purge_old_articles(
    db: Session, retention_days: int, *, sursis_days: int | None = None
) -> int:
    """Supprime les articles trop vieux, par DATE DE PUBLICATION (pas d'ajout).

    Les articles sans date de publication n'ont pas de date de post exploitable :
    pour eux seulement, on retombe sur la date d'ajout comme filet, sinon ils
    s'accumuleraient indéfiniment.

    Exception : les articles marqués USED (= postés sur le compte) sont conservés
    indéfiniment. C'est l'archive éditoriale -- elle sert à ne pas reposter deux
    fois la même actu et à mesurer ce qui a été publié. Le statut REVIEWED n'est
    que du tri, il ne protège pas de la purge.

    Seconde exception : un article que la machine n'a PAS PU juger beneficie
    d'un sursis -- `sursis_days`, par defaut
    `settings.retention_sursis_non_juges_days` (7 jours). La definition de
    « jamais juge » et le PLANCHER `max(retention_days, sursis_days)` vivent
    dans `_predicat_purgeable`, partage avec
    `purger_evenements_sans_valeur`.
    """
    result = db.execute(
        delete(Article).where(_predicat_purgeable(retention_days, _sursis(sursis_days)))
    )
    db.commit()
    return result.rowcount


def _within_retention(published_at: dt.datetime | None, retention_days: int) -> bool:
    """Un article est ingérable si non daté (âge inconnu) ou publié dans la fenêtre."""
    if published_at is None:
        return True
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retention_days)
    return published_at >= cutoff


# --- Phase 2B, Task 2 : purger les evenements sans valeur -------------------


@dataclass
class PurgeEvenementsStats:
    """Ce qu'un passage a supprime cote evenements.

    `tables_absentes` distingue « migration 0019 non jouee » (fonctionnalite
    dormante, normale) de « rien a purger » (le regime de croisiere), sur le
    modele de `events.ObservationStats`."""

    evenements_examines: int = 0
    evenements_supprimes: int = 0
    observations_supprimees: int = 0
    tables_absentes: bool = False
    erreur: str | None = None


def purger_evenements_sans_valeur(
    db: Session,
    retention_days: int,
    *,
    seuil_pertinence: int | None = None,
    sursis_days: int | None = None,
) -> PurgeEvenementsStats:
    """Supprime les evenements qui n'ont jamais rien prouve, AVANT la purge des
    articles qui les portent.

    Depuis la Task 1, TOUT article devient l'observation d'un evenement, meme
    seul. Sans cette fonction c'est une fuite lente : un evenement permanent
    par article de bruit, et rien ne purge `events` aujourd'hui.

    La regle, telle que l'utilisateur l'a tranchee le 2026-09-24 :
    - UNE seule observation, article sous le seuil de pertinence et jamais
      publie -> l'evenement meurt avec son article ;
    - DEUX observations ou plus -> permanent, sans condition. Une actu
      corroboree a une histoire qui vaut au-dela de la vie de ses articles :
      ses observations survivent orphelines (`article_id` remis a NULL par la
      FK `ON DELETE SET NULL`), exactement comme `artist_mentions` ;
    - PUBLIE (`USED`, un `Post`, un `Creneau` engage) -> permanent, sans
      condition. C'est l'archive editoriale, meme motif que l'exception `USED`
      de `purge_old_articles`.

    **L'ORDRE est la propriete fragile de cette fonction, et c'est pour ca
    qu'elle vit ici.** `event_observations.article_id` est
    `ON DELETE SET NULL` : une fois l'article purge, l'observation survit sans
    lui et plus rien ne permet de juger sa pertinence ni son statut. Appelee
    APRES la purge des articles, cette fonction ne trouverait plus AUCUN
    candidat et la table grossirait quand meme, en silence. Elle doit donc
    tourner AVANT `purge_old_articles`, dans le meme passage -- l'ordre est
    pose dans `app/pipeline/run.py` et verrouille par un test
    (`tests/pipeline/test_retention_evenements.py`).

    Cout d'un passage A VIDE : la recherche des candidats part des ARTICLES que
    la purge va supprimer (meme predicat exactement, `_predicat_purgeable`, donc
    la meme fenetre indexee sur `articles.published_at` que le DELETE qui suit
    de toute facon), jamais de la table `events`, qui n'est bornee par rien et
    grossit indefiniment. Aucun article a purger = UNE requete qui rend zero
    ligne, et plus rien ensuite : ni comptage, ni lecture d'evenement, ni
    ecriture. Un passage CHARGE coute au plus quatre requetes de plus, quel que
    soit le nombre de candidats -- comptage des preuves, `Post`, `Creneau`, puis
    les deux DELETE : rien n'est fait article par article.

    **Ne leve jamais** (meme invariant que
    `source_groups.regrouper_sources_et_propager` et `events.observer_articles`) :
    une panne ici degrade la fonctionnalite pour ce passage, jamais le passage
    horaire, qui doit continuer sur la purge des articles et la supervision.
    """
    stats = PurgeEvenementsStats()
    try:
        if not events._tables_disponibles(db):
            stats.tables_absentes = True
            return stats

        seuil = (
            settings.digest_relevance_threshold if seuil_pertinence is None else seuil_pertinence
        )
        # OBLIGATOIRE : la production tourne en `autoflush=False` (`app/db.py`).
        # Le passage horaire vient d'ecrire des observations (`observer_articles`
        # committe, mais `record_mentions` ou une injection de test peuvent
        # laisser des ecritures en attente) ; sans ce flush, la requete ci-dessous,
        # qui est du SQL brut, ne les verrait pas.
        db.flush()

        candidats = db.execute(
            select(
                EventObservation.event_id,
                EventObservation.article_id,
                Article.relevance_score,
            )
            .join(Article, Article.id == EventObservation.article_id)
            .where(_predicat_purgeable(retention_days, _sursis(sursis_days)))
        ).all()
        if not candidats:
            return stats

        # Un article n'a qu'une observation (contrainte unique event/article, et
        # un seul evenement par article) : un dict suffit.
        par_evenement = {ligne.event_id: ligne for ligne in candidats}
        stats.evenements_examines = len(par_evenement)

        # UNE requete pour tous les candidats : combien de preuves porte chacun.
        preuves = dict(
            db.execute(
                select(EventObservation.event_id, func.count(EventObservation.id))
                .where(EventObservation.event_id.in_(par_evenement))
                .group_by(EventObservation.event_id)
            ).all()
        )

        # Restent en lice : une seule preuve, et un article sans interet mesure.
        # Une note ABSENTE (jamais scoree) vaut « sous le seuil » : rien ne
        # prouve la valeur de cet article, et le laisser proteger son evenement
        # ferait de tout article non note un evenement permanent.
        en_lice = {
            event_id: ligne
            for event_id, ligne in par_evenement.items()
            if preuves.get(event_id, 0) == 1 and (ligne.relevance_score or 0) < seuil
        }
        if not en_lice:
            return stats

        # « Publie » au sens d'`election.articles_engages` : `USED`, un `Post`,
        # ou un `Creneau` non LIBRE. La regle n'est PAS recopiee ici -- elle
        # decide d'une suppression definitive, et deux copies qui divergeraient
        # feraient disparaitre en silence l'archive d'une actu publiee. Version
        # ensembliste : trois requetes pour tout le lot, jamais deux par
        # candidat (le lot peut compter des centaines d'articles le jour ou la
        # fenetre de retention est raccourcie).
        engages = articles_engages(db, (ligne.article_id for ligne in en_lice.values()))

        a_supprimer = [
            event_id
            for event_id, ligne in en_lice.items()
            if ligne.article_id not in engages
        ]

        if not a_supprimer:
            return stats

        # `event_observations.event_id` n'a PAS d'`ON DELETE CASCADE`
        # (migration 0019) : les preuves partent explicitement avant leur
        # evenement, sinon la suppression viole la contrainte.
        supprimees = db.execute(
            delete(EventObservation).where(EventObservation.event_id.in_(a_supprimer))
        )
        db.execute(delete(Event).where(Event.id.in_(a_supprimer)))
        db.commit()
        stats.evenements_supprimes = len(a_supprimer)
        stats.observations_supprimees = supprimees.rowcount
    except Exception as exc:  # noqa: BLE001 -- ne doit jamais interrompre le passage horaire
        logger.exception("Purge des evenements sans valeur impossible")
        stats.erreur = str(exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("Rollback impossible apres l'echec de la purge des evenements")
    return stats
