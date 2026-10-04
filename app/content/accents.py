"""Reperer un mot francais ecrit SANS SON ACCENT, dans un texte court.

Pourquoi un module a part. `compose.probleme_accents` ne regarde que la
LEGENDE, et son critere -- « ce texte est long et ne porte pas un seul accent »
-- ne se transpose pas aux textes de cover : ils sont courts, et une accroche
francaise parfaitement ecrite n'a souvent aucun accent (« Le line-up tombe ce
week-end »). Mesure sur les 1 980 textes de variantes en base (`posts.variants`,
champs `headline` / `title` / `subtitle`, 165 posts) : **58,6 % n'ont aucun
accent**, et le taux ne descend qu'a 36,9 % en ne gardant que les textes de
70 caracteres ou plus. Aucun seuil de longueur ne separe la faute du cas
legitime -- le critere de la legende transpose tel quel serait soit inerte,
soit un signalement sur trois textes, exactement ce que le precedent de la
cloture en question interdit (jusqu'a trois appels `composer_model` par
composition).

Le defaut se repere donc MOT PAR MOT, et par deux references independantes :

1. **La legende du MEME brouillon.** Post 168 (publie le 2026-10-03) : la bande
   de titre disait « Troye Sivan invite surprise de Charli XCX a l'ACL », et sa
   legende etait parfaitement accentuee. Le modele sait ecrire le francais, il
   ne l'applique pas aux textes rendus : la legende est donc la reference, sans
   aucune maintenance -- et deux fois sur trois c'est elle qui attrape le post
   168 (« scene » pour « scène », « joues » pour « joués »).
2. **Un lexique mesure** (`MOTS_DESACCENTUES`, 162 entrees), pour les mots que
   la legende ne contient pas.

Mesure des deux ensemble sur les 1 980 textes : **68 signales (3,4 %), 24 posts
sur 165 (14,5 % des compositions), zero faux positif** -- les 68 sont tous de
vraies fautes (« soiree », « decembre », « edition », « apres », « meme »,
« decennie »...). Une composition sur sept paie donc un appel `composer_model`
de plus, et le paie pour un defaut reel ; a comparer aux six sur six de la
cloture en question, qui avait motive la borne de cout.

Deux regles ont ete necessaires pour tenir le zero faux positif, et toutes deux
sont mesurees :

- **L'accent perdu ne doit pas etre sur la DERNIERE lettre.** C'est la classe
  ambigue : `joue`/`joué`, `signe`/`signé`, `invite`/`invité`, `lance`/`lancé`
  sont tous deux du francais valide, et rien dans le texte ne tranche. Sans
  cette regle, 15 des 26 rapprochements par la legende etaient des participes
  corrects (« Guetta a vendu son O2 Arena », « Calvin Harris joue devant
  1,6 million »).
- **Un mot CAPITALISE en milieu de texte est ignore.** Le lexique contient des
  mots dont la graphie sans accent est de l'anglais valide (`video`, `special`,
  `piece`, `debut`) : un titre de morceau ou un nom propre les ferait signaler a
  tort. Un mot en tete de texte reste examine (« Apres deux tracks remarques… »,
  post 89).

Le lexique est DERIVE, pas ecrit a la main : il vient des legendes en base --
du francais du bon registre, ecrit par le meme modele. Procedure, a rejouer
pour l'elargir : ne garder que les legendes qui PORTENT des accents (148 sur
168 : les 20 autres SONT le defaut, et leur « apres » servait de contre-preuve
a « après » -- sans ce tri le lexique tombait a 120 entrees et manquait le post
89), prendre tous leurs mots, grouper par forme sans accent, et garder une
entree quand (a) la forme sans accent n'apparait JAMAIS
telle quelle dans le corpus, (b) au moins deux occurrences de la forme
accentuee, (c) l'accent n'est pas seulement sur la derniere lettre, (d) le mot
apparait au moins une fois en minuscule (sinon c'est un nom propre : « Angele »,
« Tiesto », « Rufus »), (e) l'accent n'est pas un circonflexe sur i ou u, que la
reforme de 1990 autorise a omettre (« gout », « maitrise », « apparaitre »).

Ce module ne decide de rien : il DECRIT un defaut. C'est `compose` qui en fait
une relance non bloquante, jamais un rejet de brouillon.
"""

