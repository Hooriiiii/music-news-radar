"""Arbitrage Claude des reprises douteuses contre l'archive (Task 7, 2026-09-21).

`archive_similarity_from_tokens` (bande "certaine", seuil `cluster_archive_seuil`
= 0.8) et `title_similarity_from_tokens` (le recouvrement, seuil 0.65) se
contredisent sur une bande entiere : 15 reprises plausibles mesurees en
re-revue, 11 y passent entre les mailles (aucun seuil ne peut a la fois
attraper les reprises et epargner les actus neuves -- voir
`cluster.archive_similarity_from_tokens`). C'est cette bande, et elle seule,
que Claude tranche : `cluster.link_duplicates` n'appelle ce module que pour un
article dont le score contre un representant d'archive est sous le seuil
certain mais au-dessus du seuil de recouvrement.

Le verdict est memorise par PAIRE (les deux ids d'article, ordre indifferent)
dans un fichier JSON a cote du jeton -- meme mecanique que
`illustration_cache_path` / `visuels_web_cache_path`. Une panne (cle absente,
credits epuises, reseau mort, reponse hors schema) n'est JAMAIS memorisee :
seule une reponse effectivement rendue par Claude, conforme au schema, l'est,
sinon une panne passagere figerait "different" pour toujours et enterrerait
une actu neuve sans retour possible.

**Tour de correction 1/5 (revue sur le vrai SDK, via `MockTransport`).**
Trois defauts corriges ici :

1. Le SDK (0.116.0) retire `maxLength` du schema JSON envoye au modele
   (`transform_schema` le deplace dans la description) : la contrainte
   `Field(max_length=...)` d'avant n'etait donc PAS appliquee cote serveur,
   seulement revalidee cote client par Pydantic -- APRES facturation. Une
   reponse systematiquement trop longue payait a chaque appel, sans jamais
   memoriser de verdict, et pouvait geler tout l'arbitrage 48h si les paires
   les plus anciennes (traitees en premier) etaient celles qui debordaient.
   `VerdictArbitrage.raison` n'a donc plus de contrainte de longueur : un
   validateur la TRONQUE au lieu de rejeter une reponse par ailleurs valide.
   En complement, un compteur de TENTATIVES par paire (distinct des verdicts,
   `Cache.tentatives`) abandonne apres `cluster_arbitrage_tentatives_max`
   echecs SANS jamais poser de lien -- une panne systematique sur une paire
   ne bloque plus les autres indefiniment. Et `cluster.link_duplicates` sert
   d'abord les paires JAMAIS tentees avant de rejouer celles deja en echec :
   voir le commentaire sur les deux passes dans ce module-la.
2. Le temporaire d'ecriture portait un nom FIXE (`.tmp`) : deux processus qui
   ecrivent (le passage horaire et `scripts.marquer_doublon_manuel` lance
   pendant ce passage) auraient pu se marcher dessus. Nom de temporaire PAR
   PROCESSUS desormais (`tempfile.mkstemp`), meme mecanique que
   `app/pipeline/quota_graph.py`.
3. `link_duplicates` lisait le cache une fois au debut du passage et le
   reecrivait en BLOC a la fin : un verdict manuel pose PENDANT le passage
   (fenetre de plusieurs minutes, jusqu'a `cluster_arbitrage_max_par_passage`
   appels) etait donc EFFACE par cette reecriture. `fusionner_et_ecrire`
   relit le disque juste avant d'ecrire et ne laisse jamais une decision
   automatique de ce passage remplacer un verdict manuel pose entre-temps.

Un verdict MANUEL (pose par `scripts.marquer_doublon_manuel`) prime sur tout
rapprochement automatique ulterieur, y compris sur la bande certaine : c'est ce
qui rend enfin reelle la procedure de reparation d'un faux positif d'archive
(l'ancienne procedure de CLAUDE.md -- effacer `duplicate_of_id` et
`file_exclu_raison` -- ne tenait pas, le passage suivant relie l'article tant
qu'il reste dans la fenetre de 48 h de sa vie publiable). `marquer_manuel` leve
si l'ecriture n'est pas confirmee par une relecture : le script appelant ne
doit JAMAIS delier un article en base sur la foi d'un verdict qui n'a pas ete
durablement pose (meme defaut que l'ancienne procedure, en silence cette
fois-ci).

**Tour de correction 2/5 (re-revue).** Deux defauts de plus, et un residu :

1. Le cas le PLUS IMPORTANT du chantier : l'utilisateur marque une paire
   "differente" PENDANT que Claude, sur ce meme appel, repond "meme actu".
   Le cache retenait bien le manuel comme gagnant, mais le LIEN etait quand
   meme pose et commite dans le meme passage (rien n'etait encore lie au
   moment ou l'utilisateur a agi). Remede en DEUX parties, dans
   `cluster.link_duplicates` : `appliquer()` RELIT le disque juste avant de
   poser un lien tranche par Claude (voir `cluster.py`) ; et une
   AUTO-REPARATION tourne en tete de CHAQUE passage, ici meme, qui defait
   tout lien deja present en base dont la paire porte un verdict manuel
   "different" -- quelle que soit la course qui l'a pose. Le verdict manuel
   CONVERGE, il ne parie plus sur l'ordre de deux ecritures.
2. `lire_cache` confondait un fichier ABSENT (cache vide, normal) et un
   fichier PRESENT mais illisible (corrompu) -- les deux rendaient un cache
   vide. Une fonction qui lit PUIS ecrit (`fusionner_et_ecrire`,
   `marquer_manuel`) prenait donc un cache corrompu pour un cache vide et
   l'ecrasait, perdant tous les verdicts existants, manuels compris.
   `_lire_pour_ecriture` distingue les deux : absent reste un cache vide,
   present-et-illisible LEVE -- `fusionner_et_ecrire` capture cette
   exception et n'ecrit RIEN (annulation complete, erreur journalisee),
   `marquer_manuel` la laisse remonter (le script sort en erreur, comme I3).
"""

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from pydantic import BaseModel, field_validator

