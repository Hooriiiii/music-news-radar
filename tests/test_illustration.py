"""Le fond d'une cover : une illustration, jamais une affiche.

Aucun appel reseau, aucun appel Claude : le juge est toujours injecte, et le
telechargement remplace par un faux. C'est la contrainte du module lui-meme --
il doit degrader proprement sans l'un ni l'autre.
"""

import datetime as dt
import io
import json

import pytest
from PIL import Image

from app.config import settings
from app.content import illustration
from app.content.illustration import (
    Fond,
    Lot,
    Verdict,
    choisir_illustration,
    eviter_l_affiche,
    lire_cache,
    make_claude_juge,
    reduire,
    resolution_suffisante,
    tailles_de,
    verdicts_de,
)
from app.content.visuels import (
    FOND_KINDA,
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    ORIGINE_COMPTE,
    ORIGINE_KINDA,
    ORIGINE_WEB,
    VisuelDiffere,
)

MAINTENANT = dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.timezone.utc)

AFFICHE = "https://exemple.test/affiche.jpg"
SCENE = "https://exemple.test/scene.jpg"
PORTRAIT = "https://exemple.test/portrait.jpg"

VERDICTS = {
    AFFICHE: Verdict(affiche=True, sujet="autre", note=15),
    SCENE: Verdict(affiche=False, sujet="scene", note=80),
    PORTRAIT: Verdict(affiche=False, sujet="portrait", note=60),
}


def _jpeg(largeur: int = 40, hauteur: int = 50) -> bytes:
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (20, 30, 40)).save(tampon, format="JPEG")
    return tampon.getvalue()


@pytest.fixture(autouse=True)
def _sans_reseau(monkeypatch):
    """Toute image candidate se telecharge instantanement et sans sortir."""
    monkeypatch.setattr(
        illustration, "telecharger_media", lambda url, *a, **k: (_jpeg(), ".jpg")
    )


def _juge_espion(urls_jugees: list[str], candidats: list[str]):
    """Un juge qui rend les verdicts attendus et NOTE combien de fois il est
    appele -- le cout est la moitie du sujet de ce module."""
    appels: list[int] = []

    def juger(images):
        appels.append(len(images))
        # Les images arrivent dans l'ordre des URL non encore connues.
        return [VERDICTS[url] for url in urls_jugees]

    return juger, appels


def test_une_affiche_en_premier_laisse_la_place_a_la_photo_de_scene(tmp_path):
    """Le cas qui justifie tout : un compte de festival publie son affiche de
    line-up en tete de carrousel, et c'est elle qui faisait le fond."""
    candidats = [AFFICHE, SCENE, PORTRAIT]
    juge, appels = _juge_espion(candidats, candidats)

    choisi = choisir_illustration(
        candidats, juge=juge, maintenant=MAINTENANT, chemin_cache=tmp_path / "c.json"
    )

    assert choisi == SCENE
    assert appels == [3]


def test_un_verdict_en_cache_ne_coute_aucun_appel(tmp_path):
    chemin = tmp_path / "c.json"
    candidats = [AFFICHE, SCENE]
    juge, appels = _juge_espion(candidats, candidats)
    assert choisir_illustration(
        candidats, juge=juge, maintenant=MAINTENANT, chemin_cache=chemin
    ) == SCENE

    def juge_interdit(images):  # pragma: no cover -- il ne doit jamais tourner
        raise AssertionError("le juge a ete rappele alors que le cache repondait")

    choisi = choisir_illustration(
        candidats, juge=juge_interdit, maintenant=MAINTENANT, chemin_cache=chemin
    )

    assert choisi == SCENE
    assert appels == [2]


def test_un_juge_en_panne_laisse_le_comportement_d_avant(tmp_path):
    """Une panne d'API ne doit jamais couter une composition : on rend None,
    et l'appelant garde son premier candidat."""

    def juge_casse(images):
        raise RuntimeError("502 Bad Gateway")

    assert (
        choisir_illustration(
            [AFFICHE, SCENE], juge=juge_casse, maintenant=MAINTENANT,
            chemin_cache=tmp_path / "c.json",
        )
        is None
    )


def test_un_seul_candidat_ne_se_juge_pas(tmp_path):
    """Il n'y a rien a classer : payer un appel pour confirmer l'unique image
    disponible serait une depense sans consequence possible."""

    def juge_interdit(images):  # pragma: no cover
        raise AssertionError("juge appele sans choix a faire")

    assert (
        choisir_illustration(
            [AFFICHE], juge=juge_interdit, maintenant=MAINTENANT,
            chemin_cache=tmp_path / "c.json",
        )
        is None
    )


def test_tout_affiche_rend_la_moins_mauvaise(tmp_path):
    """Mieux vaut une cover sur une affiche qu'un creneau perdu : le repli vers
    une photo d'artiste suivi vit en amont, dans `choix_visuels`."""
    deuxieme = "https://exemple.test/affiche2.jpg"
    verdicts = [
        Verdict(affiche=True, sujet="autre", note=10),
        Verdict(affiche=True, sujet="autre", note=18),
    ]

    choisi = choisir_illustration(
        [AFFICHE, deuxieme], juge=lambda images: verdicts, maintenant=MAINTENANT,
        chemin_cache=tmp_path / "c.json",
    )

    assert choisi == deuxieme


