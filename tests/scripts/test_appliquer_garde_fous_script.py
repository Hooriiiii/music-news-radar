from app.models import Article, ArticleStatus, Source, SourceCategorie, SourceType
from app.sources.instagram import TITRE_PAR_DEFAUT
from scripts.reparations.appliquer_garde_fous import appliquer_garde_fous_existants


def _source(db, type_=SourceType.INSTAGRAM, categorie=SourceCategorie.ARTISTE):
    s = Source(name=f"S{type_.value}{categorie}", type=type_, url="ig:x", categorie=categorie)
    db.add(s)
    db.commit()
    return s


def _article(db, source, title, rel=65, hot=50, em=40, n=[0], **autres):
    n[0] += 1
    a = Article(
        source_id=source.id, url=f"https://i.g/{n[0]}", title=title,
        summary="Resume redige par Claude, la caption est perdue.",
        raw_hash=str(n[0]).zfill(64), relevance_score=rel, hotness_score=hot,
        emergence_score=em, **autres,
    )
    db.add(a)
    db.commit()
    return a


def test_dry_run_ne_touche_rien(db_session):
    s = _source(db_session)
    a = _article(db_session, s, TITRE_PAR_DEFAUT)
    plan = appliquer_garde_fous_existants(db_session)
    assert [p.article_id for p in plan] == [a.id]
    db_session.refresh(a)
    assert a.relevance_score == 65


def test_apply_plafonne_le_titre_par_defaut_et_garde_emergence(db_session):
    s = _source(db_session)
    a = _article(db_session, s, TITRE_PAR_DEFAUT, rel=65, hot=50, em=40)
    appliquer_garde_fous_existants(db_session, appliquer=True)
    db_session.refresh(a)
    assert (a.relevance_score, a.hotness_score, a.emergence_score) == (29, 29, 40)
    assert appliquer_garde_fous_existants(db_session) == []  # idempotent


def test_titre_court_seulement_sur_demande(db_session):
    s = _source(db_session)
    a = _article(db_session, s, "[📸]")
    assert appliquer_garde_fous_existants(db_session) == []
    plan = appliquer_garde_fous_existants(db_session, titres_courts=True, appliquer=True)
    assert [p.article_id for p in plan] == [a.id]
    db_session.refresh(a)
    assert a.relevance_score == 29


def test_ne_touche_ni_les_medias_ni_rss_ni_les_posts_publies_ni_les_non_scores(db_session):
    ig = _source(db_session)
    media = _source(db_session, categorie=SourceCategorie.MEDIA)
    rss = _source(db_session, type_=SourceType.RSS, categorie=SourceCategorie.MEDIA)
    _article(db_session, media, "LA SANTÉ MENTALE DE PETIT BISCUIT AU PLUS BAS", rel=68)
    _article(db_session, rss, TITRE_PAR_DEFAUT, rel=70)
    _article(db_session, ig, TITRE_PAR_DEFAUT, status=ArticleStatus.USED)
    _article(db_session, ig, TITRE_PAR_DEFAUT, rel=None, hot=None, em=None)
    _article(db_session, ig, TITRE_PAR_DEFAUT, rel=20, hot=10)
    _article(db_session, ig, "Mon nouvel EP sort vendredi chez Ninja Tune", rel=72)
    assert appliquer_garde_fous_existants(db_session, titres_courts=True) == []


def test_un_post_media_vide_est_plafonne_mais_jamais_au_titre_du_plafond_media(db_session):
    media = _source(db_session, categorie=SourceCategorie.MEDIA)
    vide = _article(db_session, media, TITRE_PAR_DEFAUT, rel=66)
    plein = _article(db_session, media, "LE NÉERLANDAIS REVIENT SUR SON ÉVOLUTION", rel=70)
    plan = appliquer_garde_fous_existants(db_session, appliquer=True)
    assert [p.article_id for p in plan] == [vide.id]
    db_session.refresh(plein)
    assert plein.relevance_score == 70
