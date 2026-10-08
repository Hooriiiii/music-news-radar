import datetime as dt

from sqlalchemy import func, select

from app.models import Article, ArticleStatus, Source, SourceType
from app.pipeline.retention import purge_old_articles

UTC = dt.timezone.utc


def _src(db):
    src = Source(name="Feed", type=SourceType.RSS, url="https://example.com/f")
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, published_days_ago=None, fetched_days_ago=0,
         status=ArticleStatus.NEW, relevance=None, duplicate_of_id=None):
    now = dt.datetime.now(UTC)
    db.add(Article(
        source_id=src.id, url=f"https://example.com/{n}", title=f"T{n}",
        raw_hash=f"{n:064d}", status=status,
        relevance_score=relevance, duplicate_of_id=duplicate_of_id,
        published_at=None if published_days_ago is None
        else now - dt.timedelta(days=published_days_ago),
        fetched_at=now - dt.timedelta(days=fetched_days_ago),
    ))
    db.commit()


def _remaining(db):
    return db.scalar(select(func.count(Article.id)))


def test_purge_deletes_by_post_date(db_session):
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=20)  # vieux -> supprimé
    _art(db_session, src, n=2, published_days_ago=5)   # récent -> gardé
    assert purge_old_articles(db_session, retention_days=14) == 1
    assert _remaining(db_session) == 1


def test_purge_ignores_fetch_date_for_dated_articles(db_session):
    src = _src(db_session)
    # publié il y a 20j mais ajouté aujourd'hui -> supprimé (la date de post prime)
    _art(db_session, src, n=1, published_days_ago=20, fetched_days_ago=0)
    assert purge_old_articles(db_session, retention_days=14) == 1
    assert _remaining(db_session) == 0


def test_purge_null_published_falls_back_to_fetch_date(db_session):
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=None, fetched_days_ago=20)  # supprimé
    _art(db_session, src, n=2, published_days_ago=None, fetched_days_ago=2)   # gardé
    assert purge_old_articles(db_session, retention_days=14) == 1
    assert _remaining(db_session) == 1


def test_purge_keeps_used_articles(db_session):
    """Un article posté est l'archive éditoriale : la purge ne doit jamais l'effacer,
    sinon on perd la mémoire de ce qui a été publié (et on peut le reposter)."""
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=60, status=ArticleStatus.USED)
    _art(db_session, src, n=2, published_days_ago=60, status=ArticleStatus.REVIEWED)
    _art(db_session, src, n=3, published_days_ago=60)  # new -> supprimé
    assert purge_old_articles(db_session, retention_days=14) == 2
    remaining = db_session.scalars(select(Article.status)).all()
    assert remaining == [ArticleStatus.USED]


def test_purge_keeps_used_articles_without_publication_date(db_session):
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=None, fetched_days_ago=60,
         status=ArticleStatus.USED)
    assert purge_old_articles(db_session, retention_days=14) == 0
    assert _remaining(db_session) == 1


def test_purge_returns_zero_when_all_recent(db_session):
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=1)
    assert purge_old_articles(db_session, retention_days=14) == 0
    assert _remaining(db_session) == 1


def test_les_posts_survivent_a_la_purge_des_articles(db_session):
    """Le point de tout l'exercice : l'article est ephemere, le post publie est
    l'archive editoriale. Les champs denormalises le rendent lisible sans lui.

    Note SQLite : les cles etrangeres ne sont pas appliquees par defaut, donc on
    verifie la SURVIE de la ligne et l'integrite des champs denormalises, pas la
    mise a NULL de `article_id` (garantie par le ON DELETE SET NULL en Postgres).
    """
    from app.models import Post

    src = Source(name="Tsugi", type=SourceType.RSS, url="https://tsugi.fr/feed")
    db_session.add(src)
    db_session.commit()
    vieux = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)
    article = Article(
        source_id=src.id,
        url="https://tsugi.fr/vieux",
        title="Une vieille actu",
        raw_hash="vieux",
        published_at=vieux,
    )
    db_session.add(article)
    db_session.commit()

    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name=src.name,
        caption="Legende archivee",
        slides=[{"headline": "**Une** archive", "image_url": None}],
    )
    db_session.add(post)
    db_session.commit()

    purge_old_articles(db_session, retention_days=14)

    assert db_session.query(Article).count() == 0
    survivant = db_session.query(Post).one()
    assert survivant.article_title == "Une vieille actu"
    assert survivant.caption == "Legende archivee"


# --- Sursis pour les articles que la machine n'a PAS PU juger ---------------
# Mesure du 2026-10-08 : la panne de credits Anthropic du 2026-10-07 a laisse
# 452 articles sans note (399 jamais juges, 53 doublons). Les 108 du 06/10
# etaient a la limite de purge le jour de la mesure : ils allaient etre effaces
# sans que personne ne les ait juges. `CLAUDE.md` : « une panne MACHINE n'exclut
# JAMAIS un article de la file -- credits Anthropic epuises ».


