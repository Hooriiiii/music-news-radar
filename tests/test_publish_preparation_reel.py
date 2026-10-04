"""Le gel d'un reel : cover, video (API puis yt-dlp), assemblage, specs Meta."""

import io
import shutil
import subprocess
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app.content.media import entree
from app.content.visuels import ORIGINE_ARTICLE, ORIGINE_ARTISTE, actif
from app.content.render import RenduSlide
from app.content.video import AssemblageIndisponible
from app.models import SourceCategorie, SourceType
from app.publish.preparation import PreparationDifferee, PreparationImpossible
from app.publish.preparation_reel import (
    API,
    YTDLP,
    LienExpire,
    preparer_reel,
    source_de_la_video,
    telecharger_video,
)

besoin_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent -- assemblage non testable ici")

SOURCE = {"width": 1080, "height": 1920, "duration": 12.0, "has_audio": True, "video_codec": "vp9",
          "audio_codec": "aac", "sample_rate": 44100, "fps": 30.0}
SORTIE = {**SOURCE, "video_codec": "h264", "duration": 12.03}
REEL = "https://www.instagram.com/reel/DAbc123/"


def _png(largeur=1080, hauteur=1920):
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (10, 20, 30)).save(tampon, "PNG")
    return tampon.getvalue()


def _rendre(overflow=False):
    def rendre(slides, template, palette_=None):
        rendre.appels.append(template)
        return [RenduSlide(png=_png(), scale=1.0, lines={}, overflow=overflow)]

    rendre.appels = []
    return rendre


# La cover d'un reel est une IMAGE rendue par Chromium (elle sert de `cover_url`
# chez Meta) : un brouillon de reel porte donc toujours un fond, comme un
# carrousel.
FOND = "https://cdn/fond.jpg"


def _post(template="kinda_reel", credit="@saralandry", *medias, fond=FOND):
    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": fond}
    return SimpleNamespace(template=template, credit=credit, slides=[cover, *medias])


def _article(*medias, url="https://www.instagram.com/p/AAA/"):
    return SimpleNamespace(media=list(medias), url=url)


VIDEO = {"type": "video", "url": "https://cdn/v.mp4", "preview_url": None, "duration_ms": None,
         "width": None, "height": None}
SANS_URL = entree("video", None, preview_url="https://cdn/v.jpg")


class Recuperer:
    """Un faux yt-dlp : enregistre ses appels, rend des octets ou leve."""

    def __init__(self, octets=b"source", erreur=None):
        self.octets, self.erreur, self.appels = octets, erreur, []

    def __call__(self, url, dossier, plafond):
        self.appels.append((url, dossier, plafond))
        if self.erreur is not None:
            raise self.erreur
        return self.octets


def _interdit(*args, **kwargs):
    raise AssertionError("cet outil ne devait pas etre appele")


def _outils(source=SOURCE, sortie=SORTIE):
    def sonder(octets):
        return source if octets == b"source" else sortie

    return {
        "rendre": _rendre(),
        "telecharger": lambda url, plafond: b"source",
        "sonder": sonder,
        "assembler": lambda cover, video: b"reel-mp4",
        "verifier_outils": lambda: None,
    }


def test_un_reel_fige_deux_fichiers_video_puis_cover(tmp_path):
    outils = _outils()
    noms = preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)
    assert [nom.rsplit("_", 1)[1] for nom in noms] == ["reel.mp4", "cover.jpg"]
    assert (tmp_path / noms[0]).read_bytes() == b"reel-mp4"
    cover = Image.open(io.BytesIO((tmp_path / noms[1]).read_bytes()))
    assert (cover.format, cover.size) == ("JPEG", (1080, 1920))
    assert outils["rendre"].appels == ["kinda_reel"]


def test_le_prefixe_ne_se_devine_pas(tmp_path):
    premier = preparer_reel(_post(), _article(VIDEO), tmp_path / "a", **_outils())
    second = preparer_reel(_post(), _article(VIDEO), tmp_path / "b", **_outils())
    assert premier[0] != second[0]


def test_un_gel_repete_efface_les_anciens_fichiers(tmp_path):
    preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils())
    noms = preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils())
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(noms)


