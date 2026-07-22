import pytest

from app.content.highlight import to_html, validate_headline


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
