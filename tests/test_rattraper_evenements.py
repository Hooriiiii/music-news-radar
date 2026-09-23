"""Le rattrapage des groupes de doublons formes avant `events.attacher` :
`scripts/rattraper_evenements.py`.

`events.attacher` n'est appelee que depuis `cluster.lier()`, donc seulement
quand un NOUVEAU lien de doublon est pose -- un groupe forme avant la mise en
service de ce module (Task 2) n'a jamais eu l'occasion de devenir un
evenement. Ce fichier verifie le SCRIPT de rattrapage, pas `attacher`
elle-meme (deja couverte par `tests/test_events_rattachement.py`).
"""

import datetime as dt

from sqlalchemy import select

from app.models import Article, ArticleStatus, Event, EventObservation, Source, SourceType
from app.pipeline import events
from scripts.rattraper_evenements import rattraper

UTC = dt.timezone.utc


def _src(db, name):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.example/feed")
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, title, hours_ago=1, status=ArticleStatus.NEW, duplicate_of_id=None):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.example/{n}",
        title=title,
        raw_hash=f"{n:064d}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago),
        status=status,
        duplicate_of_id=duplicate_of_id,
    )
    db.add(art)
    db.commit()
    return art


def _events(db):
    return db.scalars(select(Event)).all()


def _observations(db, event_id):
    return db.scalars(
        select(EventObservation).where(EventObservation.event_id == event_id)
    ).all()


def test_le_plan_compte_sans_rien_ecrire(db_session):
    db_session.autoflush = False
    src = _src(db_session, "mixmag")
    representant = _art(db_session, src, n=1, title="Dour Festival announces 2027 lineup",
                         hours_ago=5)
    _art(db_session, src, n=2, title="Doublon", hours_ago=4,
         duplicate_of_id=representant.id)

    rapport = rattraper(db_session)

    assert (rapport.groupes, rapport.membres, rapport.observations_ajoutees) == (1, 2, 2)
    assert rapport.groupes_crees == 1
    # Rien de persiste : la transaction a ete annulee.
    assert _events(db_session) == []


def test_apply_cree_l_evenement_et_ses_observations(db_session):
    db_session.autoflush = False
    src = _src(db_session, "mixmag")
    representant = _art(db_session, src, n=1, title="Dour Festival announces 2027 lineup",
                         hours_ago=5)
    membre = _art(db_session, src, n=2, title="Doublon", hours_ago=4,
                  duplicate_of_id=representant.id)

    rapport = rattraper(db_session, appliquer=True)

    assert rapport.groupes_crees == 1
    (event,) = _events(db_session)
    observations = _observations(db_session, event.id)
    assert {o.article_id for o in observations} == {representant.id, membre.id}


def test_rejouer_ne_duplique_aucune_observation(db_session):
    db_session.autoflush = False
    src = _src(db_session, "mixmag")
    representant = _art(db_session, src, n=1, title="Dour Festival announces 2027 lineup",
                         hours_ago=5)
    _art(db_session, src, n=2, title="Doublon", hours_ago=4,
         duplicate_of_id=representant.id)

    rattraper(db_session, appliquer=True)
    (event_1,) = _events(db_session)
    observations_1 = _observations(db_session, event_1.id)

    rapport_2 = rattraper(db_session, appliquer=True)

    assert rapport_2.groupes_deja_a_jour == 1
    assert rapport_2.observations_ajoutees == 0
    (event_2,) = _events(db_session)
    assert event_2.id == event_1.id
    observations_2 = _observations(db_session, event_2.id)
    assert {o.id for o in observations_2} == {o.id for o in observations_1}


def test_complete_un_evenement_partiel_sans_en_creer_un_second(db_session):
    """Un passage horaire posterieur a la migration a deja rattache DEUX des
    trois membres (cas reel de production : le seul groupe deja complet au
    moment de la mesure). Le troisieme, lie avant la mise en service du
    module, doit rejoindre le MEME evenement -- jamais un second."""
    db_session.autoflush = False
    src = _src(db_session, "mixmag")
    representant = _art(db_session, src, n=1, title="Astropolis devoile sa programmation",
                         hours_ago=5)
    membre_deja_observe = _art(db_session, src, n=2, title="Doublon recent", hours_ago=4,
                                duplicate_of_id=representant.id)
    # Rattachement REEL, via la machinerie de production : simule le passage
    # horaire qui a deja cree l'evenement partiel.
    events.attacher(db_session, membre_deja_observe, representant)
    db_session.commit()
    (event_partiel,) = _events(db_session)
    assert len(_observations(db_session, event_partiel.id)) == 2  # representant + membre

    # Un troisieme membre, lie AVANT la mise en service (donc jamais passe par
    # `attacher`).
    membre_ancien = _art(db_session, src, n=3, title="Doublon ancien", hours_ago=3,
                          duplicate_of_id=representant.id)

    rapport = rattraper(db_session, appliquer=True)

    assert rapport.groupes_completes == 1
    assert rapport.observations_ajoutees == 1
    evenements = _events(db_session)
    assert len(evenements) == 1  # jamais un second evenement
    assert evenements[0].id == event_partiel.id
    observations = _observations(db_session, evenements[0].id)
    assert {o.article_id for o in observations} == {
        representant.id, membre_deja_observe.id, membre_ancien.id,
    }


def test_traite_aussi_les_articles_used(db_session):
    """Les representants d'archive (deja publies, jamais purges) portent
    l'histoire la plus longue -- ils doivent etre rattaches comme n'importe
    quel autre groupe."""
    db_session.autoflush = False
    src = _src(db_session, "mixmag")
    representant_used = _art(
        db_session, src, n=1, title="Charlotte de Witte announces new EP",
        hours_ago=48, status=ArticleStatus.USED,
    )
    membre = _art(db_session, src, n=2, title="Doublon", hours_ago=1,
                  duplicate_of_id=representant_used.id)

    rapport = rattraper(db_session, appliquer=True)

    assert rapport.groupes_crees == 1
    (event,) = _events(db_session)
    observations = _observations(db_session, event.id)
    assert {o.article_id for o in observations} == {representant_used.id, membre.id}


def test_migration_absente_est_signalee_sans_lever(db_session):
    db_session.autoflush = False
    EventObservation.__table__.drop(db_session.get_bind(), checkfirst=True)
    Event.__table__.drop(db_session.get_bind(), checkfirst=True)
    src = _src(db_session, "mixmag")
    representant = _art(db_session, src, n=1, title="Une actu", hours_ago=5)
    _art(db_session, src, n=2, title="Doublon", hours_ago=4,
         duplicate_of_id=representant.id)

    rapport = rattraper(db_session, appliquer=True)

    assert rapport.tables_absentes is True
    assert rapport.groupes == 0
