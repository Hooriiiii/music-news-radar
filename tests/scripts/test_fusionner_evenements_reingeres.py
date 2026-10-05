"""Le rattrapage des evenements ne-s de RE-INGESTION.

Mesure du 2026-09-30 : un article purge a 2 jours, encore liste dans le flux
RSS de son editeur, revenait comme neuf et creait un SECOND evenement
permanent. `events._evenement_deja_observe` empeche desormais ces naissances ;
ce script repare l'existant (89 evenements absorbes en production, 933 -> 844).

Le critere doit rester CELUI DE LA PREVENTION : ces tests echouent si les deux
divergent, parce qu'ils s'appuient sur les memes constantes importees.

`db_session.autoflush = False` partout : c'est le reglage de PRODUCTION
(`app/db.py`), et un test en autoflush par defaut a deja masque trois fois un
defaut de production dans ce depot.
"""

import datetime as dt

from sqlalchemy import func, select

from app.models import Event, EventObservation, Source, SourceRole, SourceType
from app.pipeline.events import TITRE_FENETRE_HEURES
from scripts.fusionner_evenements_reingeres import composer_les_groupes, fusionner

UTC = dt.timezone.utc


def _source(db, nom="Mixmag"):
    source = Source(name=nom, type=SourceType.RSS, url=f"https://{nom.lower()}/rss")
    db.add(source)
    db.commit()
    return source


def _evenement(db, titre, quand):
    event = Event(titre=titre, first_seen_at=quand, last_seen_at=quand, source_count=1)
    db.add(event)
    db.commit()
    return event


def _observation(db, event, source, titre, url, quand):
    obs = EventObservation(
        event_id=event.id,
        article_id=None,
        platform=SourceType.RSS,
        source_id=source.id,
        source_role=SourceRole.SECONDARY,
        published_at=quand,
        observed_at=quand,
        title=titre,
        url=url,
        source_name=source.name,
    )
    db.add(obs)
    db.commit()
    return obs


def _reingestion(db, *, titre, url, ecarts_heures, source=None):
    """Un meme (titre, url) revu plusieurs fois : le profil exact du defaut."""
    source = source or _source(db)
    base = dt.datetime.now(UTC) - dt.timedelta(hours=sum(ecarts_heures) + 1)
    events = []
    quand = base
    for i, ecart in enumerate([0, *ecarts_heures]):
        quand = quand + dt.timedelta(hours=ecart)
        event = _evenement(db, titre, quand)
        _observation(db, event, source, titre, url, quand)
        events.append(event)
    return source, events


def test_une_reingestion_est_fusionnee(db_session):
    db_session.autoflush = False
    _, events = _reingestion(
        db_session, titre="The Warehouse Project shares details", url="https://mixmag/whp", ecarts_heures=[34, 59, 49]
    )
    absorbes, groupes = fusionner(db_session, appliquer=True)
    assert (absorbes, groupes) == (3, 1)
    restants = db_session.scalars(select(Event)).all()
    assert len(restants) == 1
    # La CIBLE est le plus petit id, comme `attacher` l'elit.
    assert restants[0].id == min(e.id for e in events)
    # Aucune observation perdue, aucune orpheline.
    assert db_session.scalar(select(func.count(EventObservation.id))) == 4
    assert all(o.event_id == restants[0].id for o in db_session.scalars(select(EventObservation)))


def test_le_plan_seul_n_ecrit_RIEN(db_session):
    db_session.autoflush = False
    _reingestion(db_session, titre="Un titre bien assez riche", url="https://m/a", ecarts_heures=[40])
    avant = db_session.scalar(select(func.count(Event.id)))
    absorbes, _ = fusionner(db_session, appliquer=False)
    assert absorbes == 1
    assert db_session.scalar(select(func.count(Event.id))) == avant


def test_un_second_passage_ne_trouve_plus_rien(db_session):
    db_session.autoflush = False
    _reingestion(db_session, titre="Un titre bien assez riche", url="https://m/a", ecarts_heures=[40])
    fusionner(db_session, appliquer=True)
    assert fusionner(db_session, appliquer=True) == (0, 0)


