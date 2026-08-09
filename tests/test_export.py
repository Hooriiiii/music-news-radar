import io
import zipfile
from types import SimpleNamespace

from app.content.export import PLAFOND_OCTETS, construire_zip, telecharger


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
