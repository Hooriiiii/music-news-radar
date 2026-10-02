"""La porte de candidature sur le RESUME, et son garde-fou de recurrence.

**Pourquoi.** `cluster.py` rapproche par recouvrement des mots du TITRE. Mesure
du 2026-09-24 sur la fenetre reelle de 48 h (599 articles, 170 820 paires) :
de vrais memes-evenements y echappent ENTIEREMENT, parce que deux redactions
peuvent couvrir la meme actu sans partager un seul mot porteur de titre --
« 2 3 weeks! » (Billboard Charts) et « Ella Langley's "Choosin' Texas" Breaks
Record for Most Weeks at No. 1 » (Pitchfork) sont le meme record ; « Vinyl
counterfeiter who made $3.6M ... jailed » (MBW) et « Mastermind behind UK's
biggest-ever vinyl counterfeit operation sentenced to prison » (Mixmag) sont
la meme condamnation. Tous ces articles restent eligibles a la publication
separement, c'est-a-dire le risque de poster deux fois la meme actu.

**Ce qui les attrape, et ce qui ne les attrape pas.** Le recouvrement NORMALISE
des resumes echoue : il sature sur les captions Instagram courtes (cinq mots
significatifs suffisent a faire 0,71). C'est le nombre ABSOLU de mots
significatifs partages qui separe -- mesure du 2026-09-24 :

    seuil   paires / 48 h   dont vrais
    sh >= 7        9            9
    sh >= 6       17           10
    sh >= 5       43           13

Le seuil retenu (`cluster_arbitrage_resume_seuil`, 6) est une borne de BUDGET,
pas de precision : un seuil bas ne pose PAS de mauvais liens -- c'est
l'ARBITRE existant qui tranche, et il a rejete 27 faux sur 27 -- il paie
seulement des appels pour rien. Abaisser le seuil coute de l'argent, jamais un
lien a tort. Consequence assumee du seuil a 6 : la levee de fonds Unit1 Studio
(MBW x Mixmag), mesuree a sh=5 le jour de la mise au point, n'est pas attrapee.

**Le traitement du resume fausse en silence si on le neglige.** `Article.summary`
est reecrit en francais par le scoring pour un article SCORE (le cas normal
quand `link_duplicates` repasse sur un article deja vu), mais pour un article
NON score c'est la description RSS brute -- parfois l'article entier, avec ses
balises et ses entites HTML -- ou une caption Instagram suivie de son BANDEAU
DE METRIQUES (`instagram._summary_with_metrics`), dont les mots (« likes »,
« commentaires ») ressembleraient d'un article a l'autre sans rien dire de
l'actu. D'ou `jetons_de_resume` : bandeau retire, balises retirees, entites
desechappees, puis les memes `significant_tokens` que les titres -- on ne
reecrit jamais une seconde tokenisation.

**Le garde-fou de RECURRENCE, sans lequel on ne cable pas cette porte.** La
mesure a trouve un faux positif que l'arbitre ne sait pas voir : « Billboard
Hot 100 Top 10 Countdown for Sept. 26, 2026 » contre les quatre articles sur
le record d'Ella Langley. L'arbitre a RAISON sur les faits (meme classement,
meme date), et pourtant lier serait un defaut : c'est une PUBLICATION
RECURRENTE hebdomadaire, pas une couverture de l'annonce -- le lien sortirait
un des deux de la file au nom d'un format qui reviendra la semaine prochaine.

Aucun garde-fou existant ne peut l'attraper : une recurrence HEBDOMADAIRE est
structurellement invisible a un critere qui raisonne sur une fenetre de 48 h,
ou il n'y a qu'un seul Countdown, donc aucune repetition a detecter. Mais
`event_observations` (Phase 2A/2B) denormalise le titre et la source et n'est
JAMAIS purgee, contrairement aux articles (2 jours) : c'est la que la
repetition est lisible.

Critere retenu (`est_publication_recurrente`) : le CADRE d'un titre est
l'ensemble de ses mots significatifs que la MEME source reutilise dans au
moins `min_titres` (4) autres titres archives, repartis sur au moins
`JOURS_MIN` (2) journees distinctes. Un article est une publication recurrente
si son cadre compte au moins `CADRE_MIN` (2) mots ET couvre au moins
`part_min` (0,4) de son titre.

Les deux exigences portent chacune la moitie du raisonnement :
- la FREQUENCE (`min_titres`) separe un cadre de redaction d'un evenement :
  les mots d'un evenement ne reviennent que dans les articles de cet
  evenement, jamais dans quatre titres de plus ;
- l'ETALEMENT SUR PLUSIEURS JOURS separe une recurrence d'une rafale : quatre
  articles du meme jour sur le meme record ne sont pas une serie.

Mesure du 2026-09-24 sur les 56 paires candidates a sh >= 6 : **8 paires
ecartees sans rien payer**, toutes justifiees -- les 7 paires du « Billboard
Hot 100 Top 10 Countdown » (le faux positif nomme) et une paire de recaps de
classement (« This week's top 10 on the Billboard 200 (chart dated Sept. »).
**Aucun des vrais positifs n'est touche** : Ella Langley x4 Billboard,
contrefacon de vinyles MBW x Mixmag et levee de fonds Unit1 Studio MBW x
Mixmag ont TOUS un cadre VIDE (part = 0,00) -- une marge enorme, pas un
reglage a la limite.

Limite a connaitre, et elle est honnete : au moment de la mise au point
`event_observations` ne portait que DEUX jours d'archive (le module Phase 2A
est recent). Le Countdown y est donc reconnu par les mots « billboard » et
« 2026 », pas encore par « countdown » qui n'avait qu'une seule autre
occurrence. Le critere est concu pour des semaines d'archive et deviendra plus
net, jamais moins : plus l'archive s'allonge, plus les vrais mots de cadre
depassent `min_titres`, et les mots d'un evenement ponctuel, eux, ne
grandissent pas.

**Ce qu'il ne faut SURTOUT PAS faire, et c'est mesure** : transposer
`cluster.est_gabarit_de_source` au RESUME. Les deux seules paires qu'il
ecarterait sont deux VRAIS positifs Billboard -- trois articles du meme media
qui couvrent le meme record partagent forcement leurs mots porteurs, et le
critere lit cette repetition comme un gabarit de redaction. `est_gabarit_de_
source` reste donc sur les TITRES, exactement tel qu'il est.
"""

