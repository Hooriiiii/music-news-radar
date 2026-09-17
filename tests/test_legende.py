from types import SimpleNamespace

import pytest

from app.content.legende import HASHTAGS_MAX, LEGENDE_MARGE, LEGENDE_MAX, aerer_legende, texte_legende


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
    assert texte_legende(post) == "Une legende 🎧 ✨\n\n🎥 @saralandry\n\n#electro"


def test_le_credit_d_un_carrousel_a_son_propre_pictogramme():
    post = _post(hashtags=())
    post.credit, post.template = "@tsugimag", "kinda_post"
    assert texte_legende(post).endswith("📸 @tsugimag")


def test_le_credit_d_un_carrousel_nomme_tous_les_artistes():
    """Plusieurs photos de comptes differents : tous les comptes sont credites."""
    post = _post(hashtags=())
    post.credit, post.template = "@fredagain @pinkpantheress", "kinda_post"
    assert texte_legende(post).endswith("📸 @fredagain @pinkpantheress")


def test_le_credit_n_est_jamais_coupe():
    post = _post(caption="x" * 3000)
    post.credit, post.template = "@saralandry", "kinda_reel"
    texte = texte_legende(post)
    assert len(texte) <= LEGENDE_MAX
    assert texte.endswith("🎥 @saralandry\n\n#electro")


def test_une_legende_seule_trop_longue_ne_finit_pas_par_des_lignes_vides():
    texte = texte_legende(_post(caption="x" * 3000, hashtags=()))
    assert len(texte) == LEGENDE_MARGE
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


# --- Abreviations, listes, marge de longueur ----------------------------------------


@pytest.mark.parametrize("texte", [
    "J. Cole feat. Travis Scott sortent un titre. Il arrive vendredi. Vous validez ?",
    "La compilation Vol. 2 arrive. Elle est signee Dr. Dre. On en parle ?",
    "Les U.S. Girls reviennent. Nouvel album en mars. Impatients ?",
])
def test_une_abreviation_ne_coupe_pas_la_phrase(texte):
    paragraphes = aerer_legende(texte).split("\n\n")
    assert len(paragraphes) == 2
    assert "\n" not in paragraphes[0].split(". ")[0]
    for abreviation in ("J. Cole", "feat. Travis", "Vol. 2", "Dr. Dre", "U.S. Girls"):
        if abreviation in texte:
            assert abreviation in aerer_legende(texte)


def test_les_abreviations_sont_prises_en_entier():
    assert aerer_legende("J. Cole feat. Travis Scott sortent un titre. Il arrive vendredi. Vous validez ?") == (
        "J. Cole feat. Travis Scott sortent un titre. Il arrive vendredi.\n\nVous validez ?"
    )


def test_une_liste_a_emojis_garde_ses_retours_simples():
    texte = "Charlotte de Witte a Paris.\n📅 24.09\n📍 Paris\n- Zenith"
    assert aerer_legende(texte) == "Charlotte de Witte a Paris.\n📅 24.09\n📍 Paris\n- Zenith"


def test_une_marge_sous_la_limite_de_meta_compte_tout_le_texte():
    post = _post(caption="x" * 3000, hashtags=[f"#tag{i}" for i in range(30)])
    post.credit, post.template = "@saralandry", "kinda_reel"
    texte = texte_legende(post)
    assert len(texte) <= LEGENDE_MARGE < LEGENDE_MAX
    assert "🎥 @saralandry" in texte


def test_credit_et_hashtags_trop_longs_retirent_des_hashtags_par_la_fin():
    post = _post(caption="Courte.", hashtags=["#" + "h" * 100 + str(i) for i in range(30)])
    texte = texte_legende(post)
    assert len(texte) <= LEGENDE_MARGE
    assert "#" + "h" * 100 + "0" in texte
    assert "#" + "h" * 100 + "29" not in texte


def test_une_ligne_ordinaire_apres_une_liste_reprend_un_paragraphe():
    assert aerer_legende("📅 24.09\n📍 Paris\nOn y va ?") == "📅 24.09\n📍 Paris\n\nOn y va ?"