def test_un_article_jamais_juge_survit_a_la_retention_normale(db_session):
    """LA propriete. Hors fenetre pour un article note, dans le sursis sinon."""
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=4)  # jamais juge -> sursis
    assert purge_old_articles(db_session, retention_days=2, sursis_days=7) == 0
    assert _remaining(db_session) == 1


def test_le_sursis_est_borne_et_finit_par_purger(db_session):
    """Le sursis est borne : il repousse la purge, il ne la supprime pas."""
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=10)
    assert purge_old_articles(db_session, retention_days=2, sursis_days=7) == 1
    assert _remaining(db_session) == 0


def test_un_article_juge_ne_beneficie_pas_du_sursis(db_session):
    """Un article NOTE et rejete part a `retention_days` : le sursis repare une
    panne de la machine, il n'allonge pas la vie du bruit juge."""
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=4, relevance=10)
    assert purge_old_articles(db_session, retention_days=2, sursis_days=7) == 1
    assert _remaining(db_session) == 0


def test_un_article_note_zero_est_juge(db_session):
    """Garde-fou contre un `relevance_score or 0` a la place d'un `IS NULL` :
    une note de 0 est une DECISION, pas une absence de decision."""
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=4, relevance=0)
    assert purge_old_articles(db_session, retention_days=2, sursis_days=7) == 1
    assert _remaining(db_session) == 0


def test_un_doublon_sans_note_reste_a_la_retention_normale(db_session):
    """Les 53 doublons de la mesure du 2026-10-08. Un doublon A ete juge -- par
    la dedup, qui a decide qu'il repetait une actu deja representee. Il n'a pas
    de note parce qu'il n'en a pas besoin, pas parce que la machine est tombee."""
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=4, relevance=50)  # le representant
    representant = db_session.scalars(select(Article)).one()
    _art(db_session, src, n=2, published_days_ago=4, duplicate_of_id=representant.id)
    # `expunge_all` avant la purge : le DELETE en masse fait reevaluer le
    # predicat EN PYTHON sur les instances chargees, sur des dates que SQLite
    # rend naives -- rien a voir avec la propriete testee (meme precaution que
    # `tests/pipeline/test_cluster_bande_fraiche.py`).
    db_session.expunge_all()
    assert purge_old_articles(db_session, retention_days=2, sursis_days=7) == 2
    assert _remaining(db_session) == 0


def test_un_non_juge_sans_date_de_publication_suit_le_sursis(db_session):
    """Le filet `fetched_at` vaut aussi pour le sursis : sinon un article non
    date et non juge serait efface a 2 jours par la branche de repli."""
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=None, fetched_days_ago=4)
    assert purge_old_articles(db_session, retention_days=2, sursis_days=7) == 0
    assert _remaining(db_session) == 1


def test_le_sursis_est_un_plancher_jamais_une_substitution(db_session):
    """LE PIEGE de ce reglage, et il est silencieux.

    Les appelants peuvent passer un `retention_days` PLUS LONG que le sursis
    (toute cette suite passe 14). Si le sursis REMPLACAIT la retention, un
    non-juge tomberait de 14 jours a 7 : le sursis RACCOURCIRAIT la
    conservation, l'exact inverse de son intention. D'ou
    `max(retention_days, sursis_days)` dans `_predicat_purgeable`.
    """
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=10)  # 10 < 14 -> garde quand meme
    _art(db_session, src, n=2, published_days_ago=3)   # dans les deux fenetres
    assert purge_old_articles(db_session, retention_days=14, sursis_days=7) == 0
    assert _remaining(db_session) == 2

    # Et au-dela du plancher, il part : le sursis n'immortalise rien.
    _art(db_session, src, n=3, published_days_ago=20)
    assert purge_old_articles(db_session, retention_days=14, sursis_days=7) == 1
    assert _remaining(db_session) == 2


def test_un_non_juge_used_n_est_jamais_purge(db_session):
    """L'archive editoriale prime sur tout : `USED` d'abord, sursis ensuite."""
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=60, status=ArticleStatus.USED)
    assert purge_old_articles(db_session, retention_days=2, sursis_days=7) == 0
    assert _remaining(db_session) == 1


def test_le_sursis_par_defaut_vient_des_reglages(db_session, monkeypatch):
    """Aucun appelant de production ne passe `sursis_days` : le defaut doit
    venir de `settings`, et `run.py` ne doit pas avoir a le connaitre."""
    from app.config import settings

    monkeypatch.setattr(settings, "retention_sursis_non_juges_days", 7)
    src = _src(db_session)
    _art(db_session, src, n=1, published_days_ago=4)
    assert purge_old_articles(db_session, retention_days=2) == 0
    assert _remaining(db_session) == 1
