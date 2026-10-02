"""Ce que la machine a fait en mon nom -- lu dans ce qui est DEJA ecrit.

POURQUOI. Semaine du 2026-09-26 au 2026-10-02 : credits Anthropic epuises deux
fois (plus une seule composition pendant deux jours), 46 % des creneaux manques
dont 25 sur 37 sans aucune raison enregistree, 751 erreurs de quota Graph avec
80 % des comptes Instagram plus lus du tout, et une actu deja publiee sur le
point de repartir. Aucun de ces incidents n'etait visible a l'ecran : chacun a
ete decouvert par hasard. Le signal existait a chaque fois -- dans
`sources.state`, dans `creneaux.erreur`, dans les fichiers d'etat a cote du
jeton -- mais rien ne le lisait.

TROIS REGLES, et la premiere gouverne les deux autres.

1. UNE VALEUR QU'ON N'A PAS PU LIRE VAUT « INCONNU », JAMAIS « TOUT VA BIEN ».
   Un voyant vert par defaut sur une machine en panne est pire que pas de
   voyant : il apprend a ne plus regarder. D'ou `lire_json`, qui distingue
   trois issues (absent, lisible, illisible) la ou les lecteurs de l'etat
   (`app/publish/mode.py`, `app/pipeline/quota_graph.py`) les confondent
   volontairement en une -- eux doivent CONTINUER A TOURNER sans fichier, ce
   module doit DIRE ce qu'il ne sait pas. Et `superviser` enveloppe chaque
   voyant : une exception imprevue devient un voyant « inconnu » qui porte le
   type de l'exception, jamais un voyant absent (qu'on lirait comme « rien a
   signaler ») ni une 500 qui viderait tout le bandeau.

2. CHAQUE VOYANT PORTE SA RAISON EN CLAIR. `Voyant.raison` est un texte
   affichable, adjacent au voyant -- la force deja presente dans la fiche de
   post, ou un controle desactive imprime toujours son motif.

3. CA NE COUTE RIEN. Aucun appel reseau, aucun appel paye : des requetes sur la
   base locale et des fichiers JSON de quelques centaines d'octets. L'echeance
   du jeton de SOURCING est le seul chiffre qui demanderait un appel a Meta :
   elle est donc CONSIGNEE par le passage horaire, qui appelle `debug_token` de
   toute facon (`app/sources/instagram_jeton.py`), et seulement relue ici.
"""

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, Creneau, EtatCreneau, Post, Source
from app.pipeline.health import SIGNATURE_CREDITS
from app.sources.instagram_jeton import FICHIER_ALERTE, RENOUVELER

logger = logging.getLogger(__name__)

OK = "ok"
ATTENTION = "attention"
PANNE = "panne"
INCONNU = "inconnu"

# L'inconnu passe DEVANT l'attention : une valeur qu'on n'a pas pu lire peut
# cacher une panne, une valeur qu'on a lue et qui merite un oeil ne cache rien.
GRAVITE = {OK: 0, ATTENTION: 1, INCONNU: 2, PANNE: 3}

# Le passage d'ingestion est HORAIRE (launchd, `com.musicnewsradar.pipeline`).
# Un creneau manque coute une publication ; une heure d'ingestion en retard ne
# coute rien, d'ou des seuils larges -- le Mac dort, et c'est assume.
INGESTION_ATTENTION = dt.timedelta(hours=2)
INGESTION_PANNE = dt.timedelta(hours=4)

# `score_pending` note TOUS les articles en attente a chaque passage horaire,
# sans limite. Un article ingere il y a plus de deux heures et toujours sans
# note n'est donc pas un retard : c'est un scoring qui ne tourne plus.
RETARD_SCORING = dt.timedelta(hours=2)

# Au-dela, un echec de composition appartient a l'histoire, plus a l'etat.
FENETRE_COMPOSITION = dt.timedelta(hours=36)


