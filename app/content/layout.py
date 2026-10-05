"""Les regles de mise en page des gabarits Kinda, en Python pur.

Ce module ne connait NI Chromium, NI Jinja, NI le reseau, NI la base : il ne
manipule que de la geometrie et de la typographie. C'est delibere -- les regles
d'ajustement vivaient auparavant en dur dans `style.css` et dans une boucle de
`render.py` qui exigeait un navigateur, donc introuvables en CI.

La repartition des roles avec `render.py` est stricte :
  Chromium MESURE (lignes rendues, largeur de chacune)
  Python DECIDE  (echelle a essayer, acceptation, abandon)
Chromium ne prend aucune decision.
"""

from dataclasses import dataclass
from typing import Literal

# Marge relevee sur la maquette du post : le voile bas fait 817 px et commence a
# y=623 ; le bloc d'accroche (276 px) est ancre a 76 px du bas. La marge entre le
# haut du voile et le haut du bloc vaut donc 817 - 276 - 76 = 465. C'est elle qu'on
# conserve quand le bloc grandit, pour que le texte reste dans la partie dense.
SCRIM_MARGE = 465
# Plancher : jamais un voile plus court que celui de la maquette, meme pour une
# accroche d'une seule ligne -- le degrade fait partie du design, pas seulement de
# la lisibilite.
#
# Ces deux longueurs restent celles de la maquette 1440 alors que la toile fait
# desormais 1350 (cf. `GABARIT_POST`), et c'est voulu : elles se comptent DEPUIS LE
# BAS et depuis le bloc de texte, pas en fraction de la hauteur. Le passage au
# format publie retire 90 px de PHOTO au milieu du cadre, il ne change rien a la
# distance entre le bas de l'image, l'accroche et la partie dense du degrade --
# qui est exactement ce que le designer a regle.
VOILE_MINIMUM = 817


# --- Palettes -------------------------------------------------------------
# La teinte d'accent d'une cover : le voile du post, le <mark> des deux gabarits
# et la bande de titre du reel. Une SEULE couleur les gouverne tous, declinee en
# opacites -- c'est pourquoi la palette porte un RGB et non trois chaines.
#
# Le vert existe parce que l'axe « musique x ecologie » est l'un des quatre axes
# editoriaux du compte (cf. CLAUDE.md) et que l'utilisateur veut qu'il se VOIE.
# Il est cale sur la LUMINANCE du rose de la charte (0.430 contre 0.433, soit
# 2.10:1 contre 2.09:1 face au texte creme #FFF7FA) : le contraste percu du
# design ne bouge donc pas d'une actu a l'autre, seule la couleur change. Un vert
# plus sombre lirait mieux sous le texte creme, mais il romprait la parite avec
# un rose valide par le proprietaire du design -- relever les deux est une
# decision de direction artistique, pas un effet de bord de cette tache.
VOILE_ALPHA = 0.3
MARK_ALPHA = 0.8


@dataclass(frozen=True)
class Palette:
    """La teinte d'accent d'une cover, declinee en opacites."""

    nom: str
    rgb: tuple[int, int, int]

    def rgba(self, alpha: float) -> str:
        r, v, b = self.rgb
        return f"rgba({r}, {v}, {b}, {alpha})"

    @property
    def hexa(self) -> str:
        return "#{:02X}{:02X}{:02X}".format(*self.rgb)


PALETTE_ROSE = Palette(nom="rose", rgb=(253, 139, 195))   # #FD8BC3, la charte
PALETTE_VERTE = Palette(nom="verte", rgb=(53, 199, 122))  # #35C77A, actus ecologie

PALETTES: dict[str, Palette] = {p.nom: p for p in (PALETTE_ROSE, PALETTE_VERTE)}


def palette(nom: str | None) -> Palette:
    """La palette nommee, la rose par defaut.

    Jamais de KeyError : le nom vient d'une colonne JSONB editable au dashboard,
    et un nom inconnu doit degrader vers la charte, pas casser un rendu.
    """
    return PALETTES.get(nom or "", PALETTE_ROSE)


