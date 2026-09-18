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
from app.content.credit import credit_video
from app.content.emojis import EMOJI_RE as _EMOJI_RE
from app.content.highlight import validate_headline
from app.content.illustration import choisir_illustration
from app.content.teinte import teinte_de_article
from app.content.visuels import image_de_fond, visuels_autorises
from app.models import Article, Post

logger = logging.getLogger(__name__)

HEADLINE_MAX = 110
NB_VARIANTES = 4
# Bornes TOLEREES des emojis de legende. Le prompt en demande 2 a 4 ; la borne
# haute est plus lache expres (voir `probleme_emojis`). Hors bornes, le
# compositeur relance, puis accepte : jamais une composition perdue pour ca.
CAPTION_EMOJIS_MIN = 2
CAPTION_EMOJIS_MAX = 6
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


def contient_emoji(texte: str) -> bool:
    """Vrai si `texte` porte au moins un pictogramme emoji."""
    return _EMOJI_RE.search(texte) is not None


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

    @field_validator("headline", "title", "subtitle")
    @classmethod
    def _sans_emoji(cls, value: str) -> str:
        """Ces trois textes sont RENDUS EN PNG par Chromium, dans une police precise.

        Un emoji y sort en couleur systeme et casse la direction artistique
        rose / creme du compte ; il fausse en plus la mesure de largeur de ligne
        sur laquelle repose la machine d'ajustement d'echelle de
        `app/content/layout.py`. Le prompt l'interdit deja, mais un prompt ne
        garantit rien : ce rejet fait relancer l'appel par la boucle de tentatives
        de `make_claude_composer`. La legende, elle, n'est pas rendue et garde ses
        emojis.
        """
        if contient_emoji(value):
            raise ValueError("Emoji interdit dans un texte rendu (headline/title/subtitle)")
        return value


class PostDraft(BaseModel):
    # min_length == max_length == 4 contraint le nombre COTE API via le schema
    # JSON envoye au modele, pas seulement apres coup par Pydantic.
    variantes: list[CoverVariante] = Field(min_length=NB_VARIANTES, max_length=NB_VARIANTES)
    caption: str = Field(max_length=2000)
    hashtags: list[str] = Field(default_factory=list, max_length=15)

    @field_validator("caption")
    @classmethod
    def _sans_balisage(cls, value: str) -> str:
        """La legende n'est pas rendue : le `**` y resterait litteral.

        Le surlignage rose est une transformation du RENDU -- `**segment**` devient
        un <mark> dans le gabarit. La legende, elle, est recopiee telle quelle dans
        Instagram par `api/posts.py::export`, ou `**Sensational**` s'affiche avec
        ses asterisques. Le modele confond les deux registres des qu'un nom propre
        lui semble meriter d'etre mis en avant (observe sur une composition reelle)
        : la regle 3 du prompt le dit deja, ce rejet le garantit.
        """
        if "**" in value:
            raise ValueError("Balisage ** interdit dans la legende : elle n'est pas rendue")
        return value



# Ce que le prompt demande (regle 6). La borne haute toleree est plus lache :
# elle n'est la que pour arreter l'exces.
CAPTION_EMOJIS_DEMANDES = (2, 4)