def test_source_de_la_video_l_api_d_abord():
    slide = {"kind": "media", "type": "video", "url": "https://cdn/choisie.mp4"}
    assert source_de_la_video(_post("kinda_reel", "@x", slide), _article(VIDEO)) == (API, "https://cdn/choisie.mp4")
    assert source_de_la_video(_post(), _article(VIDEO, url=REEL)) == (API, "https://cdn/v.mp4")
    assert source_de_la_video(_post(), _article(SANS_URL, url=REEL)) == (YTDLP, REEL)
    assert source_de_la_video(_post(), _article(SANS_URL)) == (YTDLP, "https://www.instagram.com/p/AAA/")
    assert source_de_la_video(_post(), _article(url="https://tsugi.fr/a")) is None
    assert source_de_la_video(_post(), None) is None


def test_sans_media_url_la_video_vient_de_yt_dlp(tmp_path):
    recuperer = Recuperer()
    outils = {**_outils(), "telecharger": _interdit}
    noms = preparer_reel(_post(), _article(SANS_URL, url=REEL), tmp_path, recuperer_ytdlp=recuperer, **outils)
    assert [(url, dossier, plafond) for url, dossier, plafond in recuperer.appels] == [(REEL, tmp_path, 100 * 1024 * 1024)]
    assert (tmp_path / noms[0]).read_bytes() == b"reel-mp4"


def test_une_url_directe_ne_lance_jamais_yt_dlp(tmp_path):
    preparer_reel(_post(), _article(VIDEO, url=REEL), tmp_path, recuperer_ytdlp=_interdit, **_outils())


def test_un_lien_api_expire_retombe_sur_yt_dlp(tmp_path):
    """Les media_url du CDN sont signees et expirent en quelques jours."""
    recuperer = Recuperer()

    def expire(url, plafond):
        raise LienExpire("video indisponible (HTTP 403) : lien expire ou retire")

    outils = {**_outils(), "telecharger": expire}
    preparer_reel(_post(), _article(VIDEO, url=REEL), tmp_path, recuperer_ytdlp=recuperer, **outils)
    assert [appel[0] for appel in recuperer.appels] == [REEL]


def test_un_lien_expire_hors_instagram_reste_une_faute(tmp_path):
    def expire(url, plafond):
        raise LienExpire("video indisponible (HTTP 403) : lien expire ou retire")

    outils = {**_outils(), "telecharger": expire}
    with pytest.raises(PreparationImpossible, match="403"):
        preparer_reel(_post(), _article(VIDEO, url="https://t/1"), tmp_path, recuperer_ytdlp=_interdit, **outils)


PHOTO = {"type": "photo", "url": "https://cdn/p.jpg"}


def _expire(url, plafond):
    raise LienExpire("video indisponible (HTTP 403) : lien expire ou retire")


def test_un_lien_expire_d_un_carrousel_instagram_ne_lance_pas_yt_dlp(tmp_path):
    """M2 : un post /p/ a plusieurs medias n'est pas recuperable par yt-dlp (il
    rendrait une liste) : le lien expire reste une faute, sans repli."""
    outils = {**_outils(), "telecharger": _expire}
    with pytest.raises(LienExpire, match="403"):
        preparer_reel(_post(), _article(PHOTO, VIDEO), tmp_path, recuperer_ytdlp=_interdit, **outils)


def test_un_lien_expire_d_un_carrousel_instagram_yt_dlp_coupe_reste_une_faute(tmp_path):
    """Pas de repli possible : rien a attendre, meme yt-dlp coupe."""
    outils = {**_outils(), "telecharger": _expire}
    with pytest.raises(LienExpire):
        preparer_reel(_post(), _article(PHOTO, VIDEO), tmp_path, recuperer_ytdlp=None, **outils)


def test_un_lien_expire_d_un_post_video_seul_retombe_sur_yt_dlp(tmp_path):
    """Un /p/ dont le SEUL media est la video : yt-dlp sait la recuperer."""
    recuperer = Recuperer()
    outils = {**_outils(), "telecharger": _expire}
    preparer_reel(_post(), _article(VIDEO), tmp_path, recuperer_ytdlp=recuperer, **outils)
    assert [appel[0] for appel in recuperer.appels] == ["https://www.instagram.com/p/AAA/"]


