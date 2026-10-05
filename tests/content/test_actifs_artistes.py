"""Quel post d'artiste illustre une actu : le meme artiste, recent, sur le sujet
d'abord ; jamais un vieux post sans rapport."""

import datetime as dt
import itertools
from types import SimpleNamespace

from app.content import actifs_artistes
from app.content.actifs_artistes import (
    PostArtiste,
    classer,
    medias_utilisables,
    posts_en_base,
    recouvrement,
)
from app.content.media import entree
from app.models import Article, FormatCreneau, Source, SourceCategorie, SourceType
from app.pipeline.comptes_artistes import CompteArtiste

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
FRED = CompteArtiste(
    source_id=7, handle="fredagainagainagainagainagain", nom="Fred again..", dernier_appel=None
)
ARTICLE = SimpleNamespace(
    id=100,
    title="GTA VI full soundtrack announced featuring Fred again.., PinkPantheress",
    published_at=MAINTENANT - dt.timedelta(hours=5),
    fetched_at=None,
)
PHOTO = entree("photo", "https://cdn.ig/fred.jpg")
_numeros = itertools.count()


def _post(titre, jours, media=None, texte="", permalink=None, article_id=None):
    n = next(_numeros)
    return PostArtiste(
        compte=FRED,
        permalink=permalink or f"https://www.instagram.com/p/P{n}/",
        titre=titre,
        texte=texte,
        publie_le=ARTICLE.published_at - dt.timedelta(days=jours),
        media=[PHOTO] if media is None else media,
        article_id=article_id,
    )


def _classer(*posts, format_=FormatCreneau.CARROUSEL):
    return classer(ARTICLE, list(posts), format_=format_, maintenant=MAINTENANT)


def test_le_nom_de_l_artiste_ne_fait_pas_le_sujet():
    assert recouvrement(ARTICLE.title, _post("Fred again.. live tonight", 1)) == 0
    assert recouvrement(ARTICLE.title, _post("My track on the GTA soundtrack", 1)) == 1


def test_un_post_sur_le_sujet_passe_devant_un_portrait_plus_proche():
    portrait = _post("Paris merci", 0.1)
    sujet = _post("Soundtrack announced today", 4)
    assert [r.post for r in _classer(portrait, sujet)] == [sujet, portrait]
    assert _classer(sujet)[0].sur_le_sujet


def test_hors_sujet_accepte_a_moins_de_cinq_jours_seulement():
    proche, loin = _post("Paris merci", 3), _post("Berlin merci", 6)
    assert [r.post for r in _classer(proche, loin)] == [proche]


def test_jamais_au_dela_de_la_fenetre_meme_sur_le_sujet():
    assert _classer(_post("Soundtrack announced", 11)) == []


def test_jamais_un_post_publie_apres_maintenant():
    assert _classer(_post("Soundtrack announced", -1)) == []


def test_a_sujet_egal_le_plus_proche_de_l_actu():
    loin, proche = _post("Soundtrack announced", 3), _post("Soundtrack announced", 1)
    assert [r.post for r in _classer(loin, proche)] == [proche, loin]


def test_le_carrousel_ne_prend_que_des_photos():
    # Adaptation du 2026-09-17 : une video d'artiste n'a pas de fichier local et
    # son URL de CDN aura expire au gel -- seules les photos sont copiables.
    vignette = entree("video", None, preview_url="https://cdn.ig/v.jpg")
    nue = entree("video", None)
    assert medias_utilisables([PHOTO, vignette, nue], None, FormatCreneau.CARROUSEL) == [PHOTO]
    assert _classer(_post("Soundtrack announced", 1, media=[nue])) == []
    assert _classer(_post("Soundtrack announced", 1, media=[vignette])) == []


def test_le_reel_prend_une_video_directe_de_duree_valable():
    directe = entree("video", "https://cdn.ig/v.mp4", duration_ms=20_000)
    courte = entree("video", "https://cdn.ig/c.mp4", duration_ms=1_000)
    assert medias_utilisables([PHOTO, courte, directe], None, FormatCreneau.REEL) == [directe]


def test_le_reel_prend_une_video_recuperable_par_yt_dlp():
    sans_url = entree("video", None, preview_url="https://cdn.ig/v.jpg")
    assert medias_utilisables(
        [sans_url], "https://www.instagram.com/reel/R1/", FormatCreneau.REEL
    ) == [sans_url]
    assert medias_utilisables([], "https://www.instagram.com/reel/R1/", FormatCreneau.REEL) == [
        entree("video", None)
    ]


def test_le_reel_refuse_une_photo_seule():
    assert medias_utilisables([PHOTO], "https://www.instagram.com/p/P/", FormatCreneau.REEL) == []


def test_posts_en_base_des_comptes_cites_seulement(db_session):
    def source(nom, handle, categorie=SourceCategorie.ARTISTE):
        s = Source(name=nom, type=SourceType.INSTAGRAM, url=f"ig:{handle}", categorie=categorie)
        db_session.add(s)
        db_session.commit()
        return s

    fred, autre = source("Fred again.. (IG)", "fredagain"), source("Autre (IG)", "autre")
    compte = CompteArtiste(fred.id, "fredagain", "Fred again..", None)

    def article(src, n, jours, media=True):
        a = Article(
            source_id=src.id,
            url=f"https://www.instagram.com/p/B{n}/",
            title=f"Post {n}",
            summary="caption",
            raw_hash=f"b{n}",
            media=[PHOTO] if media else None,
            published_at=MAINTENANT - dt.timedelta(days=jours),
        )
        db_session.add(a)
        db_session.commit()
        return a

    recent = article(fred, 1, 2)
    article(fred, 2, 30)
    article(fred, 3, 1, media=False)
    article(autre, 4, 1)

    (post,) = posts_en_base(db_session, [compte], MAINTENANT)
    assert (post.article_id, post.permalink, post.compte) == (recent.id, recent.url, compte)
    assert post.publie_le.tzinfo is not None


