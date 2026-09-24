"""Regroupement des doublons éditoriaux ENTRE sources.

La dédup par hash (`dedup.py`) n'attrape que le même lien vu deux fois. Or la même
actu couverte par Mixmag, DJ Mag et Tsugi produit trois articles distincts : trois
cartes dans le dashboard, trois scorings payés, et le risque de la poster deux fois.

On rapproche donc les titres : normalisation (accents, ponctuation, mots vides) puis
coefficient de RECOUVREMENT des mots significatifs. Le recouvrement (intersection sur
le plus petit des deux ensembles) est préféré à Jaccard parce qu'un titre traduit ou
suffixé du nom du média garde les mots porteurs mais en ajoute d'autres -- Jaccard
punirait cet ajout, le recouvrement non.
"""

import datetime as dt
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, ArticleStatus
from app.pipeline import events
from app.pipeline.cluster_arbitrage import (
    EntreeArbitrage,
    cle_paire,
    fusionner_et_ecrire,
    lire_cache,
)

logger = logging.getLogger(__name__)

# Un arbitre rend (meme_actu, raison) sur une vraie reponse, None sur toute
# panne -- jamais memorise dans ce dernier cas (voir `cluster_arbitrage.py`).
Arbitre = Callable[[Article, Article], tuple[bool, str] | None]

# Mots vides FR + EN : trop fréquents pour porter le sens d'un titre
_STOPWORDS = {
    "avec", "dans", "pour", "sur", "son", "sa", "ses", "les", "des", "une", "aux",
    "par", "que", "qui", "est", "sont", "cette", "leur", "plus", "tout", "tous",
    "the", "and", "for", "with", "his", "her", "its", "their", "this", "that",
    "from", "new", "nouveau", "nouvelle", "nouvel", "out", "now",
}

# Mots collés par un trait d'union à recoller avant découpe : "line-up" et "lineup"
# doivent produire le même jeton, sinon deux titres sur la même annonce divergent.
_GLUED = re.compile(r"(?<=\w)-(?=\w)")
_NON_WORD = re.compile(r"[^a-z0-9]+")

MIN_TOKEN_LENGTH = 4
MIN_SHARED_TOKENS = 2
DEFAULT_THRESHOLD = 0.65
DEFAULT_WINDOW_HOURS = 48

# Prefixe des raisons d'exclusion posees PAR CE MODULE (`appliquer`, plus bas) :
# seule une raison qui COMMENCE PAR CECI peut etre effacee par l'auto-reparation
# du verdict manuel. Sans ce filtre, l'auto-reparation effacerait AUSSI une
# exclusion posee ailleurs (gel ou composition impossibles), remettant dans la
# file un article que la publication avait ecarte pour une raison sans rapport
# avec un doublon.
RAISON_DOUBLON_PREFIXE = "doublon d'une actu deja publiee"

# Trace des liens FRAIS poses par l'arbitrage Claude (Phase 2B, Task 3). Prefixe
# DISTINCT de celui de l'archive : les deux liens n'ont pas la meme consequence
# -- un lien d'archive sort l'article de la file pour de bon, un lien frais le
# range seulement derriere un representant qui, lui, reste publiable. Mais un
# lien frais tranche par un modele doit se VOIR sur l'article lui-meme : sans
# trace, il sort du dashboard par le filtre `duplicate_of_id IS NULL` et plus
# rien ne dit pourquoi.
RAISON_DOUBLON_FRAIS_PREFIXE = "doublon d'une actu recente"

# Les raisons posees par la dedup, et elles seules : ce que l'auto-reparation
# d'un verdict manuel a le droit d'effacer, et ce que l'election efface quand
# elle promeut un doublon en representant. Une raison posee AILLEURS (« gel
# impossible : ... ») dit une vraie raison de ne pas publier, sans rapport avec
# un doublon -- l'effacer remettrait dans la file un article que la publication
# avait ecarte.
RAISONS_DOUBLON_PREFIXES = (RAISON_DOUBLON_PREFIXE, RAISON_DOUBLON_FRAIS_PREFIXE)


def raison_de_doublon(raison: str | None) -> bool:
    """Vrai si cette raison d'exclusion a ete posee par la dedup."""
    return (raison or "").startswith(RAISONS_DOUBLON_PREFIXES)