REEL_FRED = "https://www.instagram.com/reel/FRED/"


def _post_avec_actif(video, origine=ORIGINE_ARTISTE):
    post = _post()
    post.assets = [actif(video, origine=origine, compte="@fredagain", permalink=REEL_FRED)]
    return post


def _article_media_ig(*medias):
    """Un article venu d'un compte MEDIA Instagram : son info est publiable, ses
    videos jamais."""
    source = SimpleNamespace(type=SourceType.INSTAGRAM, categorie=SourceCategorie.MEDIA, url="ig:guettapen")
    return SimpleNamespace(media=list(medias), url="https://www.instagram.com/reel/GUET/", source=source)


def test_la_video_d_un_artiste_vient_de_son_permalink():
    assert source_de_la_video(_post_avec_actif(SANS_URL), _article_media_ig(VIDEO)) == (YTDLP, REEL_FRED)
    assert source_de_la_video(_post_avec_actif(VIDEO), _article_media_ig()) == (API, "https://cdn/v.mp4")


def test_jamais_la_video_d_un_compte_media():
    slide = {"kind": "media", "type": "video", "url": "https://cdn/v.mp4"}
    assert source_de_la_video(_post("kinda_reel", "@x", slide), _article_media_ig(VIDEO)) is None
    assert source_de_la_video(_post(), _article_media_ig(VIDEO)) is None


def test_un_actif_devenu_interdit_n_est_plus_publie():
    """Relecture d'un `assets` pose avant que la source passe en `media` : l'actif
    de l'article ne part pas, et son URL non plus."""
    assert source_de_la_video(_post_avec_actif(VIDEO, ORIGINE_ARTICLE), _article_media_ig(VIDEO)) is None
    assert source_de_la_video(_post_avec_actif(VIDEO), _article_media_ig(VIDEO)) is None


def test_un_lien_d_artiste_expire_retombe_sur_son_permalink(tmp_path):
    recuperer = Recuperer()
    outils = {**_outils(), "telecharger": _expire}
    preparer_reel(_post_avec_actif(VIDEO), _article_media_ig(), tmp_path, recuperer_ytdlp=recuperer, **outils)
    assert [appel[0] for appel in recuperer.appels] == [REEL_FRED]


def test_yt_dlp_indisponible_pour_ce_gel_differe_sans_exclure(tmp_path):
    """PUBLICATION_REEL_YTDLP=false apres la programmation : le creneau attend,
    l'article n'est pas exclu (yt-dlp peut etre reactive)."""
    with pytest.raises(PreparationDifferee, match="yt-dlp") as attente:
        preparer_reel(_post(), _article(SANS_URL, url=REEL), tmp_path, recuperer_ytdlp=None, **_outils())
    assert not isinstance(attente.value, PreparationImpossible)


def test_une_attente_yt_dlp_remonte_telle_quelle(tmp_path):
    recuperer = Recuperer(erreur=PreparationDifferee("yt-dlp en pause jusqu'a 11:00 UTC"))
    with pytest.raises(PreparationDifferee, match="pause"):
        preparer_reel(_post(), _article(SANS_URL, url=REEL), tmp_path, recuperer_ytdlp=recuperer, **_outils())


def test_la_cover_est_rendue_avant_yt_dlp(tmp_path):
    """Une cover qui deborde ne doit pas consommer le telechargement du passage."""
    outils = {**_outils(), "rendre": _rendre(overflow=True)}
    with pytest.raises(PreparationImpossible, match="deborde"):
        preparer_reel(_post(), _article(SANS_URL, url=REEL), tmp_path, recuperer_ytdlp=_interdit, **outils)


@pytest.mark.parametrize(
    "post, article, motif",
    [
        (_post(template="kinda_post"), _article(VIDEO), "gabarit"),
        (_post(credit=None), _article(VIDEO), "credit"),
        (_post(), _article(url="https://tsugi.fr/a"), "aucune video"),
    ],
)
def test_les_fautes_de_l_article(tmp_path, post, article, motif):
    with pytest.raises(PreparationImpossible, match=motif):
        preparer_reel(post, article, tmp_path, **_outils())


