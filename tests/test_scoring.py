import datetime as dt

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import settings
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
        emergence=30,
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


def test_system_prompt_covers_extended_editorial_line():
    from app.pipeline.scoring import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "festival" in lowered
    assert "bretagne" in lowered or "local" in lowered
    assert "positive" in lowered or "positif" in lowered
    assert "écolog" in lowered or "environnement" in lowered


# --- Radar maison : extraction d'artistes ---


def test_article_score_accepts_artists():
    assert make_score(artists=["Fred again..", "Skrillex"]).artists == ["Fred again..", "Skrillex"]


def test_article_score_artists_defaults_empty():
    assert make_score().artists == []


def test_apply_score_stores_mentioned_artists():
    article = make_article()
    apply_score(article, make_score(artists=["Peggy Gou"]))
    assert article.mentioned_artists == ["Peggy Gou"]


def test_apply_score_empty_artists_stored_as_none():
    article = make_article()
    apply_score(article, make_score(artists=[]))
    assert article.mentioned_artists is None


def test_system_prompt_asks_for_artists():
    from app.pipeline.scoring import SYSTEM_PROMPT

    assert "artist" in SYSTEM_PROMPT.lower()


def test_system_prompt_includes_kpop():
    from app.pipeline.scoring import SYSTEM_PROMPT

    assert "k-pop" in SYSTEM_PROMPT.lower() or "kpop" in SYSTEM_PROMPT.lower()


# --- Axe émergence : le signal A&R, indépendant de l'intérêt pour le feed ---


def test_article_score_rejects_out_of_range_emergence():
    with pytest.raises(ValidationError):
        make_score(emergence=101)
    with pytest.raises(ValidationError):
        make_score(emergence=-1)


def test_apply_score_writes_emergence(db_session):
    article = make_article()
    apply_score(article, make_score(emergence=75))
    assert article.emergence_score == 75


def test_emergence_is_independent_from_relevance():
    """Le cas qui justifie l'axe : un inconnu qui sort un EP est du bruit pour le
    feed (relevance basse) mais un signal A&R fort (émergence haute)."""
    score = make_score(relevance=20, emergence=80)
    assert score.relevance == 20
    assert score.emergence == 80


def test_system_prompt_asks_for_emergence_and_local_artists():
    from app.pipeline.scoring import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "emergence" in lowered or "émergence" in lowered
    assert "underground" in lowered
    assert "local" in lowered


def test_system_prompt_keeps_pop_as_conditional_adjacent_territory():
    """Resserrement du 2026-09-23 (contexte-produit §2.4) : la pop n'est plus un
    deuxième cœur de ligne protégé sans condition -- c'est un territoire ADJACENT,
    valorisé seulement quand l'actu remplit une des conditions listées. La règle
    doit rester écrite noir sur blanc dans les DEUX sens (ni bannie, ni protégée),
    sinon le modèle déprioris ou surnote la pop tout seul -- c'est le défaut
    mesuré à l'origine de l'ancienne règle inconditionnelle qu'elle remplace, et
    que ce resserrement ne doit pas reproduire à l'envers."""
    from app.pipeline.scoring import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "territoire adjacent" in lowered
    assert "ni bannie ni protégée" in lowered
    # sens 1 : ne pas la déprioriser au seul prétexte que l'électro est le cœur de ligne
    assert "ne la déprioris pas non" in lowered
    # sens 2 : la k-pop ne garde aucun laissez-passer de genre, même règle que le reste de la pop
    assert "la k-pop suit exactement la même règle" in lowered


# --- Cascade à deux étages : le modèle local videur, Claude juge ---


def scorer_interdit(article):
    raise AssertionError("Claude ne doit pas être appelé sur un article pré-filtré")


def test_prefiltre_sous_le_seuil_n_appelle_jamais_claude(db_session):
    article = seed(db_session)
    stats = score_pending(
        db_session,
        scorer=scorer_interdit,
        prefilter=lambda a: make_score(relevance=20, summary="Résumé local."),
    )
    assert stats.prefiltered == 1
    assert stats.scored == 0
    assert stats.errors == 0
    db_session.refresh(article)
    assert article.relevance_score == 20
    assert article.scored_by == "local"


def test_prefiltre_au_dessus_du_seuil_passe_par_claude(db_session):
    article = seed(db_session)
    stats = score_pending(
        db_session,
        scorer=lambda a: make_score(relevance=88),
        prefilter=lambda a: make_score(relevance=70),
    )
    assert stats.scored == 1
    assert stats.prefiltered == 0
    db_session.refresh(article)
    # la note qui fait foi est celle de Claude, pas celle du local
    assert article.relevance_score == 88
    assert article.scored_by == "claude"


def test_seuil_strict_une_note_egale_passe_chez_claude(db_session):
    article = seed(db_session)
    stats = score_pending(
        db_session,
        scorer=lambda a: make_score(relevance=88),
        prefilter=lambda a: make_score(relevance=settings.local_prefilter_threshold),
    )
    assert stats.prefiltered == 0
    assert stats.scored == 1
    db_session.refresh(article)
    assert article.scored_by == "claude"


