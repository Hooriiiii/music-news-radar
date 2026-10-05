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

import datetime as dt
import inspect
import logging
from zoneinfo import ZoneInfo

import anthropic
import pydantic
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.content.choix_visuels import VisuelIntrouvable, VisuelsDifferes, actif_du_web
from app.content.credit import credit_video
from app.content.accents import mot_desaccentue, porte_un_accent
from app.content.emojis import EMOJI_RE as _EMOJI_RE
from app.content.highlight import enumeration_incoherente, validate_headline
from app.content.legende import sans_mentions_de_source
from app.content.reperes import JOURS as JOURS_SEMAINE, repere_relatif
from app.content.illustration import (
    Fond,
    Verdict,
    choisir_illustration,
    eviter_l_affiche,
    lecture_memorisee,
    plafonner_affiches,
    prejuger,
)
from app.content.layout import gabarit as gabarit_de
from app.content.teinte import teinte_de_article
from app.content.media import PHOTO
from app.content.visuels import (
    ORIGINE_ARTICLE,
    ORIGINE_KINDA,
    ORIGINE_WEB,
    actif,
    candidats_de_fond,
    credit_des_actifs,
    image_de,
    image_de_fond,
    origine_du_fond,
    visuels_autorises,
)
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

# En dessous de cette longueur, une legende francaise peut legitimement n'avoir
# aucun accent (« Le line-up tombe ce week-end » n'en porte pas un seul). Au-dela,
# c'est le volume qui rend l'absence invraisemblable : une legende de 2 a 4
# paragraphes, ce que demande la regle 4, depasse largement 200 caracteres, et
# deux des quatre posts du 2026-09-18 sont partis en ASCII force (« un titre
# pense pour la bande son », « intitule », « depasse ») -- ca fait traduction
# automatique. Seuil bas expres : le controle ne bloque rien, il relance.
CAPTION_ACCENTS_MIN = 200


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


def contient_un_accent(texte: str) -> bool:
    """Vrai si `texte` porte au moins une lettre accentuee.

    Decomposition NFD plutot qu'une liste de caracteres : le signe diacritique
    devient un caractere combinant, quelle que soit la lettre qui le porte (e, a,
    u, c cedille...) et quelle que soit la forme normalisee d'origine. Une seule
    implementation, dans `app/content/accents.py`, pour que les deux controles
    d'accents (la legende et les textes rendus) ne divergent jamais.
    """
    return porte_un_accent(texte)


def probleme_accents(caption: str) -> str | None:
    """Decrit, pour la relance, une legende francaise visiblement desaccentuee.

    Meme mecanique que `probleme_emojis`, et pour la meme raison : ce n'est PAS un
    validateur de `PostDraft` -- jeter le brouillon entier ferait perdre la
    composition d'un creneau. Le compositeur relance, puis accepte avec un
    avertissement. Une legende sans accents vaut mieux que pas de post.
    """
    if len(caption) < CAPTION_ACCENTS_MIN or contient_un_accent(caption):
        return None
    return (
        f"La legende (`caption`) fait {len(caption)} caracteres et ne contient PAS UN SEUL "
        "accent : elle a ete ecrite en ASCII, ce qui fait traduction automatique. Reecris-la "
        "en francais correctement accentue (e accent aigu, e accent grave, a accent grave, "
        "c cedille...), dans `caption` comme dans `headline`, `title` et `subtitle`."
    )


def probleme_cloture_question(caption: str) -> str | None:
    """Decrit, pour la relance, une legende qui se termine par une question ;
    None sinon.

    Releve le 2026-09-21 en ouvrant le compte : les SIX derniers posts publies
    finissaient tous par une question rhetorique (« Qu'en pensez-vous ? »,
    « Vous suivez CORTIS depuis leurs debuts ? », « Qui tente le deplacement
    pour cette derniere session estivale ? »...). Six sur six, ca s'entend comme
    un tic de machine sur un feed. Meme mecanique que `probleme_emojis` et
    `probleme_accents`, et pour la meme raison (docs/reference/generation-de-posts.md, « Emojis de legende :
    relances, jamais bloquants ») : ce n'est PAS un validateur de `PostDraft` --
    jeter le brouillon entier pour un defaut de forme ferait perdre la
    composition d'un creneau. Le compositeur relance, puis accepte avec un
    avertissement : une legende qui finit par une question de plus vaut mieux
    que pas de post.

    Et le prompt dit la MEME chose (revue du 2026-09-21) : il proposait encore
    « une question » parmi les clotures, alors que ce controle les refuse
    toutes -- sur un modele qui en mettait six sur six, c'etait une relance
    quasi systematique, jusqu'a trois appels `composer_model` par composition.
    Un controle sans memoire ne sait pas faire « de temps en temps », seulement
    « jamais » : c'est donc la question qui sort des clotures du prompt, pas le
    controle qui s'assouplit. Une question reste permise AU MILIEU de la legende.
    """
    fin = _EMOJI_RE.sub("", caption).rstrip()
    if not fin.endswith("?"):
        return None
    return (
        "La legende (`caption`) se termine par une question, comme les six "
        "derniers posts publies sur le compte -- cette repetition s'entend comme "
        "un tic de machine. Termine autrement : un fait qui reste pour la fin, "
        "une date ou un lieu a retenir, une projection sur la suite, une phrase "
        "qui referme l'actu. La derniere phrase ne se termine jamais par un "
        "point d'interrogation."
    )