def test_une_cover_qui_deborde_est_une_faute(tmp_path):
    outils = {**_outils(), "rendre": _rendre(overflow=True)}
    with pytest.raises(PreparationImpossible, match="deborde"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)


@pytest.mark.parametrize("duree, motif", [(2.0, "trop courte"), (95.0, "trop longue")])
def test_une_duree_hors_bornes_est_refusee(tmp_path, duree, motif):
    with pytest.raises(PreparationImpossible, match=motif):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils(source={**SOURCE, "duration": duree}))


def test_le_gel_applique_le_plancher_EDITORIAL_pas_celui_de_meta(tmp_path):
    """Une video de 4 s passe le plancher technique de Meta (3 s) mais pas le
    plancher editorial (6 s). C'est le dernier filet pour une duree qui etait
    INCONNUE a la selection -- reel 168 du 2026-10-03. `ReelInadapte` libere le
    creneau sans exclure l'article, qui reste publiable en carrousel."""
    from app.publish.preparation_reel import ReelInadapte

    with pytest.raises(ReelInadapte, match="trop courte : 4.0 s, minimum 6 s") as leve:
        preparer_reel(_post(), _article(VIDEO), tmp_path,
                      **_outils(source={**SOURCE, "duration": 4.0}))
    # La duree MESUREE voyage avec l'exception : `runner._memoriser_duree`
    # l'ecrit dans `articles.media`, la video n'est plus jamais rechoisie.
    assert leve.value.duree_ms == 4000


def test_une_sortie_hors_specs_est_refusee_avant_meta(tmp_path):
    with pytest.raises(PreparationImpossible, match="cadence"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils(sortie={**SORTIE, "fps": 20.0}))


def test_une_sortie_vp9_serait_refusee(tmp_path):
    """Filet : si l'assemblage copiait un jour le flux VP9 de yt-dlp, le gel le refuserait."""
    with pytest.raises(PreparationImpossible, match="codec video vp9"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils(sortie={**SORTIE, "video_codec": "vp9"}))


def test_une_video_illisible_est_une_faute(tmp_path):
    outils = _outils()

    def illisible(octets):
        raise AssemblageIndisponible("video illisible : moov atom not found")

    outils["sonder"] = illisible
    with pytest.raises(PreparationImpossible, match="illisible"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)


def test_un_assemblage_trop_long_est_differe(tmp_path):
    """Un delai depasse vient le plus souvent d'une machine chargee : on retente."""
    outils = _outils()

    def trop_long(cover, video):
        raise subprocess.TimeoutExpired("ffmpeg", 300)

    outils["assembler"] = trop_long
    with pytest.raises(PreparationDifferee, match="delai") as attente:
        preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)
    assert not isinstance(attente.value, PreparationImpossible)


@pytest.mark.parametrize(
    "returncode, stderr",
    [
        (1, "Error writing trailer: No space left on device"),
        (1, "av_malloc failed: Cannot allocate memory"),
        (-9, ""),
    ],
)
def test_un_ffmpeg_tue_ou_a_court_de_ressources_est_systemique(tmp_path, returncode, stderr):
    outils = _outils()

    def panne(cover, video):
        raise AssemblageIndisponible("assemblage impossible", returncode=returncode, stderr=stderr)

    outils["assembler"] = panne
    with pytest.raises(AssemblageIndisponible):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)


def test_un_ffmpeg_en_echec_ordinaire_reste_une_faute(tmp_path):
    outils = _outils()

    def echec(cover, video):
        raise AssemblageIndisponible("assemblage impossible : Invalid data", returncode=1, stderr="Invalid data")

    outils["assembler"] = echec
    with pytest.raises(PreparationImpossible, match="Invalid data"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)


def test_assembler_reel_expose_le_code_et_la_sortie_d_erreur(monkeypatch):
    from app.content import video

    monkeypatch.setattr(video, "_exiger_ffmpeg", lambda nom: nom)
    monkeypatch.setattr(video, "sonder", lambda octets: {"has_audio": True})
    monkeypatch.setattr(
        video.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, -9, stdout="", stderr="debut\nNo space left on device"),
    )
    with pytest.raises(AssemblageIndisponible) as erreur:
        video.assembler_reel(b"png", b"mp4")
    assert erreur.value.returncode == -9
    assert "No space left on device" in erreur.value.stderr


