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
VOILE_MINIMUM = 817


@dataclass(frozen=True)
class Typo:
    """Les regles typographiques d'un role de texte."""

    font_size: float          # px, valeur de la maquette
    line_height: float        # multiplicateur
    letter_spacing: float     # em
    color: str
    # Bande de fond qui epouse le texte (cover reel). None = pas de bande.
    background: str | None = None
    # Fond du <mark> (surlignage rose du post). None = pas de surlignage.
    mark_background: str | None = None
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
class Gabarit:
    """Un format de sortie complet. `roles` est ordonne et fait foi : les cles
    des slides en base doivent correspondre."""

    name: str
    width: int
    height: int
    roles: dict[str, tuple[Typo, Zone]]
    # Position verticale du bloc de texte, en fraction de la hauteur. Mesuree a
    # 42,9 % sur la cover reel ; inutilisee quand les zones sont ancrees en bas.
    block_center: float | None = None
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
GABARIT_POST = Gabarit(
    name="kinda_post",
    width=1080,
    height=1440,
    roles={
        "headline": (
            Typo(
                font_size=80,
                line_height=1.15,
                letter_spacing=-0.06,
                color="#FFF7FA",
                mark_background="rgba(253, 139, 195, 0.8)",
            ),
            Zone(
                left=77,
                right=77,
                anchor="bottom",
                anchor_offset=76,
                align="left",
                # Plafond calibre a la tache 8. 5 lignes laisse la photo respirer ;
                # `HEADLINE_MAX = 110` dans compose.py plafonne deja a ~4 lignes.
                max_lines=5,
                max_line_width=926,   # 1080 - 2 x 77
                min_scale=0.7,
            ),
        ),
    },
)

# Valeurs MESUREES AU PIXEL sur `exemples/[PREVIEW] - Cover Reels.png`, faute de
# frame Figma extraite. Voir REFERENCE.md pour le releve complet.
#
# Deux pieges que l'oeil tend ici, et qui sont documentes parce qu'ils ont deja
# failli etre codes de travers :
#   - les deux corps sont PROCHES (mid-80 px), pas 1.4x. Ce qui fait paraitre le
#     titre plus gros, c'est sa bande plus haute (94 vs 85) et ses capitales
#     larges face a un sous-titre plein de lettres etroites ;
#   - la bande rose est droite, la creme est inclinee de 0,6 deg antihoraire.
#     Confirme comme voulu par le proprietaire du design.
GABARIT_REEL = Gabarit(
    name="kinda_reel",
    width=1080,
    height=1920,
    roles={
        "title": (
            Typo(
                font_size=86,
                line_height=1.1,
                letter_spacing=-0.04,
                color="#FFF7FA",
                background="#FD8BC3",
                tilt=0.0,
            ),
            Zone(
                left=77, right=77, anchor="center", anchor_offset=0, align="center",
                max_lines=2, max_line_width=926, min_scale=0.7,
            ),
        ),
        "subtitle": (
            Typo(
                font_size=80,
                line_height=1.1,
                letter_spacing=-0.04,
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
    block_center=0.429,
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


def variables_css(gabarit_: Gabarit, scale: float, scrim_height: float | None = None) -> str:
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
        "--voile-rose: rgba(253, 139, 195, 0.3);",
        "--voile-haut-hauteur: 727px;",
        # Geometrie du logo : fixe, identique sur tous les gabarits (le reel de
        # la tache 4 reprend le meme 77/87/150) -- meme raison de passer par une
        # variable que la recette des voiles.
        "--logo-right: 77px;",
        "--logo-top: 87px;",
        "--logo-width: 150px;",
        # Point de depart transparent du degrade rose : complete `--voile-rose`,
        # qui n'en porte que l'arrivee.
        "--voile-rose-debut: rgba(253, 139, 195, 0);",
    ]

    if gabarit_.block_center is not None:
        lignes.append(f"--block-center: {gabarit_.block_center * 100:.1f}%;")

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
        if typo.background is not None:
            lignes.append(f"--{role}-background: {typo.background};")
        if typo.mark_background is not None:
            lignes.append(f"--{role}-mark-background: {typo.mark_background};")
    if scrim_height is not None:
        lignes.append(f"--scrim-height: {round(scrim_height)}px;")
    return ":root {\n  " + "\n  ".join(lignes) + "\n}"
