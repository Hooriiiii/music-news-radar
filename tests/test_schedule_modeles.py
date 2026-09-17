import datetime as dt

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Article, Creneau, EtatCreneau, Source, SourceType

HEURE = dt.datetime(2026, 9, 20, 7, 12, tzinfo=dt.timezone.utc)


def test_un_creneau_neuf_est_libre_et_non_epingle(db_session):
    creneau = Creneau(publier_a=HEURE)
    db_session.add(creneau)
    db_session.commit()
    assert (creneau.etat, creneau.epingle, creneau.article_id, creneau.fichiers) == (
        EtatCreneau.LIBRE, False, None, None,
    )


def test_deux_creneaux_ne_partagent_jamais_la_meme_heure(db_session):
    db_session.add_all([Creneau(publier_a=HEURE), Creneau(publier_a=HEURE)])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_un_article_n_est_pas_exclu_par_defaut(db_session):
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://t/a", title="A", raw_hash="h")
    db_session.add(article)
    db_session.commit()
    assert article.file_exclu_raison is None