def probleme_emojis(caption: str) -> str | None:
    """Decrit, pour la relance, une legende hors bornes d'emojis ; None sinon.

    Ce n'est PLUS un validateur de `PostDraft` : `messages.parse()` aurait leve
    une ValidationError qui jette le brouillon entier, et trois legendes sans
    emoji faisaient perdre la composition d'un creneau (production, 2026-09-17,
    articles 17930 et 18078). `make_claude_composer` relance avec ce message,
    puis accepte le brouillon avec un avertissement : une legende sans emoji vaut
    mieux que pas de post.
    """
    nombre = len(_EMOJI_RE.findall(caption))
    if CAPTION_EMOJIS_MIN <= nombre <= CAPTION_EMOJIS_MAX:
        return None
    bas, haut = CAPTION_EMOJIS_DEMANDES
    return (
        f"La legende (`caption`) porte {nombre} emoji(s), attendu entre {bas} et {haut} "
        f"(tolere de {CAPTION_EMOJIS_MIN} a {CAPTION_EMOJIS_MAX}). Corrige UNIQUEMENT la "
        "legende `caption` ; jamais d'emoji dans `headline`, `title` ou `subtitle`."
    )


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
suffit. Le texte sous le post, en francais, en 2 a 4 paragraphes courts (1 a 2 \
phrases chacun), SEPARES PAR UNE ligne vide -- jamais un bloc compact, illisible \
sur mobile. Elle developpe ce que la cover annonce et cite la source de l'info. Pas de hashtags dans la legende, et \
AUCUN balisage ** : le surlignage n'existe que sur la cover, qui est une image ; \
dans la legende les asterisques s'afficheraient telles quelles.
La CLOTURE depend de la nature de l'actu :
- actu ordinaire (annonce, sortie, line-up, date, record) : termine par une \
question ou une invitation a reagir.
- ACTU TERMINALE -- annulation, liquidation judiciaire, faillite, deces, \
separation d'un groupe, derniere edition, arret definitif -- : termine par une \
projection (ce qui reste, ce qui vient apres) ou par un hommage. JAMAIS par une \
invitation a participer, a revenir, a prendre sa place ou a y retourner : \
l'evenement n'aura pas lieu.
Exemple sur une liquidation judiciaire de festival :
mauvaise cloture : "Vous y etiez cette annee ? Racontez-nous !" (le festival \
n'existe plus, on ne peut plus y aller)
bonne cloture : "Dix ans de scenes electro qui laissent une trace dans la ville. \
On espere que quelque chose renaitra de ce terrain-la."

5. AUCUNE PHRASE NE CONTREDIT L'ARTICLE : la regle de cloture n'en est qu'un cas \
particulier. Chaque phrase de la legende ne doit supposer AUCUN fait que l'article \
contredit -- ne suppose pas qu'un evenement aura lieu s'il est annule, qu'un \
artiste tourne s'il arrete, qu'un groupe sortira un album s'il se separe, qu'un \
lieu est ouvert s'il a ferme. Relis la legende en te demandant, pour chaque \
phrase : l'article dit-il le contraire ?

6. EMOJIS : 2 a 4 emojis dans la legende (`caption`), jamais deux a la suite, \
jamais un emoji purement decoratif hors sujet -- chacun doit coller a ce que dit \
la phrase. AUCUN emoji dans `headline`, `title` ou `subtitle` : ces trois textes \
sont rendus en image par un moteur de rendu, dans une police precise ; un emoji y \
sort en couleur systeme, casse la direction artistique rose / creme du compte, et \
fausse le calcul de largeur de ligne qui ajuste la taille du texte. Choisis \
uniquement dans cette palette, accordee a la charte et aux axes du compte : \
🩷 🎧 🪩 🎪 ✨ 🌱 🔊

7. HASHTAGS : 5 a 12, en minuscules, pertinents et specifiques (nom d'artiste, nom \
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
        # Brouillon valide mais aux emojis hors bornes : rendu si aucune
        # tentative ne fait mieux, plutot que de perdre la composition.
        repli: PostDraft | None = None
        consigne: str | None = None
        for tentative in range(1, max_attempts + 1):
            contenu = build_user_prompt(article)
            if consigne:
                # Le SDK ne renvoie rien au modele : sans ce retour, la relance
                # repart a l'aveugle et refait souvent la meme erreur.
                contenu += (
                    "\n\nTa reponse precedente a ete refusee. Probleme a corriger :\n"
                    f"{consigne}"
                )
            try:
                response = client.messages.parse(
                    model=settings.composer_model,
                    max_tokens=COMPOSER_MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": contenu}],
                    output_format=PostDraft,
                )
            except (pydantic.ValidationError, ValueError) as exc:
                derniere_erreur = exc
                consigne = str(exc)[:1500]
                logger.warning(
                    "composition invalide pour l'article %s (tentative %s/%s) : %s",
                    article.id,
                    tentative,
                    max_attempts,
                    exc,
                )
                continue
            draft = response.parsed_output
            if draft is None:
                # max_tokens atteint ou refus : aucune sortie structuree. Meme
                # traitement qu'un echec de validation, jamais d'AttributeError.
                raison = getattr(response, "stop_reason", None)
                derniere_erreur = ValueError(f"sortie structuree vide (stop_reason={raison})")
                consigne = (
                    "Ta reponse etait vide ou tronquee : rends le JSON complet, "
                    "sans texte autour, en restant concis."
                )
                logger.warning(
                    "composition vide pour l'article %s (tentative %s/%s) : %s",
                    article.id, tentative, max_attempts, raison,
                )
                continue
            probleme = probleme_emojis(draft.caption)
            if probleme is None:
                return draft
            repli = draft
            consigne = probleme
            logger.warning(
                "legende hors bornes d'emojis pour l'article %s (tentative %s/%s) : %s",
                article.id,
                tentative,
                max_attempts,
                probleme,
            )
        if repli is not None:
            logger.warning(
                "legende acceptee malgre les emojis pour l'article %s : %s",
                article.id,
                probleme_emojis(repli.caption),
            )
            return repli
        raise CompositionError(
            f"composition impossible pour l'article {article.id} "
            f"apres {max_attempts} tentatives"
        ) from derniere_erreur

    return composer


def _fond_par_defaut(article: Article, selecteur=None) -> str | None:
    """La photo de l'article, sinon la vignette d'une video.

    Une actu video n'a pas de photo, et sa vignette fait un fond de cover
    parfaitement valable -- c'est meme le cas normal pour ce type d'actu. Jamais
    le visuel d'un compte media Instagram.
    """
    # DELEGUE a `image_de_fond`, meme regle et meme nettoyage d'URL que pour les
    # actifs choisis : deux definitions du fond finiraient par diverger.
    return image_de_fond(visuels_autorises(article), selecteur)


def create_post(
    db: Session,
    article: Article,
    draft: PostDraft,
    template: str = "kinda_post",
    visuels=None,
    selecteur_fond=choisir_illustration,
) -> Post:
    """Persiste un brouillon : les quatre variantes, une cover sans retenue, et les
    visuels choisis (`ChoixVisuels`, cf. `choix_visuels.py`).

    Avec `visuels` : les actifs retenus AVANT l'appel paye sont ceux que le post
    publiera -- le fond de cover est leur premiere image (la copie locale d'une
    photo d'artiste, dont l'URL du CDN Instagram aura expire au gel) et le credit
    en decoule. Un choix vide est un choix : aucun fond, aucun actif.

    Sans `visuels` (composition a la main, appels anterieurs a la regle) : le fond
    vient des medias AUTORISES de l'article et `assets` reste NULL.

    `selecteur_fond` classe les images candidates avant d'en retenir une (cf.
    `app/content/illustration.py`) : l'utilisateur veut une illustration de
    l'evenement ou de l'artiste, jamais une affiche de line-up. C'est le SEUL
    endroit ou ce juge visuel est paye -- une fois par composition, quel que soit
    le gabarit, donc la cover reel suit la meme regle. Le passer a None rend le
    comportement d'avant (la premiere image exploitable).

    Les champs sont DENORMALISES expres : l'article sera purge a 14 jours, le post
    doit rester lisible sans lui.
    """
    premiere = draft.variantes[0]
    if visuels is not None:
        actifs, credit = list(visuels.actifs), visuels.credit
        fond = image_de_fond(actifs, selecteur_fond)
        if actifs and fond is None:
            # Des actifs choisis mais aucun fond extractible : la cover partirait
            # sur l'image de secours du gabarit, et le gel refuserait le post a
            # chaque passage (le creneau bloque de l'article 17974). Le choix des
            # visuels garantit un fond -- si on est ici, c'est lui qui est casse,
            # et il vaut mieux une erreur bruyante qu'une slide inpubliable en
            # base. `assets == []` reste legitime (composition a la main).
            raise ValueError(
                f"visuels sans image de fond pour l'article {article.id} : {len(actifs)} actif(s)"
            )
    else:
        actifs = None
        credit = credit_video(article)
        fond = _fond_par_defaut(article, selecteur_fond)
    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=article.source.name if article.source is not None else None,
        credit=credit,
        assets=actifs,
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
                "image_url": fond,
            }
        ],
        variants={
            "variantes": [v.model_dump() for v in draft.variantes],
            "retenue": None,
            # La teinte est FIGEE ici : l'article sera purge a 14 jours, le post
            # doit rester rendable sans lui (meme motif que les champs
            # denormalises ci-dessus). Editable ensuite sans re-scorer.
            "palette": teinte_de_article(article),
        },
    )
    db.add(post)
    db.commit()
    logger.info("post %s cree pour l'article %s", post.id, article.id)
    return post
