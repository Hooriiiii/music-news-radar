"""Un passage de l'agent de publication, toutes les 10 minutes.

Dans l'ordre :
1. les creneaux depasses de plus de 90 minutes passent en MANQUE -- sans ce
   garde-fou, un Mac rouvert le soir publierait la journee entiere d'un coup ;
2. publication des creneaux DEJA geles dont l'heure est venue : une panne des
   etapes suivantes ne doit jamais bloquer un post pret ;
3. l'arbitrage reattribue les creneaux libres ;
4. composition (appel Claude paye) des creneaux a moins de 24 h ;
5. gel des creneaux a moins de 2 h : rendu et telechargements sur disque ;
6. publication des creneaux geles pendant ce passage (Mac reveille en retard).

Chaque etape est isolee : une exception inattendue est consignee et notifiee, et
le passage continue.

Un article en defaut (composition ou gel impossible) est EXCLU de la file et son
creneau libere. Une panne systemique (credits, reseau, disque) n'exclut rien : la
faute n'est pas a l'article.

Le dashboard modifie la file PENDANT un passage, et la session de l'agent garde
ses objets en memoire (expire_on_commit=False). D'ou deux regles : relire le
creneau avant chaque etape qui le modifie, et n'ecrire le resultat d'une etape
longue (composition, gel, reclamation) que par un UPDATE conditionnel sur l'etat,
l'article et le post attendus. Si le dashboard est passe entre-temps, l'agent
abandonne le creneau pour ce passage ; un post compose pour rien reste un
brouillon, repris par `post_a_reprendre`.

Aucune double publication : le creneau est RECLAME (GELE -> ECHEC, UPDATE
conditionnel committe) AVANT l'appel a Meta, et on ne publie que si la
reclamation a touche une ligne. Un crash entre la publication et la confirmation
laisse donc un ECHEC, jamais retente -- mieux vaut un post manque qu'un post
publie deux fois.
"""

import datetime as dt
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import InvalidRequestError

from app.config import settings
from app.content.compose import CompositionError, create_post
from app.content.legende import texte_legende
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, PostStatus
from app.publish.preparation import PreparationImpossible
from app.schedule.store import appliquer_arbitrage, post_a_reprendre, utc

logger = logging.getLogger(__name__)

MODES = ("arret", "apercu", "reel")


@dataclass
class RapportPublication:
    composes: int = 0
    geles: int = 0
    publies: int = 0
    apercus: int = 0
    manques: int = 0
    echecs: int = 0
    erreurs: list[str] = field(default_factory=list)


def _maintenant_reel() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _heure_locale(moment: dt.datetime) -> str:
    return utc(moment).astimezone(ZoneInfo(settings.publication_fuseau)).strftime("%d/%m %H:%M")


def _prevenir(notifier, message: str) -> None:
    # Une notification en panne ne doit pas interrompre le passage.
    try:
        notifier("Kinda", message)
    except Exception:  # noqa: BLE001
        logger.exception("notification impossible : %s", message)


def _dossier(creneau: Creneau) -> Path:
    return settings.publication_dir / str(creneau.id)


def _effacer_dossier(creneau: Creneau) -> None:
    # Best effort : un dossier qui resiste ne vaut pas d'interrompre le passage.
    shutil.rmtree(_dossier(creneau), ignore_errors=True)


def _relire(db, creneau: Creneau) -> bool:
    """Recharge le creneau depuis la base. Faux s'il a disparu entre-temps."""
    try:
        db.refresh(creneau)
    except InvalidRequestError:
        return False
    return True


def _attendu(creneau: Creneau) -> tuple:
    return creneau.etat, creneau.article_id, creneau.post_id


def _reclamer(db, creneau: Creneau, attendu: tuple, **valeurs) -> bool:
    """UPDATE conditionnel, NON committe : vrai seulement si le creneau est encore
    dans l'etat attendu (etat, article, post). Sinon rien n'est ecrit."""
    etat, article_id, post_id = attendu
    resultat = db.execute(
        update(Creneau)
        .where(
            Creneau.id == creneau.id,
            Creneau.etat == etat,
            Creneau.article_id.is_(None) if article_id is None else Creneau.article_id == article_id,
            Creneau.post_id.is_(None) if post_id is None else Creneau.post_id == post_id,
        )
        .values(**valeurs)
        .execution_options(synchronize_session=False)
    )
    if resultat.rowcount != 1:
        db.rollback()
        _relire(db, creneau)
        return False
    return True


