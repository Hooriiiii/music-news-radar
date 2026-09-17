from app.models import Source, SourceType
from scripts.desactiver_sources_x import desactiver_sources_x


def _source(db, nom, type_, active=True):
    s = Source(name=nom, type=type_, url=f"https://exemple/{nom}", active=active)
    db.add(s)
    db.commit()
    return s


def test_plan_ne_touche_rien(db_session):
    x = _source(db_session, "David Guetta", SourceType.X)
    touchees = desactiver_sources_x(db_session)
    assert touchees == ["David Guetta"]
    assert x.active is True


def test_appliquer_desactive_les_sources_x(db_session):
    x = _source(db_session, "David Guetta", SourceType.X)
    desactiver_sources_x(db_session, appliquer=True)
    db_session.refresh(x)
    assert x.active is False


def test_ne_touche_pas_les_autres_types(db_session):
    rss = _source(db_session, "Tsugi", SourceType.RSS)
    desactiver_sources_x(db_session, appliquer=True)
    db_session.refresh(rss)
    assert rss.active is True


def test_idempotent_une_source_deja_eteinte_n_est_pas_listee(db_session):
    _source(db_session, "Drake", SourceType.X, active=False)
    assert desactiver_sources_x(db_session) == []
