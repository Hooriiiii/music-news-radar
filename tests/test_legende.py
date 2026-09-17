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


def test_le_credit_d_un_reel_precede_la_source():
    post = _post()
    post.credit, post.template = "@saralandry", "kinda_reel"
    assert texte_legende(post) == (
        "Une legende 🎧 ✨\n\n#electro\n\n🎥 via @saralandry\n\nSource : https://tsugi.fr/a"
    )


def test_le_credit_d_un_carrousel_a_son_propre_pictogramme():
    post = _post(hashtags=())
    post.credit, post.template = "@tsugimag", "kinda_post"
    assert "📸 via @tsugimag\n\nSource : https://tsugi.fr/a" in texte_legende(post)


def test_le_credit_n_est_jamais_coupe():
    post = _post(caption="x" * 3000)
    post.credit, post.template = "@saralandry", "kinda_reel"
    texte = texte_legende(post)
    assert len(texte) <= LEGENDE_MAX
    assert texte.endswith("🎥 via @saralandry\n\nSource : https://tsugi.fr/a")
