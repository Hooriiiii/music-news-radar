from types import SimpleNamespace

import pytest

from app.content.legende import (
    HASHTAGS_MAX,
    LEGENDE_MARGE,
    LEGENDE_MAX,
    aerer_legende,
    credit_de_compte,
    noms_a_bannir,
    sans_mentions_de_source,
    texte_legende,
)
from app.models import SourceCategorie


def _post(caption="Une legende 🎧 ✨", hashtags=("#electro",), url="https://tsugi.fr/a"):
    return SimpleNamespace(caption=caption, hashtags=list(hashtags), article_url=url)


def _article(url="https://www.stereogum.com/sheeran-gaza", source_name="Stereogum (RSS)"):
    return SimpleNamespace(source=SimpleNamespace(name=source_name), url=url)


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


def test_un_hashtag_rendu_sans_diese_en_recoit_un():
    """Le vrai reel du 2026-09-18 (instagram.com/reel/Ddbuwecjr5A/) s'est termine
    par « iheartradiofestival bts lesserafim cardib » en texte brut : aucune
    portee. Le modele rend tantot `#techno`, tantot `techno` -- le texte publie
    tranche."""
    post = _post(hashtags=["iheartradiofestival", "bts", "#lesserafim", "cardib", "muse"])
    assert texte_legende(post).endswith("#iheartradiofestival #bts #lesserafim #cardib #muse")


def test_un_hashtag_est_un_seul_mot_sans_espace_ni_ponctuation():
    """Instagram couperait « #vieilles charrues » au premier espace."""
    post = _post(hashtags=["vieilles charrues", "hip-hop", "#k-pop !"])
    assert texte_legende(post).endswith("#vieillescharrues #hiphop #kpop")


def test_deux_hashtags_identiques_apres_normalisation_n_en_font_qu_un():
    post = _post(hashtags=["#Techno", "techno", "#TECHNO", "#electro"])
    assert texte_legende(post).endswith("#Techno #electro")


def test_un_hashtag_sans_rien_d_utilisable_disparait():
    """Publier un `#` seul serait pire que de ne rien publier."""
    post = _post(hashtags=["#", "🎧", "electro"])
    assert texte_legende(post).endswith("\n\n#electro")


# --- Les legendes ne nomment plus aucun media (2026-09-21) ------------------
#
# Mesure sur les posts REELLEMENT publies sur @kindanewsfrance : post 71
# « Qu'en pensez-vous ? Info a retrouver via Stereogum. », post 63
# « Info via Stereogum. », post 65 « ... selon Soompi ... ». « supprime les
# sources des descriptions [...] parle de l'actu en elle-meme. »


def test_une_phrase_via_la_source_est_retiree():
    article = _article()
    caption = "Ed Sheeran evoque Gaza dans un discours poignant. Info via Stereogum."
    assert sans_mentions_de_source(caption, article) == (
        "Ed Sheeran evoque Gaza dans un discours poignant."
    )


def test_la_phrase_reellement_publiee_du_post_71_ne_survit_pas():
    article = _article()
    caption = "Qu'en pensez-vous ? Info a retrouver via Stereogum."
    assert sans_mentions_de_source(caption, article) == "Qu'en pensez-vous ?"


def test_selon_la_source_est_aussi_retiree():
    article = _article(url="https://www.soompi.com/article/1", source_name="Soompi (RSS)")
    caption = "LE SSERAFIM revient sur le devant de la scene. C'est confirme selon Soompi."
    assert sans_mentions_de_source(caption, article) == (
        "LE SSERAFIM revient sur le devant de la scene."
    )


def test_le_domaine_sans_tld_attrape_le_nom_ecrit_en_toutes_lettres():
    """`stereogum.com` doit attraper « Stereogum » (exigence du plan) -- la
    racine est prouvee ici par le nom de la source, qui EST "Stereogum"."""
    article = _article(source_name="Stereogum (RSS)", url="https://www.stereogum.com/x")
    caption = "Grosse annonce du jour. Stereogum a sorti l'info en premier."
    assert "Stereogum" not in sans_mentions_de_source(caption, article)


def test_un_artiste_homonyme_d_un_media_sans_rapport_n_est_pas_mutile():
    """Le nom banni est celui de LA source de CET article, jamais devine : un
    artiste qui porte par coincidence le nom d'un media SANS RAPPORT reste."""
    article = _article(source_name="Tsugi (RSS)", url="https://www.tsugi.fr/x")
    caption = "Stereogum, jeune artiste rennais, sort son premier titre. Il tourne en Bretagne."
    assert sans_mentions_de_source(caption, article) == caption


