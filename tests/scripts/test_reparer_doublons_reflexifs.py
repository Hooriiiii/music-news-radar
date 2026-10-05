"""La reparation des 3 lignes deja en base (2026-10-02).

Le correctif de `app/pipeline/cluster.py` empeche le lien reflexif de se
reformer ; il ne repare rien de ce qui est deja ecrit. Ce script le fait, et
deux proprietes comptent autant que la reparation elle-meme : les NOTES deja
payees ne bougent pas, et une exclusion posee AILLEURS que par la dedup n'est
jamais effacee.

`db_session.autoflush = False` : le reglage de PRODUCTION (`app/db.py`).
"""

import datetime as dt

from app.models import Article, Source, SourceType
from app.pipeline.cluster import RAISON_DOUBLON_PREFIXE
from scripts.reparations.reparer_doublons_reflexifs import reparer_doublons_reflexifs

UTC = dt.timezone.utc


def _source(db):
    source = Source(name="media", type=SourceType.RSS, url="https://media/feed")
    db.add(source)
    db.commit()
    return source


def _article(db, source, rang, *, note=None, raison=None):
    article = Article(
        source_id=source.id,
        url=f"https://media/{rang}",
        title=f"Titre {rang}",
        raw_hash=f"h{rang}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(hours=rang),
        relevance_score=note,
        file_exclu_raison=raison,
    )
    db.add(article)
    db.commit()
    return article


def test_le_plan_ne_touche_rien(db_session):
    db_session.autoflush = False
    source = _source(db_session)
    reflexif = _article(db_session, source, 1, note=65)
    reflexif.duplicate_of_id = reflexif.id
    db_session.commit()

    plan = reparer_doublons_reflexifs(db_session)

    assert [r.article_id for r in plan] == [reflexif.id]
    db_session.refresh(reflexif)
    assert reflexif.duplicate_of_id == reflexif.id  # rien n'a ete ecrit


def test_apply_delie_sans_toucher_aux_notes(db_session):
    """Les deux articles reflexifs deja notes en production (45 et 65) ne
    doivent pas repartir chez Claude : on repare un lien, pas une note."""
    db_session.autoflush = False
    source = _source(db_session)
    note = _article(db_session, source, 1, note=65)
    sans_note = _article(db_session, source, 2)
    for article in (note, sans_note):
        article.duplicate_of_id = article.id
    suiveur = _article(db_session, source, 3)
    suiveur.duplicate_of_id = note.id
    db_session.commit()

    plan = reparer_doublons_reflexifs(db_session, appliquer=True)

    assert len(plan) == 2
    for article in (note, sans_note):
        db_session.refresh(article)
        assert article.duplicate_of_id is None
    assert note.relevance_score == 65
    assert sans_note.relevance_score is None  # notee au passage suivant
    db_session.refresh(suiveur)
    # Le groupe survit : le suiveur garde sa tete, qui redevient un vrai
    # representant.
    assert suiveur.duplicate_of_id == note.id
    assert next(r for r in plan if r.article_id == note.id).suiveurs == 1


def test_une_exclusion_de_dedup_est_effacee_une_autre_jamais(db_session):
    """Meme filtre que l'auto-reparation d'un verdict manuel : effacer sans
    condition remettrait dans la file un article que la publication avait
    ecarte pour une vraie raison."""
    db_session.autoflush = False
    source = _source(db_session)
    dedup = _article(
        db_session, source, 1, raison=f"{RAISON_DOUBLON_PREFIXE} (article 999, similarite 0.90)"
    )
    ailleurs = _article(db_session, source, 2, raison="gel impossible : visuel introuvable")
    for article in (dedup, ailleurs):
        article.duplicate_of_id = article.id
    db_session.commit()

    reparer_doublons_reflexifs(db_session, appliquer=True)

    db_session.refresh(dedup)
    db_session.refresh(ailleurs)
    assert dedup.file_exclu_raison is None
    assert ailleurs.file_exclu_raison == "gel impossible : visuel introuvable"


def test_idempotent(db_session):
    db_session.autoflush = False
    source = _source(db_session)
    reflexif = _article(db_session, source, 1)
    reflexif.duplicate_of_id = reflexif.id
    db_session.commit()

    assert len(reparer_doublons_reflexifs(db_session, appliquer=True)) == 1
    assert reparer_doublons_reflexifs(db_session, appliquer=True) == []


def test_une_base_saine_donne_un_plan_vide(db_session):
    db_session.autoflush = False
    source = _source(db_session)
    representant = _article(db_session, source, 1)
    doublon = _article(db_session, source, 2)
    doublon.duplicate_of_id = representant.id
    db_session.commit()

    assert reparer_doublons_reflexifs(db_session, appliquer=True) == []
    db_session.refresh(doublon)
    assert doublon.duplicate_of_id == representant.id
