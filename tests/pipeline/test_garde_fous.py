"""Garde-fous deterministes appliques apres la note du modele.

Mesure du 2026-09-17 sur donnees reelles (Haiku) : les regles 5 et 6 du prompt
sont appliquees de facon inegale. Les exemples ci-dessous sont ces cas reels.
"""

import pytest

from app.models import Article, ScoredBy, Source, SourceCategorie, SourceType
from app.pipeline.scoring import (
    ArticleScore,
    Category,
    appliquer_garde_fous,
    post_quasi_vide,
    score_pending,
)
from app.sources.instagram import TITRE_PAR_DEFAUT

BANDEAU = "\n\n[Instagram : 8000 likes · 120 commentaires · 900000 abonnes]"


def note(relevance, hotness=None, emergence=10, **autres):
    return ArticleScore(
        relevance=relevance,
        hotness=relevance if hotness is None else hotness,
        emergence=emergence,
        category=Category.ANNOUNCEMENT,
        summary="Resume.",
        **autres,
    )


def article(caption, *, type_=SourceType.INSTAGRAM, categorie=SourceCategorie.ARTISTE,
            title=None):
    from app.sources.instagram import titre_depuis_caption

    a = Article(
        url="https://www.instagram.com/p/X/",
        title=title or titre_depuis_caption(caption),
        summary=(caption or "") + BANDEAU if type_ == SourceType.INSTAGRAM else caption,
        raw_hash="h" * 64,
    )
    a.source = Source(id=1, name="Compte", type=type_, url="ig:compte", categorie=categorie)
    return a


# --- Garde-fou 1 : post Instagram quasi vide ---


@pytest.mark.parametrize(
    "caption, relevance",
    [("[📸]", 65), ("🗂️ I-VIEW", 65), ("LE SSERAFIM feat.", 72),
     ("𝚖𝚊𝚍𝚎 𝚖𝚢 𝚍𝚊𝚢 ✨ #ive @ive_official", 70)],
)
def test_post_quasi_vide_plafonne_a_29(caption, relevance):
    resultat = appliquer_garde_fous(article(caption), note(relevance, hotness=60, emergence=44))
    assert resultat.relevance == 29
    assert resultat.hotness == 29
    assert resultat.emergence == 44  # l'axe A&R n'est jamais touche


def test_post_quasi_vide_ne_remonte_pas_une_note_basse():
    resultat = appliquer_garde_fous(article("[📸]"), note(12, hotness=5))
    assert (resultat.relevance, resultat.hotness) == (12, 5)


def test_le_bandeau_de_metriques_ne_compte_pas_comme_des_mots():
    a = article("[📸]")
    assert "likes" in a.summary
    assert post_quasi_vide(a)


def test_titre_par_defaut_sans_caption_connue_est_quasi_vide():
    a = article(None)
    a.summary = None
    assert a.title == TITRE_PAR_DEFAUT
    assert post_quasi_vide(a)


def test_un_vrai_post_d_artiste_garde_sa_note():
    a = article("Mon nouvel EP sort vendredi chez Ninja Tune, precommandes ouvertes")
    assert not post_quasi_vide(a)
    assert appliquer_garde_fous(a, note(72)).relevance == 72


def test_le_garde_fou_vide_ne_vise_que_instagram():
    a = article("Court", type_=SourceType.RSS, categorie=SourceCategorie.ARTISTE)
    assert not post_quasi_vide(a)
    assert appliquer_garde_fous(a, note(70)).relevance == 70


def test_sans_source_rien_ne_change():
    a = Article(url="u", title="x", summary="x", raw_hash="z" * 64)
    assert appliquer_garde_fous(a, note(80)).relevance == 80


# --- Plus de plafond media (decision du 2026-09-17) ---
# L'actu GTA VI x Fred again.. a ete enterree a 45-59 : la decote et le plafond
# punissaient l'information selon le canal. Le plagiat portait sur les visuels et
# la formulation, traites ailleurs.

GUETTAPEN_MENTAL = (
    "LA SANTÉ MENTALE DE PETIT BISCUIT AU PLUS BAS 😔\n\nLe producteur francais se confie."
)


