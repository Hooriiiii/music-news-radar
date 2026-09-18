"""Decision du 2026-09-17 : l'info d'un compte media Instagram, jamais ses visuels.
Les photos de presse RSS restent autorisees."""

import datetime as dt
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.config import settings
from app.content.compose import CoverVariante, PostDraft, create_post
from app.content.credit import credit_video
from app.content.media import entree
from app.content.render import RenduSlide, fetch_image_data_uri
from app.content.visuels import (
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    actif,
    chemin_cache,
    credit_des_actifs,
    image_de_fond,
    lire_fichier_cache,
    raison_sans_artiste,
    urls_interdites,
    visuels_autorises,
)
from app.content.choix_visuels import _propres
from app.models import Article, FormatCreneau, Source, SourceCategorie, SourceType
from app.publish.preparation import (
    PreparationDifferee,
    PreparationImpossible,
    medias_du_carrousel,
    preparer_fichiers,
)
from app.schedule.store import a_une_image, candidats_eligibles

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
PHOTO_IG = entree("photo", "https://cdn.ig/guettapen.jpg")
VIDEO_IG = entree("video", None, preview_url="https://cdn.ig/vignette.jpg")


def _source(type_=SourceType.INSTAGRAM, categorie=SourceCategorie.MEDIA, url="ig:guettapen"):
    return SimpleNamespace(type=type_, categorie=categorie, url=url)


def _article(source, *medias):
    return SimpleNamespace(source=source, media=list(medias), url="https://www.instagram.com/p/X/")


def _image(largeur=1200, hauteur=800, format_="PNG"):
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (200, 30, 90)).save(tampon, format_)
    return tampon.getvalue()


def _interdit(*args, **kwargs):
    raise AssertionError("ne doit pas etre appele")


def test_un_compte_media_instagram_n_a_aucun_visuel_autorise():
    assert visuels_autorises(_article(_source(), PHOTO_IG, VIDEO_IG)) == []
    assert urls_interdites(_article(_source(), PHOTO_IG, VIDEO_IG)) == {
        "https://cdn.ig/guettapen.jpg", "https://cdn.ig/vignette.jpg",
    }


@pytest.mark.parametrize(
    "source",
    [
        _source(SourceType.RSS, SourceCategorie.MEDIA, "https://billboard.com/feed"),
        _source(SourceType.INSTAGRAM, SourceCategorie.ARTISTE, "ig:fredagain"),
        _source(SourceType.INSTAGRAM, SourceCategorie.FESTIVAL, "ig:astropolis"),
    ],
)
def test_la_presse_et_les_comptes_primaires_gardent_leurs_visuels(source):
    assert visuels_autorises(_article(source, PHOTO_IG)) == [PHOTO_IG]
    assert urls_interdites(_article(source, PHOTO_IG)) == set()


def test_le_credit_ne_nomme_jamais_un_compte_media():
    assert credit_video(_article(_source())) is None
    assert credit_video(_article(_source(categorie=SourceCategorie.ARTISTE, url="ig:fredagain"))) == "@fredagain"


def test_raison_sans_artiste_est_lisible():
    assert "compte media Instagram" in raison_sans_artiste(_article(_source()))
    assert raison_sans_artiste(_article(_source(SourceType.RSS))) == "aucune image, et aucun artiste suivi cite"


def test_un_actif_a_la_forme_complete():
    publie = dt.datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    resultat = actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain",
                     permalink="https://www.instagram.com/p/F/", article_id=3, publie_le=publie)
    assert resultat == {
        "type": "photo", "url": "https://cdn.ig/guettapen.jpg", "preview_url": None, "duration_ms": None,
        "width": None, "height": None, "origine": "artiste", "compte": "@fredagain",
        "permalink": "https://www.instagram.com/p/F/", "article_id": 3,
        "publie_le": "2026-09-16T12:00:00+00:00", "fichier": None,
    }


def test_le_fond_prefere_la_copie_locale_puis_la_photo_puis_la_vignette():
    video = actif(VIDEO_IG, origine=ORIGINE_ARTICLE)
    photo = actif(PHOTO_IG, origine=ORIGINE_ARTICLE)
    copie = actif(PHOTO_IG, origine=ORIGINE_ARTISTE, fichier="file:///cache/a.jpg")
    assert image_de_fond([copie, photo]) == "file:///cache/a.jpg"
    assert image_de_fond([photo]) == "https://cdn.ig/guettapen.jpg"
    assert image_de_fond([video]) == "https://cdn.ig/vignette.jpg"
    assert image_de_fond([]) is None


