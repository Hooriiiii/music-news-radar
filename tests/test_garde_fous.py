"""Garde-fous deterministes appliques apres la note du modele.

Mesure du 2026-09-17 sur donnees reelles (Haiku) : les regles 5 et 6 du prompt
sont appliquees de facon inegale. Les exemples ci-dessous sont ces cas reels.
"""

import pytest

from app.config import settings
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


# --- Garde-fou 2 : plafond media, sauf actu majeure ---


GUETTAPEN_MENTAL = (
    "LA SANTÉ MENTALE DE PETIT BISCUIT AU PLUS BAS 😔\n\nLe producteur francais se confie."
)
GUETTAPEN_EVOLUTION = "LE NÉERLANDAIS REVIENT SUR SON ÉVOLUTION"
GUETTAPEN_RIPOST = "LA LOI RIPOST MET EN DANGER LA SCÈNE ÉLECTRONIQUE FRANÇAISE ⚠️"


@pytest.mark.parametrize(
    "caption, relevance, hotness",
    [(GUETTAPEN_MENTAL, 68, 70), (GUETTAPEN_EVOLUTION, 70, 45)],
)
def test_media_ordinaire_plafonne_a_59(caption, relevance, hotness):
    a = article(caption, categorie=SourceCategorie.MEDIA)
    resultat = appliquer_garde_fous(a, note(relevance, hotness=hotness, emergence=33))
    assert resultat.relevance == 59
    assert resultat.hotness == min(hotness, 59)
    assert resultat.emergence == 33


def test_actu_majeure_d_un_media_garde_sa_note():
    a = article(GUETTAPEN_RIPOST, categorie=SourceCategorie.MEDIA)
    resultat = appliquer_garde_fous(a, note(75, hotness=78, actu_majeure=True))
    assert (resultat.relevance, resultat.hotness) == (75, 78)


def test_plafond_media_vaut_aussi_pour_la_presse_rss():
    a = article("Titre de presse", type_=SourceType.RSS, categorie=SourceCategorie.MEDIA)
    assert appliquer_garde_fous(a, note(68)).relevance == 59


def test_actu_majeure_ne_sauve_pas_un_post_vide():
    a = article("🚨🚨", categorie=SourceCategorie.MEDIA)
    assert appliquer_garde_fous(a, note(80, actu_majeure=True)).relevance == 29


@pytest.mark.parametrize(
    "categorie", [SourceCategorie.ARTISTE, SourceCategorie.FESTIVAL, None]
)
def test_le_plafond_media_ne_vise_que_les_medias(categorie):
    a = article(GUETTAPEN_MENTAL, categorie=categorie)
    assert appliquer_garde_fous(a, note(68)).relevance == 68


def test_actu_majeure_vaut_false_par_defaut():
    assert note(50).actu_majeure is False


def test_le_prompt_definit_actu_majeure():
    from app.pipeline.scoring import SYSTEM_PROMPT

    assert "actu_majeure" in SYSTEM_PROMPT
    assert "actu_majeure" in ArticleScore.model_json_schema()["properties"]


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


def test_media_note_par_claude_est_plafonne(db_session):
    a = _persister(db_session, GUETTAPEN_MENTAL, categorie=SourceCategorie.MEDIA)
    score_pending(db_session, scorer=lambda x: note(68, hotness=70))
    db_session.refresh(a)
    assert (a.relevance_score, a.hotness_score) == (59, 59)
    assert a.scored_by == ScoredBy.CLAUDE


def test_le_plafond_media_ne_fait_pas_ecarter_par_le_prefiltre(db_session):
    """59 > seuil du pre-filtre : un media note 70 en local passe chez Claude."""
    assert settings.local_prefilter_threshold <= 59
    a = _persister(db_session, GUETTAPEN_RIPOST, categorie=SourceCategorie.MEDIA)
    stats = score_pending(
        db_session,
        scorer=lambda x: note(75, actu_majeure=True),
        prefilter=lambda x: note(70),
    )
    assert stats.scored == 1
    db_session.refresh(a)
    assert a.relevance_score == 75


def test_note_locale_sous_le_seuil_est_aussi_plafonnee(db_session):
    a = _persister(db_session, GUETTAPEN_MENTAL, categorie=SourceCategorie.MEDIA)
    score_pending(db_session, scorer=scorer_interdit, prefilter=lambda x: note(30, hotness=90))
    db_session.refresh(a)
    assert (a.relevance_score, a.hotness_score) == (30, 59)
