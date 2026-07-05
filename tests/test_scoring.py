import datetime as dt

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models import Article, Source, SourceType
from app.pipeline.scoring import (
    ArticleScore,
    Category,
    apply_score,
    build_user_prompt,
    is_digest_worthy,
    is_hot,
    score_pending,
)


def make_score(**overrides):
    defaults = dict(
        relevance=70,
        hotness=50,
        category=Category.NEW_RELEASE,
        imprint="Warp Records",
        summary="Aphex Twin annonce un nouvel album pour septembre.",
    )
    defaults.update(overrides)
    return ArticleScore(**defaults)


def make_article(**overrides):
    defaults = dict(
        source_id=1,
        url="https://example.com/a",
        title="Aphex Twin annonce un nouvel album",
        summary="Extrait du flux RSS.",
        raw_hash="x" * 64,
    )
    defaults.update(overrides)
    return Article(**defaults)


# --- Le contrat JSON strict ---


def test_article_score_rejects_out_of_range_relevance():
    with pytest.raises(ValidationError):
        make_score(relevance=101)
    with pytest.raises(ValidationError):
        make_score(relevance=-1)


def test_article_score_rejects_unknown_category():
    with pytest.raises(ValidationError):
        make_score(category="pas_une_categorie")


def test_article_score_accepts_null_imprint():
    assert make_score(imprint=None).imprint is None


# --- Le prompt ---


def test_build_user_prompt_contains_key_fields():
    article = make_article()
    article.source = Source(id=1, name="Mixmag", type=SourceType.RSS, url="https://x.com")
    prompt = build_user_prompt(article)
    assert "Aphex Twin annonce un nouvel album" in prompt
    assert "Extrait du flux RSS." in prompt
    assert "Mixmag" in prompt


def test_build_user_prompt_without_summary():
    article = make_article(summary=None)
    article.source = Source(id=1, name="Mixmag", type=SourceType.RSS, url="https://x.com")
    prompt = build_user_prompt(article)
    assert "Aphex Twin" in prompt


# --- Les seuils du design doc : pertinence >= 60 digest, hotness >= 80 alerte ---


def test_digest_threshold_at_60():
    assert is_digest_worthy(make_article(relevance_score=60))
    assert not is_digest_worthy(make_article(relevance_score=59))
    assert not is_digest_worthy(make_article(relevance_score=None))


def test_hot_threshold_at_80():
    assert is_hot(make_article(hotness_score=80))
    assert not is_hot(make_article(hotness_score=79))
    assert not is_hot(make_article(hotness_score=None))


# --- Application du score sur l'article ---


def test_apply_score_writes_all_fields():
    article = make_article()
    apply_score(article, make_score())
    assert article.relevance_score == 70
    assert article.hotness_score == 50
    assert article.category == "new_release"
    assert article.imprint == "Warp Records"
    assert article.summary == "Aphex Twin annonce un nouvel album pour septembre."


# --- score_pending : sélection, écriture, erreurs ---


def seed(db, *, scored=False):
    source = Source(name="Feed", type=SourceType.RSS, url="https://example.com/f")
    db.add(source)
    db.commit()
    article = Article(
        source_id=source.id,
        url=f"https://example.com/{'scored' if scored else 'new'}",
        title="Titre",
        raw_hash=("s" if scored else "n") * 64,
        relevance_score=50 if scored else None,
        fetched_at=dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc),
    )
    db.add(article)
    db.commit()
    return article


def test_score_pending_scores_only_unscored(db_session):
    seed(db_session, scored=True)
    pending = seed(db_session)
    calls = []

    def fake_scorer(article):
        calls.append(article.id)
        return make_score()

    stats = score_pending(db_session, scorer=fake_scorer)
    assert stats.scored == 1
    assert calls == [pending.id]
    db_session.refresh(pending)
    assert pending.relevance_score == 70


def test_score_pending_isolates_per_article_errors(db_session):
    first = seed(db_session)
    second = Article(
        source_id=first.source_id, url="https://example.com/2", title="Deux",
        raw_hash="z" * 64,
    )
    db_session.add(second)
    db_session.commit()

    def flaky_scorer(article):
        if article.id == first.id:
            raise RuntimeError("API down")
        return make_score()

    stats = score_pending(db_session, scorer=flaky_scorer)
    assert stats.scored == 1
    assert stats.errors == 1
    scored = db_session.scalars(
        select(Article).where(Article.relevance_score.is_not(None))
    ).all()
    assert [a.id for a in scored] == [second.id]


def test_score_pending_respects_limit(db_session):
    source = Source(name="Feed", type=SourceType.RSS, url="https://example.com/f")
    db_session.add(source)
    db_session.commit()
    for i in range(3):
        db_session.add(Article(source_id=source.id, url=f"https://example.com/{i}",
                               title=f"T{i}", raw_hash=str(i) * 64))
    db_session.commit()

    stats = score_pending(db_session, scorer=lambda a: make_score(), limit=2)
    assert stats.scored == 2


# --- La ligne éditoriale : électro d'abord, grosses actus pop, le reste filtré ---


def test_system_prompt_encodes_editorial_line():
    from app.pipeline.scoring import SYSTEM_PROMPT

    assert "électronique" in SYSTEM_PROMPT.lower()
    assert "pop" in SYSTEM_PROMPT.lower()
    # les genres hors ligne doivent être explicitement dépriorisés
    assert "hip-hop" in SYSTEM_PROMPT.lower() or "rap" in SYSTEM_PROMPT.lower()


def test_system_prompt_values_ugc_live_clips():
    from app.pipeline.scoring import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "ugc" in lowered or "filmé" in lowered  # les clips de fans sont du contenu premium
