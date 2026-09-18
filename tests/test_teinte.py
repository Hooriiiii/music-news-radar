"""Quelle teinte porte une cover : le CHOIX, pas les valeurs.

Demande de l'utilisateur, 2026-09-18 : « pour les actus en lien avec l'ecologie,
je veux que la couleur soit verte ».
"""

from types import SimpleNamespace

from app.content.layout import PALETTE_ROSE, PALETTE_VERTE
from app.content.teinte import palette_du_post, teinte_de_article, teinte_du_post
from app.pipeline.scoring import Category


def test_une_actu_ecologie_est_verte():
    article = SimpleNamespace(category=Category.ECOLOGY.value)

    assert teinte_de_article(article) == PALETTE_VERTE.nom


def test_toute_autre_actu_garde_la_charte():
    for categorie in (Category.EVENT_FESTIVAL.value, Category.RIP.value, None, ""):
        assert teinte_de_article(SimpleNamespace(category=categorie)) == PALETTE_ROSE.nom


def test_un_article_sans_categorie_ne_leve_pas():
    """Des appelants passent des articles partiels (SimpleNamespace)."""
    assert teinte_de_article(SimpleNamespace()) == PALETTE_ROSE.nom


def test_le_post_porte_sa_teinte_figee():
    post = SimpleNamespace(variants={"variantes": [], "retenue": None, "palette": "verte"})

    assert teinte_du_post(post) == "verte"
    assert palette_du_post(post) is PALETTE_VERTE


def test_un_post_sans_teinte_retombe_sur_la_charte():
    """Trois formes possibles en base : `variants` NULL (post anterieur a la
    regle), sans cle `palette`, ou avec un nom inconnu pose a la main. Un rendu
    ne doit jamais echouer faute de teinte."""
    for variants in (None, {}, {"variantes": []}, {"palette": "bleue"}, {"palette": None}, "?"):
        assert teinte_du_post(SimpleNamespace(variants=variants)) == PALETTE_ROSE.nom