def test_prefiltre_muet_fait_passer_par_claude(db_session):
    """LM Studio éteint : le pré-filtre renvoie None, le pipeline continue."""
    article = seed(db_session)
    stats = score_pending(
        db_session, scorer=lambda a: make_score(relevance=42), prefilter=lambda a: None
    )
    assert stats.scored == 1
    assert stats.prefiltered == 0
    assert stats.errors == 0
    db_session.refresh(article)
    assert article.relevance_score == 42
    assert article.scored_by == "claude"


def test_prefiltre_qui_leve_ne_casse_pas_le_run(db_session):
    article = seed(db_session)

    def prefiltre_casse(a):
        raise RuntimeError("LM Studio a planté")

    stats = score_pending(
        db_session, scorer=lambda a: make_score(relevance=42), prefilter=prefiltre_casse
    )
    assert stats.scored == 1
    assert stats.errors == 0
    db_session.refresh(article)
    assert article.scored_by == "claude"


def test_sans_prefiltre_le_comportement_reste_celui_d_avant(db_session):
    article = seed(db_session)
    stats = score_pending(db_session, scorer=lambda a: make_score(relevance=42))
    assert stats.scored == 1
    assert stats.prefiltered == 0
    db_session.refresh(article)
    assert article.scored_by == "claude"


def test_prefiltered_compte_plusieurs_articles(db_session):
    source = Source(name="Feed", type=SourceType.RSS, url="https://example.com/f")
    db_session.add(source)
    db_session.commit()
    for i in range(3):
        db_session.add(Article(source_id=source.id, url=f"https://example.com/{i}",
                               title=f"T{i}", raw_hash=str(i) * 64))
    db_session.commit()

    notes = iter([10, 90, 20])
    stats = score_pending(
        db_session,
        scorer=lambda a: make_score(relevance=88),
        prefilter=lambda a: make_score(relevance=next(notes)),
    )
    assert stats.prefiltered == 2
    assert stats.scored == 1


# --- Type de compte : l'artiste d'abord, le média en filet de sécurité ---


def _article_de(type_, categorie, url="ig:exemple"):
    from app.models import SourceCategorie

    article = make_article()
    article.source = Source(
        id=1, name="Compte", type=type_, url=url,
        categorie=SourceCategorie(categorie) if categorie else None,
    )
    return article


@pytest.mark.parametrize("categorie", ["artiste", "media", "festival", "label", "club", "autre"])
def test_build_user_prompt_porte_le_type_de_compte(categorie):
    prompt = build_user_prompt(_article_de(SourceType.INSTAGRAM, categorie))
    assert f"Type de compte : {categorie}" in prompt


def test_build_user_prompt_categorie_inconnue_ne_ment_pas():
    prompt = build_user_prompt(_article_de(SourceType.X, None))
    assert "Type de compte : inconnu" in prompt


@pytest.mark.parametrize(
    "type_, attendu",
    [
        (SourceType.INSTAGRAM, "post Instagram"),
        (SourceType.RSS, "article de presse"),
        (SourceType.REDDIT_RSS, "post Reddit"),
        (SourceType.X, "tweet"),
    ],
)
def test_build_user_prompt_porte_le_format(type_, attendu):
    prompt = build_user_prompt(_article_de(type_, "media"))
    assert f"Format : {attendu}" in prompt


def test_system_prompt_priorise_les_comptes_d_artistes():
    from app.pipeline.scoring import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "type de compte" in lowered
    # 2026-09-17 : l'info d'un media n'est plus decotee (le plagiat portait sur les visuels)
    assert "plagiat" not in lowered
    assert "aucune décote" in lowered
    # posts vides : le titre par défaut de l'adapter Instagram est cité
    assert "post instagram" in lowered
    assert "hard techno" in lowered
    # la règle pop/K-pop reste en place, sous sa forme conditionnelle (2026-09-23)
    assert "territoire adjacent" in lowered


def test_la_categorie_ecologie_existe_et_est_expliquee_au_modele():
    from app.pipeline.scoring import SYSTEM_PROMPT, Category

    """L'axe « musique x ecologie » etait valorise par le prompt sans avoir de
    categorie : impossible d'habiller ces actus d'une couleur propre. La regle
    doit aussi dire ce qui n'en est PAS une, sinon toute photo de nature y passe."""
    assert Category.ECOLOGY.value == "ecology"
    assert "ecology :" in SYSTEM_PROMPT
    assert "ne suffisent PAS" in SYSTEM_PROMPT
    # L'ordre de la liste de categories fait foi : `ecology` prime sur
    # event_festival, sinon une edition qui verdit serait rangee en festival.
    assert SYSTEM_PROMPT.index("ecology :") < SYSTEM_PROMPT.index("event_festival :")