def test_une_image_inaccessible_ne_fait_pas_perdre_les_autres(tmp_path, monkeypatch):
    """Une URL signee du CDN Instagram expire : son candidat est saute, les
    autres sont juges quand meme."""
    monkeypatch.setattr(
        illustration,
        "telecharger_media",
        lambda url, *a, **k: (None, "404") if url == AFFICHE else (_jpeg(), ".jpg"),
    )
    juge, appels = _juge_espion([SCENE], [AFFICHE, SCENE])

    choisi = choisir_illustration(
        [AFFICHE, SCENE], juge=juge, maintenant=MAINTENANT, chemin_cache=tmp_path / "c.json"
    )

    assert choisi == SCENE
    assert appels == [1]


def test_un_candidat_jamais_juge_passe_devant_une_affiche_averee(tmp_path):
    """Ne pas savoir vaut mieux que savoir que c'est une affiche."""
    inconnu = "https://exemple.test/inconnu.jpg"
    connus = {AFFICHE: VERDICTS[AFFICHE]}
    illustration.ecrire_cache(
        illustration.Cache(connus), MAINTENANT, tmp_path / "c.json"
    )

    # Le juge ne rend rien pour l'inconnu : il reste au verdict neutre.
    choisi = choisir_illustration(
        [AFFICHE, inconnu], juge=lambda images: [Verdict(affiche=False, sujet="autre", note=50)],
        maintenant=MAINTENANT, chemin_cache=tmp_path / "c.json",
    )

    assert choisi == inconnu


def test_le_cache_expire_apres_son_delai(tmp_path):
    chemin = tmp_path / "c.json"
    illustration.ecrire_cache(illustration.Cache({SCENE: VERDICTS[SCENE]}), MAINTENANT, chemin)

    frais = lire_cache(MAINTENANT + dt.timedelta(days=1), chemin)
    perime = lire_cache(
        MAINTENANT + dt.timedelta(days=settings.illustration_cache_jours + 1), chemin
    )

    assert SCENE in frais.verdicts
    assert perime.verdicts == {}


def test_un_cache_corrompu_vaut_un_cache_vide(tmp_path):
    chemin = tmp_path / "c.json"
    chemin.write_text("{ ceci n'est pas du JSON", encoding="utf-8")

    assert lire_cache(MAINTENANT, chemin).verdicts == {}


def test_le_cache_est_ecrit_atomiquement(tmp_path):
    chemin = tmp_path / "sous" / "c.json"
    illustration.ecrire_cache(illustration.Cache({SCENE: VERDICTS[SCENE]}), MAINTENANT, chemin)

    contenu = json.loads(chemin.read_text(encoding="utf-8"))

    assert contenu["version"] == illustration.CACHE_VERSION
    assert contenu["verdicts"][SCENE]["sujet"] == "scene"
    assert not list(chemin.parent.glob("*.tmp"))


def test_reduire_borne_la_surface_donc_le_cout_en_jetons():
    """Le cout d'une image est proportionnel a sa surface : une photo de presse
    non reduite coute quinze fois sa vignette."""
    grande = io.BytesIO()
    Image.new("RGB", (2000, 1333), (200, 30, 40)).save(grande, format="JPEG")

    petite = Image.open(io.BytesIO(reduire(grande.getvalue(), taille=512)))

    assert max(petite.size) == 512
    assert petite.format == "JPEG"


def test_la_vision_coupee_ne_juge_rien(tmp_path, monkeypatch):
    """Coupe-circuit de `.env` : sans juge injecte et vision eteinte, on rend le
    comportement d'avant plutot qu'un appel."""
    monkeypatch.setattr(settings, "illustration_vision_enabled", False)

    assert (
        choisir_illustration(
            [AFFICHE, SCENE], maintenant=MAINTENANT, chemin_cache=tmp_path / "c.json"
        )
        is None
    )


def test_le_juge_refuse_un_appariement_qui_glisse():
    """Un verdict de moins et toutes les notes glissent d'un rang : une affiche
    prendrait celle d'une photo. On prefere ne rien savoir."""

    class FauxClient:
        class messages:  # noqa: N801
            @staticmethod
            def parse(**kwargs):
                class Reponse:
                    parsed_output = illustration.Verdicts(verdicts=[VERDICTS[SCENE]])

                return Reponse()

    with pytest.raises(ValueError, match="1 verdicts pour 2 images"):
        make_claude_juge(FauxClient())([_jpeg(), _jpeg()])


def test_le_juge_envoie_une_image_par_candidat():
    envoyes = {}

    class FauxClient:
        class messages:  # noqa: N801
            @staticmethod
            def parse(**kwargs):
                envoyes.update(kwargs)

                class Reponse:
                    parsed_output = illustration.Verdicts(
                        verdicts=[VERDICTS[SCENE], VERDICTS[AFFICHE]]
                    )

                return Reponse()

    verdicts = make_claude_juge(FauxClient())([_jpeg(), _jpeg()])

    contenu = envoyes["messages"][0]["content"]
    assert [bloc["type"] for bloc in contenu] == ["text", "image", "text", "image"]
    assert envoyes["model"] == settings.visuels_model
    assert [v.sujet for v in verdicts] == ["scene", "autre"]