from app.config import settings
from app.models import Article, ArticleStatus

logger = logging.getLogger(__name__)

# Changer cette valeur sans fournir de migration (relire l'ancien format et le
# reecrire au nouveau) a DEUX effets, parce qu'un fichier d'une autre version
# est traite comme illisible (`_cache_depuis_json`) :
# - en LECTURE, `lire_cache` rend un cache VIDE : les verdicts manuels
#   « different » existants cessent de proteger, et les liens qu'ils
#   interdisaient se reforment au passage suivant -- c'est la consequence qui
#   compte pour l'utilisateur ;
# - en ECRITURE, `_lire_pour_ecriture` leve et plus rien ne s'ecrit (ni
#   verdict ni tentative) : les memes paires sont repayees a chaque passage.
# Supprimer le fichier « pour debloquer » efface les verdicts manuels pour de
# bon. Un changement de version s'accompagne donc TOUJOURS d'une migration qui
# preserve les entrees `manuel`, jamais d'un simple bump de cette constante.
CACHE_VERSION = 1

# Trace de supervision seulement : la longueur n'est PLUS une contrainte du
# schema envoye au modele (le SDK la retire quoi qu'il arrive, voir le module
# docstring), donc un depassement ne doit plus jamais faire echouer la
# validation Pydantic -- il est tronque.
RAISON_MAX_CARACTERES = 400


class VerdictArbitrage(BaseModel):
    """Sortie JSON stricte de l'arbitrage -- meme motif que `ArticleScore`
    (`app/pipeline/scoring.py`) : schema impose par `output_format`, revalide
    par Pydantic. `raison` ne porte plus de `max_length` : une reponse dont le
    verdict est valide ne doit jamais etre rejetee pour la longueur d'un champ
    qui n'est qu'une trace -- elle est tronquee, pas refusee."""

    meme_actu: bool
    raison: str

    @field_validator("raison")
    @classmethod
    def _tronquer(cls, valeur: str) -> str:
        return valeur[:RAISON_MAX_CARACTERES]


