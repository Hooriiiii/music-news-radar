"""Phase 2B, Task 2 : purger les evenements sans valeur.

Depuis la Task 1, TOUT article devient l'observation d'un evenement, meme
seul. Sans cette purge c'est une fuite lente : un evenement permanent par
article de bruit, et rien ne purgeait `events`.

La regle tranchee par l'utilisateur le 2026-09-24 :
- une seule observation, article sous le seuil de pertinence, jamais publie
  -> supprime avec son article ;
- deux observations ou plus -> permanent, sans condition ;
- publie (`USED`, un `Post`, un `Creneau` engage) -> permanent, sans condition.

Chaque test regle `autoflush = False` EXPLICITEMENT : c'est le reglage de
production (`app/db.py`), et un test en autoflush par defaut a deja masque
deux fois un defaut de production dans ce depot.
"""

import datetime as dt

from sqlalchemy import func, select

from app.config import settings
from app.models import (
    Article,
    ArticleStatus,
    Creneau,
    EtatCreneau,
    Event,
    EventObservation,
    Post,
    Source,
    SourceRole,
    SourceType,
)
from app.pipeline.events import observer_articles
from app.pipeline.retention import purge_old_articles, purger_evenements_sans_valeur

UTC = dt.timezone.utc
RETENTION = 2


def _src(db, name="Feed"):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.example/f")
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, jours=10, relevance=10, status=ArticleStatus.NEW, titre=None):
    """Par defaut : vieux (hors fenetre), sans interet, jamais publie."""
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.example/{n}",
        title=titre or f"Titre numero {n} sans ressemblance particuliere",
        raw_hash=f"{n:064d}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(days=jours),
        relevance_score=relevance,
        status=status,
    )
    db.add(art)
    db.commit()
    return art


def _evenement(db, article, *autres):
    """Un evenement portant une observation par article donne."""
    vu = article.published_at
    event = Event(titre=article.title, first_seen_at=vu, last_seen_at=vu)
    db.add(event)
    db.flush()
    for membre in (article, *autres):
        db.add(
            EventObservation(
                event_id=event.id,
                article_id=membre.id,
                platform=membre.source.type,
                source_id=membre.source_id,
                source_role=SourceRole.UNKNOWN,
                published_at=membre.published_at,
                title=membre.title,
                url=membre.url,
                source_name=membre.source.name,
            )
        )
    db.commit()
    return event


def _compte(db, modele):
    return db.scalar(select(func.count(modele.id)))


# -- les trois cas de la regle ----------------------------------------------


def test_un_evenement_solitaire_sans_interet_meurt_avec_son_article(db_session):
    db_session.autoflush = False
    src = _src(db_session)
    art = _art(db_session, src, n=1, relevance=10)
    _evenement(db_session, art)

    stats = purger_evenements_sans_valeur(db_session, RETENTION)

    assert stats.erreur is None
    assert stats.evenements_examines == 1
    assert stats.evenements_supprimes == 1
    assert stats.observations_supprimees == 1
    assert _compte(db_session, Event) == 0
    assert _compte(db_session, EventObservation) == 0


def test_un_evenement_solitaire_au_dessus_du_seuil_survit(db_session):
    """Le seuil retenu est `digest_relevance_threshold` (60), le seuil de
    pertinence general du depot -- pas un nouveau reglage."""
    db_session.autoflush = False
    src = _src(db_session)
    art = _art(db_session, src, n=1, relevance=settings.digest_relevance_threshold)
    _evenement(db_session, art)

    stats = purger_evenements_sans_valeur(db_session, RETENTION)

    assert stats.evenements_examines == 1
    assert stats.evenements_supprimes == 0
    assert _compte(db_session, Event) == 1


def test_deux_observations_rendent_l_evenement_permanent(db_session):
    """Sans condition : ni la pertinence ni le statut ne sont meme regardes."""
    db_session.autoflush = False
    src = _src(db_session)
    a = _art(db_session, src, n=1, relevance=0)
    b = _art(db_session, src, n=2, relevance=0)
    b.duplicate_of_id = a.id
    db_session.commit()
    _evenement(db_session, a, b)

    stats = purger_evenements_sans_valeur(db_session, RETENTION)

    assert stats.evenements_supprimes == 0
    assert _compte(db_session, Event) == 1
    assert _compte(db_session, EventObservation) == 2