# --- Plancher de resolution du fond (2026-09-21) ----------------------------
# Mesure sur les 6 derniers posts publies (CLAUDE.md) : nets a 1,05x et 0,89x
# d'agrandissement, flous a partir de 2,13x. `cover_fond_upscale_max` (2,2x par
# defaut, la meme valeur et la meme famille de raisonnement que
# `publication_crop_upscale_max`) separe le cas nominal des fonds minuscules.

CIBLE_POST = (1080, 1350)
CIBLE_REEL = (1080, 1920)


def test_resolution_suffisante_sur_les_cas_reellement_mesures():
    """564x345 agrandi 3,91x (post 71, flou averred) ; 1920x1280 agrandi 1,05x
    (post 65, net)."""
    assert resolution_suffisante((564, 345), CIBLE_POST) is False
    assert resolution_suffisante((1920, 1280), CIBLE_POST) is True


def test_l_og_image_standard_reste_admise_en_fond():
    """LE cas nominal du pipeline : une og:image 1200x630 demande 2,14x pour
    remplir 1080x1350. Un seuil a 1,5x la rejetait, ce qui envoyait la majorite
    des posts de presse sur l'aplat Kinda -- et, quand les slides etaient par
    ailleurs ecartees par `publication_crop_upscale_max`, jusqu'a l'exclusion
    definitive de l'article (`PreparationImpossible` -> `file_exclu_raison`).
    Meme arbitrage que le garde-fou voisin : jamais degrader le cas nominal
    pour attraper un cas limite."""
    assert resolution_suffisante((1200, 630), CIBLE_POST) is True
    assert resolution_suffisante((1920, 1080), CIBLE_REEL) is True


def test_un_fond_a_un_centieme_du_seuil_reste_admis_sciemment():
    """Le fond du post 62 (720x900 pour un reel, 2,13x) passe, a un centieme du
    seuil : assume, un fond un peu mou vaut mieux qu'un aplat Kinda ou qu'un
    article exclu. Les trois autres fonds flous mesures restent attrapes."""
    assert resolution_suffisante((720, 900), CIBLE_REEL) is True
    assert resolution_suffisante((1005, 565), CIBLE_POST) is False  # 2,39x
    assert resolution_suffisante((564, 384), CIBLE_POST) is False  # 3,52x


def test_resolution_suffisante_est_indifferente_a_une_taille_inconnue():
    """Ne pas savoir vaut mieux que rejeter a tort : l'alternative est parfois
    le fond Kinda, jamais une meilleure information."""
    assert resolution_suffisante(None, CIBLE_POST) is True


def test_resolution_suffisante_ignore_une_taille_aberrante():
    assert resolution_suffisante((0, 0), CIBLE_POST) is True


def test_verdicts_de_memorise_la_taille_reelle_sans_requete_de_plus(tmp_path):
    """La taille est mesuree AU MEME MOMENT que le juge decode l'image pour la
    reduire (`reduire`) -- aucun telechargement de plus."""
    chemin = tmp_path / "c.json"

    def juge(images):
        return [Verdict(affiche=False, sujet="scene", note=80)]

    verdicts_de([SCENE], juge=juge, maintenant=MAINTENANT, chemin_cache=chemin)

    assert tailles_de([SCENE], maintenant=MAINTENANT, chemin_cache=chemin) == {SCENE: (40, 50)}


def test_une_taille_deja_en_cache_ne_repaie_rien(tmp_path):
    chemin = tmp_path / "c.json"

    def juge(images):
        return [Verdict(affiche=False, sujet="scene", note=80)]

    verdicts_de([SCENE], juge=juge, maintenant=MAINTENANT, chemin_cache=chemin)

    def juge_interdit(images):  # pragma: no cover
        raise AssertionError("le juge a ete rappele alors que le cache repondait")

    verdicts_de([SCENE], juge=juge_interdit, maintenant=MAINTENANT, chemin_cache=chemin)
    assert tailles_de([SCENE], maintenant=MAINTENANT, chemin_cache=chemin) == {SCENE: (40, 50)}


def _verdicts_fixes(table: dict, cible_appels: list | None = None):
    def rendre(urls):
        if cible_appels is not None:
            cible_appels.append(list(urls))
        return {url: table[url] for url in urls if url in table}

    return rendre


def _tailles_fixes(table: dict):
    def mesurer(urls):
        return {url: table[url] for url in urls if url in table}

    return mesurer


def test_un_fond_trop_petit_part_agrandissable_sans_reserve():
    """Le cas mesure du post 71 : une seule og:image, trop petite pour remplir
    le gabarit, aucune reserve pour la remplacer -- mais assez grande
    (345 px de cote mini >= `visuels_upscale_source_min_px`) pour que la
    Tache 6 la retienne plutot que de sauter direct sur le fond Kinda : le
    GEL tentera l'agrandissement."""
    fond = eviter_l_affiche(
        "https://cdn.test/petite.jpg",
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({"https://cdn.test/petite.jpg": Verdict(
            affiche=False, sujet="scene", note=70
        )}),
        tailles=_tailles_fixes({"https://cdn.test/petite.jpg": (564, 345)}),
    )

    assert fond == Fond(
        "https://cdn.test/petite.jpg", ORIGINE_ARTICLE, a_agrandir=True
    )
    # `a_agrandir` est `compare=False` sur `Fond` (bagage, comme `photos`) :
    # le `==` ci-dessus ne le verifie PAS, d'ou cette assertion explicite
    # (revue, tour de correction 2/5).
    assert fond.a_agrandir is True


