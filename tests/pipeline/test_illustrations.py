"""L'illustration des articles : l'image que l'editeur designe pour le partage."""

import io

import httpx
from PIL import Image

from app.config import settings
from app.models import Article, Source, SourceType
from app.pipeline.illustrations import (
    TAILLE_MAX,
    enrichir_articles,
    image_de_partage,
)

PAGE = '''<html><head>
<meta property="og:image" content="https://cdn.tsugi.fr/photo.jpg">
<meta name="twitter:image" content="https://cdn.tsugi.fr/autre.jpg">
</head><body>x</body></html>'''


def _reponse(texte="", code=200, content_type="text/html"):
    return httpx.Response(
        code, text=texte, headers={"content-type": content_type},
        request=httpx.Request("GET", "https://x"),
    )


def _jpeg(largeur: int, hauteur: int) -> bytes:
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (10, 20, 30)).save(tampon, format="JPEG")
    return tampon.getvalue()


def _reponse_image(octets: bytes, code: int = 200) -> httpx.Response:
    return httpx.Response(
        code, content=octets, headers={"content-type": "image/jpeg"},
        request=httpx.Request("GET", "https://x"),
    )


def test_og_image_est_extraite(monkeypatch):
    assert image_de_partage("https://t.fr/a", get=lambda u, **k: _reponse(PAGE))["url"] == (
        "https://cdn.tsugi.fr/photo.jpg"
    )


def test_l_entree_a_la_forme_complete_des_medias():
    """Les six cles toujours presentes : un consommateur ne doit pas deviner
    lesquelles existent selon la source (cf. app/content/media.py)."""
    entree = image_de_partage("https://t.fr/a", get=lambda u, **k: _reponse(PAGE))
    assert set(entree) == {"type", "url", "preview_url", "duration_ms", "width", "height"}
    assert entree["type"] == "photo"


def test_repli_sur_twitter_image_quand_og_absente():
    page = '<meta name="twitter:image" content="https://cdn.fr/tw.jpg">'
    assert image_de_partage("https://t.fr/a", get=lambda u, **k: _reponse(page))["url"] == (
        "https://cdn.fr/tw.jpg"
    )


def test_ordre_des_attributs_indifferent():
    """`content` peut preceder `property` : une regex trop rigide raterait la
    moitie des sites."""
    page = '<meta content="https://cdn.fr/x.jpg" property="og:image">'
    assert image_de_partage("https://t.fr/a", get=lambda u, **k: _reponse(page))["url"] == (
        "https://cdn.fr/x.jpg"
    )


def test_url_relative_resolue_sur_la_page():
    page = '<meta property="og:image" content="/img/a.jpg">'
    assert image_de_partage("https://t.fr/actu/1", get=lambda u, **k: _reponse(page))["url"] == (
        "https://t.fr/img/a.jpg"
    )


def test_page_sans_image_rend_None():
    assert image_de_partage("https://t.fr/a", get=lambda u, **k: _reponse("<html></html>")) is None


def test_une_erreur_reseau_rend_None_sans_lever():
    """Illustrer est un CONFORT : un site injoignable ne doit jamais casser le
    pipeline qui l'appelle."""
    def get_casse(url, **kwargs):
        raise httpx.ConnectError("injoignable")

    assert image_de_partage("https://t.fr/a", get=get_casse) is None


def test_une_reponse_non_html_est_ignoree():
    """Inutile de passer une regex sur un PDF ou une image de 8 Mo."""
    assert image_de_partage(
        "https://t.fr/a.pdf", get=lambda u, **k: _reponse("%PDF", content_type="application/pdf")
    ) is None


def _article(db, relevance, media=None, url="https://t.fr/a"):
    src = db.query(Source).first()
    if src is None:
        src = Source(name="Tsugi", type=SourceType.RSS, url="https://t.fr/feed")
        db.add(src)
        db.commit()
    a = Article(
        source_id=src.id, url=url, title="titre", raw_hash=url,
        relevance_score=relevance, media=media,
    )
    db.add(a)
    db.commit()
    return a


def test_seuls_les_articles_pertinents_sont_illustres(db_session):
    """Un article de bruit ne sera jamais poste : lui chercher une image, c'est
    payer une requete reseau pour rien."""
    pertinent = _article(db_session, 75, url="https://t.fr/oui")
    bruit = _article(db_session, 20, url="https://t.fr/non")

    vus = []

    def get(url, **kwargs):
        vus.append(url)
        return _reponse(PAGE)

    stats = enrichir_articles(db_session, get=get)

    assert vus == ["https://t.fr/oui"]
    assert pertinent.media[0]["url"] == "https://cdn.tsugi.fr/photo.jpg"
    assert bruit.media is None
    assert stats.illustres == 1


