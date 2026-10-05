"""Le gel : ce qui partira chez Meta, fige sur disque avant l'heure."""

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.content.render import RenduIndisponible, RenduSlide
from app.content.upscale import UpscaleIndisponible
from app.models import SourceCategorie, SourceType
from app.content.visuels import ORIGINE_ARTICLE, actif
from app.publish.preparation import (
    PreparationDifferee,
    PreparationImpossible,
    PreparationSautee,
    medias_du_carrousel,
    verifier_fond,
)
from app.publish.preparation import preparer_fichiers as _figer


def preparer_fichiers(*args, **kwargs):
    """Les NOMS des fichiers figes seuls.

    `preparation.preparer_fichiers` rend desormais un `Figement` : les noms ET le
    manifeste de ce qui part (2026-10-04). Ces tests-ci portent sur le CONTENU du
    gel, et ce desemballage les garde lisibles ; le manifeste a ses propres tests,
    dans `tests/publish/test_manifeste_gel.py`.
    """
    return _figer(*args, **kwargs).noms


def _png(largeur, hauteur):
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (10, 20, 30)).save(tampon, "PNG")
    return tampon.getvalue()


def _rendre(overflow=False):
    def rendre(slides, template, palette_=None):
        rendre.appels.append((slides, template))
        return [RenduSlide(png=_png(1080, 1350), scale=1.0, lines={}, overflow=overflow)]

    rendre.appels = []
    return rendre


def _post(*medias):
    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": "https://img/fond.jpg"}
    return SimpleNamespace(slides=[cover, *medias])


def _article(*urls):
    return SimpleNamespace(media=[{"type": "photo", "url": url} for url in urls])


def _telecharger(echecs=()):
    def telecharger(url):
        if url in echecs:
            return None, "HTTP 404"
        return _png(1200, 800), ".png"

    return telecharger


def test_la_cover_puis_les_photos_en_jpeg(tmp_path):
    rendre = _rendre()
    noms = preparer_fichiers(
        _post(), _article("https://img/1.jpg", "https://img/2.jpg"), tmp_path,
        rendre=rendre, telecharger=_telecharger(),
    )
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg", "03.jpg"]
    for nom in noms:
        assert (tmp_path / nom).read_bytes()[:2] == b"\xff\xd8"
    assert Image.open(tmp_path / noms[0]).size == (1080, 1350)
    assert rendre.appels[0][1] == "kinda_post"


def test_une_photo_qui_ne_se_telecharge_pas_est_ignoree(tmp_path):
    noms = preparer_fichiers(
        _post(), _article("https://img/1.jpg", "https://img/2.jpg"), tmp_path,
        rendre=_rendre(), telecharger=_telecharger(echecs=("https://img/1.jpg",)),
    )
    assert len(noms) == 2


def test_une_cover_qui_deborde_n_est_jamais_publiee(tmp_path):
    with pytest.raises(PreparationImpossible, match="deborde"):
        preparer_fichiers(_post(), None, tmp_path, rendre=_rendre(overflow=True), telecharger=_telecharger())


def test_chromium_absent_n_est_pas_une_preparation_impossible(tmp_path):
    # Panne systemique : elle doit remonter telle quelle, sinon le runner
    # exclurait les articles un par un pour une faute qui n'est pas la leur.
    def rendre(slides, template, palette_=None):
        raise RenduIndisponible("Chromium Playwright est absent")

    with pytest.raises(RenduIndisponible, match="Chromium"):
        preparer_fichiers(_post(), None, tmp_path, rendre=rendre, telecharger=_telecharger())


def test_dix_images_au_plus(tmp_path):
    urls = [f"https://img/{i}.jpg" for i in range(15)]
    noms = preparer_fichiers(_post(), _article(*urls), tmp_path, rendre=_rendre(), telecharger=_telecharger())
    assert len(noms) == 10


def test_un_nouveau_gel_efface_le_precedent(tmp_path):
    (tmp_path / "ancien_01.jpg").write_bytes(b"x")
    preparer_fichiers(_post(), _article("https://img/1.jpg"), tmp_path,
                      rendre=_rendre(), telecharger=_telecharger())
    assert not (tmp_path / "ancien_01.jpg").exists()


def _post_kinda(*medias):
    from app.content.visuels import FOND_KINDA

    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": FOND_KINDA}
    return SimpleNamespace(slides=[cover, *medias])