def _ecrire_si(db, creneau: Creneau, attendu: tuple, **valeurs) -> bool:
    if not _reclamer(db, creneau, attendu, **valeurs):
        return False
    db.commit()
    _relire(db, creneau)
    return True


def _liberer(db, creneau: Creneau, attendu: tuple, raison: str) -> bool:
    # Desepingle aussi : un creneau vide epingle resterait vide jusqu'a son heure.
    return _ecrire_si(
        db, creneau, attendu,
        article_id=None, post_id=None, etat=EtatCreneau.LIBRE, epingle=False, erreur=raison[:1000],
    )


def _deja_publie(article: Article | None, post: Post | None) -> bool:
    # Publie a la main depuis le dashboard pendant que l'article attendait son creneau.
    return (article is not None and article.status == ArticleStatus.USED) or (
        post is not None and post.status == PostStatus.PUBLISHED
    )


def _exclure(db, creneau: Creneau, attendu: tuple, raison: str, rapport: RapportPublication) -> None:
    # L'article est en defaut quoi qu'ait fait le dashboard entre-temps ; le
    # creneau, lui, n'est libere que s'il le porte encore.
    article = db.get(Article, attendu[1], populate_existing=True) if attendu[1] else None
    if article is not None:
        article.file_exclu_raison = raison[:500]
        db.commit()
    rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : {raison}")
    _liberer(db, creneau, attendu, raison)


def _marquer_manques(db, maintenant: dt.datetime, rapport: RapportPublication) -> None:
    limite = maintenant - dt.timedelta(minutes=settings.publication_retard_max_minutes)
    en_retard = db.scalars(
        select(Creneau).where(
            Creneau.publier_a < limite,
            Creneau.etat.in_([EtatCreneau.LIBRE, EtatCreneau.PROGRAMME, EtatCreneau.GELE]),
        )
    ).all()
    for creneau in en_retard:
        if not _relire(db, creneau):
            continue
        attendu = _attendu(creneau)
        if _ecrire_si(db, creneau, attendu, etat=EtatCreneau.MANQUE):
            if attendu[1] is not None:
                rapport.manques += 1
            _effacer_dossier(creneau)


def _composer(db, maintenant: dt.datetime, composer, notifier, rapport: RapportPublication) -> bool:
    horizon = maintenant + dt.timedelta(hours=settings.publication_composition_heures)
    creneaux = db.scalars(
        select(Creneau)
        .where(
            Creneau.etat == EtatCreneau.PROGRAMME,
            Creneau.post_id.is_(None),
            Creneau.article_id.is_not(None),
            Creneau.publier_a <= horizon,
        )
        .order_by(Creneau.publier_a)
    ).all()
    exclusions = False
    for creneau in creneaux:
        # La liste date du debut de l'etape ; une composition prend des secondes.
        if not _relire(db, creneau) or creneau.etat != EtatCreneau.PROGRAMME:
            continue
        if creneau.post_id is not None or creneau.article_id is None:
            continue
        attendu = _attendu(creneau)
        article = db.get(Article, creneau.article_id, populate_existing=True)
        if article is None:
            _liberer(db, creneau, attendu, "article purge")
            continue
        if _deja_publie(article, None):
            _liberer(db, creneau, attendu, "article deja publie")
            continue
        post_id = post_a_reprendre(db, article.id)
        if post_id is None:
            try:
                draft = composer(article)
            except CompositionError as exc:
                _exclure(db, creneau, attendu, f"composition impossible : {exc}", rapport)
                exclusions = True
                continue
            except Exception as exc:  # noqa: BLE001 -- credits, reseau : panne systemique
                erreur = f"composition interrompue : {exc}"[:1000]
                rapport.erreurs.append(erreur)
                _ecrire_si(db, creneau, attendu, erreur=erreur)
                _prevenir(notifier, erreur)
                # Inutile d'enchainer les appels sur une panne qui touchera tous les articles.
                break
            post_id = create_post(db, article, draft).id
            rapport.composes += 1
        # Si le dashboard a change le creneau pendant la composition, son choix
        # prime : le post reste un brouillon, repris plus tard si l'article revient.
        _ecrire_si(db, creneau, attendu, post_id=post_id, erreur=None)
    return exclusions


