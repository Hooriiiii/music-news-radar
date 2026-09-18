"""La recherche web : dernier recours avant le fond Kinda.

« ne saute pas l'actu, cherche une image sur le web en dernier recours »
(utilisateur, 2026-09-18, apres un post publie sur l'aplat Kinda parce que tous
les candidats etaient des affiches de line-up).

Aucun reseau ici : le chercheur (l'outil de recherche web de l'API Anthropic) et
l'extraction d'`og:image` sont tous deux injectes.
"""

import datetime as dt
import itertools
import json

import pytest

from app.config import settings
from app.content import recherche_web
from app.content.recherche_web import (
    Candidat,
    attribution_de,
    candidats_du_web,
    domaine_de,
    indice_discipline,
    requete_pour,
)
from app.content.visuels import chemin_cache
from app.models import Article, Source, SourceCategorie, SourceType

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    chemin = tmp_path / "visuels_web.json"
    monkeypatch.setattr(settings, "visuels_web_cache_path", chemin)
    monkeypatch.setattr(settings, "visuels_web_enabled", True)
    return chemin


_numeros = itertools.count()


def _article(db, titre, artistes=(), resume=None, categorie=SourceCategorie.MEDIA):
    source = Source(name="Tsugi (RSS)", type=SourceType.RSS,
                    url="https://tsugi.example/feed", categorie=categorie)
    db.add(source)
    db.commit()
    n = next(_numeros)
    article = Article(source_id=source.id, url=f"https://tsugi.example/a{n}", title=titre,
                      raw_hash=f"h{n:063d}", summary=resume, published_at=MAINTENANT,
                      mentioned_artists=list(artistes) or None)
    db.add(article)
    db.commit()
    return article


class Chercheur:
    """L'outil de recherche web, en faux : rend des (url, titre) de pages."""

    def __init__(self, pages=(), erreur=None):
        self.pages, self.erreur, self.requetes = list(pages), erreur, []

    def __call__(self, requete):
        self.requetes.append(requete)
        if self.erreur is not None:
            raise self.erreur
        return list(self.pages)


class Extraction:
    """`og:image` d'une page, en faux."""

    def __init__(self, table=None, erreur=None):
        self.table, self.erreur, self.pages = dict(table or {}), erreur, []

    def __call__(self, url, **_):
        self.pages.append(url)
        if self.erreur is not None:
            raise self.erreur
        image = self.table.get(url)
        return {"type": "photo", "url": image} if image else None


# --- La requete ------------------------------------------------------------


def test_les_artistes_cites_font_la_requete(db_session):
    """Le nom d'un artiste est ce qui ramene une photo de lui ; le titre de
    l'actu, lui, ramene des captures d'ecran d'articles."""
    article = _article(db_session, "Les Transmusicales devoilent leur programmation",
                       artistes=["Fred again..", "Charlotte de Witte"])

    assert requete_pour(article).startswith("Fred again.. Charlotte de Witte")


def test_sans_artiste_cite_c_est_le_nom_de_l_evenement(db_session):
    """Le debut du titre, tant qu'il est en capitales : « Les Transmusicales »
    et non la phrase entiere, qui ne ramenerait que des reprises de l'article."""
    article = _article(db_session, "Les Transmusicales devoilent leur programmation 2026")

    assert requete_pour(article).startswith("Les Transmusicales")
    assert "devoilent" not in requete_pour(article)


def test_la_requete_porte_un_indice_de_discipline(db_session):
    """Sans lui, un nom d'artiste ramene surtout des pochettes et des portraits
    presse. La CATEGORIE de la source complete le texte : « Les Transmusicales
    devoilent leur programmation » ne porte pas le mot festival, et c'en est un."""
    festival = _article(db_session, "Les Transmusicales devoilent leur programmation",
                        categorie=SourceCategorie.FESTIVAL)
    club = _article(db_session, "Nico Moreno annonce un b2b au Rex Club")

    assert indice_discipline(festival) == "festival"
    assert indice_discipline(club) == "dj set"
    assert "festival" in requete_pour(festival)


def test_un_article_sans_titre_ni_artiste_n_a_pas_de_requete(db_session):
    article = _article(db_session, "")

    assert requete_pour(article) is None


# --- Les candidats ---------------------------------------------------------


def test_les_pages_trouvees_donnent_leur_og_image(db_session, cache):
    article = _article(db_session, "Charlotte de Witte en tournee", artistes=["Charlotte de Witte"])
    chercheur = Chercheur([("https://ra.co/news/1", "Charlotte de Witte")])
    extraire = Extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"})

    trouves = candidats_du_web(article, maintenant=MAINTENANT,
                               chercheur=chercheur, extraire=extraire)

    assert trouves == [Candidat(page="https://ra.co/news/1", titre="Charlotte de Witte",
                                image="https://ra.co/img/1.jpg", domaine="ra.co")]


def test_les_candidats_sont_bornes(db_session, cache, monkeypatch):
    """Chaque page est une requete reseau, chaque image des jetons de vision."""
    monkeypatch.setattr(settings, "visuels_web_max_candidats", 2)
    article = _article(db_session, "Charlotte de Witte", artistes=["Charlotte de Witte"])
    pages = [(f"https://ra.co/news/{n}", f"p{n}") for n in range(5)]
    extraire = Extraction({url: f"https://ra.co/img/{n}.jpg" for n, (url, _) in enumerate(pages)})

    trouves = candidats_du_web(article, maintenant=MAINTENANT,
                               chercheur=Chercheur(pages), extraire=extraire)

    assert len(trouves) == 2


