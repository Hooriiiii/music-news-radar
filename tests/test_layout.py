"""Les regles de mise en page, testees sans Chromium.

C'est tout l'interet de `layout.py` : les regles d'ajustement etaient
auparavant enfouies dans une boucle qui exigeait un navigateur, donc
invisibles en CI et impossibles a eprouver aux bornes.
"""

import pytest

from app.content.layout import (
    GABARIT_POST,
    PALETTE_ROSE,
    PALETTE_VERTE,
    VOILE_MINIMUM,
    Ajustement,
    Gabarit,
    Logo,
    Mesure,
    Typo,
    Zone,
    hauteur_voile,
    tient,
    variables_css,
)
# Le format publie d'un element de carrousel : c'est LUI qui dicte les
# dimensions du gabarit post depuis le 2026-09-18, d'ou l'import ici.
from app.publish.images import FORMAT_CARROUSEL


_LOGO_ESSAI = Logo(right=77, top=87, width=150)


def _zone(**kwargs) -> Zone:
    defauts = dict(
        left=77, right=77, anchor="bottom", anchor_offset=76, align="left",
        max_lines=5, max_line_width=926, min_scale=0.7,
    )
    return Zone(**{**defauts, **kwargs})


def test_mesure_compte_ses_lignes():
    assert Mesure(line_widths=(900.0, 850.0, 400.0), height=276.0).lines == 3


def test_tient_accepte_ce_qui_respecte_les_deux_plafonds():
    assert tient(_zone(), Mesure(line_widths=(900.0, 400.0), height=184.0))


def test_tient_refuse_une_ligne_de_trop():
    mesure = Mesure(line_widths=(900.0,) * 6, height=552.0)
    assert not tient(_zone(max_lines=5), mesure)


def test_tient_refuse_une_ligne_trop_large():
    """Le plafond de largeur est ce qui empeche une bande de reel de toucher
    les bords -- une contrainte que le plafond de hauteur ne voit pas."""
    mesure = Mesure(line_widths=(950.0,), height=92.0)
    assert not tient(_zone(max_line_width=926), mesure)


def _gabarit_essai(**kwargs) -> Gabarit:
    typo = Typo(font_size=80, line_height=1.15, letter_spacing=-0.06, color="#FFF7FA")
    return Gabarit(
        name="essai",
        width=1080,
        height=1440,
        roles={"headline": (typo, _zone(**kwargs))},
        logo=_LOGO_ESSAI,
    )


def test_ajustement_accepte_la_maquette_sans_reduire():
    """Le regime NORMAL : la police ne bouge pas. Une seule mesure suffit."""
    ajustement = Ajustement(_gabarit_essai())

    assert ajustement.prochaine_echelle() == 1.0
    ajustement.enregistrer({"headline": Mesure(line_widths=(900.0, 400.0), height=184.0)})

    assert ajustement.prochaine_echelle() is None
    assert ajustement.resultat.scale == 1.0
    assert ajustement.resultat.overflow is False
    assert ajustement.resultat.lines == {"headline": 2}


def test_ajustement_essaie_le_plancher_avant_de_dichotomiser():
    """Tester le plancher en deuxieme mesure permet de detecter tout de suite le
    cas desespere, au lieu de gaspiller cinq mesures pour y arriver."""
    ajustement = Ajustement(_gabarit_essai(max_lines=3, min_scale=0.7))

    assert ajustement.prochaine_echelle() == 1.0
    ajustement.enregistrer({"headline": Mesure(line_widths=(900.0,) * 5, height=460.0)})

    assert ajustement.prochaine_echelle() == 0.7


def test_ajustement_declare_le_debordement_si_le_plancher_ne_tient_pas():
    """Le comportement qui remplace le `logger.warning` suivi d'un rendu coupe :
    on rend quand meme, au plancher, mais le resultat est MARQUE."""
    ajustement = Ajustement(_gabarit_essai(max_lines=3, min_scale=0.7))

    ajustement.prochaine_echelle()
    ajustement.enregistrer({"headline": Mesure(line_widths=(900.0,) * 8, height=736.0)})
    assert ajustement.prochaine_echelle() == 0.7
    ajustement.enregistrer({"headline": Mesure(line_widths=(900.0,) * 6, height=552.0)})

    assert ajustement.prochaine_echelle() is None
    assert ajustement.resultat.scale == 0.7
    assert ajustement.resultat.overflow is True


