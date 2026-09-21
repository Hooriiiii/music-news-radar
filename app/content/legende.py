"""Le texte publie sous un post : legende aeree, credit, hashtags.

Le CREDIT nomme le proprietaire des visuels (« 📸 @artiste », « 🎥 @artiste »
pour un reel) : c'est le minimum de bonne foi quand on reprend la photo ou la
video d'un compte. Il n'est jamais coupe.

Commun a l'export zip et a la publication automatique -- un seul endroit ou
respecter les limites de Meta : 2200 caracteres (marge a 2150) et 30 hashtags.

Plus de ligne « Source : <url> » (2026-09-17, demande de l'utilisateur apres le
premier post publie) : le credit du compte reste, lui, obligatoire.

Les HASHTAGS sont normalises ICI et pas a la composition : le texte publie est le
seul goulot que TOUS les chemins traversent -- publication automatique, export zip
et essai a la main --, alors qu'un brouillon peut arriver d'ailleurs (hashtags
edites au dashboard, post compose avant cette regle et publie apres).

Retour de l'utilisateur le 2026-09-21 en ouvrant le compte reellement publie :
« supprime les sources des descriptions [...] parle de l'actu en elle-meme ».
Mesure sur les posts en ligne : « Info via Stereogum. », « Qu'en pensez-vous ?
Info a retrouver via Stereogum. », « ... selon Soompi ... ». Kinda veut etre
une SOURCE, pas un intermediaire (CLAUDE.md, « Veille rapide ») : nommer le
media d'ou vient l'info fait exactement l'inverse, ca renvoie vers un
concurrent. `sans_mentions_de_source` est le filet DETERMINISTE derriere la
regle de prompt posee dans `app/content/compose.py` (SYSTEM_PROMPT) -- un
prompt ne garantit jamais rien a lui seul, lecon deja ecrite trois fois plus
haut dans ce module et dans CLAUDE.md. Meme jour, meme demande : le credit
d'un simple nom de DOMAINE (« 📸 djmag.com », post 59) disparait aussi -- seul
un credit de COMPTE (`@artiste`) reste affiche (`credit_de_compte` ici,
`app/content/visuels.credit_des_actifs` a la creation).
"""

import re

from app.content.emojis import EMOJI_CLASSE
from app.content.recherche_web import domaine_de, nom_de_source

LEGENDE_MAX = 2200
# Marge sous la limite de Meta, en points de code sur TOUT le texte publie :
# Meta ne documente pas son unite de compte (emojis composes, retours a la ligne).
LEGENDE_MARGE = 2150
# Place gardee a la legende quand credit et hashtags debordent a eux seuls.
LEGENDE_RESERVE = 500
HASHTAGS_MAX = 30
PHRASES_PAR_PARAGRAPHE = 2
# Un point qui suit ces mots ne termine pas la phrase (« feat. Travis Scott »).
ABREVIATIONS = frozenset({"feat", "ft", "vol", "dr", "st", "mr", "mme", "no", "vs"})
_INITIALES = re.compile(r"(?:[A-Z]\.)+")
# Une ligne de liste (« 📅 24.09 », « - Zenith ») garde son retour simple.
_LIGNE_DE_LISTE = re.compile(rf"(?:{EMOJI_CLASSE}|[-•*–·])")

# Fin de phrase : ponctuation forte (et un guillemet fermant), suivie des emojis
# qui la ponctuent -- ils restent avec leur phrase --, puis un espace et une
# phrase qui COMMENCE (majuscule, chiffre, guillemet). Des points de
# suspension suivis d'une minuscule (« Cassius... et c'est ») ne coupent pas.
_FIN_DE_PHRASE = re.compile(
    # Possessif : les emojis de fin sont pris en entier, jamais laisses a la phrase suivante.
    rf"[.!?…]+[»\"')]?(?:\s*{EMOJI_CLASSE}+)*+(?=\s+[A-ZÀ-ÖØ-Þ0-9«\"])"
)


def _abreviation(texte: str, fin: re.Match) -> bool:
    """Le point de cette fin de phrase clot-il une abreviation (« J. », « U.S. »,
    « feat. ») plutot qu'une phrase ?"""
    if not fin.group().startswith(".") or fin.group().startswith(".."):
        return False
    mot = re.search(r"(\S*)$", texte[: fin.start() + 1]).group(1).lstrip("(«\"'")
    return bool(_INITIALES.fullmatch(mot)) or mot[:-1].lower() in ABREVIATIONS


def _lignes(texte: str) -> str:
    lignes = [ligne.strip() for ligne in texte.split("\n") if ligne.strip()]
    sortie = lignes[0]
    for ligne in lignes[1:]:
        sortie += ("\n" if _LIGNE_DE_LISTE.match(ligne) else "\n\n") + ligne
    return sortie