def probleme_enumeration_surlignee(texte: str) -> str | None:
    """Decrit, pour la relance, une enumeration a moitie surlignee ; None sinon.

    Post publie le 2026-10-01 : « **Angele**, **Caroline Polachek** et SebastiAn
    s'unissent sur "Love Triangle" ». Deux artistes sur trois surlignes, sans
    qu'aucune raison ne distingue le troisieme : ca se lit comme un oubli.

    Ce controle ne compte PAS les segments surlignes. La regle 3 en autorise
    deux, et la mesure sur les 1 704 textes de variantes en base (568 accroches,
    568 titres, 568 sous-titres) le justifie : 483 en portent DEUX, et la
    plupart le meritent -- l'artiste et son oeuvre, « **Bicep** referme le
    triptyque **AIR** ». Interdire le second surlignage casserait 28 % des
    textes, dont la majorite sont bons. Ce qui est fautif, c'est
    l'incoherence a l'interieur d'une enumeration -- `highlight.
    enumeration_incoherente`, mesure : 5 textes signales sur 1 704 (0,3 %), les
    cinq fautifs. Le precedent de la cloture en question (jusqu'a trois appels
    `composer_model` par composition) impose cette borne : un controle qui
    signale un texte sur trois est un controle qui relance sans cesse.

    Meme mecanique que `probleme_emojis`, `probleme_accents` et
    `probleme_cloture_question` : pas un validateur de `PostDraft`, une relance
    puis une acceptation avec avertissement. Une accroche mal surlignee vaut
    mieux que pas de post.
    """
    trouvee = enumeration_incoherente(texte)
    if trouvee is None:
        return None
    noms, etats = trouvee
    surlignes = [nom for nom, etat in zip(noms, etats, strict=True) if etat]
    oublies = [nom for nom, etat in zip(noms, etats, strict=True) if not etat]
    return (
        f"Le texte « {texte} » enumere des elements de meme nature "
        f"({', '.join(noms)}) mais n'en surligne qu'une partie : "
        f"{', '.join(surlignes)} entre ** et pas {', '.join(oublies)}. "
        "Dans une enumeration, surligne TOUS les elements ou AUCUN -- une "
        "difference que rien ne justifie se lit comme un oubli. Si l'enumeration "
        "est trop longue pour etre surlignee en entier, surligne plutot un autre "
        "segment du texte (le lieu, la date, le chiffre)."
    )


def probleme_accents_de_cover(texte: str, legende: str) -> str | None:
    """Decrit, pour la relance, un mot desaccentue dans un texte RENDU ; None sinon.

    Le trou que ce controle ferme (post 168, publie le 2026-10-03) : la bande de
    titre du reel disait « Troye Sivan / invite surprise de Charli XCX a l'ACL »
    -- « invite » pour « invité », « a » pour « à » -- pendant que la legende du
    MEME post etait parfaitement accentuee. `probleme_accents` ne recevant que
    `caption`, aucune relance n'etait declenchee : le controle couvrait le texte
    qu'on lit en second et laissait passer celui qu'on voit en premier, dessine
    en gros sur l'image.

    Le critere de `probleme_accents` ne se transpose PAS ici : il demande une
    longueur minimale, et un texte de cover est court. Mesure sur les 1 980
    textes de variantes en base : 58,6 % n'ont aucun accent, 36,9 % encore
    au-dela de 70 caracteres -- un seuil de longueur rendrait le controle inerte
    ou bavard. `app/content/accents.py` cherche donc un MOT precis, par deux
    references (la legende du meme brouillon, et un lexique mesure). Mesure du
    controle complet : 68 textes signales sur 1 980 (3,4 %), 24 posts sur 165
    (14,5 % des compositions), zero faux positif -- dont trois fois le post 168.

    Meme mecanique que `probleme_emojis`, `probleme_accents`,
    `probleme_cloture_question` et `probleme_enumeration_surlignee` : pas un
    validateur de `PostDraft`, une relance puis une acceptation avec
    avertissement. Une cover mal accentuee vaut mieux que pas de post.
    """
    trouve = mot_desaccentue(texte, legende=legende)
    if trouve is None:
        return None
    brut, attendue = trouve
    return (
        f"Le texte « {texte} » ecrit « {brut} » sans son accent : c'est "
        f"« {attendue} ». Ces textes (`headline`, `title`, `subtitle`) sont "
        "DESSINES EN GROS sur l'image du post -- c'est le texte le plus visible "
        "du post, un accent manquant s'y voit bien plus que dans la legende. "
        "Reecris les quatre variantes en francais correctement accentue."
    )


