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
import re
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, Creneau, EtatCreneau, Post, Source
from app.pipeline.credits_anthropic import chemin_etat as chemin_etat_credits
from app.pipeline.credits_anthropic import lire_etat as lire_etat_credits
from app.pipeline.health import SIGNATURE_CREDITS
from app.schedule.passage import FICHIER_PASSAGE
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
#
# MAIS `fetched_at` n'est PAS la date a laquelle l'article est devenu notable,
# et cette confusion faisait crier au loup (mesure du 2026-10-02 : le voyant
# affichait « 4 article(s) attendent une note depuis plus de 2 h ... scoring
# interrompu » alors que le passage precedent venait d'en noter 57 sans une
# seule erreur). Les quatre etaient des DOUBLONS -- un doublon n'est jamais
# score, c'est l'economie que `link_duplicates` existe pour faire -- dont le
# representant venait d'etre PURGE : `articles.duplicate_of_id` est
# `ON DELETE SET NULL`, donc la purge les a rendus notables a l'instant meme
# ou elle s'est executee, c'est-a-dire APRES le scoring du meme passage. Leur
# `fetched_at` avait 24 h, leur attente reelle quelques minutes, et le passage
# suivant les a notes. Verifie sur les quatre : ils portaient tous une
# `event_observations` creee par `cluster.lier`, donc ils avaient bien ete lies.
# Rien dans le schema ne date « devenu notable » -- et l'ajouter ne vaut pas
# une colonne. Le voyant mesure donc la PREUVE au lieu de la duree : voir
# `articles_jamais_atteints_par_le_scoring`.
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
    `tests/socle/test_supervision.py` verrouille, parce qu'un `or OK` bien
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


# --- L'agent de publication : passe-t-il ? ----------------------------------

# Le demarrage de CE processus (le dashboard) sert de borne : voir `reprise_machine`.
DEMARRAGE_PROCESSUS = dt.datetime.now(dt.timezone.utc)


def seuil_agent_publication() -> dt.timedelta:
    """Au-dela, l'agent aurait du finir un passage. DERIVE des reglages, jamais ecrit.

    L'heure consignee est celle de la FIN, et launchd ne relance rien tant qu'un
    passage tourne : un agent sain peut donc rester muet un intervalle, plus la
    duree d'un passage long. Celle-ci n'a pas de borne generale, mais ses deux
    attentes connues en ont une : l'attente de l'heure affichee
    (`publication_avance_s`) et la patience du tunnel
    (`publication_tunnel_budget_s`). Le second intervalle est la marge pour ce qui
    n'est pas borne (yt-dlp, ffmpeg, composition) et il evite de conclure sur un
    seul passage rate.

    35 min 30 aux reglages actuels, DEDUIT DU CODE : aucune duree de passage n'est
    journalisee, donc aucune n'a ete mesuree. Si ce voyant crie sur un passage
    legitime, c'est une mesure qu'il faut faire, pas ce nombre qu'il faut pousser.
    """
    return dt.timedelta(
        seconds=2 * settings.publication_intervalle_s
        + settings.publication_avance_s
        + settings.publication_tunnel_budget_s
    )


def reprise_machine() -> dt.datetime | None:
    """Depuis quand les agents PEUVENT-ils tourner ? `None` si on ne sait pas.

    LE MAC DORT, ET C'EST NORMAL. Au reveil le dashboard (`KeepAlive`) repond tout
    de suite, alors que l'agent n'a pas encore fini le passage que launchd rattrape
    et que son fichier a la duree de la nuit : sans cette date, le bandeau crierait
    a la panne a CHAQUE ouverture de l'ecran, ce qui est la faute la plus grave
    qu'il puisse commettre (voir le voyant de composition, 2026-10-02). Et le bandeau
    tourne lui aussi depuis le reveil : il ne peut rien se rappeler de sa nuit, donc
    il demande au noyau au lieu de deviner.

    `sysctl` est local et ne coute rien. Observe le 2026-10-30 sur la machine :
    `kern.waketime` valait exactement la derniere ligne `Wake` de `pmset -g log`.
    `sec = 0` n'a pas ete observe, mais une machine sans date de reveil retombe sur
    `kern.boottime`. `DEMARRAGE_PROCESSUS` couvre l'ecran de connexion : un Mac
    demarre la nuit et ouvert le matin a un `boottime` ancien, or les agents launchd
    ne tournent qu'apres l'ouverture de session -- comme ce dashboard.

    Limite, non verifiee : `pmset -g log` compte aussi des `DarkWake to FullWake`
    (6 lignes, contre 893 lignes `Wake` ou `DarkWake`, dans le journal lu le
    2026-10-30). Si `kern.waketime` n'est pas mis a jour a cette transition, un
    reveil precede d'une longue veille sombre serait date trop tot.
    """
    try:
        sortie = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "kern.waketime", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    secondes = [int(valeur) for valeur in re.findall(r"\bsec = (\d+)", sortie)]
    if not any(secondes):
        return None
    return max(dt.datetime.fromtimestamp(max(secondes), dt.timezone.utc), DEMARRAGE_PROCESSUS)