SYSTEM_PROMPT = """\
Tu arbitres une decision de deduplication editoriale pour KINDA, un compte \
Instagram d'actualite musicale.

On te presente DEUX articles : un article de REFERENCE, vu en premier (son \
en-tete dit s'il a deja ete publie par Kinda ou s'il a seulement ete ingere \
plus tot), et un NOUVEAU qui vient d'etre ingere. Un algorithme de similarite \
de titres est incertain sur ces deux-la -- ni assez proches pour etre surs, ni \
assez differents pour etre ecartes sans arbitrage.

Ta question : ces deux articles racontent-ils LA MEME ACTUALITE, ou deux \
actualites DIFFERENTES a propos du meme sujet (meme artiste, meme festival, \
meme label) ?

Regles :
- « Meme actu » veut dire : le meme evenement, la meme annonce, la meme \
sortie -- couvert deux fois, peut-etre dans une autre langue, avec un titre \
different, un angle different, ou des details supplementaires (date, feat., \
nom du label).
- « Actu differente » veut dire : deux evenements distincts a propos du meme \
sujet -- un nouvel EP puis une tournee, une sortie puis une autre, une \
annonce puis son concert. Un artiste peut faire l'actualite plusieurs fois \
dans la meme semaine sans que ce soit deux fois la meme information.
- Si l'un des deux resumes ne permet pas de trancher avec certitude, penche \
vers « actu differente » : rater un lien coute un post de moins, mais un lien \
a tort enterre une actualite neuve pour de bon.

Rends UNIQUEMENT le verdict structure demande. La raison tient en UNE phrase \
courte (15 mots maximum) : elle est stockee comme trace de supervision, pas \
comme une explication detaillee."""


def _resume_borne(article: Article) -> str:
    """Le resume d'un article pas encore score peut etre la description RSS
    brute (parfois l'article entier) ou la caption Instagram avec son bandeau
    de metriques -- rien ne le borne avant le scoring, qui n'a pas encore eu
    lieu au moment de l'arbitrage (`link_duplicates` tourne AVANT). Meme borne
    que `scoring.build_user_prompt`, pour la meme raison : un cout d'entree
    non maitrise. Import LOCAL : `scoring` importe `comptes_artistes`, qui
    importe `cluster`, qui importe ce module -- un import en tete de fichier
    boucle."""
    from app.pipeline.scoring import RESUME_MAX_CARACTERES

    if not article.summary:
        return "(pas de resume)"
    return article.summary[:RESUME_MAX_CARACTERES]


def build_user_prompt(reference: Article, nouveau: Article) -> str:
    """L'en-tete de l'article de reference dit la VERITE sur son statut, et la
    lit sur l'article lui-meme plutot que de la recevoir en parametre (tour de
    correction 1, 2026-09-24).

    Depuis que l'arbitrage s'applique aussi aux paires FRAICHES (Phase 2B,
    Task 3), la reference n'est plus forcement un article publie : l'annoncer
    « DEJA PUBLIE » donnait au juge un contexte faux sur toute la bande
    fraiche. `ArticleStatus.USED` est exactement le critere voulu -- un
    representant d'archive l'est par construction (`link_duplicates` ne
    retient que des `USED`), un representant frais ne l'est generalement pas,
    et s'il l'est (article publie a la main dans les 48 h) l'en-tete « deja
    publie » est alors vrai lui aussi.

    Sans effet sur le CACHE : la cle d'un verdict est `cle_paire(ids)`, jamais
    le texte du prompt. Reformuler ici n'invalide aucun verdict memorise et
    n'exige donc PAS de toucher a `CACHE_VERSION` (la changer desarmerait les
    verdicts manuels -- voir le commentaire de cette constante)."""

    def _bloc(article: Article) -> str:
        return f"Titre : {article.title}\nResume : {_resume_borne(article)}"

    if reference.status == ArticleStatus.USED:
        entete = "ARTICLE DE REFERENCE (DEJA PUBLIE PAR KINDA)"
    else:
        entete = "ARTICLE DE REFERENCE (ingere plus tot, pas encore publie)"
    return (
        f"{entete} :\n{_bloc(reference)}\n\n"
        f"ARTICLE NOUVEAU :\n{_bloc(nouveau)}"
    )