import datetime as dt
import html
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, EventObservation
from app.pipeline import events
from app.pipeline.cluster import significant_tokens
from app.sources.instagram import caption_depuis_summary

# Sens des imports, a ne pas inverser : ce module importe `cluster` (pour
# `significant_tokens`, la SEULE tokenisation du projet), et `cluster` importe
# celui-ci PARESSEUSEMENT, dans `link_duplicates`. Un import croise en tete
# des deux fichiers boucle -- meme piege que `cluster_arbitrage._resume_borne`
# avec `scoring`.

logger = logging.getLogger(__name__)

# Un resume trop court ne dit rien : trois mots significatifs suffisent a
# franchir le seuil par hasard si on les laisse entrer, et une caption vide
# ramenee a deux mots n'a de toute facon aucune actu a partager.
MIN_JETONS_RESUME = 3

# Le cadre doit compter au moins DEUX mots : avec un seul, la part d'un titre
# de deux mots vaudrait 0,50 et n'importe quel titre court portant le nom de
# son media passerait pour une serie.
CADRE_MIN = 2

# Et ces mots doivent revenir sur au moins DEUX journees distinctes : c'est ce
# qui distingue une SERIE (un format qui revient) d'une RAFALE (quatre
# articles du meme jour sur le meme evenement). C'est aussi, litteralement, la
# raison d'etre de ce garde-fou : la fenetre de 48 h ne peut pas le voir.
JOURS_MIN = 2

_BALISES = re.compile(r"<[^>]+>")

