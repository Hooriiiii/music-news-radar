"""« Ne pas publier » vaut pour l'ACTU, pas pour une ligne.

Le defaut reproduit le 2026-10-31 : A programme sans post, doublon B mieux classe.
Le retrait libere le creneau et exclut A, donc A n'est plus « engage », et l'election
horaire promouvait B, candidat, programme. Regle et limites :
docs/reference/dedup-et-retention.md, « Le retrait humain vaut pour l'actu ».
"""

import datetime as dt
import itertools

import pytest

from app.config import settings
from app.models import (
    Article, ArticleStatus, Creneau, EtatCreneau, ScoredBy, Source, SourceCategorie, SourceType,
)
from app.pipeline.cluster import link_duplicates
from app.pipeline.comptes_artistes import construire_index
from app.pipeline.election import elire_representants
from app.pipeline.retention import purge_old_articles
from app.pipeline.retrait_humain import RAISON_NE_PAS_PUBLIER, RAISON_SAUTE_ANCIENNE, retrait_humain
from app.pipeline.scoring import score_pending
from app.schedule.publication_incertaine import RAISON_RECLAMATION
from app.schedule.reouverture import ne_pas_publier
from app.schedule.slots import prochains_horaires
from app.schedule.store import appliquer_arbitrage, candidats_eligibles

_numeros = itertools.count(1)
VAGUE = "Grosse annonce floue electro"
DETAILLE = "Grosse annonce floue electro avec tous les details du festival"