def test_un_fond_trop_petit_pour_meme_etre_agrandi_part_sur_le_fond_kinda():
    """Sous `visuels_upscale_source_min_px` (230), meme le facteur 4 ne
    repasserait pas le plancher : agrandir ne servirait a rien, direct sur
    le fond Kinda -- signal interne du saut de la Tache 5."""
    fond = eviter_l_affiche(
        "https://cdn.test/minuscule.jpg",
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({"https://cdn.test/minuscule.jpg": Verdict(
            affiche=False, sujet="scene", note=70
        )}),
        tailles=_tailles_fixes({"https://cdn.test/minuscule.jpg": (200, 150)}),
    )

    assert fond == Fond(FOND_KINDA, ORIGINE_KINDA)


def test_le_seuil_de_230px_sauve_des_sources_qu_un_seuil_de_300_aurait_ecartees():
    """Revue, tour de correction 1/5 : le premier seuil (300 px) demontrait
    l'inverse de sa propre conclusion et ecartait a tort les sources entre
    219 (vrai seuil structurel pour le reel) et 299 px. 250 px, entre les
    deux, doit desormais etre retenu pour l'agrandissement."""
    fond = eviter_l_affiche(
        "https://cdn.test/moyenne.jpg",
        cible=CIBLE_REEL,
        verdicts=_verdicts_fixes({"https://cdn.test/moyenne.jpg": Verdict(
            affiche=False, sujet="scene", note=70
        )}),
        tailles=_tailles_fixes({"https://cdn.test/moyenne.jpg": (400, 250)}),
    )

    assert fond == Fond("https://cdn.test/moyenne.jpg", ORIGINE_ARTICLE, a_agrandir=True)
    # `a_agrandir` est `compare=False` : assertion explicite (tour de
    # correction 2/5), voir `test_un_fond_trop_petit_part_agrandissable_
    # sans_reserve`.
    assert fond.a_agrandir is True


def test_un_candidat_de_taille_inconnue_n_est_jamais_retenu_pour_l_agrandissement():
    """Regle du doute INVERSEE ici (a la difference du reste du module) : ne
    pas savoir la taille source ne rend pas un candidat agrandissable --
    l'appelant tenterait l'outil en vain sur un candidat qui a en realite
    echoue a se faire mesurer. Teste `_meilleur_agrandissable` directement :
    en passant par `eviter_l_affiche`, un candidat de taille INCONNUE est de
    toute facon deja accepte plus tot par la regle du doute normale de ce
    module (`resolution_suffisante(None, cible)` vaut True), donc ce chemin
    n'est jamais atteint en pratique -- cette fonction reste un filet."""
    meilleur = illustration._meilleur_agrandissable(
        ["https://cdn.test/taille-inconnue.jpg"],
        _verdicts_fixes({"https://cdn.test/taille-inconnue.jpg": Verdict(
            affiche=False, sujet="scene", note=70
        )}),
        CIBLE_POST,
        _tailles_fixes({}),
    )

    assert meilleur is None


def test_le_coupe_circuit_desactive_le_barreau_d_agrandissement(monkeypatch):
    """`visuels_upscale_enabled=False` retombe sur le comportement de la
    Tache 5 (signal interne du saut), meme pour un candidat par ailleurs
    agrandissable."""
    monkeypatch.setattr(settings, "visuels_upscale_enabled", False)
    fond = eviter_l_affiche(
        "https://cdn.test/petite.jpg",
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({"https://cdn.test/petite.jpg": Verdict(
            affiche=False, sujet="scene", note=70
        )}),
        tailles=_tailles_fixes({"https://cdn.test/petite.jpg": (564, 345)}),
    )

    assert fond == Fond(FOND_KINDA, ORIGINE_KINDA)


def test_un_fond_trop_petit_descend_d_un_barreau_vers_la_reserve():
    """Exactement le meme traitement qu'une affiche averee : on cherche une
    photo plus grande dans la reserve avant de se rabattre sur le fond Kinda."""
    fond = eviter_l_affiche(
        "https://cdn.test/petite.jpg",
        Lot(ORIGINE_COMPTE, lambda: ["file:///cache/grande.jpg"]),
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/petite.jpg": Verdict(affiche=False, sujet="scene", note=70),
            "file:///cache/grande.jpg": Verdict(affiche=False, sujet="scene", note=80),
        }),
        tailles=_tailles_fixes({
            "https://cdn.test/petite.jpg": (564, 345),
            "file:///cache/grande.jpg": (1920, 1280),
        }),
    )

    assert fond == Fond("file:///cache/grande.jpg", ORIGINE_COMPTE, ("file:///cache/grande.jpg",))


