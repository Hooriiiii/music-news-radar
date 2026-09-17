from types import SimpleNamespace

from app.content.legende import HASHTAGS_MAX, LEGENDE_MAX, aerer_legende, texte_legende


def _post(caption="Une legende 🎧 ✨", hashtags=("#electro",), url="https://tsugi.fr/a"):
    return SimpleNamespace(caption=caption, hashtags=list(hashtags), article_url=url)


def test_legende_puis_hashtags_sans_ligne_source():
    """Demande de l'utilisateur apres le premier post publie : plus de ligne
    « Source : <url> » sous la legende."""
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


def test_les_hashtags_ferment_la_legende_apres_le_credit():
    post = _post()
    post.credit, post.template = "@saralandry", "kinda_reel"
    assert texte_legende(post) == "Une legende 🎧 ✨\n\n🎥 via @saralandry\n\n#electro"


def test_le_credit_d_un_carrousel_a_son_propre_pictogramme():
    post = _post(hashtags=())
    post.credit, post.template = "@tsugimag", "kinda_post"
    assert texte_legende(post).endswith("📸 via @tsugimag")


def test_le_credit_n_est_jamais_coupe():
    post = _post(caption="x" * 3000)
    post.credit, post.template = "@saralandry", "kinda_reel"
    texte = texte_legende(post)
    assert len(texte) <= LEGENDE_MAX
    assert texte.endswith("🎥 via @saralandry\n\n#electro")


def test_une_legende_seule_trop_longue_ne_finit_pas_par_des_lignes_vides():
    texte = texte_legende(_post(caption="x" * 3000, hashtags=()))
    assert len(texte) == LEGENDE_MAX
    assert texte.endswith("x…")


BLOC = (
    "Charlotte de Witte revient a Paris. Elle jouera le 12 octobre au Zenith. 🪩 "
    "La billetterie ouvre vendredi. Vous y serez ? 🎧"
)


def test_un_bloc_sans_ligne_vide_est_aere_en_paragraphes_de_deux_phrases():
    assert aerer_legende(BLOC) == (
        "Charlotte de Witte revient a Paris. Elle jouera le 12 octobre au Zenith. 🪩"
        "\n\nLa billetterie ouvre vendredi. Vous y serez ? 🎧"
    )


def test_l_emoji_reste_avec_sa_phrase():
    texte = aerer_legende("Premiere phrase. 🎧 Deuxieme phrase ! ✨ Troisieme phrase.")
    assert texte == "Premiere phrase. 🎧 Deuxieme phrase ! ✨\n\nTroisieme phrase."


def test_une_legende_deja_en_paragraphes_n_est_pas_touchee():
    texte = "Une phrase. Une autre.\n\nEncore une. Et une derniere."
    assert aerer_legende(texte) == texte


def test_deux_phrases_restent_un_seul_paragraphe():
    assert aerer_legende("Une phrase. Une autre 🎧") == "Une phrase. Une autre 🎧"


def test_des_points_de_suspension_suivis_d_une_minuscule_ne_coupent_pas():
    texte = "Nico Moreno, Mosimann, Cassius... et c'est ce week-end. Rendez-vous a Carhaix. On y va ?"
    assert aerer_legende(texte) == (
        "Nico Moreno, Mosimann, Cassius... et c'est ce week-end. Rendez-vous a Carhaix."
        "\n\nOn y va ?"
    )


def test_des_retours_a_la_ligne_simples_deviennent_des_paragraphes():
    assert aerer_legende("Ligne un.\nLigne deux.\nLigne trois.") == "Ligne un.\n\nLigne deux.\n\nLigne trois."


def test_la_publication_aere_la_legende_et_respecte_la_limite():
    assert texte_legende(_post(caption=BLOC)).startswith("Charlotte de Witte revient a Paris.")
    assert "Zenith. 🪩\n\nLa billetterie" in texte_legende(_post(caption=BLOC))
    longue = " ".join(["Une phrase assez longue pour remplir la legende."] * 60)
    texte = texte_legende(_post(caption=longue))
    assert len(texte) <= LEGENDE_MAX
    assert "\n\n" in texte and texte.endswith("#electro")