def test_posts_en_base_est_borne_aux_plus_recents(db_session, monkeypatch):
    """Borne de lecture : un compte tres actif ne fait pas remonter des centaines
    de posts (la requete tourne par article candidat a l'arbitrage)."""
    monkeypatch.setattr(actifs_artistes, "LIMITE_POSTS", 3)
    source = Source(name="Fred again.. (IG)", type=SourceType.INSTAGRAM, url="ig:fredagain",
                    categorie=SourceCategorie.ARTISTE)
    db_session.add(source)
    db_session.commit()
    compte = CompteArtiste(source.id, "fredagain", "Fred again..", None)
    for n in range(6):
        db_session.add(Article(
            source_id=source.id, url=f"https://www.instagram.com/p/L{n}/", title=f"Post {n}",
            raw_hash=f"l{n:063d}", media=[PHOTO],
            published_at=MAINTENANT - dt.timedelta(days=n + 1),
        ))
    db_session.commit()

    titres = [p.titre for p in posts_en_base(db_session, [compte], MAINTENANT)]
    assert sorted(titres) == ["Post 0", "Post 1", "Post 2"]


# --- Plancher de duree EDITORIAL (2026-10-04, reel 168 : 4,0 s) --------------


def test_le_reel_refuse_une_video_sous_le_plancher_editorial():
    """4 s ne laisse pas le temps de lire la bande de titre. Le plancher est a
    6 s : la mesure du 2026-10-04 place un trou entre 4 et 7 s."""
    courte = entree("video", "https://cdn.ig/4s.mp4", duration_ms=4_000)
    juste = entree("video", "https://cdn.ig/7s.mp4", duration_ms=7_000)
    assert medias_utilisables([courte], None, FormatCreneau.REEL) == []
    assert medias_utilisables([courte, juste], None, FormatCreneau.REEL) == [juste]


def test_le_plancher_editorial_prime_sur_le_plancher_technique(monkeypatch):
    """Le plus HAUT des deux planchers s'applique, un seul point de comparaison."""
    from app.config import settings as reglages

    monkeypatch.setattr(reglages, "publication_reel_duree_editoriale_min_s", 12.0)
    assert actifs_artistes.duree_min_reel_ms() == 12_000
    monkeypatch.setattr(reglages, "publication_reel_duree_editoriale_min_s", 1.0)
    # Jamais sous la limite de Meta, meme si le reglage editorial descend.
    assert actifs_artistes.duree_min_reel_ms() == 3_000


def test_une_duree_inconnue_passe_toujours():
    """Regle du doute inchangee : le gel la mesurera (ffprobe) et `ReelInadapte`
    liberera le creneau sans exclure l'article."""
    inconnue = entree("video", "https://cdn.ig/v.mp4")
    assert inconnue["duration_ms"] is None
    assert medias_utilisables([inconnue], None, FormatCreneau.REEL) == [inconnue]


# --- Chronologie : une video empruntee doit pouvoir MONTRER l'actu -----------


def _video(jours_avant, duree_ms=20_000):
    return entree("video", f"https://cdn.ig/v{jours_avant}.mp4", duration_ms=duree_ms)


def test_une_video_hors_sujet_anterieure_a_l_actu_est_ecartee_en_reel():
    """Reel 168 du 2026-10-03 : Troye Sivan invite surprise chez Charli XCX a
    l'ACL, illustre par une video de @charli_xcx en studio publiee 22 h AVANT.
    Une video anterieure ne peut pas montrer un evenement qui vient d'avoir lieu.
    """
    avant = _post("studio day", 22 / 24, media=[_video(1)])
    assert _classer(avant, format_=FormatCreneau.REEL) == []


def test_une_video_hors_sujet_posterieure_a_l_actu_reste_prise():
    apres = _post("studio day", -0.2, media=[_video(1)])
    assert [r.post for r in _classer(apres, format_=FormatCreneau.REEL)] == [apres]


def test_une_video_SUR_LE_SUJET_peut_etre_anterieure():
    """Un teaser, une annonce, un extrait de set precedent illustrent
    legitimement une actu a venir : la fenetre reste pleine dans les deux sens."""
    teaser = _post("GTA soundtrack coming", 3, media=[_video(3)])
    assert [r.post for r in _classer(teaser, format_=FormatCreneau.REEL)] == [teaser]


def test_la_regle_ne_touche_pas_le_carrousel_ni_le_fond_de_cover():
    """Un FOND de cover est classe en format CARROUSEL (`choix_visuels._reel`) :
    une photo d'artiste anterieure a l'actu y reste parfaitement legitime."""
    photo = _post("studio day", 1)
    assert [r.post for r in _classer(photo, format_=FormatCreneau.CARROUSEL)] == [photo]


def test_mutation_sans_le_critere_la_video_du_02_10_redevient_eligible(monkeypatch):
    """Preuve par mutation : la tolerance d'anteriorite portee a 24 h (le critere
    desarme) rend la video de @charli_xcx du 02/10 eligible pour l'actu du 03/10."""
    from app.config import settings as reglages

    avant = _post("studio day", 22 / 24, media=[_video(1)])
    assert _classer(avant, format_=FormatCreneau.REEL) == []
    monkeypatch.setattr(reglages, "visuels_reel_anteriorite_toleree_heures", 24.0)
    assert [r.post for r in _classer(avant, format_=FormatCreneau.REEL)] == [avant]