def test_une_reserve_dont_toutes_les_photos_sont_trop_petites_retient_la_mieux_notee():
    """Aucune photo ne passe le plancher, mais les deux restent assez grandes
    pour l'agrandissement (Tache 6) : la mieux notee de tout ce que l'echelle
    a examine gagne, pas necessairement le fond d'origine -- avec la VRAIE
    origine (`ORIGINE_COMPTE`, celle du lot d'ou vient le gagnant, PAS
    `ORIGINE_ARTICLE` par defaut : defaut le plus grave signale en revue,
    tour de correction 1/5 -- un fond venu d'une reserve partait alors sans
    le credit `📸 @compte`) et ses photos de slide (`Fond.photos`)."""
    fond = eviter_l_affiche(
        "https://cdn.test/petite.jpg",
        Lot(ORIGINE_COMPTE, lambda: ["file:///cache/aussi-petite.jpg"]),
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/petite.jpg": Verdict(affiche=False, sujet="scene", note=70),
            "file:///cache/aussi-petite.jpg": Verdict(affiche=False, sujet="scene", note=80),
        }),
        tailles=_tailles_fixes({
            "https://cdn.test/petite.jpg": (564, 345),
            "file:///cache/aussi-petite.jpg": (600, 400),
        }),
    )

    assert fond == Fond(
        "file:///cache/aussi-petite.jpg", ORIGINE_COMPTE,
        ("file:///cache/aussi-petite.jpg",), a_agrandir=True,
    )
    assert fond.origine == ORIGINE_COMPTE
    assert fond.photos == ("file:///cache/aussi-petite.jpg",)


def test_un_candidat_agrandissable_d_une_reserve_garde_tout_son_cortege_de_photos():
    """Un lot a PLUSIEURS photos, toutes trop petites : le candidat retenu
    pour l'agrandissement garde le cortege ENTIER du lot (pour
    `actifs_de_secours`, qui en fait des slides), le gagnant place en tete --
    le meme invariant que le chemin nominal (`_meilleur_du_lot`)."""
    fond = eviter_l_affiche(
        "https://cdn.test/petite.jpg",
        Lot(ORIGINE_ARTISTE, lambda: [
            "file:///cache/artiste-1.jpg", "file:///cache/artiste-2.jpg",
        ]),
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/petite.jpg": Verdict(affiche=False, sujet="scene", note=70),
            "file:///cache/artiste-1.jpg": Verdict(affiche=False, sujet="scene", note=60),
            "file:///cache/artiste-2.jpg": Verdict(affiche=False, sujet="portrait", note=90),
        }),
        tailles=_tailles_fixes({
            "https://cdn.test/petite.jpg": (564, 345),
            "file:///cache/artiste-1.jpg": (500, 400),
            "file:///cache/artiste-2.jpg": (600, 450),
        }),
    )

    # artiste-2 (note 90) bat artiste-1 (note 60) ET le fond d'origine (70).
    assert fond.url == "file:///cache/artiste-2.jpg"
    assert fond.origine == ORIGINE_ARTISTE
    assert fond.a_agrandir is True
    assert fond.photos == ("file:///cache/artiste-2.jpg", "file:///cache/artiste-1.jpg")


def test_une_affiche_en_fond_n_empeche_plus_le_barreau_d_agrandissement():
    """Defaut le plus visible signale en revue (tour de correction 1/5) :
    une garde `not est_affiche` fermait ENTIEREMENT le barreau des que le
    fond d'origine etait une affiche, meme quand un AUTRE candidat non-
    affiche restait eligible -- exactement le cas Transmusicales (affiche de
    line-up en fond, photo d'artiste a cote) qui a motive toute l'echelle de
    secours. `_meilleur_agrandissable` filtre deja les affiches connues :
    la garde en plus n'ajoutait rien, elle amputait."""
    fond = eviter_l_affiche(
        "https://cdn.test/affiche-lineup.jpg",  # grande, mais AFFICHE
        Lot(ORIGINE_ARTISTE, lambda: ["file:///cache/artiste.jpg"]),
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/affiche-lineup.jpg": Verdict(affiche=True, sujet="autre", note=10),
            "file:///cache/artiste.jpg": Verdict(affiche=False, sujet="portrait", note=75),
        }),
        tailles=_tailles_fixes({
            "https://cdn.test/affiche-lineup.jpg": (2000, 2000),
            "file:///cache/artiste.jpg": (600, 400),  # trop petit, mais agrandissable
        }),
    )

    assert fond.url == "file:///cache/artiste.jpg"
    assert fond.origine == ORIGINE_ARTISTE
    assert fond.a_agrandir is True
    assert fond != Fond(FOND_KINDA, ORIGINE_KINDA)


# --- I-1 (tour de correction 1/5) : panne de transport != rien trouve ------


def test_une_reserve_en_panne_de_transport_reporte_au_lieu_de_conclure():
    """Une reserve qui n'a PAS PU regarder (quota, reseau) ne doit jamais etre
    confondue avec une reserve qui a regarde et n'a rien trouve : la premiere
    doit faire REPORTER (`Fond.differe`), la seconde fait descendre vers
    `FOND_KINDA`. Avant cette distinction, les deux donnaient le meme aplat --
    sur, tant qu'il restait publiable ; devenu un saut (Tache 5), la panne
    aurait fait sauter l'article a tort."""
    def panne():
        raise VisuelDiffere("quota Graph atteint")

    fond = eviter_l_affiche(
        "https://cdn.test/petite.jpg",
        Lot(ORIGINE_COMPTE, panne),
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/petite.jpg": Verdict(affiche=False, sujet="scene", note=70),
        }),
        tailles=_tailles_fixes({"https://cdn.test/petite.jpg": (564, 345)}),
    )

    assert fond.differe is True
    # Tour de correction 2/5, point 3 : `url` ne doit JAMAIS porter l'image
    # jugee affiche/trop petite -- surete par construction, pas seulement par
    # le fait que `resoudre_fond` teste `differe` en premier.
    assert fond.url is None
    assert fond.origine != ORIGINE_KINDA