def test_ajustement_dichotomise_vers_la_plus_grande_echelle_qui_tient():
    """On veut la plus GRANDE echelle acceptable, pas la premiere qui passe :
    les trois paliers 1.0/0.9/0.8 sautaient 10% d'un coup, ce qui se voit."""
    gabarit = _gabarit_essai(max_lines=3, min_scale=0.5)
    ajustement = Ajustement(gabarit)

    # Simulation : le nombre de lignes decroit quand l'echelle decroit.
    def mesure_pour(echelle: float) -> Mesure:
        lignes = 3 if echelle <= 0.8 else 5
        return Mesure(line_widths=(900.0,) * lignes, height=92.0 * lignes)

    essais: list[float] = []
    while (echelle := ajustement.prochaine_echelle()) is not None:
        essais.append(echelle)
        ajustement.enregistrer({"headline": mesure_pour(echelle)})

    assert essais[0] == 1.0
    assert essais[1] == 0.5
    assert ajustement.resultat.overflow is False
    # La dichotomie converge juste sous le seuil de 0.8, sans le depasser.
    assert 0.75 <= ajustement.resultat.scale <= 0.8


def test_ajustement_exige_que_TOUS_les_roles_tiennent():
    """Une seule echelle par slide : le bloc reduit d'un bloc, ce qui preserve
    les proportions entre titre et sous-titre du reel."""
    typo = Typo(font_size=86, line_height=1.1, letter_spacing=-0.04, color="#FFF7FA")
    gabarit = Gabarit(
        name="essai_reel", width=1080, height=1920,
        roles={
            "title": (typo, _zone(max_lines=2, max_line_width=926)),
            "subtitle": (typo, _zone(max_lines=2, max_line_width=926)),
        },
        logo=_LOGO_ESSAI,
    )
    ajustement = Ajustement(gabarit)
    ajustement.prochaine_echelle()
    ajustement.enregistrer({
        "title": Mesure(line_widths=(534.0,), height=94.0),        # tient
        "subtitle": Mesure(line_widths=(980.0,), height=85.0),     # trop large
    })

    assert ajustement.prochaine_echelle() is not None


def test_le_gabarit_post_porte_les_valeurs_de_la_maquette():
    """Les valeurs de la frame Figma 234:8. Un test-sentinelle : si quelqu'un les
    change sans repasser par REFERENCE.md, ce test le dit."""
    typo, zone = GABARIT_POST.roles["headline"]

    # 1350 et non les 1440 de la frame : le post est rendu NATIVEMENT au format
    # publie (cf. `test_le_post_est_rendu_au_format_publie`). Les marges de la
    # maquette, elles, sont intouchees -- c'est le point.
    assert (GABARIT_POST.width, GABARIT_POST.height) == (1080, 1350)
    assert typo.font_size == 80
    assert typo.line_height == 1.15
    assert typo.letter_spacing == -0.06
    assert typo.color == "#FFF7FA"
    assert zone.anchor == "bottom"
    assert (zone.left, zone.right, zone.anchor_offset) == (77, 77, 76)
    # Plafond calibre (tache 8, pas une valeur Figma) : mesure au rendu montrant
    # qu'a 5 lignes le sujet de la photo reste visible sous le texte. Voir le
    # commentaire de `GABARIT_POST` pour le releve complet.
    assert zone.max_lines == 5