def _agent_a_eu_le_temps(maintenant: dt.datetime) -> bool | None:
    """La machine est-elle eveillee depuis assez longtemps pour conclure ?

    `None` : on ne peut pas separer un agent mort d'un Mac qui dormait. Une reprise
    dans le futur (horloge, ou un `maintenant` de test) se range ici, et jamais dans
    « elle vient de se reveiller ».
    """
    reprise = reprise_machine()
    if reprise is None or reprise > maintenant:
        return None
    return maintenant - reprise >= seuil_agent_publication()


def voyant_agent_publication(maintenant: dt.datetime) -> Voyant:
    """L'agent de publication passe-t-il ? Un voyant A PART, pas dans « Publication ».

    `voyant_mode` repond « publie-t-on vraiment ? », une DECISION, et sa valeur est
    le mode. Celui-ci repond « la machine tourne-t-elle ? », un FAIT. Les fondre
    donnerait une seule pastille a deux questions : `Aperçu` (ATTENTION voulue) y
    masquerait un agent mort, et il perdrait sa valeur, l'age du passage.

    Il vaut pour tous les modes, `arret` compris : on doit savoir que l'agent est
    mort AVANT de repasser en reel. Trop vieux sur une machine reveillee depuis
    assez longtemps : PANNE. Trop vieux juste apres un reveil : INCONNU, ni vert (on
    n'a aucune mesure fraiche) ni rouge (rien n'accuse l'agent). Faux negatif assume :
    un Mac qui se rendort avant `seuil_agent_publication` ne conclut jamais.
    """
    cle, libelle = "agent_publication", "Agent de publication"
    chemin = settings.meta_token_path.parent / FICHIER_PASSAGE
    lecture = lire_json(chemin)
    if lecture.erreur:
        return Voyant(cle, libelle, INCONNU, "inconnu", f"{chemin.name} : {lecture.erreur}")
    if lecture.absent:
        return Voyant(cle, libelle, INCONNU, "inconnu", "aucun passage consigné : l'agent n'a jamais écrit sa fin.")
    donnees = lecture.donnees or {}
    fin = _date_heure(donnees.get("fin_le"))
    if fin is None:
        return Voyant(cle, libelle, INCONNU, "inconnu", f"{chemin.name} ne porte pas d'heure de fin exploitable.")
    if fin > maintenant:
        return Voyant(cle, libelle, INCONNU, "inconnu", f"{chemin.name} est daté du futur : horloge à vérifier.")
    age = maintenant - fin
    seuil = seuil_agent_publication()
    valeur = _ecoule(maintenant, fin)
    details = [f"Mode au dernier passage : {MODES_LISIBLES.get(donnees.get('mode'), 'inconnu')}"]
    panne = donnees.get("panne")
    if age < seuil:
        if panne:
            return Voyant(
                cle, libelle, ATTENTION, valeur,
                f"le dernier passage n'a pas abouti : {_ligne(panne, 300)}.", details,
            )
        return Voyant(cle, libelle, OK, valeur, None, details)
    minutes = int(seuil.total_seconds() // 60)
    a_eu_le_temps = _agent_a_eu_le_temps(maintenant)
    if a_eu_le_temps is None:
        return Voyant(
            cle, libelle, INCONNU, valeur,
            f"dernier passage {valeur}, et la date du dernier réveil est illisible : "
            "impossible de dire si le Mac dormait ou si l'agent est mort.",
            details,
        )
    if not a_eu_le_temps:
        return Voyant(
            cle, libelle, INCONNU, valeur,
            f"le Mac vient de se réveiller et le dernier passage date de {valeur.removeprefix('il y a ')} : "
            f"l'agent a {minutes} min pour repasser avant qu'on conclue.",
            details,
        )
    return Voyant(
        cle, libelle, PANNE, valeur,
        f"aucun passage fini depuis {valeur.removeprefix('il y a ')} sur un Mac éveillé, alors que l'agent "
        f"passe toutes les {settings.publication_intervalle_s // 60} min : agent déchargé de launchd, "
        "tué ou bloqué. Plus rien ne publie et aucun créneau n'est marqué manqué. "
        "Vérifier avec `uv run python -m scripts.install_agents --status`.",
        details,
    )


# --- Composition et credits -------------------------------------------------


def articles_jamais_atteints_par_le_scoring(db: Session, maintenant: dt.datetime) -> int:
    """Les articles dont le scoring n'a PROUVABLEMENT jamais eu l'occasion.

    Le voyant affirme « le scoring ne tourne plus ». La preuve de cette
    affirmation n'est pas la duree d'attente d'un article (voir
    `RETARD_SCORING` : `fetched_at` ne dit pas quand l'article est devenu
    notable) mais ceci : `score_pending` prend les articles notables par
    `fetched_at` CROISSANT et sans limite au passage horaire. Donc si un
    article ingere a T a recu une note, le scoring a tourne apres T -- et tout
    article notable ingere avant T aurait ete servi AVANT lui.

    On ne compte donc que les articles sans note dont le `fetched_at` est
    STRICTEMENT plus recent que le dernier `fetched_at` deja note. Un doublon
    libere par la purge est, lui, plus ANCIEN que ce repere (c'est tout l'objet
    du correctif) : il n'est pas compte, et le passage suivant le notera.

    Rien de note du tout -> aucun repere, on compte tout : « on ne sait pas »
    ne doit jamais se degrader en « tout va bien » (regle 1 du module).

    Faux negatif assume : `run_veille` note au plus `veille_scoring_max` (20)
    articles toutes les 5 min, donc un arriere peut faire avancer le repere
    au-dela d'articles encore en attente. Le passage HORAIRE, lui, n'a pas de
    limite et resorbe cet arriere dans l'heure -- un trou d'une heure vaut
    mieux qu'un faux positif permanent, qui apprend a ne plus regarder le
    bandeau.
    """
    repere = db.scalar(
        select(func.max(Article.fetched_at)).where(Article.relevance_score.is_not(None))
    )
    stmt = (
        select(func.count())
        .select_from(Article)
        .where(Article.relevance_score.is_(None))
        .where(Article.duplicate_of_id.is_(None))
        .where(Article.fetched_at <= maintenant - RETARD_SCORING)
    )
    if repere is not None:
        stmt = stmt.where(Article.fetched_at > repere)
    return db.scalar(stmt) or 0


def articles_leur_propre_doublon(db: Session) -> int:
    """Les articles dont `duplicate_of_id` pointe sur leur propre id.

    Une CORRUPTION, pas un retard : un tel article se presente comme un
    doublon, donc `score_pending` (qui filtre `duplicate_of_id IS NULL`) ne le
    notera jamais et `store._articles_publiables` ne le proposera jamais. Il
    disparaitra a la purge sans que rien ne l'ait dit -- c'est exactement ce
    qui est arrive aux articles 21627, 21709 et 21599 le 2026-10-02 (voir
    `cibles_a_repointer` dans `app/pipeline/cluster.py`).

    Compte a part du retard de scoring, et SANS le filtre
    `duplicate_of_id IS NULL` qui le ferait disparaitre : la cause du blocage
    n'est pas le scoring, la reparation n'est pas d'attendre, et dire « scoring
    interrompu » sur ce cas serait le genre de message faux que ce bandeau
    existe pour ne plus produire. Le defaut est corrige a la source depuis le
    2026-10-02 ; ce compteur est la vigie qui dirait qu'un autre chemin l'a
    refait.
    """
    return db.scalar(
        select(func.count())
        .select_from(Article)
        .where(Article.duplicate_of_id == Article.id)
    ) or 0


def voyant_composition(db: Session, maintenant: dt.datetime) -> Voyant:
    """La panne la plus couteuse de la semaine, et la plus silencieuse.

    Deux signaux deja ecrits, aucun appel : le dernier `Post` cree, et la
    derniere `creneaux.erreur` de composition -- dans laquelle le message exact
    de l'API Anthropic (« credit balance is too low ») se reconnait. Plus un
    troisieme, indirect mais imparable : des articles que le scoring n'a
    PROUVABLEMENT jamais atteints
    (`articles_jamais_atteints_par_le_scoring` -- et sa docstring dit pourquoi
    « ingere il y a plus de deux heures » ne suffisait pas et faisait crier au
    loup). Plus un quatrieme depuis le 2026-10-02 : les articles leur propre
    doublon, bloques pour de bon et que le filtre `duplicate_of_id IS NULL` du
    troisieme ne peut pas voir.

    Depuis le 2026-10-31, le solde se lit d'abord dans `credits_anthropic.json`,
    consigne a chaque reponse ou refus de Claude : `creneaux.erreur` ne dit rien tant
    qu'aucune composition n'a ete tentee, et disait encore « épuisés » 36 h apres la
    recharge. Fichier absent ou illisible : INCONNU, jamais vert.
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
    non_notes = articles_jamais_atteints_par_le_scoring(db, maintenant)
    reflexifs = articles_leur_propre_doublon(db)

    lecture = lire_json(chemin_etat_credits())
    etat_credits = None
    credits_inconnus = None
    if lecture.absent:
        credits_inconnus = "aucun appel à Claude consigné pour l'instant"
    elif lecture.erreur:
        credits_inconnus = lecture.erreur
    else:
        try:
            etat_credits = lire_etat_credits(lecture.donnees)
        except ValueError:
            credits_inconnus = "contenu inattendu"
    # Le dernier evenement est un succes : une erreur de composition, meme recente,
    # parle d'un solde qui a change depuis.
    resolu = etat_credits is not None and not etat_credits.epuises
    refus_en_base = recente and SIGNATURE_CREDITS in (erreur or "").lower()

    details = [f"Dernier post composé : {_ecoule(maintenant, dernier_post)}"]
    if erreur is not None:
        details.append(
            f"Dernier échec de composition ({_ecoule(maintenant, quand_erreur)}) : {_ligne(erreur, 300)}"
        )
    if non_notes:
        details.append(
            f"{non_notes} article(s) ingérés il y a plus de 2 h que le scoring "
            "n'a jamais atteints"
        )
    # Toujours en détail, même quand une branche plus grave prend le titre :
    # une corruption ne doit jamais être masquée par une panne passagère.
    if reflexifs:
        details.append(
            f"{reflexifs} article(s) sont leur propre doublon (lien de dédup corrompu)"
        )

    if etat_credits is not None:
        if etat_credits.succes_le is not None:
            details.append(f"Dernière réponse de Claude : {_ecoule(maintenant, etat_credits.succes_le)}")
        if etat_credits.refus_le is not None:
            details.append(f"Dernier refus pour crédits : {_ecoule(maintenant, etat_credits.refus_le)}")
    else:
        details.append(f"État des crédits inconnu : {credits_inconnus}")

    if (etat_credits is not None and etat_credits.epuises) or (refus_en_base and not resolu):
        # Le gel et la publication n'appellent pas Claude : ce qui est deja compose part
        # encore. Le dire evite de croire perdu un creneau qui publiera.
        return Voyant(
            "composition",
            "Composition",
            PANNE,
            "crédits épuisés",
            "crédits Anthropic épuisés : rien ne se note ni ne se compose, plus aucun post "
            "ne partira, hormis ceux déjà composés. Recharger le solde sur console.anthropic.com.",
            details,
        )
    if credits_inconnus is not None:
        attente = f" {non_notes} article(s) attendent aussi une note." if non_notes else ""
        return Voyant(
            "composition",
            "Composition",
            INCONNU,
            "crédits inconnus",
            f"état des crédits Anthropic inconnu ({credits_inconnus}).{attente}",
            details,
        )
    if non_notes:
        return Voyant(
            "composition",
            "Composition",
            ATTENTION,
            f"{non_notes} non notés",
            f"{non_notes} article(s) attendent une note depuis plus de 2 h alors que chaque "
            "passage horaire note tout : scoring interrompu (clé API ou réseau, le dernier "
            "appel à Claude n'a pas été refusé pour crédits).",
            details,
        )
    if reflexifs:
        return Voyant(
            "composition",
            "Composition",
            ATTENTION,
            f"{reflexifs} doublon(s) de soi",
            f"{reflexifs} article(s) pointent sur eux-mêmes comme doublon : ils ne seront "
            "jamais notés ni publiés. Réparer avec "
            "`uv run python -m scripts.reparations.reparer_doublons_reflexifs --apply`.",
            details,
        )
    if recente and not (refus_en_base and resolu):
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
                f"{actives} article(s) mis à l'écart : pas de visuel publiable, ou correction refusée au gel "
                "(ils reviendront d'eux-mêmes)."
            )
        if isinstance(reports, dict) and reports:
            details.append(f"{len(reports)} article(s) avec un report de visuel en cours")

    valeur = " · ".join(morceaux) if morceaux else ("inconnu" if niveau == INCONNU else "aucune")
    return Voyant("pauses", "Pauses", niveau, valeur, "; ".join(raisons) or None, details)


# --- Les creneaux du jour ---------------------------------------------------

SANS_RAISON = "sans raison enregistrée"


def _mode_lu() -> str | None:
    """Le mode applique par l'agent, None s'il ne se lit pas. Meme fichier que
    `voyant_mode`, et pour la meme raison : `mode_effectif` cache une lecture ratee.
    Un fichier absent, ou sans cle `mode` (il porte aussi `reels_actifs`), n'est pas
    illisible : c'est le repli voulu sur `.env`."""
    lecture = lire_json(settings.publication_mode_path)
    if lecture.erreur:
        return None
    mode = (lecture.donnees or {}).get("mode", settings.publication_mode)
    return mode if mode in MODES_LISIBLES else None


def voyant_creneaux(db: Session, maintenant: dt.datetime) -> Voyant:
    """Le bilan du jour, plus ce que l'agent a laisse derriere lui.

    MANQUE n'est ecrit QUE par l'agent (`runner._marquer_manques`). Un agent mort
    laissait donc les creneaux passes en PROGRAMME ou GELE, hors de tous les
    compteurs : « 0/6 publiés » en vert. On les constate ici, tous jours confondus,
    pour que le voyant ne redevienne pas vert a minuit.

    Au-dela de `publication_retard_max_minutes`, le creneau n'est plus publiable,
    donc plus jamais vert. Il n'accuse l'agent (PANNE) qu'une fois que celui-ci a eu
    `seuil_agent_publication` pour l'ecrire, depuis le depassement ET depuis le
    reveil : un agent sain ne repasse que toutes les 10 minutes, et un Mac rouvert a
    midi trouve la matinee entiere dans cet etat sans que rien soit casse. Entre les
    deux : ATTENTION, car le creneau est bel et bien rate.
    """
    bilan = _bilan_du_jour(db, maintenant)
    retard_max = dt.timedelta(minutes=settings.publication_retard_max_minutes)
    orphelins = list(
        db.scalars(
            select(Creneau)
            .where(Creneau.publier_a < maintenant - retard_max)
            .where(Creneau.etat.in_([EtatCreneau.PROGRAMME, EtatCreneau.GELE]))
            .order_by(Creneau.publier_a)
        )
    )
    if not orphelins:
        return bilan
    fuseau = ZoneInfo(settings.publication_fuseau)
    nombre = len(orphelins)
    mode = _mode_lu()
    constat = (
        f"{nombre} créneau(x) passé(s) de plus de {settings.publication_retard_max_minutes} min "
        "et jamais marqué(s) manqué(s)"
    )
    a_eu_le_temps = _agent_a_eu_le_temps(maintenant)
    seuil = seuil_agent_publication()
    # `tourner` sort avant `_marquer_manques` en arret : accuser l'agent serait un voyant
    # qui ment dans un mode legitime. ATTENTION et non OK : ces creneaux sont bel et bien rates.
    if mode is None:
        niveau = INCONNU
        raison = f"{constat}, et le mode de publication est illisible : on ne sait pas si l'agent devait les traiter."
    elif mode == "arret":
        niveau = ATTENTION
        raison = (
            f"{constat} : mode Arrêt, l'agent ne traite pas la file. Ce n'est pas une panne : "
            "ils seront marqués manqués au premier passage hors arrêt."
        )
    elif a_eu_le_temps is None:
        niveau = INCONNU
        raison = f"{constat}, et la date du dernier réveil est illisible : Mac endormi ou agent mort, on ne sait pas."
    elif a_eu_le_temps and any(maintenant - (_utc(c.publier_a) + retard_max) >= seuil for c in orphelins):
        niveau = PANNE
        raison = f"{constat} : l'agent de publication ne les traite plus."
    elif a_eu_le_temps:
        niveau = ATTENTION
        raison = f"{constat} : le prochain passage de l'agent doit les écrire."
    else:
        niveau = ATTENTION
        raison = f"{constat} : le Mac vient de se réveiller, le prochain passage de l'agent doit les écrire."
    lignes = [
        f"⚠ {_utc(c.publier_a).astimezone(fuseau):%d/%m %H:%M} — encore {c.etat.value}, jamais traité"
        for c in orphelins[:5]
    ]
    if GRAVITE[bilan.niveau] > GRAVITE[niveau]:
        return replace(bilan, details=bilan.details + lignes)
    return Voyant("creneaux", "Créneaux du jour", niveau, bilan.valeur, raison, bilan.details + lignes)


def _bilan_du_jour(db: Session, maintenant: dt.datetime) -> Voyant:
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


def voyant_relecture(db: Session, maintenant: dt.datetime) -> Voyant:
    """Qu'est-ce qui part bientot et attend un oeil ?

    Ni une alarme ni une attente : ce voyant reste `ok` meme quand des posts
    attendent, parce qu'un post non relu part quand meme a l'heure (la relecture
    est une OPTION, voir `app/schedule/relecture.py`). Le peindre en ATTENTION
    apprendrait a lire « il faut agir » la ou il n'y a rien a faire -- et le
    bandeau a deja paye cette lecon le 2026-10-02, avec un voyant de composition
    qui criait au loup pendant que le scoring notait 57 articles.

    Il dit en revanche les deux choses que l'ecran ne disait pas : COMBIEN, et
    POUR QUAND -- de quoi decider d'ouvrir la Programmation ou pas.

    Il ne juge plus le debordement de la cover (decision du 2026-10-30). Il lisait
    `render_report`, que seul `POST /posts/{id}/render` ecrit et qu'aucun bouton
    n'appelle : absent, il valait « ne deborde pas », donc vert sur une mesure qui
    n'existe pas, pendant que la fiche disait « inconnu ». Et son message
    « partiront ainsi » etait faux : le gel refuse une cover qui deborde
    (`preparation.py`). La vraie mesure est `/covers`, a l'ouverture de la fiche.
    """
    from app.api.relecture import creneaux_a_relire

    fuseau = ZoneInfo(settings.publication_fuseau)
    attendus = creneaux_a_relire(db, maintenant)
    if not attendus:
        return Voyant(
            "relecture", "Relecture", OK, "rien en attente",
            None,
            ["aucun post ne part dans les "
             f"{settings.publication_relecture_avance_minutes} prochaines minutes."],
        )
    details = []
    for creneau, post in attendus:
        quand = _utc(creneau.publier_a).astimezone(fuseau)
        accroche = _ligne(((post.slides or [{}])[0] or {}).get("headline") or f"post {post.id}", 120)
        details.append(
            f"{quand:%H:%M} — {creneau.format.value} — post {post.id} — {accroche.replace('**', '')}"
        )
    prochain = _utc(attendus[0][0].publier_a).astimezone(fuseau)
    valeur = f"{len(attendus)} à relire, prochain {prochain:%H:%M}"
    return Voyant("relecture", "Relecture", OK, valeur, None, details)


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
    l'utilisateur de tous les autres.
    """
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    sources = None
    try:
        sources = etat_sources(db, maintenant)
    except Exception:  # noqa: BLE001 -- une base muette ne vide pas le bandeau
        logger.exception("Etat des sources illisible")

    taches: list[tuple[str, str, object]] = [
        ("mode", "Publication", lambda: voyant_mode(maintenant)),
        ("agent_publication", "Agent de publication", lambda: voyant_agent_publication(maintenant)),
        ("composition", "Composition", lambda: voyant_composition(db, maintenant)),
        ("creneaux", "Créneaux du jour", lambda: voyant_creneaux(db, maintenant)),
        ("relecture", "Relecture", lambda: voyant_relecture(db, maintenant)),
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