def test_un_lot_plus_loin_qui_reussit_efface_la_panne_d_un_lot_precedent():
    """Si le PREMIER lot ne peut pas regarder mais qu'un lot SUIVANT donne une
    vraie photo, la panne n'a aucune consequence -- le premier qui reussit
    gagne, exactement comme sans aucune panne."""
    def panne():
        raise VisuelDiffere("quota Graph atteint")

    fond = eviter_l_affiche(
        "https://cdn.test/petite.jpg",
        Lot(ORIGINE_COMPTE, panne),
        Lot(ORIGINE_WEB, lambda: ["file:///cache/grande.jpg"]),
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/petite.jpg": Verdict(affiche=False, sujet="scene", note=70),
            "file:///cache/grande.jpg": Verdict(affiche=False, sujet="scene", note=80),
        }),
        tailles=_tailles_fixes({
            "https://cdn.test/petite.jpg": (564, 345),
            "file:///cache/grande.jpg": (1920, 1280),
        }),
    )

    assert fond == Fond("file:///cache/grande.jpg", ORIGINE_WEB, ("file:///cache/grande.jpg",))
    assert fond.differe is False


def test_la_reserve_choisit_la_premiere_photo_assez_grande_et_la_place_en_tete():
    """La reserve classe d'abord par affiche/sujet/note ; le plancher de
    resolution ne fait que sauter les candidats trop petits, sans changer le
    classement des autres. La photo retenue reste EN TETE de `Fond.photos`."""
    fond = eviter_l_affiche(
        "https://cdn.test/petite.jpg",
        Lot(ORIGINE_COMPTE, lambda: [
            "file:///cache/petite2.jpg", "file:///cache/grande.jpg", "file:///cache/autre.jpg",
        ]),
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/petite.jpg": Verdict(affiche=False, sujet="scene", note=70),
            "file:///cache/petite2.jpg": Verdict(affiche=False, sujet="scene", note=95),
            "file:///cache/grande.jpg": Verdict(affiche=False, sujet="scene", note=80),
            "file:///cache/autre.jpg": Verdict(affiche=False, sujet="scene", note=60),
        }),
        tailles=_tailles_fixes({
            "https://cdn.test/petite.jpg": (564, 345),
            "file:///cache/petite2.jpg": (600, 400),
            "file:///cache/grande.jpg": (1920, 1280),
            "file:///cache/autre.jpg": (1920, 1280),
        }),
    )

    assert fond.url == "file:///cache/grande.jpg"
    assert fond.photos[0] == "file:///cache/grande.jpg"
    assert set(fond.photos) == {
        "file:///cache/petite2.jpg", "file:///cache/grande.jpg", "file:///cache/autre.jpg",
    }


def test_une_taille_de_fond_inconnue_est_gardee():
    """Meme regle du doute que le juge d'affiches : ne pas savoir mesurer n'est
    pas savoir que le fond est trop petit."""
    fond = eviter_l_affiche(
        "https://cdn.test/inconnue.jpg",
        Lot(ORIGINE_COMPTE, lambda: ["file:///cache/grande.jpg"]),
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/inconnue.jpg": Verdict(affiche=False, sujet="scene", note=70),
        }),
        tailles=_tailles_fixes({}),
    )

    assert fond == Fond("https://cdn.test/inconnue.jpg", ORIGINE_ARTICLE)


def test_une_bonne_resolution_n_ouvre_aucune_reserve():
    ouverts = []

    def photos():
        ouverts.append(ORIGINE_COMPTE)
        return ["file:///cache/grande.jpg"]

    fond = eviter_l_affiche(
        "https://cdn.test/grande.jpg",
        Lot(ORIGINE_COMPTE, photos),
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/grande.jpg": Verdict(affiche=False, sujet="scene", note=70),
        }),
        tailles=_tailles_fixes({"https://cdn.test/grande.jpg": (1920, 1280)}),
    )

    assert fond == Fond("https://cdn.test/grande.jpg", ORIGINE_ARTICLE)
    assert ouverts == []


def test_sans_cible_le_plancher_de_resolution_est_desactive():
    """Comportement d'avant cette regle : les appelants qui ne fournissent pas
    `cible` (compositions anterieures) ne subissent aucune verification de
    resolution."""
    fond = eviter_l_affiche(
        "https://cdn.test/petite.jpg",
        verdicts=_verdicts_fixes({
            "https://cdn.test/petite.jpg": Verdict(affiche=False, sujet="scene", note=70),
        }),
        tailles=_tailles_fixes({"https://cdn.test/petite.jpg": (10, 10)}),
    )

    assert fond == Fond("https://cdn.test/petite.jpg", ORIGINE_ARTICLE)


def test_une_affiche_averee_reste_rejetee_meme_assez_grande():
    """Les deux defauts sont independants : une grande affiche reste une
    affiche."""
    fond = eviter_l_affiche(
        "https://cdn.test/grande-affiche.jpg",
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/grande-affiche.jpg": Verdict(affiche=True, sujet="autre", note=10),
        }),
        tailles=_tailles_fixes({"https://cdn.test/grande-affiche.jpg": (1920, 1280)}),
    )

    assert fond == Fond(FOND_KINDA, ORIGINE_KINDA)


