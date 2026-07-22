"""Article score -> post carrousel Instagram (slides + legende).

Meme forme que `app/pipeline/scoring.py` : structured outputs via `messages.parse()`,
schema Pydantic re-valide en sortie. Les bornes vivent dans le SCHEMA et pas dans le
prompt : si le modele produit huit slides ou une accroche trop longue, la validation
echoue et l'appel est retente.
"""

import logging

import anthropic
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.content.highlight import validate_headline
from app.models import Article, Post

logger = logging.getLogger(__name__)

HEADLINE_MAX = 110


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


def make_claude_composer(client: anthropic.Anthropic | None = None):
    """Compositeur par defaut : un appel API par post, sortie structuree validee."""
    if client is None:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def composer(article: Article) -> PostDraft:
        response = client.messages.parse(
            model=settings.composer_model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(article)}],
            output_format=PostDraft,
        )
        return response.parsed_output

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
