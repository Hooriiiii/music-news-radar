"""L'ordre de notation, et l'arret au premier refus de credits.

Apres une panne, la veille (20 articles par passage) et le pipeline notaient
l'arriere vieux avant le frais, alors que seul le frais peut donner un post. Et
chaque article pris faisait un appel refuse : 4 000 a 14 600 par jour dans les logs.
"""

import datetime as dt
import json
from types import SimpleNamespace

import pytest

import app.pipeline.credits_anthropic as credits
from app.config import settings
from app.models import Article, Source, SourceType
from app.pipeline.health import CREDITS_EPUISES, diagnostiquer
from app.pipeline.retention import purge_old_articles
from app.pipeline.scoring import ArticleScore, Category, score_pending

UTC = dt.timezone.utc
REFUS_API = "Error code: 400 - Your credit balance is too low to access the Anthropic API."


def _note(_article=None):
    return ArticleScore(
        relevance=70, hotness=40, emergence=10, category=Category.ANNOUNCEMENT, imprint=None,
        summary="s", artists=[],
    )


@pytest.fixture
def source(db_session):
    db_session.autoflush = False  # le reglage de PRODUCTION (`app/db.py`)
    source = Source(name="media", type=SourceType.RSS, url="https://media/feed")
    db_session.add(source)
    db_session.commit()
    return source


def _article(db, source, *, heures, rang, note=None, fetched_at=None):
    article = Article(
        source_id=source.id,
        url=f"https://media/{rang}",
        title=f"Titre {rang}",
        summary="Un resume assez long pour ne pas passer pour un post quasi vide.",
        raw_hash=f"h{rang}",
        relevance_score=note,
        fetched_at=fetched_at or dt.datetime.now(UTC) - dt.timedelta(hours=heures),
    )
    db.add(article)
    db.commit()
    return article


def _espion(calls, *, erreur=None):
    def scorer(article):
        calls.append(article.id)
        if erreur is not None:
            raise erreur
        return _note()

    return scorer


# --- L'ordre ---


def test_le_frais_passe_avant_l_arriere(db_session, source):
    vieux = _article(db_session, source, heures=30, rang=1)
    frais = _article(db_session, source, heures=1, rang=2)
    moyen = _article(db_session, source, heures=10, rang=3)
    calls = []

    score_pending(db_session, scorer=_espion(calls), prefilter=None)

    assert calls == [frais.id, moyen.id, vieux.id]


def test_a_fetched_at_egal_le_plus_grand_id_passe_d_abord(db_session, source):
    """Un lot d'ingestion partage souvent son horodatage : sans second critere,
    l'ordre dependrait du plan de la base."""
    meme = dt.datetime(2026, 7, 1, tzinfo=UTC)
    premier = _article(db_session, source, heures=0, rang=1, fetched_at=meme)
    second = _article(db_session, source, heures=0, rang=2, fetched_at=meme)
    calls = []

    score_pending(db_session, scorer=_espion(calls), prefilter=None)

    assert calls == [second.id, premier.id]


def test_une_limite_laisse_passer_le_frais_et_le_passage_sans_limite_vide_le_reste(db_session, source):
    """La famine est impossible : la limite n'existe que pour la veille, et le
    passage horaire, sans limite, prend tout ce qu'elle a laisse."""
    articles = [_article(db_session, source, heures=heures, rang=heures) for heures in (40, 30, 20, 10, 1)]
    calls = []

    score_pending(db_session, scorer=_espion(calls), prefilter=None, limit=2)
    assert calls == [articles[4].id, articles[3].id]

    score_pending(db_session, scorer=_espion(calls), prefilter=None)
    assert calls[2:] == [articles[2].id, articles[1].id, articles[0].id]


# --- L'arret au premier refus ---


def test_le_premier_refus_de_credits_arrete_le_passage(db_session, source):
    for rang in range(1, 6):
        _article(db_session, source, heures=rang, rang=rang)
    calls = []

    stats = score_pending(db_session, scorer=_espion(calls, erreur=RuntimeError(REFUS_API)), prefilter=None)

    assert len(calls) == 1
    assert stats.errors == 1
    assert credits.lire_etat(_etat()).epuises is True