def test_une_cover_sur_l_aplat_kinda_sans_rien_derriere_n_est_jamais_publiee(tmp_path):
    """Article 17946 (Noisia) : une seule image, ecartee, et un post parti avec
    la cover seule SUR L'APLAT -- « la c'est n'importe quoi ! ». Ce post ne
    montre aucune image de l'actu : mieux vaut sauter l'article."""
    with pytest.raises(PreparationImpossible, match="cover seule"):
        preparer_fichiers(
            _post_kinda(), _article("https://img/1.jpg"), tmp_path,
            rendre=_rendre(), telecharger=_telecharger(echecs=("https://img/1.jpg",)),
        )


def test_la_seule_carte_d_annonce_restante_fait_toujours_un_post(tmp_path):
    """Le plafond d'une affiche par carrousel (cf. `illustration.AFFICHES_MAX`)
    ne doit jamais faire tomber un post sous deux elements : quand l'unique
    visuel de l'actu est une carte d'annonce, elle reste et la cover part sur
    l'aplat Kinda -- cover + carte, le post est publiable."""
    noms = preparer_fichiers(
        _post_kinda(), _article("https://img/prog.jpg"), tmp_path,
        rendre=_rendre(), telecharger=_telecharger(),
    )
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg"]


def test_une_panne_reseau_differe_le_gel_au_lieu_d_exclure(tmp_path):
    """Un article exclu ne revient jamais dans la file : une coupure de deux
    minutes ne doit pas lui couter sa publication. On retente au passage
    suivant."""
    from app.publish.preparation import PreparationDifferee

    def telecharger(url):
        return None, "reseau: ConnectError"

    with pytest.raises(PreparationDifferee, match="reessaie|nouvelle tentative"):
        preparer_fichiers(_post_kinda(), _article("https://img/1.jpg"), tmp_path,
                          rendre=_rendre(), telecharger=telecharger)


def test_un_post_sans_aucun_media_n_est_jamais_publie(tmp_path):
    with pytest.raises(PreparationImpossible, match="cover seule"):
        preparer_fichiers(_post_kinda(), _article(), tmp_path,
                          rendre=_rendre(), telecharger=_telecharger())


def test_l_unique_image_reste_le_fond_et_le_post_part_avec_la_cover_seule(tmp_path):
    """2026-09-19, post « Fred again.. au Palacio » : la slide 2 etait la photo
    de la cover en version nette -- « ca vaut pas le coup, on la voit bien assez
    sur la premiere slide ». Un seul fichier fige, donc un post IMAGE simple
    chez Meta (`instagram.preparer_carrousel`), et rien de repete."""
    telecharges = []

    def telecharger(url):
        telecharges.append(url)
        return _png(1200, 800), ".png"

    noms = preparer_fichiers(
        _post(), _article("https://img/fond.jpg"), tmp_path,
        rendre=_rendre(), telecharger=telecharger,
    )
    assert [nom[-6:] for nom in noms] == ["01.jpg"]
    assert telecharges == []
    assert Image.open(tmp_path / noms[0]).size == (1080, 1350)


def test_un_article_sans_aucun_media_mais_une_cover_illustree_est_publiable(tmp_path):
    """Le fond vient d'une reserve (compte source, artiste cite, web) et l'actu
    n'a rien d'autre : le post montre quand meme une image, il part."""
    noms = preparer_fichiers(
        _post(), _article(), tmp_path, rendre=_rendre(), telecharger=_telecharger(),
    )
    assert len(noms) == 1


def test_l_image_de_la_cover_n_est_pas_repetee_quand_une_autre_existe(tmp_path):
    telecharges = []

    def telecharger(url):
        telecharges.append(url)
        return _png(1200, 800), ".png"

    noms = preparer_fichiers(
        _post(), _article("https://img/fond.jpg", "https://img/autre.jpg"), tmp_path,
        rendre=_rendre(), telecharger=telecharger,
    )
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg"]
    assert telecharges == ["https://img/autre.jpg"]