def _phrases_de(texte: str) -> list[str]:
    """Les phrases de `texte`, coupees sur `_FIN_DE_PHRASE` (abreviations et
    emojis de fin respectes -- cf. `_abreviation`).

    Extrait d'`aerer_legende`, qui pave ces phrases par paires, pour que
    `sans_mentions_de_source` les reutilise SANS reecrire une seconde machine de
    decoupage : les deux fonctions doivent voir exactement les memes phrases.
    """
    phrases, debut = [], 0
    for fin in _FIN_DE_PHRASE.finditer(texte):
        if _abreviation(texte, fin):
            continue
        phrases.append(texte[debut : fin.end()].strip())
        debut = fin.end()
    phrases.append(texte[debut:].strip())
    return [phrase for phrase in phrases if phrase]


def aerer_legende(caption: str) -> str:
    """Filet de lisibilite : un bloc compact devient des paragraphes courts.

    Le prompt demande deja 2 a 4 paragraphes separes par une ligne vide ; une
    legende qui en porte une est laissee telle quelle. Sinon, des retours a la
    ligne simples deviennent des lignes vides (sauf devant une ligne de liste,
    emoji ou puce), et un bloc de plus de deux phrases
    est coupe en paragraphes de deux phrases.
    """
    texte = caption.strip()
    if "\n\n" in texte:
        return texte
    if "\n" in texte:
        return _lignes(texte)
    phrases = _phrases_de(texte)
    if len(phrases) <= PHRASES_PAR_PARAGRAPHE:
        return texte
    return "\n\n".join(
        " ".join(phrases[i : i + PHRASES_PAR_PARAGRAPHE])
        for i in range(0, len(phrases), PHRASES_PAR_PARAGRAPHE)
    )


# En dessous de cette longueur, la racine d'un domaine (sans son TLD) coincide
# avec un mot ordinaire du francais ou de l'anglais : « vice.com » -> « vice »,
# « ra.co » -> « ra » (les deux sont des domaines REELEMENT presents dans ce
# depot -- CLAUDE.md cite Vice, RA). Bannie seule, une telle racine mutilerait
# une phrase qui n'a rien a voir avec le media ("un vice certain", "un sacre
# ra-le-bol"). En dessous du seuil on ne bannit donc que le domaine COMPLET
# (avec son TLD, ex. « vice.com »), jamais la racine isolee.
RACINE_DOMAINE_MIN = 5


def noms_a_bannir(article) -> list[str]:
    """Les noms qui identifient LA SOURCE de cet article -- jamais devines,
    toujours CONSTRUITS depuis lui : le nom de sa source debarrasse de son
    canal (« Transmusicales (IG) » -> « Transmusicales », le nettoyage existe
    deja dans `recherche_web.nom_de_source`, reutilise plutot que reecrit), et
    le domaine de `article.url` -- avec son TLD (« stereogum.com ») et, si elle
    est assez longue pour ne pas etre un mot ordinaire (`RACINE_DOMAINE_MIN`),
    sans (« stereogum », pour attraper « Stereogum » ecrit en toutes lettres
    dans la legende, exactement le cas du post 71 reellement publie).
    """
    noms: list[str] = []
    source = getattr(article, "source", None)
    nom_source = nom_de_source(getattr(source, "name", None))
    if nom_source:
        noms.append(nom_source)
    domaine = domaine_de(getattr(article, "url", None) or "")
    if domaine:
        noms.append(domaine)
        racine = domaine.split(".")[0]
        if racine and len(racine) >= RACINE_DOMAINE_MIN and racine not in noms:
            noms.append(racine)
    return noms


def sans_mentions_de_source(caption: str, article) -> str:
    """Retire les phrases qui NOMMENT la source de l'article -- « Info via
    Stereogum. », « ... selon Soompi ... », « Info a retrouver via Stereogum. »,
    les trois formulations reellement publiees le 2026-09-21. Kinda veut etre
    une SOURCE, pas un intermediaire (CLAUDE.md, « Veille rapide ») : nommer le
    media d'ou vient l'info revient a renvoyer vers un concurrent.

    Elle ne DEVINE aucun nom a bannir : ils viennent de `noms_a_bannir`, donc
    un ARTISTE homonyme d'un media SANS RAPPORT avec cet article n'est jamais
    touche -- seule la source de CET article est bannie.

    Deux garde-fous : une phrase n'est retiree que si elle nomme la source, le
    reste ne bouge pas ; et si tout serait retire, la legende part TELLE
    QUELLE -- une legende vide est pire qu'une legende qui cite un media, et
    `PostDraft` la validerait mal.
    """
    noms = [nom for nom in noms_a_bannir(article) if nom]
    if not noms:
        return caption
    motifs = [re.compile(rf"\b{re.escape(nom)}\b", re.IGNORECASE) for nom in noms]

    def nomme_la_source(phrase: str) -> bool:
        return any(motif.search(phrase) for motif in motifs)

    paragraphes: list[str] = []
    for paragraphe in caption.split("\n\n"):
        phrases = [phrase for phrase in _phrases_de(paragraphe) if not nomme_la_source(phrase)]
        if phrases:
            paragraphes.append(" ".join(phrases))
    resultat = "\n\n".join(paragraphes).strip()
    return resultat if resultat else caption