@dataclass(frozen=True)
class Typo:
    """Les regles typographiques d'un role de texte."""

    font_size: float          # px, valeur de la maquette
    line_height: float        # multiplicateur
    letter_spacing: float     # em
    color: str
    # Bande de fond qui epouse le texte (cover reel). None = pas de bande.
    # Une bande NEUTRE (la creme du sous-titre) porte sa couleur ici ; une bande
    # TEINTEE (le titre) la tient de la palette -- cf. `bande_teinte`.
    background: str | None = None
    # La bande de fond prend la teinte de la PALETTE (rose par defaut, verte pour
    # une actu ecologie) au lieu d'une couleur figee.
    bande_teinte: bool = False
    # Le role porte un <mark> surligne a la teinte de la palette. Booleen et non
    # couleur : la teinte depend de l'ARTICLE, pas du gabarit.
    mark_teinte: bool = False
    # Degres. Negatif = antihoraire. Releve a -0.6 sur la bande creme du reel.
    tilt: float = 0.0


@dataclass(frozen=True)
class Zone:
    """Ou le texte a le droit de vivre, et jusqu'ou il peut grandir."""

    left: int
    right: int
    anchor: Literal["top", "bottom", "center"]
    anchor_offset: int
    align: Literal["left", "center"]
    # Plafond de HAUTEUR : au-dela, on reduit la police.
    max_lines: int
    # Plafond de LARGEUR : ce qui empeche une bande de reel de toucher les bords.
    max_line_width: int
    # Plancher absolu de reduction. En dessous, on declare le debordement.
    min_scale: float


@dataclass(frozen=True)
class Logo:
    """Ou se pose le logo Kinda sur la toile -- une valeur PAR GABARIT.

    `right` et `width` se trouvent etre identiques sur les deux gabarits, mais
    `top` ne peut pas l'etre, et c'est la grille Instagram qui l'impose : la
    vignette d'une publication y est un crop 4:5 CENTRE, dont l'origine
    verticale vaut (hauteur - largeur x 5/4) / 2. Sur le post (1080x1350, deja
    en 4:5) elle tombe a 0, sur le reel (1080x1920) a 285 px. Un logo pose a
    87 px du haut passe donc entier dans la vignette du post, mais se ferait
    COUPER dans celle du reel, dont les 285 premiers pixels n'existent pas en
    grille.

    D'ou 372 = 285 + 87 sur le reel : ce n'est pas un reglage a l'oeil, c'est le
    decalage qui redonne au logo la meme place percue une fois la grille rognee.
    Deplacer une hauteur de gabarit oblige a recalculer ce nombre.
    """

    right: int
    top: int
    width: int


@dataclass(frozen=True)
class Gabarit:
    """Un format de sortie complet. `roles` est ordonne et fait foi : les cles
    des slides en base doivent correspondre."""

    name: str
    width: int
    height: int
    roles: dict[str, tuple[Typo, Zone]]
    logo: Logo
    # Distance (px) entre le BAS de la toile et le bas du bloc de texte (title +
    # subtitle empiles) sur le reel. Ancrage par le bas, et non plus une fraction
    # de hauteur (l'ancien `block_center` centrait le bloc a 42,9 % de la toile) :
    # la machine d'ajustement d'echelle peut reduire la police, donc la hauteur
    # du bloc, et un centre fixe ecarte alors le BAS du bloc de sa limite -- un
    # bloc plus court y remonte vers le milieu du cadre au lieu de rester colle
    # a la meme limite basse. Un ancrage par le bas est stable par construction :
    # quelle que soit la hauteur mesuree, seul le bas compte, et il ne bouge pas.
    # None sur le post : ses zones sont deja ancrees en bas role par role.
    block_bottom: int | None = None
    # Le post a trois degrades pour rendre le texte lisible ; la cover reel n'en a
    # pas, son contraste vient des bandes.
    voile: bool = True


@dataclass(frozen=True)
class Mesure:
    """Ce que Chromium a mesure pour un role, a une echelle donnee.

    Une largeur PAR LIGNE RENDUE, pas une largeur de bloc : c'est ce dont on a
    besoin pour les bandes du reel, qui epousent chacune leur ligne.
    """

    line_widths: tuple[float, ...]
    height: float

    @property
    def lines(self) -> int:
        return len(self.line_widths)


def tient(zone: Zone, mesure: Mesure) -> bool:
    """Vrai si la mesure respecte les DEUX plafonds de la zone."""
    return mesure.lines <= zone.max_lines and all(
        largeur <= zone.max_line_width for largeur in mesure.line_widths
    )