@pytest.fixture(autouse=True)
def _reglages(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
    monkeypatch.setattr(settings, "publication_creneaux", 2)
    monkeypatch.setattr(settings, "publication_min_relevance", 60)
    monkeypatch.setattr(settings, "cluster_arbitrage_cache_path", tmp_path / "arbitrage.json")


def _maintenant():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _source(db, nom="Mixmag", categorie=SourceCategorie.MEDIA):
    n = next(_numeros)
    source = Source(name=f"{nom}{n}", type=SourceType.RSS, url=f"https://{n}/f", categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre=VAGUE, *, rep=None, heures=3.0, relevance=90, **champs):
    n = next(_numeros)
    moment = _maintenant() - dt.timedelta(hours=heures)
    article = Article(
        source_id=source.id, url=f"https://t/{n}", title=titre, raw_hash=f"r{n}", published_at=moment,
        fetched_at=moment, relevance_score=relevance, hotness_score=20,
        duplicate_of_id=rep.id if rep else None,
        media=[{"type": "photo", "url": f"https://img/{n}.jpg"}], **champs,
    )
    db.add(article)
    db.commit()
    return article


def _groupe(db):
    a = _article(db, _source(db))
    b = _article(db, _source(db, "Artiste", SourceCategorie.ARTISTE), DETAILLE, rep=a, heures=2.0)
    return a, b


def _programmer(db, article):
    creneau = Creneau(publier_a=prochains_horaires(_maintenant(), 1)[0], article_id=article.id,
                      etat=EtatCreneau.PROGRAMME, epingle=True)
    db.add(creneau)
    db.commit()
    return creneau


def _election(db):
    return elire_representants(db, construire_index(db), maintenant=_maintenant())


def _candidats(db):
    return {c.article_id for c in candidats_eligibles(db, _maintenant())}


def test_le_scenario_du_defaut_ne_repart_plus(client, db_session):
    a, b = _groupe(db_session)
    creneau = _programmer(db_session, a)
    assert _election(db_session).verrouilles == 1

    assert client.patch(f"/schedule/{creneau.id}", json={"sauter": True}).status_code == 200

    stats = _election(db_session)
    assert stats.reelus == 0 and stats.verrouilles == 1
    db_session.refresh(b)
    assert b.duplicate_of_id == a.id
    assert not _candidats(db_session) & {a.id, b.id}
    appliquer_arbitrage(db_session, _maintenant())
    assert not {c.article_id for c in db_session.query(Creneau).all()} & {a.id, b.id}


def test_un_doublon_arrive_apres_le_retrait_ne_repart_pas_non_plus(client, db_session):
    a, b = _groupe(db_session)
    creneau = _programmer(db_session, a)
    client.patch(f"/schedule/{creneau.id}", json={"sauter": True})

    tardif = _article(db_session, _source(db_session, "Label", SourceCategorie.LABEL),
                      f"{DETAILLE} electro complet", heures=1.0, relevance=None)
    assert link_duplicates(db_session).total == 1

    assert _election(db_session).reelus == 0
    db_session.refresh(tardif)
    assert tardif.duplicate_of_id == a.id
    assert not _candidats(db_session) & {a.id, b.id, tardif.id}


@pytest.mark.parametrize("raison", [RAISON_NE_PAS_PUBLIER, RAISON_SAUTE_ANCIENNE])
def test_les_deux_formes_du_geste_humain_verrouillent(db_session, raison):
    a, b = _groupe(db_session)
    a.file_exclu_raison = raison
    db_session.commit()

    assert retrait_humain(raison)
    assert _election(db_session).reelus == 0
    assert b.id not in _candidats(db_session)


@pytest.mark.parametrize("raison", [
    "gel impossible : image cassee",
    "composition impossible : texte",
    RAISON_RECLAMATION,
    "echec de publication : refus",
    "doublon d'une actu deja publiee (article 1, similarite 0.80)",
])
def test_une_exclusion_machine_ne_verrouille_jamais_le_groupe(db_session, raison):
    a, b = _groupe(db_session)
    a.file_exclu_raison = raison
    db_session.commit()

    assert not retrait_humain(raison)
    assert _election(db_session).reelus == 1
    db_session.refresh(b)
    assert b.duplicate_of_id is None
    assert b.id in _candidats(db_session)


def test_remettre_dans_la_file_rouvre_tout_sans_verrou(client, db_session):
    a, b = _groupe(db_session)
    creneau = _programmer(db_session, a)
    client.patch(f"/schedule/{creneau.id}", json={"sauter": True})

    reponse = client.post(f"/articles/{a.id}/reintegrer")
    assert reponse.status_code == 200 and reponse.json()["file_exclu_raison"] is None

    db_session.expire_all()
    assert _election(db_session).reelus == 1, "plus aucun verrou : l'election reprend"
    assert b.id in _candidats(db_session)


def test_un_retrait_rattache_plus_tard_passe_au_representant(client, db_session):
    """Un article plus ancien ingere apres coup prend la tete du groupe : sans report,
    il partait, et la carte du retrait etait masquee (doublon)."""
    retire = _article(db_session, _source(db_session), heures=3.0, file_exclu_raison=RAISON_NE_PAS_PUBLIER)
    ancien = _article(db_session, _source(db_session), f"{VAGUE} complet", heures=5.0)

    assert link_duplicates(db_session).total == 1

    db_session.refresh(retire)
    db_session.refresh(ancien)
    assert retire.duplicate_of_id == ancien.id
    assert ancien.file_exclu_raison == RAISON_NE_PAS_PUBLIER
    assert ancien.id not in _candidats(db_session)

    assert client.post(f"/articles/{ancien.id}/reintegrer").status_code == 200
    db_session.expire_all()
    assert ancien.file_exclu_raison is None and retire.file_exclu_raison is None
    assert ancien.id in _candidats(db_session)


def test_retirer_un_doublon_programme_retire_aussi_son_representant(db_session):
    """Un article programme puis rattache : le geste vise l'actu, pas la ligne."""
    representant = _article(db_session, _source(db_session))
    doublon = _article(db_session, _source(db_session), rep=representant)
    creneau = _programmer(db_session, doublon)

    ne_pas_publier(db_session, creneau)

    db_session.refresh(representant)
    assert representant.file_exclu_raison == RAISON_NE_PAS_PUBLIER
    assert representant.id not in _candidats(db_session)


def test_une_ligne_ancienne_ou_seul_un_doublon_porte_le_retrait_se_repare(db_session):
    a, b = _groupe(db_session)
    b.file_exclu_raison = RAISON_SAUTE_ANCIENNE
    db_session.commit()

    assert _election(db_session).reelus == 0

    db_session.refresh(a)
    assert a.file_exclu_raison == RAISON_SAUTE_ANCIENNE
    assert a.id not in _candidats(db_session)


def test_une_exclusion_deja_posee_sur_le_representant_n_est_pas_recouverte(db_session):
    a, b = _groupe(db_session)
    a.file_exclu_raison = "gel impossible : image"
    b.file_exclu_raison = RAISON_NE_PAS_PUBLIER
    db_session.commit()

    assert _election(db_session).reelus == 0
    db_session.refresh(a)
    assert a.file_exclu_raison == "gel impossible : image"


def test_un_representant_publie_ne_recoit_jamais_le_retrait(db_session):
    publie = _article(db_session, _source(db_session), status=ArticleStatus.USED)
    _article(db_session, _source(db_session), rep=publie, file_exclu_raison=RAISON_NE_PAS_PUBLIER)

    _election(db_session)

    db_session.refresh(publie)
    assert publie.file_exclu_raison is None


def test_la_purge_du_representant_transmet_le_retrait_a_ses_doublons(client, db_session):
    """`ON DELETE SET NULL` : sans transmission, le doublon redevenait representant,
    libre, deux jours apres le retrait."""
    a = _article(db_session, _source(db_session), heures=60.0, file_exclu_raison=RAISON_NE_PAS_PUBLIER)
    aid = a.id
    b = _article(db_session, _source(db_session), DETAILLE, rep=a, heures=2.0, relevance=None)
    # SQLite de test : `ON DELETE SET NULL` n'y joue que si la cle etrangere est active.
    db_session.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=ON"))

    purge_old_articles(db_session, 2)

    db_session.expire_all()
    assert db_session.get(Article, aid) is None
    assert b.duplicate_of_id is None
    assert b.file_exclu_raison == RAISON_NE_PAS_PUBLIER
    assert client.post(f"/articles/{b.id}/reintegrer").json()["file_exclu_raison"] is None


def test_la_purge_d_un_representant_exclu_par_la_machine_ne_transmet_rien(db_session):
    a = _article(db_session, _source(db_session), heures=60.0, file_exclu_raison="gel impossible : x")
    b = _article(db_session, _source(db_session), DETAILLE, rep=a, heures=2.0, relevance=None)

    purge_old_articles(db_session, 2)

    db_session.expire_all()
    assert b.file_exclu_raison is None


def test_le_verrou_ne_coute_aucun_appel_claude(db_session):
    """Ni reelection d'un doublon jamais note, ni remise a noter du representant."""
    a = _article(db_session, _source(db_session), scored_by=ScoredBy.LOCAL, file_exclu_raison=RAISON_NE_PAS_PUBLIER)
    for _ in range(settings.cluster_sources_claude):
        _article(db_session, _source(db_session), rep=a, relevance=None)

    stats = _election(db_session)

    assert stats.reelus == 0 and stats.a_rescorer == 0
    db_session.refresh(a)
    assert a.relevance_score == 90


def test_un_article_retire_sans_note_n_est_pas_paye(db_session):
    heritier = _article(db_session, _source(db_session), relevance=None, file_exclu_raison=RAISON_NE_PAS_PUBLIER)
    libre = _article(db_session, _source(db_session), DETAILLE, relevance=None, heures=1.0)
    machine = _article(db_session, _source(db_session), f"{DETAILLE} bis", relevance=None, heures=1.5,
                       file_exclu_raison="gel impossible : x")
    vus = []

    def scorer(article):
        vus.append(article.id)
        raise RuntimeError("stop")

    score_pending(db_session, scorer)

    assert heritier.id not in vus
    assert libre.id in vus and machine.id in vus
