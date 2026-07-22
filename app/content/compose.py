"""Article score -> post carrousel Instagram (slides + legende).

Meme forme que `app/pipeline/scoring.py` : structured outputs via `messages.parse()`,
schema Pydantic re-valide en sortie. Les bornes numeriques (nombre de slides,
longueur d'accroche) vivent dans le SCHEMA JSON et sont donc contraintes cote API ;
la regle de balisage (`_balisage_valide`, au moins un segment surligne) n'est,
elle, verifiee que par Pydantic APRES coup -- c'est precisement la contrainte que
le modele peut rater. `client.messages.parse()` leve alors une `pydantic.
ValidationError` que le SDK NE retente PAS (son `max_retries` ne couvre que les
erreurs HTTP -- 429, 5xx -- pas les echecs de schema) : `make_claude_composer`
retente donc lui-meme, jusqu'a `max_attempts` fois, et leve `CompositionError`
si aucune tentative n'aboutit.
"""

import logging

import anthropic
import pydantic
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.content.highlight import validate_headline
from app.models import Article, Post

logger = logging.getLogger(__name__)

HEADLINE_MAX = 110
# Budget genereux : sur Sonnet 5, omettre `thinking` active un raisonnement adaptatif
# par defaut, et max_tokens plafonne le TOTAL raisonnement + sortie -- un carrousel a
# 6 slides + legende de 2000 caracteres + 12 hashtags peut depasser un budget serre,
# ce qui tronque le JSON et retombe sur le defaut n°1 (validation ratee).
COMPOSER_MAX_TOKENS = 8000
# Bornee : au-dela, le probleme n'est pas la chance mais le prompt ou l'article.
COMPOSER_MAX_ATTEMPTS = 3


class CompositionError(RuntimeError):
    """Levee quand aucune tentative de composition n'a produit un brouillon valide."""


class Slide(BaseModel):
    """Une slide du carrousel. Le template ne comporte QU'UNE zone de texte : pas
    de corps de texte, pas de type de slide. `image_url` n'est pas demande au
    modele -- un modele a qui l'on demande une URL l'invente."""

    headline: str = Field(max_length=HEADLINE_MAX)

    @field_validator("headline")
    @classmethod
    def _balisage_valide(cls, value: str) -> str:
        validate_headline(value)
        return value


class PostDraft(BaseModel):
    slides: list[Slide] = Field(min_length=2, max_length=6)
    caption: str = Field(max_length=2000)
    hashtags: list[str] = Field(default_factory=list, max_length=15)


SYSTEM_PROMPT = """\
Tu ecris les posts Instagram de KINDA, un compte francais d'actualite musicale \
POP / ELECTRO, a destination d'un public francophone NATIONAL.

Kinda couvre la scene electro francaise dans son ensemble (Paris, Lyon, Marseille, \
Bordeaux, Lille, Toulouse, Nantes, Rennes, Brest), les festivals a toutes les \
echelles, et les grosses actus pop et K-pop. Ce n'est PAS un compte regional : la \
Bretagne est un angle recurrent, jamais le sujet principal.

Tu produis un CARROUSEL a partir d'un article. Regles :

1. NOMBRE DE SLIDES : entre 2 et 6, selon la matiere reellement disponible dans \
l'article. Une breve de trois lignes fait 2 slides. N'INVENTE JAMAIS de contenu \
pour remplir une slide de plus : un carrousel court et dense vaut mieux qu'un \
carrousel delaye.

2. ACCROCHE (`headline`) : une phrase par slide, 110 caracteres maximum, en \
francais. C'est le SEUL texte affiche sur l'image. Ton direct, parle, sans \
ponctuation d'article de presse. La premiere slide accroche et donne l'info \
principale ; les suivantes deroulent le detail ; la derniere donne le rendez-vous \
ou l'invitation (date, lieu, sortie, lien en bio).

3. SURLIGNAGE : dans chaque accroche, entoure de ** les segments a mettre en \
avant -- ils seront surlignes en rose, c'est la signature visuelle du compte. \
Surligne ce qui accroche l'oeil : le nom d'artiste, le lieu, la date, le chiffre \
qui frappe. UN A DEUX segments par accroche, jamais plus, sinon l'effet se perd. \
CHAQUE accroche doit contenir au moins un segment surligne.
Exemple d'accroche correcte :
Nico Moreno, Mosimann, Cassius... et **c'est ce week-end** aux **Vieilles Charrues**

4. LEGENDE (`caption`) : le texte sous le post, en francais, 3 a 6 lignes. Elle \
developpe ce que les slides annoncent, cite la source de l'info, et se termine par \
une question ou une invitation a reagir. Pas de hashtags dans la legende.

5. HASHTAGS : 5 a 12, en minuscules, pertinents et specifiques (nom d'artiste, nom \
de festival, ville, genre). Evite les hashtags fourre-tout du type #music ou #love.

N'invente aucun fait qui ne figure pas dans l'article : ni date, ni lieu, ni nom.\
"""


def build_user_prompt(article: Article) -> str:
    parts = [f"Titre : {article.title}"]
    if article.summary:
        parts.append(f"Resume : {article.summary[:2000]}")
    if article.source is not None:
        parts.append(f"Source : {article.source.name}")
    if article.published_at is not None:
        parts.append(f"Publie le : {article.published_at:%d/%m/%Y}")
    if article.category:
        parts.append(f"Categorie : {article.category}")
    if article.mentioned_artists:
        parts.append(f"Artistes cites : {', '.join(article.mentioned_artists)}")
    parts.append(f"URL : {article.url}")
    return "\n".join(parts)


def make_claude_composer(
    client: anthropic.Anthropic | None = None,
    max_attempts: int = COMPOSER_MAX_ATTEMPTS,
):
    """Compositeur par defaut : un appel API par post, sortie structuree validee.

    Boucle bornee de tentatives : un echec de VALIDATION (pas un echec HTTP, deja
    couvert par le `max_retries` du SDK) relance l'appel jusqu'a `max_attempts`
    fois avant de lever `CompositionError`.
    """
    if client is None:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def composer(article: Article) -> PostDraft:
        derniere_erreur: Exception | None = None
        for tentative in range(1, max_attempts + 1):
            try:
                response = client.messages.parse(
                    model=settings.composer_model,
                    max_tokens=COMPOSER_MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": build_user_prompt(article)}],
                    output_format=PostDraft,
                )
                return response.parsed_output
            except (pydantic.ValidationError, ValueError) as exc:
                derniere_erreur = exc
                logger.warning(
                    "composition invalide pour l'article %s (tentative %s/%s) : %s",
                    article.id,
                    tentative,
                    max_attempts,
                    exc,
                )
        raise CompositionError(
            f"composition impossible pour l'article {article.id} "
            f"apres {max_attempts} tentatives"
        ) from derniere_erreur

    return composer


def create_post(
    db: Session, article: Article, draft: PostDraft, template: str = "kinda_post"
) -> Post:
    """Persiste un brouillon. Les champs sont DENORMALISES exprès : l'article sera
    purge a 14 jours, le post doit rester lisible sans lui."""
    premiere_image = None
    if article.media_urls:
        premiere_image = article.media_urls[0]

    slides = [
        {"headline": slide.headline, "image_url": premiere_image if index == 0 else None}
        for index, slide in enumerate(draft.slides)
    ]

    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=article.source.name if article.source is not None else None,
        template=template,
        caption=draft.caption,
        hashtags=draft.hashtags or None,
        slides=slides,
    )
    db.add(post)
    db.commit()
    logger.info("post %s cree pour l'article %s", post.id, article.id)
    return post