def test_un_refus_en_cours_de_passage_garde_ce_qui_est_deja_note(db_session, source):
    frais = _article(db_session, source, heures=1, rang=1)
    _article(db_session, source, heures=2, rang=2)
    _article(db_session, source, heures=3, rang=3)
    calls = []

    def scorer(article):
        calls.append(article.id)
        if len(calls) == 2:
            raise RuntimeError(REFUS_API)
        return _note()

    stats = score_pending(db_session, scorer=scorer, prefilter=None)

    assert (stats.scored, stats.errors, len(calls)) == (1, 1, 2)
    db_session.refresh(frais)
    assert frais.relevance_score == 70


@pytest.mark.parametrize(
    "erreur",
    [ConnectionError("Connection error."), RuntimeError("Error code: 529 - overloaded")],
    ids=["reseau", "surcharge"],
)
def test_une_autre_erreur_n_arrete_pas_le_passage(db_session, source, erreur):
    for rang in range(1, 4):
        _article(db_session, source, heures=rang, rang=rang)
    calls = []

    stats = score_pending(db_session, scorer=_espion(calls, erreur=erreur), prefilter=None)

    assert len(calls) == 3
    assert stats.errors == 3


def _etat():
    return json.loads((settings.meta_token_path.parent / credits.FICHIER_ETAT).read_text())


# --- Le rapport nomme toujours la panne, meme sur une seule erreur ---


def test_le_rapport_nomme_les_credits_avec_une_seule_erreur(db_session, source):
    for rang in range(1, 4):
        _article(db_session, source, heures=rang, rang=rang)

    stats = score_pending(db_session, scorer=_espion([], erreur=RuntimeError(REFUS_API)), prefilter=None)

    assert stats.errors == 1
    assert any(CREDITS_EPUISES in probleme for probleme in diagnostiquer(db_session, [], stats))


def test_le_rapport_et_l_arret_reconnaissent_la_meme_signature(db_session, source):
    """`est_refus_de_credits` lit en minuscules. Si le rapport lisait la casse exacte,
    un message a majuscule arreterait le passage et se dirait « en échec sur 1 article »."""
    _article(db_session, source, heures=1, rang=1)
    _article(db_session, source, heures=2, rang=2)
    refus = RuntimeError("Credit Balance Is Too Low")

    stats = score_pending(db_session, scorer=_espion([], erreur=refus), prefilter=None)

    assert stats.errors == 1
    assert any(CREDITS_EPUISES in probleme for probleme in diagnostiquer(db_session, [], stats))


def test_le_refus_prend_le_dernier_mot_apres_une_panne_reseau(db_session, source):
    """Une coupure sur le premier article, le refus sur le second : le rapport nomme le refus."""
    _article(db_session, source, heures=1, rang=1)
    _article(db_session, source, heures=2, rang=2)
    _article(db_session, source, heures=3, rang=3)
    erreurs = iter([ConnectionError("Connection error."), RuntimeError(REFUS_API)])

    def scorer(_article):
        raise next(erreurs)

    stats = score_pending(db_session, scorer=scorer, prefilter=None)

    assert stats.errors == 2
    assert any(CREDITS_EPUISES in probleme for probleme in diagnostiquer(db_session, [], stats))


def test_une_panne_reseau_ne_se_dit_pas_credits(db_session):
    stats = SimpleNamespace(errors=1, derniere_erreur="Connection error.")
    assert not any(CREDITS_EPUISES in probleme for probleme in diagnostiquer(db_session, [], stats))


# --- La purge ne depend pas de l'ordre ---


def test_le_sursis_protege_l_article_que_l_arret_a_laisse_de_cote(db_session, source):
    """L'arret laisse des articles sans note : ce sont des « jamais juges », proteges
    par le sursis (7 j) et non par la retention (2 j). Purge sans argument d'ordre."""
    protege = _article(db_session, source, heures=24 * 3, rang=1).id
    note = _article(db_session, source, heures=24 * 3, rang=2, note=30).id
    trop_vieux = _article(db_session, source, heures=24 * 8, rang=3).id

    score_pending(db_session, scorer=_espion([], erreur=RuntimeError(REFUS_API)), prefilter=None)
    # L'article le plus recent a pris le refus ; les deux autres n'ont pas ete vus.
    purge_old_articles(db_session, retention_days=2, sursis_days=7)

    restants = {identifiant for (identifiant,) in db_session.query(Article.id).all()}
    assert protege in restants
    assert note not in restants
    assert trop_vieux not in restants