def test_un_article_publie_rend_son_evenement_permanent(db_session):
    """USED, un `Post` ou un `Creneau` engage : trois façons d'etre publie,
    chacune protege (meme predicat que `election.representant_engage`)."""
    db_session.autoflush = False
    src = _src(db_session)

    # USED : `purge_old_articles` ne le supprime deja pas, son evenement non plus.
    used = _art(db_session, src, n=1, relevance=0, status=ArticleStatus.USED)
    _evenement(db_session, used)

    # Un `Post` compose, sur un article qui n'est pas (encore) USED.
    avec_post = _art(db_session, src, n=2, relevance=0)
    db_session.add(
        Post(
            article_id=avec_post.id,
            article_title=avec_post.title,
            article_url=avec_post.url,
            source_name=src.name,
            template="kinda_post",
            caption="Une legende",
            hashtags=[],
            slides=[],
        )
    )
    db_session.commit()
    _evenement(db_session, avec_post)

    # Un creneau engage (non LIBRE).
    avec_creneau = _art(db_session, src, n=3, relevance=0)
    db_session.add(
        Creneau(
            publier_a=dt.datetime.now(UTC),
            etat=EtatCreneau.GELE,
            article_id=avec_creneau.id,
        )
    )
    db_session.commit()
    _evenement(db_session, avec_creneau)

    stats = purger_evenements_sans_valeur(db_session, RETENTION)

    assert stats.evenements_supprimes == 0
    assert _compte(db_session, Event) == 3


# -- le principe d'archive : deux preuves survivent a leurs deux articles ----


def test_un_evenement_a_deux_preuves_survit_a_la_purge_de_ses_deux_articles(db_session):
    """L'histoire d'une actu corroboree vaut au-dela de la vie de ses articles.

    C'est exactement ce que le projet veut preserver -- meme motif que
    `artist_mentions`, qui survit lui aussi a la purge des articles."""
    db_session.autoflush = False
    src = _src(db_session)
    a = _art(db_session, src, n=1, relevance=0)
    b = _art(db_session, src, n=2, relevance=0)
    b.duplicate_of_id = a.id
    db_session.commit()
    event = _evenement(db_session, a, b)

    purger_evenements_sans_valeur(db_session, RETENTION)
    assert purge_old_articles(db_session, retention_days=RETENTION) == 2

    assert _compte(db_session, Article) == 0
    assert db_session.get(Event, event.id) is not None
    orphelines = db_session.scalars(
        select(EventObservation).where(EventObservation.event_id == event.id)
    ).all()
    assert len(orphelines) == 2


# -- l'ordre : la decision se prend AVANT la purge des articles --------------


def test_la_decision_prise_apres_la_purge_ne_trouve_plus_rien(db_session):
    """La preuve par l'absurde de l'invariant d'ordre.

    `event_observations.article_id` est `ON DELETE SET NULL` : une fois
    l'article efface, l'observation survit sans lui et plus rien ne permet de
    juger sa pertinence. Dans l'ordre INVERSE, l'evenement de bruit survit --
    ce test le montre, et
    `test_le_passage_horaire_purge_les_evenements_avant_les_articles`
    verrouille l'ordre reel."""
    db_session.autoflush = False
    src = _src(db_session)
    art = _art(db_session, src, n=1, relevance=0)
    _evenement(db_session, art)

    purge_old_articles(db_session, retention_days=RETENTION)
    stats = purger_evenements_sans_valeur(db_session, RETENTION)

    assert stats.evenements_examines == 0
    assert stats.evenements_supprimes == 0
    assert _compte(db_session, Event) == 1  # la fuite lente, en une ligne


def test_le_passage_horaire_purge_les_evenements_avant_les_articles(db_session, monkeypatch):
    """Le passage horaire reel, sans injecter ni la purge ni l'observation :
    un article de bruit hors fenetre entre dans un evenement (Task 1) et les
    deux disparaissent dans le MEME passage.

    Inverser les deux appels dans `run_pipeline` rend ce test rouge (preuve par
    mutation, 2026-09-24)."""
    from app.pipeline.run import run_pipeline

    db_session.autoflush = False
    monkeypatch.setattr(settings, "retention_days", RETENTION)
    src = _src(db_session)
    art = _art(db_session, src, n=1, relevance=0, jours=10)
    # L'article est hors fenetre : `observer_articles` ne l'observe pas (il
    # serait purge dans la foulee). On pose donc l'evenement comme un passage
    # ANTERIEUR l'aurait fait, quand l'article etait encore frais.
    _evenement(db_session, art)

    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        score=lambda db: None,
        illustrer=lambda db: type("R", (), {"illustres": 0})(),
        alert=lambda db: None,
    )

    assert report.purge_evenements is not None
    assert report.purge_evenements.evenements_supprimes == 1
    assert report.purged == 1
    assert _compte(db_session, Event) == 0
    assert _compte(db_session, EventObservation) == 0


# -- invariants transverses --------------------------------------------------


def test_un_article_dans_la_fenetre_n_est_jamais_candidat(db_session):
    """Rien a purger = quasi gratuit : la recherche part des ARTICLES que la
    purge va supprimer, jamais de la table `events`."""
    db_session.autoflush = False
    src = _src(db_session)
    art = _art(db_session, src, n=1, relevance=0, jours=0)
    observer_articles(db_session)
    assert _compte(db_session, Event) == 1

    stats = purger_evenements_sans_valeur(db_session, RETENTION)

    assert stats.evenements_examines == 0
    assert stats.evenements_supprimes == 0
    assert db_session.get(Article, art.id) is not None
    assert _compte(db_session, Event) == 1