def est_gabarit_de_source(
    partage: set[str], jetons_de_la_source: list[set[str]], seuil: int
) -> bool:
    """Le critere ANTI-GABARIT (Phase 2B, Task 3), et tout son raisonnement.

    Deux titres d'une meme source peuvent se ressembler pour deux raisons
    opposees : ils couvrent le meme evenement (Billboard qui revient sur une
    actu, Soompi qui la reprend), ou la source publie en SERIE sur un gabarit
    de redaction. Mesure du 2026-09-24 : 179 des 255 paires de meme source de
    la bande 0.30-0.65 viennent d'un seul compte (AIM UK), qui publie « The
    winner of <X> at the 2026 Independent Music Awards » une fois par prix.
    Payer un arbitrage pour chacune serait payer un gabarit.

    Le remede naif -- ecarter toute paire de meme source -- est un piege
    MESURE : il tuerait 3 des 5 vrais rates de la mesure (deux paires
    Billboard, une paire Soompi). Ce qu'il faut detecter n'est pas l'identite
    de la source, c'est la REPETITION.

    D'ou le critere : les mots que la paire PARTAGE -- ceux-la memes qui la
    font ressembler a un evenement -- se retrouvent-ils dans au moins `seuil`
    articles de la meme source ? Deux articles qui couvrent un evenement
    partagent des mots propres a cet evenement, qu'aucun troisieme article de
    la source ne porte (porteurs = 2). Un gabarit, lui, ressort partout
    (porteurs = 18 chez AIM UK). Le seuil de 3 dit exactement « au moins un
    article au-dela de la paire », et la distribution mesuree est nette : 44
    paires a 2 porteurs, puis 17 a 3, 12 a 6, 60 a 18.

    Applique aux seules paires de MEME SOURCE (l'appelant s'en assure), parce
    qu'un gabarit est l'habitude d'UNE source : quand les deux cotes viennent
    de sources differentes, la ressemblance ne s'explique plus par l'habitude
    d'une seule, et c'est precisement le signal recherche. Verifie plutot que
    suppose : etendre le critere aux paires croisees ecarte 132 paires de plus
    -- dont « Taylor Swift Announces New Single Patient Zero » (Pitchfork)
    contre « Taylor Swift has updated her Instagram bio » (Pop Crave), c'est-a-
    dire le rate semantique meme que ce chantier existe pour attraper.
    """
    if not partage:
        return False  # jamais le cas dans la bande (MIN_SHARED_TOKENS), ceinture
    return sum(1 for jetons in jetons_de_la_source if partage <= jetons) >= seuil