@pytest.mark.parametrize(
    "categorie",
    [SourceCategorie.MEDIA, SourceCategorie.ARTISTE, SourceCategorie.FESTIVAL, None],
)
def test_la_categorie_ne_plafonne_plus_la_note(categorie):
    a = article(GUETTAPEN_MENTAL, categorie=categorie)
    resultat = appliquer_garde_fous(a, note(68, hotness=70, emergence=33))
    assert (resultat.relevance, resultat.hotness, resultat.emergence) == (68, 70, 33)


def test_la_presse_rss_garde_sa_note():
    a = article("Titre de presse", type_=SourceType.RSS, categorie=SourceCategorie.MEDIA)
    assert appliquer_garde_fous(a, note(68)).relevance == 68


def test_un_post_vide_de_media_reste_plafonne():
    a = article("🚨🚨", categorie=SourceCategorie.MEDIA)
    assert appliquer_garde_fous(a, note(80)).relevance == 29


def test_le_prompt_ne_decote_plus_les_medias():
    from app.pipeline.scoring import SYSTEM_PROMPT

    assert "PLAGIAT" not in SYSTEM_PROMPT
    assert "plafonne" not in SYSTEM_PROMPT
    assert "actu_majeure" not in SYSTEM_PROMPT
    assert "AUCUNE décote" in SYSTEM_PROMPT


def test_actu_majeure_n_existe_plus_dans_le_schema():
    assert "actu_majeure" not in ArticleScore.model_json_schema()["properties"]


def test_une_reponse_qui_porte_encore_actu_majeure_reste_valide():
    """Un pre-filtre local charge avec l'ancien schema peut encore l'envoyer."""
    brut = (
        '{"relevance": 70, "hotness": 50, "emergence": 10, "category": "announcement",'
        ' "summary": "R.", "artists": [], "actu_majeure": true}'
    )
    assert ArticleScore.model_validate_json(brut).relevance == 70


# --- Branchement dans score_pending ---


def scorer_interdit(a):
    raise AssertionError("Claude ne doit pas etre appele")


def _persister(db, caption, *, categorie=SourceCategorie.ARTISTE, type_=SourceType.INSTAGRAM):
    source = Source(name="Compte", type=type_, url="ig:compte", categorie=categorie)
    db.add(source)
    db.commit()
    a = article(caption, type_=type_, categorie=categorie)
    a.source = source
    db.add(a)
    db.commit()
    return a


def test_post_vide_sans_prefiltre_n_appelle_pas_claude(db_session):
    a = _persister(db_session, "[📸]")
    stats = score_pending(db_session, scorer=scorer_interdit)
    assert stats.errors == 0
    assert stats.quasi_vides == 1
    db_session.refresh(a)
    assert a.scored_by == ScoredBy.LOCAL
    assert (a.relevance_score, a.hotness_score, a.emergence_score) == (10, 10, 0)
    assert a.category == Category.OTHER.value
    # la caption reste lisible au dashboard, sans le bandeau de metriques
    assert a.summary == "[📸]"


def test_post_vide_garde_la_note_locale_plafonnee(db_session):
    a = _persister(db_session, "🗂️ I-VIEW")
    stats = score_pending(
        db_session, scorer=scorer_interdit, prefilter=lambda x: note(65, emergence=70)
    )
    assert stats.quasi_vides == 1
    db_session.refresh(a)
    assert a.scored_by == ScoredBy.LOCAL
    assert (a.relevance_score, a.hotness_score, a.emergence_score) == (29, 29, 70)


def test_media_note_par_claude_garde_sa_note(db_session):
    a = _persister(db_session, GUETTAPEN_MENTAL, categorie=SourceCategorie.MEDIA)
    score_pending(db_session, scorer=lambda x: note(68, hotness=70))
    db_session.refresh(a)
    assert (a.relevance_score, a.hotness_score) == (68, 70)
    assert a.scored_by == ScoredBy.CLAUDE


def test_note_locale_d_un_media_n_est_plus_plafonnee(db_session):
    a = _persister(db_session, GUETTAPEN_MENTAL, categorie=SourceCategorie.MEDIA)
    score_pending(db_session, scorer=scorer_interdit, prefilter=lambda x: note(30, hotness=90))
    db_session.refresh(a)
    assert (a.relevance_score, a.hotness_score) == (30, 90)