@dataclass(frozen=True)
class Resultat:
    """Ce que l'ajustement a retenu. `overflow` est le drapeau qui remonte
    jusqu'au dashboard : un post marque ne doit pas etre publie tel quel."""

    scale: float
    lines: dict[str, int]
    overflow: bool


# Bornee : la dichotomie converge vite, et au-dela le probleme n'est pas la taille
# de police mais l'accroche elle-meme.
DICHOTOMIE_MAX = 4
# En deca de cet ecart d'echelle, la difference n'est plus visible a l'oeil.
DICHOTOMIE_TOLERANCE = 0.02


class Ajustement:
    """Machine a etats de l'ajustement. Recoit des mesures, rend l'echelle
    suivante a essayer, ou None quand c'est fini.

    Trois etages, dans cet ordre :
      1. l'echelle de la maquette (1.0). Si ca tient, on s'arrete la -- c'est le
         regime normal, et `HEADLINE_MAX = 110` dans `compose.py` fait qu'une
         accroche depasse rarement 4 lignes ;
      2. le PLANCHER (`min_scale`). Le tester en deuxieme detecte tout de suite le
         cas desespere au lieu de gaspiller la dichotomie pour y arriver ;
      3. la dichotomie entre le plancher (qui tient) et 1.0 (qui ne tient pas),
         pour trouver la plus GRANDE echelle acceptable.

    Elle suppose la monotonie : reduire la police ne peut pas ajouter de lignes ni
    elargir une ligne. Vrai en pratique, faux en theorie sur des cas pathologiques
    de cesure -- d'ou la borne `DICHOTOMIE_MAX`, qui garantit la terminaison.
    """

    def __init__(self, gabarit: Gabarit) -> None:
        self._gabarit = gabarit
        # max et non min : `min_scale` est un plancher PAR ROLE, et le plancher
        # liant pour la slide est le plus RESTRICTIF des deux. Prendre le minimum
        # autoriserait la dichotomie a descendre sous le plancher declare d'un
        # role sans jamais lever `overflow`.
        self._plancher = max(zone.min_scale for _, zone in gabarit.roles.values())
        self._etape = "maquette"
        self._essai: float | None = None
        self._bas = self._plancher          # borne basse : tient (a confirmer)
        self._haut = 1.0                    # borne haute : ne tient pas
        self._dichotomies = 0
        self._meilleur: tuple[float, dict[str, int]] | None = None
        self._dernier: tuple[float, dict[str, int]] | None = None
        self._fini = False

    def prochaine_echelle(self) -> float | None:
        if self._fini:
            return None
        if self._etape == "maquette":
            self._essai = 1.0
        elif self._etape == "plancher":
            self._essai = self._plancher
        else:
            self._essai = (self._bas + self._haut) / 2
        return self._essai

    def enregistrer(self, mesures: dict[str, Mesure]) -> None:
        """Enregistre le resultat de la mesure a l'echelle rendue par
        `prochaine_echelle()` et avance la machine."""
        if self._essai is None:  # pragma: no cover -- usage incorrect
            raise RuntimeError("enregistrer() avant prochaine_echelle()")

        echelle = self._essai
        lignes = {role: mesure.lines for role, mesure in mesures.items()}
        # TOUS les roles doivent tenir : une seule echelle par slide, pour que le
        # bloc reduise d'un bloc et garde ses proportions internes.
        ok = all(
            tient(self._gabarit.roles[role][1], mesure) for role, mesure in mesures.items()
        )
        self._dernier = (echelle, lignes)
        if ok and (self._meilleur is None or echelle > self._meilleur[0]):
            self._meilleur = (echelle, lignes)

        if self._etape == "maquette":
            if ok:
                self._fini = True
            else:
                self._etape = "plancher"
            return

        if self._etape == "plancher":
            if not ok:
                # Meme au plancher ca deborde : inutile de dichotomiser.
                self._fini = True
            else:
                self._etape = "dichotomie"
                self._bas = echelle
            return

        if ok:
            self._bas = echelle
        else:
            self._haut = echelle
        self._dichotomies += 1
        if self._dichotomies >= DICHOTOMIE_MAX or self._haut - self._bas < DICHOTOMIE_TOLERANCE:
            self._fini = True

    @property
    def resultat(self) -> Resultat:
        if self._meilleur is not None:
            echelle, lignes = self._meilleur
            return Resultat(scale=echelle, lines=lignes, overflow=False)
        # Rien n'a tenu : on rend quand meme, au plancher, mais MARQUE. On ne
        # produit jamais une image coupee en silence.
        echelle, lignes = self._dernier if self._dernier else (self._plancher, {})
        return Resultat(scale=echelle, lines=lignes, overflow=True)


