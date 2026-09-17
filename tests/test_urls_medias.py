"""Les URL de medias ne portent jamais d'entites HTML.

Le 2026-09-17, le reel de 20:24 (post 29, article 17974) est parti sur le fond de
secours du gabarit : l'URL du CDN Instagram stockee dans `articles.media[0].url`
portait `&amp;` a la place de `&` (`..._nc_cat=105&amp;ccb=7-5...`). Chromium
demandait cette URL telle quelle, le CDN la refusait, et `fetch_image_data_uri`
retombait en silence sur `fallback.jpg`.

Deux etages, testes ici : le nettoyage A L'INGESTION (toute entree de media passe
par `entree()`), et la ceinture A L'USAGE (rendu, export, gel, cache des visuels)
-- une ligne deja en base ne doit pas repartir en publication avec son `&amp;`.
"""

import httpx
import pytest

from app.content import export, render, visuels
from app.content.media import PHOTO, VIDEO, entree
from app.content.urls import url_propre, url_telechargeable
from app.pipeline import illustrations
from app.publish import preparation
from app.sources.instagram import _media as media_instagram
from app.sources.rss import RssAdapter

SALE = "https://cdn.example/a.jpg?_nc_cat=105&amp;ccb=7-5&amp;oh=00"
PROPRE = "https://cdn.example/a.jpg?_nc_cat=105&ccb=7-5&oh=00"


# --- Le vocabulaire ----------------------------------------------------------


def test_url_propre_desechappe_les_entites():
    assert url_propre(SALE) == PROPRE


def test_url_propre_desechappe_jusqu_au_point_fixe():
    """Un double echappement (`&amp;amp;`) vient d'un HTML passe deux fois dans un
    echappeur : une seule passe laisserait `&amp;`."""
    assert url_propre("https://x/a?b=1&amp;amp;c=2") == "https://x/a?b=1&c=2"


@pytest.mark.parametrize("valeur", [None, "", "   "])
def test_url_propre_rend_none_sur_du_vide(valeur):
    assert url_propre(valeur) is None


def test_url_propre_decode_aussi_les_entites_numeriques():
    assert url_propre("https://x/a?b=1&#38;c=2") == "https://x/a?b=1&c=2"


@pytest.mark.parametrize(
    "valeur",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "/relative/a.jpg",
        "data:image/jpeg;base64,AAA",
    ],
)
def test_url_telechargeable_refuse_ce_qui_n_est_pas_http(valeur):
    assert url_telechargeable(valeur) is None


def test_url_telechargeable_rend_l_url_nettoyee():
    assert url_telechargeable(f"  {SALE}  ") == PROPRE


# --- Etage 1 : l'ingestion ---------------------------------------------------


def test_entree_nettoie_url_et_preview():
    e = entree(VIDEO, SALE, preview_url=SALE)
    assert (e["url"], e["preview_url"]) == (PROPRE, PROPRE)