def normalize_title(title: str) -> str:
    """Minuscules, sans accents ni ponctuation, traits d'union recollés."""
    folded = unicodedata.normalize("NFKD", title.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return _NON_WORD.sub(" ", _GLUED.sub("", folded)).strip()


def significant_tokens(title: str) -> set[str]:
    return {
        token
        for token in normalize_title(title).split()
        if len(token) >= MIN_TOKEN_LENGTH and token not in _STOPWORDS
    }


def title_similarity(left: str, right: str) -> float:
    """0.0 à 1.0. Deux titres sur la même actu partagent leurs mots porteurs
    (noms d'artistes, de festivals, années) même dans deux langues différentes."""
    a, b = significant_tokens(left), significant_tokens(right)
    if not a or not b:
        return 0.0
    shared = a & b
    if len(shared) < MIN_SHARED_TOKENS:
        return 0.0
    return len(shared) / min(len(a), len(b))


def archive_similarity_from_tokens(a: set[str], b: set[str]) -> float:
    """La similarité utilisée CONTRE L'ARCHIVE, et elle seule : la part des mots
    porteurs du titre LE PLUS LONG que les deux partagent.

    Pourquoi pas `title_similarity_from_tokens` avec un seuil plus haut : ce
    coefficient-là divise par le plus PETIT des deux ensembles, donc il vaut
    1.00 dès qu'un titre est contenu dans l'autre. Mesuré le 2026-09-21 sur des
    paires réelles, « Charlotte de Witte annonce un nouvel EP » (« EP » tombe,
    deux lettres) est entièrement contenu dans « Charlotte de Witte annonce une
    tournée mondiale » : 1.00, alors que ce sont deux actus différentes. AUCUN
    seuil ne peut épargner cette paire, puisqu'aucun seuil ne dépasse 1.00.

    Diviser par le plus GRAND des deux compte, lui, les mots qui DIFFÈRENT :
    les trois faux positifs mesurés tombent à 0.60 / 0.75 / 0.67, alors qu'une
    vraie reprise traduite (« Dour Festival unveils its 2027 lineup » /
    « Dour Festival devoile son line-up 2027 ») reste à 0.80. D'où le seuil
    d'archive (`cluster_archive_seuil`, 0.8).

    Sur la fenêtre fraîche de 48 h, rien ne change : le recouvrement y est
    éprouvé, et un faux positif entre deux articles frais ne coûte qu'un
    représentant à réélire, pas une actu enterrée sous une archive publiée.
    """
    if not a or not b:
        return 0.0
    shared = a & b
    if len(shared) < MIN_SHARED_TOKENS:
        return 0.0
    return len(shared) / max(len(a), len(b))


@dataclass
class ClusterStats:
    """Les liens posés, FRAIS et ARCHIVE comptés à part.

    Un lien frais rapproche deux articles de la même fenêtre de 48 h : au pire
    il coûte une réélection de représentant. Un lien d'archive, lui, rattache un
    article NEUF à une actu DÉJÀ PUBLIÉE : il le sort de la file pour de bon.
    Les confondre dans un seul entier rendait un faux positif d'archive
    invisible à la supervision -- c'est exactement ce qui est arrivé.
    """

    frais: int = 0
    archive: int = 0
    # Sous-ensemble de `archive` : lien pose par Claude (bande douteuse, appel
    # frais ou verdict deja en cache) plutot que par le seuil "certain" -- une
    # observabilite a part, demandee au chantier Task 7.
    arbitres: int = 0
    # Appels a l'arbitre qui n'ont rendu aucune reponse exploitable : jamais
    # memorises, jamais un lien, mais utile a voir dans la supervision.
    arbitrage_pannes: int = 0
    # Sous-ensemble de `frais` : lien entre deux articles FRAIS pose par Claude
    # dans la bande 0.30-0.65 (Phase 2B, Task 3), que le seuil de 0.65 n'aurait
    # jamais pose. Compte a part de `arbitres` (l'archive) : les deux n'ont ni
    # la meme mesure, ni le meme risque, et les melanger rendrait invisible
    # celui des deux qui derape.
    frais_arbitres: int = 0
    # Paires de la bande fraiche ecartees SANS rien payer par l'anti-gabarit
    # (`est_gabarit_de_source`). Comptees une fois par paire.
    gabarits_ecartes: int = 0

    @property
    def total(self) -> int:
        return self.frais + self.archive


def link_duplicates(
    db: Session,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    threshold: float = DEFAULT_THRESHOLD,
    archive_days: int | None = None,
    archive_threshold: float | None = None,
    arbitre: Arbitre | None = None,
    arbitrage_max: int | None = None,
    tentatives_max: int | None = None,
    cache_chemin: Path | None = None,
    arbitrage_frais_min: float | None = None,
    gabarit_min_articles: int | None = None,
) -> ClusterStats:
    """Pointe chaque doublon vers son représentant et renvoie le compte des liens créés.

    Le représentant est l'article vu le PLUS TÔT (c'est la source qui a sorti l'info
    la première). Les liens ne forment jamais de chaîne : un troisième doublon pointe
    vers le même représentant, pas vers le deuxième -- ainsi un seul saut suffit
    toujours pour remonter à l'original.

    Idempotent : l'ordre de parcours est déterministe et les articles déjà liés sont
    laissés en place, donc rejouer la fonction ne crée aucun lien supplémentaire.

    Une actu déjà PUBLIÉE (`ArticleStatus.USED`) ne ressort jamais : les articles
    USED échappent à la purge (`retention.py`) précisément pour servir d'archive
    éditoriale, mais rien ne les comparait aux nouveaux articles au-delà de la
    fenêtre `window_hours` -- une reprise plus tardive de la même actu n'était
    donc jamais reconnue. On étend l'ensemble des représentants POSSIBLES aux
    articles USED de l'archive (bornée par `archive_days`, voir `app/config.py`),
    sans jamais les faire entrer dans la boucle : un article archivé ne peut donc
    jamais devenir lui-même un doublon, seulement servir de cible.

    Le rapprochement contre l'archive a sa PROPRE mesure et son propre seuil
    (`archive_similarity_from_tokens`, `cluster_archive_seuil`) : le seuil de
    0.65 est éprouvé sur 48 h, pas sur des semaines d'archive d'un feed centré
    sur quelques artistes récurrents, où il enterrait des actus neuves (trois
    paires mesurées le 2026-09-21, cf. `archive_similarity_from_tokens`). Un
    faux positif d'archive est IRRÉVERSIBLE en pratique : l'article sort de la
    file, le représentant publié est verrouillé par `elire_representants`, et
    rien ne remonte au dashboard -- d'où la trace posée sur l'article lié et le
    compteur séparé.

    **Arbitrage Claude de la bande douteuse** (Task 7, 2026-09-21). Entre le
    seuil certain (`archive_threshold`) et le seuil de recouvrement
    (`threshold`, 0.65), les deux mesures se contredisent -- ni assez sûres
    pour lier sans demander, ni assez sûres pour écarter. `arbitre`, s'il est
    fourni, tranche cette bande (voir `app/pipeline/cluster_arbitrage.py`) ;
    resté à `None` (le défaut, et le cas de `run_veille`), cette bande ne
    produit jamais de lien et jamais d'appel -- le doute favorise la
    publication. Chaque paire n'est arbitrée QU'UNE FOIS : le verdict est
    mémorisé dans `cluster_arbitrage_cache_path`, relu à chaque passage avant
    d'appeler l'API. Un verdict MANUEL (posé par
    `scripts.marquer_doublon_manuel`) est vérifié AVANT toute mesure et prime
    sur tout, y compris sur la bande certaine -- et vaut aussi pour une paire
    de deux articles FRAIS : l'utilisateur a le droit de défaire n'importe
    quel lien, pas seulement un lien d'archive.

    **Deux passes pour ne pas geler l'arbitrage sur une paire qui échoue**
    (mesuré sur le vrai SDK). Une réponse Claude qui ne passe pas la
    validation (JSON hors schéma) est une PANNE : rien n'est
    mémorisé, donc rien n'empêche de la redemander -- mais un article dont
    l'UNIQUE candidat douteux échoue systématiquement consommerait alors une
    part du budget à CHAQUE passage, au détriment des paires jamais tentées
    (elles sont vues plus tard si les articles en échec sont plus anciens).
    Chaque paire garde un compteur de TENTATIVES distinct des verdicts
    (`Cache.tentatives`) ; passé `cluster_arbitrage_tentatives_max`, elle
    n'est plus jamais retentée (sans qu'aucun verdict ne soit mémorisé pour
    autant). Et le budget d'un passage sert d'abord les paires JAMAIS tentées
    (passe 1) avant de rejouer celles déjà en échec, sous le plafond (passe 2)
    -- un article n'est promu nouveau représentant qu'après ces deux passes.
    Un article DIFFÉRÉ en passe 1 reste néanmoins un représentant POSSIBLE
    dès cette passe : sinon un article frais plus RÉCENT qui le reprend
    usurpe sa place, et le plus ancien finit par se lier
    au plus récent -- l'inverse de la règle ci-dessus. S'il résout finalement
    vers l'archive en passe 2, tout article qui l'avait pris pour
    représentant entre-temps est REPOINTÉ directement vers la vraie cible :
    jamais de chaîne à deux sauts.

    **La bande douteuse FRAÎCHE** (Phase 2B, Task 3, 2026-09-24). La même
    machinerie s'applique aux paires de deux articles FRAIS dont le
    recouvrement est sous `threshold` (0.65, où le lien est déjà posé sans
    rien demander) mais au-dessus de `arbitrage_frais_min` (0.30 mesuré : 0
    vrai positif sur 18 paires en dessous, et les deux seuls ratés purement
    sémantiques à 0.33). `arbitrage_frais_min` resté à `None` -- le DÉFAUT,
    donc `run_veille` et tout appelant qui ne demande rien -- cette bande ne
    produit aucun lien et aucun appel, et le module rend exactement les
    décisions d'avant ce chantier : le coupe-circuit est le paramètre
    lui-même, jamais une devinette sur l'appelant. Le cache, les verdicts
    manuels, le plafond de tentatives et le plafond par passage sont les
    MÊMES que pour l'archive -- une seule machinerie, pas deux.

    Une paire de la bande fraîche dont la ressemblance s'explique par un
    GABARIT de rédaction de sa source est écartée AVANT tout appel
    (`est_gabarit_de_source`, qui porte la mesure et le piège du remède
    naïf).

    **Le manuel converge, il ne parie pas sur l'ordre des écritures** (tour de
    correction 2/5, LE défaut le plus important trouvé en re-revue) : un
    lien tranché par Claude n'est posé qu'après avoir RELU le disque -- si un
    verdict manuel "différent" y est apparu entre l'appel à l'arbitre et cet
    instant (l'utilisateur qui corrige PENDANT que Claude répond le
    contraire), le lien n'est pas posé. Et en tête de CHAQUE passage, une
    auto-réparation défait tout lien déjà présent en base dont la paire porte
    un verdict manuel "différent", quelle que soit la façon dont il est
    apparu (course, crash entre deux écritures, version antérieure du code).
    """
    archive_days = settings.cluster_archive_jours if archive_days is None else archive_days
    archive_threshold = (
        settings.cluster_archive_seuil if archive_threshold is None else archive_threshold
    )
    arbitrage_max = (
        settings.cluster_arbitrage_max_par_passage if arbitrage_max is None else arbitrage_max
    )
    tentatives_max = (
        settings.cluster_arbitrage_tentatives_max if tentatives_max is None else tentatives_max
    )
    gabarit_min_articles = (
        settings.cluster_arbitrage_gabarit_min_articles
        if gabarit_min_articles is None
        else gabarit_min_articles
    )
    seen_at = func.coalesce(Article.published_at, Article.fetched_at)
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=window_hours)
    archive_cutoff = now - dt.timedelta(days=archive_days)

    stats = ClusterStats()
    # Le cache est TOUJOURS lu (lecture de fichier, jamais d'appel réseau) :
    # un verdict manuel ou déjà arbitré doit valoir même quand `arbitre` est
    # absent (c'est le cas de `run_veille`), sans jamais coûter un appel.
    cache = lire_cache(cache_chemin)

    # AUTO-RÉPARATION, à CHAQUE passage, AVANT toute lecture des candidats :
    # un lien déjà posé EN BASE dont la paire porte un verdict manuel
    # "différent" est défait ici, quelle que soit la façon
    # dont il est apparu -- une course non prévue entre l'arbitre et
    # `scripts.marquer_doublon_manuel` (l'utilisateur corrige PENDANT que
    # Claude répond "même actu" dans le même passage), un crash entre deux
    # écritures, ou un lien posé par une version antérieure du code. Posé
    # AVANT les requêtes ci-dessous : un article tout juste délié doit pouvoir
    # redevenir un candidat de CE passage, pas seulement du suivant. Le
    # verdict manuel devient ainsi une propriété qui CONVERGE, plutôt qu'un
    # pari sur l'ordre relatif de deux écritures.
    for cle, entree in cache.entrees.items():
        if not entree.manuel or entree.meme_actu:
            continue
        try:
            id_gauche, id_droite = (int(part) for part in cle.split(":"))
        except ValueError:
            continue
        for id_article, id_representant in ((id_gauche, id_droite), (id_droite, id_gauche)):
            article_lie = db.get(Article, id_article)
            if article_lie is not None and article_lie.duplicate_of_id == id_representant:
                article_lie.duplicate_of_id = None
                # N'efface QUE la raison posee par CE module (tour de
                # correction 3/5) : une exclusion posee ailleurs (gel ou
                # composition impossibles, ex. « gel impossible : … ») n'a
                # rien à voir avec ce verdict manuel -- l'effacer remettrait
                # dans la file un article que la publication avait écarté
                # pour une vraie raison, sans rapport avec un doublon.
                if raison_de_doublon(article_lie.file_exclu_raison):
                    article_lie.file_exclu_raison = None
    db.commit()

    articles = db.scalars(
        select(Article).where(seen_at >= cutoff).order_by(seen_at, Article.id)
    ).all()
    archived = db.scalars(
        select(Article).where(
            Article.status == ArticleStatus.USED,
            Article.duplicate_of_id.is_(None),
            seen_at < cutoff,
            seen_at >= archive_cutoff,
        ).order_by(seen_at, Article.id)
    ).all()

    # (article, jetons, vient de l'archive) : l'origine du représentant décide
    # de la mesure, du seuil, du compteur et de la trace -- elle ne se déduit
    # plus de rien une fois le lien posé. L'objet ARTICLE (pas seulement son id
    # et son titre) est gardé : l'arbitrage a aussi besoin de `summary`.
    representatives: list[tuple[Article, set[str], bool]] = [
        (article, significant_tokens(article.title), True) for article in archived
    ]

    # Jetons de TOUS les articles frais, groupes par source : le materiau de
    # l'anti-gabarit (`est_gabarit_de_source`). Construit une seule fois --
    # `significant_tokens` est deja appele par article dans les boucles, mais
    # le cout est negligeable devant une requete, et le construire ici garde
    # la mesure independante de l'ordre de parcours.
    jetons_par_source: dict[int | None, list[set[str]]] = {}
    for article_frais in articles:
        jetons_par_source.setdefault(article_frais.source_id, []).append(
            significant_tokens(article_frais.title)
        )
    # Paires deja ecartees par l'anti-gabarit : comptees UNE fois, meme si
    # `resoudre` repasse dessus en passe 2.
    gabarits_vus: set[str] = set()

    # Etat de travail pour ce passage SEULEMENT : jamais écrit tel quel, fusionné
    # avec une relecture du disque juste avant l'écriture (voir `fusionner_et_
    # ecrire`) pour ne jamais effacer un verdict manuel posé pendant ce passage.
    tentatives_travail = dict(cache.tentatives)
    nouveaux_verdicts: dict[str, EntreeArbitrage] = {}
    appels_restants = arbitrage_max
    # Différée : le seul candidat douteux d'un article est une paire déjà en
    # échec (tentatives > 0), mais sous le plafond -- retentée en passe 2
    # seulement si une paire jamais tentée n'a pas déjà tout consommé.
    DIFFERE = object()

    def resoudre(article: Article, tokens: set[str], *, autoriser_retentatives: bool):
        nonlocal appels_restants
        differe = False
        for rep_article, rep_tokens, rep_archive in representatives:
            if rep_article is article:
                # Un article différé est ajouté à `representatives` en passe 1
                # pour rester un représentant POSSIBLE pour d'autres articles
                # frais -- jamais pour
                # lui-même : sans ce garde-fou, sa propre entrée matcherait à
                # coup sûr (mêmes jetons) et le lierait à... lui-même.
                continue
            cle = cle_paire(article.id, rep_article.id)
            # `nouveaux_verdicts` d'ABORD que le cache disque : un verdict
            # rendu plus tôt dans CE passage (passe 1) n'est écrit sur disque
            # qu'à la toute fin (`fusionner_et_ecrire`) -- sans le consulter
            # ici, la passe 2 rappelait l'arbitre sur la MÊME paire (tour de
            # correction 3/5, appels observés `[(2,3), (1,2), (2,3)]`).
            entree = nouveaux_verdicts.get(cle) or cache.entrees.get(cle)
            if entree is not None and entree.manuel:
                # Le verdict humain prime sur tout, y compris sur un score qui
                # atteindrait la bande certaine ci-dessous -- FRAIS ou ARCHIVE.
                if entree.meme_actu:
                    return (rep_article, 1.0, "manuel", entree.raison)
                continue

            if not rep_archive:
                score = title_similarity_from_tokens(tokens, rep_tokens)
                if score >= threshold:
                    return (rep_article, score, "frais", None)
                if arbitrage_frais_min is None or score < arbitrage_frais_min:
                    continue  # bande « differente », ou arbitrage frais coupe
                # Bande douteuse FRAICHE. L'anti-gabarit passe AVANT tout --
                # avant le cache, avant le budget, avant l'appel : son interet
                # est justement de ne rien payer.
                if rep_article.source_id == article.source_id and est_gabarit_de_source(
                    tokens & rep_tokens,
                    jetons_par_source.get(article.source_id, []),
                    gabarit_min_articles,
                ):
                    if cle not in gabarits_vus:
                        gabarits_vus.add(cle)
                        stats.gabarits_ecartes += 1
                    continue
                if entree is not None:
                    if entree.meme_actu:
                        return (rep_article, score, "arbitre_frais", entree.raison)
                    continue
                deja_tentee_frais = tentatives_travail.get(cle, 0)
                if deja_tentee_frais >= tentatives_max:
                    continue
                if deja_tentee_frais > 0 and not autoriser_retentatives:
                    differe = True
                    continue
                if arbitre is None or appels_restants <= 0:
                    continue
                verdict = arbitre(rep_article, article)
                appels_restants -= 1
                if verdict is None:
                    stats.arbitrage_pannes += 1
                    tentatives_travail[cle] = deja_tentee_frais + 1
                    continue
                meme_actu, raison = verdict
                nouveaux_verdicts[cle] = EntreeArbitrage(meme_actu, raison, manuel=False)
                if meme_actu:
                    return (rep_article, score, "arbitre_frais", raison)
                continue

            score_certain = archive_similarity_from_tokens(tokens, rep_tokens)
            if score_certain >= archive_threshold:
                return (rep_article, score_certain, "certain", None)

            score_doute = title_similarity_from_tokens(tokens, rep_tokens)
            if score_doute < threshold:
                continue  # bande "différent" : aucune mesure ne rapproche, aucun appel

            # Bande douteuse : un verdict Claude déjà mémorisé fait foi sans
            # nouvel appel -- "une paire n'est jamais redemandée".
            if entree is not None:
                if entree.meme_actu:
                    return (rep_article, score_doute, "arbitre", entree.raison)
                continue

            deja_tentee = tentatives_travail.get(cle, 0)
            if deja_tentee >= tentatives_max:
                continue  # abandon définitif : jamais retentée, jamais mémorisée

            if deja_tentee > 0 and not autoriser_retentatives:
                # Une paire déjà en échec ne doit pas consommer le budget avant
                # les paires jamais tentées de CE passage -- déférée en passe 2.
                differe = True
                continue

            if arbitre is None or appels_restants <= 0:
                continue  # pas d'arbitre (run_veille) ou budget épuisé -> le doute gagne

            verdict = arbitre(rep_article, article)
            appels_restants -= 1
            if verdict is None:
                # Une panne n'est JAMAIS un verdict : rien n'est mémorisé, la
                # paire sera redemandée (jusqu'au plafond de tentatives).
                stats.arbitrage_pannes += 1
                tentatives_travail[cle] = deja_tentee + 1
                continue
            meme_actu, raison = verdict
            nouveaux_verdicts[cle] = EntreeArbitrage(meme_actu, raison, manuel=False)
            if meme_actu:
                return (rep_article, score_doute, "arbitre", raison)
            continue

        return DIFFERE if differe else None

    def lier(article: Article, representant: Article) -> list[Article]:
        """SEUL point d'écriture de `duplicate_of_id` VERS un autre article
        dans ce module : pose le lien ET aplatit
        IMMÉDIATEMENT toute chaîne qui en découlerait, en repointant vers
        `representant` tout article qui pointait déjà vers `article`. La
        garantie « jamais de chaîne, un seul saut suffit toujours » doit
        tenir par CONSTRUCTION -- un correctif localisé à la passe 2 (version
        précédente) ratait deux cas rejoués en re-revue : un lien posé en
        passe 1 la même passe (P rejoint D en passe 1, D rejoint R en passe
        2 -- la requête ci-dessous ne voyait pas le lien P→D posé plus tôt
        dans CE MÊME passage), et un lien plus ancien (D représentant d'un
        passage PRÉCÉDENT avec P→D, D lié à R par un verdict manuel « même »
        ou par l'arbitre un passage plus tard).

        `db.flush()` est OBLIGATOIRE avant la requête : `SessionLocal` tourne
        en `autoflush=False` en production (`app/db.py`), donc SANS flush
        explicite ni ce chemin ni le paragraphe précédent ne verraient
        l'affectation qui précède -- exactement le trou mesuré en re-revue
        (chaîne à deux sauts invisible avec `autoflush=False`, masquée dans
        les tests par la fixture `db_session`, qui tourne en autoflush par
        défaut).

        Rend les articles REPOINTÉS : la mécanique est la même quelle que soit
        l'origine du lien, mais ce qu'il faut en ENREGISTRER (trace, compteur)
        dépend de l'origine, que seul `appliquer` connaît.

        Phase 2A (Task 2, `app/pipeline/events.py`) : SEUL point d'où
        `events.attacher` est appelé. Elle relit elle-même le groupe ENTIER
        de `representant` (pas seulement `article` et les repointés d'ICI),
        donc un membre lié lors d'un passage antérieur à l'existence de ce
        module devient une observation lui aussi -- voir le docstring de
        `attacher`. Dégrade en no-op si la migration `0019` n'est pas encore
        appliquée, jamais une exception : elle ne doit jamais faire échouer
        ce module ni la transaction en cours."""
        article.duplicate_of_id = representant.id
        db.flush()
        repointes = list(
            db.scalars(select(Article).where(Article.duplicate_of_id == article.id))
        )
        for pointeur in repointes:
            pointeur.duplicate_of_id = representant.id
        events.attacher(db, article, representant)
        return repointes

    def appliquer(article: Article, tokens: set[str], resultat) -> None:
        if resultat is None:
            representatives.append((article, tokens, False))
            return
        rep_article, score, origine, raison = resultat
        if origine in ("arbitre", "arbitre_frais"):
            # Relecture juste AVANT de poser un lien tranché par Claude (tour
            # de correction 2/5, cas 2, LE défaut le plus important du
            # chantier) : entre l'appel à l'arbitre et cet instant, un verdict
            # manuel "différent" a pu apparaître sur le DISQUE -- typiquement
            # l'utilisateur qui corrige PENDANT que Claude répond "même actu"
            # dans le même passage. Le cache en mémoire (lu une fois en début
            # de passage) ne le sait pas encore ; ne pas relire ici poserait
            # le lien quand même, contredisant le verdict humain qui vient
            # d'être écrit.
            fraiche = lire_cache(cache_chemin).entrees.get(cle_paire(article.id, rep_article.id))
            if fraiche is not None and fraiche.manuel and not fraiche.meme_actu:
                representatives.append((article, tokens, False))
                return
        repointes = lier(article, rep_article)
        if origine == "arbitre_frais":
            # Un lien que le seuil de 0.65 n'aurait JAMAIS pose : compteur a
            # part, journalise, et trace sur l'article lui-meme. Sans les
            # trois, un article sortirait du dashboard (filtre
            # `duplicate_of_id IS NULL`) sans que rien ne dise pourquoi --
            # exactement ce que ce depot s'interdit depuis le faux positif
            # d'archive. La raison posee AILLEURS (« gel impossible : ... »)
            # n'est jamais recouverte, meme regle que pour l'archive.
            stats.frais += 1
            stats.frais_arbitres += 1
            if article.file_exclu_raison is None:
                article.file_exclu_raison = (
                    f"{RAISON_DOUBLON_FRAIS_PREFIXE} (article {rep_article.id}, "
                    f"tranche par Claude : {raison})"
                )[:500]
            logger.warning(
                "doublon frais tranche par Claude : « %s » (%s) rattache a « %s » (%s), "
                "recouvrement %.2f : %s",
                article.title, article.id, rep_article.title, rep_article.id, score, raison,
            )
            return
        if origine == "frais":
            stats.frais += 1
            return
        stats.archive += 1
        if origine == "arbitre":
            stats.arbitres += 1
        # La trace, sur l'article lié lui-même. Sans elle, un faux positif
        # disparaît en silence : l'article sort de la file par le filtre
        # `duplicate_of_id IS NULL` et plus rien ne dit pourquoi.
        # `file_exclu_raison` est le champ que le projet utilise déjà pour
        # « pourquoi cet article ne sera pas publié » -- réparer un faux
        # positif suppose désormais un verdict manuel « différent »
        # (`scripts.marquer_doublon_manuel`), pas seulement effacer ce champ :
        # sinon le passage suivant relie l'article tant qu'il reste dans la
        # fenêtre de sa vie publiable (voir CLAUDE.md, « Dédup à deux étages »).
        if origine == "certain":
            detail = f"similarite {score:.2f}"
        elif origine == "arbitre":
            detail = f"tranche par Claude : {raison}"
        else:
            detail = "verdict manuel"
        article.file_exclu_raison = (
            f"{RAISON_DOUBLON_PREFIXE} (article {rep_article.id}, {detail})"
        )[:500]
        logger.warning(
            "doublon d'archive : « %s » (%s) rattache a « %s » (%s), %s",
            article.title, article.id, rep_article.title, rep_article.id, detail,
        )
        # Un article qui SUIVAIT `article` (lien frais P -> D, aplati en P -> R
        # par `lier`) sort lui aussi de la file sous une actu déjà publiée :
        # même trace, même compteur qu'un lien d'archive direct. Avant le tour
        # de correction 4/5 il suivait son représentant EN SILENCE -- ni
        # `file_exclu_raison` ni `archive`, l'invisibilité même que la trace
        # et le compteur séparé devaient empêcher. Pas `arbitres` : Claude a
        # jugé la paire (D, R), jamais (P, R).
        for pointeur in repointes:
            stats.archive += 1
            # Une raison posée AILLEURS (« gel impossible : … ») dit déjà une
            # vraie raison de ne pas publier : ne pas la recouvrir, pour la
            # même raison que l'auto-réparation ne l'efface jamais.
            if pointeur.file_exclu_raison is None or raison_de_doublon(
                pointeur.file_exclu_raison
            ):
                pointeur.file_exclu_raison = (
                    f"{RAISON_DOUBLON_PREFIXE} (article {rep_article.id}, "
                    f"via son representant {article.id} : {detail})"
                )[:500]
            logger.warning(
                "doublon d'archive : « %s » (%s) suit son representant %s vers « %s » (%s)",
                pointeur.title, pointeur.id, article.id, rep_article.title, rep_article.id,
            )

    # Passe 1 : sert les paires jamais tentées en priorité. Un article dont le
    # SEUL candidat pertinent est une paire déjà en échec est différé, mais
    # reste un REPRÉSENTANT POSSIBLE pour les articles frais traités plus tard
    # dans CETTE passe : sans ça, un article plus récent usurperait la place
    # du plus ancien dans la docstring ("le représentant est l'article vu le
    # PLUS TÔT"), puisque
    # l'ancien n'aurait alors jamais figuré parmi les candidats.
    differes: list[tuple[Article, set[str]]] = []
    for article in articles:
        if article.duplicate_of_id is not None:
            continue
        tokens = significant_tokens(article.title)
        resultat = resoudre(article, tokens, autoriser_retentatives=False)
        if resultat is DIFFERE:
            differes.append((article, tokens))
            representatives.append((article, tokens, False))
            continue
        appliquer(article, tokens, resultat)

    # Passe 2 : ce qui reste du budget rejoue les paires déjà en échec, dans
    # le même ordre. Aucune paire jamais tentée n'attend plus à ce stade.
    # L'aplatissement anti-chaîne (un article différé qui servait déjà de
    # représentant possible depuis la passe 1 peut lui-même devenir un
    # doublon ici) est géré à la SOURCE par `lier()`, pas ici -- voir sa
    # docstring.
    for article, tokens in differes:
        resultat = resoudre(article, tokens, autoriser_retentatives=True)
        appliquer(article, tokens, resultat)

    db.commit()
    tentatives_modifiees = {
        cle: valeur for cle, valeur in tentatives_travail.items() if cache.tentatives.get(cle) != valeur
    }
    if nouveaux_verdicts or tentatives_modifiees:
        fusionner_et_ecrire(nouveaux_verdicts, tentatives_modifiees, cache_chemin)
    return stats


def title_similarity_from_tokens(a: set[str], b: set[str]) -> float:
    """Variante de `title_similarity` sur des jetons déjà calculés (boucle O(n^2))."""
    if not a or not b:
        return 0.0
    shared = a & b
    if len(shared) < MIN_SHARED_TOKENS:
        return 0.0
    return len(shared) / min(len(a), len(b))
