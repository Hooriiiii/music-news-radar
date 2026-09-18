"""Le web comme DERNIER recours dans le choix des visuels.

« ne saute pas l'actu, cherche une image sur le web en dernier recours »
(utilisateur, 2026-09-18). Ce fichier verifie l'ordre de l'echelle (le web n'est
ouvert qu'apres tout le reste), le credit obligatoire, et les deux issues d'un
article qui n'avait rien : il est desormais composable au lieu d'etre exclu.

Aucun reseau : chercheur, telechargement et juge sont injectes.
"""

import datetime as dt
import io
import itertools

import pytest
from PIL import Image

from app.config import settings
from app.content.choix_visuels import AucunVisuel, actif_du_web, choisir_visuels, photos_du_web
from app.content.compose import actifs_de_secours
from app.content.illustration import Fond, Lot, Verdict, eviter_l_affiche
from app.content.legende import texte_legende
from app.content.media import entree
from app.content.visuels import ORIGINE_ARTISTE, ORIGINE_COMPTE, ORIGINE_KINDA, ORIGINE_WEB
from app.models import Article, Source, SourceCategorie, SourceType

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
_numeros = itertools.count()

AFFICHE = Verdict(affiche=True, sujet="autre", note=15)
PHOTO = Verdict(affiche=False, sujet="scene", note=80)


@pytest.fixture(autouse=True)
def web_allume(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "visuels_web_enabled", True)
    monkeypatch.setattr(settings, "visuels_web_cache_path", tmp_path / "web.json")


def _image():
    tampon = io.BytesIO()
    Image.new("RGB", (900, 1200), (7, 7, 7)).save(tampon, "JPEG")
    return tampon.getvalue()


def _telecharger(url):
    return _image(), ".jpg"


def _source(db, nom="Tsugi (RSS)", type_=SourceType.RSS, categorie=SourceCategorie.MEDIA):
    source = Source(name=nom, type=type_, url=f"https://{nom[:4].lower()}.example/feed",
                    categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre="Charlotte de Witte annonce une date a Rennes",
             media=(), artistes=("Charlotte de Witte",)):
    n = next(_numeros)
    article = Article(source_id=source.id, url=f"https://tsugi.example/{n}", title=titre,
                      raw_hash=f"w{n:063d}", published_at=MAINTENANT, media=list(media),
                      mentioned_artists=list(artistes) or None)
    db.add(article)
    db.commit()
    return article


class Chercheur:
    def __init__(self, pages=(("https://ra.co/news/1", "RA"),)):
        self.pages, self.requetes = list(pages), []

    def __call__(self, requete):
        self.requetes.append(requete)
        return list(self.pages)


def _extraction(table):
    def extraire(url, **_):
        image = table.get(url)
        return {"type": "photo", "url": image} if image else None

    return extraire


def _visuels(db, article, **options):
    options.setdefault("telecharger", _telecharger)
    return choisir_visuels(db, article, maintenant=MAINTENANT, **options)


# --- L'article n'est plus saute -------------------------------------------


