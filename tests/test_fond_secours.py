"""Le fond de secours : chercher ailleurs plutot que publier une affiche.

Demande de l'utilisateur, 2026-09-18, sur le post des Transmusicales : « pour le
post des Trans c'est toujours une affiche en background sur la premiere slide. Si
tu trouves pas, va trouver une belle photo sur le compte Instagram de l'info en
question. » Puis, apres une troisieme republication du meme post : « ya toujours
l'affiche en fond !! »

La regle est donc absolue : JAMAIS une affiche en fond de cover. Quand tout ce
qu'on trouve en est une, la cover part sur le fond Kinda (`FOND_KINDA`, l'aplat
sombre du gabarit) -- mieux vaut le fond Kinda qu'une affiche. Et avant d'en
arriver la, on cherche dans deux reserves : le compte qui a publie l'actu, puis
les artistes suivis cites.
"""

import datetime as dt
import io
import itertools

from PIL import Image

from app.config import settings
from app.content.actifs_artistes import PostArtiste
from app.content.choix_visuels import (
    choisir_visuels,
    photos_des_artistes_cites,
    photos_du_compte,
)
from app.content.illustration import Fond, Lot, Verdict, eviter_l_affiche
from app.content.lecture_artistes import LectureDifferee
from app.content.media import entree
from app.content.visuels import (
    FOND_KINDA,
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    ORIGINE_COMPTE,
    ORIGINE_KINDA,
)
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
        self.fraicheur_ignoree = []

    def __call__(self, compte, maintenant, *, ignorer_fraicheur=False):
        self.appels.append(compte.handle)
        self.fraicheur_ignoree.append(ignorer_fraicheur)
        if self.erreur is not None:
            raise self.erreur
        return list(self.posts)


def _secours(db, article, **options):
    options.setdefault("telecharger", Telechargement())
    return photos_du_compte(db, article, maintenant=MAINTENANT, **options)


# --- Ce que le compte fournit ---------------------------------------------