def test_deux_sources_sur_la_meme_actu_ne_sont_JAMAIS_fusionnees(db_session):
    """C'est une vraie corroboration, pas un artefact -- et c'est la dedup par
    titre qui relie ces deux articles, pas ce script."""
    db_session.autoflush = False
    mixmag, ra = _source(db_session, "Mixmag"), _source(db_session, "RA")
    quand = dt.datetime.now(UTC) - dt.timedelta(hours=5)
    titre = "Laurent Garnier annonce une tournee europeenne"
    for source, url in ((mixmag, "https://mixmag/lg"), (ra, "https://ra/lg")):
        event = _evenement(db_session, titre, quand)
        _observation(db_session, event, source, titre, url, quand)
    assert fusionner(db_session, appliquer=True) == (0, 0)
    assert db_session.scalar(select(func.count(Event.id))) == 2


def test_un_titre_trop_pauvre_ne_fusionne_rien_par_le_titre(db_session):
    """« Post Instagram » vaut 2 jetons significatifs : six posts promo d'un
    meme compte fabriqueraient une velocite. Le garde-fou de CONTENU de la
    prevention vaut aussi ici -- les URL, elles, restent distinctes."""
    db_session.autoflush = False
    source = _source(db_session, "Nina Kraviz")
    quand = dt.datetime.now(UTC) - dt.timedelta(hours=6)
    for i in range(3):
        event = _evenement(db_session, "Post Instagram", quand + dt.timedelta(hours=i))
        _observation(
            db_session, event, source, "Post Instagram", f"https://ig/p/{i}", quand + dt.timedelta(hours=i)
        )
    assert fusionner(db_session, appliquer=True) == (0, 0)
    assert db_session.scalar(select(func.count(Event.id))) == 3


def test_au_dela_de_la_fenetre_du_titre_rien_n_est_fusionne(db_session):
    """Une chronique hebdomadaire n'est pas une re-ingestion : deux editions
    distinctes doivent rester deux evenements. Les URL differant, seule la
    branche titre pourrait les rapprocher -- et sa fenetre l'en empeche."""
    db_session.autoflush = False
    source = _source(db_session, "Billboard Charts")
    titre = "This week top 10 on the Global chart"
    base = dt.datetime.now(UTC) - dt.timedelta(hours=TITRE_FENETRE_HEURES + 60)
    for i, ecart in enumerate([0, TITRE_FENETRE_HEURES + 24]):
        quand = base + dt.timedelta(hours=ecart)
        event = _evenement(db_session, titre, quand)
        _observation(db_session, event, source, titre, f"https://bb/semaine-{i}", quand)
    assert fusionner(db_session, appliquer=True) == (0, 0)


def test_une_chaine_longue_est_fusionnee_en_entier(db_session):
    """Cinq re-ingestions etalees sur plus que la fenetre sont UN evenement si
    chacune tombe dans la fenetre de la PRECEDENTE -- c'est ce que fait la
    prevention, qui regarde en arriere a chaque ingestion. Tester l'etendue du
    groupe entier refuserait de reparer ces chaines."""
    db_session.autoflush = False
    _reingestion(
        db_session, titre="Un titre bien assez riche pour compter", url="https://m/chaine", ecarts_heures=[50, 50, 50, 50]
    )
    absorbes, groupes = fusionner(db_session, appliquer=True)
    assert (absorbes, groupes) == (4, 1)
    assert db_session.scalar(select(func.count(Event.id))) == 1


def test_les_groupes_sont_calcules_sans_rien_ecrire(db_session):
    db_session.autoflush = False
    _reingestion(db_session, titre="Un titre bien assez riche", url="https://m/a", ecarts_heures=[40])
    avant = db_session.scalar(select(func.count(Event.id)))
    groupes = composer_les_groupes(db_session)
    assert len(groupes) == 1
    assert db_session.scalar(select(func.count(Event.id))) == avant
