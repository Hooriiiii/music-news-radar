import pytest

from app.content.highlight import enumeration_incoherente, to_html, validate_headline


def test_to_html_convertit_les_segments_surlignes():
    resultat = to_html("Nico Moreno et **c'est ce week-end** aux **Vieilles Charrues**")
    assert resultat == (
        "Nico Moreno et <mark>c&#x27;est ce week-end</mark> "
        "aux <mark>Vieilles Charrues</mark>"
    )


def test_to_html_echappe_le_html_avant_de_baliser():
    """Le titre vient d'un flux RSS : il ne doit jamais injecter de balise."""
    resultat = to_html("**<script>alert(1)</script>** en tournee")
    assert "<script>" not in resultat
    assert resultat.startswith("<mark>&lt;script&gt;")


def test_validate_refuse_les_marqueurs_impairs():
    with pytest.raises(ValueError, match="apparies"):
        validate_headline("Un titre **sans fermeture")


def test_validate_refuse_une_accroche_sans_surlignage():
    """Une accroche sans surlignage perd l'identite visuelle du compte."""
    with pytest.raises(ValueError, match="surligne"):
        validate_headline("Un titre parfaitement plat")


def test_validate_accepte_une_accroche_correcte():
    validate_headline("Le retour de **Daft Punk** ?")


# --- Une enumeration se surligne en entier, ou pas du tout -----------------
#
# Post publie le 2026-10-01 : « **Angele**, **Caroline Polachek** et SebastiAn
# s'unissent sur "Love Triangle" ». Deux artistes sur trois surlignes, sans
# raison. Le defaut n'est PAS le nombre de segments (277 des 960 accroches en
# base en portent deux, et la plupart le meritent) : c'est l'incoherence DANS
# une enumeration.


def test_une_enumeration_a_moitie_surlignee_est_signalee():
    noms, etats = enumeration_incoherente(
        "**Angele**, **Caroline Polachek** et SebastiAn s'unissent sur « Love Triangle »"
    )

    assert noms == ["Angele", "Caroline Polachek", "SebastiAn"]
    assert etats == [True, True, False]


def test_une_enumeration_entierement_surlignee_passe():
    assert (
        enumeration_incoherente("**Angele**, **Caroline Polachek** et **SebastiAn** s'unissent")
        is None
    )


def test_une_enumeration_sans_aucun_surlignage_passe():
    """L'exemple meme du prompt : les trois noms sont nus, le surlignage porte
    sur la date et le lieu. C'est voulu, et ce doit rester permis."""
    assert (
        enumeration_incoherente(
            "Nico Moreno, Mosimann, Cassius... et **c'est ce week-end** aux "
            "**Vieilles Charrues**"
        )
        is None
    )


def test_l_artiste_et_son_oeuvre_ne_sont_pas_une_enumeration():
    """Le double surlignage legitime, mesure comme majoritaire parmi les 277 :
    deux elements de nature DIFFERENTE, qui ne s'enumerent pas."""
    assert enumeration_incoherente("**Bicep** referme le triptyque **AIR**") is None
    assert (
        enumeration_incoherente(
            "**AIR (Part 3 of 3)** est enfin disponible, la trilogie de **Bicep**"
        )
        is None
    )


def test_un_debut_de_phrase_en_majuscule_ne_fabrique_pas_une_enumeration():
    """« C'est », « En », « Trois » ouvrent une phrase, ils n'enumerent rien :
    sans la regle « le segment se reduit au nom », ces textes -- pourtant
    coherents -- etaient signales (mesure sur les 1 704 textes en base)."""
    assert (
        enumeration_incoherente(
            "C'est officiel, **\"Run\"** de **Amelie Lens** et **Angele** est en ligne"
        )
        is None
    )
    assert enumeration_incoherente("En **1996**, **Sasha & Digweed** posaient les bases") is None


def test_une_enumeration_fermee_par_et_ne_deborde_pas_sur_la_suite():
    """« entre Londres, Miami et Liverpool, **Mele** sort son album » : la liste
    des villes est close par « et Liverpool ». Sans cette regle, Mele rejoignait
    les villes et l'incoherence etait inventee."""
    assert (
        enumeration_incoherente(
            "**14 titres** entre Londres, Miami et Liverpool, **Mele** sort son premier album"
        )
        is None
    )


def test_un_couple_ne_suffit_pas():
    """A deux elements la mesure donne des faux positifs francs (une salle et sa
    ville, un artiste et son label) : le seuil est a trois."""
    assert enumeration_incoherente("**Palacio**, Mexico") is None
    assert enumeration_incoherente("Adam Beyer et **Drumcode** a **Athenes**") is None
