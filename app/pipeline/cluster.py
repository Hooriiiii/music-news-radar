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
from app.pipeline.cluster_arbitrage import EntreeArbitrage, cle_paire, ecrire_cache, lire_cache

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
    cache_chemin: Path | None = None,
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
    sur tout, y compris sur la bande certaine.
    """
    archive_days = settings.cluster_archive_jours if archive_days is None else archive_days
    archive_threshold = (
        settings.cluster_archive_seuil if archive_threshold is None else archive_threshold
    )
    arbitrage_max = (
        settings.cluster_arbitrage_max_par_passage if arbitrage_max is None else arbitrage_max
    )
    seen_at = func.coalesce(Article.published_at, Article.fetched_at)
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=window_hours)
    articles = db.scalars(
        select(Article).where(seen_at >= cutoff).order_by(seen_at, Article.id)
    ).all()

    archive_cutoff = now - dt.timedelta(days=archive_days)
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
    stats = ClusterStats()
    # Le cache est TOUJOURS lu (lecture de fichier, jamais d'appel réseau) :
    # un verdict manuel ou déjà arbitré doit valoir même quand `arbitre` est
    # absent (c'est le cas de `run_veille`), sans jamais coûter un appel.
    cache = lire_cache(cache_chemin)
    cache_modifie = False
    appels_restants = arbitrage_max

    for article in articles:
        tokens = significant_tokens(article.title)
        if article.duplicate_of_id is not None:
            continue
        # (représentant, score, origine, raison) -- `raison` n'est renseignée
        # que pour un lien tranché par arbitrage (Claude ou manuel).
        match: tuple[Article, float, str, str | None] | None = None
        for rep_article, rep_tokens, rep_archive in representatives:
            if not rep_archive:
                score = title_similarity_from_tokens(tokens, rep_tokens)
                if score >= threshold:
                    match = (rep_article, score, "frais", None)
                    break
                continue

            cle = cle_paire(article.id, rep_article.id)
            entree = cache.entrees.get(cle)
            if entree is not None and entree.manuel:
                # Le verdict humain prime sur tout, y compris sur un score qui
                # atteindrait la bande certaine ci-dessous.
                if entree.meme_actu:
                    match = (rep_article, 1.0, "manuel", entree.raison)
                    break
                continue

            score_certain = archive_similarity_from_tokens(tokens, rep_tokens)
            if score_certain >= archive_threshold:
                match = (rep_article, score_certain, "certain", None)
                break

            score_doute = title_similarity_from_tokens(tokens, rep_tokens)
            if score_doute < threshold:
                continue  # bande "différent" : aucune mesure ne rapproche, aucun appel

            # Bande douteuse : un verdict Claude déjà mémorisé fait foi sans
            # nouvel appel -- "une paire n'est jamais redemandée".
            if entree is not None:
                if entree.meme_actu:
                    match = (rep_article, score_doute, "arbitre", entree.raison)
                    break
                continue

            if arbitre is None or appels_restants <= 0:
                continue  # pas d'arbitre (run_veille) ou budget épuisé -> le doute gagne

            verdict = arbitre(rep_article, article)
            appels_restants -= 1
            if verdict is None:
                # Une panne n'est JAMAIS un verdict : rien n'est mémorisé, la
                # paire sera redemandée au prochain passage.
                stats.arbitrage_pannes += 1
                continue
            meme_actu, raison = verdict
            cache.entrees[cle] = EntreeArbitrage(meme_actu, raison, manuel=False)
            cache_modifie = True
            if meme_actu:
                match = (rep_article, score_doute, "arbitre", raison)
                break
            continue

        if match is None:
            representatives.append((article, tokens, False))
            continue
        rep_article, score, origine, raison = match
        article.duplicate_of_id = rep_article.id
        if origine == "frais":
            stats.frais += 1
            continue
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
            f"doublon d'une actu deja publiee (article {rep_article.id}, {detail})"
        )[:500]
        logger.warning(
            "doublon d'archive : « %s » (%s) rattache a « %s » (%s), %s",
            article.title, article.id, rep_article.title, rep_article.id, detail,
        )
    db.commit()
    if cache_modifie:
        ecrire_cache(cache, cache_chemin)
    return stats


def title_similarity_from_tokens(a: set[str], b: set[str]) -> float:
    """Variante de `title_similarity` sur des jetons déjà calculés (boucle O(n^2))."""
    if not a or not b:
        return 0.0
    shared = a & b
    if len(shared) < MIN_SHARED_TOKENS:
        return 0.0
    return len(shared) / min(len(a), len(b))
