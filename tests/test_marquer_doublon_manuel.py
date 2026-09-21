import datetime as dt

import pytest

from app.models import Article, ArticleStatus, Source, SourceType
from app.pipeline.cluster_arbitrage import lire_cache
from scripts.marquer_doublon_manuel import appliquer

UTC = dt.timezone.utc


def _src(db, name="feed"):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.example/feed")
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, title, status=ArticleStatus.NEW, duplicate_of_id=None,
         file_exclu_raison=None):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.example/{n}",
        title=title,
        raw_hash=f"{n:064d}",
        published_at=dt.datetime.now(UTC),
        status=status,
        duplicate_of_id=duplicate_of_id,
        file_exclu_raison=file_exclu_raison,
    )
    db.add(art)
    db.commit()
    return art


def test_marquer_different_defait_le_lien_et_pose_le_verdict(db_session, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cluster_arbitrage_cache_path", tmp_path / "cache.json")
    src = _src(db_session)
    publie = _art(db_session, src, n=1, title="A", status=ArticleStatus.USED)
    reprise = _art(
        db_session, src, n=2, title="B", duplicate_of_id=publie.id,
        file_exclu_raison="doublon d'une actu deja publiee (article 1, similarite 0.85)",
    )

    appliquer(db_session, publie.id, reprise.id, meme_actu=False, raison="deux annonces distinctes")

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert reprise.file_exclu_raison is None
    cache = lire_cache(settings.cluster_arbitrage_cache_path)
    from app.pipeline.cluster_arbitrage import cle_paire

    entree = cache.entrees[cle_paire(publie.id, reprise.id)]
    assert entree.meme_actu is False
    assert entree.manuel is True


def test_marquer_meme_actu_ne_touche_pas_les_articles(db_session, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "cluster_arbitrage_cache_path", tmp_path / "cache.json")
    src = _src(db_session)
    publie = _art(db_session, src, n=1, title="A", status=ArticleStatus.USED)
    autre = _art(db_session, src, n=2, title="B")

    message = appliquer(db_session, publie.id, autre.id, meme_actu=True, raison="confirme")

    assert "meme actu" in message
    db_session.refresh(autre)
    assert autre.duplicate_of_id is None  # ce script ne pose pas le lien lui-meme


def test_article_introuvable_leve(db_session):
    with pytest.raises(SystemExit):
        appliquer(db_session, 9999, 9998, meme_actu=False, raison="x")