def test_un_article_qui_a_deja_un_media_est_laisse_tranquille(db_session):
    deja = _article(db_session, 90, media=[{"type": "video", "url": "https://v.mp4"}])
    enrichir_articles(db_session, get=lambda u, **k: _reponse(PAGE))
    assert deja.media[0]["url"] == "https://v.mp4"


def test_un_article_non_score_est_ignore(db_session):
    """Pas encore juge : on ne sait pas s'il merite une requete."""
    a = _article(db_session, None)
    enrichir_articles(db_session, get=lambda u, **k: _reponse(PAGE))
    assert a.media is None


def test_un_echec_n_empeche_pas_les_suivants(db_session):
    _article(db_session, 80, url="https://t.fr/casse")
    _article(db_session, 80, url="https://t.fr/ok")

    def get(url, **kwargs):
        if "casse" in url:
            raise httpx.ConnectError("nope")
        return _reponse(PAGE)

    stats = enrichir_articles(db_session, get=get)
    assert stats.illustres == 1
    assert stats.echecs == 1


def test_le_plafond_borne_les_requetes(db_session):
    """Un backfill ne doit pas partir sur des milliers de requetes d'un coup."""
    for i in range(5):
        _article(db_session, 80, url=f"https://t.fr/{i}")
    stats = enrichir_articles(db_session, get=lambda u, **k: _reponse(PAGE), limite=2)
    assert stats.illustres == 2


def test_le_seuil_vient_de_la_config(db_session, monkeypatch):
    monkeypatch.setattr(settings, "illustration_min_relevance", 90)
    _article(db_session, 80, url="https://t.fr/sous-le-seuil")
    stats = enrichir_articles(db_session, get=lambda u, **k: _reponse(PAGE))
    assert stats.illustres == 0


def test_une_url_google_news_est_ignoree_sans_requete(db_session):
    """Les liens Google News ne menent PAS a l'article : ils atterrissent sur la
    page de consentement de Google, qui n'a evidemment pas d'og:image. Verifie
    en vrai. Depenser une requete dessus est certain d'echouer."""
    _article(db_session, 85, url="https://news.google.com/rss/articles/CBMiX0FV")
    vus = []
    stats = enrichir_articles(db_session, get=lambda u, **k: vus.append(u) or _reponse(PAGE))
    assert vus == [], "aucune requete ne doit partir"
    assert stats.ignores == 1
    assert stats.echecs == 0, "ce n'est pas un echec mais une impossibilite connue"


# --- Variante pleine resolution (2026-09-21) --------------------------------
# Mesure sur le post 71 (docs/reference/generation-de-posts.md) : `?w=564` rendait 564x345 (50 Ko) une
# image dont la vraie taille, sans ce parametre, est 2156x1320 (386 Ko).

PAGE_REDIMENSIONNEE = '''<html><head>
<meta property="og:image"
  content="https://lede-admin.stereogum.com/wp-content/uploads/photo.png?w=564">
</head><body>x</body></html>'''

URL_PETITE = "https://lede-admin.stereogum.com/wp-content/uploads/photo.png?w=564"
URL_PLEINE = "https://lede-admin.stereogum.com/wp-content/uploads/photo.png"


def test_une_og_image_redimensionnee_demande_la_variante_pleine_resolution(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "illustration_variante_cache_path", tmp_path / "v.json")
    demandes = []

    def get(url, **kwargs):
        demandes.append(url)
        if url == "https://t.fr/a":
            return _reponse(PAGE_REDIMENSIONNEE)
        return _reponse_image(_jpeg(2156, 1320))

    entree = image_de_partage("https://t.fr/a", get=get)

    assert entree["url"] == URL_PLEINE
    assert demandes == ["https://t.fr/a", URL_PLEINE]


def test_la_variante_est_gardee_seulement_si_reellement_plus_grande(tmp_path, monkeypatch):
    """Un editeur peut ignorer le parametre et servir la MEME image quel que
    soit `w` : la comparaison doit etre reelle, pas deduite de l'URL."""
    monkeypatch.setattr(settings, "illustration_variante_cache_path", tmp_path / "v.json")

    def get(url, **kwargs):
        if url == "https://t.fr/a":
            return _reponse(PAGE_REDIMENSIONNEE)
        return _reponse_image(_jpeg(564, 345))  # meme taille que celle declaree

    entree = image_de_partage("https://t.fr/a", get=get)

    assert entree["url"] == URL_PETITE


def test_une_variante_injoignable_garde_l_url_d_origine(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "illustration_variante_cache_path", tmp_path / "v.json")

    def get(url, **kwargs):
        if url == "https://t.fr/a":
            return _reponse(PAGE_REDIMENSIONNEE)
        return _reponse(code=404)

    entree = image_de_partage("https://t.fr/a", get=get)

    assert entree["url"] == URL_PETITE