def test_une_legende_entierement_faite_de_mentions_de_source_reste_intacte():
    """Une legende vide serait pire qu'une legende qui cite un media --
    `PostDraft` la validerait mal."""
    article = _article()
    caption = "Info via Stereogum. Selon Stereogum, c'est confirme."
    assert sans_mentions_de_source(caption, article) == caption


def test_un_paragraphe_garde_ses_autres_phrases():
    article = _article()
    caption = "Ed Sheeran evoque Gaza.\n\nInfo via Stereogum. La sortie est prevue vendredi."
    assert sans_mentions_de_source(caption, article) == (
        "Ed Sheeran evoque Gaza.\n\nLa sortie est prevue vendredi."
    )


def test_credit_de_domaine_ne_s_affiche_plus():
    """Post 59 reellement publie : credit `djmag.com beatportal.com`."""
    post = _post(hashtags=())
    post.credit, post.template = "djmag.com", "kinda_post"
    assert texte_legende(post) == "Une legende 🎧 ✨"


def test_credit_de_compte_reste_affiche():
    post = _post(hashtags=())
    post.credit, post.template = "@defectedrecords", "kinda_post"
    assert texte_legende(post).endswith("📸 @defectedrecords")


def test_credit_de_compte_est_pur():
    assert credit_de_compte("djmag.com") is None
    assert credit_de_compte("@defectedrecords") == "@defectedrecords"
    assert credit_de_compte("@fredagain @pinkpantheress") == "@fredagain @pinkpantheress"
    assert credit_de_compte(None) is None


def test_credit_de_compte_retire_seulement_le_domaine_d_un_credit_mixte():
    """Un fond d'origine `artiste` qui s'avere etre une affiche peut ouvrir une
    reserve `web` sur le MEME `actifs` (ordre compte -> artiste -> web dans
    `choix_visuels.py`) : un brouillon compose par l'ancienne version peut donc
    porter un credit MIXTE (« @fredagain ra.co »). Seul le domaine doit partir,
    jamais le credit d'artiste legitime -- pas de tout-ou-rien."""
    assert credit_de_compte("@fredagain ra.co") == "@fredagain"
    assert credit_de_compte("ra.co @fredagain") == "@fredagain"
    assert credit_de_compte("@fredagain djmag.com @pinkpantheress") == "@fredagain @pinkpantheress"


# --- La racine d'un domaine n'est bannie QUE si le nom de la source le prouve
#
# Tour de correction 2/5 : un seuil de longueur (racine >= 5 caracteres) a ete
# tente puis rejete en revue, reproduction a l'appui -- « radio.fr » a une
# racine de 5 lettres ("radio"), un mot parfaitement ordinaire, et la bannir
# aurait ampute toute legende qui parle de musique passee "a la radio". Le
# remede retenu : la racine n'est bannie que si elle se retrouve dans le NOM
# DE LA SOURCE (comparaison insensible a la casse, aux accents, aux
# tirets/espaces) -- une PREUVE, pas une statistique sur la longueur du mot.


def test_une_racine_sans_rapport_avec_la_source_ne_mutile_pas_un_mot_ordinaire():
    """Cas reproduit par la revue : source generique, domaine « radio.fr » --
    la racine "radio" n'est nulle part dans le nom de la source, donc jamais
    bannie seule. La phrase sur la radio survit."""
    article = _article(source_name="Le flux (RSS)", url="https://www.radio.fr/article/x")
    caption = "Enorme soiree hier soir. On a passe la radio a fond toute la nuit, ambiance dingue."
    assert sans_mentions_de_source(caption, article) == caption


def test_vice_com_n_est_pas_banni_seul_sans_preuve_dans_le_nom_de_la_source():
    # Le nom de la SOURCE est generique (pas "Vice") : rien ne prouve que la
    # racine du domaine ("vice") designe le media, donc elle n'est pas bannie.
    article = _article(source_name="Le flux (RSS)", url="https://www.vice.com/fr/article/x")
    caption = "Le clip assume un vice certain pour la nuit electro. On adore ce parti pris."
    assert sans_mentions_de_source(caption, article) == caption


def test_le_domaine_complet_reste_toujours_banni_sans_condition():
    """Le domaine COMPLET (avec son TLD) reste banni sans condition, meme
    quand sa racine seule ne l'est pas -- il n'a aucune chance de coincider
    avec un mot du langage courant."""
    article = _article(source_name="Le flux (RSS)", url="https://www.vice.com/fr/article/x")
    caption = "Gros dossier electro. A lire sur vice.com des ce soir."
    assert "vice.com" not in sans_mentions_de_source(caption, article)