# --- Les gabarits ---------------------------------------------------------
# Valeurs relevees dans Figma (frame 234:8) -- voir
# templates/kinda_post/REFERENCE.md pour la provenance et la procedure de
# re-extraction. Ne JAMAIS les modifier a l'aveugle.
#
# HAUTEUR 1350 ET NON LES 1440 DE LA FRAME (2026-09-18). La frame Figma est en
# 3:4, un rapport que Meta REFUSE dans le fil : la cover etait donc rendue en
# 1440 puis recadree au centre en 1350 juste avant publication. Ce recadrage
# prenait 45 px en haut et 45 px en bas, c'est-a-dire la moitie de la marge du
# logo (87 -> 42) et 60 % de celle de l'accroche (76 -> 31) : toutes les marges
# posees par le designer contre les bords etaient fausses a la publication, et
# elles seules -- ce que l'utilisateur a vu tout de suite en comparant aux
# premiers posts.
#
# On rend donc AU FORMAT PUBLIE, et les marges de la maquette sont conservees
# TELLES QUELLES (87, 76, 77 lateral) : la largeur n'ayant pas bouge, une marge
# laterale de 77 px et une marge haute de 87 px forment le meme anneau qu'avant.
# Ce qui disparait, ce sont 90 px de PHOTO au milieu du cadre -- exactement ce
# que la publication retirait deja, mais sans toucher au dessin.
# Sentinelles : `test_le_post_est_rendu_au_format_publie` et
# `test_les_marges_de_la_maquette_sont_celles_qui_sont_publiees`.
GABARIT_POST = Gabarit(
    name="kinda_post",
    width=1080,
    height=1350,
    roles={
        "headline": (
            Typo(
                font_size=80,
                line_height=1.15,
                letter_spacing=-0.06,
                color="#FFF7FA",
                mark_teinte=True,
            ),
            Zone(
                left=77,
                right=77,
                anchor="bottom",
                anchor_offset=76,
                align="left",
                # Mesure : 92 px par ligne a l'echelle 1.0 (184 px a 2 lignes, 276 a 3,
                # 368 a 4, 460 a 5). Rendu d'accroches croissantes sur une vraie photo
                # (pas le fallback) : a 5 lignes le sujet de la photo reste visible sous
                # le bloc de texte ; rendues sans plafond, 7-8 lignes a l'echelle pleine
                # avalent les visages -- la photo cesse alors d'exister comme image.
                # 5 est donc le dernier palier ou elle survit. `HEADLINE_MAX = 110` dans
                # compose.py plafonne deja les accroches normales a ~4 lignes : ce
                # plafond est un filet de secours, pas le regime courant.
                # Inchange au passage en 1350 : ces paliers ont ete juges sur ce
                # qu'on VOYAIT de la photo dans l'image PUBLIEE, qui etait deja
                # recadree a 1350. Le rendu natif ne retire rien de plus.
                max_lines=5,
                max_line_width=926,   # 1080 - 2 x 77
                min_scale=0.7,
            ),
        ),
    },
    # La toile EST le crop 4:5 : la vignette de grille n'en retire plus rien, et
    # les 87 px de la maquette sont ceux qu'on voit, en grille comme dans le fil.
    logo=Logo(right=77, top=87, width=150),
)