def probleme_repere_relatif(texte: str, *, prose: bool = False) -> str | None:
    """Decrit, pour la relance, un repere temporel RELATIF ; None sinon.

    Le defaut (post 153, publie le 2026-10-04 a 10h21) : la cover annoncait
    « **This Dream Of You** [...] sort a minuit » et la legende « sort ce soir a
    minuit », pour un morceau sorti dans la nuit du 2 au 3 -- le brouillon avait
    ete compose 45,9 heures plus tot. Sur les 12 derniers posts publies, 3
    portaient un repere relatif et 2 etaient FAUX au moment de partir.

    La cause est structurelle : le modele ecrit a la composition, avec l'actu sous
    les yeux, et il ecrit juste A CET INSTANT -- or le post part des heures, parfois
    des jours plus tard (le creneau glisse). Une date absolue, elle, reste vraie
    quelle que soit l'heure de depart. C'est le seul de ces controles qui porte sur
    la VERACITE et non sur la forme, d'ou sa place en tete : un fait faux se voit
    plus qu'un accent manquant.

    Mesure sur la base (1 992 textes de cover, 169 legendes) : 3,8 % des covers et
    9,5 % des legendes signalees, 13,0 % des posts -- l'ordre de grandeur de
    `probleme_accents_de_cover` (3,4 % des textes, 14,5 % des posts), et les 91
    textes signales sont des reperes reels, zero faux positif. Deux formulations
    frequentes sont volontairement HORS table parce qu'elles ne vieillissent pas :
    « dispo des maintenant » (une sortie deja faite le restera) et « bientot »
    (vague par nature). Voir `app/content/reperes.py` pour le detail.

    Meme mecanique que les quatre controles precedents : pas un validateur de
    `PostDraft`, une relance puis une acceptation avec avertissement. Un post qui
    dit « ce soir » vaut encore mieux que pas de post.
    """
    repere = repere_relatif(texte, prose=prose)
    if repere is None:
        return None
    return (
        f"Le texte « {texte} » situe l'actu avec « {repere.expression} », un repere "
        "RELATIF au jour ou tu ecris. Ce post ne part pas maintenant : il est "
        "compose a l'avance et publie plus tard, parfois le lendemain ou le "
        "surlendemain -- « ce soir » et « demain » seront alors FAUX, et un compte "
        "d'actu qui annonce comme a venir ce qui est deja sorti perd sa "
        "credibilite. Donne la date ABSOLUE a la place : « vendredi 3 octobre », "
        "« le 3 octobre a minuit », « du 14 au 16 novembre ». Calcule-la depuis la "
        "date du jour et la date de l'article, qui te sont donnees. Si tu ne peux "
        "pas la calculer, formule sans repere temporel du tout."
    )


def probleme_de_variantes(draft: PostDraft) -> str | None:
    """Les defauts de FORME des textes RENDUS (accroche, titre, sous-titre).

    Separe de `probleme_de_legende` parce que la cible l'est : la legende n'est
    pas rendue et n'a pas de surlignage, ces trois textes-la en vivent -- et
    c'est aussi ELLE qui sert de reference d'accentuation a ces trois-la."""
    for variante in draft.variantes:
        for texte in (variante.headline, variante.title, variante.subtitle):
            probleme = (
                probleme_repere_relatif(texte)
                or probleme_enumeration_surlignee(texte)
                or probleme_accents_de_cover(texte, draft.caption)
            )
            if probleme is not None:
                return probleme
    return None