def test_un_img_du_html_rss_arrive_nettoye():
    """C'est le chemin le plus exposé : un attribut HTML porte `&amp;` par norme."""
    flux = f"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Actu</title><link>https://media/1</link>
      <description>&lt;img src="{SALE}" width="600"&gt;</description></item>
    </channel></rss>""".encode()
    items = RssAdapter.parse(RssAdapter.__new__(RssAdapter), flux)
    assert items[0].media[0]["url"] == PROPRE


def test_une_og_image_echappee_arrive_nettoyee():
    page = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text=f'<meta property="og:image" content="{SALE}">',
    )
    trouvee = illustrations.image_de_partage("https://editeur/a", get=lambda *a, **k: page)
    assert trouvee["url"] == PROPRE


def test_un_media_instagram_arrive_nettoye():
    noeud = {"media_type": "IMAGE", "media_url": SALE, "thumbnail_url": SALE}
    assert media_instagram(noeud) == [entree(PHOTO, PROPRE, preview_url=PROPRE)]


# --- Etage 2 : l'usage -------------------------------------------------------


def test_le_rendu_demande_l_url_nettoyee(monkeypatch):
    demandees = []

    def faux_get(url, **_):
        demandees.append(url)
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"jpg")

    monkeypatch.setattr(render.httpx, "get", faux_get)
    uri = render.fetch_image_data_uri(SALE)
    assert demandees == [PROPRE]
    assert uri.startswith("data:image/jpeg;base64,")


def test_le_rendu_ne_va_jamais_chercher_une_url_hors_http(monkeypatch):
    def jamais(*_a, **_k):  # pragma: no cover -- doit ne pas etre appele
        raise AssertionError("aucune requete ne doit partir")

    monkeypatch.setattr(render.httpx, "get", jamais)
    assert render.fetch_image_data_uri("javascript:alert(1)").startswith("data:image/")


def test_l_export_telecharge_l_url_nettoyee():
    demandees = []

    class FauxClient:
        def stream(self, _methode, url, **_):
            demandees.append(url)
            return _Reponse()

    class _Reponse:
        headers = {"content-type": "image/jpeg"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"jpg"

    contenu, extension = export.telecharger(SALE, client=FauxClient())
    assert (demandees, contenu, extension) == ([PROPRE], b"jpg", ".jpg")


def test_l_export_refuse_une_url_hors_http_sans_requete():
    class Interdit:
        def stream(self, *_a, **_k):  # pragma: no cover -- doit ne pas etre appele
            raise AssertionError("aucune requete ne doit partir")

    contenu, raison = export.telecharger("javascript:alert(1)", client=Interdit())
    assert contenu is None and "URL" in raison


def test_le_gel_telecharge_la_photo_nettoyee():
    demandees = []

    def faux_telecharger(url):
        demandees.append(url)
        return b"jpg", ".jpg"

    assert preparation._octets_image(SALE, faux_telecharger) == b"jpg"
    assert demandees == [PROPRE]


def test_le_gel_telecharge_la_video_nettoyee():
    demandees = []

    def faux_telecharger_video(url, _plafond):
        demandees.append(url)
        raise RuntimeError("assez : on ne veut que l'URL demandee")

    preparation._video_de_carrousel(
        {"type": VIDEO, "url": SALE}, faux_telecharger_video, None, None
    )
    assert demandees == [PROPRE]


def test_l_image_d_un_actif_est_nettoyee():
    assert visuels.image_de({"type": PHOTO, "url": SALE}) == PROPRE
    assert visuels.image_de({"type": VIDEO, "url": None, "preview_url": SALE}) == PROPRE


def test_le_cache_des_visuels_est_indexe_sur_l_url_nettoyee():
    """Sinon la meme image occuperait deux copies, et la copie posee depuis une
    URL echappee ne serait jamais retrouvee depuis l'URL propre."""
    assert visuels.chemin_cache(SALE) == visuels.chemin_cache(PROPRE)


def test_une_url_interdite_l_est_sous_ses_deux_formes():
    """Les lignes deja en base portent l'URL echappee, les nouvelles la propre :
    la ceinture « visuel d'un compte media » doit reconnaitre les deux."""

    class Source:
        type = None
        categorie = None

    class Article:
        source = Source()
        media = [{"url": SALE, "preview_url": None}]

    from app.models import SourceCategorie, SourceType

    Source.type, Source.categorie = SourceType.INSTAGRAM, SourceCategorie.MEDIA
    assert visuels.urls_interdites(Article()) == {SALE, PROPRE}


# --- Le fond litteralement a « None » ----------------------------------------
#
# Des brouillons de production (posts 34 et 35, article 17974) portent
# `slides[0].image_url == "None"` : un `str(None)` ecrit a la place d'un vrai
# vide. Chaine non vide, elle passe toutes les gardes « pas de fond » et le rendu
# retombe en silence sur `fallback.jpg` -- exactement le bug du 2026-09-17.


def test_un_fond_a_none_est_lu_comme_absent():
    assert url_propre("None") is None
    assert url_propre("none") is None
    assert url_telechargeable("None") is None


def test_le_rendu_ne_va_jamais_chercher_l_url_none(monkeypatch):
    def interdit(*args, **kwargs):  # pragma: no cover -- doit ne jamais servir
        raise AssertionError("aucune requete pour un fond a « None »")

    monkeypatch.setattr(render.httpx, "get", interdit)
    assert render.fetch_image_data_uri("None").startswith("data:image/jpeg;base64,")


def test_le_gel_refuse_un_fond_a_none(tmp_path):
    from types import SimpleNamespace

    def interdit(*args, **kwargs):  # pragma: no cover -- doit ne jamais servir
        raise AssertionError("ni rendu ni telechargement sans fond")

    post = SimpleNamespace(slides=[{"kind": "cover", "image_url": "None"}], assets=[])
    with pytest.raises(preparation.PreparationImpossible, match="aucun fond de cover"):
        preparation.preparer_fichiers(post, None, tmp_path, rendre=interdit, telecharger=interdit)
