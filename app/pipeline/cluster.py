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
import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, ArticleStatus

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


def link_duplicates(
    db: Session,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    threshold: float = DEFAULT_THRESHOLD,
    archive_days: int | None = None,
) -> int:
    """Pointe chaque doublon vers son représentant et renvoie le nombre de liens créés.

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
    """
    archive_days = settings.cluster_archive_jours if archive_days is None else archive_days
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
        )
    ).all()

    representatives: list[tuple[int, set[str]]] = [
        (article.id, significant_tokens(article.title)) for article in archived
    ]
    linked = 0
    for article in articles:
        tokens = significant_tokens(article.title)
        if article.duplicate_of_id is not None:
            continue
        match = next(
            (
                rep_id
                for rep_id, rep_tokens in representatives
                if title_similarity_from_tokens(tokens, rep_tokens) >= threshold
            ),
            None,
        )
        if match is None:
            representatives.append((article.id, tokens))
        else:
            article.duplicate_of_id = match
            linked += 1
    db.commit()
    return linked


def title_similarity_from_tokens(a: set[str], b: set[str]) -> float:
    """Variante de `title_similarity` sur des jetons déjà calculés (boucle O(n^2))."""
    if not a or not b:
        return 0.0
    shared = a & b
    if len(shared) < MIN_SHARED_TOKENS:
        return 0.0
    return len(shared) / min(len(a), len(b))