def test_le_reel_est_plus_exigeant_que_le_post_sur_la_meme_photo():
    """La cible depend du gabarit publie : 1080x800 suffit pour un post
    (1350/800 = 1,69x) et manque pour un reel (1920/800 = 2,4x), au-dela du
    seuil commun de 2,2x -- mais 800 px de cote mini reste assez grand pour
    que la Tache 6 le retienne comme agrandissable plutot que de sauter."""
    verdicts = _verdicts_fixes({
        "https://cdn.test/x.jpg": Verdict(affiche=False, sujet="scene", note=70),
    })
    tailles = _tailles_fixes({"https://cdn.test/x.jpg": (1080, 800)})

    assert eviter_l_affiche(
        "https://cdn.test/x.jpg", cible=CIBLE_POST, verdicts=verdicts, tailles=tailles,
    ) == Fond("https://cdn.test/x.jpg", ORIGINE_ARTICLE)
    assert eviter_l_affiche(
        "https://cdn.test/x.jpg", cible=CIBLE_REEL, verdicts=verdicts, tailles=tailles,
    ) == Fond("https://cdn.test/x.jpg", ORIGINE_ARTICLE, a_agrandir=True)


def test_une_panne_de_mesure_ne_leve_pas_et_garde_le_fond():
    def mesurer_casse(urls):
        raise RuntimeError("disque plein")

    fond = eviter_l_affiche(
        "https://cdn.test/x.jpg",
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            "https://cdn.test/x.jpg": Verdict(affiche=False, sujet="scene", note=70),
        }),
        tailles=mesurer_casse,
    )

    assert fond == Fond("https://cdn.test/x.jpg", ORIGINE_ARTICLE)


# --- Le barreau 1 balaye ses propres candidats par taille (revue I-4) ------
# Les taches 2 (plancher de nettete) et 4 (visuels de tout le groupe de
# doublons) ne se composaient pas : `choisir_illustration` ne classe que sur le
# verdict affiche/photo, et un fond trop petit descendait directement vers les
# reserves sans essayer les autres photos du MEME groupe. La grande photo
# apportee par un doublon restait en slide, jamais en fond.

PETITE = "https://stereogum.example/og.png?w=564"
GRANDE = "https://presse.example/grande.jpg"


def test_un_fond_trop_petit_cede_la_place_a_une_grande_photo_du_meme_barreau():
    ouverts = []

    def lot():
        ouverts.append("compte")
        return ["file:///cache/compte.jpg"]

    fond = eviter_l_affiche(
        PETITE,
        Lot(ORIGINE_COMPTE, lot),
        candidats=[PETITE, GRANDE],
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            PETITE: Verdict(affiche=False, sujet="portrait", note=80),
            GRANDE: Verdict(affiche=False, sujet="scene", note=60),
            "file:///cache/compte.jpg": Verdict(affiche=False, sujet="scene", note=90),
        }),
        tailles=_tailles_fixes({PETITE: (564, 345), GRANDE: (2156, 1320),
                                "file:///cache/compte.jpg": (1080, 1350)}),
    )

    assert fond == Fond(GRANDE, ORIGINE_ARTICLE)
    assert ouverts == [], "le barreau suivant n'est ouvert que si le premier n'a rien"


def test_le_balayage_n_elit_jamais_une_affiche_meme_grande():
    """GRANDE est plus grande ET une affiche averee : le barreau
    d'agrandissement (Tache 6) ne la choisit jamais pour autant -- PETITE,
    non-affiche et assez grande pour etre agrandie, gagne a sa place."""
    fond = eviter_l_affiche(
        PETITE,
        candidats=[PETITE, GRANDE],
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({
            PETITE: Verdict(affiche=False, sujet="portrait", note=80),
            GRANDE: Verdict(affiche=True, sujet="autre", note=10),
        }),
        tailles=_tailles_fixes({PETITE: (564, 345), GRANDE: (2156, 1320)}),
    )

    assert fond == Fond(PETITE, ORIGINE_ARTICLE, a_agrandir=True)


def test_le_balayage_rend_l_origine_du_candidat_retenu():
    """Le fond remplacant peut venir d'un actif d'une autre origine que le fond
    elu (une photo d'artiste cite a cote de la photo de l'actu) : son origine
    est relue sur lui, pas heritee."""
    fond = eviter_l_affiche(
        PETITE,
        candidats=[PETITE, GRANDE],
        origine_de={PETITE: ORIGINE_ARTICLE, GRANDE: "artiste"}.get,
        cible=CIBLE_POST,
        verdicts=_verdicts_fixes({GRANDE: Verdict(affiche=False, sujet="scene", note=60)}),
        tailles=_tailles_fixes({PETITE: (564, 345), GRANDE: (2156, 1320)}),
    )

    assert fond == Fond(GRANDE, "artiste")


# --- Un visuel deja couvert de texte n'est pas un fond ---------------------

STORY = "https://exemple.test/story-legendee.jpg"
VERDICT_STORY = Verdict(affiche=False, texte_incruste=True, sujet="scene", note=70)