def _geler(db, maintenant: dt.datetime, preparer, notifier, rapport: RapportPublication) -> bool:
    seuil = maintenant + dt.timedelta(hours=settings.publication_gel_heures)
    creneaux = db.scalars(
        select(Creneau)
        .where(
            Creneau.etat == EtatCreneau.PROGRAMME,
            Creneau.post_id.is_not(None),
            Creneau.publier_a <= seuil,
        )
        .order_by(Creneau.publier_a)
    ).all()
    exclusions = False
    for creneau in creneaux:
        if not _relire(db, creneau) or creneau.etat != EtatCreneau.PROGRAMME or creneau.post_id is None:
            continue
        attendu = _attendu(creneau)
        post = db.get(Post, creneau.post_id, populate_existing=True)
        if post is None:
            _liberer(db, creneau, attendu, "post supprime")
            continue
        if post.article_id != creneau.article_id:
            # Pas d'exclusion : l'article n'y est pour rien, c'est la file qui a bouge.
            erreur = "gel refuse : le post ne correspond pas a l'article du creneau"
            rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : {erreur}")
            _ecrire_si(db, creneau, attendu, erreur=erreur)
            continue
        article = db.get(Article, post.article_id, populate_existing=True) if post.article_id else None
        if _deja_publie(article, post):
            _liberer(db, creneau, attendu, "article deja publie")
            continue
        try:
            noms = preparer(post, article, _dossier(creneau))
        except PreparationImpossible as exc:
            _exclure(db, creneau, attendu, f"gel impossible : {exc}", rapport)
            exclusions = True
            continue
        except Exception as exc:  # noqa: BLE001 -- rendu, disque, reseau : panne systemique
            # Le creneau reste PROGRAMME : retente au passage suivant, tant que le
            # delai de retard le permet.
            erreur = f"gel interrompu : {exc}"[:1000]
            rapport.erreurs.append(erreur)
            _ecrire_si(db, creneau, attendu, erreur=erreur)
            _prevenir(notifier, erreur)
            break
        if _ecrire_si(
            db, creneau, attendu,
            fichiers=noms, etat=EtatCreneau.GELE, gele_le=maintenant, erreur=None,
        ):
            rapport.geles += 1
    return exclusions


