import datetime as dt

from app.models import Article, Post, PostStatus, Source, SourceType


def _src(db):
    src = Source(name="Tsugi", type=SourceType.RSS, url="https://tsugi.fr/feed")
    db.add(src)
    db.commit()
    return src


def test_post_defaults(db_session):
    """Un post nait en brouillon, sur le template Kinda, sans date de publication."""
    src = _src(db_session)
    article = Article(
        source_id=src.id,
        url="https://tsugi.fr/a",
        title="Les Vieilles Charrues devoilent leur line-up",
        raw_hash="h1",
        published_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(article)
    db_session.commit()

    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=src.name,
        caption="Le line-up est tombe.",
        slides=[{"headline": "**Vieilles Charrues** : le line-up", "image_url": None}],
    )
    db_session.add(post)
    db_session.commit()

    assert post.status == PostStatus.DRAFT
    assert post.template == "kinda_post"
    assert post.published_at is None
    assert post.created_at is not None
    assert post.slides[0]["headline"].startswith("**Vieilles Charrues**")


def test_post_survit_a_la_suppression_de_l_article(db_session):
    """Les champs denormalises rendent le post lisible sans son article."""
    src = _src(db_session)
    article = Article(source_id=src.id, url="https://tsugi.fr/b", title="Titre", raw_hash="h2")
    db_session.add(article)
    db_session.commit()

    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=src.name,
        caption="c",
        slides=[],
    )
    db_session.add(post)
    db_session.commit()

    db_session.delete(article)
    db_session.commit()

    survivant = db_session.query(Post).one()
    assert survivant.article_title == "Titre"
    assert survivant.article_url == "https://tsugi.fr/b"
    assert survivant.source_name == "Tsugi"