# Les deux artefacts STRUCTURELS que les flux WordPress accolent a chaque
# description : le pied « The post <titre> appeared first on <Site>. » et le
# lien de coupure « ... Continue reading <titre> ». Ce ne sont PAS un signal de
# FREQUENCE -- on les reconnait a leur FORME, exactement comme on reconnait le
# bandeau de metriques d'Instagram -- et ils ne parlent jamais de l'actu : les
# retirer ne ressemble en rien au filtre de frequence sur les mots du resume,
# que la mesure interdit (voir le docstring de `cluster.est_gabarit_de_source`).
# Sans eux, « appeared », « first », « post », « stereogum » / « soompi »,
# « continue », « reading » suffisaient a eux seuls a faire franchir le seuil a
# deux articles sans aucun rapport de la meme source.
_PIED_WORDPRESS = re.compile(
    r"\s*\bThe post\b.{0,400}?\bappeared first on\b[^.]{0,80}\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_COUPURE_WORDPRESS = re.compile(
    r"(?:\.{3}|\u2026)?\s*\bContinue reading\b.*$", re.IGNORECASE | re.DOTALL
)


def jetons_de_resume(article: Article) -> set[str]:
    """Les mots significatifs du resume d'un article, bandeau et HTML retires.

    Quatre nettoyages, chacun pour une forme reellement rencontree en base :
    le bandeau de metriques d'une caption Instagram (`caption_depuis_summary`,
    valable AVANT le scoring -- ensuite `apply_score` a remplace le summary par
    le resume du modele et il n'y a plus de bandeau a retirer, la fonction est
    alors un no-op) ; les balises d'une description RSS ; les entites HTML que
    ces descriptions portent (`&amp;`, `&#8217;`).

    Et les deux artefacts de flux WordPress (`_PIED_WORDPRESS`,
    `_COUPURE_WORDPRESS`), reconnus a leur FORME et non a la frequence de
    leurs mots.

    Reutilise `significant_tokens` (mots vides FR+EN, longueur minimale,
    accents replies) : une SECONDE tokenisation divergerait de celle des
    titres a la premiere evolution des mots vides.
    """
    texte = caption_depuis_summary(article.summary)
    if not texte:
        return set()
    texte = html.unescape(_BALISES.sub(" ", texte))
    texte = _PIED_WORDPRESS.sub(" ", texte)
    texte = _COUPURE_WORDPRESS.sub(" ", texte)
    return significant_tokens(texte)


def titre_pauvre(article: Article) -> bool:
    """Vrai si ce titre ne porte PAS assez de contenu pour identifier une actu.

    Meme seuil que `events.TITRE_JETONS_MIN` (3 jetons significatifs), et
    volontairement la MEME constante : deux seuils divergeraient a la premiere
    evolution des mots vides. Voir `paire_sans_titre_exploitable` pour l'usage.
    """
    return len(significant_tokens(article.title)) < events.TITRE_JETONS_MIN


def paire_sans_titre_exploitable(gauche: Article, droite: Article) -> bool:
    """Vrai si NI l'un NI l'autre des deux titres ne porte de contenu.

    Le garde-fou de contenu de `events._evenement_deja_observe`, transpose ici
    (2026-10-02). Un titre qui ne porte RIEN -- « Post Instagram », « This is
    happening.. », « IVE EP » -- ne doit jamais servir de cle d'identite : la
    meme mesure qui a fixe `TITRE_JETONS_MIN` a 3 pour la re-ingestion vaut
    pour cette porte, et le seuil est volontairement le MEME (une seconde
    constante divergerait a la premiere evolution des mots vides).

    Mais la condition est « NI l'un NI l'autre », jamais « l'un des deux » :
    le cas reel du 2026-10-02 est precisement une paire dont UN cote n'a pas de
    titre (« This is happening.. », un jeton significatif) et dont l'autre en a
    six. Exiger les deux reperdrait le defaut qu'on corrige. Ce que ce
    garde-fou ecarte, c'est la paire ou AUCUN des deux titres ne dit rien :
    l'arbitre n'aurait alors que deux resumes a comparer, et la porte du resume
    serait son SEUL rempart.

    Mesure du 2026-10-02 sur les 124 paires fraiches candidates a sh >= 6 :
    UNE SEULE est ecartee, et c'est un faux positif -- « The Mix 125: Sam
    Girling » contre « The Mix 123: Optimo » (deux editions d'une serie, dont
    les resumes partagent la signature « Megan Townsend speaks about club
    moments »). Zero vrai positif touche, des deux cotes de la porte.
    """
    return titre_pauvre(gauche) and titre_pauvre(droite)


