import datetime as dt

import pytest
from pydantic import ValidationError

from app.content.compose import (
    CompositionError,
    CoverVariante,
    PostDraft,
    build_user_prompt,
    create_post,
    make_claude_composer,
)
from app.models import Article, Source, SourceType


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


def _variante(n):
    return CoverVariante(
        headline=f"**Accroche {n}**", title=f"**Titre {n}**", subtitle=f"**Sous {n}**"
    )


def test_une_variante_exige_les_trois_textes_balises():
    """Chaque variante porte l'accroche du post ET le couple titre/sous-titre du
    reel : c'est ce qui rend la bascule POST/REELS instantanee, sans redemander a
    Claude. Les trois passent par la meme regle de surlignage."""
    with pytest.raises(ValidationError):
        CoverVariante(headline="sans surlignage", title="**Titre**", subtitle="**Sous**")


def test_le_brouillon_exige_exactement_quatre_variantes():
    for nombre in (3, 5):
        with pytest.raises(ValidationError):
            PostDraft(variantes=[_variante(i) for i in range(nombre)], caption="c", hashtags=[])

    draft = PostDraft(variantes=[_variante(i) for i in range(4)], caption="c", hashtags=[])
    assert len(draft.variantes) == 4


def test_build_user_prompt_contient_le_titre_et_la_source(db_session):
    article = _article(db_session)
    prompt = build_user_prompt(article)
    assert "Vieilles Charrues" in prompt
    assert "Tsugi" in prompt


def _draft():
    return PostDraft(variantes=[_variante(i) for i in range(4)], caption="c", hashtags=["#e"])


def test_create_post_pose_les_quatre_variantes_sans_retenue(db_session):
    """La retenue est nulle a la creation : l'utilisateur n'a pas encore choisi, et
    c'est ce qui rend le brouillon reprenable sans repayer un appel Claude."""
    article = _article(db_session)

    post = create_post(db_session, article, _draft())

    assert len(post.variants["variantes"]) == 4
    assert post.variants["retenue"] is None


def test_create_post_pose_une_cover_avec_le_fond_de_larticle(db_session):
    """La slide 1 est une cover, pas un media : c'est la seule que le renderer
    rendra."""
    article = _article(db_session)

    post = create_post(db_session, article, _draft())

    (cover,) = post.slides
    assert cover["kind"] == "cover"
    assert cover["image_url"] == "https://tsugi.fr/photo.jpg"
    # Les trois textes sont poses des la creation, pour que la bascule de gabarit
    # n'ait rien a redemander.
    assert cover["headline"] and cover["title"] and cover["subtitle"]


def test_create_post_prend_la_vignette_dune_video_comme_fond(db_session):
    """Une actu video n'a pas de photo : sa vignette est un fond parfaitement
    valable, et c'est meme le cas normal."""
    article = _article(db_session)
    article.media = [
        {"type": "video", "url": "https://v.mp4", "preview_url": "https://thumb.jpg"}
    ]
    db_session.commit()

    post = create_post(db_session, article, _draft())

    assert post.slides[0]["image_url"] == "https://thumb.jpg"


def _validation_error() -> ValidationError:
    """Une vraie ValidationError, telle que `messages.parse()` la leve quand la
    sortie du modele viole le schema (ex. la regle de balisage `_balisage_valide`,
    qui n'est verifiee qu'apres coup et n'est donc PAS contrainte cote API)."""
    try:
        PostDraft.model_validate({"variantes": [], "caption": "x", "hashtags": []})
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
        variantes=[_variante(i) for i in range(4)],
        caption="c",
        hashtags=[],
    )
    client = _FakeClient([_validation_error(), _FakeParsedResponse(bon_draft)])
    composer = make_claude_composer(client=client, max_attempts=3)

    draft = composer(article)

    assert draft is bon_draft
