"""L'alerte « 0 artiste suivi » etait FAUSSE, et une alerte fausse est pire que
pas d'alerte : elle fait chercher une panne ailleurs.

Elle comptait `Artist.tracked`, une colonne ecrite par `PATCH /artists/{id}` et
lue par PERSONNE. Les chemins qu'elle declarait morts -- l'exception au
pre-filtre (`scoring.ContexteScoring`) et la reserve de visuels
(`content/choix_visuels.py`) -- lisent `comptes_artistes.IndexComptes`,
c'est-a-dire les SOURCES Instagram actives de categorie artiste. Il y en avait
88 en production le 2026-10-03, et ces chemins tournaient.
"""

import datetime as dt

from app.models import Artist, Source, SourceCategorie, SourceType
from scripts.diagnostic.etat_notion import collecter, formater

UTC = dt.timezone.utc

ALERTE = "0 compte d'artiste suivi"


def _compte(db, handle="folamour"):
    source = Source(
        name=f"{handle} (IG)",
        type=SourceType.INSTAGRAM,
        url=f"ig:{handle}",
        categorie=SourceCategorie.ARTISTE,
    )
    db.add(source)
    db.commit()
    return source


def _artiste(db, nom="Folamour", **kwargs):
    maintenant = dt.datetime.now(UTC)
    artist = Artist(
        name=nom,
        slug=nom.lower(),
        first_seen_at=maintenant,
        last_seen_at=maintenant,
        mentions_count=1,
        **kwargs,
    )
    db.add(artist)
    db.commit()
    return artist


def test_un_compte_suivi_ne_declenche_aucune_alerte(db_session):
    """Le cas de production : des artistes en memoire, aucun `tracked`, mais un
    compte suivi en place. L'ancienne version criait ici."""
    _compte(db_session)
    _artiste(db_session)

    etat = collecter(db_session)
    assert etat["comptes_artistes"] == 1
    assert ALERTE not in formater(etat)


def test_aucun_compte_suivi_declenche_l_alerte(db_session):
    """La vraie condition de panne, et la seule."""
    _artiste(db_session)

    etat = collecter(db_session)
    assert etat["comptes_artistes"] == 0
    assert ALERTE in formater(etat)


def test_le_tableau_compte_les_fiches_rattachees(db_session):
    source = _compte(db_session)
    _artiste(db_session, source_instagram_id=source.id)
    _artiste(db_session, nom="Inconnu")

    etat = collecter(db_session)
    assert etat["artistes"] == 2
    assert etat["artistes_lies"] == 1
    assert "1 fiches artistes rattachees" in formater(etat)
