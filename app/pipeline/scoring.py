import logging
from dataclasses import dataclass, field
from enum import Enum

import anthropic
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, ScoredBy, SourceType
from app.pipeline.comptes_artistes import IndexComptes, construire_index
from app.pipeline.election import tailles_clusters
from app.sources.instagram import (
    TITRE_PAR_DEFAUT,
    caption_depuis_summary,
    caption_quasi_vide,
)

logger = logging.getLogger(__name__)


class Category(str, Enum):
    NEW_RELEASE = "new_release"
    ANNOUNCEMENT = "announcement"
    EVENT_FESTIVAL = "event_festival"
    # Ajoutee le 2026-09-18. `articles.category` est un VARCHAR(100) SANS
    # contrainte CHECK (cf. `app/models.py` et la migration 0001) : une valeur
    # de plus ne demande aucune migration.
    ECOLOGY = "ecology"
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
  A. LES GROSSES ACTUS de la musique électronique, et de la pop quand elle remplit \
les conditions du territoire adjacent (règle 2) : c'est ce qui fait l'audience. \
-> axe `relevance`.
  B. LA MISE EN AVANT D'ARTISTES LOCAUX, ÉMERGENTS ET UNDERGROUND : c'est la \
mission de Kinda, une structure qui aide les jeunes artistes prometteurs à se \
lancer. -> axe `emergence`.

ATTENTION, règle explicite : Kinda montera un label de musique ÉLECTRONIQUE ; le \
COMPTE, lui, ne se ferme pas à la pop — mais ne l'accueille pas non plus sans \
condition. La pop est un TERRITOIRE ADJACENT, pas un deuxième cœur de ligne : elle \
mérite ses scores hauts quand l'actu remplit une des conditions de la règle 2, \
jamais par défaut au seul motif que l'artiste est connu. Ne la déprioris pas non \
plus au seul prétexte que l'électro est le cœur de ligne : juge chaque actu pop \
sur ces conditions précises, ni plus sévèrement ni plus généreusement que le reste \
de la grille.

Ces deux axes sont INDÉPENDANTS et ne se compensent jamais. Un producteur inconnu \
qui sort un premier EP auto-produit n'intéresse pas le feed d'actu (relevance 20) \
mais c'est un signal précieux à suivre (emergence 75). À l'inverse, une tournée \
mondiale de David Guetta est une grosse actu (relevance 85) sans aucun intérêt \
d'émergence (emergence 0).

1. Cœur de ligne : la MUSIQUE ÉLECTRONIQUE (house, techno, drum & bass, EDM, dance, \
expérimental, clubbing, festivals, labels et artistes électro), y compris les \
MUSIQUES DURES (hard techno, hardstyle, hardcore) et la pop à tendance électro.
2. La POP est un TERRITOIRE ADJACENT, pas le cœur de ligne : elle mérite un score \
haut seulement quand l'actu remplit au moins une de ces conditions — très grosse \
actualité internationale (album surprise, décès, scandale majeur, tournée \
événement d'une star mondiale) ; phénomène culturel massif ; artiste très \
important en France ; dance-pop ou electro-pop (la frontière avec le cœur de \
ligne) ; hyperpop ; collaboration avec la scène électronique ; nouveau mouvement \
esthétique ; phénomène générationnel ; tendance susceptible d'influencer les \
nouvelles scènes. LA K-POP SUIT EXACTEMENT LA MÊME RÈGLE que le reste de la pop — \
« lorsque l'actualité est réellement importante », jamais un laissez-passer de \
genre : un comeback confirmé, une tournée mondiale ou un record vaut 60+ (ex. BTS, \
BLACKPINK, Stray Kids, SEVENTEEN, NewJeans, aespa, LE SSERAFIM — des exemples \
d'artistes concernés, pas une liste qui vaudrait passe-droit), mais un classement \
hebdomadaire de charts, une actu de promotion routinière (teaser, cap de vues \
d'un clip, vinyle collector, MV watch) ou une actu qui ne parle qu'à un public \
déjà acquis à la K-pop retombe sous 60 comme n'importe quelle pop mineure. Hors \
ces conditions, une actu pop reste dans la grille normale (40-59 pour une actu \
moyenne, <40 pour du bruit) : elle n'est ni bannie ni protégée.
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
climat d'un artiste, innovation durable dans l'événementiel) : angle recherché, 60+. \
Ces actus-là se rangent dans la catégorie `ecology` (voir plus bas) : le compte \
les habille d'une couleur qui leur est propre, la catégorie n'est donc pas qu'une \
étiquette de tri.
   - Les GROSSES actus musicales générales qui font parler, même transverses.