@pytest.mark.parametrize("erreur", [KeyError("duration"), ValueError("could not convert")])
def test_un_flux_source_bizarre_est_une_faute(tmp_path, erreur):
    outils = _outils()

    def bizarre(octets):
        raise erreur

    outils["sonder"] = bizarre
    with pytest.raises(PreparationImpossible, match="sonde"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **outils)


@pytest.mark.parametrize(
    "defaut, motif",
    [
        ({"width": 720, "height": 1280}, "dimensions 720x1280"),
        ({"audio_codec": "opus"}, "codec audio opus"),
        ({"sample_rate": 96000}, "echantillonnage 96000"),
        ({"duration": 2.0}, "duree 2.0"),
    ],
)
def test_chaque_spec_de_sortie_est_controlee(tmp_path, defaut, motif):
    with pytest.raises(PreparationImpossible, match=motif):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils(sortie={**SORTIE, **defaut}))


def test_une_sortie_trop_lourde_est_refusee(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "publication_reel_max_mo", 0)
    with pytest.raises(PreparationImpossible, match="poids"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils())


def test_une_cover_inexploitable_est_une_faute(tmp_path, monkeypatch):
    from app.publish import preparation_reel
    from app.publish.images import ImageInexploitable

    def inexploitable(octets):
        raise ImageInexploitable("image corrompue")

    monkeypatch.setattr(preparation_reel, "en_jpeg", inexploitable)
    with pytest.raises(PreparationImpossible, match="cover inexploitable"):
        preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils())


def test_un_lien_expire_sans_yt_dlp_sur_instagram_differe(tmp_path):
    """yt-dlp coupe par .env : le repli existe, il est seulement indisponible."""
    def expire(url, plafond):
        raise LienExpire("video indisponible (HTTP 403) : lien expire ou retire")

    outils = {**_outils(), "telecharger": expire}
    with pytest.raises(PreparationDifferee, match="yt-dlp") as attente:
        preparer_reel(_post(), _article(VIDEO, url=REEL), tmp_path, recuperer_ytdlp=None, **outils)
    assert not isinstance(attente.value, PreparationImpossible)


@pytest.mark.parametrize("statut", [408, 429])
def test_un_statut_transitoire_differe_sans_repli(tmp_path, statut):
    client = _client(lambda requete: httpx.Response(statut))
    with pytest.raises(PreparationDifferee, match=str(statut)) as attente:
        telecharger_video("https://cdn/v.mp4", 1000, client=client)
    assert not isinstance(attente.value, PreparationImpossible)

    def transitoire(url, plafond):
        return telecharger_video(url, plafond, client=_client(lambda requete: httpx.Response(statut)))

    outils = {**_outils(), "telecharger": transitoire}
    with pytest.raises(PreparationDifferee):
        preparer_reel(_post(), _article(VIDEO, url=REEL), tmp_path, recuperer_ytdlp=_interdit, **outils)


def test_ffmpeg_absent_est_une_panne_systemique(tmp_path):
    """Pas d'exclusion : la faute est a la machine, pas a l'article."""
    outils = _outils()

    def absent():
        raise AssemblageIndisponible("ffmpeg est introuvable")

    outils["verifier_outils"] = absent
    with pytest.raises(AssemblageIndisponible):
        preparer_reel(_post(), _article(SANS_URL, url=REEL), tmp_path, recuperer_ytdlp=_interdit, **outils)


# --- Telechargement -------------------------------------------------------------


def _client(gestionnaire):
    return httpx.Client(transport=httpx.MockTransport(gestionnaire))


def test_telecharger_rend_les_octets():
    client = _client(lambda requete: httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"mp4"))
    assert telecharger_video("https://cdn/v.mp4", 1000, client=client) == b"mp4"