def cle_paire(id_a: int, id_b: int) -> str:
    """Independante de l'ordre : (12, 40) et (40, 12) sont la MEME paire."""
    lo, hi = sorted((id_a, id_b))
    return f"{lo}:{hi}"


@dataclass(frozen=True)
class EntreeArbitrage:
    meme_actu: bool
    raison: str
    # Pose par l'utilisateur (`scripts.marquer_doublon_manuel`), jamais par
    # `link_duplicates` -- c'est ce champ qui lui fait primer sur tout.
    manuel: bool = False


@dataclass(frozen=True)
class Cache:
    entrees: dict[str, EntreeArbitrage] = field(default_factory=dict)
    # Echecs comptes PAR PAIRE, jamais melanges aux verdicts : un verdict veut
    # dire "Claude a repondu", une tentative veut dire "on a essaye". Distinct
    # aussi pour qu'un verdict manuel pose sur une paire en echec efface son
    # compteur (voir `fusionner_et_ecrire`) sans avoir a toucher aux verdicts.
    tentatives: dict[str, int] = field(default_factory=dict)


def _chemin_cache() -> Path:
    return settings.cluster_arbitrage_cache_path


def _lire_brut(chemin: Path) -> dict | None:
    """`None` si le fichier n'EXISTE pas -- le cas normal du tout premier
    passage. LEVE si le fichier existe mais ne se laisse pas lire ou parser :
    distinguer les deux est essentiel -- une fonction qui va ENSUITE ECRIRE ne
    doit jamais confondre « rien encore » et « corrompu », sous peine
    d'ecraser silencieusement tous les verdicts existants, manuels compris."""
    if not chemin.exists():
        return None
    return json.loads(chemin.read_text(encoding="utf-8"))


def _cache_depuis_json(brut: dict) -> Cache:
    if brut.get("version") != CACHE_VERSION:
        raise ValueError(f"version de cache inattendue : {brut.get('version')!r}")
    entrees = {
        cle: EntreeArbitrage(
            meme_actu=bool(ligne["meme_actu"]),
            raison=str(ligne["raison"]),
            manuel=bool(ligne.get("manuel", False)),
        )
        for cle, ligne in (brut.get("verdicts") or {}).items()
    }
    tentatives = {cle: int(valeur) for cle, valeur in (brut.get("tentatives") or {}).items()}
    return Cache(entrees, tentatives)


def lire_cache(chemin: Path | None = None) -> Cache:
    """Lecture SEULE, tolerante : un fichier absent OU illisible vaut un
    cache vide, jamais une exception. C'est le bon comportement pour un
    consommateur qui ne fait QUE lire (`link_duplicates`, en debut de
    passage) -- il ne risque pas d'ecraser quoi que ce soit. Pas de TTL sur
    les verdicts : un verdict sur une paire de titres/resumes figes ne
    « perime » pas comme la lecture d'une image.

    Les fonctions qui ECRIVENT ensuite (`fusionner_et_ecrire`,
    `marquer_manuel`) n'appellent PAS cette fonction pour leur propre
    relecture -- voir `_lire_pour_ecriture`, qui NE masque PAS un fichier
    corrompu."""
    chemin = chemin or _chemin_cache()
    try:
        brut = _lire_brut(chemin)
        return Cache({}) if brut is None else _cache_depuis_json(brut)
    except Exception as exc:  # noqa: BLE001 -- un cache illisible n'est qu'un cache vide
        logger.info("cache d'arbitrage de doublons illisible (%s)", type(exc).__name__)
        return Cache({})


