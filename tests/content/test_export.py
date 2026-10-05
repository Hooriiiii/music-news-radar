import io
import zipfile
from types import SimpleNamespace

import httpx

from app.content.export import PLAFOND_OCTETS, construire_zip, echec_transitoire, telecharger
from app.models import SourceCategorie, SourceType


class FausseReponse:
    """Imite l'objet retourne par `client.stream(...)` : contexte + iter_bytes."""

    def __init__(self, morceaux, content_type=None, status_code=200):
        self._morceaux = morceaux
        self.headers = {"content-type": content_type} if content_type else {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self):
        yield from self._morceaux

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FauxClient:
    """Un seul flux configure : suffit pour tester `telecharger` sans reseau."""

    def __init__(self, reponse):
        self._reponse = reponse

    def stream(self, method, url, headers=None, timeout=None):
        return self._reponse


def _post_avec_medias():
    return SimpleNamespace(
        template="kinda_post",
        slides=[
            {"kind": "cover", "headline": "x", "title": "x", "subtitle": "x"},
            {"kind": "media", "type": "photo", "url": "https://p.jpg"},
            {"kind": "media", "type": "video", "url": "https://v.mp4"},
        ]
    )


def _telecharger_factice(url, **kwargs):
    if url.endswith(".jpg"):
        return b"JPEGDATA", ".jpg"
    if url.endswith(".mp4"):
        return b"MP4DATA", ".mp4"
    raise AssertionError(f"url inattendue : {url}")


def test_le_zip_contient_la_cover_les_medias_et_la_legende():
    post = _post_avec_medias()
    zip_octets = construire_zip(
        post, [b"PNGDATA"], "legende du post", telecharger=_telecharger_factice
    )
    noms = zipfile.ZipFile(io.BytesIO(zip_octets)).namelist()

    assert noms == ["01_cover.png", "02_photo.jpg", "03_video.mp4", "caption.txt"]


def test_un_media_qui_echoue_ne_fait_pas_echouer_le_zip():
    """Meme philosophie que l'image de secours du rendu : un export partiel vaut
    mieux qu'une erreur, surtout a 23 h. La raison et l'URL doivent survivre pour
    qu'on puisse aller chercher le media a la main."""
    post = _post_avec_medias()

    def telecharger_casse(url, **kwargs):
        if url.endswith(".jpg"):
            return b"JPEGDATA", ".jpg"
        return None, "404 Not Found"

    zip_octets = construire_zip(post, [b"PNGDATA"], "legende", telecharger=telecharger_casse)
    archive = zipfile.ZipFile(io.BytesIO(zip_octets))

    assert "medias_manquants.txt" in archive.namelist()
    manquants = archive.read("medias_manquants.txt").decode()
    assert "https://v.mp4" in manquants and "404" in manquants
    assert "01_cover.png" in archive.namelist()  # la cover survit


def test_pas_de_fichier_manquants_quand_tout_passe():
    """Un fichier vide dans le zip ferait croire a un probleme."""
    post = _post_avec_medias()
    zip_octets = construire_zip(
        post, [b"PNGDATA"], "legende", telecharger=_telecharger_factice
    )
    assert "medias_manquants.txt" not in zipfile.ZipFile(io.BytesIO(zip_octets)).namelist()


def test_extension_inconnue_part_dans_les_manquants():
    """Un fichier .bin muet vaudrait moins qu'un fichier absent mais signale."""
    post = SimpleNamespace(
        slides=[{"kind": "media", "type": "photo", "url": "https://cdn.example/blob"}]
    )

    def telecharger_sans_extension(url, **kwargs):
        return b"DONNEES", ".bin"

    zip_octets = construire_zip(
        post, [], "legende", telecharger=telecharger_sans_extension
    )
    archive = zipfile.ZipFile(io.BytesIO(zip_octets))

    assert "01_photo.bin" in archive.namelist()
    manquants = archive.read("medias_manquants.txt").decode()
    assert "https://cdn.example/blob" in manquants


def test_telecharger_refuse_au_dela_du_plafond():
    """Une source qui renvoie 2 Go ne doit pas bloquer l'export. Le plafond se
    verifie EN COURS de lecture : se fier a Content-Length laisserait passer une
    reponse sans en-tete ou mensongere."""
    gros_morceaux = [b"x" * 1024 for _ in range(2048)]  # 2 Mo, plafond a 1 Ko
    client = FauxClient(FausseReponse(gros_morceaux, content_type="video/mp4"))

    contenu, raison = telecharger("https://gros.mp4", plafond=1024, client=client)

    assert contenu is None
    assert "plafond" in raison