4. Hors ligne : rap/hip-hop, rock, metal, country, jazz, classique quand l'actu est \
purement interne à ces genres — relevance <40, SAUF si elle relève d'un des axes \
ci-dessus (festival breton multi-genres, initiative écolo, événement historique).
5. QUI PARLE. Chaque contenu indique son « Type de compte » et son « Format ». \
Ces règles portent sur relevance (et hotness), jamais sur emergence :
   - artiste : l'artiste qui annonce SA sortie, SA date, SON projet est une source \
PRIMAIRE. Note-la selon la grille.
   - media (Guettapen, Tsugi, Mixmag, DJ Mag, Electro News, Pop Crave, Konbini, \
Booska-P, AllTime EDM, Billboard, FIP...) : Kinda reprend l'INFORMATION, jamais la \
formulation ni les visuels du média. Note l'information elle-même, exactement comme \
si elle venait de la source primaire : AUCUNE décote parce qu'elle arrive par un \
média. Une actu reprise par plusieurs médias est souvent une GROSSE actu.
   - festival / label / club : une annonce OFFICIELLE (line-up, dates, billetterie, \
annulation, sortie) est une source primaire et garde sa note ; la promo courante \
(rappel de billetterie, compte à rebours, visuel d'ambiance) vaut 40-55.
   - inconnu ou autre : juge sur le contenu seul.
6. POSTS SANS INFORMATION : relevance <30, quel que soit le compte, star comprise. \
Légende vide ou quasi vide (titre « Post Instagram »), emojis/hashtags/mentions \
seuls, selfie ou photo d'ambiance, promo générique sans objet (« link in bio », \
« out now » sans dire quoi), teaser d'idole pop/K-pop sans fait (photo concept, \
version alternative d'un titre déjà sorti, « D-3 »). La notoriété du compte ne \
remplace pas une info. Une VRAIE actu pop/K-pop (comeback daté, album, tournée, \
record) échappe à ce plafond de 30 — mais retombe ensuite sous la règle 2 : elle \
n'est notée haut que si elle remplit une des conditions du territoire adjacent.

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
  3. ecology : le SUJET de l'actu est le rapport de la musique à l'ÉCOLOGIE ou \
au CLIMAT — festival qui verdit son édition (énergie, mobilité, déchets, \
gobelets, bilan carbone), prise de position climat d'un artiste ou d'un label, \
tournée ou événementiel durable, innovation technique pour réduire l'empreinte, \
menace climatique sur un événement (canicule, sécheresse, inondation). Il faut \
que l'écologie soit LE SUJET, pas un détail : une affiche verte, une photo de \
nature, un festival « en plein air », un nom d'artiste ou de label qui évoque la \
nature, ou une simple mention en passant ne suffisent PAS. Dans le doute, prends \
la catégorie suivante qui s'applique. Prime sur event_festival : une édition qui \
passe au 100 % renouvelable est `ecology`, son line-up reste `event_festival`.
  4. event_festival : le sujet est un ÉVÉNEMENT physique daté — festival, soirée, \
club, rave. Line-up, programmation, édition, billetterie, bilan, annulation, \
fermeture ou ouverture de lieu. Vaut MÊME quand l'actu est une annonce : un \
line-up dévoilé est `event_festival`, jamais `announcement`.
  5. new_release : de la musique DÉJÀ ÉCOUTABLE — single, EP, album, remix, mix, \
clip sortis. Le critère est la disponibilité immédiate, pas le fait qu'on en parle.
  6. announcement : quelque chose est annoncé POUR PLUS TARD et n'est pas un \
événement au sens 4 — sortie à venir, tournée, date, collaboration confirmée, \
retour d'un artiste. Si c'est déjà écoutable, c'est 5 ; si c'est un festival ou \
une soirée, c'est 4.
  7. gear_tech : matériel, logiciel, plugin, contrôleur, synthé, IA musicale, \
outils de production.
  8. industry : le business — labels, streaming, droits, chiffres de vente, \
rachats, nominations, plateformes, réglementation.
  9. culture : analyse, portrait, interview, rétrospective, tendance de fond, \
histoire d'un genre. Du contenu de fond, pas une actu chaude.
  10. other : rien de ce qui précède.
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


# Le format dit COMMENT l'info arrive ; le type de compte (`sources.categorie`)
# dit QUI la produit. Le prompt système s'appuie sur les deux (règles 5 et 6).
FORMATS = {
    SourceType.INSTAGRAM: "post Instagram",
    SourceType.RSS: "article de presse",
    SourceType.REDDIT_RSS: "post Reddit",
    SourceType.X: "tweet",
    SourceType.BLUESKY: "post Bluesky",
}


# Un article pas encore score peut porter la description RSS brute (parfois
# l'article entier) ou la caption Instagram avec son bandeau de metriques :
# rien ne le borne en amont. Reutilisee par `cluster_arbitrage.py`, qui
# arbitre AVANT le scoring et recoit donc le meme resume brut -- un cout
# d'entree qui doit rester borne de la meme facon.
RESUME_MAX_CARACTERES = 1500


def build_user_prompt(article: Article) -> str:
    parts = [f"Titre : {article.title}"]
    if article.summary:
        parts.append(f"Extrait : {article.summary[:RESUME_MAX_CARACTERES]}")
    if article.source is not None:
        parts.append(f"Source : {article.source.name}")
        parts.append(f"Format : {FORMATS.get(article.source.type, 'inconnu')}")
        categorie = article.source.categorie
        parts.append(f"Type de compte : {categorie.value if categorie else 'inconnu'}")
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


# --- Garde-fou déterministe ------------------------------------------------------
# Mesuré le 2026-09-17 sur données réelles : Haiku applique la règle 6 de façon
# inégale (IVE « [📸] » 65, LE SSERAFIM « feat. » 72). Le code tranche donc après
# le modèle, sur tous les chemins de scoring. Le plafond des comptes media a été
# retiré le même jour : il enterrait des grosses actus (GTA VI × Fred again..).
PLAFOND_POST_VIDE = 29


def post_quasi_vide(article: Article) -> bool:
    """Un post Instagram dont la légende ne porte pas d'information.

    Lit la caption dans `article.summary` : à appeler AVANT `apply_score`, qui
    remplace le summary par le résumé du modèle. Sans caption connue, seul le
    titre par défaut de l'adapter (« Post Instagram ») fait foi.
    """
    source = article.source
    if source is None or source.type != SourceType.INSTAGRAM:
        return False
    if article.summary is None:
        return article.title == TITRE_PAR_DEFAUT
    return caption_quasi_vide(caption_depuis_summary(article.summary))


def appliquer_garde_fous(article: Article, score: ArticleScore) -> ArticleScore:
    """Post Instagram quasi vide -> relevance et hotness plafonnées à 29, quel que
    soit le compte. L'axe emergence n'est jamais touché. Fonction pure : rend une
    copie."""
    if not post_quasi_vide(article):
        return score
    return score.model_copy(
        update={
            "relevance": min(score.relevance, PLAFOND_POST_VIDE),
            "hotness": min(score.hotness, PLAFOND_POST_VIDE),
        }
    )


def score_minimal(article: Article) -> ArticleScore:
    """La note d'un post quasi vide quand le pré-filtre local est muet : Claude
    n'aurait rien à juger, on ne le paie pas. La caption (sans métriques) sert de
    résumé pour rester lisible au dashboard."""
    caption = caption_depuis_summary(article.summary)
    return ArticleScore(
        relevance=10,
        hotness=10,
        emergence=0,
        category=Category.OTHER,
        summary=caption or article.title or TITRE_PAR_DEFAUT,
    )


@dataclass
class ScoringStats:
    scored: int = 0
    errors: int = 0
    # Articles écartés par le pré-filtre local, donc jamais soumis à Claude.
    prefiltered: int = 0
    # Posts Instagram quasi vides, notés sans Claude (inclus dans `prefiltered`).
    quasi_vides: int = 0
    # Articles pour lesquels le pré-filtre était branché mais n'a RIEN rendu
    # (LM Studio éteint, modèle déchargé, contexte trop court, timeout). Chacun
    # est un appel Claude payé que le pré-filtre aurait pu éviter -- et sans ce
    # compteur, la panne ne se voit nulle part.
    prefiltre_muet: int = 0
    # Notes locales sous le seuil envoyées quand même à Claude (2026-09-17) :
    # groupe d'au moins N sources, ou artiste suivi cité.
    forces_cluster: int = 0
    forces_artiste: int = 0
    # Le message de la dernière erreur, pour que la supervision puisse NOMMER la
    # panne. Sans lui, des crédits épuisés passent pour une erreur réseau parmi
    # d'autres -- c'est ce qui les a rendus invisibles pendant des jours.
    derniere_erreur: str | None = None


RAISON_CLUSTER = "cluster"
RAISON_ARTISTE = "artiste_suivi"


@dataclass
class ContexteScoring:
    """Ce que la cascade doit savoir d'un run entier, calculé une fois : les
    comptes d'artistes suivis et la taille (en sources) des groupes de doublons."""

    index: IndexComptes
    tailles: dict[int, int] = field(default_factory=dict)

    def raison_claude(self, article: Article, note: ArticleScore | None) -> str | None:
        """Pourquoi un article noté sous le seuil doit quand même aller chez
        Claude, ou None. L'actu GTA VI x Fred again.. a été écartée à 15 et 25."""
        if self.tailles.get(article.id, 1) >= settings.cluster_sources_claude:
            return RAISON_CLUSTER
        noms = [*(note.artists if note is not None else []), *(article.mentioned_artists or [])]
        if self.index.cites(
            textes=[article.title, article.summary], noms=noms, exclure_source_id=article.source_id
        ):
            return RAISON_ARTISTE
        return None


def construire_contexte(db: Session, articles: list[Article]) -> ContexteScoring:
    return ContexteScoring(construire_index(db), tailles_clusters(db, [a.id for a in articles]))


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
    db: Session,
    scorer=None,
    limit: int | None = None,
    prefilter=None,
    contexte: ContexteScoring | None = None,
) -> ScoringStats:
    """Score tous les articles pas encore évalués (relevance_score IS NULL).

    Cascade à deux étages quand un pré-filtre est disponible : un petit modèle
    local note d'abord, et l'article n'atteint Claude que si sa note atteint
    `local_prefilter_threshold`. Sous le seuil, on garde la note locale et on
    n'appelle jamais l'API payante -- sauf pour un représentant d'un groupe d'au
    moins `cluster_sources_claude` sources ou un article qui cite un artiste
    suivi (`ContexteScoring.raison_claude`).

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
    if contexte is None:
        contexte = construire_contexte(db, articles)

    stats = ScoringStats()
    for article in articles:
        try:
            note = _note_du_prefiltre(prefilter, article)
            if prefilter is not None and note is None:
                # Le pré-filtre était là et n'a rien dit : LM Studio éteint,
                # modèle déchargé, contexte trop court, timeout. L'article part
                # chez Claude et tout se passe bien -- sauf qu'on PAIE ce que le
                # pré-filtre existe précisément pour ne pas payer.
                #
                # Sans ce compteur la panne est INVISIBLE : elle ne produit que
                # des WARNING par article, et le rapport masque la ligne du
                # pré-filtre tant qu'il n'a rien écarté. Mesuré le 2026-09-24 :
                # LM Studio était éteint et chaque passage horaire payait le
                # plein tarif sans qu'aucune sortie ne le dise. C'est la forme
                # exacte de la panne qui a dure cinq semaines sur l'agent digest.
                stats.prefiltre_muet += 1
            # Post quasi vide : Claude n'aurait rien à juger. La note locale si
            # elle existe (pour son emergence), plafonnée ; sinon une note minimale.
            if post_quasi_vide(article):
                score = note if note is not None else score_minimal(article)
                apply_score(article, appliquer_garde_fous(article, score))
                article.scored_by = ScoredBy.LOCAL
                stats.prefiltered += 1
                stats.quasi_vides += 1
                db.commit()
                continue
            # Seuil STRICT : une note égale au seuil passe chez Claude. Le seuil
            # dit « en dessous, ça ne mérite pas qu'on paie », pas « à partir de ».
            # La décision d'écarter se prend sur la note BRUTE, avant garde-fou.
            sous_le_seuil = note is not None and note.relevance < settings.local_prefilter_threshold
            raison = contexte.raison_claude(article, note) if sous_le_seuil else None
            if sous_le_seuil and raison is None:
                apply_score(article, appliquer_garde_fous(article, note))
                article.scored_by = ScoredBy.LOCAL
                stats.prefiltered += 1
            else:
                apply_score(article, appliquer_garde_fous(article, scorer(article)))
                article.scored_by = ScoredBy.CLAUDE
                stats.scored += 1
                if raison == RAISON_CLUSTER:
                    stats.forces_cluster += 1
                elif raison == RAISON_ARTISTE:
                    stats.forces_artiste += 1
            db.commit()
        except Exception as exc:
            logger.exception("Scoring échoué pour l'article %s (%s)", article.id, article.title)
            db.rollback()
            stats.errors += 1
            stats.derniere_erreur = str(exc)
    return stats
