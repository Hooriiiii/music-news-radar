"""Le reperage d'un mot ecrit sans son accent dans un texte COURT.

Le defaut de production : post 168, publie le 2026-10-03, bande de titre du
reel « Troye Sivan / invite surprise de Charli XCX a l'ACL ». Les textes de ce
module sont ceux du brouillon reel, pas des inventions.
"""

from app.content.accents import (
    LONGUEUR_MOT_MIN,
    MOTS_DESACCENTUES,
    mot_desaccentue,
    porte_un_accent,
    sans_accents,
)

# La legende du post 168, telle qu'elle est partie en ligne : parfaitement
# accentuee. C'est tout le probleme -- `probleme_accents` ne voyait qu'elle.
LEGENDE_168 = (
    "Troye Sivan a fait une apparition surprise pendant le set de Charli XCX a "
    "l'Austin City Limits, pour interpreter deux titres tires de son prochain "
    "album.\n\nCes morceaux sont extraits de She's The Best, qui sort la semaine "
    "prochaine. Les voir jouer en live avant meme la sortie officielle donne un "
    "sacre apercu de ce qui nous attend.\n\nLe duo sur scène confirme une "
    "complicite artistique, entre deux figures majeures de la pop electro.\n\n"
    "On attend deja de decouvrir l'album, avec en tete ces deux premiers "
    "extraits joués live a Austin."
)


def test_la_legende_du_meme_brouillon_sert_de_reference():
    """« scene » dans la cover, « scène » dans la legende : le modele SAIT."""
    assert mot_desaccentue(
        "**Troye Sivan** monte sur scene avec Charli XCX", legende=LEGENDE_168
    ) == ("scene", "scène")


def test_le_lexique_attrape_un_mot_absent_de_la_legende():
    """Post 154 : « le club legendaire marque une pause en 2027 »."""
    assert mot_desaccentue("le club legendaire marque une pause en **2027**") == (
        "legendaire",
        "légendaire",
    )


def test_un_texte_correctement_accentue_ne_dit_rien():
    assert mot_desaccentue(
        "**Bicep** referme le triptyque **AIR** à **Londres**", legende=LEGENDE_168
    ) is None


def test_un_participe_ambigu_n_est_jamais_signale():
    """« joue » et « joué » sont tous deux du francais valide : sans analyse
    syntaxique rien ne tranche, et 15 des 26 rapprochements bruts mesures le
    2026-10-04 etaient des participes CORRECTS (« Calvin Harris joue devant
    1,6 million de personnes »)."""
    legende = "Calvin Harris a joué devant un million de personnes."
    assert mot_desaccentue(
        "Calvin Harris joue devant **1,6 million** de personnes", legende=legende
    ) is None


def test_un_accent_en_milieu_de_mot_est_signale_lui():
    """« joues » pour « joués » : l'accent n'est PAS sur la derniere lettre."""
    assert mot_desaccentue(
        "deux titres joues en avant-premiere a l'**ACL**", legende=LEGENDE_168
    ) == ("joues", "joués")


def test_un_mot_capitalise_en_milieu_de_texte_est_laisse_tranquille():
    """Le lexique porte des graphies qui sont aussi de l'anglais valide
    (`video`, `special`, `piece`) : un titre de morceau les ferait signaler."""
    assert mot_desaccentue("**Lana Del Rey** ressort Video Games pour ses 15 ans") is None
    assert mot_desaccentue("une video de **Fred again..**") == ("video", "vidéo")


def test_un_mot_en_tete_de_texte_reste_examine():
    """Post 89 : « Apres deux tracks remarques, Curbi balance un EP complet »."""
    assert mot_desaccentue("Apres deux tracks remarques, **Curbi** sort un EP") == (
        "Apres",
        "après",
    )


def test_un_mot_que_la_legende_ecrit_des_deux_facons_ne_tranche_rien():
    """Le modele hesite : on ne tranche pas a sa place."""
    legende = "La scene francaise bouge, et la scène de Rennes la premiere."
    assert mot_desaccentue("sur scene a Rennes", legende=legende) is None


def test_les_mots_grammaticaux_courts_sont_hors_de_portee():
    """« a » pour « à » est le second defaut du post 168, et il est ambigu avec
    le verbe avoir. C'est le reste du brouillon qui declenche la relance, pas
    lui -- la composition entiere est reecrite."""
    legende = "Troye Sivan à l'ACL, il a joué deux titres."
    assert mot_desaccentue("invite surprise de **Charli XCX** a l'ACL", legende=legende) is None


def test_le_lexique_ne_contient_que_des_formes_sans_accent():
    """Une cle accentuee ne serait jamais consultee : la recherche ne se fait
    que sur les mots deja en ASCII."""
    for nu, accentuee in MOTS_DESACCENTUES.items():
        assert nu == sans_accents(nu) == nu.lower()
        assert len(nu) >= LONGUEUR_MOT_MIN
        assert porte_un_accent(accentuee)
        assert sans_accents(accentuee).lower() == nu