def test_le_credit_des_actifs_liste_les_comptes_distincts_dans_l_ordre():
    actifs = [
        actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain"),
        actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@pinkpantheress"),
        actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain"),
        actif(PHOTO_IG, origine=ORIGINE_ARTICLE),
    ]
    assert credit_des_actifs(actifs) == "@fredagain @pinkpantheress"
    assert credit_des_actifs([actif(PHOTO_IG, origine=ORIGINE_ARTICLE)]) is None


def test_la_copie_locale_se_lit_dans_le_cache_seulement(tmp_path):
    chemin = chemin_cache("https://cdn.ig/fred.jpg")
    chemin.parent.mkdir(parents=True)
    chemin.write_bytes(b"jpeg")
    assert chemin.parent == settings.visuels_cache_dir
    assert lire_fichier_cache(chemin.resolve().as_uri()) == b"jpeg"

    ailleurs = tmp_path / "ailleurs.jpg"
    ailleurs.write_bytes(b"secret")
    assert lire_fichier_cache(ailleurs.resolve().as_uri()) is None
    evasion = settings.visuels_cache_dir / ".." / "ailleurs.jpg"
    assert lire_fichier_cache(f"file://{evasion}") is None
    assert lire_fichier_cache("https://cdn.ig/fred.jpg") is None
    assert lire_fichier_cache(None) is None


def test_le_rendu_lit_la_copie_locale_sans_reseau(monkeypatch):
    import app.content.render as render

    monkeypatch.setattr(render.httpx, "get", _interdit)
    chemin = chemin_cache("https://cdn.ig/fred.jpg")
    chemin.parent.mkdir(parents=True)
    chemin.write_bytes(_image(format_="JPEG"))
    assert fetch_image_data_uri(chemin.resolve().as_uri()).startswith("data:image/jpeg;base64,")


def test_le_rendu_ne_lit_pas_un_fichier_hors_cache(tmp_path, monkeypatch):
    import app.content.render as render

    monkeypatch.setattr(render.httpx, "get", _interdit)
    ailleurs = tmp_path / "ailleurs.jpg"
    ailleurs.write_bytes(_image(format_="JPEG"))
    assert fetch_image_data_uri(ailleurs.resolve().as_uri()) == fetch_image_data_uri(None)


def _persister_media_ig(db):
    source = Source(name="Guettapen (IG)", type=SourceType.INSTAGRAM, url="ig:guettapen",
                    categorie=SourceCategorie.MEDIA)
    db.add(source)
    db.commit()
    article = Article(source_id=source.id, url="https://www.instagram.com/p/G/", title="Actu relayee",
                      raw_hash="v1", relevance_score=80, hotness_score=20,
                      published_at=MAINTENANT - dt.timedelta(hours=2), media=[PHOTO_IG])
    db.add(article)
    db.commit()
    return article