def test_noms_a_bannir_ajoute_la_racine_prouvee_par_le_nom_de_la_source():
    """Unite : le nom de la source ("Ouest-France") prouve que la racine du
    domaine ("ouest-france") designe bien le media -- elle rejoint la liste."""
    article = _article(source_name="Ouest-France (RSS)", url="https://www.ouest-france.fr/x")
    assert "ouest-france" in noms_a_bannir(article)


def test_noms_a_bannir_n_ajoute_pas_la_racine_sans_preuve():
    """Unite, cas radio.fr : rien dans le nom de la source ne dit que "radio"
    designe ce media -- la racine reste hors de la liste, seul le domaine
    complet y figure."""
    article = _article(source_name="Le flux (RSS)", url="https://www.radio.fr/x")
    noms = noms_a_bannir(article)
    assert "radio" not in noms
    assert "radio.fr" in noms


def test_la_racine_prouvee_attrape_une_graphie_que_le_nom_de_la_source_seul_raterait():
    """La SOURCE est enregistree sans tiret ("Ouest France"), la LEGENDE ecrit
    le media avec un tiret ("Ouest-France", la graphie du domaine) : le nom de
    la source seul (`\\bOuest France\\b`, avec un espace) ne matcherait pas
    "Ouest-France". La racine prouvee ("ouest-france", tiret compris) si."""
    article = _article(source_name="Ouest France (RSS)", url="https://www.ouest-france.fr/x")
    caption = "Le festival prend de l'ampleur. C'est confirme par Ouest-France ce matin."
    assert sans_mentions_de_source(caption, article) == "Le festival prend de l'ampleur."


def test_la_preuve_ignore_casse_accents_et_tirets():
    """La comparaison entre la racine du domaine et le nom de la source est
    insensible a la casse, aux accents et aux tirets/espaces."""
    article = _article(source_name="ouest france", url="https://www.Ouest-France.fr/x")
    assert "ouest-france" in noms_a_bannir(article)


# --- La source EST le sujet : festival, artiste, label, club (revue 2026-09-21)
# Mesure : source « Astropolis (IG) », la phrase qui porte l'actu disparaissait.
# CLAUDE.md, « Qui parle : `sources.categorie` » : pour un compte d'artiste, de
# festival, de label ou de club, `source.name` est le SUJET de l'actu, pas la
# source de l'info. Seul un compte `media` (ou de categorie inconnue, dans le
# doute) voit son nom banni.

LEGENDE_ASTROPOLIS = (
    "Astropolis devoile sa programmation. Trois jours a Brest fin aout. "
    "Les billets partent vite."
)


def _article_de(categorie, nom="Astropolis (IG)", url="https://www.instagram.com/p/abc/"):
    return SimpleNamespace(source=SimpleNamespace(name=nom, categorie=categorie), url=url)


def test_le_nom_d_un_festival_qui_publie_son_actu_n_est_jamais_banni():
    article = _article_de(SourceCategorie.FESTIVAL)

    assert sans_mentions_de_source(LEGENDE_ASTROPOLIS, article) == LEGENDE_ASTROPOLIS


def test_le_nom_d_un_media_reste_banni_a_cote():
    """Meme legende, meme nom, mais la source est un MEDIA : la phrase qui le
    nomme renvoie vers un concurrent, elle part."""
    article = _article_de(SourceCategorie.MEDIA)

    assert sans_mentions_de_source(LEGENDE_ASTROPOLIS, article) == (
        "Trois jours a Brest fin aout. Les billets partent vite."
    )


def test_une_source_de_categorie_inconnue_est_traitee_comme_un_media():
    """Dans le doute on protege du plagiat : `None` = inconnu (sources X, ou pas
    encore classee)."""
    assert "Astropolis" in noms_a_bannir(_article_de(None))


def test_artiste_label_et_club_sont_aussi_le_sujet():
    for categorie in (SourceCategorie.ARTISTE, SourceCategorie.LABEL, SourceCategorie.CLUB):
        assert "Astropolis" not in noms_a_bannir(_article_de(categorie))


def test_le_domaine_reste_bannissable_meme_pour_un_festival():
    """Le domaine de `article.url` nomme le SITE, jamais le sujet de l'actu : il
    reste banni dans tous les cas. Sa racine, elle, se confond avec le nom du
    festival (« astropolis.org » / « Astropolis ») et n'est donc plus bannie."""
    article = _article_de(
        SourceCategorie.FESTIVAL, nom="Astropolis", url="https://www.astropolis.org/prog",
    )

    noms = noms_a_bannir(article)

    assert "astropolis.org" in noms
    assert "astropolis" not in noms
    assert "Astropolis" not in noms
