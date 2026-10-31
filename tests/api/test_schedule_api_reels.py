import datetime as dt

from app.config import settings
from app.content.media import entree
from app.models import (
    Article, Creneau, EtatCreneau, FormatCreneau, Post, PostStatus, Source, SourceCategorie, SourceType,
)


def _maintenant():
    return dt.datetime.now(dt.timezone.utc)


def _article(db, n, video=True, categorie=SourceCategorie.ARTISTE):
    source = Source(name=f"compte{n}", type=SourceType.INSTAGRAM, url=f"ig:compte{n}", categorie=categorie)
    db.add(source)
    db.commit()
    media = (
        [entree("video", f"https://cdn/{n}.mp4", preview_url=f"https://cdn/{n}.jpg")]
        if video else [entree("photo", f"https://img/{n}.jpg")]
    )
    article = Article(source_id=source.id, url=f"https://www.instagram.com/p/{n}/", title=f"Actu {n}",
                      raw_hash=f"h{n}", relevance_score=80, published_at=_maintenant(), media=media)
    db.add(article)
    db.commit()
    return article


def _creneau(db, heures, format_, article=None, etat=None):
    creneau = Creneau(publier_a=_maintenant() + dt.timedelta(hours=heures), format=format_,
                      article_id=article.id if article else None,
                      etat=etat or (EtatCreneau.PROGRAMME if article else EtatCreneau.LIBRE))
    db.add(creneau)
    db.commit()
    return creneau


def _brouillon(db, article, template):
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                status=PostStatus.DRAFT, caption="x", hashtags=[], slides=[], template=template)
    db.add(post)
    db.commit()
    return post


def test_la_file_montre_le_format(client, db_session):
    _creneau(db_session, 5, FormatCreneau.REEL)
    _creneau(db_session, 6, FormatCreneau.CARROUSEL)
    assert [v["format"] for v in client.get("/schedule").json()] == ["reel", "carrousel"]


def test_remplacer_un_reel_par_un_article_sans_video_est_refuse(client, db_session):
    photo = _article(db_session, 1, video=False)
    reel = _creneau(db_session, 5, FormatCreneau.REEL)
    reponse = client.patch(f"/schedule/{reel.id}", json={"article_id": photo.id})
    assert reponse.status_code == 409
    assert reponse.json()["detail"].startswith("Article inutilisable en reel : aucune video")


def test_remplacer_un_reel_par_un_media_est_refuse(client, db_session):
    article = _article(db_session, 1, categorie=SourceCategorie.MEDIA)
    reel = _creneau(db_session, 5, FormatCreneau.REEL)
    assert client.patch(f"/schedule/{reel.id}", json={"article_id": article.id}).status_code == 409


def test_remplacer_un_reel_par_une_video_d_artiste(client, db_session):
    article = _article(db_session, 1)
    reel = _creneau(db_session, 5, FormatCreneau.REEL)
    vue = client.patch(f"/schedule/{reel.id}", json={"article_id": article.id}).json()
    assert (vue["article_id"], vue["epingle"], vue["format"]) == (article.id, True, "reel")


def test_remplacer_un_reel_reprend_le_brouillon_reel_pas_le_carrousel(client, db_session):
    article = _article(db_session, 1)
    _brouillon(db_session, article, "kinda_post")
    brouillon_reel = _brouillon(db_session, article, "kinda_reel")
    _brouillon(db_session, article, "kinda_post")
    reel = _creneau(db_session, 5, FormatCreneau.REEL)
    vue = client.patch(f"/schedule/{reel.id}", json={"article_id": article.id}).json()
    assert vue["post_id"] == brouillon_reel.id


def test_remplacer_un_reel_n_accroche_jamais_un_brouillon_carrousel(client, db_session):
    article = _article(db_session, 1)
    _brouillon(db_session, article, "kinda_post")
    reel = _creneau(db_session, 5, FormatCreneau.REEL)
    assert client.patch(f"/schedule/{reel.id}", json={"article_id": article.id}).json()["post_id"] is None


def test_remplacer_un_carrousel_reprend_le_brouillon_carrousel(client, db_session):
    article = _article(db_session, 1)
    carrousel_post = _brouillon(db_session, article, "kinda_post")
    _brouillon(db_session, article, "kinda_reel")
    creneau = _creneau(db_session, 5, FormatCreneau.CARROUSEL)
    vue = client.patch(f"/schedule/{creneau.id}", json={"article_id": article.id}).json()
    assert vue["post_id"] == carrousel_post.id


def test_remplacer_refuse_un_article_engage_sur_un_creneau_verrouille_d_un_autre_format(client, db_session):
    article = _article(db_session, 1)
    _creneau(db_session, 1, FormatCreneau.REEL, article, etat=EtatCreneau.GELE)
    carrousel = _creneau(db_session, 5, FormatCreneau.CARROUSEL)
    reponse = client.patch(f"/schedule/{carrousel.id}", json={"article_id": article.id})
    assert reponse.status_code == 409


def test_un_reel_gele_ne_se_modifie_plus(client, db_session):
    article, autre = _article(db_session, 1), _article(db_session, 2)
    reel = _creneau(db_session, 1, FormatCreneau.REEL, article, etat=EtatCreneau.GELE)
    assert client.patch(f"/schedule/{reel.id}", json={"article_id": autre.id}).status_code == 409


def test_remplacer_un_reel_par_un_reel_d_artiste_sans_media_url(client, db_session):
    """Meta ne donne pas la video : yt-dlp la recuperera au gel."""
    article = _article(db_session, 1, video=False)
    article.media = [entree("video", None, preview_url="https://cdn/1.jpg")]
    article.url = "https://www.instagram.com/reel/AAA/"
    db_session.commit()
    reel = _creneau(db_session, 5, FormatCreneau.REEL)
    assert client.patch(f"/schedule/{reel.id}", json={"article_id": article.id}).status_code == 200


def test_l_offre_de_reels(client, db_session):
    _article(db_session, 1)
    _article(db_session, 2, categorie=SourceCategorie.MEDIA)
    _article(db_session, 3, video=False)
    assert client.get("/schedule/offre-reels").json() == {
        "avec_video": 2, "directes": 2, "eligibles": 1, "fraicheur_jours": 3,
    }


def test_les_reels_sont_coupes_par_defaut(client):
    assert client.get("/publication/reels").json() == {"actifs": False, "ytdlp": True}


def test_ytdlp_reflete_le_env(client, monkeypatch):
    monkeypatch.setattr(settings, "publication_reel_ytdlp", False)
    assert client.get("/publication/reels").json()["ytdlp"] is False


def test_couper_les_reels_vide_la_file_des_reels_sans_toucher_au_mode(client, db_session):
    assert client.put("/publication/mode", json={"mode": "apercu"}).status_code == 200
    client.post("/schedule/rebuild")
    # Opt-in : tant que la case n'est pas cochee, aucun creneau reel.
    assert {v["format"] for v in client.get("/schedule").json()} == {"carrousel"}
    assert client.put("/publication/reels", json={"actifs": True}).json()["actifs"] is True
    assert "reel" in {v["format"] for v in client.get("/schedule").json()}

    reponse = client.put("/publication/reels", json={"actifs": False})
    assert reponse.json() == {"actifs": False, "ytdlp": True}
    assert {v["format"] for v in client.get("/schedule").json()} == {"carrousel"}
    assert client.get("/publication/mode").json()["mode"] == "apercu"

    client.put("/publication/reels", json={"actifs": True})
    assert "reel" in {v["format"] for v in client.get("/schedule").json()}