# Valeurs MESUREES AU PIXEL sur `exemples/[PREVIEW] - Cover Reels.png`, faute de
# frame Figma extraite. Voir REFERENCE.md pour le releve complet.
#
# `font_size`, `letter_spacing` (et le padding de style.css) ne se deduisent pas
# par inversion des glyphes de la maquette -- une police de substitution n'a pas
# les memes metriques. Ils se calent en rendant puis en comparant, dans cet
# ordre (la largeur de bande depend des trois a la fois, la hauteur seulement de
# `font_size`/`line_height`/padding vertical, ce qui decouple le probleme) :
#   1. `font_size` cale sur la HAUTEUR de bande mesuree (94 px rose, 89 px
#      creme) -- insensible a `letter_spacing` ;
#   2. `letter_spacing` cale ensuite sur la LARGEUR de bande mesuree (534 px
#      rose, 753 px creme), `font_size` etant deja fixe.
# Ces chiffres sont cales sur le texte d'exemple utilise a la calibration :
# "Gregoire Cliquet" (titre) et "au festival des vieilles Charrues"
# (sous-titre) -- cf. tests/content/test_render.py. Une recalibration sur un autre
# texte (longueur de chaine differente) ne retombera pas forcement dessus,
# la largeur de bande dependant du nombre de caracteres autant que du corps.
# Piege a l'oeil documente parce qu'il a deja failli etre code de travers : la
# bande rose est droite, la creme est inclinee de 0,6 deg antihoraire. Confirme
# comme voulu par le proprietaire du design.

# Le bloc de texte du reel remonte en bas, comme celui du post (2026-09-18).
# Constate sur la grille publiee (instagram.com/kindanewsfrance) : `block_center`
# plaçait le bloc a 42,9 % de la hauteur, soit au MILIEU du cadre -- les deux
# reels y ressortaient comme des corps etrangers au milieu des posts, dont le
# bloc est en bas sur un voile. L'utilisateur veut le meme repere visuel partout.
#
# On ne peut pas coller le bloc au bord comme sur le post : Instagram superpose
# sa legende, le nom du compte et sa colonne de boutons sur le bas du reel.
# REEL_LIMITE_BASSE_UI = 1500 est la limite basse retenue pour rester au-dessus
# de cette interface (~22 % de la hauteur laisses libres en bas, 1920 - 1500 =
# 420) -- un reglage de securite fourni par l'utilisateur, pas une mesure au
# pixel comme le reste du gabarit (aucune capture de l'interface Instagram
# n'est disponible ici pour la mesurer).
#
# Cette limite doit aussi rester DANS le crop 4:5 de la grille : il commence a
# y=285 (cf. `Logo` ci-dessus) et se termine donc a 285 + 1080 x 5/4 = 1635.
# 1500 < 1635 avec 135 px de marge : la contrainte d'interface est la plus
# stricte des deux, et c'est donc elle qui dimensionne le bloc. Verifie par
# `tests/content/test_layout.py::test_la_limite_basse_du_bloc_reste_dans_le_crop_de_la_grille`.
REEL_LIMITE_BASSE_UI = 1500

GABARIT_REEL = Gabarit(
    name="kinda_reel",
    width=1080,
    height=1920,
    roles={
        "title": (
            Typo(
                font_size=84.3,
                line_height=1.1,
                letter_spacing=-0.02,
                color="#FFF7FA",
                bande_teinte=True,
                tilt=0.0,
            ),
            Zone(
                left=77, right=77, anchor="center", anchor_offset=0, align="center",
                max_lines=2, max_line_width=926, min_scale=0.7,
            ),
        ),
        "subtitle": (
            Typo(
                font_size=75.3,
                line_height=1.1,
                letter_spacing=-0.08,
                color="#1D1D1D",
                background="#FFF7FA",
                tilt=-0.6,
            ),
            Zone(
                left=77, right=77, anchor="center", anchor_offset=0, align="center",
                max_lines=2, max_line_width=926, min_scale=0.7,
            ),
        ),
    },
    # 285 + 87 : le crop 4:5 de la grille commence a y=285 sur une toile 1920,
    # donc un logo a 87 px du haut y serait coupe. Voir `Logo`.
    logo=Logo(right=77, top=372, width=150),
    # Ancre par le bas plutot que par une fraction de hauteur -- voir le
    # commentaire au-dessus de `REEL_LIMITE_BASSE_UI`. Dans le cas nominal (les
    # deux bandes a leur taille de police pleine) le bas du bloc tombe donc
    # exactement sur cette limite ; si la police est reduite par la machine
    # d'ajustement d'echelle, le bloc raccourcit vers LE HAUT et son bas ne
    # bouge pas -- il ne peut donc jamais remonter vers le milieu du cadre.
    block_bottom=1920 - REEL_LIMITE_BASSE_UI,  # 1920 = height, ci-dessus
    voile=False,
)