import re
import unicodedata

# Sous cette longueur, les mots sont les mots grammaticaux ambigus (`a`/`à`,
# `ne`/`né`, `ou`/`où`, `la`/`là`), que rien ne permet de trancher sans analyse
# syntaxique. Le texte de post 168 en porte un (« a l'ACL ») ; il est attrape
# par les autres mots du meme brouillon, pas par lui-meme.
LONGUEUR_MOT_MIN = 4

# Mot = suite de lettres, apostrophes et traits d'union exclus (« avant-gout »
# donne « avant » et « gout », ce qui est ce qu'on veut chercher au lexique).
_MOT = re.compile(r"[^\W\d_]+", re.UNICODE)

# Derive des 148 legendes ACCENTUEES en base (voir la procedure dans l'en-tete).
MOTS_DESACCENTUES: dict[str, str] = {
    "acces": "accès", "americain": "américain", "annee": "année", "annees": "années",
    "annoncee": "annoncée", "annoncees": "annoncées", "apercu": "aperçu", "apprete": "apprête",
    "apres": "après", "arrete": "arrête", "bientot": "bientôt", "carriere": "carrière",
    "chargee": "chargée", "cloture": "clôture", "cloturer": "clôturer",
    "communiquee": "communiquée", "complete": "complète", "completent": "complètent",
    "concrete": "concrète", "concretement": "concrètement", "confirmee": "confirmée",
    "cote": "côté", "creation": "création", "cree": "créé", "debat": "débat", "debut": "début",
    "debuts": "débuts", "decarbonation": "décarbonation", "decede": "décédé",
    "decembre": "décembre", "decennie": "décennie", "decennies": "décennies",
    "decision": "décision", "decouvrir": "découvrir", "defendre": "défendre",
    "depart": "départ", "depasse": "dépasse", "deplacement": "déplacement",
    "derniere": "dernière", "dernieres": "dernières", "derriere": "derrière",
    "desormais": "désormais", "detail": "détail", "deuxieme": "deuxième", "devoile": "dévoile",
    "devoiler": "dévoiler", "echelle": "échelle", "ecouter": "écouter", "ecoutes": "écoutes",
    "edition": "édition", "electronique": "électronique", "electroniques": "électroniques",
    "emblematique": "emblématique", "energie": "énergie", "engagees": "engagées",
    "enigmatique": "énigmatique", "enorme": "énorme", "entiere": "entière", "entree": "entrée",
    "epoque": "époque", "etape": "étape", "etiez": "étiez", "etre": "être",
    "evenement": "événement", "evenements": "évènements", "eventuelle": "éventuelle",
    "evoque": "évoque", "experience": "expérience", "facon": "façon", "faconne": "façonné",
    "fete": "fête", "fevrier": "février", "fidele": "fidèle", "fidelite": "fidélité",
    "fixee": "fixée", "forcement": "forcément", "francais": "français",
    "francaise": "française", "francaises": "françaises", "frontieres": "frontières",
    "geant": "géant", "geants": "géants", "generation": "génération",
    "generations": "générations", "hate": "hâte", "icone": "icône",
    "immediatement": "immédiatement", "inedit": "inédit", "inquietudes": "inquiétudes",
    "inspires": "inspirés", "lacher": "lâcher", "legendaire": "légendaire",
    "legende": "légende", "leve": "lève", "maneges": "manèges", "maniere": "manière",
    "melange": "mélange", "melant": "mêlant", "melodie": "mélodie", "melodies": "mélodies",
    "meme": "même", "memorable": "mémorable", "memorables": "mémorables", "montee": "montée",
    "mystere": "mystère", "mysterieux": "mystérieux", "neerlandais": "néerlandais",
    "numero": "numéro", "partagee": "partagée", "pensee": "pensée", "pensees": "pensées",
    "piece": "pièce", "plongee": "plongée", "plutot": "plutôt", "portee": "portée",
    "precedents": "précédents", "precise": "précise", "precision": "précision",
    "premiere": "première", "premieres": "premières", "prepare": "prépare",
    "preparent": "préparent", "pres": "près", "prevente": "prévente", "prevu": "prévu",
    "prevue": "prévue", "quittes": "quittés", "rassemblees": "rassemblées",
    "reference": "référence", "rentree": "rentrée", "representent": "représentent",
    "repression": "répression", "reseaux": "réseaux", "reserve": "réserve",
    "residence": "résidence", "resonner": "résonner", "resultat": "résultat", "reuni": "réuni",
    "reunie": "réunie", "reunies": "réunies", "reunis": "réunis", "reunit": "réunit",
    "reveillon": "réveillon", "scenes": "scènes", "scenique": "scénique", "serie": "série",
    "singuliere": "singulière", "soignees": "soignées", "soiree": "soirée",
    "soirees": "soirées", "special": "spécial", "speculations": "spéculations",
    "succes": "succès", "suedois": "suédois", "suedoise": "suédoise", "tete": "tête",
    "tetes": "têtes", "tombee": "tombée", "tournee": "tournée", "tres": "très",
    "troisieme": "troisième", "video": "vidéo",
}