def probleme_de_legende(caption: str) -> str | None:
    """Les defauts de FORME qui font relancer sans jamais faire perdre le post."""
    return (
        probleme_repere_relatif(caption, prose=True)
        or probleme_emojis(caption)
        or probleme_accents(caption)
        or probleme_cloture_question(caption)
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
Dans une ENUMERATION -- plusieurs elements de meme nature separes par des \
virgules et « et », typiquement des noms d'artistes --, surligne-les TOUS ou \
AUCUN : « **Angele**, **Caroline Polachek** et SebastiAn » se lit comme un \
oubli sur le troisieme. Si la liste est trop longue pour etre surlignee en \
entier, laisse-la entierement sans surlignage et surligne autre chose (le lieu, \
la date, le chiffre).
Exemple de headline correcte :
Nico Moreno, Mosimann, Cassius... et **c'est ce week-end** aux **Vieilles Charrues**
Exemple de couple title/subtitle correct :
title : **Vieilles Charrues**
subtitle : le line-up electro tombe **ce week-end**

4. LEGENDE (`caption`) : COMMUNE aux quatre variantes -- les variantes sont des \
formulations de cover, pas des angles editoriaux distincts, une seule legende \
suffit. Le texte sous le post, en francais, en 2 a 4 paragraphes courts (1 a 2 \
phrases chacun), SEPARES PAR UNE ligne vide -- jamais un bloc compact, illisible \
sur mobile. Elle developpe ce que la cover annonce, SANS JAMAIS NOMMER LE MEDIA \
d'ou vient l'info -- ni "via X", ni "selon X", ni "d'apres X", ni "info a \
retrouver sur X" : Kinda est une source, pas un intermediaire qui renvoie vers \
un concurrent. Les noms d'ARTISTES, de labels, de festivals, de salles, de \
villes restent evidemment permis : ce sont le sujet de l'actu, pas sa source. \
Pas de hashtags dans la legende, et AUCUN balisage ** : le surlignage n'existe \
que sur la cover, qui est une image ; dans la legende les asterisques \
s'afficheraient telles quelles.
La CLOTURE depend de la nature de l'actu, et NE PREND PAS TOUJOURS LA MEME \
FORME -- alterne. La derniere phrase ne se termine JAMAIS par une question : \
une question peut vivre au milieu de la legende, jamais a la fin.
- actu ordinaire (annonce, sortie, line-up, date, record) : termine par un \
fait marquant garde pour la fin, une date ou un lieu a retenir, une \
projection sur la suite, ou une invitation a reagir formulee sans point \
d'interrogation. Varie reellement d'une composition a l'autre.
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

7. HASHTAGS : 5 a 12, pertinents et specifiques (nom d'artiste, nom de festival, \
ville, genre). Chacun COMMENCE PAR UN `#`, en minuscules, en un seul mot colle, \
sans espace ni ponctuation : `#vieillescharrues`, jamais `vieilles charrues` ni \
`#vieilles-charrues`. Evite les hashtags fourre-tout du type #music ou #love.

8. FRANCAIS ACCENTUE : tu ecris en francais, donc AVEC LES ACCENTS -- dans la \
legende (`caption`) comme dans `headline`, `title` et `subtitle`. « pense pour la \
bande-son », « intitule », « depasse » sans accent font traduction automatique : \
ecris « pensé », « intitulé », « dépassé », « à », « ça », « où ». Jamais d'ASCII \
approximatif.

9. JAMAIS DE REPERE TEMPOREL RELATIF -- des DATES. Tu ecris MAINTENANT, mais ce \
post ne part pas maintenant : il est compose a l'avance, et publie des heures, \
parfois un ou deux jours plus tard. « sort ce soir », « demain a 18h », « ce \
week-end », « cette semaine », « a minuit », « aujourd'hui » seront alors FAUX -- \
un compte d'actu qui annonce comme a venir ce qui est deja sorti perd sa \
credibilite. Ecris la date : "**This Dream Of You** sort le **3 octobre**", \
jamais "sort ce soir a minuit" ; "vendredi **3 octobre** a minuit", jamais \
"demain a minuit". Calcule-la depuis « Date du jour » et « Publie le », qui te \
sont donnees -- et attention, l'ARTICLE est souvent plus vieux que la date du \
jour : son « demain » n'est pas le tien, ne le recopie pas. Si la date reste \
incalculable, formule sans aucun repere temporel. Ce qui reste permis : une date \
absolue, un mois, une saison, une annee, et « des maintenant » pour ce qui est \
deja sorti (vrai pour toujours).

10. LE VISUEL COMMANDE LE NOM : quand la ligne « Compte du visuel publie » est \
donnee, elle dit de QUI est la video ou la photo que le post affichera. Dans \
`title` / `subtitle` -- les textes du REEL, qui sont poses sur cette video --, \
l'artiste nomme doit etre CELUI-LA. Si l'actu cite plusieurs artistes (un \
line-up, un plateau, un festival), nomme celui du visuel, ou n'en nomme AUCUN et \
prends l'evenement comme sujet. Ne prends jamais le premier nom de la liste par \
defaut : un titre qui annonce un artiste pendant qu'un autre est a l'ecran est \
un contresens visible.

N'invente aucun fait qui ne figure pas dans l'article : ni date, ni lieu, ni nom.\
"""


def build_user_prompt(
    article: Article,
    compte_visuel: str | None = None,
    *,
    aujourd_hui: dt.date | None = None,
) -> str:
    """Le prompt utilisateur d'une composition.

    « Date du jour » n'est pas decorative : sans elle, la regle 9 est
    INSATISFIABLE. On demande au modele une date absolue a la place de « demain »,
    or « Publie le » seule ne lui dit pas quel jour on est -- il ne peut donc ni
    convertir le « demain » de l'article, ni voir que cet article date de la
    veille. C'est exactement ce qui s'est passe sur le post 156 (publie le
    2026-10-02 a 16h54, « sort demain » pour une actu de la veille deja sortie le
    jour meme) : le modele a recopie le repere de l'article faute de pouvoir le
    dater. Le fuseau est celui de la publication -- c'est le jour du lecteur.
    """
    if aujourd_hui is None:
        aujourd_hui = dt.datetime.now(ZoneInfo(settings.publication_fuseau)).date()
    # Le nom du jour vient de `reperes.JOURS`, jamais de `%A` : celui-la suit la
    # locale du processus et rendrait « Sunday » sur une machine en anglais.
    jour = JOURS_SEMAINE[aujourd_hui.weekday()]
    parts = [f"Date du jour : {jour} {aujourd_hui:%d/%m/%Y}", f"Titre : {article.title}"]
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
    if compte_visuel:
        # Seulement quand elle est connue : une composition a la main, ou un
        # appelant anterieur a cette regle, n'a pas cette information.
        parts.append(f"Compte du visuel publie : {compte_visuel}")
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

    def composer(article: Article, *, compte_visuel: str | None = None) -> PostDraft:
        derniere_erreur: Exception | None = None
        # Brouillon valide mais aux emojis hors bornes : rendu si aucune
        # tentative ne fait mieux, plutot que de perdre la composition.
        repli: PostDraft | None = None
        consigne: str | None = None
        for tentative in range(1, max_attempts + 1):
            contenu = build_user_prompt(article, compte_visuel)
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
            # Le filet des sources AVANT le controle de forme. Applique apres
            # (dans `create_post`), il pouvait retirer la derniere phrase
            # (« Info via Tsugi. ») et remettre en fin de legende la question
            # que le controle venait d'ecarter : la garantie n'en etait pas une.
            nettoyee = sans_mentions_de_source(draft.caption, article)
            if nettoyee != draft.caption:
                draft = draft.model_copy(update={"caption": nettoyee})
            probleme = probleme_de_legende(draft.caption) or probleme_de_variantes(draft)
            if probleme is None:
                return draft
            repli = draft
            consigne = probleme
            logger.warning(
                "legende imparfaite pour l'article %s (tentative %s/%s) : %s",
                article.id,
                tentative,
                max_attempts,
                probleme,
            )
        if repli is not None:
            logger.warning(
                "composition acceptee malgre son defaut de forme pour l'article %s : %s",
                article.id,
                probleme_de_legende(repli.caption) or probleme_de_variantes(repli),
            )
            return repli
        raise CompositionError(
            f"composition impossible pour l'article {article.id} "
            f"apres {max_attempts} tentatives"
        ) from derniere_erreur

    return composer


def _accepte_le_compte(composer) -> bool:
    try:
        parametres = inspect.signature(composer).parameters
    except (TypeError, ValueError):
        # Un callable dont la signature n'est pas lisible (builtin, objet C) :
        # on s'en tient a l'appel historique.
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parametres.values()):
        return True
    parametre = parametres.get("compte_visuel")
    return parametre is not None and parametre.kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def appeler_compositeur(composer, article: Article, compte_visuel: str | None = None) -> PostDraft:
    """Appelle le crochet de composition, avec le compte du visuel s'il sait le recevoir.

    Le compositeur est INJECTE (`scripts/run_publication.py`, `app/api/posts.py`,
    des doubles dans les tests) : passer un argument de plus a un crochet ecrit
    avant cette regle le ferait planter, et une exception ici passe pour une
    panne systemique qui pose une erreur sur le creneau. L'introspection coute une
    lecture de signature par composition -- quelques fois par jour --, et laisse
    un crochet ancien fonctionner a l'identique.
    """
    if compte_visuel and _accepte_le_compte(composer):
        return composer(article, compte_visuel=compte_visuel)
    return composer(article)


def _fond_par_defaut(article: Article, selecteur=None) -> str | None:
    """La photo de l'article, sinon la vignette d'une video.

    Une actu video n'a pas de photo, et sa vignette fait un fond de cover
    parfaitement valable -- c'est meme le cas normal pour ce type d'actu. Jamais
    le visuel d'un compte media Instagram.
    """
    # DELEGUE a `image_de_fond`, meme regle et meme nettoyage d'URL que pour les
    # actifs choisis : deux definitions du fond finiraient par diverger.
    return image_de_fond(visuels_autorises(article), selecteur)


def cible_gabarit(template: str) -> tuple[int, int] | None:
    """La taille (largeur, hauteur) du gabarit publie, ou None si le nom est
    inconnu -- jamais d'exception pour un plancher de resolution qui est un
    confort, pas une garantie.

    Publique (Tache 6, tour de correction 1/5) : `app.publish.preparation` en
    a besoin pour la MEME cible au gel, quand il verifie si le fond agrandi
    repasse le plancher -- une seconde implementation aurait fini par
    diverger (meme motif que `illustration.dimensions_de`)."""
    try:
        gabarit_ = gabarit_de(template)
    except KeyError:
        logger.warning("gabarit inconnu (%s) : plancher de resolution du fond desactive", template)
        return None
    return gabarit_.width, gabarit_.height


def actifs_de_secours(
    choisi, actifs: list[dict], *, maintenant: dt.datetime | None = None
) -> list[dict]:
    """Les photos de la reserve retenue, en ACTIFS -- donc en slides du carrousel.

    Le defaut du post Noisia (article 17946, 2026-09-18) : l'unique image de
    l'actu etait une affiche, donc ecartee du fond ; les photos des artistes
    cites ne servaient qu'au fond ; et le post est parti avec la cover seule.
    « Il n'y a pas de slide qui montre les artistes en question. »

    Ces photos sont deja copiees en local et deja jugees (pas d'affiche parmi
    elles) : les publier ne coute plus ni requete ni jeton. Elles gardent leur
    origine (`compte` ou `artiste`), que le dashboard affiche, et sont bornees
    par `visuels_max_photos` comme les visuels d'artistes d'un article sans
    image propre.

    `maintenant` est INJECTABLE et non lu de l'horloge systeme : il sert a lire
    le cache des attributions du web (`attribution_de`, memorise
    `visuels_web_cache_jours`), et un appelant qui travaille sur une date fixee
    -- un test -- verrait sinon son entree de cache expirer le jour ou il
    relance la suite. Defaut = maintenant, le cas de la production.
    """
    connues = {image_de(a) for a in actifs}
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    nouveaux: list[dict] = []
    for url in choisi.photos[: settings.visuels_max_photos]:
        if url in connues:
            continue
        connues.add(url)
        if choisi.origine == ORIGINE_WEB:
            # Une image du web se cite : le domaine du site part dans le credit
            # de la legende (« 📸 ra.co »), jamais coupe.
            nouveaux.append(actif_du_web(url, maintenant=maintenant))
            continue
        nouveaux.append(actif({"type": PHOTO, "url": None}, origine=choisi.origine, fichier=url))
    return nouveaux


# Message pose sur le creneau (et notifie au dashboard) quand toute l'echelle a
# ete parcourue -- actu, compte source, artiste suivi cite, recherche web -- sans
# trouver une image ni affiche ni trop petite. Lisible : ce n'est ni une panne
# (VisuelsDifferes) ni un defaut de l'article (AucunVisuel), juste pas de bonne
# photo AUJOURD'HUI.
RAISON_VISUEL_INTROUVABLE = (
    "aucune image de bonne qualite pour cette actu (ni sur l'actu, ni sur le "
    "compte source, ni chez un artiste suivi cite, ni sur le web)"
)


def resoudre_fond(
    visuels,
    template: str,
    *,
    selecteur_fond=choisir_illustration,
    verdicts_fond=None,
) -> tuple[list[dict], Fond, dict[str, Verdict]]:
    """Le fond FINAL d'un post -- actifs, `Fond` retenu, verdicts memorises --
    calcule UNE SEULE FOIS, avant l'appel PAYE de composition (Sonnet).

    Tache 5 (2026-09-21) : decider du saut se fait AVANT de payer la
    composition, la ou les visuels sont deja choisis (`choix_visuels` tourne
    avant l'appel Claude). `app/schedule/runner.py` appelle cette fonction pour
    savoir s'il faut sauter le creneau ; `create_post` l'appelle ensuite pour
    persister -- avec le MEME resultat, jamais un second calcul : les reserves
    de secours (`visuels.secours`, cf. `choix_visuels.py`) sont des lots
    PARESSEUX qui lisent le reseau (compte Instagram, recherche web) des qu'on
    les ouvre, et les ouvrir deux fois doublerait ce cout.

    Leve `VisuelIntrouvable` quand `eviter_l_affiche` a parcouru toute
    l'echelle -- actu, compte, artiste, web -- sans rien trouver d'assez net
    NI d'assez grand pour etre AGRANDI : `FOND_KINDA` n'est plus une issue
    publiable (Tache 5), seulement le signal interne d'echec que cette
    fonction traduit en saut.

    Ne leve PLUS (Tache 6, 2026-09-23) quand `choisi.a_agrandir` est vrai :
    la composition n'a pas besoin d'un fond net, seulement d'un fond a
    ecrire dans le brouillon -- c'est le GEL (T-2h,
    `app/publish/preparation.py`) qui tente la super-resolution et repasse
    le plancher, jamais la composition. `create_post` persiste ce marqueur
    dans `variants["fond"]["a_agrandir"]`.

    Leve `VisuelsDifferes` quand `choisi.differe` dit qu'au moins une reserve
    n'a PAS PU etre consultee (panne de transport :
    quota Graph, reseau, budget de lecture). C'est un REPORT, pas un saut : la
    distinction existe pour ne pas confondre une reserve qui a regarde et n'a
    rien trouve avec une reserve qui n'a pas pu regarder -- avant cette
    distinction, une simple coupure reseau faisait sauter un article avec une
    raison fausse (« aucune image... ni sur le web » alors que le web n'avait
    jamais repondu).
    """
    actifs = list(visuels.actifs)
    connus = prejuger(actifs, verdicts=verdicts_fond)
    retenu = image_de_fond(actifs, selecteur_fond)
    choisi = eviter_l_affiche(
        retenu,
        # `getattr` : des appels plus anciens passent un objet partiel.
        *(getattr(visuels, "secours", None) or []),
        origine=origine_du_fond(actifs, retenu) or ORIGINE_ARTICLE,
        verdicts=verdicts_fond,
        cible=cible_gabarit(template),
        candidats=candidats_de_fond(actifs),
        origine_de=lambda url: origine_du_fond(actifs, url),
    )
    if choisi.differe:
        raise VisuelsDifferes(
            "au moins une reserve (compte, artiste ou web) n'a pas pu etre "
            "consultee : nouvelle tentative au prochain passage"
        )
    if choisi.origine == ORIGINE_KINDA:
        raise VisuelIntrouvable(RAISON_VISUEL_INTROUVABLE)
    return actifs, choisi, connus


def create_post(
    db: Session,
    article: Article,
    draft: PostDraft,
    template: str = "kinda_post",
    visuels=None,
    selecteur_fond=choisir_illustration,
    verdicts_fond=None,
    fond_precalcule: tuple[list[dict], Fond, dict[str, Verdict]] | None = None,
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

    Quand le fond retenu est ENCORE une affiche, ou trop petit pour le gabarit --
    le cas ou tous les candidats en sont, comme le carrousel de line-up des
    Transmusicales --, on ouvre les reserves attachees aux visuels
    (`visuels.secours` : le compte de l'actu, les artistes cites, puis le web),
    via `resoudre_fond`. Depuis la Tache 5 (2026-09-21), quand meme le web n'a
    rien donne, `resoudre_fond` leve `VisuelIntrouvable` -- l'aplat Kinda n'est
    plus une issue publiable, ce POST N'EST PAS CREE. `verdicts_fond` n'existe
    que pour injecter un juge dans les tests. `fond_precalcule` permet au
    planificateur (`app/schedule/runner.py`) de passer le resultat d'un appel
    DEJA fait a `resoudre_fond` (avant l'appel paye de composition), pour ne
    jamais ouvrir une reserve reseau deux fois pour le meme creneau.

    Et le carrousel ne porte qu'UNE carte d'annonce (`plafonner_affiches`) : sept
    slides de la charte d'un festival font de notre post le sien. Le choix est
    PERSISTE ici, dans `assets`, pour que le dashboard le montre et que le gel ne
    le rejoue pas.

    L'origine du fond retenu est gardee dans `variants["fond"]` : une fois l'URL
    ecrite dans la slide, plus rien ne dirait si elle vient de l'actu, du compte,
    d'un artiste ou de l'aplat Kinda -- et c'est ce que le dashboard montre.

    Les champs sont DENORMALISES expres : l'article sera purge a `retention_days`
    (2 jours depuis le 2026-09-21), le post doit rester lisible sans lui.
    """
    premiere = draft.variantes[0]
    if visuels is not None:
        credit = visuels.credit
        if fond_precalcule is not None:
            # Deja calcule par le planificateur, AVANT l'appel paye de
            # composition (cf. `resoudre_fond`) : une nouvelle liste, jamais la
            # meme reference, puisqu'on va lui ajouter les slides de secours.
            actifs, choisi, connus = fond_precalcule
            actifs = list(actifs)
        else:
            # Appel direct (dashboard, tests, brouillon a la main) : calcule ici,
            # une seule fois. Peut lever `VisuelIntrouvable` (Tache 5) : plus
            # aucun chemin ne persiste un post sur l'aplat Kinda.
            actifs, choisi, connus = resoudre_fond(
                visuels, template, selecteur_fond=selecteur_fond, verdicts_fond=verdicts_fond
            )
        fond, origine_fond = choisi.url, choisi.origine
        # Tache 6 : ce fond est encore SOUS le plancher de nettete, retenu
        # quand meme (`eviter_l_affiche` l'a marque plutot que de sauter) --
        # c'est au GEL, pas ici, de tenter l'agrandissement et de repasser le
        # plancher. Le marqueur voyage dans `variants["fond"]`, comme
        # l'origine : le post est purge de son article avant le gel (2 j de
        # retention), il doit rester decidable sans lui.
        a_agrandir_fond = bool(getattr(choisi, "a_agrandir", False))
        # Le fond retenu vient d'une reserve : ses photos deviennent aussi des
        # SLIDES. Sans cela, un article dont l'unique image est une affiche part
        # avec la cover seule -- le post Noisia (cf. `actifs_de_secours`).
        actifs += actifs_de_secours(choisi, actifs)
        # Au plus UNE carte d'annonce dans le carrousel (cf. `AFFICHES_MAX`).
        # ICI et pas au gel : le brouillon est ce que le dashboard montre et
        # edite, et le gel ne doit pas defaire ce qu'on y a valide -- rejouee a
        # chaque passage, la regle retirerait une slide remise a la main. C'est
        # aussi le seul endroit ou les verdicts du juge viennent d'etre payes.
        # Le fond, lui, n'est jamais une affiche AVEREE (`eviter_l_affiche` l'a
        # deja remplace) : le plafond ne peut donc pas retirer l'actif qui le
        # porte. Lecture SANS appel : `prejuger` a deja tout juge, et les photos
        # des reserves l'ont ete par le lot -- personne ne repaie ici.
        actifs = plafonner_affiches(
            actifs, verdicts=verdicts_fond or lecture_memorisee(connus)
        )
        # Le credit est RELU sur les actifs finaux : une image venue du web
        # arrive apres coup (reserve ouverte par `eviter_l_affiche`) et doit
        # etre citee. `or credit` garde le credit d'origine quand aucun actif ne
        # porte de compte (composition a la main, article sans compte source).
        credit = credit_des_actifs(actifs) or credit
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
        origine_fond = ORIGINE_ARTICLE
        a_agrandir_fond = False
    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=article.source.name if article.source is not None else None,
        credit=credit,
        assets=actifs,
        template=template,
        # Filet deterministe derriere la regle 4 du prompt : le brouillon rendu
        # par Claude peut encore nommer sa source (« Info via Stereogum. »,
        # posts 71/63/65 reellement publies) -- retire ici, a la composition,
        # pour que la legende persistee soit deja celle qui part en ligne.
        caption=sans_mentions_de_source(draft.caption, article),
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
            # La teinte est FIGEE ici : l'article sera purge a 2 jours, le post
            # doit rester rendable sans lui (meme motif que les champs
            # denormalises ci-dessus). Editable ensuite sans re-scorer.
            "palette": teinte_de_article(article),
            # D'ou vient le fond de la cover (article | compte | artiste |
            # kinda). Ici et non sur la slide : `slides[0]` est reecrite a chaque
            # choix de variante et editable au dashboard, alors que l'origine est
            # un fait de la composition -- fige au meme titre que la palette.
            "fond": {"origine": origine_fond, "a_agrandir": a_agrandir_fond},
        },
    )
    db.add(post)
    db.commit()
    logger.info("post %s cree pour l'article %s", post.id, article.id)
    return post