def test_tous_les_autres_visuels_restent_des_slides(tmp_path):
    """Retirer l'image du fond ne doit appauvrir que LUI : les autres visuels de
    l'actu partent tous, dans l'ordre et au format publie."""
    telecharges = []

    def telecharger(url):
        telecharges.append(url)
        return _png(1200, 800), ".png"

    noms = preparer_fichiers(
        _post(),
        _article("https://img/a.jpg", "https://img/fond.jpg", "https://img/b.jpg", "https://img/c.jpg"),
        tmp_path, rendre=_rendre(), telecharger=telecharger,
    )
    assert len(noms) == 4
    assert telecharges == ["https://img/a.jpg", "https://img/b.jpg", "https://img/c.jpg"]
    for nom in noms:
        assert Image.open(tmp_path / nom).size == (1080, 1350)


def test_les_medias_viennent_des_slides_puis_de_l_article_sans_doublon():
    post = _post({"kind": "media", "type": "photo", "url": "https://img/a.jpg"},
                 {"kind": "media", "type": "video", "url": "https://img/v.mp4"})
    assert [m["url"] for m in medias_du_carrousel(post, _article("https://img/a.jpg", "https://img/b.jpg"))] == [
        "https://img/a.jpg", "https://img/v.mp4", "https://img/b.jpg",
    ]
    assert [m["type"] for m in medias_du_carrousel(post, None)] == ["photo", "video"]


# --- Videos et fond de cover ---------------------------------------------------------

SOURCE_OK = {"width": 1280, "height": 720, "duration": 10.0, "video_codec": "h264", "audio_codec": "aac"}
SORTIE_OK = {"width": 1080, "height": 1350, "duration": 10.0, "video_codec": "h264", "audio_codec": "aac"}


def _outils_video(echec_telechargement=False, infos_source=SOURCE_OK, echec_sonde=False):
    journal = []

    def telecharger_video(url, plafond):
        journal.append(("telecharger", url))
        if echec_telechargement:
            raise RuntimeError("HTTP 403")
        return b"source"

    def sonder(octets):
        if echec_sonde:
            raise RuntimeError("video illisible")
        return dict(SORTIE_OK if octets == b"sortie" else infos_source)

    def transcoder(octets):
        journal.append(("transcoder",))
        return b"sortie"

    return journal, {"telecharger_video": telecharger_video, "sonder": sonder, "transcoder": transcoder}


def _video(url="https://cdn/v.mp4", preview="https://cdn/v.jpg", duree_ms=10_000):
    return {"type": "video", "url": url, "preview_url": preview, "duration_ms": duree_ms}


def _article_medias(*medias, source=None):
    return SimpleNamespace(media=list(medias), source=source)


def _preparer(tmp_path, post, article, **outils):
    return preparer_fichiers(post, article, tmp_path, rendre=_rendre(), telecharger=_telecharger(), **outils)


def test_une_video_de_l_article_devient_un_element_video_du_carrousel(tmp_path):
    """Premier post reel (Charlotte de Witte, article 17839) : 5 videos, et un
    carrousel parti avec la cover seule."""
    journal, outils = _outils_video()
    article = _article_medias(_video(), {"type": "photo", "url": "https://img/p.jpg"})
    noms = _preparer(tmp_path, _post(), article, **outils)
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.mp4", "03.jpg"]
    assert (tmp_path / noms[1]).read_bytes() == b"sortie"
    assert journal == [("telecharger", "https://cdn/v.mp4"), ("transcoder",)]


def test_une_video_trop_longue_est_remplacee_par_sa_vignette(tmp_path):
    journal, outils = _outils_video()
    noms = _preparer(tmp_path, _post(), _article_medias(_video(duree_ms=75_000)), **outils)
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg"]
    assert journal == []


def test_une_video_trop_longue_a_la_sonde_est_remplacee_par_sa_vignette(tmp_path):
    _, outils = _outils_video(infos_source={**SOURCE_OK, "duration": 61.0})
    noms = _preparer(tmp_path, _post(), _article_medias(_video(duree_ms=None)), **outils)
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg"]


@pytest.mark.parametrize("panne", [{"echec_telechargement": True}, {"echec_sonde": True}])
def test_une_video_en_echec_retombe_sur_sa_vignette_sans_faire_echouer_le_gel(tmp_path, panne):
    _, outils = _outils_video(**panne)
    noms = _preparer(tmp_path, _post(), _article_medias(_video(), _video(url="https://cdn/w.mp4", preview=None)), **outils)
    # La premiere retombe sur sa vignette ; la seconde, sans vignette, est ignoree.
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg"]


