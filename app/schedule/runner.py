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

Publication A L'HEURE : un creneau gele a moins de `publication_avance_s` de son
heure est reclame des ce passage, son conteneur cree, puis l'agent dort jusqu'a
l'heure affichee avant `media_publish` (mode relu juste avant).

Chaque etape est isolee : une exception inattendue est consignee et notifiee, et
le passage continue.

Deux formats, carrousel et reel, suivent le meme cycle. Seuls changent le
gabarit compose, le preparateur du gel et le publieur, choisis selon
`creneau.format`. Une panne systemique ne suspend que son format : ffmpeg
absent ne bloque pas le gel d'un carrousel. Une ATTENTE (PreparationDifferee :
pause ou budget de yt-dlp) n'est ni une faute ni une panne : une raison sur le
creneau, sans exclusion ni notification.

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

Aucune transaction ouverte pendant l'attente de l'heure ni pendant l'appel a
Meta : Neon coupe une session restee « idle in transaction », et c'est
l'ecriture d'APRES publication qui tombait (voir `_clore_transaction` et
`_ecrire_apres_publication`).
"""

import datetime as dt
import logging
import shutil
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import InterfaceError, InvalidRequestError, OperationalError, PendingRollbackError

from app.config import settings
from app.content.choix_visuels import (
    AucunVisuel,
    VisuelIntrouvable,
    VisuelsDifferes,
    choisir_visuels,
    compte_du_visuel,
)
from app.content.compose import CompositionError, appeler_compositeur, create_post, resoudre_fond
from app.content.credit import credit_video
from app.content.legende import texte_legende
from app.content.reperes import Repere, repere_perime, repere_relatif
from app.content.visuels import (
    FOND_KINDA,
    fond_de_cover,
    image_de_fond,
    origine_interdite,
    purger_cache,
)
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau, Post, PostStatus
from app.pipeline import credits_anthropic
from app.publish.hosting import HebergementIndisponible
from app.publish.manifeste import normaliser as normaliser_figement
from app.publish.preparation import PreparationDifferee, PreparationImpossible, PreparationSautee
from app.publish.preparation_reel import ReelInadapte
from app.schedule.publication_incertaine import PREFIXE_ECHEC_PUBLICATION, RAISON_RECLAMATION
from app.schedule.pause_visuels import marquer_saute, reinitialiser_report, signaler_report
from app.schedule.relecture import (
    RAISON_RELECTURE,
    deja_annonce,
    marquer_annonce,
    porte_une_correction,
    reference_du_texte,
)
from app.schedule.store import (
    TEMPLATES,
    actifs_interdits,
    appliquer_arbitrage,
    post_a_reprendre,
    raison_ineligible_reel,
    utc,
)

logger = logging.getLogger(__name__)

MODES = ("arret", "apercu", "reel")
# Duree pendant laquelle `RAISON_RECLAMATION` (publication_incertaine.py) se lit
# « publication en cours » au dashboard et dans api/posts.py.
RECLAMATION_EN_COURS = dt.timedelta(minutes=20)


@dataclass
class RapportPublication:
    # Les etapes tombees, nom et type d'exception seuls : leur message peut porter un
    # jeton, et ce champ finit dans un fichier d'etat lu par le bandeau.
    pannes: list[str] = field(default_factory=list)
    composes: int = 0
    geles: int = 0
    publies: int = 0
    apercus: int = 0
    manques: int = 0
    echecs: int = 0
    # Parmi les manques : ceux d'un format sans outils (reels coupes au
    # dashboard). Ils ne doivent pas etre annonces comme un Mac en veille.
    manques_formats_coupes: int = 0
    # Tache 5 (2026-09-21) : un creneau libere faute d'image de bonne qualite,
    # PAS une exclusion -- l'article reste eligible et repartira des qu'un
    # barreau de l'echelle (compte, artiste, web) donnera mieux. Compte a part
    # des `manques`, pour que le saut reste VISIBLE au lieu de se confondre avec
    # un Mac en veille ou un article vraiment en defaut.
    sautes: int = 0
    # Chaque REPORT (`VisuelsDifferes`), qu'il reste un simple report ou qu'il
    # bascule en saut au-dela du seuil de reports consecutifs -- un feed qui
    # se tait sans rien dire est le pire des deux mondes (CLAUDE.md), et un
    # report qui se prolonge doit se VOIR avant de devenir un saut.
    reportes: int = 0
    # Les creneaux pour lesquels on vient de PREVENIR qu'un post attend un oeil.
    # Ce n'est ni une faute ni une attente : l'app publie quand meme a l'heure si
    # personne ne relit (voir `app/schedule/relecture.py`).
    annonces_relecture: int = 0
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


TERMINES = (EtatCreneau.LIBRE, EtatCreneau.PUBLIE, EtatCreneau.APERCU, EtatCreneau.ECHEC, EtatCreneau.MANQUE)


def _menage(db, maintenant: dt.datetime) -> None:
    """Les gros fichiers d'un creneau termine. Les chemins nominaux effacent deja
    leur dossier ; le menage rattrape un crash apres reclamation ou un gel
    interrompu, qui laisseraient des dizaines de Mo de video sur le disque.
    `essais/` (script d'essai) n'est pas un nombre : jamais touche. Un creneau
    GELE, ou PROGRAMME a venir, garde toujours son dossier."""
    # Les copies locales des visuels d'artistes : elles ne servent qu'entre la
    # composition et le gel, personne d'autre ne les efface.
    purger_cache(maintenant)
    racine = settings.publication_dir
    if not racine.is_dir():
        return
    for dossier in racine.iterdir():
        if not (dossier.is_dir() and dossier.name.isdigit()):
            continue
        creneau = db.get(Creneau, int(dossier.name))
        if creneau is None or creneau.etat in TERMINES or (
            creneau.etat != EtatCreneau.GELE and utc(creneau.publier_a) <= maintenant
        ):
            shutil.rmtree(dossier, ignore_errors=True)


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


def _memoriser_duree(db, exc: ReelInadapte) -> bool:
    """Ecrit dans `articles.media` la duree que ffprobe vient de mesurer.

    Graph ne donne aucune duree pour une video : le champ reste a None et
    `videos_exploitables` laisse passer l'inconnu. La sonde du gel est la
    premiere mesure fiable -- la perdre, c'est refaire le meme telechargement
    (et la meme composition payee) au creneau suivant.

    Rend False quand la lecon n'a pas pu etre ecrite (article introuvable, ou
    URL qui ne correspond a aucun media -- le cas d'un permalink yt-dlp) :
    l'appelant retombe alors sur l'exclusion, la protection d'avant.
    """
    if exc.article_id is None or exc.url is None or exc.duree_ms is None:
        return False
    article = db.get(Article, exc.article_id, populate_existing=True)
    if article is None:
        return False
    # Liste NEUVE : une colonne JSON ne persiste pas une mutation en place.
    media, touche = [], False
    for entree_media in article.media or []:
        if not touche and isinstance(entree_media, dict) and entree_media.get("url") == exc.url:
            media.append({**entree_media, "duration_ms": exc.duree_ms})
            touche = True
        else:
            media.append(entree_media)
    if not touche:
        return False
    article.media = media
    db.commit()
    return True


def _deja_publie(article: Article | None, post: Post | None) -> bool:
    # Deja parti par un autre creneau, ou ligne ecrite du temps ou le dashboard
    # posait ces statuts a la main (les routes le refusent depuis le 2026-10-30).
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


def _differer(
    db, creneau: Creneau, attendu: tuple, article: Article, maintenant: dt.datetime,
    rapport: RapportPublication, exc: Exception,
) -> bool:
    """Un REPORT (`VisuelsDifferes`) : retenter au passage suivant reste le bon
    geste, mais pas eternellement. Compte les reports CONSECUTIFS
    (`signaler_report`) ; au-dela du seuil, bascule dans la MEME mise a
    l'ecart qu'un saut -- sans elle, un report qui ne guerit jamais fait
    camper l'article sur son creneau jusqu'a MANQUE (mesure : 1 publication
    sur 4, 3 MANQUE, 0 mise a l'ecart).

    MEME INVARIANT D'ORDRE que la ceinture de `_publier` : `_liberer` est une
    ecriture CONDITIONNELLE qui peut echouer si le dashboard est passe
    entre-temps ; l'article n'est mis a l'ecart, et le report ne compte comme
    converti, QUE SI cette liberation reussit -- sinon il prendrait une pause
    de plusieurs heures pour un creneau qu'on n'a meme pas reussi a lui
    retirer.

    Rend True si l'article vient de basculer en pause (le creneau a ete
    libere, l'appelant doit relancer l'arbitrage), False s'il reste un simple
    report (le creneau garde l'article)."""
    rapport.reportes += 1
    if signaler_report(article.id, maintenant) and _liberer(
        db, creneau, attendu, f"visuel introuvable (reports repetes) : {exc}"
    ):
        marquer_saute(article.id, maintenant)
        rapport.sautes += 1
        return True
    erreur = f"composition differee : {exc}"[:1000]
    if creneau.erreur != erreur:
        _ecrire_si(db, creneau, attendu, erreur=erreur)
    return False


def _sauter(
    db, creneau: Creneau, attendu: tuple, article: Article, maintenant: dt.datetime,
    rapport: RapportPublication, exc: Exception,
) -> None:
    """Un SAUT (`VisuelIntrouvable`) : toute l'echelle a ete parcourue, web
    compris, et rien n'a donne AUJOURD'HUI. Pas d'exclusion -- l'article peut
    avoir une photo demain (nouveau post du compte source, recherche web qui
    sort du cache apres `visuels_web_cache_jours`). Le creneau se libere et
    prend le candidat suivant tout de suite.

    La mise a l'ecart temporaire est indispensable : sans elle, l'article
    revenait sur ce meme creneau libere a CHAQUE passage (le meilleur score
    gagne l'arbitrage), sautait encore, jusqu'a MANQUE -- une seule mauvaise
    image pouvait ainsi faire taire tout le compte une journee entiere.

    INVARIANT D'ORDRE, le meme que `_differer` et que la ceinture de
    `_publier` : `_liberer` est une ecriture CONDITIONNELLE qui peut echouer si
    le dashboard est passe entre-temps, donc on libere D'ABORD et on ne met
    l'article a l'ecart QUE SI cette liberation a reussi -- sinon il prend une
    pause de plusieurs heures pour un creneau qu'on ne lui a meme pas retire.
    UN SEUL point d'appel pour les deux chemins de composition (le choix des
    visuels et la resolution du fond) : deux copies de cet ordre divergeraient.
    """
    if _liberer(db, creneau, attendu, f"visuel introuvable : {exc}"):
        marquer_saute(article.id, maintenant)
        rapport.sautes += 1


# Marque, sur le creneau, qu'une recomposition pour texte perime a DEJA ete tentee
# et a echoue : le gel ne relache pas une seconde fois ce post. Sans cette borne,
# gel et composition se renvoyaient la balle a chaque passage de l'agent (10 min) --
# jusqu'a trois appels Sonnet par passage sur une panne qui ne guerit pas.
RAISON_TEXTE_PERIME = "texte perime"


# Sur un creneau GELE qui part quand meme : une correction humaine a un repere perime.
AVERTISSEMENT_REPERE = "repere perime dans un post corrige a la main, il part tel quel"


def _replier(db, creneau: Creneau, attendu: tuple, post_id: int, raison: str) -> bool:
    """Rend au creneau le brouillon PERIME qu'on venait de lui retirer pour le
    recomposer, quand la recomposition a echoue.

    « Une recomposition qui echoue ne doit jamais faire perdre le creneau : on
    publie l'ancien texte plutot que rien, en le journalisant. » Un brouillon
    perime pour son TEXTE est par ailleurs intact -- visuels copies en local,
    credit, palette, legende --, donc publiable tel quel. C'est la seule difference
    avec un brouillon perime pour ses VISUELS, qui ne partirait pas de toute facon
    et dont le rejet n'a donc pas de repli.

    Rend True si le repli est pose (l'appelant passe au creneau suivant sans
    exclure ni liberer), False si le dashboard a bouge entre-temps -- son choix
    prime, et l'appelant reprend son traitement d'erreur normal.
    """
    erreur = f"{RAISON_TEXTE_PERIME}, recomposition impossible : {raison}"[:1000]
    if not _ecrire_si(db, creneau, attendu, post_id=post_id, erreur=erreur):
        return False
    logger.warning(
        "recomposition impossible pour le creneau %s : le post %s repart avec son "
        "texte perime (%s)", creneau.id, post_id, raison,
    )
    return True


def _compter_manque(creneau: Creneau, attendu: tuple, formats_actifs: set, rapport: RapportPublication) -> None:
    # Un creneau REELLEMENT vide -- ni article ni post -- n'est pas une
    # publication manquee : rien n'attendait de partir. Un creneau qui porte un
    # POST compose, lui, en est une, meme si la purge a emporte son article : le
    # post est autonome (voir `store.porte_un_post_orphelin`). Ne tester que
    # l'article, c'est exactement ce qui rendait ces manques invisibles du
    # rapport ET de la notification de fin de passage.
    if attendu[1] is None and attendu[2] is None:
        return
    rapport.manques += 1
    if creneau.format not in formats_actifs:
        rapport.manques_formats_coupes += 1


def _raison_manque(creneau: Creneau, formats_actifs: set) -> str:
    """Pourquoi ce creneau est manque. UN CRENEAU MANQUE PORTE TOUJOURS UNE
    RAISON -- 25 des 37 manques de production n'en avaient aucune, et c'est ce
    qui a rendu le defaut de la file invisible pendant une semaine
    (mesure du 2026-09-30). Ce depot a une longue histoire de pannes muettes :
    `_marquer_manques` ecrivait `etat = MANQUE` sans jamais toucher `erreur`,
    qui valait donc None des que la derniere etape reussie etait une
    composition ou une reattribution.

    Mais cette raison est GENERIQUE : elle dit l'etat atteint, jamais la cause.
    Or le creneau porte souvent deja la cause, ecrite par l'etape qui a echoue
    (« hebergement indisponible : aucun tunnel joignable ... », « gel differe :
    yt-dlp en pause ... », « composition differee : ... »). L'ecraser detruit
    la seule trace qui survivait a la purge des logs : mesure du 2026-10-02,
    11 des 44 manques venaient d'un tunnel cloudflared injoignable, et les plus
    recents ne le disaient plus -- il a fallu recroiser `publication.log` pour
    le retrouver. On CONSERVE donc l'erreur existante et on lui AJOUTE l'etat
    atteint ; le generique ne reste seul que si rien n'avait ete inscrit.

    L'erreur existante passe en TETE, pas en queue : `app/supervision.py` repere
    les pannes de composition par `Creneau.erreur.like("composition%")`, et un
    prefixe la rendrait invisible."""
    retard = settings.publication_retard_max_minutes
    if creneau.format not in formats_actifs:
        return (
            f"creneau manque : aucun outil pour le format {creneau.format.value} "
            "(reels coupes ?), rien n'a ete publie"
        )
    if creneau.etat == EtatCreneau.GELE:
        etat = f"gele mais jamais publie, plus de {retard} min de retard"
    elif creneau.post_id is None:
        etat = f"aucun post compose a l'heure dite, plus de {retard} min de retard"
    else:
        etat = f"post compose mais jamais gele, plus de {retard} min de retard"
    precedente = (creneau.erreur or "").strip()
    # « creneau manque » dans la raison precedente : deja passe ici (reprise
    # manuelle au dashboard, par exemple). Ne pas empiler deux fois le meme etat.
    if precedente and "creneau manque" not in precedente:
        return f"{precedente} — creneau manque : {etat}"
    return f"creneau manque : {etat}"


def _completer_credit(db, post: Post, article: Article | None) -> None:
    """Un brouillon `kinda_reel` compose avant le credit n'en porte pas : le
    compte source est complete a la reprise plutot que d'exclure l'article au gel
    (credit_video ne rend None que pour un article deja inapte au reel)."""
    if post.template != TEMPLATES[FormatCreneau.REEL] or post.credit or article is None:
        return
    credit = credit_video(article)
    if credit is not None:
        post.credit = credit
        db.commit()


def _choix_sans_lecture(db, article, format_, maintenant):
    # Par defaut, aucune lecture Graph : l'agent planifie passe un lecteur borne
    # (scripts/run_publication.py), les tests et les appels directs n'en ont pas.
    return choisir_visuels(db, article, format_=format_, maintenant=maintenant)


def _reperes_perimes(post: Post, maintenant: dt.datetime) -> Repere | None:
    """Le premier repere temporel du post que le temps a rendu FAUX ; None sinon.

    On relit ce que le post VA PUBLIER -- la cover retenue (`slides[0]`) et la
    legende --, jamais les quatre variantes : trois d'entre elles ne partiront
    jamais, et recomposer pour un « demain » qui dort dans une variante non
    retenue serait payer Sonnet pour rien.

    La reference de temps d'un texte est sa derniere edition humaine, a defaut
    `post.created_at` (`relecture.reference_du_texte`) : un humain qui ecrit « ce soir »
    ce matin dit vrai, quel que soit l'age du brouillon. Elle se compare a `maintenant`, jamais a
    `creneau.publier_a`, et c'est ce qui rend la regle SURE : un brouillon compose
    ce soir pour un creneau de demain matin serait sinon perime a la seconde ou on
    l'ecrit, donc recompose a chaque passage de l'agent (toutes les 10 minutes)
    jusqu'a minuit. Avec `maintenant`, un brouillon frais n'est jamais perime, et
    une recomposition pose un `created_at` du jour de lecture : au plus une
    recomposition par frontiere franchie, jamais une boucle. L'ecart entre
    `maintenant` (le gel, T-2h) et l'heure de publication ne change rien : les
    fenetres de publication vivent entre 08h30 et 22h30 heure de Paris, donc un gel
    tombe toujours le jour meme de son creneau.
    """
    if not settings.publication_reperes_relus:
        return None
    textes = []
    for slide in post.slides or []:
        if slide.get("kind") == "cover":
            textes += [(cle, slide.get(cle), False) for cle in ("headline", "title", "subtitle")]
            break
    textes.append(("caption", post.caption, True))
    for champ, texte, prose in textes:
        repere = repere_relatif(texte, prose=prose)
        if repere is not None and repere_perime(repere, reference_du_texte(post, champ, texte), maintenant):
            return repere
    return None


def _brouillon_perime(db, post: Post | None, article: Article | None, *, sources=None) -> bool:
    """Un brouillon dont les visuels ne sont plus publiables : compose avant la
    regle du 2026-09-17 sur un compte media (`assets` NULL), compose SANS aucun
    visuel autorise (`assets == []`, un post fait a la main au dashboard),
    portant l'actif d'un compte d'artiste passe `media` depuis la composition, ou
    dont le fond de cover a ete efface a la main au dashboard.

    Le TEXTE vieilli (« sort ce soir » publie deux jours plus tard) est un critere
    VOISIN mais separe, `_reperes_perimes` : les deux font recomposer par le meme
    chemin -- le creneau lache le post, la composition suivante en refait un --,
    mais un brouillon perime pour son texte reste PUBLIABLE (ses visuels, son
    credit et sa palette sont intacts), donc il sert de REPLI si la recomposition
    echoue, et sa raison au dashboard n'est pas la meme. Ce sont ses deux
    appelants, et non cette fonction, qui portent cette distinction.

    Le cas sans fond est le plus sournois : le brouillon existe, la file le
    trouve deja compose et le gel partirait sur le fond de secours. La file ne
    publie que ce qui porte un visuel : le creneau recompose, et si l'article n'a
    toujours aucun visuel autorise il sort par le chemin normal (AucunVisuel).
    Jamais d'exclusion ici -- une inadequation de filtre n'est pas la faute de
    l'article.
    """
    if post is None:
        return False
    if fond_de_cover(post) is None:
        # Fond EFFACE a la main au dashboard (le champ est editable), jamais pose
        # (brouillon de reel compose avant le correctif : article 17974, posts 34
        # et 35), ou reduit au `str(None)` que `fond_de_cover` lit comme absent :
        # le rendu partirait sur l'image de secours. Un brouillon a relacher,
        # jamais un article a exclure -- ses actifs sont peut-etre intacts, et la
        # composition suivante reposera un fond. Les DEUX gabarits : la cover d'un
        # reel est rendue comme celle d'un carrousel et sert de `cover_url`.
        return True
    if fond_de_cover(post) == FOND_KINDA:
        # Brouillon compose AVANT la Tache 5 (2026-09-21) : l'aplat Kinda etait
        # alors un fond valable. Il ne l'est plus -- la composition suivante
        # refait tourner l'echelle au complet (compte, artiste, web) et, si elle
        # ne trouve toujours rien, le creneau saute au lieu de geler ce fond.
        return True
    if post.assets is None:
        return origine_interdite(getattr(article, "source", None))
    if image_de_fond(post.assets) is None:
        # Aucun fond extractible : liste vide, ou des actifs qui ne portent
        # qu'une video sans vignette.
        return True
    return bool(actifs_interdits(db, post, sources))


def _marquer_manques(
    db, maintenant: dt.datetime, rapport: RapportPublication, formats_actifs: set
) -> None:
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
        raison = _raison_manque(creneau, formats_actifs)
        if _ecrire_si(db, creneau, attendu, etat=EtatCreneau.MANQUE, erreur=raison[:1000]):
            _compter_manque(creneau, attendu, formats_actifs, rapport)
            _effacer_dossier(creneau)


def _annoncer_relecture(db, maintenant: dt.datetime, notifier, rapport: RapportPublication) -> None:
    """Previent qu'un post attend un oeil -- et NE RETIENT RIEN.

    C'est le defaut n° 1 du chantier : un post gele attendait deux heures et
    personne ne le savait ; l'utilisateur devait ouvrir le dashboard par hasard au
    bon moment. Le canal est la notification macOS, le seul du projet (ni mail ni
    Discord : CLAUDE.md interdit de re-proposer SMTP, et les alertes Discord sont
    debranchees).

    DECLENCHEE PAR L'HORLOGE, jamais par le gel, et c'est la mesure qui l'impose
    (voir `app/schedule/relecture.py`) : 21 % des creneaux reellement geles l'ont
    ete a moins de 30 min de leur depart et 14 % a moins de 5 min -- une annonce
    accrochee au gel aurait donc donne zero minute de relecture a un creneau sur
    sept, exactement ceux dont la journee a derape.

    AU PLUS UNE PAR CRENEAU (`relecture.json`). Six annonces par post, c'est la
    facon la plus sure d'apprendre a ignorer les notifications -- le depot a deja
    paye cette lecon sur les alertes de quota.

    Ne leve jamais rien d'utile a l'appelant : elle est de toute facon enveloppee
    par `etape`, donc une annonce en panne ne peut pas emporter une publication.
    """
    from app.api.relecture import creneaux_a_relire

    attendus = creneaux_a_relire(db, maintenant)
    nouveaux = [(c, p) for c, p in attendus if not deja_annonce(c.id, maintenant)]
    if not nouveaux:
        return
    for creneau, _post in nouveaux:
        marquer_annonce(creneau.id, maintenant)
    rapport.annonces_relecture = len(nouveaux)
    premier, post = nouveaux[0]
    accroche = ((post.slides or [{}])[0] or {}).get("headline") or ""
    accroche = accroche.replace("**", "").strip()
    reste = f" (+{len(nouveaux) - 1} autre(s))" if len(nouveaux) > 1 else ""
    _prevenir(
        notifier,
        f"relecture : « {accroche[:70] or f'post {post.id}'} » part a "
        f"{_heure_locale(premier.publier_a)}{reste} — dashboard, panneau Programmation",
    )


def _composer(
    db, maintenant: dt.datetime, composer, notifier, rapport: RapportPublication, formats: set, choisir
) -> bool:
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
        if creneau.format not in formats:
            # Reels coupes : pas d'appel Claude paye pour un post qui ne partira pas.
            erreur = f"composition suspendue : aucun outil pour le format {creneau.format.value}"
            if creneau.erreur != erreur:
                _ecrire_si(db, creneau, attendu, erreur=erreur)
            continue
        article = db.get(Article, creneau.article_id, populate_existing=True)
        if article is None:
            _liberer(db, creneau, attendu, "article purge")
            continue
        if _deja_publie(article, None):
            _liberer(db, creneau, attendu, "article deja publie")
            continue
        template = TEMPLATES[creneau.format]
        if creneau.format == FormatCreneau.REEL:
            raison = raison_ineligible_reel(article, maintenant=maintenant)
            if raison is not None:
                # Pas d'exclusion : l'article reste bon pour un carrousel.
                _liberer(db, creneau, attendu, f"reel impossible : {raison}")
                continue
        post_id = post_a_reprendre(db, article.id, template)
        # Le brouillon perime pour son seul TEXTE reste un REPLI : il est
        # publiable (visuels, credit, palette intacts), seule sa date a vieilli.
        # Si la recomposition echoue, il repart tel quel plutot que de perdre le
        # creneau -- un « ce soir » date d'hier vaut mieux que rien.
        repli_id: int | None = None
        if post_id is not None:
            brouillon = db.get(Post, post_id, populate_existing=True)
            if _brouillon_perime(db, brouillon, article):
                # Compose avant la regle du 2026-09-17, ou sur un compte passe
                # media depuis : un nouveau brouillon est compose avec des
                # visuels autorises. Aucun repli : il ne partirait pas.
                post_id = None
            elif (
                brouillon is not None
                and not porte_une_correction(brouillon)
                and _reperes_perimes(brouillon, maintenant) is not None
            ):
                repli_id, post_id = post_id, None
                _completer_credit(db, brouillon, article)
            else:
                _completer_credit(db, brouillon, article)
        if post_id is None:
            # Les visuels AVANT l'appel paye : pas de composition pour un post qui
            # ne partirait pas.
            try:
                visuels = choisir(db, article, creneau.format, maintenant)
            except VisuelsDifferes as exc:
                if repli_id is not None and _replier(db, creneau, attendu, repli_id, str(exc)):
                    continue
                if _differer(db, creneau, attendu, article, maintenant, rapport, exc):
                    exclusions = True
                continue
            except VisuelIntrouvable as exc:
                # Rien dans l'actu, rien chez les artistes suivis cites, et le
                # WEB a regarde pour rien (2026-10-05). C'est la MEME conclusion
                # que celle de `resoudre_fond` ci-dessous, donc le meme geste :
                # un SAUT. Sans ce handler, l'exception remontait comme une
                # panne systemique -- et avant la conversion de ces chemins,
                # c'etait une EXCLUSION a vie, ce qui aurait brule un par un
                # les articles que le desserrage de `store.raison_sans_visuel`
                # vient de rendre eligibles.
                if repli_id is not None and _replier(db, creneau, attendu, repli_id, str(exc)):
                    continue
                _sauter(db, creneau, attendu, article, maintenant, rapport, exc)
                exclusions = True
                continue
            except AucunVisuel as exc:
                # Le FORMAT est impossible, quoi que trouve le web (un reel sans
                # video) : aucun passage suivant n'y changera rien, on exclut.
                if repli_id is not None and _replier(db, creneau, attendu, repli_id, str(exc)):
                    continue
                _exclure(db, creneau, attendu, f"composition impossible : {exc}", rapport)
                exclusions = True
                continue
            # Le fond AVANT l'appel paye (Sonnet) : Tache 5, decider du saut se
            # fait la ou les visuels sont deja choisis, jamais apres avoir paye
            # la composition. `resoudre_fond` ouvre les MEMES reserves reseau
            # (compte, artiste, web) que `create_post` en aurait ouvertes de
            # toute facon -- ce n'est pas un appel de plus, seulement plus tot.
            try:
                fond_precalcule = resoudre_fond(visuels, template)
            except VisuelsDifferes as exc:
                # Au moins une reserve n'a pas pu etre CONSULTEE (reseau,
                # quota) -- un report, jamais un saut : sans cette distinction,
                # une simple coupure reseau mettait l'article en pause pour
                # rien. `_differer` borne neanmoins la SUITE de reports.
                if repli_id is not None and _replier(db, creneau, attendu, repli_id, str(exc)):
                    continue
                if _differer(db, creneau, attendu, article, maintenant, rapport, exc):
                    exclusions = True
                continue
            except VisuelIntrouvable as exc:
                if repli_id is not None and _replier(db, creneau, attendu, repli_id, str(exc)):
                    continue
                _sauter(db, creneau, attendu, article, maintenant, rapport, exc)
                exclusions = True
                continue
            # Les visuels ont fini par se resoudre pour cet article : une
            # SUITE de reports vient de se casser, le compteur ne doit pas
            # survivre -- sinon un futur report isole repartirait avec un
            # compte deja proche du seuil.
            reinitialiser_report(article.id, maintenant)
            try:
                # Le compte du visuel part avec l'article : le compositeur ne
                # sait pas, sinon, de qui est la video qu'il titre (le reel « BTS »
                # publie sur une video de LE SSERAFIM, 2026-09-18).
                draft = appeler_compositeur(
                    composer, article, compte_du_visuel(visuels, creneau.format)
                )
                credits_anthropic.consigner_succes()
            except CompositionError as exc:
                if repli_id is not None and _replier(db, creneau, attendu, repli_id, str(exc)):
                    continue
                _exclure(db, creneau, attendu, f"composition impossible : {exc}", rapport)
                exclusions = True
                continue
            except Exception as exc:  # noqa: BLE001 -- credits, reseau : panne systemique
                if credits_anthropic.est_refus_de_credits(exc):
                    credits_anthropic.consigner_refus()
                erreur = f"composition interrompue : {exc}"[:1000]
                rapport.erreurs.append(erreur)
                _ecrire_si(db, creneau, attendu, erreur=erreur)
                _prevenir(notifier, erreur)
                if repli_id is not None:
                    # Panne systemique (credits, reseau) : le creneau repart avec
                    # son texte perime plutot que d'attendre une recomposition qui
                    # n'arrivera pas de la journee (solde Anthropic epuise le
                    # 2026-09-30) et de finir en MANQUE.
                    _replier(db, creneau, attendu, repli_id, str(exc))
                # Inutile d'enchainer les appels sur une panne qui touchera tous les articles.
                break
            post_id = create_post(
                db, article, draft, template=template, visuels=visuels,
                fond_precalcule=fond_precalcule,
            ).id
            rapport.composes += 1
        # Si le dashboard a change le creneau pendant la composition, son choix
        # prime : le post reste un brouillon, repris plus tard si l'article revient.
        _ecrire_si(db, creneau, attendu, post_id=post_id, erreur=None)
    return exclusions


def _geler(db, maintenant: dt.datetime, preparateurs: dict, notifier, rapport: RapportPublication) -> bool:
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
    formats_en_panne: set = set()
    for creneau in creneaux:
        if not _relire(db, creneau) or creneau.etat != EtatCreneau.PROGRAMME or creneau.post_id is None:
            continue
        if creneau.format in formats_en_panne:
            continue
        attendu = _attendu(creneau)
        # Creneau ROUVERT a la main pour relecture (`app/api/schedule.py`). Lu
        # AVANT toute ecriture de ce passage : les chemins ci-dessous reecrivent
        # `creneau.erreur`, et le marqueur y vit (meme procede que
        # `RAISON_RECLAMATION`). Il change une seule chose, et c'est la demande
        # « ne perds pas le post » : un gel IMPOSSIBLE apres une edition manuelle
        # libere le creneau au lieu d'exclure l'article a vie. Une accroche
        # rallongee d'un caractere de trop (cover qui deborde) ne doit pas bruler
        # l'actu -- c'est l'utilisateur qui vient de l'editer, pas l'article qui
        # est en defaut.
        rouvert = RAISON_RELECTURE in (creneau.erreur or "")
        preparer = preparateurs.get(creneau.format)
        if preparer is None:
            erreur = f"aucun preparateur pour le format {creneau.format.value}"
            if creneau.erreur != erreur:
                _ecrire_si(db, creneau, attendu, erreur=erreur)
            continue
        post = db.get(Post, creneau.post_id, populate_existing=True)
        if post is None:
            _liberer(db, creneau, attendu, "post supprime")
            continue
        if post.article_id != creneau.article_id:
            # Pas d'exclusion : l'article n'y est pour rien, c'est la file qui a bouge.
            erreur = "gel refuse : le post ne correspond pas a l'article du creneau"
            rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : {erreur}")
            if creneau.erreur != erreur:
                _ecrire_si(db, creneau, attendu, erreur=erreur)
            continue
        if post.template != TEMPLATES[creneau.format]:
            # Le switch POST/REELS a ete bascule au dashboard : pas la faute de
            # l'article. Le creneau lache le post ; la composition suivante reprend
            # ou compose un post au bon gabarit.
            erreur = (
                f"gel refuse : le post est au gabarit {post.template}, "
                f"le creneau attend {TEMPLATES[creneau.format]}"
            )
            rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : {erreur}")
            _ecrire_si(db, creneau, attendu, post_id=None, erreur=erreur)
            continue
        article = db.get(Article, post.article_id, populate_existing=True) if post.article_id else None
        if _deja_publie(article, post):
            _liberer(db, creneau, attendu, "article deja publie")
            continue
        if creneau.format == FormatCreneau.REEL and article is not None:
            raison = raison_ineligible_reel(article, maintenant=maintenant)
            if raison is not None:
                # L'article a change depuis la composition (video retiree, compte
                # reclasse) : il reste bon pour un carrousel, pas d'exclusion.
                _liberer(db, creneau, attendu, f"reel impossible : {raison}")
                continue
            _completer_credit(db, post, article)
        # Article PURGE (`retention_days`, 2 jours) : la relecture d'eligibilite
        # est SAUTEE, et le creneau part quand meme. Ce n'est pas une
        # complaisance -- c'est qu'il n'y a plus rien a relire : la composition a
        # deja verifie cette eligibilite (video, categorie du compte, credit), et
        # tout ce que la publication demande vit desormais sur le POST, qui est
        # denormalise exprès pour survivre a la purge -- le credit
        # (`posts.credit`, obligatoire, verifie par `preparer_reel`), les actifs
        # et leur video (`posts.assets`), les copies locales des visuels, la
        # palette, les textes. Liberer ici, c'etait jeter un reel PRET pour une
        # information absente, et enterrer le creneau en MANQUE sans raison.
        deja_tente = (creneau.erreur or "").startswith(RAISON_TEXTE_PERIME)
        # Le robot ne remplace jamais une correction humaine : ni relance possible sur un
        # texte humain, ni appel paye. Le controle de FORME accepte donc, avec un
        # avertissement visible (`generation-de-posts.md`, « relance, puis acceptation »).
        corrige = porte_une_correction(post)
        repere = (
            _reperes_perimes(post, maintenant)
            if corrige or (article is not None and not deja_tente)
            else None
        )
        avertissement = (
            f"{AVERTISSEMENT_REPERE} : le texte dit « {repere.expression} »"
            if corrige and repere is not None
            else None
        )
        if repere is not None and not corrige:
            # Dernier moment ou l'on peut encore agir (T-2h). Le creneau lache le
            # post, la composition du passage suivant en refait un avec une date
            # absolue -- ni exclusion ni creneau perdu : l'article n'y est pour
            # rien, c'est le TEXTE qui a vieilli. Message distinct de celui des
            # visuels : une raison fausse au dashboard coute une enquete.
            # SEULEMENT quand l'article est encore la (`article is not None`) :
            # purge passee, le creneau est AUTONOME (`store.
            # porte_un_post_orphelin`) et personne ne pourra recomposer -- lacher
            # le post rendrait alors le creneau invisible de la composition comme
            # du gel, jusqu'a MANQUE. C'est le cas MEME du post 153, dont
            # l'article avait ete purge : on publie son texte perime plutot que
            # de perdre le creneau, et c'est le controle de composition (remede 1)
            # qui empeche ce texte d'exister.
            # Et une seule fois (`deja_tente`) : un post rendu par `_replier` apres
            # une recomposition en echec repart tel quel plutot que de refaire le
            # tour a chaque passage.
            erreur = (
                f"gel refuse : le texte dit « {repere.expression} », "
                "ce qui n'est plus vrai depuis la composition"
            )
            rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : {erreur}")
            _ecrire_si(db, creneau, attendu, post_id=None, erreur=erreur)
            continue
        if _brouillon_perime(db, post, article):
            # Relu au gel, APRES la regle reel : la source d'un actif d'artiste
            # est reprise par son article (compte passe `media` depuis la
            # composition), et un brouillon sans aucun visuel ne part pas sur le
            # fond de secours. L'article n'y est pour rien : le creneau lache le
            # post, et la composition suivante en refait un avec des visuels
            # autorises (ou l'exclut, avec la bonne raison).
            erreur = "gel refuse : le post n'a pas de visuel publiable"
            rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : {erreur}")
            _ecrire_si(db, creneau, attendu, post_id=None, erreur=erreur)
            continue
        try:
            figement = normaliser_figement(preparer(post, article, _dossier(creneau)))
        except PreparationDifferee as exc:
            # Pause ou budget de yt-dlp : le creneau attend le passage suivant.
            # Ni exclusion ni notification (la panne l'a deja ete, une fois), et
            # le format n'est pas suspendu : un reel a video directe peut geler.
            #
            # Le marqueur de relecture passe en TETE, comme `_raison_manque`
            # conserve la cause precedente : sans ca, un premier gel differe
            # effacerait la protection et le second echec exclurait l'article que
            # l'utilisateur vient d'editer.
            erreur = f"gel differe : {exc}"[:1000]
            if rouvert:
                erreur = f"{RAISON_RELECTURE} — {erreur}"[:1000]
            if creneau.erreur != erreur:
                _ecrire_si(db, creneau, attendu, erreur=erreur)
            continue
        except ReelInadapte as exc:
            # La VIDEO ne convient pas, l'article si -- elle est souvent empruntee
            # a un compte d'artiste (article 18951, 2026-09-21). On libere sans
            # exclure, mais SEULEMENT si on a su ecrire la duree mesuree : sans
            # cette trace, la meme video serait rechoisie et recomposee a chaque
            # creneau, et c'est l'exclusion qui tenait ce role.
            if not _memoriser_duree(db, exc):
                _exclure(db, creneau, attendu, f"gel impossible : {exc}", rapport)
                exclusions = True
                continue
            rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : reel impossible : {exc}")
            _liberer(db, creneau, attendu, f"reel impossible : {exc}")
            continue
        except PreparationSautee as exc:
            # Tache 6 : l'agrandissement au gel n'a pas suffi (outil
            # indisponible, ou resultat encore sous le plancher). SAUT, pas
            # exclusion -- meme mecanique que le saut de la Tache 5 a la
            # composition (`VisuelIntrouvable`, plus haut dans ce fichier) :
            # liberer le creneau D'ABORD, ne poser la mise a l'ecart que si
            # la liberation a reussi.
            rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : visuel trop petit : {exc}")
            if _liberer(db, creneau, attendu, f"visuel trop petit : {exc}"):
                if article is not None:
                    marquer_saute(article.id, maintenant)
                rapport.sautes += 1
            continue
        except PreparationImpossible as exc:
            if rouvert:
                # « Ne perds pas le post » : le creneau se libere, l'article RESTE
                # eligible, et le POST survit (les posts ne sont jamais purges) --
                # l'utilisateur corrige son edition et le reprend. Une exclusion
                # ici brulerait une actu pour une faute de frappe.
                rapport.erreurs.append(
                    f"{_heure_locale(creneau.publier_a)} : gel refuse apres relecture : {exc}"
                )
                _liberer(db, creneau, attendu, f"{RAISON_RELECTURE} — gel refuse : {exc}")
                exclusions = True
                continue
            _exclure(db, creneau, attendu, f"gel impossible : {exc}", rapport)
            exclusions = True
            continue
        except Exception as exc:  # noqa: BLE001 -- rendu, ffmpeg, disque, reseau : panne systemique
            # Le creneau reste PROGRAMME : retente au passage suivant, tant que le
            # delai de retard le permet. Seul son format est suspendu.
            erreur = f"gel interrompu : {exc}"[:1000]
            rapport.erreurs.append(erreur)
            _ecrire_si(db, creneau, attendu, erreur=erreur)
            _prevenir(notifier, erreur)
            formats_en_panne.add(creneau.format)
            continue
        if _ecrire_si(
            db, creneau, attendu,
            fichiers=figement.noms,
            # Le MANIFESTE, ecrit dans la meme ecriture CONDITIONNELLE que les
            # noms de fichiers : les deux decrivent le meme gel et ne doivent
            # jamais pouvoir se desynchroniser. `None` quand le preparateur ne le
            # rend pas (preparateur injecte) : inconnu, jamais une liste inventee.
            elements=figement.elements,
            etat=EtatCreneau.GELE, gele_le=maintenant, erreur=avertissement,
        ):
            rapport.geles += 1
            if avertissement:
                message = f"{_heure_locale(creneau.publier_a)} : {avertissement}"
                rapport.erreurs.append(message)
                _prevenir(notifier, f"{message} — corrige-le depuis le dashboard, sinon il part ainsi")
    return exclusions


def _mode_courant(lire_mode) -> str:
    # Illisible : on ne reclame rien. Un post manque vaut mieux qu'un post
    # publie contre le choix de l'utilisateur.
    try:
        mode = lire_mode()
    except Exception:  # noqa: BLE001
        logger.exception("mode de publication illisible : rien n'est reclame")
        return "arret"
    return mode if mode in MODES else "arret"


PAS_ATTENTE_S = 30


def _attendre_l_heure(cible: dt.datetime, horloge, dormir) -> None:
    """Dort jusqu'a `cible` par pas courts, relus sur l'horloge MURALE : un Mac
    mis en veille pendant l'attente ne rallonge pas l'attente, il se voit au
    reveil (et `_encore_publier` jugera le retard). Jamais plus que l'avance au
    total. Conteneur pret apres l'heure : aucune attente."""
    fin = min(cible, horloge() + dt.timedelta(seconds=settings.publication_avance_s))
    while (restant := (fin - horloge()).total_seconds()) > 0:
        dormir(min(restant, PAS_ATTENTE_S))


def _encore_publier(decision: dict, lire_mode, publier_a: dt.datetime, horloge) -> bool:
    """Relu juste avant media_publish : le retard d'abord (Mac endormi pendant
    l'attente, enfants video longs a encoder), puis le mode."""
    if horloge() - publier_a > dt.timedelta(minutes=settings.publication_retard_max_minutes):
        decision["mode"] = "manque"
        return False
    decision["mode"] = _mode_courant(lire_mode)
    return decision["mode"] == "reel"


def _clore_transaction(db) -> None:
    """Rend la session SANS transaction ouverte, avant une attente ou un appel long.

    Une session qui a LU garde sa transaction ouverte tant qu'on ne la clot pas
    (ici : le `refresh` de la reclamation, les `get` du post et de l'article).
    L'attente de l'heure puis l'appel a Meta durent des minutes : Neon coupe
    alors la connexion restee « idle in transaction », et c'est l'ecriture
    d'APRES publication qui tombe -- celle qui enregistre PUBLIE, l'ig_media_id
    et l'article USED. Le 17/09, le reel etait en ligne et la base disait ECHEC.

    `expire_on_commit=False` : les objets gardent leurs valeurs apres ce commit,
    et rien ne doit les relire avant le retour de `publier` (ce serait rouvrir
    une transaction pour la duree de l'appel).
    """
    try:
        db.commit()
    except Exception:  # noqa: BLE001 -- une session deja fautive se referme ici
        logger.exception("commit impossible avant l'attente : rollback")
        db.rollback()


# Une connexion morte se signale ainsi ; `PendingRollbackError` est ce que rend la
# session quand un echec precedent l'a laissee a nettoyer.
COUPURES = (OperationalError, InterfaceError, PendingRollbackError)


def _ecrire_apres_publication(db, ecrire):
    """Ecrit le resultat d'une publication, avec UNE reconnexion si besoin.

    L'appel a Meta est deja fait : perdre cette ecriture laisserait un post EN
    LIGNE enregistre en ECHEC, article exclu et jamais marque USED -- la base ne
    dirait plus la realite, et l'utilisateur verrait un faux echec. Un second
    essai ne republie RIEN : seule la base est retouchee, le conteneur Meta n'est
    pas renvoye (la reclamation, elle, reste committee et interdit toujours un
    doublon).

    `rollback()` rend la connexion au pool (`pool_pre_ping` en fournira une
    vivante) et defait les modifications en attente : `ecrire` les repose.
    """
    try:
        return ecrire()
    except COUPURES:
        logger.exception("connexion perdue apres la publication : reconnexion et second essai")
    db.rollback()
    return ecrire()


def _publier(
    db,
    maintenant: dt.datetime,
    lire_mode,
    publieurs: dict,
    notifier,
    rapport: RapportPublication,
    horloge,
    dormir,
    reportes: set,
) -> None:
    # Horloge fraiche + avance : un creneau a moins d'un intervalle de l'agent
    # est traite des maintenant, puis attendu (voir plus bas).
    horizon = horloge() + dt.timedelta(seconds=settings.publication_avance_s)
    creneaux = db.scalars(
        select(Creneau)
        .where(Creneau.etat == EtatCreneau.GELE, Creneau.publier_a <= horizon)
        .order_by(Creneau.publier_a)
    ).all()
    for creneau in creneaux:
        if not _relire(db, creneau) or creneau.etat != EtatCreneau.GELE:
            continue
        if creneau.id in reportes:
            # Son tunnel vient d'echouer dans ce meme passage : le rouvrir tout de
            # suite couterait plusieurs minutes pour rien (et une seconde
            # notification). Le passage suivant, dix minutes plus tard, retentera.
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
        a_venir = retard < dt.timedelta(0)
        if retard > dt.timedelta(minutes=settings.publication_retard_max_minutes):
            # Une raison, toujours : ce chemin passait un creneau GELE en MANQUE
            # sans rien inscrire.
            if _ecrire_si(
                db, creneau, attendu, etat=EtatCreneau.MANQUE,
                erreur=_raison_manque(creneau, set(publieurs))[:1000],
            ):
                _compter_manque(creneau, attendu, set(publieurs), rapport)
                _effacer_dossier(creneau)
            continue

        publier = publieurs.get(creneau.format)
        if publier is None:
            # Il reste GELE : il finira en MANQUE si aucun publieur n'arrive a temps.
            erreur = f"aucun publieur pour le format {creneau.format.value}"
            if creneau.erreur != erreur:
                _ecrire_si(db, creneau, attendu, erreur=erreur)
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

        if fond_de_cover(post) == FOND_KINDA:
            # Ceinture : un creneau deja GELE avant que l'aplat Kinda cesse
            # d'etre une issue publiable peut porter un post compose sur ce
            # fond. Refuser de publier plutot que d'exiger une operation
            # manuelle sur les creneaux deja geles en base. Pas d'exclusion
            # (l'article n'y est pour rien) ; mise en pause comme un article
            # sans visuel, sinon l'arbitrage relancerait le meme article sur
            # ce meme creneau.
            # ORDRE : `_liberer` D'ABORD -- c'est une ecriture CONDITIONNELLE
            # (`_ecrire_si`) qui peut echouer si le dashboard est passe
            # entre-temps ; mettre l'article en pause AVANT de le savoir lui
            # infligerait 3 h d'ecart pour rien sur un creneau qu'on n'a meme
            # pas reussi a liberer. Meme invariant que `_differer`.
            if _liberer(
                db, creneau, attendu,
                "aplat Kinda en fond : publication refusee, nouvelle composition",
            ):
                if article is not None:
                    marquer_saute(article.id, maintenant)
                rapport.sautes += 1
                _effacer_dossier(creneau)
            continue

        # Le mode RELU juste avant la reclamation : un passage peut durer bien plus
        # de 10 minutes (yt-dlp, ffmpeg, tunnel), et le dashboard a pu repasser
        # en apercu ou en arret entre-temps. Son dernier choix prime.
        mode = _mode_courant(lire_mode)
        if mode == "arret":
            continue
        if a_venir and mode != "reel":
            # En apercu rien n'est public : inutile de reclamer d'avance, le
            # creneau attend son heure (et peut encore partir si l'on repasse en reel).
            continue

        # Reclamation committee AVANT l'appel : si le processus meurt pendant ou
        # juste apres la publication, le creneau reste en ECHEC et n'est jamais
        # retente. L'article est exclu de meme, sinon il repartirait sur un autre
        # creneau. Zero ligne touchee : un autre passage (ou le dashboard) est passe.
        interruption = RAISON_RECLAMATION
        if not _reclamer(db, creneau, attendu, etat=EtatCreneau.ECHEC, erreur=interruption):
            continue
        if article is not None:
            article.file_exclu_raison = interruption
        db.commit()
        _relire(db, creneau)

        # Publication a l'heure affichee : conteneur cree d'avance, attente dans
        # le tunnel ouvert, mode RELU juste avant media_publish. Un processus tue
        # pendant l'attente laisse l'ECHEC de la reclamation : post perdu, jamais double.
        # En reel, mode ET retard sont relus avant media_publish meme pour un
        # creneau deja du : l'encodage des enfants video peut durer des minutes.
        decision = {"mode": mode}
        reclame = _attendu(creneau)
        crochets = {}
        if mode == "reel":
            crochets["encore_publier"] = partial(
                _encore_publier, decision, lire_mode, utc(creneau.publier_a), horloge
            )
        if a_venir and mode == "reel":
            crochets["attendre_jusqu_a"] = partial(_attendre_l_heure, utc(creneau.publier_a), horloge, dormir)

        # Tout ce dont l'appel a besoin est lu MAINTENANT, avant de rendre la
        # session sans transaction : rien ne doit toucher la base entre ici et le
        # retour de `publier` (voir `_clore_transaction`).
        dossier, fichiers, legende = _dossier(creneau), creneau.fichiers or [], texte_legende(post)
        _clore_transaction(db)
        try:
            identifiant = publier(dossier, fichiers, legende, mode == "reel", **crochets)
        except HebergementIndisponible as exc:
            # Panne MACHINE (cloudflared, reseau, tunnel injoignable), pas un
            # defaut de l'article : rien n'a ete envoye a Meta, le conteneur
            # n'etant cree qu'une fois le tunnel debout (voir app/publish/publier.py,
            # ou `preparer_carrousel` / `preparer_reel` sont DANS le `with
            # heberger(...)`). Retenter ne peut donc pas publier deux fois.
            # L'exclusion posee a la reclamation est levee, le creneau redevient
            # GELE avec ses fichiers (toujours valides, le dossier est conserve)
            # et le passage suivant retente -- jusqu'a la limite de retard, qui
            # le passera en MANQUE comme n'importe quel creneau gele.
            faute = exc

            def rendre():
                if article is not None:
                    article.file_exclu_raison = None
                _ecrire_si(
                    db, creneau, reclame,
                    etat=EtatCreneau.GELE, erreur=f"hebergement indisponible : {faute}"[:1000],
                )

            _ecrire_apres_publication(db, rendre)
            reportes.add(creneau.id)
            message = f"publication de {_heure_locale(creneau.publier_a)} reportee : {exc}"
            rapport.erreurs.append(message[:1000])
            _prevenir(notifier, message)
            continue
        except Exception as exc:  # noqa: BLE001 -- tout echec doit se voir
            # Alias : `exc` est efface a la sortie du bloc `except`, la fermeture
            # le lirait donc sur un nom disparu.
            faute = exc

            def echec():
                creneau.etat = EtatCreneau.ECHEC
                # Le prefixe est la trace du doute : `store._articles_publiables`
                # le lit si l'exclusion ci-dessous venait a disparaitre.
                creneau.erreur = f"{PREFIXE_ECHEC_PUBLICATION}{faute}"[:1000]
                if article is not None:
                    # Un article refuse par Meta ne revient pas dans la file.
                    article.file_exclu_raison = f"{PREFIXE_ECHEC_PUBLICATION}{faute}"[:500]
                db.commit()

            _ecrire_apres_publication(db, echec)
            _effacer_dossier(creneau)
            rapport.echecs += 1
            _prevenir(notifier, f"publication de {_heure_locale(creneau.publier_a)} en echec : {exc}")
            continue
        mode = decision["mode"]

        def effacer_exclusion():
            if article is not None:
                article.file_exclu_raison = None

        if mode == "arret":
            # Arret choisi pendant l'attente : rien n'est parti (le conteneur non
            # publie expire seul). Le creneau redevient GELE, dossier conserve :
            # il partira si l'on repasse en reel avant la limite de retard.
            # Ecriture CONDITIONNELLE : si le dashboard est passe, son choix prime.
            def suspendre():
                effacer_exclusion()
                _ecrire_si(
                    db, creneau, reclame,
                    etat=EtatCreneau.GELE,
                    erreur="publication suspendue : mode arret choisi avant l'heure",
                )

            _ecrire_apres_publication(db, suspendre)
            continue
        if mode == "manque":
            # Trop tard au moment de media_publish : rien n'est parti, l'article
            # peut revenir dans la file.
            def manquer():
                effacer_exclusion()
                return _ecrire_si(
                    db, creneau, reclame, etat=EtatCreneau.MANQUE, erreur="retard au moment de publier"
                )

            if _ecrire_apres_publication(db, manquer):
                _compter_manque(creneau, reclame, set(publieurs), rapport)
                _effacer_dossier(creneau)
            continue

        # Le post EST parti (ou son conteneur est pret, en apercu) : cette ecriture
        # doit aboutir, quitte a reconnecter. Les compteurs du rapport sont poses
        # APRES, pour qu'un second essai ne compte pas deux publications.
        def enregistrer():
            effacer_exclusion()
            creneau.ig_media_id = identifiant
            creneau.erreur = None
            if mode == "reel":
                # Heure REELLE de la publication, apres l'eventuelle attente.
                publie_le = horloge()
                creneau.etat = EtatCreneau.PUBLIE
                creneau.publie_le = publie_le
                post.status = PostStatus.PUBLISHED
                post.published_at = publie_le
                if article is not None:
                    # USED protege l'article de la purge et l'ecarte de la file.
                    article.status = ArticleStatus.USED
            else:
                # Pas de publie_le : rien n'est parti. L'etat APERCU suffit a faire
                # avancer la file (voir candidats_eligibles).
                creneau.etat = EtatCreneau.APERCU
            db.commit()

        _ecrire_apres_publication(db, enregistrer)
        if mode == "reel":
            rapport.publies += 1
        else:
            rapport.apercus += 1
        _effacer_dossier(creneau)


def tourner(
    db,
    maintenant: dt.datetime,
    *,
    mode: str,
    composer,
    preparer,
    publier,
    notifier,
    horloge=None,
    preparer_reel=None,
    publier_reel=None,
    lire_mode=None,
    choisir=None,
    dormir=None,
) -> RapportPublication:
    if mode not in MODES:
        raise ValueError(f"mode de publication inconnu : {mode!r}, attendu {MODES}")
    rapport = RapportPublication()
    if mode == "arret":
        return rapport
    horloge = horloge or _maintenant_reel
    dormir = dormir or time.sleep
    # Relu avant chaque reclamation (voir _publier). Par defaut : le mode du debut.
    lire_mode = lire_mode or (lambda: mode)
    choisir = choisir or _choix_sans_lecture
    preparateurs = {FormatCreneau.CARROUSEL: preparer}
    publieurs = {FormatCreneau.CARROUSEL: publier}
    if preparer_reel is not None:
        preparateurs[FormatCreneau.REEL] = preparer_reel
    if publier_reel is not None:
        publieurs[FormatCreneau.REEL] = publier_reel

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
            rapport.pannes.append(f"etape {nom} en panne ({type(exc).__name__})")
            _prevenir(notifier, message)
            return None

    # Partage par les deux etapes de publication du passage : un creneau dont le
    # tunnel vient d'echouer n'est pas repris a la seconde.
    reportes: set = set()

    def publication():
        _publier(db, maintenant, lire_mode, publieurs, notifier, rapport, horloge, dormir, reportes)

    formats_actifs = set(preparateurs) & set(publieurs)
    etape("retards", lambda: _marquer_manques(db, maintenant, rapport, formats_actifs))
    # Isolee comme les autres, et volontairement APRES les retards : on ne
    # previent jamais pour un creneau qui vient de passer en MANQUE. Elle ne
    # retient rien -- si personne ne relit, la suite du passage publie a l'heure.
    etape("relecture", lambda: _annoncer_relecture(db, maintenant, notifier, rapport))
    etape("menage", lambda: _menage(db, maintenant))
    etape("publication", publication)
    etape("arbitrage", lambda: appliquer_arbitrage(db, maintenant))
    exclusions = bool(
        etape(
            "composition",
            lambda: _composer(db, maintenant, composer, notifier, rapport, formats_actifs, choisir),
        )
    )
    exclusions = bool(etape("gel", lambda: _geler(db, maintenant, preparateurs, notifier, rapport))) or exclusions
    if exclusions:
        # Le creneau libere se remplit tout de suite ; sa composition viendra au
        # passage suivant.
        etape("arbitrage", lambda: appliquer_arbitrage(db, maintenant))
    etape("publication", publication)
    en_retard = rapport.manques - rapport.manques_formats_coupes
    if en_retard:
        _prevenir(
            notifier,
            f"{en_retard} publication(s) manquee(s) : plus de "
            f"{settings.publication_retard_max_minutes} minutes de retard (Mac en veille ?)",
        )
    if rapport.manques_formats_coupes:
        _prevenir(
            notifier,
            f"{rapport.manques_formats_coupes} reel(s) manque(s) : reels coupés depuis le dashboard, "
            "rien n'a ete publie",
        )
    return rapport