@dataclass(frozen=True)
class Voyant:
    """Un fait sur la machine, son niveau, et la raison en clair."""

    cle: str
    libelle: str
    niveau: str
    valeur: str
    raison: str | None = None
    details: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Lecture:
    """Le resultat d'une lecture de fichier d'etat, en TROIS issues.

    `absent` n'est pas `erreur` : un `quota_graph.json` qui n'existe pas veut
    dire « aucune pause n'a jamais ete posee », un fichier tronque veut dire
    « on ne sait pas ». Les confondre est exactement ce qui fabrique un voyant
    vert sur une machine en panne.
    """

    donnees: dict | None = None
    absent: bool = False
    erreur: str | None = None


def lire_json(chemin: Path) -> Lecture:
    try:
        if not chemin.exists():
            return Lecture(absent=True)
        donnees = json.loads(chemin.read_text())
    except OSError as exc:
        return Lecture(erreur=f"fichier illisible ({type(exc).__name__})")
    except ValueError:
        return Lecture(erreur="contenu JSON invalide")
    if not isinstance(donnees, dict):
        return Lecture(erreur="contenu JSON inattendu (pas un objet)")
    return Lecture(donnees=donnees)


def niveau_global(voyants: list[Voyant]) -> str:
    """Le niveau du bandeau : le PIRE de ses voyants.

    Jamais `ok` des qu'un seul voyant ne l'est pas -- c'est la propriete que
    `tests/test_supervision.py` verrouille, parce qu'un `or OK` bien
    intentionne la casserait sans faire echouer quoi que ce soit d'autre.
    """
    if not voyants:
        return INCONNU
    return max((v.niveau for v in voyants), key=lambda niveau: GRAVITE.get(niveau, GRAVITE[INCONNU]))


def _utc(moment: dt.datetime | None) -> dt.datetime | None:
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.timezone.utc)


def _date_heure(valeur) -> dt.datetime | None:
    """Un horodatage ISO du disque, ou None -- ne leve jamais."""
    if not isinstance(valeur, str):
        return None
    try:
        return _utc(dt.datetime.fromisoformat(valeur))
    except ValueError:
        return None


