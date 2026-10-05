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
import json

import pytest
from PIL import Image

from app.config import settings
from app.content.choix_visuels import (
    AucunVisuel,
    VisuelIntrouvable,
    actif_du_web,
    choisir_visuels,
    photos_du_web,
)
from app.content.compose import actifs_de_secours
from app.content.illustration import Fond, Lot, Verdict, eviter_l_affiche
from app.content.legende import texte_legende
from app.content.recherche_web import (
    CACHE_VERSION,
    candidats_du_web,
    est_un_logo,
)
from app.content.media import entree
from app.content.visuels import (
    FOND_KINDA,
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    ORIGINE_COMPTE,
    ORIGINE_KINDA,
    ORIGINE_WEB,
    candidats_de_fond,
    image_de_fond,
)
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


def test_sans_rien_sur_le_web_non_plus_le_creneau_saute_sans_exclure(db_session, monkeypatch):
    """Le web n'est pas une garantie. Mais quand il ne donne rien non plus, ce
    n'est pas la FAUTE de l'article : c'est « rien trouve AUJOURD'HUI », donc un
    SAUT (`VisuelIntrouvable`) et jamais l'exclusion a vie que levait
    `AucunVisuel` jusqu'au 2026-10-05 -- c'est cette conversion qui rend sur le
    desserrage de `store.raison_sans_visuel` du meme jour."""
    monkeypatch.setattr("app.content.recherche_web.image_de_partage", _extraction({}))
    article = _article(db_session, _source(db_session))

    with pytest.raises(VisuelIntrouvable):
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


