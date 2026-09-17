"""Le compte a crediter : un reel ne part jamais sans lui."""

from types import SimpleNamespace

from app.content.compose import CoverVariante, PostDraft, create_post
from app.content.credit import credit_video
from app.models import Article, Source, SourceCategorie, SourceType


def _article(type_, url):
    return SimpleNamespace(source=SimpleNamespace(type=type_, url=url))


def test_une_source_instagram_donne_son_compte():
    assert credit_video(_article(SourceType.INSTAGRAM, "ig:saralandry")) == "@saralandry"


def test_une_source_rss_ne_donne_pas_de_credit():
    assert credit_video(_article(SourceType.RSS, "https://tsugi.fr/feed")) is None


def test_sans_source_pas_de_credit():
    assert credit_video(SimpleNamespace(source=None)) is None


def test_create_post_pose_le_credit(db_session):
    source = Source(name="Sara Landry", type=SourceType.INSTAGRAM, url="ig:saralandry",
                    categorie=SourceCategorie.ARTISTE)
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://www.instagram.com/p/AAA/", title="Live", raw_hash="c1")
    db_session.add(article)
    db_session.commit()
    variante = CoverVariante(headline="**Accroche**", title="**Titre**", subtitle="**Sous**")
    draft = PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=["#techno"])
    post = create_post(db_session, article, draft, template="kinda_reel")
    assert (post.credit, post.template) == ("@saralandry", "kinda_reel")