def test_une_og_image_sans_parametre_ne_declenche_aucune_requete_de_plus(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "illustration_variante_cache_path", tmp_path / "v.json")
    demandes = []

    def get(url, **kwargs):
        demandes.append(url)
        return _reponse(PAGE)

    entree = image_de_partage("https://t.fr/a", get=get)

    assert entree["url"] == "https://cdn.tsugi.fr/photo.jpg"
    assert demandes == ["https://t.fr/a"], "une seule requete : la page elle-meme"


def test_la_variante_retenue_est_memorisee(tmp_path, monkeypatch):
    """Un second passage sur le meme og:image ne repaie pas la requete de
    verification tant que le cache n'a pas expire."""
    monkeypatch.setattr(settings, "illustration_variante_cache_path", tmp_path / "v.json")
    demandes = []

    def get(url, **kwargs):
        demandes.append(url)
        if url == "https://t.fr/a":
            return _reponse(PAGE_REDIMENSIONNEE)
        return _reponse_image(_jpeg(2156, 1320))

    image_de_partage("https://t.fr/a", get=get)
    assert demandes == ["https://t.fr/a", URL_PLEINE]

    entree = image_de_partage("https://t.fr/a", get=get)

    assert entree["url"] == URL_PLEINE
    assert demandes == ["https://t.fr/a", URL_PLEINE, "https://t.fr/a"], (
        "la page est relue (og:image peut avoir change), mais pas la variante"
    )


# --- Variante : telechargement borne, jamais d'exception (revue I-7, M-5) ---


def test_la_variante_est_demandee_par_une_plage_bornee(tmp_path, monkeypatch):
    """Un master d'editeur de plusieurs Mo, telecharge en entier juste pour lire
    des dimensions, bloquait un run (40 illustrations par passage, plus les
    appels depuis `recherche_web`). La requete porte une plage bornee au
    plafond deja en vigueur dans le module (`TAILLE_MAX`)."""
    monkeypatch.setattr(settings, "illustration_variante_cache_path", tmp_path / "v.json")
    entetes = {}

    def get(url, **kwargs):
        if url == "https://t.fr/a":
            return _reponse(PAGE_REDIMENSIONNEE)
        entetes.update(kwargs.get("headers") or {})
        return _reponse_image(_jpeg(2156, 1320))

    image_de_partage("https://t.fr/a", get=get)

    assert entetes["Range"] == f"bytes=0-{TAILLE_MAX - 1}"


def test_une_variante_plus_lourde_que_le_plafond_garde_l_url_d_origine(tmp_path, monkeypatch):
    """Serveur qui ignore la plage (200, corps entier) : au-dela du plafond, la
    variante n'est pas retenue -- elle serait sinon retelechargee a chaque
    composition et a chaque gel."""
    monkeypatch.setattr(settings, "illustration_variante_cache_path", tmp_path / "v.json")

    def get(url, **kwargs):
        if url == "https://t.fr/a":
            return _reponse(PAGE_REDIMENSIONNEE)
        # Le corps annonce plus que le plafond : c'est l'en-tete qui fait foi,
        # sans avoir a fabriquer un JPEG de 2 Mo dans le test.
        return httpx.Response(
            200, content=_jpeg(2156, 1320),
            headers={"content-type": "image/jpeg", "content-length": str(TAILLE_MAX + 1)},
            request=httpx.Request("GET", "https://x"),
        )

    assert image_de_partage("https://t.fr/a", get=get)["url"] == URL_PETITE


def test_une_reponse_partielle_suffit_a_lire_les_dimensions(tmp_path, monkeypatch):
    """Serveur qui honore la plage (206) : l'en-tete de l'image porte ses
    dimensions, le reste n'est jamais transfere."""
    monkeypatch.setattr(settings, "illustration_variante_cache_path", tmp_path / "v.json")
    complete = _jpeg(2156, 1320)

    def get(url, **kwargs):
        if url == "https://t.fr/a":
            return _reponse(PAGE_REDIMENSIONNEE)
        return httpx.Response(
            206, content=complete[:1000],
            headers={"content-type": "image/jpeg",
                     "content-range": f"bytes 0-999/{len(complete)}"},
            request=httpx.Request("GET", "https://x"),
        )

    assert image_de_partage("https://t.fr/a", get=get)["url"] == URL_PLEINE


def test_une_url_pathologique_ne_fait_jamais_lever_image_de_partage(tmp_path, monkeypatch):
    """`_sans_parametres_taille` decoupait l'URL HORS du `try` : une URL que
    `urlsplit` refuse (port invalide) levait depuis `image_de_partage`, un
    module qui ne doit jamais lever."""
    monkeypatch.setattr(settings, "illustration_variante_cache_path", tmp_path / "v.json")
    page = '<meta property="og:image" content="http://[::1/img.jpg?w=564">'

    entree = image_de_partage("https://t.fr/a", get=lambda u, **k: _reponse(page))

    assert entree is None or entree["url"].endswith("?w=564")