def test_le_post_est_rendu_au_format_publie():
    """Le defaut du 2026-09-18, et la raison d'etre de cette hauteur.

    La cover etait dessinee en 1080x1440 (3:4) puis RECADREE AU CENTRE en
    1080x1350 par `app/publish/images.py`, parce que Meta refuse le 3:4 dans le
    fil. Ce recadrage mangeait 45 px en haut et 45 px en bas : le logo, pose a
    87 px du bord, arrivait a 42 px du bord publie, et l'accroche ancree a 76 px
    du bas a 31 px. Toutes les marges que le designer a posees contre les bords
    etaient donc fausses d'un tiers a la publication.

    La correction n'est pas un reglage de marges, c'est de rendre au format
    publie : si le rapport du gabarit est deja celui que Meta accepte, plus rien
    n'est recadre et les marges de la maquette sont celles qu'on voit.
    """
    assert (GABARIT_POST.width, GABARIT_POST.height) == FORMAT_CARROUSEL


def test_les_marges_de_la_maquette_sont_celles_qui_sont_publiees():
    """Le corollaire, verifie sur les deux marges dont l'utilisateur s'est
    plaint : plus aucun ecart entre ce qui est rendu et ce qui est publie."""
    _, zone = GABARIT_POST.roles["headline"]
    rogne = (GABARIT_POST.height - FORMAT_CARROUSEL[1]) / 2

    assert rogne == pytest.approx(0)
    assert GABARIT_POST.logo.top - rogne == pytest.approx(87)
    assert zone.anchor_offset - rogne == pytest.approx(76)


def test_hauteur_voile_retrouve_la_valeur_de_la_maquette():
    """Le bloc de reference fait 3 lignes = 276 px, et le voile de la maquette
    817 px. La formule doit retomber exactement dessus, sinon elle n'est pas
    une generalisation de la maquette mais une invention."""
    mesure = Mesure(line_widths=(900.0, 880.0, 400.0), height=276.0)

    assert hauteur_voile(GABARIT_POST, "headline", mesure) == pytest.approx(817.0)


def test_hauteur_voile_grandit_avec_le_bloc():
    """La consequence obligatoire du choix « bloc extensible » : sans ca, une
    accroche de 5 lignes sort du degrade dense et devient illisible sur une
    photo claire."""
    mesure = Mesure(line_widths=(900.0,) * 5, height=460.0)

    assert hauteur_voile(GABARIT_POST, "headline", mesure) == pytest.approx(1001.0)


def test_hauteur_voile_ne_descend_jamais_sous_la_maquette():
    mesure = Mesure(line_widths=(400.0,), height=92.0)

    assert hauteur_voile(GABARIT_POST, "headline", mesure) == VOILE_MINIMUM


def test_variables_css_expose_les_valeurs_du_gabarit():
    css = variables_css(GABARIT_POST, scale=0.85, scrim_height=900.0)

    assert "--scale: 0.85;" in css
    assert "--headline-font-size: 80px;" in css
    assert "--headline-letter-spacing: -0.06em;" in css
    assert "--headline-offset: 76px;" in css
    assert "--scrim-height: 900px;" in css


def test_variables_css_omet_ce_que_le_gabarit_ne_definit_pas():
    """Le reel n'a pas de voile ; le post n'a pas de bande de fond. Une variable
    ecrite a vide donnerait un `background: ;` silencieusement invalide."""
    css = variables_css(GABARIT_POST, scale=1.0)

    assert "--scrim-height" not in css
    assert "--headline-background" not in css


def test_variables_css_expose_la_recette_des_voiles():
    css = variables_css(GABARIT_POST, scale=1.0)

    assert "--voile-noir: rgba(0, 0, 0, 0.8);" in css
    assert "--voile-haut-hauteur: 727px;" in css
    assert "--voile-teinte-debut: rgba(253, 139, 195, 0);" in css


def test_variables_css_expose_la_geometrie_du_logo_du_gabarit():
    """La geometrie du logo passe par des variables (cf. style.css, qui ne doit
    declarer aucune valeur en dur derriere ces noms) mais elle vient du GABARIT :
    seuls `right` et `width` sont communs, `top` ne l'est pas."""
    from app.content.layout import GABARIT_REEL

    post = variables_css(GABARIT_POST, scale=1.0)
    reel = variables_css(GABARIT_REEL, scale=1.0)

    assert "--logo-right: 77px;" in post
    assert "--logo-width: 150px;" in post
    assert "--logo-top: 87px;" in post
    assert "--logo-top: 372px;" in reel