def _publier(
    db, maintenant: dt.datetime, mode: str, publier, notifier, rapport: RapportPublication, horloge
) -> None:
    creneaux = db.scalars(
        select(Creneau)
        .where(Creneau.etat == EtatCreneau.GELE, Creneau.publier_a <= maintenant)
        .order_by(Creneau.publier_a)
    ).all()
    for creneau in creneaux:
        if not _relire(db, creneau) or creneau.etat != EtatCreneau.GELE:
            continue
        attendu = _attendu(creneau)
        post = db.get(Post, creneau.post_id, populate_existing=True) if creneau.post_id else None
        article = db.get(Article, post.article_id, populate_existing=True) if post and post.article_id else None
        if _deja_publie(article, post):
            if _liberer(db, creneau, attendu, "article deja publie"):
                _effacer_dossier(creneau)
            continue

        # Horloge FRAICHE : les publications precedentes de ce passage ont pu
        # prendre des minutes chacune (tunnel, attente du conteneur chez Meta).
        retard = horloge() - utc(creneau.publier_a)
        if retard > dt.timedelta(minutes=settings.publication_retard_max_minutes):
            if _ecrire_si(db, creneau, attendu, etat=EtatCreneau.MANQUE):
                rapport.manques += 1
                _effacer_dossier(creneau)
            continue

        if post is None or post.article_id != creneau.article_id:
            # Pas d'exclusion : l'article n'y est pour rien.
            refus = "post supprime apres le gel" if post is None else (
                "publication refusee : le post ne correspond pas a l'article du creneau"
            )
            if _ecrire_si(db, creneau, attendu, etat=EtatCreneau.ECHEC, erreur=refus):
                rapport.echecs += 1
                _effacer_dossier(creneau)
                _prevenir(notifier, f"publication de {_heure_locale(creneau.publier_a)} en echec : {refus}")
            continue

        # Reclamation committee AVANT l'appel : si le processus meurt pendant ou
        # juste apres la publication, le creneau reste en ECHEC et n'est jamais
        # retente. L'article est exclu de meme, sinon il repartirait sur un autre
        # creneau. Zero ligne touchee : un autre passage (ou le dashboard) est passe.
        interruption = "publication interrompue avant confirmation"
        if not _reclamer(db, creneau, attendu, etat=EtatCreneau.ECHEC, erreur=interruption):
            continue
        if article is not None:
            article.file_exclu_raison = interruption
        db.commit()
        _relire(db, creneau)

        try:
            identifiant = publier(
                _dossier(creneau),
                creneau.fichiers or [],
                texte_legende(post),
                mode == "reel",
            )
        except Exception as exc:  # noqa: BLE001 -- tout echec doit se voir
            creneau.etat = EtatCreneau.ECHEC
            creneau.erreur = str(exc)[:1000]
            if article is not None:
                # Un article refuse par Meta ne revient pas dans la file.
                article.file_exclu_raison = f"echec de publication : {exc}"[:500]
            db.commit()
            _effacer_dossier(creneau)
            rapport.echecs += 1
            _prevenir(notifier, f"publication de {_heure_locale(creneau.publier_a)} en echec : {exc}")
            continue
        creneau.ig_media_id = identifiant
        creneau.erreur = None
        if article is not None:
            article.file_exclu_raison = None
        if mode == "reel":
            creneau.etat = EtatCreneau.PUBLIE
            creneau.publie_le = maintenant
            post.status = PostStatus.PUBLISHED
            post.published_at = maintenant
            if article is not None:
                # USED protege l'article de la purge et l'ecarte de la file.
                article.status = ArticleStatus.USED
            rapport.publies += 1
        else:
            # Pas de publie_le : rien n'est parti. L'etat APERCU suffit a faire
            # avancer la file (voir candidats_eligibles).
            creneau.etat = EtatCreneau.APERCU
            rapport.apercus += 1
        db.commit()
        _effacer_dossier(creneau)


def tourner(
    db, maintenant: dt.datetime, *, mode: str, composer, preparer, publier, notifier, horloge=None
) -> RapportPublication:
    if mode not in MODES:
        raise ValueError(f"mode de publication inconnu : {mode!r}, attendu {MODES}")
    rapport = RapportPublication()
    if mode == "arret":
        return rapport
    horloge = horloge or _maintenant_reel

    def etape(nom: str, fonction):
        # Une etape en panne ne doit pas emporter les suivantes, et surtout pas
        # la publication d'un creneau deja gele.
        try:
            # Relire la base a chaque etape : la precedente a pu durer des minutes
            # (publication), et le dashboard a pu epingler ou remplacer entre-temps.
            db.expire_all()
            return fonction()
        except Exception as exc:  # noqa: BLE001
            logger.exception("etape %s en panne", nom)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                logger.exception("rollback impossible apres l'etape %s", nom)
            message = f"etape {nom} en panne : {exc}"[:1000]
            rapport.erreurs.append(message)
            _prevenir(notifier, message)
            return None

    def publication():
        _publier(db, maintenant, mode, publier, notifier, rapport, horloge)

    etape("retards", lambda: _marquer_manques(db, maintenant, rapport))
    etape("publication", publication)
    etape("arbitrage", lambda: appliquer_arbitrage(db, maintenant))
    exclusions = bool(etape("composition", lambda: _composer(db, maintenant, composer, notifier, rapport)))
    exclusions = bool(etape("gel", lambda: _geler(db, maintenant, preparer, notifier, rapport))) or exclusions
    if exclusions:
        # Le creneau libere se remplit tout de suite ; sa composition viendra au
        # passage suivant.
        etape("arbitrage", lambda: appliquer_arbitrage(db, maintenant))
    etape("publication", publication)
    if rapport.manques:
        _prevenir(
            notifier,
            f"{rapport.manques} publication(s) manquee(s) : plus de "
            f"{settings.publication_retard_max_minutes} minutes de retard (Mac en veille ?)",
        )
    return rapport
