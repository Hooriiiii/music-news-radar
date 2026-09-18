"""Le fond de secours : une photo du compte QUI A PUBLIE l'actu.

Demande de l'utilisateur, 2026-09-18, sur le post des Transmusicales : « pour le
post des Trans c'est toujours une affiche en background sur la premiere slide. Si
tu trouves pas, va trouver une belle photo sur le compte Instagram de l'info en
question. »

Le juge visuel (2026-09-18, `illustration.py`) sait deja ECARTER une affiche
quand il existe une photo a lui preferer. Le cas des Trans est celui ou il n'y en
a pas : le compte du festival a publie un carrousel d'affiches de line-up, donc
les cinq candidats sont des affiches et le juge rend la moins mauvaise. Ce module
va alors chercher ailleurs -- sur le compte lui-meme.
"""

import datetime as dt
import io
import itertools

from PIL import Image

from app.config import settings
from app.content.actifs_artistes import PostArtiste
from app.content.choix_visuels import choisir_visuels, photos_du_compte
from app.content.illustration import Verdict, eviter_l_affiche
from app.content.lecture_artistes import LectureDifferee
from app.content.media import entree
from app.models import Article, Source, SourceCategorie, SourceType
from app.pipeline.comptes_artistes import CompteArtiste

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
DATE_ACTU = MAINTENANT - dt.timedelta(hours=3)
TITRE = "Les Transmusicales devoilent leur programmation 2026"
_numeros = itertools.count()

AFFICHE = Verdict(affiche=True, sujet="autre", note=15)
PHOTO = Verdict(affiche=False, sujet="scene", note=80)


def _image():
    tampon = io.BytesIO()
    Image.new("RGB", (900, 1200), (4, 5, 6)).save(tampon, "JPEG")
    return tampon.getvalue()


def _source(db, nom="Transmusicales (IG)", handle="transmusicales",
            categorie=SourceCategorie.FESTIVAL, type_=SourceType.INSTAGRAM):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{handle}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre=TITRE, media=(), date=DATE_ACTU):
    n = next(_numeros)
    article = Article(
        source_id=source.id, url=f"https://www.instagram.com/p/T{n}/", title=titre,
        raw_hash=f"t{n:063d}", published_at=date, media=list(media),
    )
    db.add(article)
    db.commit()
    return article


class Telechargement:
    def __init__(self, echecs=()):
        self.urls, self.echecs = [], set(echecs)

    def __call__(self, url):
        self.urls.append(url)
        return (None, "HTTP 403") if url in self.echecs else (_image(), ".jpg")


class Lecteur:
    """Le lecteur `business_discovery`, avec ses budgets -- ici un faux."""

    def __init__(self, posts=(), erreur=None):
        self.posts, self.erreur, self.appels = list(posts), erreur, []

    def __call__(self, compte, maintenant):
        self.appels.append(compte.handle)
        if self.erreur is not None:
            raise self.erreur
        return list(self.posts)


def _secours(db, article, **options):
    options.setdefault("telecharger", Telechargement())
    return photos_du_compte(db, article, maintenant=MAINTENANT, **options)


# --- Ce que le compte fournit ---------------------------------------------


