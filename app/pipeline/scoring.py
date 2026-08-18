import logging
from dataclasses import dataclass
from enum import Enum

import anthropic
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, ScoredBy

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
    # Axe A&R, volontairement INDÉPENDANT de relevance : un inconnu qui sort un
    # premier EP est du bruit pour le feed et un signal fort pour le label.
    emergence: int = Field(ge=0, le=100)
    category: Category
    imprint: str | None = None
    summary: str
    artists: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """\
Tu es le filtre éditorial de KINDA, un compte Instagram FRANÇAIS d'actualité \
musicale à la ligne éditoriale précise : POP / ÉLECTRO.

Kinda s'adresse à un public francophone NATIONAL (toute la France) : la scène \
électro française dans son ensemble — Paris, Lyon, Marseille, Bordeaux, Lille, \
Toulouse, Nantes, Rennes, Brest... — au même titre que l'actu internationale. \
Ce n'est PAS un compte régional.

Le compte tient sur DEUX PILIERS, et tu notes chaque contenu sur les deux :
  A. LES GROSSES ACTUS de la musique électronique ET de la POP : c'est ce qui \
fait l'audience. -> axe `relevance`.
  B. LA MISE EN AVANT D'ARTISTES LOCAUX, ÉMERGENTS ET UNDERGROUND : c'est la \
mission de Kinda, une structure qui aide les jeunes artistes prometteurs à se \
lancer. -> axe `emergence`.

ATTENTION, règle explicite : Kinda montera un label de musique ÉLECTRONIQUE, mais \
le COMPTE, lui, ne se ferme pas à la pop. Les grosses actus pop (et K-pop) gardent \
toute leur place et leurs scores hauts — ne les déprioris JAMAIS au prétexte que \
l'électro est le cœur de ligne ou que le label sera électro.

Ces deux axes sont INDÉPENDANTS et ne se compensent jamais. Un producteur inconnu \
qui sort un premier EP auto-produit n'intéresse pas le feed d'actu (relevance 20) \
mais c'est un signal précieux à suivre (emergence 75). À l'inverse, une tournée \
mondiale de David Guetta est une grosse actu (relevance 85) sans aucun intérêt \
d'émergence (emergence 0).

1. Cœur de ligne : la MUSIQUE ÉLECTRONIQUE (house, techno, drum & bass, EDM, dance, \
expérimental, clubbing, festivals, labels et artistes électro).
2. Bienvenues aussi : les GROSSES actus POP mainstream (stars internationales : \
album surprise, décès, scandale majeur, tournée événement), Y COMPRIS LA K-POP \
(BTS, BLACKPINK, Stray Kids, SEVENTEEN, NewJeans, aespa, LE SSERAFIM... : comebacks, \
sorties, tournées, récompenses, fancams virales) — la K-pop fait partie de la pop \
mainstream de ce compte, à valoriser comme telle.
3. Axes prioritaires TRANSVERSAUX, à valoriser même hors du strict club électro :
   - FESTIVALS ET ÉVÉNEMENTS à toutes les échelles : annonces, line-ups, éditions, \
ouvertures de billetterie, soirées et clubs marquants. C'est le PILIER du compte. \
Les festivals FRANÇAIS (Dour côté belge francophone, Delta, Marsatac, Nuits Sonores, \
Family Piknik, Garorock, Hellfest, Solidays, Rock en Seine, Astropolis, Panoramas, \
Vieilles Charrues, Trans Musicales...) comptent autant que les grands rendez-vous \
internationaux (Tomorrowland, Awakenings, Time Warp, Sónar, Ultra, Coachella).
   - Léger bonus BRETAGNE : à intérêt éditorial comparable, une actu bretonne \
(Astropolis, Panoramas, Route du Rock, scènes rennaise et brestoise) passe devant. \
C'est un bonus de départage, PAS une ligne à part : ne surnote pas une actu bretonne \
sans intérêt, et ne fais jamais de la Bretagne le sujet principal du compte.
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
  60-79 : actu électro intéressante ; actu pop notable ; annonce de festival ou \
d'événement français ; info positive marquante ; angle musique-écologie.
  40-59 : actu électro mineure, actu pop moyenne, festival très confidentiel.
  <40 : bruit (question perso, meme, discussion sans info) OU actu hors ligne ne \
relevant d'aucun axe prioritaire. La sortie d'un artiste inconnu tombe souvent ici \
— c'est normal, elle sera rattrapée par l'axe `emergence` ci-dessous.
- emergence (0-100) : intérêt A&R. « Cet artiste mérite-t-il d'être suivi, mis en \
avant, voire accompagné par Kinda ? » Rien à voir avec la taille de l'actu.
  80+ : artiste local/émergent avec un vrai signal (premier EP remarqué, passage \
en club reconnu, soutien d'un label ou d'un DJ établi, engouement inhabituel).
  50-79 : artiste peu connu qui apparaît dans un média ou une programmation \
sérieuse ; scène underground française ; jeune producteur qui sort quelque chose.
  20-49 : artiste en milieu de carrière, déjà installé mais pas mainstream.
  <20 : artiste déjà établi ou star confirmée, contenu sans artiste identifiable.
  L'AUTOPROMO d'un inconnu n'est PAS du bruit sur cet axe : si le morceau ou le \
projet a l'air sérieux (sortie réelle, label, retours positifs), note 50+. Reste \
lucide en revanche sur le spam pur (lien nu, message répété, contenu vide).
  Priorité aux artistes FRANÇAIS et à la scène locale : à signal comparable, un \
producteur français ou breton passe devant un artiste étranger.
- hotness (0-100) : urgence et potentiel viral, indépendamment de relevance.
  80+ : breaking news à poster dans l'heure (décès, annonce surprise, drama chaud, \
séparation/reformation) — et uniquement si l'actu est dans la ligne éditoriale. \
Une interview de fond ou une rétrospective reste froide (<40) même si pertinente.
- category : le type d'actu. Parcours cette liste DANS L'ORDRE et prends la \
PREMIÈRE qui s'applique — l'ordre va du plus spécifique au plus général, il tranche \
donc les cas où deux catégories sembleraient convenir.
  1. rip : un décès. Rien d'autre ne prime là-dessus.
  2. drama : conflit, polémique, accusation, procès, clash public, séparation \
houleuse, annulation pour scandale.
  3. event_festival : le sujet est un ÉVÉNEMENT physique daté — festival, soirée, \
club, rave. Line-up, programmation, édition, billetterie, bilan, annulation, \
fermeture ou ouverture de lieu. Vaut MÊME quand l'actu est une annonce : un \
line-up dévoilé est `event_festival`, jamais `announcement`.
  4. new_release : de la musique DÉJÀ ÉCOUTABLE — single, EP, album, remix, mix, \
clip sortis. Le critère est la disponibilité immédiate, pas le fait qu'on en parle.
  5. announcement : quelque chose est annoncé POUR PLUS TARD et n'est pas un \
événement au sens 3 — sortie à venir, tournée, date, collaboration confirmée, \
retour d'un artiste. Si c'est déjà écoutable, c'est 4 ; si c'est un festival ou \
une soirée, c'est 3.
  6. gear_tech : matériel, logiciel, plugin, contrôleur, synthé, IA musicale, \
outils de production.
  7. industry : le business — labels, streaming, droits, chiffres de vente, \
rachats, nominations, plateformes, réglementation.
  8. culture : analyse, portrait, interview, rétrospective, tendance de fond, \
histoire d'un genre. Du contenu de fond, pas une actu chaude.
  9. other : rien de ce qui précède.
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
Ne sur-note pas la hotness : réserve 80+ aux actus qui ne peuvent pas attendre demain.

RÈGLE DE DÉPARTAGE, à appliquer avant de rendre tes notes. Quand tu hésites entre \
deux tranches, prends TOUJOURS LA PLUS BASSE. Ce filtre existe pour ÉCARTER du \
bruit : une actu moyenne notée trop haut pollue le feed, alors qu'une bonne actu \
notée un peu bas reste visible dans le classement. Le coût des deux erreurs n'est \
pas le même, ne les traite pas de la même façon.

Concrètement, les trois bornes qui comptent :
- 60 sépare ce qui MÉRITE D'ÊTRE VU de ce qui ne le mérite pas. Ne mets 60 ou plus \
que si tu peux dire en une phrase pourquoi l'abonné devrait s'y arrêter. « C'est \
de la musique électro » ne suffit pas — il faut un fait : un nom connu, une date, \
un chiffre, un lieu, un événement.
- 80 sépare la grosse actu du reste. Une annonce ordinaire, même parfaitement dans \
la ligne, vaut 65-75 : 80+ est réservé à ce qui fait réagir tout le monde.
- Sur hotness, 80 déclenche une notification immédiate. Demande-toi : est-ce que ça \
justifie de réveiller quelqu'un maintenant ? Si la réponse est « ça peut attendre \
demain », alors c'est en dessous de 80, quelle que soit la qualité de l'actu.\
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
    article.emergence_score = score.emergence
    article.category = score.category.value
    article.imprint = score.imprint
    # Le résumé éditorial de Claude remplace l'extrait brut du flux
    article.summary = score.summary
    article.mentioned_artists = score.artists or None


@dataclass
class ScoringStats:
    scored: int = 0
    errors: int = 0
    # Articles écartés par le pré-filtre local, donc jamais soumis à Claude.
    prefiltered: int = 0
    # Le message de la dernière erreur, pour que la supervision puisse NOMMER la
    # panne. Sans lui, des crédits épuisés passent pour une erreur réseau parmi
    # d'autres -- c'est ce qui les a rendus invisibles pendant des jours.
    derniere_erreur: str | None = None


def _note_du_prefiltre(prefilter, article: Article) -> ArticleScore | None:
    """La note du pré-filtre, ou None s'il n'a rien à dire.

    `make_local_scorer` promet déjà de ne jamais lever, mais un pré-filtre injecté
    par un test ou une future implémentation ne tient pas forcément cette promesse.
    On l'isole ici : le pré-filtre est une OPTIMISATION de coût, jamais une raison
    de perdre un article — quoi qu'il arrive, on retombe sur Claude.
    """
    if prefilter is None:
        return None
    try:
        return prefilter(article)
    except Exception:
        logger.warning(
            "Pré-filtre en échec sur l'article %s : on passe par Claude", article.id
        )
        return None


def score_pending(
    db: Session, scorer=None, limit: int | None = None, prefilter=None
) -> ScoringStats:
    """Score tous les articles pas encore évalués (relevance_score IS NULL).

    Cascade à deux étages quand un pré-filtre est disponible : un petit modèle
    local note d'abord, et l'article n'atteint Claude que si sa note atteint
    `local_prefilter_threshold`. Sous le seuil, on garde la note locale et on
    n'appelle jamais l'API payante.

    Commit après chaque article : un run interrompu ne perd pas les appels API
    déjà payés, et un article qui échoue n'annule pas les autres.
    """
    if scorer is None:
        scorer = make_claude_scorer()
    if prefilter is None and settings.local_scoring_enabled:
        from app.pipeline.prefilter import make_local_scorer

        prefilter = make_local_scorer()
    # Les doublons d'une actu déjà couverte ne sont pas scorés : ils seraient
    # payés deux fois pour la même information (cf. pipeline/cluster.py)
    stmt = (
        select(Article)
        .where(Article.relevance_score.is_(None))
        .where(Article.duplicate_of_id.is_(None))
        .order_by(Article.fetched_at)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    articles = db.scalars(stmt).all()

    stats = ScoringStats()
    for article in articles:
        try:
            note = _note_du_prefiltre(prefilter, article)
            # Seuil STRICT : une note égale au seuil passe chez Claude. Le seuil
            # dit « en dessous, ça ne mérite pas qu'on paie », pas « à partir de ».
            if note is not None and note.relevance < settings.local_prefilter_threshold:
                apply_score(article, note)
                article.scored_by = ScoredBy.LOCAL
                stats.prefiltered += 1
            else:
                apply_score(article, scorer(article))
                article.scored_by = ScoredBy.CLAUDE
                stats.scored += 1
            db.commit()
        except Exception as exc:
            logger.exception("Scoring échoué pour l'article %s (%s)", article.id, article.title)
            db.rollback()
            stats.errors += 1
            stats.derniere_erreur = str(exc)
    return stats
