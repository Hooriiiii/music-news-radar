"""Le rattrapage des durees deja en base : `scripts/reparations/remplir_durees_medias.py`.

La lecture a l'ingestion ne vaut que pour les nouvelles lignes. Celles posees
avant -- dont la video de 190 s qui a fait exclure l'article 18951 le
2026-09-21 -- gardent leur `duration_ms` a None et refont le meme degat.
"""

import base64
import datetime as dt
import json

from app.models import Article, Post, Source, SourceType
from scripts.reparations.remplir_durees_medias import remplir_durees


def _url(duree_s: int | None) -> str:
    charge = {"venc_tag": "xpv_progressive"}
    if duree_s is not None:
        charge["duration_s"] = duree_s
    efg = base64.b64encode(json.dumps(charge).encode()).decode().rstrip("=")
    return f"https://scontent.cdninstagram.com/o1/v/t16/AQ{duree_s}.mp4?efg={efg}&ccb=17-1"


def _video(duree_s: int | None, **extras) -> dict:
    return {
        "type": "video", "url": _url(duree_s), "preview_url": None,
        "duration_ms": None, "width": None, "height": None, **extras,
    }


def _article(db, media):
    source = Source(name="IG", type=SourceType.INSTAGRAM, url="ig:artiste")
    db.add(source)
    db.commit()
    article = Article(
        source_id=source.id, url="https://instagram.com/p/1", title="Actu", raw_hash="h1",
        published_at=dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc), media=media,
    )
    db.add(article)
    db.commit()
    return article


def test_le_plan_compte_sans_rien_ecrire(db_session):
    article = _article(db_session, [_video(190), _video(24)])

    rapport = remplir_durees(db_session)

    assert (rapport.videos, rapport.articles, rapport.hors_limite) == (2, 1, 1)
    assert [m["duration_ms"] for m in article.media] == [None, None]


def test_apply_ecrit_les_durees_et_signale_les_hors_limite(db_session):
    article = _article(db_session, [_video(190), _video(24)])

    rapport = remplir_durees(db_session, appliquer=True)

    assert rapport.hors_limite == 1
    db_session.refresh(article)
    assert [m["duration_ms"] for m in article.media] == [190_000, 24_000]


def test_une_duree_connue_n_est_jamais_ecrasee_et_le_reste_est_intact(db_session):
    photo = {"type": "photo", "url": "https://cdn/a.jpg"}
    article = _article(db_session, [photo, _video(190, duration_ms=12_000), _video(None)])

    rapport = remplir_durees(db_session, appliquer=True)

    assert rapport.videos == 0
    db_session.refresh(article)
    assert article.media[0] == photo
    assert article.media[1]["duration_ms"] == 12_000
    assert article.media[2]["duration_ms"] is None


def test_les_actifs_d_un_brouillon_sont_dates_aussi(db_session):
    article = _article(db_session, [])
    post = Post(
        article_id=article.id, article_title="Actu", article_url=article.url,
        caption="Legende", slides=[], assets=[_video(190)],
    )
    db_session.add(post)
    db_session.commit()

    remplir_durees(db_session, appliquer=True)

    db_session.refresh(post)
    assert post.assets[0]["duration_ms"] == 190_000


def test_idempotent(db_session):
    _article(db_session, [_video(190)])
    remplir_durees(db_session, appliquer=True)

    assert remplir_durees(db_session, appliquer=True).videos == 0