def test_telecharger_nomme_lextension_dapres_le_type_de_contenu():
    """Instagram refuse un fichier sans extension coherente."""
    client = FauxClient(FausseReponse([b"data"], content_type="image/png"))

    contenu, extension = telecharger("https://x.example/media", client=client)

    assert contenu == b"data"
    assert extension == ".png"


def test_telecharger_retombe_sur_le_suffixe_de_lurl():
    client = FauxClient(FausseReponse([b"data"], content_type=None))

    contenu, extension = telecharger("https://x.example/clip.mov", client=client)

    assert contenu == b"data"
    assert extension == ".mov"


def test_le_zip_exclut_un_media_de_compte_media_instagram():
    """Un utilisateur du dashboard qui exporte a la main un post dont une slide
    pointe vers l'image d'un compte media Instagram (watermarks) ne doit jamais
    recevoir ce fichier : meme politique qu'au gel automatique (preparation.py)."""
    post = _post_avec_medias()
    source_interdite = SimpleNamespace(type=SourceType.INSTAGRAM, categorie=SourceCategorie.MEDIA)
    article = SimpleNamespace(
        source=source_interdite,
        media=[{"type": "photo", "url": "https://p.jpg"}, {"type": "video", "url": "https://v.mp4"}],
    )

    zip_octets = construire_zip(
        post, [b"PNGDATA"], "legende du post", telecharger=_telecharger_factice, article=article
    )
    archive = zipfile.ZipFile(io.BytesIO(zip_octets))

    assert archive.namelist() == ["01_cover.png", "medias_manquants.txt", "caption.txt"]
    manquants = archive.read("medias_manquants.txt").decode()
    assert "https://p.jpg" in manquants and "https://v.mp4" in manquants
    assert "compte media" in manquants


def test_le_zip_sans_article_ne_filtre_rien():
    """Un post dont l'article a ete purge (article_id -> NULL) exporte comme
    avant : pas de politique applicable sans savoir d'ou vient le media."""
    post = _post_avec_medias()

    zip_octets = construire_zip(
        post, [b"PNGDATA"], "legende", telecharger=_telecharger_factice, article=None
    )
    noms = zipfile.ZipFile(io.BytesIO(zip_octets)).namelist()

    assert noms == ["01_cover.png", "02_photo.jpg", "03_video.mp4", "caption.txt"]


def test_telecharger_retombe_sur_bin_sans_indice():
    client = FauxClient(FausseReponse([b"data"], content_type="application/octet-stream"))

    contenu, extension = telecharger("https://x.example/media", client=client)

    assert contenu == b"data"
    assert extension == ".bin"


def test_telecharger_capture_lechec_reseau():
    class ClientQuiExplose:
        def stream(self, method, url, headers=None, timeout=None):
            raise RuntimeError("connexion refusee")

    contenu, raison = telecharger("https://x.example/media", client=ClientQuiExplose())

    assert contenu is None
    assert "connexion refusee" in raison


def test_plafond_par_defaut_est_80_mo():
    assert PLAFOND_OCTETS == 80 * 1024 * 1024


# --- Gabarit reel : le zip porte la video assemblee, pas ses morceaux ---


def _post_reel():
    return SimpleNamespace(
        template="kinda_reel",
        slides=[
            {"kind": "cover", "headline": "x", "title": "x", "subtitle": "x"},
            {"kind": "media", "type": "video", "url": "https://v.mp4"},
        ],
    )


def test_un_post_reel_contient_le_mp4_assemble(): 
    """Le livrable d'un reel est UNE video, pas une cover et une video a
    recoller a la main."""
    appels = []

    def assembler(cover, video):
        appels.append((cover, video))
        return b"REELASSEMBLE"

    zip_octets = construire_zip(
        _post_reel(), [b"PNG"], "legende",
        telecharger=_telecharger_factice, assembler=assembler,
    )
    with zipfile.ZipFile(io.BytesIO(zip_octets)) as archive:
        noms = archive.namelist()
        assert any(n.endswith("_reel.mp4") for n in noms), noms
        assert archive.read("01_reel.mp4") == b"REELASSEMBLE"
    assert appels == [(b"PNG", b"MP4DATA")], "la cover et la video source sont passees telles quelles"