def test_une_photo_legendee_perd_contre_une_photo_nue(tmp_path):
    """Post du 28/09 (Dimitri Vegas & Like Mike) : le fond etait une frame de
    story portant sa legende (« Much love to that one person watching my show on
    FaceTime... »), et l'accroche Kinda venait par-dessus -- deux textes sur la
    meme cover. Le juge disait `affiche: false` a juste titre : c'EST une
    photographie. Seul `texte_incruste` la distingue."""
    verdicts = {STORY: VERDICT_STORY, SCENE: VERDICTS[SCENE]}
    candidats = [STORY, SCENE]

    choisi = choisir_illustration(
        candidats,
        juge=lambda images: [verdicts[url] for url in candidats],
        maintenant=MAINTENANT,
        chemin_cache=tmp_path / "c.json",
    )

    assert choisi == SCENE


def test_une_photo_legendee_descend_l_echelle_comme_une_affiche(tmp_path):
    """Le defaut est le meme (un texte qui concurrence l'accroche), la reponse
    doit donc etre la meme : ouvrir les reserves."""
    fond = eviter_l_affiche(
        STORY,
        Lot(ORIGINE_COMPTE, lambda: [SCENE]),
        verdicts=lambda urls: {
            url: {STORY: VERDICT_STORY, SCENE: VERDICTS[SCENE]}[url] for url in urls
        },
    )

    assert fond == Fond(SCENE, ORIGINE_COMPTE)


def test_sans_reserve_une_photo_legendee_ne_reste_pas_le_fond(tmp_path):
    """Sans remplacant, on part sur le fond Kinda -- que `compose.resoudre_fond`
    traduit en saut du creneau. Jamais la photo legendee."""
    fond = eviter_l_affiche(
        STORY,
        Lot(ORIGINE_COMPTE, lambda: []),
        verdicts=lambda urls: {STORY: VERDICT_STORY},
    )

    assert fond == Fond(FOND_KINDA, ORIGINE_KINDA)


def test_le_texte_dans_la_scene_ne_disqualifie_rien(tmp_path):
    """Le piege du critere : presque toute photo porte un peu de texte (une
    enseigne, un t-shirt, une banderole « FREE PARTY IS NOT A CRIME »). Le juge
    repond `texte_incruste: false` pour tout cela, et rien ne doit alors bouger
    -- sinon le critere viderait les covers."""
    fond = eviter_l_affiche(
        SCENE,
        Lot(ORIGINE_COMPTE, lambda: [PORTRAIT]),
        verdicts=lambda urls: {SCENE: VERDICTS[SCENE]},
    )

    assert fond == Fond(SCENE, ORIGINE_ARTICLE)


def test_une_photo_legendee_reste_une_slide_honnete(tmp_path):
    """`plafonner_affiches` ne lit PAS `texte_incruste` : sous l'accroche la
    frame de story est fautive, dans le carrousel elle montre l'actu."""
    autre_story = "https://exemple.test/story-2.jpg"
    actifs = [
        {"type": "photo", "url": STORY},
        {"type": "photo", "url": autre_story},
        {"type": "photo", "url": SCENE},
    ]

    gardes = illustration.plafonner_affiches(
        actifs,
        verdicts=lambda urls: {
            STORY: VERDICT_STORY,
            autre_story: VERDICT_STORY,
            SCENE: VERDICTS[SCENE],
        },
    )

    # DEUX frames legendees, et le plafond (une affiche par carrousel) ne les
    # compte pas : il ne lit que `affiche`.
    assert gardes == actifs


def test_un_verdict_sans_le_champ_vaut_pas_de_texte_incruste():
    """La regle du doute du module : un verdict construit sans ce champ (code
    ancien, cache ecrit a la main) ne doit jamais faire sauter une image."""
    assert Verdict(affiche=False, sujet="scene", note=70).inexploitable_en_fond is False


def test_le_schema_envoye_au_juge_exige_le_champ():
    """Test-sentinelle. `texte_incruste` porte un defaut cote Python, et une
    valeur par defaut sort le champ du `required` du schema JSON : le modele
    pourrait l'omettre, le critere serait INERTE et le defaut du 28/09
    repasserait en silence. `Verdicts.model_json_schema` l'y remet."""
    schema = illustration.Verdicts.model_json_schema()

    assert "texte_incruste" in schema["$defs"]["Verdict"]["required"]


def test_les_verdicts_d_un_cache_d_avant_le_critere_sont_jetes(tmp_path):
    """Un verdict pose en version 1 ne dit rien de `texte_incruste` : le relire
    comme « pas de texte » laisserait passer le defaut pendant une semaine. Les
    TAILLES, elles, ne dependent d'aucun critere du juge et sont gardees."""
    chemin = tmp_path / "c.json"
    chemin.write_text(
        json.dumps(
            {
                "version": 1,
                "verdicts": {
                    SCENE: {
                        "affiche": False,
                        "sujet": "scene",
                        "note": 80,
                        "le": MAINTENANT.isoformat(),
                    }
                },
                "tailles": {
                    SCENE: {"largeur": 1600, "hauteur": 900, "le": MAINTENANT.isoformat()}
                },
            }
        ),
        encoding="utf-8",
    )

    cache = lire_cache(MAINTENANT, chemin)

    assert cache.verdicts == {}
    assert cache.tailles == {SCENE: (1600, 900)}