def test_un_lien_expire_leve_lien_expire():
    client = _client(lambda requete: httpx.Response(403))
    with pytest.raises(LienExpire, match="403"):
        telecharger_video("https://cdn/v.mp4", 1000, client=client)


def test_une_panne_du_serveur_est_systemique():
    client = _client(lambda requete: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        telecharger_video("https://cdn/v.mp4", 1000, client=client)


def test_une_video_trop_lourde_est_refusee_en_cours_de_lecture():
    client = _client(lambda requete: httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"x" * 2000))
    with pytest.raises(PreparationImpossible, match="trop lourde"):
        telecharger_video("https://cdn/v.mp4", 1000, client=client)


def test_une_page_html_est_un_portail_captif_differe():
    """Un Wi-Fi d'hotel rend une page HTML : la faute n'est pas a l'article."""
    client = _client(lambda requete: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"))
    with pytest.raises(PreparationDifferee, match="pas une video") as attente:
        telecharger_video("https://cdn/v.mp4", 1000, client=client)
    assert not isinstance(attente.value, PreparationImpossible)


def test_un_autre_type_n_est_pas_une_video():
    client = _client(lambda requete: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"jpg"))
    with pytest.raises(PreparationImpossible, match="pas une video"):
        telecharger_video("https://cdn/v.mp4", 1000, client=client)


@pytest.mark.parametrize("type_", ["application/octet-stream", "binary/octet-stream"])
def test_un_flux_binaire_est_accepte(type_):
    client = _client(lambda requete: httpx.Response(200, headers={"content-type": type_}, content=b"mp4"))
    assert telecharger_video("https://cdn/v.mp4", 1000, client=client) == b"mp4"


def test_une_redirection_hors_https_est_refusee():
    def gestionnaire(requete):
        if requete.url.scheme == "https":
            return httpx.Response(302, headers={"location": "http://cdn/v.mp4"})
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"mp4")

    client = httpx.Client(transport=httpx.MockTransport(gestionnaire), follow_redirects=True)
    with pytest.raises(PreparationImpossible, match="https"):
        telecharger_video("https://cdn/v.mp4", 1000, client=client)


def test_une_url_hors_https_est_refusee():
    with pytest.raises(PreparationImpossible, match="https"):
        telecharger_video("http://cdn/v.mp4", 1000, client=_client(_interdit))


def test_un_telechargement_interminable_est_differe():
    temps = iter([0.0, 301.0, 302.0, 303.0])
    client = _client(lambda requete: httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"mp4"))
    with pytest.raises(PreparationDifferee, match="delai"):
        telecharger_video("https://cdn/v.mp4", 1000, client=client, horloge=lambda: next(temps))


# --- Chaine reelle, ffmpeg compris ------------------------------------------------


@besoin_ffmpeg
def test_la_chaine_complete_avec_ffmpeg(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30:duration=8",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
        capture_output=True, check=True,
    )
    octets = source.read_bytes()
    noms = preparer_reel(_post(), _article(SANS_URL, url=REEL), tmp_path / "fige", rendre=_rendre(),
                         recuperer_ytdlp=Recuperer(octets=octets))
    assert (tmp_path / "fige" / noms[0]).stat().st_size > 0


def test_une_pause_yt_dlp_connue_n_allume_pas_chromium(tmp_path):
    """Pause ou budget epuise : l'attente part AVANT le rendu de la cover."""
    from app.publish.ytdlp import YtdlpEnAttente

    recuperer = Recuperer()
    recuperer.attente = lambda: YtdlpEnAttente("yt-dlp en pause jusqu'a 11:00 UTC")
    outils = {**_outils(), "rendre": _interdit}
    with pytest.raises(YtdlpEnAttente, match="pause"):
        preparer_reel(_post(), _article(SANS_URL, url=REEL), tmp_path, recuperer_ytdlp=recuperer, **outils)
    assert recuperer.appels == []


def test_une_pause_yt_dlp_ne_bloque_pas_une_video_directe(tmp_path):
    from app.publish.ytdlp import YtdlpEnAttente

    recuperer = Recuperer()
    recuperer.attente = lambda: YtdlpEnAttente("yt-dlp en pause")
    preparer_reel(_post(), _article(VIDEO, url=REEL), tmp_path, recuperer_ytdlp=recuperer, **_outils())