def test_les_deux_gabarits_n_ont_pas_le_meme_logo_top():
    """Deux hauteurs de toile, deux rognages de la grille Instagram : un
    `--logo-top` commun aux deux serait forcement faux pour l'un des deux."""
    from app.content.layout import GABARIT_REEL

    assert GABARIT_POST.logo.top != GABARIT_REEL.logo.top
    assert "--logo-top: 87px;" not in variables_css(GABARIT_REEL, scale=1.0)


def test_le_logo_du_reel_tombe_au_meme_endroit_dans_la_vignette_de_grille():
    """Le POURQUOI de 372, ancre pour que personne ne le prenne pour un reglage
    a l'oeil : la vignette de grille Instagram est un crop 4:5 CENTRE, donc son
    origine verticale vaut (hauteur - largeur x 5/4) / 2. Sur le reel elle tombe
    a 285 px ; le logo doit donc commencer 285 px plus bas que sur le post pour
    apparaitre a la meme place dans la vignette. Si quelqu'un change une hauteur
    de gabarit sans revoir le logo, c'est ce test qui casse."""
    from app.content.layout import GABARIT_REEL

    origine_reel = (GABARIT_REEL.height - GABARIT_REEL.width * 5 / 4) / 2

    assert origine_reel == pytest.approx(285)
    assert GABARIT_REEL.logo.top - origine_reel == pytest.approx(GABARIT_POST.logo.top)


def test_ajustement_respecte_le_max_des_planchers_par_role():
    """Le plancher liant est le plus RESTRICTIF des min_scale par role.
    Avec max() au lieu de min(), la dichotomie converge au-dessus du plancher
    le plus strict, meme si d'autres roles auraient un plancher plus permissif."""
    typo = Typo(font_size=80, line_height=1.15, letter_spacing=-0.06, color="#FFF7FA")
    gabarit = Gabarit(
        name="test_planchers",
        width=1080,
        height=1440,
        roles={
            "title": (typo, _zone(max_lines=2, max_line_width=926, min_scale=0.7)),
            "subtitle": (typo, _zone(max_lines=2, max_line_width=926, min_scale=0.4)),
        },
        logo=_LOGO_ESSAI,
    )
    ajustement = Ajustement(gabarit)

    # La premiere mesure ne tient pas : excede max_lines.
    assert ajustement.prochaine_echelle() == 1.0
    ajustement.enregistrer({
        "title": Mesure(line_widths=(500.0,) * 3, height=282.0),
        "subtitle": Mesure(line_widths=(500.0,), height=85.0),
    })

    # Le plancher teste en deuxieme est 0.7 (max des deux), pas 0.4.
    assert ajustement.prochaine_echelle() == 0.7


def test_variables_css_arrondit_la_hauteur_fractionnaire():
    """hauteur_voile() rend un float ; Chromium fournira 817.6 px.
    On doit arrondir (818px) plutot que tronquer (817px)."""
    css = variables_css(GABARIT_POST, scale=1.0, scrim_height=817.6)

    assert "--scrim-height: 818px;" in css


