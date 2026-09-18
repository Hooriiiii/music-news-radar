"""Le gel : ce qui partira chez Meta, fige sur disque avant l'heure."""

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.content.render import RenduIndisponible, RenduSlide
from app.models import SourceCategorie, SourceType
from app.content.visuels import ORIGINE_ARTICLE, actif
from app.publish.preparation import PreparationImpossible, medias_du_carrousel, preparer_fichiers


def _png(largeur, hauteur):
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (10, 20, 30)).save(tampon, "PNG")
    return tampon.getvalue()


def _rendre(overflow=False):
    def rendre(slides, template, palette_=None):
        rendre.appels.append((slides, template))
        return [RenduSlide(png=_png(1080, 1440), scale=1.0, lines={}, overflow=overflow)]

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
    preparer_fichiers(_post(), None, tmp_path, rendre=_rendre(), telecharger=_telecharger())
    assert not (tmp_path / "ancien_01.jpg").exists()


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


def test_une_video_qui_retomberait_sur_la_vignette_du_fond_est_ecartee(tmp_path):
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
    _preparer(tmp_path, _post(), None)
    assert not (tmp_path / "ancien_02.mp4").exists()


def test_une_cover_sans_fond_n_est_jamais_publiee(tmp_path):
    """Sans fond, la cover partirait sur l'image de secours : ce n'est pas un
    post publiable, et c'est le signe d'un brouillon compose sans visuel."""
    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": None}
    post = SimpleNamespace(slides=[cover], assets=[])
    with pytest.raises(PreparationImpossible, match="aucun fond"):
        preparer_fichiers(post, _article(), tmp_path, rendre=_rendre(), telecharger=_telecharger())
