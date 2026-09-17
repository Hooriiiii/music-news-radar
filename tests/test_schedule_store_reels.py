"""La file des reels : eligibilite, creneaux propres, pas de double emploi."""

import datetime as dt

from app.config import settings
from app.content.media import entree
from app.models import (
    Article, Creneau, EtatCreneau, FormatCreneau, Post, Source, SourceCategorie, SourceType,
)
from app.publish.mode import enregistrer_reels
from app.schedule.slots import prochains_horaires
from app.schedule.store import (
    appliquer_arbitrage,
    candidats_eligibles,
    offre_reels,
    post_a_reprendre,
    raison_ineligible_reel,
    source_video_reel,
    synchroniser_creneaux,
    utc,
)

MAINTENANT = dt.datetime(2026, 9, 20, 4, 0, tzinfo=dt.timezone.utc)


def _source(db, nom="saralandry", categorie=SourceCategorie.ARTISTE, type_=SourceType.INSTAGRAM):
    url = f"ig:{nom}" if type_ == SourceType.INSTAGRAM else f"https://{nom}/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, n, relevance=80, video=True, photo=False, duree_ms=None, sans_url=False, permalink=None):
    media = []
    if video:
        media.append(entree("video", f"https://cdn/{n}.mp4", preview_url=f"https://cdn/{n}.jpg", duration_ms=duree_ms))
    if sans_url:
        # Un reel d'artiste tel que Meta le rend : pas de media_url (musique protegee).
        media.append(entree("video", None, preview_url=f"https://cdn/{n}.jpg"))
    if photo:
        media.append(entree("photo", f"https://img/{n}.jpg"))
    article = Article(source_id=source.id, url=permalink or f"https://t/{n}", title=f"Actu {n}", raw_hash=f"h{n}",
                      relevance_score=relevance, hotness_score=20,
                      published_at=MAINTENANT - dt.timedelta(hours=2), media=media)
    db.add(article)
    db.commit()
    return article


def _post(db, article, template):
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                caption="L", slides=[{"kind": "cover"}], template=template)
    db.add(post)
    db.commit()
    return post


def test_les_creneaux_reel_suivent_leurs_propres_fenetres(db_session):
    creneaux = synchroniser_creneaux(db_session, MAINTENANT, FormatCreneau.REEL)
    assert [utc(c.publier_a) for c in creneaux] == prochains_horaires(MAINTENANT, 6, "reel")
    assert {c.format for c in creneaux} == {FormatCreneau.REEL}


