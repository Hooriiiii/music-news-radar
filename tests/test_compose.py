import datetime as dt

import pytest
from pydantic import ValidationError

from app.content.compose import (
    CompositionError,
    PostDraft,
    Slide,
    build_user_prompt,
    create_post,
    make_claude_composer,
)
from app.models import Article, PostStatus, Source, SourceType


def _article(db):
    src = Source(name="Tsugi", type=SourceType.RSS, url="https://tsugi.fr/feed")
    db.add(src)
    db.commit()
    article = Article(
        source_id=src.id,
        url="https://tsugi.fr/vieilles-charrues",
        title="Les Vieilles Charrues devoilent leur line-up electro",
        summary="Nico Moreno, Mosimann et Cassius sont annonces.",
        raw_hash="h1",
        published_at=dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc),
        media=[{"type": "photo", "url": "https://tsugi.fr/photo.jpg"}],
    )
    db.add(article)
    db.commit()
    return article


def test_draft_refuse_moins_de_deux_slides():
    with pytest.raises(ValidationError):
        PostDraft(slides=[Slide(headline="**Un** seul")], caption="c", hashtags=[])


def test_draft_refuse_plus_de_six_slides():
    slides = [Slide(headline=f"**Slide** {i}") for i in range(7)]
    with pytest.raises(ValidationError):
        PostDraft(slides=slides, caption="c", hashtags=[])


def test_slide_refuse_une_accroche_sans_surlignage():
    with pytest.raises(ValidationError):
        Slide(headline="Une accroche parfaitement plate")


def test_slide_refuse_une_accroche_trop_longue():
    with pytest.raises(ValidationError):
        Slide(headline="**A** " + "x" * 120)


def test_build_user_prompt_contient_le_titre_et_la_source(db_session):
    article = _article(db_session)
    prompt = build_user_prompt(article)
    assert "Vieilles Charrues" in prompt
    assert "Tsugi" in prompt


def test_create_post_denormalise_et_pose_l_image_de_l_article(db_session):
    """La premiere slide herite de l'image de l'article ; les autres restent vides
    tant que l'utilisateur n'en a pas choisi une."""
    article = _article(db_session)
    draft = PostDraft(
        slides=[
            Slide(headline="**Vieilles Charrues** : le line-up"),
            Slide(headline="Rendez-vous **ce week-end**"),
        ],
        caption="Le line-up electro est tombe.",
        hashtags=["#vieillescharrues", "#electro"],
    )

    post = create_post(db_session, article, draft)

    assert post.status == PostStatus.DRAFT
    assert post.article_id == article.id
    assert post.article_title == article.title
    assert post.article_url == article.url
    assert post.source_name == "Tsugi"
    assert post.caption == "Le line-up electro est tombe."
    assert post.hashtags == ["#vieillescharrues", "#electro"]
    assert len(post.slides) == 2
    assert post.slides[0]["image_url"] == "https://tsugi.fr/photo.jpg"
    assert post.slides[1]["image_url"] is None


def _validation_error() -> ValidationError:
    """Une vraie ValidationError, telle que `messages.parse()` la leve quand la
    sortie du modele viole le schema (ex. la regle de balisage `_balisage_valide`,
    qui n'est verifiee qu'apres coup et n'est donc PAS contrainte cote API)."""
    try:
        PostDraft.model_validate({"slides": [], "caption": "x", "hashtags": []})
    except ValidationError as exc:
        return exc
    raise AssertionError("attendu une ValidationError")  # pragma: no cover


class _FakeParsedResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class _FakeMessages:
    def __init__(self, effects):
        self._effects = list(effects)

    def parse(self, **_kwargs):
        effect = self._effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class _FakeClient:
    def __init__(self, effects):
        self.messages = _FakeMessages(effects)


def test_composer_leve_composition_error_apres_echecs_repetes(db_session):
    """Une composition qui rate systematiquement la validation doit produire une
    erreur exploitable (CompositionError), pas laisser fuiter la ValidationError
    brute jusqu'a l'API (ce qui deviendrait un 500 avec stacktrace)."""
    article = _article(db_session)
    client = _FakeClient([_validation_error(), _validation_error(), _validation_error()])
    composer = make_claude_composer(client=client, max_attempts=3)

    with pytest.raises(CompositionError):
        composer(article)


def test_composer_reessaie_puis_reussit(db_session):
    """La boucle de tentatives sert a quelque chose : un premier echec suivi d'un
    succes doit bien aboutir a un brouillon, pas a une erreur."""
    article = _article(db_session)
    bon_draft = PostDraft(
        slides=[Slide(headline="**Vieilles Charrues** : le line-up"), Slide(headline="**Info**")],
        caption="c",
        hashtags=[],
    )
    client = _FakeClient([_validation_error(), _FakeParsedResponse(bon_draft)])
    composer = make_claude_composer(client=client, max_attempts=3)

    draft = composer(article)

    assert draft is bon_draft
