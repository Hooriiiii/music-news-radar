"""Les regles de mise en page, testees sans Chromium.

C'est tout l'interet de `layout.py` : les regles d'ajustement etaient
auparavant enfouies dans une boucle qui exigeait un navigateur, donc
invisibles en CI et impossibles a eprouver aux bornes.
"""

import pytest

from app.content.layout import (
    GABARIT_POST,
    VOILE_MINIMUM,
    Ajustement,
    Gabarit,
    Mesure,
    Typo,
    Zone,
    hauteur_voile,
    tient,
    variables_css,
)


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
    return Gabarit(name="essai", width=1080, height=1440, roles={"headline": (typo, _zone(**kwargs))})


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

    assert (GABARIT_POST.width, GABARIT_POST.height) == (1080, 1440)
    assert typo.font_size == 80
    assert typo.line_height == 1.15
    assert typo.letter_spacing == -0.06
    assert typo.color == "#FFF7FA"
    assert zone.anchor == "bottom"
    assert (zone.left, zone.right, zone.anchor_offset) == (77, 77, 76)


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
    assert "--voile-rose-debut: rgba(253, 139, 195, 0);" in css
    # Geometrie du logo : fixe, identique sur tous les gabarits -- meme raison
    # de passer par une variable que la recette des voiles (cf. style.css, qui
    # ne doit declarer aucune valeur en dur derriere ces noms).
    assert "--logo-right: 77px;" in css
    assert "--logo-top: 87px;" in css
    assert "--logo-width: 150px;" in css


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
    assert titre.background == "#FD8BC3"
    assert titre.color == "#FFF7FA"
    assert sous_titre.background == "#FFF7FA"
    assert sous_titre.color == "#1D1D1D"
    # La bande rose est DROITE, la creme est inclinee : confirme par l'utilisateur
    # comme voulu (effet autocollant), et mesure a 0,6 deg antihoraire.
    assert titre.tilt == 0.0
    assert sous_titre.tilt == -0.6
    # Les deux corps sont PROCHES, contrairement a ce que l'oeil suggere : les
    # glyphes mesurent 83 px (titre) et 64 px (sous-titre), ce qui ramene les deux
    # dans les mid-80 px. Coder « titre 1.4x » ferait diverger le rendu.
    assert abs(titre.font_size - sous_titre.font_size) <= 8
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
