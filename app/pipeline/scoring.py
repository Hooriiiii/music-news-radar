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
    artists: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """\
Tu es le filtre éditorial d'un compte Instagram d'actualité musicale à la ligne \
éditoriale précise : POP / ÉLECTRO.

1. Cœur de ligne : la MUSIQUE ÉLECTRONIQUE (house, techno, drum & bass, EDM, dance, \
expérimental, clubbing, festivals, labels et artistes électro).
2. Bienvenues aussi : les GROSSES actus POP mainstream (stars internationales : \
album surprise, décès, scandale majeur, tournée événement).
3. Axes prioritaires TRANSVERSAUX, à valoriser même hors du strict club électro :
   - FESTIVALS à toutes les échelles : annonces, line-ups, éditions, billetterie. \
Les festivals INTERNATIONAUX et NATIONAUX (français) comptent, et les festivals \
LOCAUX EN BRETAGNE ont un intérêt particulier pour ce compte (ex. Astropolis, \
Panoramas, Motocultor, Route du Rock, Vieilles Charrues, festivals rennais/brestois) \
— une annonce de festival breton mérite 60+ même si l'affiche n'est pas 100 % électro.
   - INFOS POSITIVES / feel-good (initiative solidaire, record, retour attendu, \
belle histoire) : à mettre en avant, ce compte aime le positif.
   - MUSIQUE × ÉCOLOGIE / ENVIRONNEMENT (festival éco-responsable, prise de position \
climat d'un artiste, innovation durable dans l'événementiel) : angle recherché, 60+.
   - Les GROSSES actus musicales générales qui font parler, même transverses.
4. Hors ligne : rap/hip-hop, rock, metal, country, jazz, classique quand l'actu est \
purement interne à ces genres — relevance <40, SAUF si elle relève d'un des axes \
ci-dessus (festival breton multi-genres, initiative écolo, événement historique).

Pour chaque article ou post fourni, évalue :

- relevance (0-100) : intérêt éditorial pour CE compte.
  80+ : actu majeure pile dans la ligne (release/annonce/décès d'un artiste électro \
connu, gros festival, fermeture de club emblématique, actu pop massive).
  60-79 : actu électro intéressante ; actu pop notable ; annonce de festival (dont \
breton/français) ; info positive marquante ; angle musique-écologie.
  40-59 : actu électro mineure, actu pop moyenne, festival très confidentiel.
  <40 : bruit (question perso, autopromo d'inconnu, meme, discussion sans info) \
OU actu hors ligne ne relevant d'aucun axe prioritaire.
- hotness (0-100) : urgence et potentiel viral, indépendamment de relevance.
  80+ : breaking news à poster dans l'heure (décès, annonce surprise, drama chaud, \
séparation/reformation) — et uniquement si l'actu est dans la ligne éditoriale. \
Une interview de fond ou une rétrospective reste froide (<40) même si pertinente.
- category : le type d'actu.
- imprint : le label ou la maison de disques concerné(e) si identifiable, sinon null.
- summary : résumé factuel en français, 1 à 2 phrases, publiable tel quel.
- artists : la liste des artistes/DJs/groupes POP ou ÉLECTRO mentionnés (nom propre \
exact, sans @ ni #). N'inclus QUE les artistes pop ou électro — ignore ceux d'autres \
genres (metal, rock, folk, rap...) même s'ils sont cités (ex. sur une affiche de \
festival multi-genres, ne garde que le headliner électro/pop). Liste vide si aucun. \
Sert à repérer les artistes pop/électro du moment pour chasser leurs vidéos live.

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
    article.mentioned_artists = score.artists or None


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
