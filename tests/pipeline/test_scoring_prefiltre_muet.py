"""Un pre-filtre MUET doit se compter, sinon la panne est invisible.

Mesure du 2026-09-24 : LM Studio etait eteint, chaque passage horaire payait
le plein tarif chez Claude, et RIEN ne le disait -- le pre-filtre ne produit
que des WARNING par article, et `run_pipeline` masque sa ligne tant qu'il n'a
rien ecarte. C'est la forme exacte de la panne qui a dure cinq semaines sur
l'agent digest.

`db_session.autoflush = False` : c'est le reglage de PRODUCTION (`app/db.py`),
et un test en autoflush par defaut a deja masque deux fois un defaut ici.
"""

import datetime as dt

from app.models import Article, Source, SourceType
from app.pipeline.scoring import ArticleScore, Category, score_pending

UTC = dt.timezone.utc


def _article(db, rang):
    source = db.query(Source).first()
    if source is None:
        source = Source(name="Presse", type=SourceType.RSS, url="https://presse/rss")
        db.add(source)
        db.commit()
    article = Article(
        source_id=source.id,
        url=f"https://exemple/{rang}",
        title=f"Un festival electro devoile sa programmation complete {rang}",
        summary="Le festival annonce vingt artistes pour son edition de printemps.",
        raw_hash=f"h{rang}",
        published_at=dt.datetime.now(UTC),
    )
    db.add(article)
    db.commit()
    return article


def _note(relevance=80):
    return ArticleScore(
        relevance=relevance,
        emergence=20,
        hotness=30,
        category=Category.OTHER,
        summary="resume",
        mentioned_artists=[],
    )


def test_un_prefiltre_muet_est_compte(db_session):
    """LM Studio eteint : le pre-filtre rend None, l'article part chez Claude
    (la note reste bonne) -- mais on a PAYE ce qu'il existe pour eviter."""
    db_session.autoflush = False
    _article(db_session, 1)
    _article(db_session, 2)
    stats = score_pending(
        db_session,
        scorer=lambda article, contexte=None: _note(),
        prefilter=lambda article: None,
    )
    assert stats.prefiltre_muet == 2
    assert stats.scored == 2
    assert stats.prefiltered == 0


def test_un_prefiltre_qui_repond_n_est_jamais_compte_comme_muet(db_session):
    db_session.autoflush = False
    _article(db_session, 3)
    stats = score_pending(
        db_session,
        scorer=lambda article, contexte=None: _note(),
        prefilter=lambda article: _note(relevance=90),
    )
    assert stats.prefiltre_muet == 0


def test_sans_prefiltre_branche_rien_n_est_compte_comme_muet(db_session):
    """Une installation sans LM Studio n'est pas une panne : le compteur doit
    rester a zero, sinon le rapport crierait au loup sur chaque passage."""
    db_session.autoflush = False
    _article(db_session, 4)
    stats = score_pending(
        db_session,
        scorer=lambda article, contexte=None: _note(),
        prefilter=None,
    )
    assert stats.prefiltre_muet == 0