def test_une_video_sans_lien_direct_donne_sa_vignette(tmp_path):
    journal, outils = _outils_video()
    noms = _preparer(tmp_path, _post(), _article_medias(_video(url=None)), **outils)
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg"]
    assert journal == []


def test_la_photo_du_fond_de_cover_n_est_pas_reprise_mais_la_video_oui():
    """Un clip n'est pas le meme asset que son image fixe : la video dont la
    vignette sert de fond reste dans le carrousel."""
    fond = "https://img/fond.jpg"
    article = _article_medias(
        {"type": "photo", "url": fond},
        _video(preview=fond),
        {"type": "photo", "url": "https://img/autre.jpg"},
    )
    assert [m["url"] for m in medias_du_carrousel(_post(), article)] == ["https://cdn/v.mp4", "https://img/autre.jpg"]


def test_une_video_qui_retomberait_sur_la_vignette_du_fond_laisse_la_cover_seule(tmp_path):
    """La vignette de la video EST le fond de la cover : la video ne se
    telecharge pas, sa vignette ne sera donc pas republiee derriere l'image
    qu'elle a deja servie a faire. Le post part avec la cover seule, qui porte
    bien une photo."""
    journal, outils = _outils_video(echec_telechargement=True)
    telecharges = []

    def telecharger(url):
        telecharges.append(url)
        return _png(1200, 800), ".png"

    noms = preparer_fichiers(
        _post(), _article_medias(_video(preview="https://img/fond.jpg")), tmp_path,
        rendre=_rendre(), telecharger=telecharger, **outils,
    )
    assert len(noms) == 1
    assert telecharges == []


def test_une_video_dont_la_vignette_est_le_fond_reste_une_slide(tmp_path):
    """Un clip n'est pas le meme asset que son image fixe : la video part en
    slide 2 meme si le fond de la cover est sa vignette."""
    _, outils = _outils_video()
    noms = preparer_fichiers(
        _post(), _article_medias(_video(preview="https://img/fond.jpg")), tmp_path,
        rendre=_rendre(), telecharger=_telecharger(), **outils,
    )
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.mp4"]


@pytest.mark.parametrize("sortie", [
    {**SORTIE_OK, "width": 1080, "height": 1920},
    {**SORTIE_OK, "audio_codec": None},
])
def test_une_video_transcodee_hors_specs_retombe_sur_sa_vignette(tmp_path, sortie):
    _, outils = _outils_video()
    outils["sonder"] = lambda octets: dict(sortie if octets == b"sortie" else SOURCE_OK)
    noms = _preparer(tmp_path, _post(), _article_medias(_video()), **outils)
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg"]


def test_les_visuels_d_un_compte_media_instagram_ne_sont_jamais_repris(tmp_path):
    """Watermarks : l'info d'un compte d'actu se reecrit, ses visuels jamais."""
    source = SimpleNamespace(type=SourceType.INSTAGRAM, categorie=SourceCategorie.MEDIA)
    article = _article_medias({"type": "photo", "url": "https://img/p.jpg"}, _video(), source=source)
    assert medias_du_carrousel(_post(), article) == []
    artiste = SimpleNamespace(type=SourceType.INSTAGRAM, categorie=SourceCategorie.ARTISTE)
    assert len(medias_du_carrousel(_post(), _article_medias(_video(), source=artiste))) == 1


def test_un_actif_du_post_devenu_interdit_n_est_plus_repris():
    """Relecture d'un `assets` pose quand la source etait encore autorisee : si
    elle est passee en compte media depuis, ses visuels ne partent pas."""
    source = SimpleNamespace(type=SourceType.INSTAGRAM, categorie=SourceCategorie.MEDIA)
    photo = {"type": "photo", "url": "https://img/p.jpg", "preview_url": None,
             "duration_ms": None, "width": None, "height": None}
    article = _article_medias(photo, source=source)
    post = _post()
    post.assets = [actif(photo, origine=ORIGINE_ARTICLE, permalink="https://www.instagram.com/p/G/")]
    assert medias_du_carrousel(post, article) == []


