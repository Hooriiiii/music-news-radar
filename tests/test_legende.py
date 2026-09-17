from types import SimpleNamespace

from app.content.legende import HASHTAGS_MAX, LEGENDE_MAX, texte_legende


def _post(caption="Une legende 🎧 ✨", hashtags=("#electro",), url="https://tsugi.fr/a"):
    return SimpleNamespace(caption=caption, hashtags=list(hashtags), article_url=url)


def test_legende_puis_hashtags_sans_ligne_source():
    """Demande de l'utilisateur apres le premier post publie : plus de ligne
    « Source : <url> » sous la legende (un lien n'est pas cliquable sur Instagram)."""
    assert texte_legende(_post()) == "Une legende 🎧 ✨\n\n#electro"


def test_sans_hashtags_pas_de_paragraphe_vide():
    assert texte_legende(_post(hashtags=())) == "Une legende 🎧 ✨"


def test_aucune_url_d_article_dans_le_texte_publie():
    assert "tsugi.fr" not in texte_legende(_post())
    assert "Source" not in texte_legende(_post())


def test_trente_hashtags_au_plus():
    """Au-dela, Meta refuse la publication."""
    texte = texte_legende(_post(hashtags=[f"#h{i}" for i in range(40)]))
    assert texte.count("#") == HASHTAGS_MAX


def test_une_legende_trop_longue_est_coupee_sans_perdre_les_hashtags():
    texte = texte_legende(_post(caption="x" * 3000))
    assert len(texte) <= LEGENDE_MAX
    assert texte.endswith("#electro")
    assert "…" in texte


def test_le_credit_d_un_reel_suit_les_hashtags():
    post = _post()
    post.credit, post.template = "@saralandry", "kinda_reel"
    assert texte_legende(post) == "Une legende 🎧 ✨\n\n#electro\n\n🎥 via @saralandry"


def test_le_credit_d_un_carrousel_a_son_propre_pictogramme():
    post = _post(hashtags=())
    post.credit, post.template = "@tsugimag", "kinda_post"
    assert texte_legende(post).endswith("📸 via @tsugimag")


def test_le_credit_n_est_jamais_coupe():
    post = _post(caption="x" * 3000)
    post.credit, post.template = "@saralandry", "kinda_reel"
    texte = texte_legende(post)
    assert len(texte) <= LEGENDE_MAX
    assert texte.endswith("🎥 via @saralandry")


def test_une_legende_seule_trop_longue_ne_finit_pas_par_des_lignes_vides():
    texte = texte_legende(_post(caption="x" * 3000, hashtags=()))
    assert len(texte) == LEGENDE_MAX
    assert texte.endswith("x…")