def test_la_video_source_n_est_pas_dupliquee_dans_le_zip():
    """Elle est deja DANS le reel : la remettre a part doublerait le poids et
    laisserait croire qu'il y a deux medias a publier."""
    zip_octets = construire_zip(
        _post_reel(), [b"PNG"], "legende",
        telecharger=_telecharger_factice, assembler=lambda c, v: b"REEL",
    )
    with zipfile.ZipFile(io.BytesIO(zip_octets)) as archive:
        assert not any(n.endswith("_video.mp4") for n in archive.namelist())


def test_un_assemblage_qui_echoue_laisse_un_zip_utilisable():
    """Meme philosophie que le reste du module : un export partiel vaut mieux
    qu'une erreur, surtout a 23 h. On retombe sur cover + video separees, et on
    NOMME l'echec."""
    def assembler_casse(cover, video):
        raise RuntimeError("ffmpeg est introuvable")

    zip_octets = construire_zip(
        _post_reel(), [b"PNG"], "legende",
        telecharger=_telecharger_factice, assembler=assembler_casse,
    )
    with zipfile.ZipFile(io.BytesIO(zip_octets)) as archive:
        noms = archive.namelist()
        assert not any(n.endswith("_reel.mp4") for n in noms)
        assert any(n.endswith("_video.mp4") for n in noms), "la video brute doit rester"
        assert "ffmpeg est introuvable" in archive.read("medias_manquants.txt").decode()


def test_un_post_normal_n_assemble_rien():
    """Le gabarit post ne produit pas de video : l'assembleur ne doit meme pas
    etre appele."""
    def assembler_interdit(cover, video):
        raise AssertionError("ne doit pas etre appele pour un post")

    construire_zip(
        _post_avec_medias(), [b"PNG"], "legende",
        telecharger=_telecharger_factice, assembler=assembler_interdit,
    )


class _ClientQuiEchoue:
    """Un client httpx qui ne sort jamais sur le reseau : il leve, comme le vrai."""

    def __init__(self, exception):
        self.exception = exception

    def stream(self, *args, **kwargs):
        raise self.exception

    def close(self):
        pass


def _reponse(code):
    return httpx.Response(code, request=httpx.Request("GET", "https://cdn/x.jpg"))


def test_un_echec_de_transport_est_signale_comme_passager():
    coupure = httpx.ConnectTimeout("delai depasse")
    _, raison = telecharger("https://cdn/x.jpg", client=_ClientQuiEchoue(coupure))
    assert echec_transitoire(raison)
    assert "ConnectTimeout" in raison


def test_un_5xx_et_un_429_sont_passagers_un_404_non():
    for code in (500, 503, 429):
        erreur = httpx.HTTPStatusError("oups", request=None, response=_reponse(code))
        _, raison = telecharger("https://cdn/x.jpg", client=_ClientQuiEchoue(erreur))
        assert echec_transitoire(raison), code
    erreur = httpx.HTTPStatusError("oups", request=None, response=_reponse(404))
    _, raison = telecharger("https://cdn/x.jpg", client=_ClientQuiEchoue(erreur))
    assert not echec_transitoire(raison)
    assert "404" in raison


def test_la_raison_d_un_media_absent_ne_montre_pas_le_drapeau_interne():
    """`medias_manquants.txt` est lu par un humain : le prefixe qui sert a
    classer les echecs en interne n'a rien a y faire."""
    from app.content.export import PREFIXE_TRANSPORT, raison_lisible

    assert raison_lisible(f"{PREFIXE_TRANSPORT}ConnectTimeout") == "ConnectTimeout"
    assert raison_lisible("HTTP 404") == "HTTP 404"
    assert raison_lisible(None) == ""

    post = SimpleNamespace(
        slides=[{"kind": "cover"}, {"kind": "media", "type": "photo", "url": "https://cdn/x.jpg"}],
        template="kinda_post", article_url=None, assets=None,
    )
    contenu = construire_zip(
        post, [], "legende",
        telecharger=lambda url: (None, f"{PREFIXE_TRANSPORT}ConnectTimeout"),
    )
    with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
        texte = archive.read("medias_manquants.txt").decode()
    assert "ConnectTimeout" in texte
    assert PREFIXE_TRANSPORT not in texte