def test_les_photos_du_compte_source_viennent_de_la_base_sans_reseau(db_session):
    """Le cas des Trans : le compte a publie l'affiche ET, la veille, une photo
    de concert. La photo est en base -- aucun appel Graph n'est necessaire."""
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    _article(db_session, trans, "Retour en images sur les Transmusicales",
             media=[entree("photo", "https://cdn.ig/live.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))
    lecteur = Lecteur()

    urls = _secours(db_session, article, lecteur=lecteur)

    assert len(urls) == 1
    assert urls[0].startswith("file://")
    assert str(settings.visuels_cache_dir.resolve()) in urls[0]
    assert lecteur.appels == []


def test_les_photos_de_l_article_lui_meme_ne_sont_pas_du_secours(db_session):
    """Sinon on proposerait l'affiche comme remplacante de l'affiche."""
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])

    assert _secours(db_session, article) == []


def test_le_compte_est_lu_en_direct_quand_la_base_n_a_rien(db_session):
    """« va trouver une belle photo sur le compte Instagram de l'info en
    question » : la lecture en direct, avec les budgets du LecteurComptes."""
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    compte = CompteArtiste(source_id=trans.id, handle="transmusicales",
                           nom="Transmusicales", dernier_appel=None)
    lecteur = Lecteur([
        PostArtiste(compte=compte, permalink="https://www.instagram.com/p/L1/",
                    titre="Transmusicales 2025", texte="", publie_le=DATE_ACTU - dt.timedelta(days=2),
                    media=[entree("photo", "https://cdn.ig/live.jpg")])
    ])

    urls = _secours(db_session, article, lecteur=lecteur)

    assert lecteur.appels == ["transmusicales"]
    assert len(urls) == 1 and urls[0].startswith("file://")


def test_une_lecture_differee_ne_fait_pas_echouer_la_composition(db_session):
    """Budget epuise, quota Graph, reseau : le secours est un CONFORT. Il rend
    une liste vide et l'appelant garde l'affiche -- jamais une exception, qui
    ferait perdre le creneau pour une question de gout."""
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    lecteur = Lecteur(erreur=LectureDifferee("budget du passage epuise"))

    assert _secours(db_session, article, lecteur=lecteur) == []


def test_jamais_le_compte_d_un_media(db_session):
    """La regle du 2026-09-17 ne souffre pas d'exception : reposter le visuel
    d'un media concurrent est un plagiat visible, fond de cover compris."""
    guettapen = _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)
    article = _article(db_session, guettapen, media=[entree("photo", "https://cdn.ig/g.jpg")])
    _article(db_session, guettapen, "Retour en images", media=[entree("photo", "https://cdn.ig/live.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))
    lecteur = Lecteur()

    assert _secours(db_session, article, lecteur=lecteur) == []
    assert lecteur.appels == []


def test_une_source_non_instagram_n_a_pas_de_compte_a_lire(db_session):
    """`business_discovery` ne sait lire qu'un compte Instagram professionnel :
    un flux RSS n'a rien a offrir ici."""
    presse = _source(db_session, "Tsugi (RSS)", "tsugi", SourceCategorie.MEDIA, SourceType.RSS)
    article = _article(db_session, presse, media=[entree("photo", "https://tsugi/og.jpg")])

    assert _secours(db_session, article, lecteur=Lecteur()) == []


def test_le_secours_est_borne_en_nombre_de_photos(db_session):
    """Chaque photo est un telechargement ET des jetons de vision : on n'en
    rapatrie qu'une poignee."""
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    for n in range(5):
        _article(db_session, trans, f"Transmusicales, image {n}",
                 media=[entree("photo", f"https://cdn.ig/live{n}.jpg")],
                 date=DATE_ACTU - dt.timedelta(days=1, minutes=n))

    urls = _secours(db_session, article)

    assert len(urls) == settings.fond_secours_max_photos


def test_le_choix_des_visuels_attache_le_secours(db_session):
    """Le secours porte le budget de lecture DU PASSAGE : il est fabrique la ou
    ce budget vit, et voyage avec les visuels jusqu'a la composition."""
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    _article(db_session, trans, "Retour en images sur les Transmusicales",
             media=[entree("photo", "https://cdn.ig/live.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))

    choix = choisir_visuels(db_session, article, maintenant=MAINTENANT,
                            telecharger=Telechargement(), lecteur=Lecteur())

    assert choix.secours is not None
    urls = choix.secours()
    assert len(urls) == 1 and urls[0].startswith("file://")


# --- L'arbitrage : ne remplacer qu'une affiche, et seulement par mieux ------
#
# Le juge visuel est injecte par sa sortie : `verdicts` rend, pour une liste
# d'URL, ce qu'on sait de chacune (cache des verdicts, puis appel de vision pour
# les inconnues). Les tests en passent un faux -- aucun reseau, aucun jeton.


def _verdicts(table, appels=None):
    def rendre(urls):
        if appels is not None:
            appels.append(list(urls))
        return {url: table[url] for url in urls if url in table}

    return rendre


def test_un_fond_qui_n_est_pas_une_affiche_ne_coute_rien():
    """La regle de cout qui prime : quand la premiere image est deja une bonne
    photo, le compte n'est jamais lu, et on ne juge que ce qu'on a deja."""
    lectures, juges = [], []

    fond = eviter_l_affiche(
        "https://cdn.ig/photo.jpg",
        lambda: lectures.append(1) or ["file:///cache/live.jpg"],
        verdicts=_verdicts({"https://cdn.ig/photo.jpg": PHOTO}, juges),
    )

    assert fond == "https://cdn.ig/photo.jpg"
    assert lectures == []
    assert juges == [["https://cdn.ig/photo.jpg"]]


def test_une_affiche_cede_la_place_a_la_photo_du_compte():
    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg", lambda: ["file:///cache/live.jpg"],
        verdicts=_verdicts({"https://cdn.ig/affiche.jpg": AFFICHE,
                            "file:///cache/live.jpg": PHOTO}),
    )

    assert fond == "file:///cache/live.jpg"


def test_la_meilleure_photo_du_compte_est_retenue():
    """Meme classement que le juge d'origine : une scene ou un portrait d'abord,
    la note ensuite."""
    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        lambda: ["file:///cache/objet.jpg", "file:///cache/live.jpg"],
        verdicts=_verdicts({
            "https://cdn.ig/affiche.jpg": AFFICHE,
            "file:///cache/objet.jpg": Verdict(affiche=False, sujet="autre", note=95),
            "file:///cache/live.jpg": PHOTO,
        }),
    )

    assert fond == "file:///cache/live.jpg"


def test_une_affiche_reste_si_le_compte_ne_donne_que_des_affiches():
    """Le dernier repli assume : mieux vaut une cover sur une affiche qu'un
    creneau perdu -- et l'affiche de l'actu vaut mieux qu'une affiche d'ailleurs."""
    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg", lambda: ["file:///cache/vieille-affiche.jpg"],
        verdicts=_verdicts({
            "https://cdn.ig/affiche.jpg": AFFICHE,
            "file:///cache/vieille-affiche.jpg": Verdict(affiche=True, sujet="autre", note=90),
        }),
    )

    assert fond == "https://cdn.ig/affiche.jpg"


def test_une_affiche_reste_si_le_compte_ne_donne_rien():
    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg", list,
        verdicts=_verdicts({"https://cdn.ig/affiche.jpg": AFFICHE}),
    )

    assert fond == "https://cdn.ig/affiche.jpg"


def test_le_secours_ne_leve_jamais():
    """Meme regle que le juge visuel : une panne ici ne doit pas couter un post."""
    def secours():
        raise RuntimeError("le CDN a explose")

    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg", secours,
        verdicts=_verdicts({"https://cdn.ig/affiche.jpg": AFFICHE}),
    )

    assert fond == "https://cdn.ig/affiche.jpg"


def test_sans_fond_ou_sans_secours_rien_ne_se_passe():
    juges = []
    assert eviter_l_affiche(None, lambda: ["x"], verdicts=_verdicts({}, juges)) is None
    assert eviter_l_affiche("https://cdn.ig/a.jpg", None, verdicts=_verdicts({}, juges)) == (
        "https://cdn.ig/a.jpg"
    )
    assert juges == []


def test_un_verdict_absent_ne_declenche_rien():
    """Vision coupee, image illisible, juge muet : on ne sait pas, donc on ne
    depense pas -- le fond d'origine reste. Ne pas savoir n'est pas savoir que
    c'est une affiche."""
    lectures = []

    fond = eviter_l_affiche(
        "https://cdn.ig/x.jpg", lambda: lectures.append(1) or ["file:///cache/live.jpg"],
        verdicts=_verdicts({}),
    )

    assert fond == "https://cdn.ig/x.jpg"
    assert lectures == []


def test_une_image_deja_dans_l_article_n_est_pas_proposee(db_session):
    """Un compte qui republie la meme affiche dans deux posts la reproposerait
    sinon comme remplacante d'elle-meme."""
    trans = _source(db_session)
    affiche = entree("photo", "https://cdn.ig/affiche.jpg")
    article = _article(db_session, trans, media=[affiche])
    _article(db_session, trans, "Transmusicales, on remet l'affiche", media=[affiche],
             date=DATE_ACTU - dt.timedelta(days=1))

    assert _secours(db_session, article) == []