def _ecoule(maintenant: dt.datetime, quand: dt.datetime | None) -> str:
    if quand is None:
        return "jamais"
    secondes = (maintenant - quand).total_seconds()
    if secondes < 0:
        return "a venir"
    minutes = int(secondes // 60)
    if minutes < 60:
        return f"il y a {minutes} min"
    heures = minutes // 60
    if heures < 48:
        return f"il y a {heures} h"
    return f"il y a {heures // 24} j"


def _jours(delta: dt.timedelta) -> int:
    return int(delta.total_seconds() // 86400)


def _ligne(texte, maximum: int = 200) -> str:
    """Un message d'erreur reduit a UNE ligne, et borne.

    Les erreurs du monde reel portent des sauts de ligne (httpx ajoute « For
    more information check: … » sous le message) : telles quelles, elles
    cassent la mise en page du bandeau et noient la phrase utile.
    """
    aplati = " ".join(str(texte).split())
    return aplati if len(aplati) <= maximum else aplati[: maximum - 1] + "…"


# --- Le mode de publication -------------------------------------------------

MODES_LISIBLES = {"arret": "Arrêt", "apercu": "Aperçu", "reel": "Réel"}


def voyant_mode(maintenant: dt.datetime) -> Voyant:
    """Publie-t-on vraiment ? La premiere question du bandeau.

    On relit le fichier NOUS-MEMES au lieu d'appeler `mode_effectif()` : celui-ci
    retombe deliberement sur `.env` quand le fichier est illisible (un agent
    planifie ne doit pas mourir faute de fichier), ce qui afficherait un mode
    peut-etre faux sans le dire.
    """
    lecture = lire_json(settings.publication_mode_path)
    if lecture.erreur:
        return Voyant(
            "mode",
            "Publication",
            INCONNU,
            "inconnu",
            f"{settings.publication_mode_path.name} : {lecture.erreur} — "
            f"le mode affiche est celui du .env ({MODES_LISIBLES.get(settings.publication_mode, settings.publication_mode)})",
        )
    donnees = lecture.donnees or {}
    mode = donnees.get("mode") if donnees.get("mode") in MODES_LISIBLES else None
    source = "dashboard" if mode else "env"
    if mode is None:
        mode = settings.publication_mode
    reels = donnees.get("reels_actifs")
    details = [
        "Reels auto : "
        + ("oui" if reels is True else "non" if reels is False else "inconnu (clé absente)")
    ]
    if lecture.absent:
        details.append(f"{settings.publication_mode_path.name} absent : valeur du .env")
    if mode == "reel":
        niveau, raison = OK, None
    else:
        niveau = ATTENTION
        raison = (
            "rien n'est publié publiquement : "
            + ("tout est préparé sauf la publication" if mode == "apercu" else "la file ne tourne pas")
        )
    return Voyant(
        "mode",
        "Publication",
        niveau,
        f"{MODES_LISIBLES.get(mode, mode)} ({source})",
        raison,
        details,
    )


# --- Composition et credits -------------------------------------------------


def voyant_composition(db: Session, maintenant: dt.datetime) -> Voyant:
    """La panne la plus couteuse de la semaine, et la plus silencieuse.

    Deux signaux deja ecrits, aucun appel : le dernier `Post` cree, et la
    derniere `creneaux.erreur` de composition -- dans laquelle le message exact
    de l'API Anthropic (« credit balance is too low ») se reconnait. Plus un
    troisieme, indirect mais imparable : des articles ingeres il y a plus de
    deux heures et toujours sans note, alors que chaque passage horaire note
    TOUT ce qui attend.
    """
    dernier_post = _utc(db.scalar(select(func.max(Post.created_at))))
    creneau = db.scalars(
        select(Creneau)
        .where(Creneau.erreur.is_not(None))
        .where(Creneau.erreur.like("composition%"))
        .order_by(Creneau.publier_a.desc())
        .limit(1)
    ).first()
    erreur = creneau.erreur if creneau is not None else None
    quand_erreur = _utc(creneau.publier_a) if creneau is not None else None
    recente = (
        erreur is not None
        and quand_erreur is not None
        and maintenant - quand_erreur <= FENETRE_COMPOSITION
    )
    non_notes = db.scalar(
        select(func.count())
        .select_from(Article)
        .where(Article.relevance_score.is_(None))
        .where(Article.duplicate_of_id.is_(None))
        .where(Article.fetched_at <= maintenant - RETARD_SCORING)
    )

    details = [f"Dernier post composé : {_ecoule(maintenant, dernier_post)}"]
    if erreur is not None:
        details.append(
            f"Dernier échec de composition ({_ecoule(maintenant, quand_erreur)}) : {_ligne(erreur, 300)}"
        )
    if non_notes:
        details.append(
            f"{non_notes} article(s) ingérés il y a plus de 2 h et toujours sans note"
        )

    if recente and SIGNATURE_CREDITS in (erreur or "").lower():
        return Voyant(
            "composition",
            "Composition",
            PANNE,
            "crédits épuisés",
            "crédits Anthropic épuisés : plus aucune composition ne part. "
            "Recharger le solde sur console.anthropic.com.",
            details,
        )
    if non_notes:
        return Voyant(
            "composition",
            "Composition",
            ATTENTION,
            f"{non_notes} non notés",
            f"{non_notes} article(s) attendent une note depuis plus de 2 h alors que chaque "
            "passage horaire note tout : scoring interrompu (crédits, clé API ou réseau).",
            details,
        )
    if recente:
        return Voyant(
            "composition",
            "Composition",
            ATTENTION,
            "dernier échec",
            f"la dernière composition a échoué : {_ligne(erreur, 300)}",
            details,
        )
    if dernier_post is None:
        return Voyant(
            "composition",
            "Composition",
            ATTENTION,
            "aucun post",
            "aucun post n'a jamais été composé.",
            details,
        )
    return Voyant("composition", "Composition", OK, _ecoule(maintenant, dernier_post), None, details)


# --- Les deux jetons Meta ---------------------------------------------------


def voyant_jeton_publication(maintenant: dt.datetime) -> Voyant:
    """Le jeton `IGAA…` : rafraichi tout seul, mais l'echeance se voit ici."""
    chemin = settings.meta_token_path
    lecture = lire_json(chemin)
    if lecture.erreur:
        return Voyant("jeton_publication", "Jeton publication", INCONNU, "inconnu", f"{chemin.name} : {lecture.erreur}")
    if lecture.absent:
        return Voyant(
            "jeton_publication",
            "Jeton publication",
            INCONNU,
            "inconnu",
            f"{chemin.name} absent : le jeton du .env n'a pas encore été rafraîchi, "
            "son échéance n'est donc écrite nulle part.",
        )
    donnees = lecture.donnees or {}
    expire_le = _date_heure(donnees.get("expire_le"))
    echec_le = _date_heure(donnees.get("echec_le"))
    details = [f"Rafraîchi {_ecoule(maintenant, _date_heure(donnees.get('rafraichi_le')))}"]
    if echec_le is not None:
        details.append(f"Dernier rafraîchissement en échec {_ecoule(maintenant, echec_le)}")
    if expire_le is None:
        return Voyant(
            "jeton_publication",
            "Jeton publication",
            INCONNU,
            "inconnu",
            f"{chemin.name} ne porte pas d'échéance (`expire_le` absent).",
            details,
        )
    restant = expire_le - maintenant
    valeur = f"{_jours(restant)} j" if restant > dt.timedelta(0) else "expiré"
    if restant <= dt.timedelta(0):
        return Voyant(
            "jeton_publication",
            "Jeton publication",
            PANNE,
            valeur,
            f"jeton de publication expiré le {expire_le:%d/%m} : plus aucune publication ne part.",
            details,
        )
    if restant <= dt.timedelta(days=7) or echec_le is not None:
        raison = f"expire le {expire_le:%d/%m/%Y}"
        if echec_le is not None:
            raison += " — et le dernier rafraîchissement automatique a échoué"
        return Voyant("jeton_publication", "Jeton publication", ATTENTION, valeur, raison, details)
    return Voyant("jeton_publication", "Jeton publication", OK, valeur, None, details)


def voyant_jeton_sourcing(maintenant: dt.datetime) -> Voyant:
    """Le jeton `EAA…` : 60 jours, renouvele A LA MAIN, aucune alerte a l'ecran.

    Son echeance ne se lit que chez Meta. On ne l'appelle PAS ici : le passage
    horaire la consigne dans `sourcing_token_alert.json` (il appelle
    `debug_token` de toute facon), et ce voyant la relit. Tant qu'aucun passage
    ne l'a consignee, le voyant dit « inconnu » -- jamais « tout va bien ».
    """
    chemin = settings.meta_token_path.parent / FICHIER_ALERTE
    if not settings.meta_fb_access_token:
        return Voyant(
            "jeton_sourcing",
            "Jeton sourcing",
            ATTENTION,
            "absent",
            "META_FB_ACCESS_TOKEN non configuré : les sources Instagram sont sautées en silence.",
        )
    lecture = lire_json(chemin)
    if lecture.erreur:
        return Voyant("jeton_sourcing", "Jeton sourcing", INCONNU, "inconnu", f"{chemin.name} : {lecture.erreur}")
    donnees = lecture.donnees or {}
    releve_le = _date_heure(donnees.get("releve_le"))
    if "expire_le" not in donnees or releve_le is None:
        return Voyant(
            "jeton_sourcing",
            "Jeton sourcing",
            INCONNU,
            "inconnu",
            "échéance jamais relevée : elle est consignée par le passage horaire "
            "(debug_token), et le dashboard ne l'appelle jamais lui-même.",
        )
    details = [f"Échéance relevée {_ecoule(maintenant, releve_le)}"]
    expire_le = _date_heure(donnees.get("expire_le"))
    if expire_le is None:
        return Voyant(
            "jeton_sourcing",
            "Jeton sourcing",
            OK,
            "sans échéance",
            None,
            details + ["Meta ne donne aucune date d'expiration pour ce jeton."],
        )
    restant = expire_le - maintenant
    if restant <= dt.timedelta(0):
        return Voyant(
            "jeton_sourcing",
            "Jeton sourcing",
            PANNE,
            "expiré",
            f"jeton de sourcing expiré le {expire_le:%d/%m} : les sources Instagram sont en panne. {RENOUVELER}",
            details,
        )
    valeur = f"{_jours(restant)} j"
    if restant <= dt.timedelta(days=7):
        return Voyant(
            "jeton_sourcing",
            "Jeton sourcing",
            ATTENTION,
            valeur,
            f"expire le {expire_le:%d/%m/%Y} et ne se renouvelle PAS tout seul. {RENOUVELER}",
            details,
        )
    return Voyant("jeton_sourcing", "Jeton sourcing", OK, valeur, None, details)


# --- Sources : sante et dernier passage -------------------------------------


@dataclass
class EtatSources:
    actives: int = 0
    en_erreur: list[tuple[str, str]] = field(default_factory=list)
    muettes: list[tuple[str, float]] = field(default_factory=list)
    jamais_lues: int = 0
    illisibles: list[str] = field(default_factory=list)
    derniere_lecture: dt.datetime | None = None


def etat_sources(db: Session, maintenant: dt.datetime) -> EtatSources:
    """Ce que `sources.state` dit de la sante des sources -- une seule requete.

    `last_error` y est ecrit a chaque ingestion (`app/pipeline/health.py`) et
    n'etait expose par rien : ni l'API, ni l'ecran.
    """
    etat = EtatSources()
    for source in db.scalars(select(Source).where(Source.active)):
        etat.actives += 1
        brut = source.state
        if brut is not None and not isinstance(brut, dict):
            etat.illisibles.append(source.name)
            continue
        brut = brut or {}
        erreur = brut.get("last_error")
        if erreur:
            etat.en_erreur.append((source.name, _ligne(erreur)))
        dernier_ok = _date_heure(brut.get("last_ok_at"))
        if dernier_ok is None:
            if not erreur:
                etat.jamais_lues += 1
            continue
        if etat.derniere_lecture is None or dernier_ok > etat.derniere_lecture:
            etat.derniere_lecture = dernier_ok
        jours = (maintenant - dernier_ok).total_seconds() / 86400
        if jours >= settings.source_muette_jours:
            etat.muettes.append((source.name, jours))
    etat.en_erreur.sort()
    etat.muettes.sort(key=lambda couple: -couple[1])
    return etat


def voyant_sources(etat: EtatSources) -> Voyant:
    details = [f"{etat.actives} source(s) active(s)"]
    if etat.jamais_lues:
        details.append(f"{etat.jamais_lues} jamais lue(s) avec succès")
    details += [f"⚠ {nom} — {erreur}" for nom, erreur in etat.en_erreur[:3]]
    details += [f"🔇 {nom} — muette depuis {jours:.0f} j" for nom, jours in etat.muettes[:3]]
    if etat.illisibles:
        return Voyant(
            "sources",
            "Sources",
            INCONNU,
            "inconnu",
            f"état illisible pour {len(etat.illisibles)} source(s) : "
            + ", ".join(etat.illisibles[:3]),
            details,
        )
    if not etat.actives:
        return Voyant("sources", "Sources", ATTENTION, "aucune", "aucune source active.", details)
    nombre = len(etat.en_erreur)
    if nombre and nombre * 3 >= etat.actives:
        return Voyant(
            "sources",
            "Sources",
            PANNE,
            f"{nombre}/{etat.actives} en erreur",
            f"{nombre} source(s) sur {etat.actives} en erreur : panne de masse "
            "(quota Graph, jeton, réseau) plutôt qu'un flux cassé.",
            details,
        )
    if nombre or etat.muettes:
        morceaux = []
        if nombre:
            morceaux.append(f"{nombre} en erreur")
        if etat.muettes:
            morceaux.append(f"{len(etat.muettes)} muette(s) depuis plus de {settings.source_muette_jours} j")
        return Voyant(
            "sources",
            "Sources",
            ATTENTION,
            " · ".join(morceaux),
            "; ".join(f"{nom} : {erreur}" for nom, erreur in etat.en_erreur[:2])
            or f"{len(etat.muettes)} source(s) ne rendent plus rien.",
            details,
        )
    return Voyant("sources", "Sources", OK, f"{etat.actives} OK", None, details)


def voyant_passage(db: Session, maintenant: dt.datetime, etat: EtatSources) -> Voyant:
    """Le dernier passage d'ingestion : quand, et combien d'articles.

    Il n'existe nulle part en base : on le deduit du plus recent `last_ok_at`
    consigne par `marquer_sante`. C'est exact a une source pres, et gratuit.
    """
    ingeres = db.scalar(
        select(func.count())
        .select_from(Article)
        .where(Article.fetched_at >= maintenant - dt.timedelta(hours=24))
    )
    details = [f"{ingeres} article(s) ingérés sur 24 h"]
    if etat.en_erreur:
        details.append(f"{len(etat.en_erreur)} source(s) en erreur au dernier passage")
    quand = etat.derniere_lecture
    if quand is None:
        return Voyant(
            "passage",
            "Dernier passage",
            INCONNU,
            "inconnu",
            "aucune source n'a consigné de lecture réussie : l'agent horaire n'a "
            "peut-être jamais tourné sur cette base.",
            details,
        )
    retard = maintenant - quand
    valeur = _ecoule(maintenant, quand)
    if retard >= INGESTION_PANNE:
        return Voyant(
            "passage",
            "Dernier passage",
            PANNE,
            valeur,
            f"aucune ingestion depuis {valeur.removeprefix('il y a ')} alors que l'agent "
            "passe toutes les heures : Mac éteint, agent déchargé ou base injoignable.",
            details,
        )
    if retard >= INGESTION_ATTENTION:
        return Voyant(
            "passage",
            "Dernier passage",
            ATTENTION,
            valeur,
            "le passage est horaire : ce retard veut dire que le Mac a dormi ou que l'agent a sauté.",
            details,
        )
    return Voyant("passage", "Dernier passage", OK, valeur, None, details)


# --- Quota Graph et pauses --------------------------------------------------


def voyant_quota(maintenant: dt.datetime) -> Voyant:
    """La pause de quota Graph, partagee par la veille et le passage horaire.

    Un appel refuse pour quota CONSOMME du quota : la pause est ce qui empeche
    le systeme de s'entretenir lui-meme en panne. Qu'elle soit posee est donc
    un fait grave, et elle n'etait visible que dans le log.
    """
    chemin = settings.meta_token_path.parent / "quota_graph.json"
    lecture = lire_json(chemin)
    details: list[str] = []
    veille = lire_json(settings.meta_token_path.parent / "veille.json")
    if veille.erreur:
        details.append(f"Compteur de la veille : {veille.erreur}")
    elif veille.donnees:
        appels = veille.donnees.get("appels")
        heure = _date_heure(veille.donnees.get("heure"))
        if isinstance(appels, int):
            ligne = f"Veille rapide : {appels} appel(s) sur {settings.veille_appels_max_par_heure}"
            if heure is not None:
                ligne += f" dans l'heure de {heure:%H:%M}"
            details.append(ligne)
    if lecture.erreur:
        return Voyant("quota", "Quota Graph", INCONNU, "inconnu", f"{chemin.name} : {lecture.erreur}", details)
    if lecture.absent or not lecture.donnees:
        return Voyant("quota", "Quota Graph", OK, "libre", None, details + ["Aucune pause jamais posée."])
    jusqu_a = _date_heure(lecture.donnees.get("jusqu_a"))
    motif = _ligne(lecture.donnees.get("motif") or "sans motif")
    par = str(lecture.donnees.get("declaree_par") or "?")
    if jusqu_a is None:
        return Voyant(
            "quota",
            "Quota Graph",
            INCONNU,
            "inconnu",
            f"{chemin.name} ne porte pas de date de fin exploitable.",
            details,
        )
    if jusqu_a > maintenant:
        minutes = max(1, int((jusqu_a - maintenant).total_seconds() // 60))
        return Voyant(
            "quota",
            "Quota Graph",
            PANNE,
            f"pause {minutes} min",
            f"lectures Instagram suspendues jusqu'à {jusqu_a:%H:%M} ({par}) : {motif}",
            details,
        )
    return Voyant(
        "quota",
        "Quota Graph",
        OK,
        "libre",
        None,
        details + [f"Dernière pause levée {_ecoule(maintenant, jusqu_a)} ({par}) : {motif}"],
    )


def voyant_pauses(maintenant: dt.datetime) -> Voyant:
    """yt-dlp et les articles mis a l'ecart faute de visuel.

    Deux etats distincts, meme question : qu'est-ce qui est a l'arret sans que
    personne l'ait decide ?
    """
    dossier = settings.meta_token_path.parent
    details: list[str] = []
    niveau, morceaux, raisons = OK, [], []

    ytdlp = lire_json(dossier / "ytdlp_etat.json")
    if ytdlp.erreur:
        niveau, raisons = INCONNU, raisons + [f"ytdlp_etat.json : {ytdlp.erreur}"]
    elif ytdlp.donnees:
        pause = _date_heure(ytdlp.donnees.get("pause_jusqu_a"))
        erreur = _ligne(ytdlp.donnees.get("derniere_erreur") or "")
        echecs = ytdlp.donnees.get("echecs")
        if pause is not None and pause > maintenant:
            niveau = PANNE
            morceaux.append("yt-dlp en pause")
            raisons.append(f"yt-dlp en pause jusqu'à {pause:%H:%M} : {erreur or 'sans message'}")
        elif erreur:
            details.append(
                f"yt-dlp — dernière erreur {_ecoule(maintenant, _date_heure(ytdlp.donnees.get('echec_le')))} : {erreur}"
            )
        if isinstance(echecs, dict) and echecs:
            details.append(f"yt-dlp : {len(echecs)} lien(s) en échec répété")
        details.append(
            f"yt-dlp — dernier téléchargement {_ecoule(maintenant, _date_heure(ytdlp.donnees.get('dernier_telechargement')))}"
        )

    visuels = lire_json(dossier / "visuel_saute.json")
    if visuels.erreur:
        niveau = PANNE if niveau == PANNE else INCONNU
        raisons.append(f"visuel_saute.json : {visuels.erreur}")
    elif visuels.donnees:
        pauses = visuels.donnees.get("pauses")
        reports = visuels.donnees.get("reports")
        actives = 0
        if isinstance(pauses, dict):
            actives = sum(1 for valeur in pauses.values() if (_date_heure(valeur) or maintenant) > maintenant)
        if actives:
            morceaux.append(f"{actives} article(s) à l'écart")
            if niveau == OK:
                niveau = ATTENTION
            raisons.append(
                f"{actives} article(s) mis à l'écart faute d'un visuel publiable "
                "(ils reviendront d'eux-mêmes)."
            )
        if isinstance(reports, dict) and reports:
            details.append(f"{len(reports)} article(s) avec un report de visuel en cours")

    valeur = " · ".join(morceaux) if morceaux else ("inconnu" if niveau == INCONNU else "aucune")
    return Voyant("pauses", "Pauses", niveau, valeur, "; ".join(raisons) or None, details)


# --- Les creneaux du jour ---------------------------------------------------

SANS_RAISON = "sans raison enregistrée"


def voyant_creneaux(db: Session, maintenant: dt.datetime) -> Voyant:
    """La journee de publication, agregee -- aujourd'hui noyee dans une liste.

    Le jour est celui de Paris, comme les fenetres de publication : un creneau
    de 21 h n'appartient pas au lendemain.
    """
    fuseau = ZoneInfo(settings.publication_fuseau)
    jour = maintenant.astimezone(fuseau).date()
    debut = dt.datetime.combine(jour, dt.time(0), tzinfo=fuseau).astimezone(dt.timezone.utc)
    fin = debut + dt.timedelta(days=1)
    creneaux = list(
        db.scalars(
            select(Creneau)
            .where(Creneau.publier_a >= debut)
            .where(Creneau.publier_a < fin)
            .order_by(Creneau.publier_a)
        )
    )
    if not creneaux:
        return Voyant(
            "creneaux",
            "Créneaux du jour",
            ATTENTION,
            "aucun",
            "aucun créneau aujourd'hui : la file n'a pas été calculée.",
        )
    publies = [c for c in creneaux if c.etat == EtatCreneau.PUBLIE]
    apercus = [c for c in creneaux if c.etat == EtatCreneau.APERCU]
    manques = [c for c in creneaux if c.etat == EtatCreneau.MANQUE]
    echecs = [c for c in creneaux if c.etat == EtatCreneau.ECHEC]
    attente = {EtatCreneau.LIBRE, EtatCreneau.PROGRAMME, EtatCreneau.GELE}
    a_venir = [c for c in creneaux if c.etat in attente and _utc(c.publier_a) > maintenant]
    details = [
        f"{len(publies)} publié(s), {len(apercus)} en aperçu, {len(manques)} manqué(s), "
        f"{len(echecs)} en échec, {len(a_venir)} à venir sur {len(creneaux)}"
    ]
    for creneau in manques + echecs:
        quand = _utc(creneau.publier_a)
        details.append(f"⚠ {quand.astimezone(fuseau):%H:%M} — {_ligne(creneau.erreur or SANS_RAISON, 300)}")
    valeur = f"{len(publies)}/{len(creneaux)} publiés"
    if manques:
        sans = sum(1 for c in manques if not c.erreur)
        raison = f"{len(manques)} créneau(x) manqué(s) aujourd'hui"
        if sans:
            raison += f", dont {sans} sans raison enregistrée"
        niveau = PANNE if len(manques) * 2 >= len(creneaux) else ATTENTION
        return Voyant("creneaux", "Créneaux du jour", niveau, valeur, raison + ".", details)
    if echecs:
        return Voyant(
            "creneaux",
            "Créneaux du jour",
            ATTENTION,
            valeur,
            f"{len(echecs)} créneau(x) en échec : {_ligne(echecs[0].erreur or SANS_RAISON, 300)}",
            details,
        )
    return Voyant("creneaux", "Créneaux du jour", OK, valeur, None, details)


# --- L'assemblage -----------------------------------------------------------


@dataclass(frozen=True)
class Supervision:
    niveau: str
    releve_le: dt.datetime
    voyants: list[Voyant]


def superviser(db: Session, maintenant: dt.datetime | None = None) -> Supervision:
    """Tous les voyants, et le pire d'entre eux.

    Chaque voyant est enveloppe : une exception imprevue (schema change, etat
    corrompu d'une facon non anticipee) devient un voyant « inconnu » qui porte
    le type de l'exception. Elle ne doit ni faire disparaitre le voyant -- une
    case vide se lit « rien a signaler » -- ni rendre une 500 qui priverait
    l'utilisateur des huit autres.
    """
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    sources = None
    try:
        sources = etat_sources(db, maintenant)
    except Exception:  # noqa: BLE001 -- une base muette ne vide pas le bandeau
        logger.exception("Etat des sources illisible")

    taches: list[tuple[str, str, object]] = [
        ("mode", "Publication", lambda: voyant_mode(maintenant)),
        ("composition", "Composition", lambda: voyant_composition(db, maintenant)),
        ("creneaux", "Créneaux du jour", lambda: voyant_creneaux(db, maintenant)),
        ("passage", "Dernier passage", lambda: voyant_passage(db, maintenant, sources)),
        ("sources", "Sources", lambda: voyant_sources(sources)),
        ("quota", "Quota Graph", lambda: voyant_quota(maintenant)),
        ("pauses", "Pauses", lambda: voyant_pauses(maintenant)),
        ("jeton_publication", "Jeton publication", lambda: voyant_jeton_publication(maintenant)),
        ("jeton_sourcing", "Jeton sourcing", lambda: voyant_jeton_sourcing(maintenant)),
    ]
    voyants: list[Voyant] = []
    for cle, libelle, calcul in taches:
        if sources is None and cle in {"sources", "passage"}:
            voyants.append(
                Voyant(cle, libelle, INCONNU, "inconnu", "état des sources illisible en base.")
            )
            continue
        try:
            voyants.append(calcul())
        except Exception as exc:  # noqa: BLE001 -- jamais de voyant manquant
            logger.exception("Voyant %s illisible", cle)
            voyants.append(
                Voyant(cle, libelle, INCONNU, "inconnu", f"lecture impossible ({type(exc).__name__}).")
            )
    return Supervision(niveau=niveau_global(voyants), releve_le=maintenant, voyants=voyants)
