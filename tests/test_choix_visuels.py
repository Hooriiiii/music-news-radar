"""Le choix des visuels : propres s'ils sont autorises, sinon chez les artistes
cites (base, puis lecture en direct pour un carrousel), copies en local."""

import datetime as dt
import io
import itertools

import pytest
from PIL import Image

from app.content.actifs_artistes import PostArtiste
from app.content.choix_visuels import AucunVisuel, VisuelsDifferes, choisir_visuels
from app.content.export import PREFIXE_TRANSPORT
from app.content.lecture_artistes import LectureDifferee
from app.content.media import entree
from app.config import settings
from app.models import Article, FormatCreneau, Source, SourceCategorie, SourceType
from app.pipeline.comptes_artistes import CompteArtiste, IndexComptes, cle_artiste, construire_index

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
DATE_ACTU = MAINTENANT - dt.timedelta(hours=5)
TITRE = "GTA VI soundtrack : Fred again.. au generique"
_numeros = itertools.count()


def _image():
    tampon = io.BytesIO()
    Image.new("RGB", (1200, 800), (1, 2, 3)).save(tampon, "JPEG")
    return tampon.getvalue()


def _source(db, nom, handle=None, categorie=SourceCategorie.ARTISTE, type_=SourceType.INSTAGRAM):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{nom}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre=TITRE, media=(), mentioned=None, date=DATE_ACTU, url=None):
    n = next(_numeros)
    article = Article(source_id=source.id, url=url or f"https://www.instagram.com/p/A{n}/", title=titre,
                      raw_hash=f"x{n:063d}", published_at=date, media=list(media), mentioned_artists=mentioned)
    db.add(article)
    db.commit()
    return article


@pytest.fixture()
def fred(db_session):
    return _source(db_session, "Fred again.. (IG)", "fredagain")


@pytest.fixture()
def guettapen(db_session):
    return _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)


class Telechargement:
    def __init__(self, echecs=()):
        self.urls, self.echecs = [], set(echecs)

    def __call__(self, url):
        self.urls.append(url)
        return (None, "HTTP 403") if url in self.echecs else (_image(), ".jpg")


class Lecteur:
    def __init__(self, posts=(), erreur=None):
        self.posts, self.erreur, self.appels = list(posts), erreur, []

    def __call__(self, compte, maintenant):
        self.appels.append(compte.handle)
        if self.erreur is not None:
            raise self.erreur
        return [p for p in self.posts if p.compte.source_id == compte.source_id]


def _choisir(db, article, **options):
    options.setdefault("telecharger", Telechargement())
    return choisir_visuels(db, article, maintenant=MAINTENANT, **options)


def test_la_photo_de_presse_reste_le_visuel_sans_copie(db_session):
    billboard = _source(db_session, "Billboard (RSS)", categorie=SourceCategorie.MEDIA, type_=SourceType.RSS)
    article = _article(db_session, billboard, media=[entree("photo", "https://billboard/og.jpg")])
    telechargement = Telechargement()
    choix = _choisir(db_session, article, telecharger=telechargement)
    assert [(a["origine"], a["url"], a["fichier"]) for a in choix.actifs] == [
        ("article", "https://billboard/og.jpg", None),
    ]
    assert choix.credit is None
    assert telechargement.urls == []


def test_le_post_d_un_artiste_est_credite_a_son_compte(db_session, fred):
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/fred-propre.jpg")])
    assert _choisir(db_session, article).credit == "@fredagain"


def test_un_media_instagram_prend_la_photo_de_l_artiste_cite(db_session, fred, guettapen):
    article = _article(db_session, guettapen, media=[entree("photo", "https://cdn.ig/guettapen.jpg")],
                       mentioned=["Fred again.."])
    post_fred = _article(db_session, fred, "My track on the GTA soundtrack",
                         media=[entree("photo", "https://cdn.ig/fred.jpg")], date=DATE_ACTU - dt.timedelta(days=1))
    telechargement, lecteur = Telechargement(), Lecteur()

    choix = _choisir(db_session, article, telecharger=telechargement, lecteur=lecteur)

    (visuel,) = choix.actifs
    assert (visuel["origine"], visuel["compte"], visuel["permalink"], visuel["article_id"], visuel["url"]) == (
        "artiste", "@fredagain", post_fred.url, post_fred.id, "https://cdn.ig/fred.jpg",
    )
    assert visuel["fichier"].startswith("file://")
    assert str(settings.visuels_cache_dir.resolve()) in visuel["fichier"]
    assert choix.credit == "@fredagain"
    assert telechargement.urls == ["https://cdn.ig/fred.jpg"]
    assert lecteur.appels == []