def test_neuf_medias_au_plus_apres_la_cover(tmp_path):
    _, outils = _outils_video()
    medias = [_video(url=f"https://cdn/{i}.mp4", preview=f"https://cdn/{i}.jpg") for i in range(12)]
    noms = _preparer(tmp_path, _post(), _article_medias(*medias), **outils)
    assert len(noms) == 10
    assert sum(nom.endswith(".mp4") for nom in noms) == 9


def test_un_nouveau_gel_efface_aussi_les_anciennes_videos(tmp_path):
    (tmp_path / "ancien_02.mp4").write_bytes(b"x")
    _preparer(tmp_path, _post(), _article("https://img/1.jpg"))
    assert not (tmp_path / "ancien_02.mp4").exists()


def test_une_cover_sans_fond_n_est_jamais_publiee(tmp_path):
    """Sans fond, la cover partirait sur l'image de secours : ce n'est pas un
    post publiable, et c'est le signe d'un brouillon compose sans visuel."""
    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": None}
    post = SimpleNamespace(slides=[cover], assets=[])
    with pytest.raises(PreparationImpossible, match="aucun fond"):
        preparer_fichiers(post, _article(), tmp_path, rendre=_rendre(), telecharger=_telecharger())


def test_le_fond_kinda_est_un_fond_publiable(tmp_path):
    """L'inverse du test precedent : `FOND_KINDA` n'est pas une absence de fond,
    c'est le choix assume de l'aplat sombre plutot qu'une affiche de line-up. Le
    gel doit le laisser passer -- sinon la file refuserait le post a chaque
    passage jusqu'a MANQUE."""
    from app.content.visuels import FOND_KINDA

    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": FOND_KINDA}
    post = SimpleNamespace(slides=[cover], assets=None)

    noms = preparer_fichiers(
        post, _article("https://img/affiche.jpg"), tmp_path,
        rendre=_rendre(), telecharger=_telecharger(),
    )

    # La cover, PLUS l'affiche : elle reste une slide honnete du carrousel, elle
    # ne fait simplement plus le fond du titre.
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg"]


def _telecharger_formats(formats):
    """Une image par URL, aux dimensions demandees : {url: (largeur, hauteur)}."""

    def telecharger(url):
        return _png(*formats[url]), ".png"

    return telecharger


def test_toutes_les_images_du_carrousel_sortent_au_format_publie(tmp_path):
    """Instagram impose a tout le carrousel le ratio du PREMIER element : une
    photo paysage laissee telle quelle revenait coupee n'importe comment
    (2026-09-18, « sinon les images rendent bizarre »)."""
    noms = preparer_fichiers(
        _post(), _article("https://img/paysage.jpg", "https://img/portrait.jpg"), tmp_path,
        rendre=_rendre(),
        telecharger=_telecharger_formats({
            "https://img/paysage.jpg": (3200, 1800),
            "https://img/portrait.jpg": (1080, 1920),
        }),
    )
    assert len(noms) == 3
    for nom in noms:
        assert Image.open(tmp_path / nom).size == (1080, 1350)


def test_une_image_que_le_cadrage_abimerait_est_simplement_sautee(tmp_path):
    """Ni erreur, ni exclusion : le panoramique n'est pas dans le carrousel, la
    photo correcte y est."""
    noms = preparer_fichiers(
        _post(), _article("https://img/pano.jpg", "https://img/ok.jpg"), tmp_path,
        rendre=_rendre(),
        telecharger=_telecharger_formats({
            "https://img/pano.jpg": (3000, 700),
            "https://img/ok.jpg": (1600, 1600),
        }),
    )
    assert len(noms) == 2
    assert Image.open(tmp_path / noms[1]).size == (1080, 1350)


def test_un_carrousel_vide_par_le_cadrage_dit_pourquoi(tmp_path):
    """Le seul visuel est un panoramique : l'article sort de la file (regle de la
    cover seule), avec la raison -- sinon l'exclusion serait muette."""
    with pytest.raises(PreparationImpossible, match="cover seule.*trop eloignee"):
        preparer_fichiers(
            _post_kinda(), _article("https://img/pano.jpg"), tmp_path,
            rendre=_rendre(),
            telecharger=_telecharger_formats({"https://img/pano.jpg": (3000, 700)}),
        )