def _lire_pour_ecriture(chemin: Path) -> Cache:
    """Comme `lire_cache`, mais un fichier PRESENT et illisible fait LEVER
    l'exception d'origine au lieu de la masquer : l'appelant s'apprete a
    ECRIRE juste apres, et un cache vide sur cette base effacerait tous les
    verdicts existants -- manuels compris -- d'un seul fichier corrompu. Un
    fichier ABSENT reste un cache vide normal (premier passage)."""
    brut = _lire_brut(chemin)
    return Cache({}) if brut is None else _cache_depuis_json(brut)


def _contenu_json(cache: Cache) -> dict:
    return {
        "version": CACHE_VERSION,
        "verdicts": {
            cle: {
                "meme_actu": entree.meme_actu,
                "raison": entree.raison,
                "manuel": entree.manuel,
            }
            for cle, entree in cache.entrees.items()
        },
        "tentatives": dict(cache.tentatives),
    }


def _ecrire_strict(cache: Cache, chemin: Path) -> None:
    """Ecriture ATOMIQUE (fichier temporaire NOMME PAR PROCESSUS +
    `os.replace`), comme `app/pipeline/quota_graph.py` : un `.tmp` fixe serait
    ecrit a deux mains par le passage horaire et `scripts.marquer_doublon_manuel`
    lance pendant ce passage. Leve sur toute panne -- c'est le comportement
    voulu pour `marquer_manuel` (voir plus bas) ; `ecrire_cache` l'attrape."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    descripteur, temporaire = tempfile.mkstemp(dir=chemin.parent, prefix=chemin.name + ".")
    try:
        with os.fdopen(descripteur, "w", encoding="utf-8") as fichier:
            json.dump(_contenu_json(cache), fichier, ensure_ascii=False)
        os.replace(temporaire, chemin)
    except Exception:
        Path(temporaire).unlink(missing_ok=True)
        raise


def ecrire_cache(cache: Cache, chemin: Path | None = None) -> None:
    """Ecriture au mieux : un echec est journalise, jamais leve -- le
    prochain passage redemandera. Utilisee pour les decisions AUTOMATIQUES
    (`fusionner_et_ecrire`) ; `marquer_manuel` a besoin d'une garantie plus
    forte et n'appelle pas cette fonction."""
    chemin = chemin or _chemin_cache()
    try:
        _ecrire_strict(cache, chemin)
    except Exception as exc:  # noqa: BLE001 -- ne jamais faire echouer un run pour ca
        logger.warning(
            "verdict d'arbitrage de doublons non memorise (%s) : le prochain passage redemandera",
            type(exc).__name__,
        )


def fusionner_et_ecrire(
    nouveaux_verdicts: dict[str, EntreeArbitrage],
    nouvelles_tentatives: dict[str, int],
    chemin: Path | None = None,
) -> None:
    """Fusionne les decisions prises PENDANT un passage de `link_duplicates`
    avec l'etat ACTUEL du disque, juste avant d'ecrire -- jamais l'inverse.

    Un `link_duplicates` peut passer plusieurs minutes a arbitrer (jusqu'a
    `cluster_arbitrage_max_par_passage` appels). Si `scripts.
    marquer_doublon_manuel` pose un verdict manuel sur une paire PENDANT cette
    fenetre, une reecriture en bloc a la fin du passage l'effacerait purement
    et simplement (reproduit en revue). Cette fonction relit le disque a
    l'instant de l'ecriture : un verdict manuel qui y figure GAGNE toujours,
    que ce passage ait ou non une decision concurrente pour la meme paire.

    Si le disque est PRESENT mais illisible a cet instant (corruption, panne
    disque), l'ecriture est ANNULEE entierement -- jamais un ecrasement par
    un cache vide, qui perdrait tous les verdicts existants, manuels compris.
    Le passage suivant retentera.
    """
    chemin = chemin or _chemin_cache()
    try:
        disque = _lire_pour_ecriture(chemin)
    except Exception as exc:  # noqa: BLE001 -- ne jamais ecraser sur la foi d'un doute
        logger.warning(
            "cache d'arbitrage de doublons illisible : ecriture ANNULEE pour ne pas "
            "ecraser d'eventuels verdicts existants (%s)", type(exc).__name__,
        )
        return
    entrees = dict(disque.entrees)
    for cle, entree in nouveaux_verdicts.items():
        existante = entrees.get(cle)
        if existante is not None and existante.manuel:
            continue  # le verdict manuel prime, meme pose pendant ce passage
        entrees[cle] = entree
    tentatives = dict(disque.tentatives)
    for cle, valeur in nouvelles_tentatives.items():
        existante = disque.entrees.get(cle)
        if existante is not None and existante.manuel:
            continue  # un verdict manuel rend le compteur d'echecs obsolete
        tentatives[cle] = valeur
    ecrire_cache(Cache(entrees, tentatives), chemin)


