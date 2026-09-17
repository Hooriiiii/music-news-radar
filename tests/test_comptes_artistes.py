"""Qui est un « artiste suivi » : le nom d'une source Instagram d'artiste, son
handle, ou une fiche artists liee a la main."""

import datetime as dt

import pytest

from app.models import Artist, Source, SourceCategorie, SourceType
from app.pipeline.comptes_artistes import cle_artiste, construire_index, lier_artistes_comptes

MAINTENANT = dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.timezone.utc)


def _compte(db, nom, handle, categorie=SourceCategorie.ARTISTE, active=True, state=None):
    source = Source(name=nom, type=SourceType.INSTAGRAM, url=f"ig:{handle}", categorie=categorie,
                    active=active, state=state)
    db.add(source)
    db.commit()
    return source


@pytest.mark.parametrize(
    "nom, cle",
    [
        ("Fred again.. (IG)", "fred again"),
        ("DJ Snake (FR) (IG)", "dj snake"),
        ("@amelie_lens", "amelie lens"),
        ("Guy-Man", "guyman"),
        ("Amélie Lens", "amelie lens"),
        (None, ""),
    ],
)
def test_cle_artiste(nom, cle):
    assert cle_artiste(nom) == cle


def test_un_titre_de_presse_cite_un_artiste_suivi_de_deux_mots(db_session):
    fred = _compte(db_session, "Fred again.. (IG)", "fredagainagainagainagainagain")
    index = construire_index(db_session)
    titre = "GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more"
    assert [c.source_id for c in index.cites(textes=[titre])] == [fred.id]


def test_un_nom_rendu_par_un_modele_cite_meme_d_un_seul_mot(db_session):
    anyma = _compte(db_session, "Anyma (IG)", "anyma")
    index = construire_index(db_session)
    assert index.cites(textes=["Anyma au Sphere de Las Vegas"]) == []
    assert [c.source_id for c in index.cites(noms=["Anyma"])] == [anyma.id]


def test_un_handle_mentionne_cite(db_session):
    anyma = _compte(db_session, "Anyma (IG)", "anyma")
    index = construire_index(db_session)
    assert [c.source_id for c in index.cites(textes=["Merci @Anyma pour hier"])] == [anyma.id]
    assert index.cites(textes=["Merci @anyma_fanclub"]) == []


def test_pas_de_citation_par_morceau_de_mot(db_session):
    _compte(db_session, "Black Coffee (IG)", "realblackcoffee")
    index = construire_index(db_session)
    assert index.cites(textes=["Blackcoffee lovers unite"]) == []


def test_seuls_les_comptes_d_artistes_actifs_comptent(db_session):
    _compte(db_session, "Guettapen (IG)", "guettapen", categorie=SourceCategorie.MEDIA)
    _compte(db_session, "Peggy Gou (IG)", "peggygou", active=False)
    index = construire_index(db_session)
    assert index.cites(textes=["Guettapen annonce", "Peggy Gou en tournee"]) == []


def test_la_source_de_l_article_peut_etre_exclue(db_session):
    fred = _compte(db_session, "Fred again.. (IG)", "fredagain")
    index = construire_index(db_session)
    assert index.cites(noms=["Fred again.."], exclure_source_id=fred.id) == []


def test_une_fiche_artiste_liee_ajoute_un_alias(db_session):
    thomas = _compte(db_session, "Thomas Bangalter (IG)", "thomasbangalter")
    db_session.add(Artist(name="Guy-Manuel", slug="guy-manuel", first_seen_at=MAINTENANT,
                          last_seen_at=MAINTENANT, source_instagram_id=thomas.id))
    db_session.commit()
    index = construire_index(db_session)
    assert [c.source_id for c in index.cites(noms=["Guy-Manuel"])] == [thomas.id]


def test_le_dernier_appel_du_pipeline_est_lu(db_session):
    _compte(db_session, "Fred again.. (IG)", "fredagain", state={"dernier_appel": MAINTENANT.isoformat()})
    (compte,) = construire_index(db_session).comptes.values()
    assert compte.dernier_appel == MAINTENANT
    assert compte.handle == "fredagain"
    assert compte.nom == "Fred again.."


def test_lier_artistes_comptes_par_le_nom_sans_ecraser(db_session):
    fred = _compte(db_session, "Fred again.. (IG)", "fredagain")
    autre = _compte(db_session, "Autre (IG)", "autre")
    libre = Artist(name="Fred Again..", slug="fred again..", first_seen_at=MAINTENANT, last_seen_at=MAINTENANT)
    deja = Artist(name="Fred again", slug="fred again", first_seen_at=MAINTENANT, last_seen_at=MAINTENANT,
                  source_instagram_id=autre.id)
    inconnu = Artist(name="Inconnu Total", slug="inconnu total", first_seen_at=MAINTENANT, last_seen_at=MAINTENANT)
    db_session.add_all([libre, deja, inconnu])
    db_session.commit()

    assert lier_artistes_comptes(db_session) == 1
    assert lier_artistes_comptes(db_session) == 0
    assert (libre.source_instagram_id, deja.source_instagram_id, inconnu.source_instagram_id) == (
        fred.id, autre.id, None,
    )