def test_une_vignette_trop_petite_est_sautee(tmp_path):
    noms = preparer_fichiers(
        _post(), _article("https://img/vignette.jpg", "https://img/ok.jpg"), tmp_path,
        rendre=_rendre(),
        telecharger=_telecharger_formats({
            "https://img/vignette.jpg": (320, 240),
            "https://img/ok.jpg": (1600, 1600),
        }),
    )
    assert len(noms) == 2


# --- Tache 6 (2026-09-23) : agrandissement du fond au GEL -------------------
# Aucun test de ce bloc n'invoque le vrai binaire upscayl-bin : `agrandir` et
# `binaire_disponible` sont TOUJOURS injectes, comme `rendre` et `telecharger`
# le sont deja pour Chromium et le reseau -- la suite doit passer sur une
# machine sans Upscayl installe.


def _post_a_agrandir(fond="https://img/petit-fond.jpg", *medias, template="kinda_post"):
    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": fond}
    return SimpleNamespace(
        slides=[cover, *medias],
        template=template,
        variants={"fond": {"origine": "article", "a_agrandir": True}},
    )


def _post_sans_marqueur(fond="https://img/fond.jpg", template="kinda_post"):
    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": fond}
    return SimpleNamespace(
        slides=[cover], template=template,
        variants={"fond": {"origine": "article", "a_agrandir": False}},
    )


def _agrandisseur(sortie=None, *, appels=None):
    """Un faux `agrandir` qui simule `upscale.agrandir_fond` en entier : rend
    une image PNG de la taille demandee (par defaut 2640x1760, la sortie
    mesuree reellement sur 660x440 en 4x), puis la borne a `sortie_max_px`
    -- exactement ce que fait la vraie fonction apres l'appel a
    `upscayl-bin`, jamais invoque ici."""
    def agrandir(octets, *, sortie_max_px=None, **kwargs):
        if appels is not None:
            appels.append((octets, {"sortie_max_px": sortie_max_px, **kwargs}))
        largeur, hauteur = sortie or (2640, 1760)
        if sortie_max_px is not None and max(largeur, hauteur) > sortie_max_px:
            echelle = sortie_max_px / max(largeur, hauteur)
            largeur, hauteur = int(largeur * echelle), int(hauteur * echelle)
        return _png(largeur, hauteur)

    return agrandir


def test_un_fond_non_marque_n_est_jamais_envoye_a_l_agrandissement(tmp_path):
    """L'immense majorite des posts : pas de marqueur, le fond part tel quel,
    sans toucher `agrandir` ni `binaire_disponible`."""
    appels = []

    def binaire_disponible():
        appels.append("verifie")
        return True

    fond = verifier_fond(
        _post_sans_marqueur(), None,
        telecharger=_telecharger(), agrandir=_agrandisseur(), binaire_disponible=binaire_disponible,
    )

    assert fond == "https://img/fond.jpg"
    assert appels == []


def test_un_fond_deja_assez_grand_au_gel_n_est_pas_agrandi(tmp_path):
    """Le candidat a change entre la composition et le gel (cache renouvele) :
    s'il passe deja le plancher, `agrandir` n'est jamais appele."""
    appels = []

    fond = verifier_fond(
        _post_a_agrandir(), None,
        telecharger=_telecharger_formats({"https://img/petit-fond.jpg": (1600, 1600)}),
        agrandir=_agrandisseur(appels=appels), binaire_disponible=lambda: True,
    )

    assert fond == "https://img/petit-fond.jpg"
    assert appels == []


def test_binaire_absent_saute_sans_exclure(tmp_path):
    """PANNE MACHINE, jamais une exclusion : `PreparationSautee`, pas
    `PreparationImpossible`."""
    with pytest.raises(PreparationSautee, match="indisponible"):
        verifier_fond(
            _post_a_agrandir(), None,
            telecharger=_telecharger_formats({"https://img/petit-fond.jpg": (564, 345)}),
            agrandir=_agrandisseur(), binaire_disponible=lambda: False,
        )


def test_un_echec_de_l_outil_saute_sans_exclure(tmp_path):
    """Binaire present mais l'appel echoue (timeout, code de retour non nul,
    ...) : meme famille que le binaire absent, meme SAUT."""
    def agrandir_casse(octets, **kwargs):
        raise UpscaleIndisponible("agrandissement abandonne : delai de 60 s depasse")

    with pytest.raises(PreparationSautee, match="agrandissement en echec"):
        verifier_fond(
            _post_a_agrandir(), None,
            telecharger=_telecharger_formats({"https://img/petit-fond.jpg": (564, 345)}),
            agrandir=agrandir_casse, binaire_disponible=lambda: True,
        )