def test_create_post_ne_prend_jamais_le_fond_d_un_media(db_session):
    article = _persister_media_ig(db_session)
    variante = CoverVariante(headline="**A**", title="**T**", subtitle="**S**")
    post = create_post(db_session, article, PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨"))
    assert post.slides[0]["image_url"] is None
    assert post.credit is None


def test_un_article_de_media_instagram_sans_autre_visuel_n_est_pas_candidat(db_session):
    _persister_media_ig(db_session)
    assert candidats_eligibles(db_session, MAINTENANT) == []


def test_le_carrousel_ignore_les_slides_interdites():
    article = _article(_source(), PHOTO_IG)
    post = SimpleNamespace(slides=[{"kind": "cover"}, {"kind": "media", "type": "photo", "url": PHOTO_IG["url"]}],
                           assets=None)
    assert medias_du_carrousel(post, article) == []


def test_le_carrousel_vient_des_actifs_quand_le_post_en_a():
    article = _article(_source(SourceType.RSS), entree("photo", "https://presse/ignoree.jpg"))
    video_presse = entree("video", "https://presse/clip.mp4", preview_url="https://presse/clip.jpg")
    actifs = [
        actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain", fichier="file:///cache/fred.jpg"),
        actif(video_presse, origine=ORIGINE_ARTICLE),
        actif(entree("photo", "https://presse/photo.jpg"), origine=ORIGINE_ARTICLE),
        actif(VIDEO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain", fichier="file:///cache/vignette.jpg"),
    ]
    post = SimpleNamespace(slides=[{"kind": "cover"}], assets=actifs)
    assert [(m["type"], m["url"]) for m in medias_du_carrousel(post, article)] == [
        ("photo", "file:///cache/fred.jpg"),
        ("video", "https://presse/clip.mp4"),
        ("photo", "https://presse/photo.jpg"),
        ("photo", "file:///cache/vignette.jpg"),
    ]


def test_le_gel_refuse_un_fond_tire_d_un_media(tmp_path):
    post = SimpleNamespace(slides=[{"kind": "cover", "image_url": PHOTO_IG["url"]}], assets=None)
    with pytest.raises(PreparationImpossible, match="compte media Instagram"):
        preparer_fichiers(post, _article(_source(), PHOTO_IG), tmp_path, rendre=_interdit, telecharger=_interdit)


def test_le_gel_lit_les_copies_locales_sans_telecharger(tmp_path):
    copies = []
    for nom in ("fred", "autre"):
        chemin = chemin_cache(f"https://cdn.ig/{nom}.jpg")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(_image(format_="JPEG"))
        copies.append(actif(PHOTO_IG, origine=ORIGINE_ARTISTE, compte="@fredagain", fichier=chemin.resolve().as_uri()))
    hors_cache = tmp_path / "hors.jpg"
    hors_cache.write_bytes(_image(format_="JPEG"))
    copies.append(actif(PHOTO_IG, origine=ORIGINE_ARTISTE, fichier=hors_cache.resolve().as_uri()))
    # Le fond est la premiere copie : elle n'est pas repetee dans le carrousel.
    post = SimpleNamespace(slides=[{"kind": "cover", "image_url": copies[0]["fichier"]}], assets=copies)

    def rendre(slides, template, palette_=None):
        return [RenduSlide(png=_image(1080, 1350), scale=1.0, lines={}, overflow=False)]

    noms = preparer_fichiers(post, _article(_source()), tmp_path / "gel", rendre=rendre, telecharger=_interdit)
    assert len(noms) == 2


def test_le_cache_perd_ses_images_trop_vieilles():
    """`visuels_cache_jours` : les copies locales ne sont utiles que le temps d'un
    creneau, et personne ne les nettoyait."""
    import os

    from app.content.visuels import purger_cache

    maintenant = dt.datetime(2026, 9, 17, 20, 0, tzinfo=dt.timezone.utc)
    vieille = chemin_cache("https://cdn.ig/vieille.jpg")
    recente = chemin_cache("https://cdn.ig/recente.jpg")
    vieille.parent.mkdir(parents=True, exist_ok=True)
    for chemin in (vieille, recente):
        chemin.write_bytes(b"x")
    age = (maintenant - dt.timedelta(days=settings.visuels_cache_jours + 1)).timestamp()
    os.utime(vieille, (age, age))

    assert purger_cache(maintenant) == 1
    assert not vieille.exists() and recente.exists()
    # Idempotent, et sans dossier il ne se plaint pas.
    assert purger_cache(maintenant) == 0


def test_le_fond_prefere_une_vraie_photo_a_la_vignette_d_une_video():
    """Un carrousel ouvre souvent sur une video : sa vignette est une image de
    secours, pas le meilleur fond de cover (l'ancien `_fond_par_defaut` prenait
    deja la photo d'abord)."""
    video = actif(entree("video", "https://cdn.ig/clip.mp4", preview_url="https://cdn.ig/vignette.jpg"),
                  origine=ORIGINE_ARTICLE)
    photo = actif(PHOTO_IG, origine=ORIGINE_ARTICLE)
    assert image_de_fond([video, photo]) == "https://cdn.ig/guettapen.jpg"
    # Sans aucune photo, la vignette reste le seul fond possible.
    assert image_de_fond([video]) == "https://cdn.ig/vignette.jpg"


# Les formes de medias vues en base (API X, Instagram, RSS) : le `type` peut
# valoir gif, None, ou manquer, et l'URL peut n'etre qu'une vignette.
FORMES_MEDIAS = [
    {"type": "photo", "url": "https://p/a.jpg"},
    {"type": "photo", "preview_url": "https://p/b.jpg"},
    {"type": "photo"},
    {"type": "video", "url": "https://v/a.mp4", "preview_url": "https://v/a.jpg"},
    {"type": "video", "url": "https://v/b.mp4"},
    {"type": "video", "preview_url": "https://v/c.jpg"},
    {"type": "gif", "preview_url": "https://g/a.jpg"},
    {"type": "gif", "url": "https://g/b.gif"},
    {"type": None, "preview_url": "https://n/a.jpg"},
    {"url": "https://x/a.jpg"},
]


@pytest.mark.parametrize("media", FORMES_MEDIAS, ids=lambda m: f"{m.get('type')}-{sorted(m)}")
def test_sentinelle_la_file_et_le_choix_voient_le_meme_visuel(media):
    """Verrou : `store.a_une_image` (la file) et `choix_visuels._propres` (la
    composition) doivent rendre le MEME verdict sur chaque forme de media. Un
    gif ou un type absent qui passait l'un et pas l'autre faisait retenir un
    article dans la file, puis l'excluait au choix des visuels, avec une raison
    fausse."""
    article = _article(_source(SourceType.RSS), media)
    article.id = 1
    assert a_une_image(article) == (_propres(article, FormatCreneau.CARROUSEL) is not None)


def test_sentinelle_un_compte_media_instagram_n_a_de_visuel_pour_personne():
    article = _article(_source(), PHOTO_IG)
    article.id = 1
    assert a_une_image(article) is False
    assert _propres(article, FormatCreneau.CARROUSEL) is None


def test_le_gel_differe_quand_la_copie_locale_du_fond_a_disparu(tmp_path):
    """Purge du cache, dossier applicatif efface : le fond `file://` n'existe
    plus. Le rendu retomberait sur l'image de secours du gabarit -- report, et
    jamais une exclusion (l'article n'y est pour rien)."""
    fond = chemin_cache("https://cdn.ig/disparue.jpg").resolve().as_uri()
    post = SimpleNamespace(slides=[{"kind": "cover", "image_url": fond}], assets=[])
    with pytest.raises(PreparationDifferee, match="copie locale du fond"):
        preparer_fichiers(post, _article(_source(SourceType.RSS)), tmp_path,
                          rendre=_interdit, telecharger=_interdit)


def test_le_fond_ignore_un_porteur_de_video_et_prefere_la_vraie_photo():
    """Les deux regles du fond, ensemble : un porteur de video NU (Meta omet
    `media_url` et la vignette) ne donne aucune image -- c'est lui qui bloquait
    les reels --, et une vraie photo passe devant la vignette d'une video, meme
    quand la video vient en premier."""
    porteur = actif(entree("video", None), origine=ORIGINE_ARTICLE, compte="@cdw")
    vignette = actif(entree("video", None, preview_url="https://cdn.ig/v.jpg"),
                     origine=ORIGINE_ARTICLE, compte="@cdw")
    photo = actif(entree("photo", "https://cdn.ig/p.jpg"), origine=ORIGINE_ARTICLE, compte="@cdw")
    assert image_de_fond([porteur]) is None
    assert image_de_fond([porteur, photo]) == "https://cdn.ig/p.jpg"
    assert image_de_fond([vignette, photo]) == "https://cdn.ig/p.jpg"
    assert image_de_fond([vignette]) == "https://cdn.ig/v.jpg"


def test_un_fichier_de_cache_a_none_n_est_pas_un_fond():
    """Ceinture defensive : `fichier` litteralement a « None » (brouillons de
    production) est lu comme absent, jamais comme un chemin."""
    assert image_de_fond([{"type": "photo", "fichier": "None", "url": None}]) is None


def test_create_post_refuse_des_actifs_sans_fond(db_session):
    """Un choix de visuels qui ne donne aucun fond est un defaut du CHOIX : mieux
    vaut une erreur bruyante qu'une slide inpubliable persistee (le creneau
    bloque de l'article 17974)."""
    source = Source(name="Charlotte de Witte (IG)", type=SourceType.INSTAGRAM,
                    url="ig:charlottedewittemusic", categorie=SourceCategorie.ARTISTE)
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://www.instagram.com/reel/AAA/", title="Set",
                      raw_hash="cdw1", media=[entree("video", None)])
    db_session.add(article)
    db_session.commit()
    variante = CoverVariante(headline="**x**", title="**x**", subtitle="**x**")
    draft = PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=[])
    porteur = actif(entree("video", None), origine=ORIGINE_ARTICLE, compte="@charlottedewittemusic")
    with pytest.raises(ValueError, match="sans image de fond"):
        create_post(db_session, article, draft, template="kinda_reel",
                    visuels=SimpleNamespace(actifs=[porteur], credit="@charlottedewittemusic"))