def test_les_photos_du_compte_source_viennent_de_la_base(db_session):
    """Le cas des Trans : le compte a publie l'affiche ET, la veille, une photo
    de concert. La base la donne sans reseau."""
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    _article(db_session, trans, "Retour en images sur les Transmusicales",
             media=[entree("photo", "https://cdn.ig/live.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))

    urls = _secours(db_session, article)

    assert len(urls) == 1
    assert urls[0].startswith("file://")
    assert str(settings.visuels_cache_dir.resolve()) in urls[0]


def test_la_base_pleine_se_passe_du_reseau(db_session):
    """Assez de candidats en base : aucun appel Graph. Le quota partage n'est
    depense que quand la reserve locale ne suffit pas."""
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    for n in range(settings.fond_secours_max_photos):
        _article(db_session, trans, f"Transmusicales, image {n}",
                 media=[entree("photo", f"https://cdn.ig/live{n}.jpg")],
                 date=DATE_ACTU - dt.timedelta(days=1, minutes=n))
    lecteur = Lecteur()

    urls = _secours(db_session, article, lecteur=lecteur)

    assert len(urls) == settings.fond_secours_max_photos
    assert lecteur.appels == []


def test_la_fenetre_du_secours_remonte_bien_plus_loin(db_session):
    """« cherche plus loin » : les visuels d'artistes s'arretent a
    `visuels_artiste_fenetre_jours` (10 j) parce qu'un post doit ILLUSTRER
    l'actu. Un fond de cover n'a pas cette exigence : une photo de concert d'il y
    a trois semaines fait un fond parfait, et c'est souvent tout ce qu'un compte
    de festival a hors saison."""
    vieux = settings.visuels_artiste_fenetre_jours + 12
    assert vieux < settings.fond_secours_fenetre_jours
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    _article(db_session, trans, "Retour en images sur l'edition precedente",
             media=[entree("photo", "https://cdn.ig/live.jpg")],
             date=DATE_ACTU - dt.timedelta(days=vieux))

    assert len(_secours(db_session, article)) == 1


def test_le_compte_est_relu_meme_s_il_vient_d_etre_ingere(db_session):
    """La garde de fraicheur du `LecteurComptes` existe pour EPARGNER le quota a
    l'ingestion, pas pour interdire cette lecture-ci : quand le fond serait une
    affiche, une lecture vaut la peine. Les plafonds durs (2 par passage, 24 par
    jour, cache 6 h, pause quota) restent, eux, dans le lecteur."""
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    lecteur = Lecteur()

    _secours(db_session, article, lecteur=lecteur)

    assert lecteur.appels == ["transmusicales"]
    assert lecteur.fraicheur_ignoree == [True]


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
    for n in range(settings.fond_secours_max_photos + 3):
        _article(db_session, trans, f"Transmusicales, image {n}",
                 media=[entree("photo", f"https://cdn.ig/live{n}.jpg")],
                 date=DATE_ACTU - dt.timedelta(days=1, minutes=n))

    urls = _secours(db_session, article)

    assert len(urls) == settings.fond_secours_max_photos


def test_le_choix_des_visuels_attache_les_deux_reserves(db_session):
    """Le secours porte le budget de lecture DU PASSAGE : il est fabrique la ou
    ce budget vit, et voyage avec les visuels jusqu'a la composition. Deux lots,
    dans l'ordre : le compte de l'actu, puis les artistes suivis cites."""
    trans = _source(db_session)
    article = _article(db_session, trans, media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    _article(db_session, trans, "Retour en images sur les Transmusicales",
             media=[entree("photo", "https://cdn.ig/live.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))

    choix = choisir_visuels(db_session, article, maintenant=MAINTENANT,
                            telecharger=Telechargement(), lecteur=Lecteur())

    assert [lot.origine for lot in choix.secours] == [ORIGINE_COMPTE, ORIGINE_ARTISTE]
    urls = choix.secours[0].photos()
    assert len(urls) == 1 and urls[0].startswith("file://")


# --- La deuxieme reserve : les artistes suivis cites -----------------------


def test_un_artiste_suivi_cite_fournit_un_fond(db_session):
    """« un line-up des Trans cite des artistes qu'on suit ; un portrait de l'un
    d'eux vaut mieux qu'une affiche » (utilisateur, 2026-09-18)."""
    trans = _source(db_session)
    fred = _source(db_session, "Fred again.. (IG)", "fredagain", SourceCategorie.ARTISTE)
    article = _article(db_session, trans, titre="Les Transmusicales annoncent Fred again..",
                       media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    _article(db_session, fred, "Fred again.. en studio",
             media=[entree("photo", "https://cdn.ig/fred.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))
    article.mentioned_artists = ["Fred again.."]
    db_session.commit()

    urls = photos_des_artistes_cites(
        db_session, article, maintenant=MAINTENANT, telecharger=Telechargement()
    )

    assert len(urls) == 1 and urls[0].startswith("file://")


def test_les_artistes_cites_n_incluent_jamais_un_compte_media(db_session):
    guettapen = _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)
    trans = _source(db_session)
    article = _article(db_session, trans, titre="Guettapen relaie les Transmusicales",
                       media=[entree("photo", "https://cdn.ig/affiche.jpg")])
    _article(db_session, guettapen, "Guettapen en images",
             media=[entree("photo", "https://cdn.ig/g.jpg")],
             date=DATE_ACTU - dt.timedelta(days=1))
    article.mentioned_artists = ["Guettapen"]
    db_session.commit()

    assert photos_des_artistes_cites(
        db_session, article, maintenant=MAINTENANT, telecharger=Telechargement()
    ) == []


# --- L'arbitrage : jamais une affiche, quitte a partir sur le fond Kinda ----
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


def _lot(origine, urls, compteur=None):
    def photos():
        if compteur is not None:
            compteur.append(origine)
        return list(urls)

    return Lot(origine, photos)


def test_un_fond_qui_n_est_pas_une_affiche_ne_coute_rien():
    """La regle de cout qui prime : quand la premiere image est deja une bonne
    photo, aucune reserve n'est ouverte, et on ne juge que ce qu'on a deja."""
    lectures, juges = [], []

    fond = eviter_l_affiche(
        "https://cdn.ig/photo.jpg",
        _lot(ORIGINE_COMPTE, ["file:///cache/live.jpg"], lectures),
        verdicts=_verdicts({"https://cdn.ig/photo.jpg": PHOTO}, juges),
    )

    assert fond == Fond("https://cdn.ig/photo.jpg", ORIGINE_ARTICLE)
    assert lectures == []
    assert juges == [["https://cdn.ig/photo.jpg"]]


def test_une_affiche_cede_la_place_a_la_photo_du_compte():
    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        _lot(ORIGINE_COMPTE, ["file:///cache/live.jpg"]),
        verdicts=_verdicts({"https://cdn.ig/affiche.jpg": AFFICHE,
                            "file:///cache/live.jpg": PHOTO}),
    )

    assert fond == Fond("file:///cache/live.jpg", ORIGINE_COMPTE)


def test_la_meilleure_photo_du_compte_est_retenue():
    """Meme classement que le juge d'origine : une scene ou un portrait d'abord,
    la note ensuite."""
    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        _lot(ORIGINE_COMPTE, ["file:///cache/objet.jpg", "file:///cache/live.jpg"]),
        verdicts=_verdicts({
            "https://cdn.ig/affiche.jpg": AFFICHE,
            "file:///cache/objet.jpg": Verdict(affiche=False, sujet="autre", note=95),
            "file:///cache/live.jpg": PHOTO,
        }),
    )

    assert fond == Fond("file:///cache/live.jpg", ORIGINE_COMPTE)


def test_le_lot_des_artistes_ne_s_ouvre_qu_apres_celui_du_compte():
    """Le compte de l'actu d'abord (c'est SON actu) ; les artistes cites ensuite,
    et seulement si le compte n'a rien donne -- chaque lot coute des
    telechargements et des jetons."""
    ouverts = []

    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        _lot(ORIGINE_COMPTE, ["file:///cache/vieille-affiche.jpg"], ouverts),
        _lot(ORIGINE_ARTISTE, ["file:///cache/fred.jpg"], ouverts),
        verdicts=_verdicts({
            "https://cdn.ig/affiche.jpg": AFFICHE,
            "file:///cache/vieille-affiche.jpg": AFFICHE,
            "file:///cache/fred.jpg": Verdict(affiche=False, sujet="portrait", note=70),
        }),
    )

    assert fond == Fond("file:///cache/fred.jpg", ORIGINE_ARTISTE)
    assert ouverts == [ORIGINE_COMPTE, ORIGINE_ARTISTE]


def test_le_lot_des_artistes_reste_ferme_si_le_compte_a_donne():
    ouverts = []

    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        _lot(ORIGINE_COMPTE, ["file:///cache/live.jpg"], ouverts),
        _lot(ORIGINE_ARTISTE, ["file:///cache/fred.jpg"], ouverts),
        verdicts=_verdicts({"https://cdn.ig/affiche.jpg": AFFICHE,
                            "file:///cache/live.jpg": PHOTO}),
    )

    assert fond == Fond("file:///cache/live.jpg", ORIGINE_COMPTE)
    assert ouverts == [ORIGINE_COMPTE]


def test_tout_affiche_part_sur_le_fond_kinda():
    """« ya toujours l'affiche en fond !! » (utilisateur, 2026-09-18, apres trois
    republications). La moins mauvaise des affiches reste une affiche : mieux
    vaut le fond Kinda."""
    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        _lot(ORIGINE_COMPTE, ["file:///cache/vieille-affiche.jpg"]),
        _lot(ORIGINE_ARTISTE, ["file:///cache/flyer.jpg"]),
        verdicts=_verdicts({
            "https://cdn.ig/affiche.jpg": AFFICHE,
            "file:///cache/vieille-affiche.jpg": Verdict(affiche=True, sujet="autre", note=90),
            "file:///cache/flyer.jpg": AFFICHE,
        }),
    )

    assert fond == Fond(FOND_KINDA, ORIGINE_KINDA)


def test_une_affiche_sans_aucune_reserve_part_aussi_sur_le_fond_kinda():
    """Une composition a la main, ou un article dont la source n'offre rien : la
    regle ne se negocie pas pour autant."""
    assert eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        verdicts=_verdicts({"https://cdn.ig/affiche.jpg": AFFICHE}),
    ) == Fond(FOND_KINDA, ORIGINE_KINDA)


def test_une_reserve_vide_part_sur_le_fond_kinda():
    assert eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        Lot(ORIGINE_COMPTE, list),
        verdicts=_verdicts({"https://cdn.ig/affiche.jpg": AFFICHE}),
    ) == Fond(FOND_KINDA, ORIGINE_KINDA)


def test_un_candidat_non_juge_ne_remplace_pas_une_affiche():
    """Ne pas savoir n'est pas savoir que c'est une photo : on ne pose en fond
    qu'un candidat dont le juge a dit qu'il n'est PAS une affiche. Sinon une
    image illisible ou un juge muet remettraient une affiche par la fenetre."""
    assert eviter_l_affiche(
        "https://cdn.ig/affiche.jpg",
        _lot(ORIGINE_COMPTE, ["file:///cache/mystere.jpg"]),
        verdicts=_verdicts({"https://cdn.ig/affiche.jpg": AFFICHE}),
    ) == Fond(FOND_KINDA, ORIGINE_KINDA)


def test_une_reserve_en_panne_ne_leve_pas_et_ne_publie_pas_l_affiche():
    """Meme regle que le juge visuel : une panne ici ne doit pas couter un post.
    Mais on SAIT que le fond est une affiche -- la panne ne la rend pas
    publiable, elle prive seulement d'une alternative."""
    def photos():
        raise RuntimeError("le CDN a explose")

    fond = eviter_l_affiche(
        "https://cdn.ig/affiche.jpg", Lot(ORIGINE_COMPTE, photos),
        verdicts=_verdicts({"https://cdn.ig/affiche.jpg": AFFICHE}),
    )

    assert fond == Fond(FOND_KINDA, ORIGINE_KINDA)


def test_un_juge_en_panne_garde_le_fond_d_origine():
    """L'autre moitie : si l'on n'a meme pas pu juger le fond, on ne sait rien --
    et le comportement d'avant (le fond d'origine) reste."""
    def rendre(urls):
        raise RuntimeError("Claude est muet")

    assert eviter_l_affiche(
        "https://cdn.ig/x.jpg", _lot(ORIGINE_COMPTE, ["file:///cache/live.jpg"]),
        verdicts=rendre,
    ) == Fond("https://cdn.ig/x.jpg", ORIGINE_ARTICLE)


def test_sans_fond_rien_ne_se_passe():
    juges = []
    assert eviter_l_affiche(None, _lot(ORIGINE_COMPTE, ["x"]),
                            verdicts=_verdicts({}, juges)) == Fond(None, ORIGINE_ARTICLE)
    assert juges == []


def test_un_verdict_absent_sur_le_fond_ne_declenche_rien():
    """Vision coupee, image illisible, juge muet : on ne sait pas, donc on ne
    depense pas -- le fond d'origine reste. C'est le coupe-circuit qui rend le
    comportement d'avant quand la vision est eteinte."""
    lectures = []

    fond = eviter_l_affiche(
        "https://cdn.ig/x.jpg", _lot(ORIGINE_COMPTE, ["file:///cache/live.jpg"], lectures),
        verdicts=_verdicts({}),
    )

    assert fond == Fond("https://cdn.ig/x.jpg", ORIGINE_ARTICLE)
    assert lectures == []


def test_l_origine_du_fond_d_origine_est_celle_qu_on_lui_donne():
    """Un reel sans photo propre part deja sur une photo d'artiste : l'origine
    posee en entree est celle qui ressort si ce fond convient."""
    assert eviter_l_affiche(
        "file:///cache/fred.jpg", origine=ORIGINE_ARTISTE,
        verdicts=_verdicts({"file:///cache/fred.jpg": PHOTO}),
    ) == Fond("file:///cache/fred.jpg", ORIGINE_ARTISTE)


def test_une_image_deja_dans_l_article_n_est_pas_proposee(db_session):
    """Un compte qui republie la meme affiche dans deux posts la reproposerait
    sinon comme remplacante d'elle-meme."""
    trans = _source(db_session)
    affiche = entree("photo", "https://cdn.ig/affiche.jpg")
    article = _article(db_session, trans, media=[affiche])
    _article(db_session, trans, "Transmusicales, on remet l'affiche", media=[affiche],
             date=DATE_ACTU - dt.timedelta(days=1))

    assert _secours(db_session, article) == []




# --- Le post Noisia : un post d'une seule slide, et aucun artiste montre ------


def _draft():
    from app.content.compose import CoverVariante, PostDraft

    variante = CoverVariante(headline="**Noisia** se reforme", title="**Noisia**",
                             subtitle="de retour apres **sept ans**")
    return PostDraft(variantes=[variante] * 4, caption="Ils reviennent 🎧 et ca fait plaisir ✨")


def test_le_post_noisia_montre_une_vraie_photo_de_l_artiste(db_session):
    """Le defaut du 2026-09-18, article 17946 (« Noisia Reunite After Seven-Year
    Hiatus », Billboard, UNE image) : l'image a ete jugee affiche, donc ecartee du
    fond ; ecartee du carrousel aussi comme « image de la cover » ; et les photos
    des artistes cites n'alimentaient que le fond. Resultat publie : une cover
    sans fond, et rien d'autre -- « la c'est n'importe quoi ! ».

    Ce que le post doit contenir desormais : un fond qui est une VRAIE photo de
    Noisia, et au moins une slide qui montre l'artiste."""
    from app.content.compose import create_post
    from app.publish.preparation import medias_du_carrousel

    billboard = _source(db_session, "Billboard", "billboard", SourceCategorie.MEDIA,
                        type_=SourceType.RSS)
    noisia = _source(db_session, "Noisia (IG)", "noisia", SourceCategorie.ARTISTE)
    affiche = "https://billboard.com/noisia.jpg"
    article = _article(db_session, billboard, titre="Noisia Reunite After Seven-Year Hiatus",
                       media=[entree("photo", affiche)])
    article.mentioned_artists = ["Noisia"]
    for n, url in enumerate(("https://cdn.ig/noisia-1.jpg", "https://cdn.ig/noisia-2.jpg")):
        _article(db_session, noisia, f"Noisia en studio {n}", media=[entree("photo", url)],
                 date=DATE_ACTU - dt.timedelta(days=n + 1))
    db_session.commit()

    choix = choisir_visuels(db_session, article, maintenant=MAINTENANT,
                            telecharger=Telechargement())
    post = create_post(
        db_session, article, _draft(), visuels=choix,
        verdicts_fond=lambda urls: {
            url: (AFFICHE if url == affiche else PHOTO) for url in urls
        },
    )

    fond = post.slides[0]["image_url"]
    assert fond not in (FOND_KINDA, None) and fond.startswith("file://")
    assert post.variants["fond"] == {"origine": ORIGINE_ARTISTE}

    medias = medias_du_carrousel(post, article)
    assert any(m["url"].startswith("file://") for m in medias), medias
    # L'image de l'actu reste une slide honnete : une affiche n'est pas un bon
    # FOND, elle reste le visuel de l'info.
    assert affiche in [m["url"] for m in medias]