def test_sans_artiste_suivi_cite_aucun_visuel(db_session, guettapen):
    article = _article(db_session, guettapen, "Actu sans artiste", media=[entree("photo", "https://cdn.ig/g.jpg")])
    with pytest.raises(AucunVisuel, match="compte media Instagram"):
        _choisir(db_session, article)


def test_la_lecture_en_direct_quand_la_base_n_a_rien_sur_le_sujet(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    compte = construire_index(db_session).comptes[fred.id]
    en_direct = PostArtiste(compte, "https://www.instagram.com/p/LIVE/", "GTA soundtrack day", "",
                            MAINTENANT - dt.timedelta(hours=2), [entree("photo", "https://cdn.ig/live.jpg")])
    lecteur = Lecteur([en_direct])
    choix = _choisir(db_session, article, lecteur=lecteur)
    assert lecteur.appels == ["fredagain"]
    assert [(a["permalink"], a["article_id"]) for a in choix.actifs] == [("https://www.instagram.com/p/LIVE/", None)]


def test_une_lecture_differee_sans_rien_en_base_differe_la_composition(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    with pytest.raises(VisuelsDifferes, match="quota"):
        _choisir(db_session, article, lecteur=Lecteur(erreur=LectureDifferee("quota Graph atteint")))


def test_un_portrait_recent_en_base_evite_le_report(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    _article(db_session, fred, "Paris merci", media=[entree("photo", "https://cdn.ig/paris.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))
    choix = _choisir(db_session, article, lecteur=Lecteur(erreur=LectureDifferee("quota Graph atteint")))
    assert [a["url"] for a in choix.actifs] == ["https://cdn.ig/paris.jpg"]


def test_des_copies_toutes_en_echec_ne_donnent_aucun_visuel(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    _article(db_session, fred, "GTA soundtrack", media=[entree("photo", "https://cdn.ig/expire.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))
    with pytest.raises(AucunVisuel):
        _choisir(db_session, article, telecharger=Telechargement(echecs={"https://cdn.ig/expire.jpg"}))


def test_trois_images_au_plus(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    _article(db_session, fred, "GTA soundtrack", date=DATE_ACTU - dt.timedelta(days=1),
             media=[entree("photo", f"https://cdn.ig/{n}.jpg") for n in range(5)])
    assert len(_choisir(db_session, article).actifs) == 3


def test_un_reel_prend_la_video_de_l_artiste_sans_la_copier(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."],
                       media=[entree("video", "https://cdn.ig/guettapen.mp4", duration_ms=20_000)])
    reel = _article(db_session, fred, "GTA soundtrack", url="https://www.instagram.com/reel/FRED/",
                    media=[entree("video", None, preview_url="https://cdn.ig/v.jpg")],
                    date=DATE_ACTU - dt.timedelta(days=1))
    telechargement, lecteur = Telechargement(), Lecteur()

    choix = _choisir(db_session, article, format_=FormatCreneau.REEL, telecharger=telechargement, lecteur=lecteur)

    (video,) = choix.actifs
    assert (video["type"], video["url"], video["permalink"], video["compte"]) == (
        "video", None, reel.url, "@fredagain",
    )
    assert choix.credit == "@fredagain"
    assert telechargement.urls == [] and lecteur.appels == []


def test_un_reel_sans_video_d_artiste_n_a_aucun_visuel(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    _article(db_session, fred, "GTA soundtrack", media=[entree("photo", "https://cdn.ig/p.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))
    with pytest.raises(AucunVisuel):
        _choisir(db_session, article, format_=FormatCreneau.REEL)


def test_un_reel_propre_garde_sa_video(db_session, fred):
    video = entree("video", "https://cdn.ig/fred.mp4", duration_ms=20_000)
    article = _article(db_session, fred, media=[video])
    choix = _choisir(db_session, article, format_=FormatCreneau.REEL)
    assert [(a["origine"], a["url"], a["permalink"]) for a in choix.actifs] == [
        ("article", "https://cdn.ig/fred.mp4", article.url),
    ]
    assert choix.credit == "@fredagain"


def test_le_carrousel_garde_la_video_de_l_article(db_session, fred):
    """Adaptation : depuis « carrousel avec videos » (main), le carrousel publie
    aussi les videos de l'actu -- les actifs ne doivent pas les perdre."""
    article = _article(db_session, fred, media=[
        entree("photo", "https://cdn.ig/fred.jpg"),
        entree("video", "https://cdn.ig/fred.mp4", duration_ms=20_000),
    ])
    choix = _choisir(db_session, article)
    assert [(a["type"], a["url"]) for a in choix.actifs] == [
        ("photo", "https://cdn.ig/fred.jpg"),
        ("video", "https://cdn.ig/fred.mp4"),
    ]


def test_une_vignette_d_artiste_n_est_jamais_un_actif(db_session, fred, guettapen):
    """Une video d'artiste sans URL directe n'a que sa vignette : impossible a
    copier en local, donc pas de visuel de carrousel (regle portee par T8)."""
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    _article(db_session, fred, "GTA soundtrack", date=DATE_ACTU - dt.timedelta(days=1),
             media=[entree("video", None, preview_url="https://cdn.ig/vignette.jpg")])
    telechargement = Telechargement()
    with pytest.raises(AucunVisuel):
        _choisir(db_session, article, telecharger=telechargement)
    assert telechargement.urls == []


def test_un_compte_media_cite_ne_fournit_jamais_de_visuel(db_session, guettapen):
    """Ceinture au niveau du choix : meme si un compte media Instagram entre dans
    l'index (classement manuel, regression amont), ses visuels sont interdits."""
    electro = _source(db_session, "Electro News (IG)", "electronews", SourceCategorie.MEDIA)
    _article(db_session, electro, "GTA soundtrack le generique", date=DATE_ACTU - dt.timedelta(days=1),
             media=[entree("photo", "https://cdn.ig/electronews.jpg")])
    article = _article(db_session, guettapen, mentioned=["Electro News"])
    compte = CompteArtiste(source_id=electro.id, handle="electronews", nom="Electro News",
                           dernier_appel=None)
    index = IndexComptes({electro.id: compte}, {cle_artiste("Electro News"): compte})
    telechargement, lecteur = Telechargement(), Lecteur()

    with pytest.raises(AucunVisuel):
        _choisir(db_session, article, index=index, telecharger=telechargement, lecteur=lecteur)

    assert telechargement.urls == [] and lecteur.appels == []


def test_un_compte_media_cite_n_empeche_pas_l_artiste(db_session, fred, guettapen):
    """Le compte media est ecarte, pas le reste des comptes cites."""
    electro = _source(db_session, "Electro News (IG)", "electronews", SourceCategorie.MEDIA)
    _article(db_session, electro, "GTA soundtrack le generique", date=DATE_ACTU - dt.timedelta(days=1),
             media=[entree("photo", "https://cdn.ig/electronews.jpg")])
    _article(db_session, fred, "GTA soundtrack le generique", date=DATE_ACTU - dt.timedelta(days=1),
             media=[entree("photo", "https://cdn.ig/fred.jpg")])
    article = _article(db_session, guettapen, mentioned=["Electro News", "Fred again.."])
    comptes = {s.id: CompteArtiste(s.id, h, n, None) for s, h, n in
               ((electro, "electronews", "Electro News"), (fred, "fredagain", "Fred again.."))}
    index = IndexComptes(comptes, {cle_artiste(c.nom): c for c in comptes.values()})

    choix = _choisir(db_session, article, index=index)
    assert [a["url"] for a in choix.actifs] == ["https://cdn.ig/fred.jpg"]
    assert choix.credit == "@fredagain"


def test_une_coupure_reseau_differe_au_lieu_d_exclure(db_session, fred, guettapen):
    """Un timeout n'est pas une absence de visuel : l'article serait exclu POUR
    TOUJOURS sur une panne de quelques minutes."""
    article = _article(db_session, guettapen, mentioned=["Fred again.."])
    _article(db_session, fred, "GTA soundtrack", media=[entree("photo", "https://cdn.ig/coupe.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))

    def telecharger(url):
        return None, f"{PREFIXE_TRANSPORT}ConnectTimeout"

    with pytest.raises(VisuelsDifferes, match="reseau"):
        _choisir(db_session, article, telecharger=telecharger)