def credit_de_compte(credit: str | None) -> str | None:
    """Le credit affiche sous le post, ne gardant que les tokens qui nomment un
    COMPTE (`@handle`) -- jamais tout-ou-rien.

    `credit_des_actifs` (`app/content/visuels.py`) n'emet plus de credit de
    DOMAINE pour un post NOUVEAU (decision de l'utilisateur, 2026-09-21), mais
    un brouillon DEJA EN BASE, compose par l'ancienne version, peut porter un
    credit MIXTE : un fond d'origine `artiste` (« @fredagain ») qui s'avere
    etre une affiche ouvre une reserve qui peut ajouter un actif `web`
    (« ra.co ») au MEME `actifs` (ordre compte -> artiste -> web dans
    `choix_visuels.py`), et l'ancien `credit_des_actifs` les concatenait tous.
    Jeter le credit ENTIER des qu'un seul token n'est pas un `@handle`
    effacerait alors un credit d'artiste pourtant legitime -- le plan est
    clair : « les credits de COMPTE restent strictement inchanges ». On ne
    retire donc que les tokens qui ne sont pas des comptes ; un domaine, lui,
    ne porte jamais de `@` (`credit_video`, `compte_du_visuel`).
    """
    comptes = [mot for mot in (credit or "").split() if mot.startswith("@")]
    return " ".join(comptes) or None


def normaliser_hashtags(tags) -> list[str]:
    """Des mots quelconques en vrais hashtags : un `#` en tete, et rien d'autre.

    Le reel du 2026-09-18 (instagram.com/reel/Ddbuwecjr5A/) s'est termine par
    « iheartradiofestival bts lesserafim ... » en texte brut : sans `#`, aucune
    portee, et ca fait bacle. Le prompt demandait des minuscules sans jamais dire
    que le `#` en faisait partie, et le modele rendait tantot l'un tantot l'autre
    -- un prompt ne garantit rien, d'ou cette normalisation deterministe.

    Tout ce qu'Instagram ne garde pas dans un tag est retire (espaces,
    ponctuation, emojis) : « vieilles charrues » devient un seul `#vieillescharrues`
    plutot qu'un tag coupe en deux a la publication. Un tag qui n'a plus rien
    d'utilisable disparait -- publier un `#` seul serait pire que rien.
    """
    propres: list[str] = []
    vus: set[str] = set()
    for tag in tags or []:
        mot = "".join(c for c in str(tag) if c.isalnum() or c == "_")
        if not mot:
            continue
        # Dedup insensible a la casse et au `#` : le modele rend parfois la meme
        # idee sous deux formes (« #Techno » et « techno ») dans la meme liste.
        cle = mot.casefold()
        if cle in vus:
            continue
        vus.add(cle)
        propres.append(f"#{mot}")
    return propres


def texte_legende(post) -> str:
    # Normalise AVANT de plafonner : sinon un doublon consommerait une des 30 places.
    hashtags = normaliser_hashtags(post.hashtags)[:HASHTAGS_MAX]
    legende = aerer_legende(post.caption)
    credit = credit_de_compte(getattr(post, "credit", None))
    ligne_credit = None
    if credit:
        pictogramme = "🎥" if getattr(post, "template", None) == "kinda_reel" else "📸"
        # « 📸 @artiste », sans « via » : le pictogramme dit deja ce qu'est ce compte.
        ligne_credit = f"{pictogramme} {credit}"

    def fin_du_texte(tags) -> str:
        # Credit puis hashtags, en dernier bloc a part.
        blocs = [bloc for bloc in (ligne_credit, " ".join(tags)) if bloc]
        return "".join(f"\n\n{bloc}" for bloc in blocs)

    fin = fin_du_texte(hashtags)
    if len(legende) + len(fin) <= LEGENDE_MARGE:
        return legende + fin
    # Credit et hashtags trop longs a eux seuls : des hashtags partent, par la
    # fin. Le credit, jamais -- citer le compte est le minimum de bonne foi.
    reserve = min(len(legende), LEGENDE_RESERVE)
    while hashtags and len(fin) + reserve > LEGENDE_MARGE:
        hashtags = hashtags[:-1]
        fin = fin_du_texte(hashtags)
    if len(legende) + len(fin) <= LEGENDE_MARGE:
        return legende + fin
    # Puis on coupe la legende elle-meme, paragraphes compris.
    place = LEGENDE_MARGE - len(fin) - len("…")
    return legende[: max(place, 0)].rstrip() + "…" + fin