def sans_accents(texte: str) -> str:
    """La meme chaine, ses signes diacritiques retires."""
    return "".join(
        c for c in unicodedata.normalize("NFD", texte) if not unicodedata.combining(c)
    )


def porte_un_accent(texte: str) -> bool:
    """Vrai si `texte` porte au moins un signe diacritique."""
    return any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", texte))


def _accent_seulement_final(forme: str) -> bool:
    """Vrai si les accents de `forme` ne touchent que sa DERNIERE lettre.

    C'est la classe ambigue des participes (`joue` / `joué`) : les deux graphies
    sont du francais valide, aucune mesure deterministe ne les separe.
    """
    nu = sans_accents(forme)
    indice = -1
    for caractere in unicodedata.normalize("NFD", forme):
        if unicodedata.combining(caractere):
            if indice != len(nu) - 1:
                return False
        else:
            indice += 1
    return True


def _reference(legende: str) -> dict[str, str]:
    """Les mots que la legende ecrit AVEC un accent, indexes par forme nue.

    Un mot que la legende ecrit AUSSI sans accent est retire : le modele hesite,
    on ne tranche pas a sa place.
    """
    formes: dict[str, set[str]] = {}
    for mot in _MOT.findall(legende or ""):
        formes.setdefault(sans_accents(mot).lower(), set()).add(mot.lower())
    reference: dict[str, str] = {}
    for nu, vues in formes.items():
        if nu in vues:
            continue
        accentuees = sorted(f for f in vues if not _accent_seulement_final(f))
        if accentuees:
            reference[nu] = accentuees[0]
    return reference


def mot_desaccentue(texte: str, *, legende: str = "") -> tuple[str, str] | None:
    """Le premier mot de `texte` ecrit sans son accent, et sa forme attendue.

    `legende` est la seconde reference, celle qui ne demande aucune maintenance :
    un mot que la legende du MEME brouillon ecrit accentue, et que ce texte-ci
    ecrit en ASCII. Rend None si rien n'est certain -- c'est la regle du doute de
    tout le projet, un signalement de trop coute un appel `composer_model`.
    """
    reference = _reference(legende)
    premier = True
    for mot in _MOT.finditer(texte or ""):
        brut = mot.group()
        en_tete, premier = premier, False
        nu = brut.lower()
        if len(nu) < LONGUEUR_MOT_MIN or nu != sans_accents(nu):
            continue
        # Un mot capitalise en milieu de texte est un nom propre ou un titre
        # (« Video Games », « Special Guest ») : le lexique porte des graphies
        # qui sont aussi de l'anglais valide.
        if brut[:1].isupper() and not en_tete:
            continue
        attendue = reference.get(nu) or MOTS_DESACCENTUES.get(nu)
        if attendue:
            return brut, attendue
    return None
