"""Article score -> brouillon de cover Instagram (4 variantes + legende).

Meme forme que `app/pipeline/scoring.py` : structured outputs via `messages.parse()`,
schema Pydantic re-valide en sortie. Les bornes numeriques (nombre de variantes,
longueur des textes) vivent dans le SCHEMA JSON et sont donc contraintes cote API ;
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
from app.content.media import PHOTO
from app.models import Article, Post

logger = logging.getLogger(__name__)

HEADLINE_MAX = 110
NB_VARIANTES = 4
# Le sous-titre du reel vit dans une bande plus etroite que l'accroche du post :
# une borne plus courte evite de proposer un texte qui ne tiendra pas.
SUBTITLE_MAX = 70
# Quatre variantes de trois textes + legende + hashtags. L'ancien budget etait
# calibre pour UN carrousel ; le depasser tronque le JSON, ce qui retombe sur
# l'echec de validation que la boucle de tentatives doit rattraper -- autant ne pas
# le provoquer.
COMPOSER_MAX_TOKENS = 16000
# Bornee : au-dela, le probleme n'est pas la chance mais le prompt ou l'article.
COMPOSER_MAX_ATTEMPTS = 3


class CompositionError(RuntimeError):
    """Levee quand aucune tentative de composition n'a produit un brouillon valide."""


class CoverVariante(BaseModel):
    """Une proposition de cover : les trois textes, pour les deux gabarits.

    `headline` sert au gabarit post (une zone de texte), `title` + `subtitle` au
    gabarit reel (deux bandes empilees). Porter les trois d'avance est ce qui rend
    la bascule POST/REELS instantanee -- sinon il faudrait un appel Claude par
    bascule, et l'utilisateur perdrait sa selection.

    `image_url` n'est PAS demande au modele : un modele a qui l'on demande une URL
    l'invente. Le fond vient des medias de l'article, ou de l'utilisateur.
    """

    headline: str = Field(max_length=HEADLINE_MAX)
    title: str = Field(max_length=HEADLINE_MAX)
    subtitle: str = Field(max_length=SUBTITLE_MAX)

    @field_validator("headline", "title", "subtitle")
    @classmethod
    def _balisage_valide(cls, value: str) -> str:
        validate_headline(value)
        return value


class PostDraft(BaseModel):
    # min_length == max_length == 4 contraint le nombre COTE API via le schema
    # JSON envoye au modele, pas seulement apres coup par Pydantic.
    variantes: list[CoverVariante] = Field(min_length=NB_VARIANTES, max_length=NB_VARIANTES)
    caption: str = Field(max_length=2000)
    hashtags: list[str] = Field(default_factory=list, max_length=15)


SYSTEM_PROMPT = """\
Tu ecris les posts Instagram de KINDA, un compte francais d'actualite musicale \
POP / ELECTRO, a destination d'un public francophone NATIONAL.

Kinda couvre la scene electro francaise dans son ensemble (Paris, Lyon, Marseille, \
Bordeaux, Lille, Toulouse, Nantes, Rennes, Brest), les festivals a toutes les \
echelles, et les grosses actus pop et K-pop. Ce n'est PAS un compte regional : la \
Bretagne est un angle recurrent, jamais le sujet principal.

Tu ne produis PLUS un carrousel de slides de texte. Tu produis UNE cover -- la \
premiere image du post -- sous QUATRE variantes parmi lesquelles l'utilisateur \
choisira. Les slides suivantes du post seront les vraies photos et videos de \
l'actu, pas du texte genere.

1. QUATRE VARIANTES, UNE SEULE INFORMATION : les 4 variantes disent TOUJOURS la \
meme chose -- ce n'est pas 4 angles editoriaux differents, c'est 4 FORMULATIONS \
de la meme info, pour que l'utilisateur choisisse celle qui accroche le mieux. \
Varie l'angle d'attaque : le chiffre qui frappe, le nom de l'artiste ou du lieu, \
la question, le rendez-vous (date/heure). N'INVENTE AUCUN fait different d'une \
variante a l'autre.

2. TROIS TEXTES PAR VARIANTE : chaque variante fournit `headline` (pour le \
gabarit post, une seule zone de texte, 110 caracteres maximum), et le couple \
`title` / `subtitle` (pour le gabarit reel, deux bandes de texte empilees). \
`title` porte le NOM ou le CHIFFRE -- COURT et FRAPPANT, quelques mots, pas une \
phrase (exemple : "Gregoire Cliquet", pas "Gregoire Cliquet est en interview"). \
`subtitle` porte le CONTEXTE qui complete `title` (70 caracteres maximum) -- \
c'est la structure des deux bandes de la maquette : `title` = "Gregoire Cliquet", \
`subtitle` = "au festival des vieilles Charrues". `headline` reste une phrase \
complete, ton direct, parle, sans ponctuation d'article de presse.

3. SURLIGNAGE : dans `headline`, `title` ET `subtitle`, entoure de ** les \
segments a mettre en avant -- ils seront surlignes en rose, c'est la signature \
visuelle du compte. Surligne ce qui accroche l'oeil : le nom d'artiste, le lieu, \
la date, le chiffre qui frappe. UN A DEUX segments par texte, jamais plus, sinon \
l'effet se perd. CHACUN des trois textes doit contenir au moins un segment \
surligne.
Exemple de headline correcte :
Nico Moreno, Mosimann, Cassius... et **c'est ce week-end** aux **Vieilles Charrues**
Exemple de couple title/subtitle correct :
title : **Vieilles Charrues**
subtitle : le line-up electro tombe **ce week-end**

4. LEGENDE (`caption`) : COMMUNE aux quatre variantes -- les variantes sont des \
formulations de cover, pas des angles editoriaux distincts, une seule legende \
suffit. Le texte sous le post, en francais, 3 a 6 lignes. Elle \
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


def _fond_par_defaut(article: Article) -> str | None:
    """La photo de l'article, sinon la vignette d'une video.

    Une actu video n'a pas de photo, et sa vignette fait un fond de cover
    parfaitement valable -- c'est meme le cas normal pour ce type d'actu.
    """
    for entree in article.media or []:
        if entree.get("type") == PHOTO and entree.get("url"):
            return entree["url"]
    for entree in article.media or []:
        if entree.get("preview_url"):
            return entree["preview_url"]
    return None


def create_post(
    db: Session, article: Article, draft: PostDraft, template: str = "kinda_post"
) -> Post:
    """Persiste un brouillon : les quatre variantes, et une cover sans retenue.

    Les champs sont DENORMALISES expres : l'article sera purge a 14 jours, le post
    doit rester lisible sans lui.
    """
    premiere = draft.variantes[0]
    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=article.source.name if article.source is not None else None,
        template=template,
        caption=draft.caption,
        hashtags=draft.hashtags or None,
        # La cover affiche la variante 1 en attendant un choix, pour que le rendu
        # soit possible immediatement ; `variants.retenue` reste null.
        slides=[
            {
                "kind": "cover",
                "headline": premiere.headline,
                "title": premiere.title,
                "subtitle": premiere.subtitle,
                "image_url": _fond_par_defaut(article),
            }
        ],
        variants={
            "variantes": [v.model_dump() for v in draft.variantes],
            "retenue": None,
        },
    )
    db.add(post)
    db.commit()
    logger.info("post %s cree pour l'article %s", post.id, article.id)
    return post