def test_zero_creneau_reel_desactive_les_reels(db_session, monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert len(creneaux) == 6
    assert {c.format for c in creneaux} == {FormatCreneau.CARROUSEL}


def test_eligibilite_reel(db_session):
    artiste = _source(db_session)
    media = _source(db_session, "djmag", SourceCategorie.MEDIA)
    rss = _source(db_session, "tsugi.fr", SourceCategorie.ARTISTE, SourceType.RSS)
    assert raison_ineligible_reel(_article(db_session, artiste, 1)) is None
    assert "media" in raison_ineligible_reel(_article(db_session, media, 2))
    assert raison_ineligible_reel(_article(db_session, artiste, 3, video=False, photo=True)) == "aucune video exploitable"
    assert raison_ineligible_reel(_article(db_session, artiste, 4, duree_ms=120_000)) == "aucune video exploitable"
    assert "credit" in raison_ineligible_reel(_article(db_session, rss, 5))


def test_un_reel_d_artiste_sans_media_url_est_eligible_par_yt_dlp(db_session):
    artiste = _source(db_session)
    reel = _article(db_session, artiste, 1, video=False, sans_url=True,
                    permalink="https://www.instagram.com/reel/AAA/")
    seul = _article(db_session, artiste, 2, video=False, sans_url=True,
                    permalink="https://www.instagram.com/p/BBB/")
    assert (source_video_reel(reel), raison_ineligible_reel(reel)) == ("ytdlp", None)
    assert (source_video_reel(seul), raison_ineligible_reel(seul)) == ("ytdlp", None)
    assert source_video_reel(_article(db_session, artiste, 3)) == "api"


def test_yt_dlp_ne_vaut_que_pour_une_source_instagram(db_session):
    rss = _source(db_session, "tsugi.fr", SourceCategorie.ARTISTE, SourceType.RSS)
    article = _article(db_session, rss, 1, video=False, sans_url=True,
                       permalink="https://www.instagram.com/reel/AAA/")
    assert source_video_reel(article) is None
    assert raison_ineligible_reel(article) == "aucune video exploitable"


def test_yt_dlp_coupe_par_le_env_rend_ineligible_un_reel_sans_media_url(db_session, monkeypatch):
    monkeypatch.setattr(settings, "publication_reel_ytdlp", False)
    artiste = _source(db_session)
    sans_url = _article(db_session, artiste, 1, video=False, sans_url=True,
                        permalink="https://www.instagram.com/reel/AAA/")
    assert raison_ineligible_reel(sans_url) == "aucune video exploitable"
    assert raison_ineligible_reel(_article(db_session, artiste, 2)) is None


def test_reels_coupes_au_dashboard_plus_aucun_creneau_reel(db_session):
    enregistrer_reels(False, MAINTENANT)
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert {c.format for c in creneaux} == {FormatCreneau.CARROUSEL}
    enregistrer_reels(True, MAINTENANT)
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert sum(c.format == FormatCreneau.REEL for c in creneaux) == 6


def test_seuls_les_articles_eligibles_sont_candidats_reel(db_session):
    artiste = _source(db_session)
    bon = _article(db_session, artiste, 1)
    _article(db_session, artiste, 2, video=False, photo=True)
    _article(db_session, _source(db_session, "djmag", SourceCategorie.MEDIA), 3)
    assert [c.article_id for c in candidats_eligibles(db_session, MAINTENANT, FormatCreneau.REEL)] == [bon.id]


def test_un_article_video_va_en_reel_et_jamais_aussi_en_carrousel(db_session):
    video = _article(db_session, _source(db_session), 1, relevance=95)
    photo = _article(db_session, _source(db_session, "tsugi", SourceCategorie.MEDIA), 2, video=False, photo=True)
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    reels = [c.article_id for c in creneaux if c.format == FormatCreneau.REEL]
    carrousels = [c.article_id for c in creneaux if c.format == FormatCreneau.CARROUSEL]
    assert reels.count(video.id) == 1 and video.id not in carrousels
    assert photo.id in carrousels
    assert [utc(c.publier_a) for c in creneaux] == sorted(utc(c.publier_a) for c in creneaux)


def test_un_carrousel_epingle_garde_son_article_hors_des_reels(db_session):
    video = _article(db_session, _source(db_session), 1)
    db_session.add(Creneau(publier_a=MAINTENANT + dt.timedelta(hours=1, minutes=7), article_id=video.id,
                           etat=EtatCreneau.PROGRAMME, epingle=True))
    db_session.commit()
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert all(c.article_id is None for c in creneaux if c.format == FormatCreneau.REEL)


def test_deux_passages_donnent_la_meme_file(db_session):
    artiste = _source(db_session)
    for n in range(3):
        _article(db_session, artiste, n, relevance=70 + n, photo=True)
    premiere = [(c.format, c.article_id) for c in appliquer_arbitrage(db_session, MAINTENANT)]
    seconde = [(c.format, c.article_id) for c in appliquer_arbitrage(db_session, MAINTENANT)]
    assert premiere == seconde
    assert db_session.query(Creneau).count() == 12


def test_post_a_reprendre_filtre_le_gabarit(db_session):
    article = _article(db_session, _source(db_session), 1)
    _post(db_session, article, "kinda_post")
    assert post_a_reprendre(db_session, article.id, "kinda_reel") is None
    reel = _post(db_session, article, "kinda_reel")
    assert post_a_reprendre(db_session, article.id, "kinda_reel") == reel.id


def test_un_brouillon_carrousel_ne_donne_pas_d_avance_en_reel(db_session):
    article = _article(db_session, _source(db_session), 1)
    _post(db_session, article, "kinda_post")
    (candidat,) = candidats_eligibles(db_session, MAINTENANT, FormatCreneau.REEL)
    assert candidat.compose is False


def test_offre_reels(db_session):
    artiste = _source(db_session)
    _article(db_session, artiste, 1)
    _article(db_session, _source(db_session, "djmag", SourceCategorie.MEDIA), 2)
    _article(db_session, artiste, 3, video=False, photo=True)
    _article(db_session, artiste, 4, video=False, sans_url=True, permalink="https://www.instagram.com/reel/DDD/")
    assert offre_reels(db_session, MAINTENANT) == {
        "avec_video": 3, "directes": 2, "eligibles": 2, "fraicheur_jours": 3,
    }


def test_une_video_trop_courte_reste_carrousel_mais_pas_reel(db_session):
    # Le gel refuserait une video de 2 s et exclurait l'article pour tous les formats.
    artiste = _source(db_session)
    courte = _article(db_session, artiste, 1, duree_ms=2_000, photo=True)
    courte_ytdlp = _article(db_session, artiste, 2, video=False, sans_url=True,
                            permalink="https://www.instagram.com/reel/CCC/")
    courte_ytdlp.media = [entree("video", None, preview_url="https://cdn/2.jpg", duration_ms=2_000)]
    db_session.commit()
    inconnue = _article(db_session, artiste, 3)
    assert source_video_reel(courte) is None
    assert source_video_reel(courte_ytdlp) is None
    assert raison_ineligible_reel(inconnue) is None
    reels = [c.article_id for c in candidats_eligibles(db_session, MAINTENANT, FormatCreneau.REEL)]
    assert courte.id not in reels and inconnue.id in reels
    assert courte.id in [c.article_id for c in candidats_eligibles(db_session, MAINTENANT)]


def test_un_carrousel_gele_garde_son_article_hors_des_reels(db_session):
    video = _article(db_session, _source(db_session), 1)
    db_session.add(Creneau(publier_a=MAINTENANT + dt.timedelta(hours=1, minutes=7), article_id=video.id,
                           etat=EtatCreneau.GELE, epingle=False))
    db_session.commit()
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert all(c.article_id is None for c in creneaux if c.format == FormatCreneau.REEL)