@pytest.mark.parametrize("fond", [None, "", "None"])
def test_un_reel_sans_fond_de_cover_n_est_jamais_gele(tmp_path, fond):
    """La cover d'un reel est l'image de couverture envoyee a Meta (`cover_url`).
    Sans fond -- efface au dashboard, ou reduit au `str(None)` de brouillons de
    production --, elle partirait sur l'image de secours du gabarit. Refuse AVANT
    le rendu et avant le telechargement de la video."""
    outils = {**_outils(), "rendre": _interdit, "telecharger": _interdit}
    with pytest.raises(PreparationImpossible, match="aucun fond de cover"):
        preparer_reel(_post(fond=fond), _article(VIDEO, url=REEL), tmp_path,
                      recuperer_ytdlp=_interdit, **outils)


# --- La video ne convient pas, l'article n'y est pour rien (2026-09-21) -------

def test_une_duree_hors_bornes_leve_reel_inadapte_et_dit_ce_qu_elle_a_mesure():
    """Le 21/09, l'article 18951 (Billboard, The Weeknd) a ete EXCLU
    definitivement parce qu'une video de 190 s etait trop longue -- une video
    qui n'etait meme pas la sienne, empruntee a un compte d'artiste. La faute
    est celle du visuel, pas de l'actu : `ReelInadapte` la distingue, et porte
    la duree MESUREE pour qu'on l'ecrive plutot que de la redecouvrir.
    """
    from app.publish.preparation_reel import ReelInadapte, verifier_source

    with pytest.raises(ReelInadapte) as leve:
        verifier_source({"duration": 190.0}, url="https://cdn/v.mp4", article_id=18951)

    assert leve.value.duree_ms == 190_000
    assert (leve.value.url, leve.value.article_id) == ("https://cdn/v.mp4", 18951)
    assert isinstance(leve.value, PreparationImpossible)  # le gel la saute comme avant


def test_le_gel_d_un_reel_trop_long_porte_la_video_et_son_article(tmp_path):
    from app.publish.preparation_reel import ReelInadapte

    with pytest.raises(ReelInadapte) as leve:
        preparer_reel(_post(), _article(VIDEO), tmp_path, **_outils(source={**SOURCE, "duration": 190.0}))

    assert leve.value.url == VIDEO["url"]
    assert leve.value.duree_ms == 190_000


# --- Tache 6, tour de correction 1/5 : la sentinelle promise par CLAUDE.md --
# doit couvrir le reel aussi, pas seulement le carrousel (`test_publish_
# preparation.py`) : sans ce test, un futur branchement de l'agrandissement
# sur la VIDEO du reel ne serait rouge nulle part.


def _post_a_agrandir(*medias, fond=FOND):
    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": fond}
    return SimpleNamespace(
        template="kinda_reel", credit="@saralandry", slides=[cover, *medias],
        variants={"fond": {"origine": "article", "a_agrandir": True}},
    )


def _telecharger_image_formats(formats):
    def telecharger(url):
        largeur, hauteur = formats.get(url, (1200, 800))
        return _png(largeur, hauteur), ".png"

    return telecharger


def test_l_agrandissement_ne_touche_que_la_cover_jamais_la_video_du_reel(tmp_path):
    """Un post marque `a_agrandir` dont la video source est ELLE AUSSI plus
    petite que ses specs ne doit agrandir QUE la cover -- la video suit sa
    propre chaine (telechargement, sonde, assemblage ffmpeg), jamais
    `upscale.agrandir_fond`."""
    appels = []

    def agrandisseur(octets, **kwargs):
        appels.append(kwargs)
        return _png(2640, 1760)

    outils = _outils()
    noms = preparer_reel(
        _post_a_agrandir(), _article(VIDEO), tmp_path,
        telecharger_image=_telecharger_image_formats({FOND: (660, 440)}),
        agrandir=agrandisseur, binaire_disponible=lambda: True,
        **outils,
    )

    assert len(appels) == 1  # uniquement la cover, jamais la video
    assert [nom.rsplit("_", 1)[1] for nom in noms] == ["reel.mp4", "cover.jpg"]
