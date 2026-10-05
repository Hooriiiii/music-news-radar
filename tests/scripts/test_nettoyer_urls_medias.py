"""Le rattrapage des lignes deja en base : `scripts/reparations/nettoyer_urls_medias.py`.

Le nettoyage a l'ingestion ne vaut que pour les nouvelles lignes. Celles posees
avant -- dont l'article 17974, dont le reel est parti sur le fond de secours --
gardent leur `&amp;` et referaient le meme post.
"""

import datetime as dt

from app.models import Article, Post, Source, SourceType
from scripts.reparations.nettoyer_urls_medias import nettoyer_urls

SALE = "https://cdn/a.jpg?_nc_cat=105&amp;ccb=7-5"
PROPRE = "https://cdn/a.jpg?_nc_cat=105&ccb=7-5"


def _article(db, media):
    source = Source(name="IG", type=SourceType.INSTAGRAM, url="ig:artiste")
    db.add(source)
    db.commit()
    article = Article(
        source_id=source.id, url="https://instagram.com/p/1", title="Actu", raw_hash="h1",
        published_at=dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc), media=media,
    )
    db.add(article)
    db.commit()
    return article


def _post(db, article, slides, assets=None):
    post = Post(
        article_id=article.id, article_title="Actu", article_url=article.url,
        caption="Legende", slides=slides, assets=assets,
    )
    db.add(post)
    db.commit()
    return post


def test_le_plan_compte_sans_rien_ecrire(db_session):
    article = _article(db_session, [{"type": "photo", "url": SALE, "preview_url": SALE}])
    rapport = nettoyer_urls(db_session)
    assert rapport.articles == 1 and rapport.urls == 2
    db_session.refresh(article)
    assert article.media[0]["url"] == SALE


def test_appliquer_desechappe_les_medias_d_article(db_session):
    article = _article(db_session, [{"type": "video", "url": None, "preview_url": SALE}])
    nettoyer_urls(db_session, appliquer=True)
    db_session.refresh(article)
    assert article.media[0] == {"type": "video", "url": None, "preview_url": PROPRE}


def test_appliquer_desechappe_les_slides_et_les_actifs_d_un_post(db_session):
    article = _article(db_session, [])
    post = _post(
        db_session, article,
        slides=[
            {"kind": "cover", "headline": "Hop", "image_url": SALE},
            {"kind": "media", "type": "photo", "url": SALE, "preview_url": None},
        ],
        assets=[{"type": "photo", "url": SALE, "preview_url": SALE, "fichier": None}],
    )
    rapport = nettoyer_urls(db_session, appliquer=True)
    db_session.refresh(post)
    assert post.slides[0]["image_url"] == PROPRE
    assert post.slides[1]["url"] == PROPRE and post.slides[1]["preview_url"] is None
    assert post.assets[0]["url"] == PROPRE and post.assets[0]["preview_url"] == PROPRE
    assert (rapport.posts, rapport.urls) == (1, 4)


def test_une_ligne_propre_n_est_pas_comptee_ni_reecrite(db_session):
    _article(db_session, [{"type": "photo", "url": PROPRE, "preview_url": None}])
    rapport = nettoyer_urls(db_session, appliquer=True)
    assert (rapport.articles, rapport.posts, rapport.urls) == (0, 0, 0)


def test_idempotent(db_session):
    article = _article(db_session, [{"type": "photo", "url": SALE, "preview_url": None}])
    nettoyer_urls(db_session, appliquer=True)
    db_session.refresh(article)
    assert nettoyer_urls(db_session, appliquer=True).urls == 0
    db_session.refresh(article)
    assert article.media[0]["url"] == PROPRE


def test_une_valeur_non_texte_est_laissee_telle_quelle(db_session):
    """Un media mal forme (URL a None, champ absent, dict etranger) ne doit pas
    faire tomber un script de maintenance lance sur toute la base."""
    article = _article(db_session, [{"type": "photo"}, "pas un dict", {"url": 12}])
    assert nettoyer_urls(db_session, appliquer=True).urls == 0
    db_session.refresh(article)
    assert article.media == [{"type": "photo"}, "pas un dict", {"url": 12}]