def test_une_panne_ne_leve_jamais_et_remonte_dans_le_rapport(db_session, monkeypatch):
    db_session.autoflush = False
    src = _src(db_session)
    art = _art(db_session, src, n=1, relevance=0)
    _evenement(db_session, art)

    # La panne tombe APRES les lectures, au moment d'ecrire : c'est la que le
    # rollback compte.
    import app.pipeline.retention as retention_module

    def _explose(*args, **kwargs):
        raise RuntimeError("base injoignable")

    monkeypatch.setattr(retention_module, "delete", _explose)

    stats = purger_evenements_sans_valeur(db_session, RETENTION)

    assert stats.erreur is not None
    assert stats.evenements_supprimes == 0
    assert _compte(db_session, Event) == 1


def test_sans_migration_0019_la_purge_est_un_no_op(db_session, monkeypatch):
    db_session.autoflush = False
    import app.pipeline.events as events_module

    monkeypatch.setattr(events_module, "_tables_disponibles", lambda db: False)

    stats = purger_evenements_sans_valeur(db_session, RETENTION)

    assert stats.tables_absentes is True
    assert stats.erreur is None
    assert stats.evenements_supprimes == 0


# -- la regle « engage » n'existe qu'a un seul endroit -----------------------


def test_les_deux_chemins_s_accordent_sur_les_trois_formes_d_engagement(db_session):
    """`election.representant_engage` (un article) et `election.articles_engages`
    (un lot) doivent toujours dire la meme chose.

    La seconde decide de la suppression DEFINITIVE d'un evenement : une
    divergence ferait disparaitre en silence l'archive editoriale d'une actu
    publiee. Ce test echoue a la prochaine divergence au lieu de la laisser
    passer."""
    from app.pipeline.election import articles_engages, representant_engage

    db_session.autoflush = False
    src = _src(db_session)

    libre = _art(db_session, src, n=1)

    used = _art(db_session, src, n=2, status=ArticleStatus.USED)

    avec_post = _art(db_session, src, n=3)
    db_session.add(
        Post(
            article_id=avec_post.id,
            article_title=avec_post.title,
            article_url=avec_post.url,
            source_name=src.name,
            template="kinda_post",
            caption="Une legende",
            hashtags=[],
            slides=[],
        )
    )

    avec_creneau = _art(db_session, src, n=4)
    db_session.add(
        Creneau(
            publier_a=dt.datetime.now(UTC),
            etat=EtatCreneau.GELE,
            article_id=avec_creneau.id,
        )
    )

    # Un creneau LIBRE n'engage rien, des deux cotes.
    avec_creneau_libre = _art(db_session, src, n=5)
    db_session.add(
        Creneau(
            publier_a=dt.datetime.now(UTC),
            etat=EtatCreneau.LIBRE,
            article_id=avec_creneau_libre.id,
        )
    )
    db_session.commit()

    tous = [libre, used, avec_post, avec_creneau, avec_creneau_libre]
    en_lot = articles_engages(db_session, [a.id for a in tous])

    assert en_lot == {used.id, avec_post.id, avec_creneau.id}
    for article in tous:
        assert representant_engage(db_session, article) is (article.id in en_lot), article.title

    assert articles_engages(db_session, []) == set()
    assert articles_engages(db_session, [None]) == set()


# -- le sursis des non-juges traverse le predicat PARTAGE --------------------


def test_l_evenement_d_un_article_jamais_juge_survit_avec_lui(db_session):
    """L'invariant de `_predicat_purgeable` applique au sursis.

    Les deux etapes doivent viser EXACTEMENT le meme ensemble. Si le sursis
    n'etait cable que dans `purge_old_articles`, cette fonction supprimerait
    l'evenement d'un article qui RESTE en base -- le pire des deux cas que le
    docstring du predicat decrit. Le sursis vit donc dans le predicat partage,
    et il suffit de le verifier ici : l'evenement d'un non-juge sous sursis
    n'est meme pas EXAMINE.
    """
    db_session.autoflush = False
    src = _src(db_session)
    art = _art(db_session, src, n=1, jours=4, relevance=None)
    event = _evenement(db_session, art)

    stats = purger_evenements_sans_valeur(db_session, RETENTION, sursis_days=7)
    assert stats.evenements_examines == 0
    assert stats.evenements_supprimes == 0
    assert db_session.get(Event, event.id) is not None
    assert purge_old_articles(db_session, retention_days=RETENTION, sursis_days=7) == 0


def test_passe_le_sursis_l_evenement_d_un_non_juge_meurt_comme_les_autres(db_session):
    """Le sursis repousse la decision, il ne la supprime pas : au-dela, le
    non-juge et son evenement solitaire partent comme n'importe quel bruit."""
    db_session.autoflush = False
    src = _src(db_session)
    art = _art(db_session, src, n=1, jours=10, relevance=None)
    event = _evenement(db_session, art)

    stats = purger_evenements_sans_valeur(db_session, RETENTION, sursis_days=7)
    assert stats.evenements_supprimes == 1
    assert db_session.get(Event, event.id) is None
    assert purge_old_articles(db_session, retention_days=RETENTION, sursis_days=7) == 1