def test_le_gabarit_reel_porte_les_valeurs_mesurees_sur_la_maquette():
    from app.content.layout import GABARIT_REEL

    titre, zone_titre = GABARIT_REEL.roles["title"]
    sous_titre, zone_sous_titre = GABARIT_REEL.roles["subtitle"]

    assert (GABARIT_REEL.width, GABARIT_REEL.height) == (1080, 1920)
    # La bande de titre porte la TEINTE de l'article (rose par defaut, verte sur
    # une actu ecologie) : elle n'est donc plus une couleur figee du gabarit.
    assert titre.bande_teinte is True
    assert titre.background is None
    assert PALETTE_ROSE.hexa == "#FD8BC3"
    assert titre.color == "#FFF7FA"
    # La bande creme, elle, est NEUTRE : aucune teinte ne la traverse.
    assert sous_titre.bande_teinte is False
    assert sous_titre.background == "#FFF7FA"
    assert sous_titre.color == "#1D1D1D"
    # La bande rose est DROITE, la creme est inclinee : confirme par l'utilisateur
    # comme voulu (effet autocollant), et mesure a 0,6 deg antihoraire.
    assert titre.tilt == 0.0
    assert sous_titre.tilt == -0.6
    # Bloc centre : 42,9 % de 1920.
    assert zone_titre.anchor == "center"
    assert zone_titre.align == "center"
    assert zone_sous_titre.align == "center"
    # Deux bandes par role au maximum, puis reduction.
    assert zone_titre.max_lines == 2
    assert zone_sous_titre.max_lines == 2


def test_variables_css_expose_le_centre_du_bloc_quand_il_existe():
    from app.content.layout import GABARIT_REEL

    assert "--block-center: 42.9%;" in variables_css(GABARIT_REEL, scale=1.0)
    assert "--block-center" not in variables_css(GABARIT_POST, scale=1.0)


def test_la_palette_teinte_le_voile_et_le_mark_du_post():
    """Une seule couleur gouverne le voile et le surlignage : c'est pourquoi la
    palette porte un RGB et non trois chaines."""
    rose = variables_css(GABARIT_POST, scale=1.0)
    vert = variables_css(GABARIT_POST, scale=1.0, palette_=PALETTE_VERTE)

    assert "--voile-teinte: rgba(253, 139, 195, 0.3);" in rose
    assert "--headline-mark-background: rgba(253, 139, 195, 0.8);" in rose
    assert "--voile-teinte: rgba(53, 199, 122, 0.3);" in vert
    assert "--voile-teinte-debut: rgba(53, 199, 122, 0);" in vert
    assert "--headline-mark-background: rgba(53, 199, 122, 0.8);" in vert
    # Le reste de la mise en page ne bouge pas d'une teinte a l'autre.
    assert "--headline-font-size: 80px;" in vert


def test_la_palette_teinte_la_bande_de_titre_du_reel_mais_pas_la_creme():
    """Sur le reel, la teinte ne porte que la bande de TITRE : la bande creme du
    sous-titre est neutre et le reste, quelle que soit l'actu."""
    from app.content.layout import GABARIT_REEL

    vert = variables_css(GABARIT_REEL, scale=1.0, palette_=PALETTE_VERTE)

    assert "--title-background: #35C77A;" in vert
    assert "--subtitle-background: #FFF7FA;" in vert
    assert "--title-background: #FD8BC3;" in variables_css(GABARIT_REEL, scale=1.0)


def test_la_charte_reste_le_defaut_de_variables_css():
    """Aucun appelant ne doit avoir a demander le rose : c'est la charte."""
    assert variables_css(GABARIT_POST, scale=1.0) == variables_css(
        GABARIT_POST, scale=1.0, palette_=PALETTE_ROSE
    )


def test_une_palette_inconnue_degrade_vers_la_charte():
    """Le nom vient d'une colonne JSONB editable au dashboard : un nom inconnu
    doit degrader, jamais casser un rendu."""
    from app.content.layout import palette

    assert palette("bleue") is PALETTE_ROSE
    assert palette(None) is PALETTE_ROSE
    assert palette("verte") is PALETTE_VERTE


def test_le_vert_est_cale_sur_la_luminance_du_rose():
    """La parite de contraste est le POURQUOI de ce vert precis : le contraste
    percu du design ne doit pas bouger d'une actu a l'autre. Si quelqu'un change
    la couleur a l'oeil, ce test le dit."""

    def luminance(rgb):
        canaux = []
        for valeur in rgb:
            v = valeur / 255
            canaux.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]

    assert abs(luminance(PALETTE_VERTE.rgb) - luminance(PALETTE_ROSE.rgb)) < 0.01
