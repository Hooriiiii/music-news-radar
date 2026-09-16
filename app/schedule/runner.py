"""Un passage de l'agent de publication, toutes les 10 minutes.

Dans l'ordre :
1. les creneaux depasses de plus de 90 minutes passent en MANQUE -- sans ce
   garde-fou, un Mac rouvert le soir publierait la journee entiere d'un coup ;
2. l'arbitrage reattribue les creneaux libres ;
3. composition (appel Claude paye) des creneaux a moins de 24 h ;
4. gel des creneaux a moins de 2 h : rendu et telechargements sur disque ;
5. publication des creneaux geles dont l'heure est venue.

Un article en defaut (composition ou gel impossible) est EXCLU de la file et son
creneau libere. Une panne systemique (credits, reseau, disque) n'exclut rien : la
faute n'est pas a l'article.

Aucune double publication : le creneau est committe en ECHEC AVANT l'appel a
Meta. Un crash entre la publication et la confirmation laisse donc un ECHEC, jamais
retente -- mieux vaut un post manque qu'un post publie deux fois.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from sqlalchemy import select

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


def _heure_locale(moment: dt.datetime) -> str:
    return utc(moment).astimezone(ZoneInfo(settings.publication_fuseau)).strftime("%d/%m %H:%M")


def _prevenir(notifier, message: str) -> None:
    # Une notification en panne ne doit pas interrompre le passage.
    try:
        notifier("Kinda", message)
    except Exception:  # noqa: BLE001
        logger.exception("notification impossible : %s", message)


def _liberer(creneau: Creneau, raison: str) -> None:
    creneau.article_id = None
    creneau.post_id = None
    creneau.etat = EtatCreneau.LIBRE
    creneau.erreur = raison[:1000]


def _exclure(db, creneau: Creneau, raison: str, rapport: RapportPublication) -> None:
    article = db.get(Article, creneau.article_id) if creneau.article_id else None
    if article is not None:
        article.file_exclu_raison = raison[:500]
    rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : {raison}")
    _liberer(creneau, raison)
    db.commit()


def _marquer_manques(db, maintenant: dt.datetime, rapport: RapportPublication) -> None:
    limite = maintenant - dt.timedelta(minutes=settings.publication_retard_max_minutes)
    en_retard = db.scalars(
        select(Creneau).where(
            Creneau.publier_a < limite,
            Creneau.etat.in_([EtatCreneau.LIBRE, EtatCreneau.PROGRAMME, EtatCreneau.GELE]),
        )
    ).all()
    for creneau in en_retard:
        if creneau.article_id is not None:
            rapport.manques += 1
        creneau.etat = EtatCreneau.MANQUE
    db.commit()


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
        article = db.get(Article, creneau.article_id)
        if article is None:
            _liberer(creneau, "article purge")
            db.commit()
            continue
        post_id = post_a_reprendre(db, article.id)
        if post_id is None:
            try:
                draft = composer(article)
            except CompositionError as exc:
                _exclure(db, creneau, f"composition impossible : {exc}", rapport)
                exclusions = True
                continue
            except Exception as exc:  # noqa: BLE001 -- credits, reseau : panne systemique
                creneau.erreur = f"composition interrompue : {exc}"[:1000]
                rapport.erreurs.append(creneau.erreur)
                db.commit()
                _prevenir(notifier, creneau.erreur)
                # Inutile d'enchainer les appels sur une panne qui touchera tous les articles.
                break
            post_id = create_post(db, article, draft).id
            rapport.composes += 1
        creneau.post_id = post_id
        creneau.erreur = None
        db.commit()
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
        post = db.get(Post, creneau.post_id)
        if post is None:
            _liberer(creneau, "post supprime")
            db.commit()
            continue
        article = db.get(Article, creneau.article_id) if creneau.article_id else None
        try:
            noms = preparer(post, article, settings.publication_dir / str(creneau.id))
        except PreparationImpossible as exc:
            _exclure(db, creneau, f"gel impossible : {exc}", rapport)
            exclusions = True
            continue
        except Exception as exc:  # noqa: BLE001 -- rendu, disque, reseau : panne systemique
            # Le creneau reste PROGRAMME : retente au passage suivant, tant que le
            # delai de retard le permet.
            creneau.erreur = f"gel interrompu : {exc}"[:1000]
            rapport.erreurs.append(creneau.erreur)
            db.commit()
            _prevenir(notifier, creneau.erreur)
            break
        creneau.fichiers = noms
        creneau.etat = EtatCreneau.GELE
        creneau.gele_le = maintenant
        creneau.erreur = None
        db.commit()
        rapport.geles += 1
    return exclusions


def _publier(db, maintenant: dt.datetime, mode: str, publier, notifier, rapport: RapportPublication) -> None:
    creneaux = db.scalars(
        select(Creneau)
        .where(Creneau.etat == EtatCreneau.GELE, Creneau.publier_a <= maintenant)
        .order_by(Creneau.publier_a)
    ).all()
    for creneau in creneaux:
        post = db.get(Post, creneau.post_id) if creneau.post_id else None
        if post is not None:
            # Committe AVANT l'appel : si le processus meurt pendant ou juste apres
            # la publication, le creneau reste en ECHEC et n'est jamais retente.
            # L'article est exclu de meme, sinon il repartirait sur un autre creneau.
            creneau.etat = EtatCreneau.ECHEC
            creneau.erreur = "publication interrompue avant confirmation"
            article = db.get(Article, post.article_id) if post.article_id else None
            if article is not None:
                article.file_exclu_raison = creneau.erreur
            db.commit()
        try:
            if post is None:
                raise RuntimeError("post supprime apres le gel")
            identifiant = publier(
                settings.publication_dir / str(creneau.id),
                creneau.fichiers or [],
                texte_legende(post),
                mode == "reel",
            )
        except Exception as exc:  # noqa: BLE001 -- tout echec doit se voir
            creneau.etat = EtatCreneau.ECHEC
            creneau.erreur = str(exc)[:1000]
            article = db.get(Article, post.article_id) if post is not None and post.article_id else None
            if article is not None:
                # Un article refuse par Meta ne revient pas dans la file.
                article.file_exclu_raison = f"echec de publication : {exc}"[:500]
            db.commit()
            rapport.echecs += 1
            _prevenir(notifier, f"publication de {_heure_locale(creneau.publier_a)} en echec : {exc}")
            continue
        creneau.ig_media_id = identifiant
        creneau.erreur = None
        article = db.get(Article, post.article_id) if post.article_id else None
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


def tourner(db, maintenant: dt.datetime, *, mode: str, composer, preparer, publier, notifier) -> RapportPublication:
    if mode not in MODES:
        raise ValueError(f"mode de publication inconnu : {mode!r}, attendu {MODES}")
    rapport = RapportPublication()
    if mode == "arret":
        return rapport
    _marquer_manques(db, maintenant, rapport)
    appliquer_arbitrage(db, maintenant)
    exclusions = _composer(db, maintenant, composer, notifier, rapport)
    exclusions = _geler(db, maintenant, preparer, notifier, rapport) or exclusions
    if exclusions:
        # Le creneau libere se remplit tout de suite ; sa composition viendra au
        # passage suivant.
        appliquer_arbitrage(db, maintenant)
    _publier(db, maintenant, mode, publier, notifier, rapport)
    return rapport