GABARITS: dict[str, Gabarit] = {g.name: g for g in (GABARIT_POST, GABARIT_REEL)}


def gabarit(nom: str) -> Gabarit:
    """Leve KeyError avec un message lisible plutot qu'un KeyError nu."""
    try:
        return GABARITS[nom]
    except KeyError:
        raise KeyError(f"gabarit inconnu : {nom!r} (connus : {sorted(GABARITS)})") from None


def hauteur_voile(gabarit_: Gabarit, role: str, mesure: Mesure) -> float:
    """Hauteur du voile bas, fonction de la hauteur mesuree du bloc.

    Sans cela, le choix « bloc extensible » sortirait le texte du degrade dense
    des qu'une accroche depasse trois lignes : illisible sur une photo claire,
    et invisible sur la maquette dont le bas est deja sombre.
    """
    _, zone = gabarit_.roles[role]
    return max(VOILE_MINIMUM, mesure.height + zone.anchor_offset + SCRIM_MARGE)


def variables_css(
    gabarit_: Gabarit,
    scale: float,
    scrim_height: float | None = None,
    palette_: Palette = PALETTE_ROSE,
) -> str:
    """Les valeurs du gabarit, en variables CSS. Le CSS n'a plus de nombres.

    Les variables absentes ne sont PAS ecrites a vide : un `background: ;`
    serait silencieusement invalide, et le rendu partirait sans bande sans que
    rien ne le signale.
    """
    lignes = [
        f"--slide-width: {gabarit_.width}px;",
        f"--slide-height: {gabarit_.height}px;",
        f"--scale: {scale};",
    ]

    # Recette decorative des voiles, fixe : elle ne depend pas du texte. Elle passe
    # quand meme par une variable pour que la sentinelle de style.css reste stricte
    # -- une valeur en dur dans la feuille est une regle qui echappe aux tests.
    lignes += [
        "--voile-noir: rgba(0, 0, 0, 0.8);",
        # La TEINTE, elle, depend de l'article : rose par defaut, verte pour une
        # actu ecologie. Meme couleur de base pour le voile, le <mark> et la
        # bande de titre du reel -- une seule decision, trois usages.
        f"--voile-teinte: {palette_.rgba(VOILE_ALPHA)};",
        "--voile-haut-hauteur: 727px;",
        # Geometrie du logo : elle vient du GABARIT, pas d'ici -- `top` depend du
        # rognage de la grille Instagram, donc de la hauteur de la toile (cf.
        # `Logo`). Meme raison de passer par une variable que la recette des
        # voiles : une valeur en dur dans style.css echapperait aux tests.
        f"--logo-right: {gabarit_.logo.right}px;",
        f"--logo-top: {gabarit_.logo.top}px;",
        f"--logo-width: {gabarit_.logo.width}px;",
        # Point de depart transparent du degrade teinte : complete
        # `--voile-teinte`, qui n'en porte que l'arrivee.
        f"--voile-teinte-debut: {palette_.rgba(0)};",
    ]

    if gabarit_.block_bottom is not None:
        lignes.append(f"--block-bottom: {gabarit_.block_bottom}px;")

    for role, (typo, zone) in gabarit_.roles.items():
        lignes += [
            f"--{role}-font-size: {typo.font_size}px;",
            f"--{role}-line-height: {typo.line_height};",
            f"--{role}-letter-spacing: {typo.letter_spacing}em;",
            f"--{role}-color: {typo.color};",
            f"--{role}-left: {zone.left}px;",
            f"--{role}-right: {zone.right}px;",
            f"--{role}-offset: {zone.anchor_offset}px;",
            f"--{role}-align: {zone.align};",
            f"--{role}-tilt: {typo.tilt}deg;",
        ]
        fond = palette_.hexa if typo.bande_teinte else typo.background
        if fond is not None:
            lignes.append(f"--{role}-background: {fond};")
        if typo.mark_teinte:
            lignes.append(f"--{role}-mark-background: {palette_.rgba(MARK_ALPHA)};")
    if scrim_height is not None:
        lignes.append(f"--scrim-height: {round(scrim_height)}px;")
    return ":root {\n  " + "\n  ".join(lignes) + "\n}"