def test_les_domaines_sans_photo_exploitable_sont_ecartes(db_session, cache):
    """Une banque d'images filigranee, un reseau social derriere un mur de
    connexion : leur `og:image` est un logo, jamais la photo de l'actu."""
    article = _article(db_session, "Charlotte de Witte", artistes=["Charlotte de Witte"])
    pages = [("https://www.gettyimages.com/photos/cdw", "Getty"),
             ("https://www.instagram.com/p/X/", "IG"),
             ("https://ra.co/news/1", "RA")]
    extraire = Extraction({url: f"{url}/og.jpg" for url, _ in pages})

    trouves = candidats_du_web(article, maintenant=MAINTENANT,
                               chercheur=Chercheur(pages), extraire=extraire)

    assert [c.domaine for c in trouves] == ["ra.co"]


def test_une_page_sans_og_image_est_sautee(db_session, cache):
    article = _article(db_session, "Charlotte de Witte", artistes=["Charlotte de Witte"])
    pages = [("https://muet.example/a", "muet"), ("https://ra.co/news/1", "RA")]

    trouves = candidats_du_web(
        article, maintenant=MAINTENANT, chercheur=Chercheur(pages),
        extraire=Extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"}),
    )

    assert [c.page for c in trouves] == ["https://ra.co/news/1"]


def test_une_recherche_en_panne_ne_leve_jamais(db_session, cache):
    """Degradation silencieuse : la cover partira sur le fond Kinda, la
    composition n'est jamais perdue pour une question d'image."""
    article = _article(db_session, "Charlotte de Witte", artistes=["Charlotte de Witte"])

    assert candidats_du_web(article, maintenant=MAINTENANT,
                            chercheur=Chercheur(erreur=RuntimeError("429")),
                            extraire=Extraction()) == []


def test_le_coupe_circuit_eteint_la_recherche(db_session, cache, monkeypatch):
    """`VISUELS_WEB_ENABLED=false` : retour exact au comportement d'avant."""
    monkeypatch.setattr(settings, "visuels_web_enabled", False)
    article = _article(db_session, "Charlotte de Witte", artistes=["Charlotte de Witte"])
    chercheur = Chercheur([("https://ra.co/news/1", "RA")])

    assert candidats_du_web(article, maintenant=MAINTENANT, chercheur=chercheur,
                            extraire=Extraction()) == []
    assert chercheur.requetes == []


# --- Le cache par requete --------------------------------------------------


def test_une_meme_requete_ne_cherche_qu_une_fois(db_session, cache):
    """Une recherche web est facturee, et un article peut se recomposer a chaque
    passage tant que son creneau n'est pas gele."""
    article = _article(db_session, "Charlotte de Witte", artistes=["Charlotte de Witte"])
    chercheur = Chercheur([("https://ra.co/news/1", "RA")])
    extraire = Extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"})

    premiers = candidats_du_web(article, maintenant=MAINTENANT,
                                chercheur=chercheur, extraire=extraire)
    seconds = candidats_du_web(article, maintenant=MAINTENANT + dt.timedelta(hours=2),
                               chercheur=chercheur, extraire=extraire)

    assert premiers == seconds
    assert len(chercheur.requetes) == 1
    assert len(extraire.pages) == 1


def test_le_cache_expire(db_session, cache):
    article = _article(db_session, "Charlotte de Witte", artistes=["Charlotte de Witte"])
    chercheur = Chercheur([("https://ra.co/news/1", "RA")])
    extraire = Extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"})

    candidats_du_web(article, maintenant=MAINTENANT, chercheur=chercheur, extraire=extraire)
    plus_tard = MAINTENANT + dt.timedelta(days=settings.visuels_web_cache_jours + 1)
    candidats_du_web(article, maintenant=plus_tard, chercheur=chercheur, extraire=extraire)

    assert len(chercheur.requetes) == 2


def test_un_cache_illisible_vaut_un_cache_vide(db_session, cache):
    cache.write_text("{ ceci n'est pas du JSON", encoding="utf-8")
    article = _article(db_session, "Charlotte de Witte", artistes=["Charlotte de Witte"])

    trouves = candidats_du_web(
        article, maintenant=MAINTENANT, chercheur=Chercheur([("https://ra.co/n/1", "RA")]),
        extraire=Extraction({"https://ra.co/n/1": "https://ra.co/img/1.jpg"}),
    )

    assert len(trouves) == 1
    assert json.loads(cache.read_text(encoding="utf-8"))["version"] == recherche_web.CACHE_VERSION


# --- Le credit -------------------------------------------------------------


def test_le_credit_est_le_domaine_de_la_page(db_session):
    assert domaine_de("https://www.resident-advisor.net/news/1") == "resident-advisor.net"


def test_l_attribution_se_retrouve_depuis_la_copie_locale(db_session, cache):
    """Le fond publie est un `file://` du cache ; le credit doit pouvoir etre
    reconstitue a partir de lui seul, y compris apres un redemarrage."""
    article = _article(db_session, "Charlotte de Witte", artistes=["Charlotte de Witte"])
    candidats_du_web(
        article, maintenant=MAINTENANT, chercheur=Chercheur([("https://ra.co/n/1", "RA")]),
        extraire=Extraction({"https://ra.co/n/1": "https://ra.co/img/1.jpg"}),
    )

    uri = chemin_cache("https://ra.co/img/1.jpg").as_uri()
    trouve = attribution_de(uri, maintenant=MAINTENANT)

    assert trouve is not None
    assert trouve.domaine == "ra.co"
    assert trouve.page == "https://ra.co/n/1"


def test_une_copie_inconnue_n_a_pas_d_attribution(cache):
    assert attribution_de("file:///ailleurs/abc.jpg", maintenant=MAINTENANT) is None