def test_une_image_du_web_n_est_plus_creditee_par_son_domaine(db_session, monkeypatch):
    """`actif_du_web` garde encore le domaine sur l'actif (`compte`, permalink) --
    c'est `credit_des_actifs` qui ne le fait plus remonter en credit de legende,
    decision de l'utilisateur le 2026-09-21 (« retirer aussi le credit web »)."""
    monkeypatch.setattr("app.content.recherche_web.image_de_partage",
                        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"}))
    article = _article(db_session, _source(db_session))

    choix = _visuels(db_session, article, chercheur_web=Chercheur())

    assert choix.credit is None
    assert choix.actifs[0]["permalink"] == "https://ra.co/news/1"


def test_le_domaine_du_web_n_apparait_plus_dans_la_legende(db_session, monkeypatch):
    """Plus de « via » ni de ligne « Source : » (regles du 2026-09-17), et
    desormais plus de credit de domaine du tout (2026-09-21)."""
    monkeypatch.setattr("app.content.recherche_web.image_de_partage",
                        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"}))
    article = _article(db_session, _source(db_session))
    choix = _visuels(db_session, article, chercheur_web=Chercheur())

    class Faux:
        caption, hashtags, template = "Une actu.", ["techno"], "kinda_post"
        credit = choix.credit

    texte = texte_legende(Faux())

    assert "ra.co" not in texte
    assert "via" not in texte and "Source :" not in texte


def test_une_photo_du_web_retenue_en_secours_reste_creditee(db_session, monkeypatch):
    """Le fond venu d'une RESERVE arrive apres le choix des visuels : sans ce
    rattrapage, l'image serait publiee sans dire d'ou elle vient."""
    monkeypatch.setattr("app.content.recherche_web.image_de_partage",
                        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"}))
    fichiers = photos_du_web(_article(db_session, _source(db_session)), maintenant=MAINTENANT,
                             telecharger=_telecharger, chercheur=Chercheur())
    choisi = Fond(fichiers[0], ORIGINE_WEB, tuple(fichiers))

    nouveaux = actifs_de_secours(choisi, [], maintenant=MAINTENANT)

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


# --- L'article 18260, de bout en bout -------------------------------------
#
# Le defaut mesure en production apres la fusion : « Le voile se leve sur le
# programme des 48es Rencontres Trans Musicales », source « Transmusicales (IG) »,
# `mentioned_artists` VIDE. La requete etait None, le dernier recours ne partait
# jamais, et la cover repartait sur l'aplat Kinda -- sur le cas meme pour lequel
# la fonctionnalite a ete ecrite.

TITRE_18260 = "Le voile se leve sur le programme des 48es Rencontres Trans Musicales"
AFFICHE_18260 = "https://cdn.ig/affiche-trans.jpg"


def _trans(db):
    source = Source(name="Transmusicales (IG)", type=SourceType.INSTAGRAM,
                    url="ig:transmusicales", categorie=SourceCategorie.FESTIVAL)
    db.add(source)
    db.commit()
    return source


def _verdicts_sauf_l_affiche(urls):
    return {url: (AFFICHE if url == AFFICHE_18260 else PHOTO) for url in urls}


def _fond_de_l_article_18260(db, chercheur, monkeypatch, pages_ok=True):
    monkeypatch.setattr(
        "app.content.recherche_web.image_de_partage",
        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"} if pages_ok else {}),
    )
    article = _article(db, _trans(db), titre=TITRE_18260,
                       media=[entree("photo", AFFICHE_18260)], artistes=())
    choix = _visuels(db, article, chercheur_web=chercheur)
    # Exactement le cablage de `create_post` : le fond retenu, puis les reserves.
    return eviter_l_affiche(
        image_de_fond(choix.actifs), *choix.secours,
        origine=ORIGINE_ARTICLE, verdicts=_verdicts_sauf_l_affiche,
    )


def test_l_article_18260_trouve_son_fond_sur_le_web(db_session, monkeypatch):
    """Le compte n'a rien d'autre en base, aucun artiste n'est cite : sans le
    web, cette cover partait sur l'aplat Kinda."""
    chercheur = Chercheur()

    fond = _fond_de_l_article_18260(db_session, chercheur, monkeypatch)

    assert fond.origine == ORIGINE_WEB
    assert fond.url.startswith("file://")
    # La requete est bien partie, et elle nomme le festival.
    assert chercheur.requetes and "Transmusicales" in chercheur.requetes[0]
    assert "(IG)" not in chercheur.requetes[0]


def test_l_article_18260_ne_part_sur_l_aplat_que_si_le_web_n_a_rien(db_session, monkeypatch):
    """La contrepartie : le fond Kinda reste l'issue, mais seulement APRES que le
    web a ete essaye -- jamais avant."""
    chercheur = Chercheur()

    fond = _fond_de_l_article_18260(db_session, chercheur, monkeypatch, pages_ok=False)

    assert fond.origine == ORIGINE_KINDA
    assert chercheur.requetes, "le web doit avoir ete interroge avant l'aplat"


# --- Le meme parcours pour un fond TROP PETIT (2026-09-21) -----------------
#
# Decision de l'utilisateur apres la revue : avec une retention de 2 jours, les
# reserves « compte source » et « artiste cite » lisent une base quasi vide --
# `fond_secours_fenetre_jours` et `visuels_artiste_fenetre_jours` ne peuvent pas
# remonter au-dela de la purge. La RECHERCHE WEB, seul barreau qui ne lit pas la
# base, reprend le relais. Le plancher de nettete doit donc l'atteindre
# exactement comme une affiche averee le fait : sinon une photo floue serait
# remplacee par PAS de photo du tout.

PETITE_18260 = "https://cdn.ig/capture-trans.jpg"
CIBLE_POST = (1080, 1350)


def _fond_trop_petit(db, chercheur, monkeypatch, pages_ok=True):
    monkeypatch.setattr(
        "app.content.recherche_web.image_de_partage",
        _extraction({"https://ra.co/news/1": "https://ra.co/img/1.jpg"} if pages_ok else {}),
    )
    # Aucune reserve en base : le compte du festival n'a pas d'autre post (la
    # purge a 2 jours les a emportes), aucun artiste suivi n'est cite.
    article = _article(db, _trans(db), titre=TITRE_18260,
                       media=[entree("photo", PETITE_18260)], artistes=())
    choix = _visuels(db, article, chercheur_web=chercheur)
    # Le cablage de `create_post`, plancher de resolution compris.
    return eviter_l_affiche(
        image_de_fond(choix.actifs), *choix.secours,
        origine=ORIGINE_ARTICLE,
        verdicts=lambda urls: {url: PHOTO for url in urls},
        cible=CIBLE_POST,
        tailles=lambda urls: {url: (564, 345) for url in urls if url == PETITE_18260},
        candidats=candidats_de_fond(choix.actifs),
    )


def test_un_fond_trop_petit_sans_reserve_en_base_trouve_son_fond_sur_le_web(
    db_session, monkeypatch
):
    chercheur = Chercheur()

    fond = _fond_trop_petit(db_session, chercheur, monkeypatch)

    assert fond.origine == ORIGINE_WEB
    assert fond.url.startswith("file://")
    assert chercheur.requetes, "le plancher de nettete doit atteindre le barreau web"


def test_un_fond_trop_petit_ne_part_sur_l_aplat_qu_apres_le_web(db_session, monkeypatch):
    """Le web bredouille (`pages_ok=False`) : plus aucune reserve. Le fond de
    depart (564x345, 345 px de cote mini) reste assez grand pour le barreau
    d'agrandissement (Tache 6) -- il n'atteint donc l'aplat que si CE
    barreau-la est lui aussi coupe."""
    chercheur = Chercheur()

    fond = _fond_trop_petit(db_session, chercheur, monkeypatch, pages_ok=False)

    assert fond == Fond(PETITE_18260, ORIGINE_ARTICLE, a_agrandir=True)
    assert chercheur.requetes, "le web doit avoir ete interroge avant l'agrandissement"


def test_un_fond_trop_petit_pour_etre_agrandi_part_sur_l_aplat_apres_le_web(
    db_session, monkeypatch
):
    """Meme scenario, mais sous `visuels_upscale_source_min_px` : le barreau
    d'agrandissement n'a plus rien a proposer non plus, l'aplat Kinda reste
    donc la seule issue -- signal interne du saut de la Tache 5."""
    chercheur = Chercheur()
    monkeypatch.setattr(settings, "visuels_upscale_source_min_px", 1000)

    fond = _fond_trop_petit(db_session, chercheur, monkeypatch, pages_ok=False)

    assert fond == Fond(FOND_KINDA, ORIGINE_KINDA)
    assert chercheur.requetes, "le web doit avoir ete interroge avant l'aplat"


# --- Le post 164 : une encyclopedie ne publie pas des photos, des logos ----


def _maintenant():
    """Horloge RELATIVE : une date figee a deja pourri deux fois ces tests."""
    return dt.datetime.now(UTC)


def test_wikipedia_est_ecarte_comme_une_banque_d_images(db_session, monkeypatch):
    """Post 164 (Tomorrowland Winter, publie le 2026-10-03) : la troisieme slide
    etait le logo du festival, recadre au point que les lettres etaient coupees
    (« MORROWL »). Il venait de l'`og:image` de `en.wikipedia.org`. Mesure sur
    le cache reel (7 requetes, 21 candidats) : c'est le domaine le PLUS
    frequent, 4 candidats sur 21."""
    pages = [
        ("https://en.wikipedia.org/wiki/Tomorrowland_(festival)", "Tomorrowland"),
        ("https://djmag.com/news/tomorrowland", "DJ Mag"),
    ]
    monkeypatch.setattr(
        "app.content.recherche_web.image_de_partage",
        _extraction({
            "https://en.wikipedia.org/wiki/Tomorrowland_(festival)":
                "https://upload.wikimedia.org/wikipedia/en/9/90/Tomorrowland_festival_logo.svg",
            "https://djmag.com/news/tomorrowland": "https://djmag.com/img/tml.jpg",
        }),
    )
    trouves = candidats_du_web(
        _article(db_session, _source(db_session)),
        maintenant=_maintenant(),
        chercheur=Chercheur(pages),
    )
    assert [c.domaine for c in trouves] == ["djmag.com"]


def test_une_og_image_qui_se_dit_logo_est_ecartee(db_session, monkeypatch):
    """Le nom du fichier suffit, quel que soit le site : trois logos du cache
    reel le disaient (`..._logo.svg`, `OUT.FEST_2021_logo_1.png`,
    `logo_blue_white_320x250.png`), soit 3 candidats sur 21."""
    pages = [
        ("https://1001tracklists.com/dj/avh", "1001"),
        ("https://i-d.co/article/avh", "i-D"),
    ]
    monkeypatch.setattr(
        "app.content.recherche_web.image_de_partage",
        _extraction({
            "https://1001tracklists.com/dj/avh":
                "https://cdn.1001tracklists.com/images/static/logo_blue_white_320x250.png",
            "https://i-d.co/article/avh": "https://media.i-d.co/2024/05/avh.jpg",
        }),
    )
    trouves = candidats_du_web(
        _article(db_session, _source(db_session)),
        maintenant=_maintenant(),
        chercheur=Chercheur(pages),
    )
    assert [c.domaine for c in trouves] == ["i-d.co"]


def test_le_mot_logo_doit_etre_isole_dans_le_chemin():
    """Cherche en sous-chaine libre, le filtre attraperait « technologo » ou un
    artiste nomme « Logout » : une photo jetee par erreur appauvrit le post."""
    assert est_un_logo("https://x.example/img/festival_logo.svg")
    assert est_un_logo("https://x.example/logos/tml.png")
    assert est_un_logo("https://x.example/wp-json/generate-og-image-with-logo/45845")
    assert not est_un_logo("https://x.example/img/technologo-live.jpg")
    assert not est_un_logo("https://x.example/artists/logout/photo.jpg")
    # Jamais la requete : un parametre de CDN peut porter n'importe quoi.
    assert not est_un_logo("https://x.example/img/scene.jpg?ref=logo")


def test_un_candidat_deja_memorise_repasse_par_les_filtres(db_session, monkeypatch):
    """Une entree posee avant ces filtres servirait sinon le defaut pendant
    encore `visuels_web_cache_jours`. Refiltrer coute zero requete ; reponnoncer
    la recherche en couterait une payante."""
    maintenant = _maintenant()
    chemin = settings.visuels_web_cache_path
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps({
        "version": CACHE_VERSION,
        "requetes": {
            "Tomorrowland festival photo": {
                "le": maintenant.isoformat(),
                "candidats": [
                    {"page": "https://en.wikipedia.org/wiki/Tomorrowland",
                     "titre": "Tomorrowland", "image": "https://upload.wikimedia.org/a.svg",
                     "domaine": "en.wikipedia.org"},
                    {"page": "https://djmag.com/news/tml", "titre": "DJ Mag",
                     "image": "https://djmag.com/img/tml.jpg", "domaine": "djmag.com"},
                ],
            }
        },
    }), encoding="utf-8")
    article = _article(db_session, _source(db_session), titre="Tomorrowland devoile",
                       artistes=())
    monkeypatch.setattr("app.content.recherche_web.requete_pour",
                        lambda _: "Tomorrowland festival photo")

    def jamais(_requete):
        raise AssertionError("une recherche payante a ete relancee")

    trouves = candidats_du_web(article, maintenant=maintenant, chercheur=jamais)
    assert [c.domaine for c in trouves] == ["djmag.com"]
