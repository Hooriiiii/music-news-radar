from app.models import Source, SourceCategorie, SourceType
from scripts.categoriser_sources import categorie_attendue, categoriser_sources


def _source(db, nom, type_, url, categorie=None):
    s = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(s)
    db.commit()
    return s


def test_mapping_instagram():
    ig = SourceType.INSTAGRAM
    assert categorie_attendue(ig, "ig:tsugimag") == SourceCategorie.MEDIA
    assert categorie_attendue(ig, "https://www.instagram.com/Mixmag/") == SourceCategorie.MEDIA
    assert categorie_attendue(ig, "ig:@nuits_sonores") == SourceCategorie.FESTIVAL
    assert categorie_attendue(ig, "ig:edbanger") == SourceCategorie.LABEL
    assert categorie_attendue(ig, "ig:rexclub") == SourceCategorie.CLUB
    # Keinemusik est un collectif d'artistes, pas un label
    assert categorie_attendue(ig, "ig:keinemusik") == SourceCategorie.ARTISTE
    assert categorie_attendue(ig, "ig:fredagainagainagain") == SourceCategorie.ARTISTE


def test_mapping_autres_types():
    assert categorie_attendue(SourceType.RSS, "https://tsugi.fr/feed") == SourceCategorie.MEDIA
    assert categorie_attendue(SourceType.REDDIT_RSS, "https://reddit.com/r/techno") == (
        SourceCategorie.MEDIA
    )
    assert categorie_attendue(SourceType.X, "https://x.com/guetta") is None


def test_plan_ne_touche_rien(db_session):
    s = _source(db_session, "Tsugi IG", SourceType.INSTAGRAM, "ig:tsugimag")
    plan = categoriser_sources(db_session)
    assert plan == [("Tsugi IG", None, SourceCategorie.MEDIA)]
    db_session.refresh(s)
    assert s.categorie is None


def test_appliquer_puis_idempotent(db_session):
    a = _source(db_session, "Artiste", SourceType.INSTAGRAM, "ig:amelielens")
    x = _source(db_session, "X", SourceType.X, "https://x.com/a")
    rss = _source(db_session, "Mixmag", SourceType.RSS, "https://mixmag.net/rss.xml")
    categoriser_sources(db_session, appliquer=True)
    for s in (a, x, rss):
        db_session.refresh(s)
    assert a.categorie == SourceCategorie.ARTISTE
    assert x.categorie is None
    assert rss.categorie == SourceCategorie.MEDIA
    assert categoriser_sources(db_session) == []


def test_corrige_une_categorie_fausse(db_session):
    _source(db_session, "Ultra", SourceType.INSTAGRAM, "ig:ultra", SourceCategorie.ARTISTE)
    assert categoriser_sources(db_session) == [
        ("Ultra", SourceCategorie.ARTISTE, SourceCategorie.FESTIVAL)
    ]


def test_le_defaut_n_ecrase_pas_un_choix_manuel(db_session):
    # un compte inconnu classe a la main (dashboard) garde son choix : seul le
    # mapping EXPLICITE fait foi contre une valeur deja posee
    _source(db_session, "Collectif", SourceType.INSTAGRAM, "ig:uncollectif", SourceCategorie.AUTRE)
    _source(db_session, "Blog", SourceType.RSS, "https://blog/feed", SourceCategorie.ARTISTE)
    assert categoriser_sources(db_session) == []
