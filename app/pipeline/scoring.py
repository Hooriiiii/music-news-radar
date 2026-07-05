import logging
from dataclasses import dataclass
from enum import Enum

import anthropic
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article

logger = logging.getLogger(__name__)


class Category(str, Enum):
    NEW_RELEASE = "new_release"
    ANNOUNCEMENT = "announcement"
    EVENT_FESTIVAL = "event_festival"
    INDUSTRY = "industry"
    GEAR_TECH = "gear_tech"
    CULTURE = "culture"
    DRAMA = "drama"
    RIP = "rip"
    OTHER = "other"


class ArticleScore(BaseModel):
    """Sortie JSON stricte du scoring -- le schéma est imposé à l'API via structured
    outputs, et re-validé ici par Pydantic (bornes 0-100 incluses)."""

    relevance: int = Field(ge=0, le=100)
    hotness: int = Field(ge=0, le=100)
    category: Category
    imprint: str | None = None
    summary: str


SYSTEM_PROMPT = """\
Tu es le filtre éditorial d'un compte Instagram d'actualité musicale à la ligne \
éditoriale précise : POP / ÉLECTRO.

1. Cœur de ligne : la MUSIQUE ÉLECTRONIQUE (house, techno, drum & bass, EDM, dance, \
expérimental, clubbing, festivals, labels et artistes électro).
2. Bienvenues aussi : les GROSSES actus POP mainstream uniquement (stars \
internationales : album surprise, décès, scandale majeur, tournée événement).
3. Hors ligne éditoriale : rap/hip-hop, rock, metal, country, jazz, classique... \
même quand l'actu est importante dans son genre, elle ne nous concerne pas — \
relevance <40, sauf événement historique qui transcende les genres.

Pour chaque article ou post fourni, évalue :

- relevance (0-100) : intérêt éditorial pour CE compte.
  80+ : actu majeure pile dans la ligne (release/annonce/décès d'un artiste électro \
connu, gros festival électro, fermeture de club emblématique, actu pop massive).
  60-79 : actu électro intéressante, digne du digest quotidien ; actu pop notable.
  40-59 : actu électro mineure, actu pop moyenne.
  <40 : bruit (question perso, autopromo d'inconnu, meme, discussion sans info) \
OU actu hors ligne pop/électro quel que soit son poids dans son genre.
- hotness (0-100) : urgence et potentiel viral, indépendamment de relevance.
  80+ : breaking news à poster dans l'heure (décès, annonce surprise, drama chaud, \
séparation/reformation) — et uniquement si l'actu est dans la ligne éditoriale. \
Une interview de fond ou une rétrospective reste froide (<40) même si pertinente.
- category : le type d'actu.
- imprint : le label ou la maison de disques concerné(e) si identifiable, sinon null.
- summary : résumé factuel en français, 1 à 2 phrases, publiable tel quel.

Cas particulier — les clips UGC (marqués "clip UGC filmé par un fan" dans les \
métadonnées) : ce sont des vidéos brutes filmées au smartphone en concert, club ou \
festival par des spectateurs. C'est un contenu PREMIUM pour ce compte, précieux \
parce que vivant et non filtré : si l'artiste ou l'événement est notable et que \
l'engagement décolle, relevance 60-80 même si le texte du tweet est trivial \
("c'était fou hier soir"). Ne les traite jamais en bruit personnel.

Sois sévère sur relevance : la majorité des posts de forums sont du bruit (<40). \
Ne sur-note pas la hotness : réserve 80+ aux actus qui ne peuvent pas attendre demain.\
"""


def build_user_prompt(article: Article) -> str:
    parts = [f"Titre : {article.title}"]
    if article.summary:
        parts.append(f"Extrait : {article.summary[:1500]}")
    if article.source is not None:
        parts.append(f"Source : {article.source.name}")
    if article.published_at is not None:
        parts.append(f"Publié le : {article.published_at:%d/%m/%Y %H:%M} UTC")
    parts.append(f"URL : {article.url}")
    return "\n".join(parts)


def is_digest_worthy(article: Article) -> bool:
    score = article.relevance_score
    return score is not None and score >= settings.digest_relevance_threshold


def is_hot(article: Article) -> bool:
    score = article.hotness_score
    return score is not None and score >= settings.alert_hotness_threshold


def make_claude_scorer(client: anthropic.Anthropic | None = None):
    """Scorer par défaut : un appel API par article, sortie structurée validée."""
    if client is None:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def scorer(article: Article) -> ArticleScore:
        response = client.messages.parse(
            model=settings.scoring_model,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(article)}],
            output_format=ArticleScore,
        )
        return response.parsed_output

    return scorer


def apply_score(article: Article, score: ArticleScore) -> None:
    article.relevance_score = score.relevance
    article.hotness_score = score.hotness
    article.category = score.category.value
    article.imprint = score.imprint
    # Le résumé éditorial de Claude remplace l'extrait brut du flux
    article.summary = score.summary


@dataclass
class ScoringStats:
    scored: int = 0
    errors: int = 0


def score_pending(db: Session, scorer=None, limit: int | None = None) -> ScoringStats:
    """Score tous les articles pas encore évalués (relevance_score IS NULL).

    Commit après chaque article : un run interrompu ne perd pas les appels API
    déjà payés, et un article qui échoue n'annule pas les autres.
    """
    if scorer is None:
        scorer = make_claude_scorer()
    stmt = select(Article).where(Article.relevance_score.is_(None)).order_by(Article.fetched_at)
    if limit is not None:
        stmt = stmt.limit(limit)
    articles = db.scalars(stmt).all()

    stats = ScoringStats()
    for article in articles:
        try:
            apply_score(article, scorer(article))
            db.commit()
            stats.scored += 1
        except Exception:
            logger.exception("Scoring échoué pour l'article %s (%s)", article.id, article.title)
            db.rollback()
            stats.errors += 1
    return stats