def marquer_manuel(
    id_a: int,
    id_b: int,
    meme_actu: bool,
    raison: str,
    chemin: Path | None = None,
) -> None:
    """Enregistre un verdict humain sur une paire -- reparation d'un faux
    positif d'archive, ou confirmation d'un lien qu'on veut garder quoi qu'il
    arrive. Prime sur tout rapprochement automatique ulterieur, y compris la
    bande "certaine" de `link_duplicates`. Utilise par
    `scripts.marquer_doublon_manuel`.

    LEVE si l'ecriture n'est pas confirmee par une relecture : l'appelant (le
    script) ne doit jamais delier un article en base sur la foi d'un verdict
    qui n'a pas ete durablement pose -- c'est exactement de cette facon
    silencieuse que l'ancienne procedure (effacer deux champs sans rien
    memoriser) echouait. LEVE de la meme facon si le cache existant est
    PRESENT mais illisible : y ecrire quand meme effacerait tous les verdicts
    deja poses, manuels compris.
    """
    chemin = chemin or _chemin_cache()
    try:
        cache = _lire_pour_ecriture(chemin)
    except Exception as exc:
        raise RuntimeError(
            f"cache d'arbitrage illisible, verdict manuel NON pose ({chemin}) : {exc}"
        ) from exc
    cle = cle_paire(id_a, id_b)
    cache.entrees[cle] = EntreeArbitrage(meme_actu, raison, manuel=True)
    # Un verdict manuel efface aussi un compteur d'echecs perime sur la meme
    # paire : `link_duplicates` ne doit plus jamais retenter une paire que
    # l'utilisateur vient de trancher.
    cache.tentatives.pop(cle, None)
    _ecrire_strict(cache, chemin)
    relu = lire_cache(chemin).entrees.get(cle)
    if relu is None or not relu.manuel or relu.meme_actu != meme_actu:
        raise RuntimeError(f"verdict manuel non confirme apres ecriture ({chemin})")


def make_claude_arbitre(client: anthropic.Anthropic | None = None):
    """Un arbitre par defaut : un appel API par paire douteuse, sortie
    structuree validee -- meme motif que `scoring.make_claude_scorer`.

    Rend `(meme_actu, raison)` sur une reponse conforme au schema, `None` sur
    TOUTE panne (cle absente, credits epuises, reseau mort, JSON hors schema,
    max_tokens, refus) : le doute favorise la publication, jamais un lien par
    defaut, et une panne n'est jamais memorisee par l'appelant.
    """
    if client is None:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def arbitre(reference: Article, nouveau: Article) -> tuple[bool, str] | None:
        try:
            response = client.messages.parse(
                model=settings.scoring_model,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_prompt(reference, nouveau)}],
                output_format=VerdictArbitrage,
            )
            sortie = response.parsed_output
            if sortie is None:
                logger.warning(
                    "arbitrage de doublon sans sortie structuree (%s, %s)",
                    reference.id, nouveau.id,
                )
                return None
            return sortie.meme_actu, sortie.raison
        except Exception as exc:  # noqa: BLE001 -- une panne n'est jamais un verdict
            logger.warning(
                "arbitrage de doublon en echec pour (%s, %s) : %s (%s)",
                reference.id, nouveau.id, exc, type(exc).__name__,
            )
            return None

    return arbitre