def test_un_article_sans_rien_trouve_desormais_une_image(db_session, monkeypatch):
    """Le cas qui a declenche la demande : ni media, ni compte source Instagram,
    ni artiste suivi cite. Avant, `AucunVisuel` -- l'article sortait de la file."""
    monkeypatch.setattr("app.content.recherche_web.image_de_partage",
                        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"}))
    article = _article(db_session, _source(db_session))

    choix = _visuels(db_session, article, chercheur_web=Chercheur())

    assert [a["origine"] for a in choix.actifs] == [ORIGINE_WEB]
    assert choix.actifs[0]["fichier"].startswith("file://")


def test_sans_rien_sur_le_web_non_plus_l_article_sort_de_la_file(db_session, monkeypatch):
    """Le web n'est pas une garantie : quand il ne donne rien non plus, le
    comportement d'avant revient mot pour mot."""
    monkeypatch.setattr("app.content.recherche_web.image_de_partage", _extraction({}))
    article = _article(db_session, _source(db_session))

    with pytest.raises(AucunVisuel):
        _visuels(db_session, article, chercheur_web=Chercheur())


def test_le_web_reste_ferme_quand_l_article_a_sa_propre_image(db_session):
    """L'ordre de l'echelle : une recherche est payee, les medias de l'actu sont
    gratuits ET meilleurs -- ce sont ceux de l'actu."""
    chercheur = Chercheur()
    article = _article(db_session, _source(db_session),
                       media=[entree("photo", "https://tsugi.example/og.jpg")])

    choix = _visuels(db_session, article, chercheur_web=chercheur)

    assert chercheur.requetes == []
    assert [a["origine"] for a in choix.actifs] == ["article"]


# --- Le credit -------------------------------------------------------------


def test_une_image_du_web_est_creditee_par_son_domaine(db_session, monkeypatch):
    monkeypatch.setattr("app.content.recherche_web.image_de_partage",
                        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"}))
    article = _article(db_session, _source(db_session))

    choix = _visuels(db_session, article, chercheur_web=Chercheur())

    assert choix.credit == "ra.co"
    assert choix.actifs[0]["permalink"] == "https://ra.co/news/1"


def test_le_credit_du_web_apparait_dans_la_legende(db_session, monkeypatch):
    """« 📸 ra.co », sans « via » et sans ligne « Source : » -- les regles de
    credit du 2026-09-17 valent aussi pour une image du web."""
    monkeypatch.setattr("app.content.recherche_web.image_de_partage",
                        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"}))
    article = _article(db_session, _source(db_session))
    choix = _visuels(db_session, article, chercheur_web=Chercheur())

    class Faux:
        caption, hashtags, template = "Une actu.", ["techno"], "kinda_post"
        credit = choix.credit

    texte = texte_legende(Faux())

    assert "📸 ra.co" in texte
    assert "via" not in texte and "Source :" not in texte


def test_une_photo_du_web_retenue_en_secours_reste_creditee(db_session, monkeypatch):
    """Le fond venu d'une RESERVE arrive apres le choix des visuels : sans ce
    rattrapage, l'image serait publiee sans dire d'ou elle vient."""
    monkeypatch.setattr("app.content.recherche_web.image_de_partage",
                        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"}))
    fichiers = photos_du_web(_article(db_session, _source(db_session)), maintenant=MAINTENANT,
                             telecharger=_telecharger, chercheur=Chercheur())
    choisi = Fond(fichiers[0], ORIGINE_WEB, tuple(fichiers))

    nouveaux = actifs_de_secours(choisi, [])

    assert nouveaux[0]["compte"] == "ra.co"
    assert nouveaux[0]["origine"] == ORIGINE_WEB


# --- La reserve, dans l'ordre ---------------------------------------------


def test_le_lot_du_web_est_le_dernier_avant_le_fond_kinda():
    ouverts = []

    def _lot(origine, urls):
        def photos():
            ouverts.append(origine)
            return list(urls)

        return Lot(origine, photos)

    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        _lot(ORIGINE_COMPTE, ["file:///cache/affiche2.jpg"]),
        _lot(ORIGINE_ARTISTE, []),
        _lot(ORIGINE_WEB, ["file:///cache/web.jpg"]),
        verdicts=lambda urls: {
            "https://cdn.ig/affiche.jpg": AFFICHE,
            "file:///cache/affiche2.jpg": AFFICHE,
            "file:///cache/web.jpg": PHOTO,
        },
    )

    assert fond == Fond("file:///cache/web.jpg", ORIGINE_WEB)
    assert ouverts == [ORIGINE_COMPTE, ORIGINE_ARTISTE, ORIGINE_WEB]


def test_le_web_muet_laisse_la_cover_sur_le_fond_kinda():
    """Mieux vaut le fond Kinda qu'une affiche, meme quand le web a ete essaye."""
    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        Lot(ORIGINE_WEB, lambda: []),
        verdicts=lambda urls: {"https://cdn.ig/affiche.jpg": AFFICHE},
    )

    assert fond.origine == ORIGINE_KINDA


# --- Le reel ---------------------------------------------------------------


def test_le_web_ne_sert_jamais_la_video_d_un_reel(db_session, monkeypatch):
    """Il peut porter le FOND de la cover, jamais la video : une image du web ne
    remplace pas une video, et un reel sans video n'existe pas."""
    monkeypatch.setattr("app.content.recherche_web.image_de_partage",
                        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"}))
    from app.models import FormatCreneau

    article = _article(db_session, _source(db_session))

    with pytest.raises(AucunVisuel):
        _visuels(db_session, article, format_=FormatCreneau.REEL, chercheur_web=Chercheur())


def test_l_attribution_survit_a_la_copie_locale(db_session, monkeypatch):
    """`actif_du_web` reconstitue le credit depuis le seul `file://` : c'est ce
    qui le rend disponible a la composition, qui vient apres le choix."""
    monkeypatch.setattr("app.content.recherche_web.image_de_partage",
                        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"}))
    fichiers = photos_du_web(_article(db_session, _source(db_session)), maintenant=MAINTENANT,
                             telecharger=_telecharger, chercheur=Chercheur())

    assert actif_du_web(fichiers[0], maintenant=MAINTENANT)["compte"] == "ra.co"