def test_un_resultat_encore_trop_petit_saute_au_lieu_de_publier(tmp_path):
    """Le plancher n'est JAMAIS contourne : un agrandissement qui ne suffit
    toujours pas (source proche du seuil bas) fait sauter, pas publier flou."""
    with pytest.raises(PreparationSautee, match="toujours sous le plancher"):
        verifier_fond(
            _post_a_agrandir(template="kinda_reel"), None,
            telecharger=_telecharger_formats({"https://img/petit-fond.jpg": (400, 400)}),
            # Le modele agrandit peu : 1920/600 = 3,2x, toujours au-dela du
            # plancher du reel (2,2x).
            agrandir=_agrandisseur(sortie=(600, 600)), binaire_disponible=lambda: True,
        )


def test_un_agrandissement_reussi_ecrit_le_cache_et_reecrit_les_slides(tmp_path):
    """Le cas nominal : le fond agrandi est copie dans `visuels_cache_dir`
    (meme purge que les autres copies locales), et `post.slides[0]` pointe
    dessus -- c'est ce que `render_post` lira ensuite."""
    from app.config import settings

    post = _post_a_agrandir()
    fond = verifier_fond(
        post, None,
        telecharger=_telecharger_formats({"https://img/petit-fond.jpg": (660, 440)}),
        agrandir=_agrandisseur(), binaire_disponible=lambda: True,
    )

    assert fond.startswith("file://")
    assert fond != "https://img/petit-fond.jpg"
    chemin = settings.visuels_cache_dir
    fichiers = list(chemin.glob("*.jpg"))
    assert len(fichiers) == 1
    assert post.slides[0]["image_url"] == fond
    # Reassignation, pas mutation en place (JSON) : une NOUVELLE liste.
    assert Image.open(fichiers[0]).size[0] <= settings.visuels_upscale_sortie_max_px


def test_un_telechargement_en_echec_differe_au_lieu_de_sauter(tmp_path):
    """Une panne de RESEAU sur le fond a agrandir n'est ni un saut ni une
    exclusion : on retente au passage suivant, comme le reste du carrousel."""
    with pytest.raises(PreparationDifferee):
        verifier_fond(
            _post_a_agrandir(), None,
            telecharger=_telecharger(echecs=("https://img/petit-fond.jpg",)),
            agrandir=_agrandisseur(), binaire_disponible=lambda: True,
        )


def test_l_agrandissement_ne_touche_que_le_fond_jamais_un_media_du_carrousel(tmp_path):
    """Test-sentinelle exige par le plan : un post marque `a_agrandir` dont le
    carrousel porte AUSSI un media minuscule ne doit agrandir QUE le fond
    (`slides[0].image_url`) -- jamais les autres slides, qui sont
    DOCUMENTAIRES (photo de presse ou capture de l'artiste), pas decoratives.
    """
    appels = []
    # 900x900 passe deja le plancher du CARROUSEL (`publication_crop_upscale_max`,
    # 2,2x sur 1080x1350 : 1350/900 = 1,5x) -- s'il finissait absent du
    # carrousel, ce serait la preuve que le test ne prouve rien, pas que le
    # sentinelle tient.
    media_minuscule = {"kind": "media", "type": "photo", "url": "https://img/media-minuscule.jpg"}
    post = _post_a_agrandir("https://img/petit-fond.jpg", media_minuscule)

    noms = preparer_fichiers(
        post, _article(), tmp_path,
        rendre=_rendre(),
        telecharger=_telecharger_formats({
            "https://img/petit-fond.jpg": (660, 440),
            "https://img/media-minuscule.jpg": (900, 900),
        }),
        agrandir=_agrandisseur(appels=appels), binaire_disponible=lambda: True,
    )

    # Un seul appel a l'agrandisseur : celui du fond. Le media minuscule du
    # carrousel, lui, suit la regle EXISTANTE (cadrage, jamais de
    # super-resolution) -- il est simplement carre/redimensionne par
    # `cadrer_carrousel`, jamais passe a `agrandir`.
    assert len(appels) == 1
    assert len(noms) == 2
