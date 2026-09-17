from types import SimpleNamespace

from app.content.legende import HASHTAGS_MAX, LEGENDE_MAX, texte_legende


def _post(caption="Une legende 🎧 ✨", hashtags=("#electro",), url="https://tsugi.fr/a"):
    return SimpleNamespace(caption=caption, hashtags=list(hashtags), article_url=url)


def test_legende_puis_hashtags_puis_source():
    assert texte_legende(_post()) == "Une legende 🎧 ✨\n\n#electro\n\nSource : https://tsugi.fr/a"


def test_sans_hashtags_pas_de_paragraphe_vide():
    assert texte_legende(_post(hashtags=())) == "Une legende 🎧 ✨\n\nSource : https://tsugi.fr/a"


def test_trente_hashtags_au_plus():
    """Au-dela, Meta refuse la publication."""
    texte = texte_legende(_post(hashtags=[f"#h{i}" for i in range(40)]))
    assert texte.count("#") == HASHTAGS_MAX


def test_une_legende_trop_longue_est_coupee_sans_perdre_la_source():
    texte = texte_legende(_post(caption="x" * 3000))
    assert len(texte) <= LEGENDE_MAX
    assert texte.endswith("Source : https://tsugi.fr/a")
    assert "…" in texte