@dataclass
class ArchiveTitres:
    """Les titres archives (`event_observations`), groupes par source.

    Chaque entree : (id d'article, mots significatifs du titre, journee). L'id
    sert a ne jamais compter l'article lui-meme comme sa propre preuve de
    recurrence -- une observation porte l'article dont elle vient tant qu'il
    n'a pas ete purge.
    """

    par_source: dict[int | None, list[tuple[int | None, set[str], dt.date]]] = field(
        default_factory=dict
    )

    def __bool__(self) -> bool:
        return bool(self.par_source)


def charger_archive(db: Session, jours: int) -> ArchiveTitres:
    """Lit l'archive des titres, bornee a `jours`.

    Ne LEVE jamais et n'exige pas la migration `0019` : sans les tables
    d'evenements, l'archive est vide et `est_publication_recurrente` rend
    toujours False -- une fonctionnalite degradee, jamais un pipeline qui
    casse (meme philosophie qu'`events.attacher`).

    L'appelant ne doit l'appeler que s'il a REELLEMENT une paire a juger :
    c'est la seule requete que cette porte ajoute, et un passage sans candidat
    ne doit rien couter.
    """
    if not events._tables_disponibles(db):
        return ArchiveTitres()
    depuis = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=jours)
    par_source: dict[int | None, list[tuple[int | None, set[str], dt.date]]] = {}
    lignes = db.execute(
        select(
            EventObservation.source_id,
            EventObservation.article_id,
            EventObservation.title,
            EventObservation.published_at,
            EventObservation.observed_at,
        ).where(EventObservation.observed_at >= depuis)
    ).all()
    for source_id, article_id, titre, publie_le, observe_le in lignes:
        if source_id is None:
            continue
        quand = publie_le or observe_le
        if quand is None:
            continue
        par_source.setdefault(source_id, []).append(
            (article_id, significant_tokens(titre), quand.date())
        )
    return ArchiveTitres(par_source)


def cadre_de_titre(
    article: Article, archive: ArchiveTitres, min_titres: int
) -> tuple[set[str], float]:
    """Le CADRE d'un titre et la part du titre qu'il couvre.

    Un mot entre dans le cadre s'il revient dans au moins `min_titres` AUTRES
    titres archives de la MEME source, etales sur au moins `JOURS_MIN`
    journees distinctes.
    """
    jetons = significant_tokens(article.title)
    if not jetons:
        return set(), 0.0
    observations = archive.par_source.get(article.source_id, ())
    cadre: set[str] = set()
    for jeton in jetons:
        jours = [
            journee
            for (article_id, titre_jetons, journee) in observations
            if article_id != article.id and jeton in titre_jetons
        ]
        if len(jours) >= min_titres and len(set(jours)) >= JOURS_MIN:
            cadre.add(jeton)
    return cadre, len(cadre) / len(jetons)


def est_publication_recurrente(
    article: Article, archive: ArchiveTitres, min_titres: int, part_min: float
) -> bool:
    """Vrai si la source publie REGULIEREMENT un titre de cette forme.

    Voir le docstring du module pour la mesure, les deux exigences (frequence
    et etalement sur plusieurs jours) et la limite connue de l'archive courte.
    """
    cadre, part = cadre_de_titre(article, archive, min_titres)
    return len(cadre) >= CADRE_MIN and part >= part_min
