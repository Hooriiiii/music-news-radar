"""Le representant d'un groupe de doublons : le plus informatif, plus seulement le
plus ancien. Cas reel du 2026-09-17 : DJ Mag masque derriere Mixmag."""

import datetime as dt
import itertools

import pytest

from app.config import settings
from app.content.visuels import origine_interdite
from app.models import (
    Article, ArticleStatus, Creneau, EtatCreneau, Post, ScoredBy, Source, SourceCategorie, SourceType,
)
from app.pipeline.comptes_artistes import construire_index
from app.pipeline.election import (
    cle_information,
    elire_representants,
    representant_engage,
    tailles_clusters,
)

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 17, 18, 0, tzinfo=UTC)
_numeros = itertools.count()

MIXMAG = "Fred again.., PinkPantheress and more teased for GTA 6 soundtrack"
DJMAG = "GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more"


def _source(db, nom, categorie=SourceCategorie.MEDIA, type_=SourceType.RSS, handle=None):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{nom}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, *, heures=2, rep=None, relevance=None, scored_by=None, status=ArticleStatus.NEW):
    n = next(_numeros)
    moment = MAINTENANT - dt.timedelta(hours=heures)
    article = Article(
        source_id=source.id, url=f"https://a.example/{n}", title=titre, raw_hash=f"e{n:063d}",
        published_at=moment, fetched_at=moment, duplicate_of_id=rep.id if rep else None,
        relevance_score=relevance, scored_by=scored_by, status=status,
    )
    db.add(article)
    db.commit()
    return article


@pytest.fixture()
def fred(db_session):
    return _source(db_session, "Fred again.. (IG)", SourceCategorie.ARTISTE, SourceType.INSTAGRAM,
                   "fredagainagainagainagainagain")


def test_origine_interdite_ne_vise_que_les_comptes_instagram_media():
    def source(type_, categorie):
        return Source(name="x", type=type_, url="x", categorie=categorie)

    assert origine_interdite(source(SourceType.INSTAGRAM, SourceCategorie.MEDIA))
    assert not origine_interdite(source(SourceType.RSS, SourceCategorie.MEDIA))
    assert not origine_interdite(source(SourceType.INSTAGRAM, SourceCategorie.ARTISTE))
    assert not origine_interdite(source(SourceType.INSTAGRAM, None))
    assert not origine_interdite(None)


def test_la_taille_se_compte_en_sources_distinctes(db_session):
    a, b = _source(db_session, "a"), _source(db_session, "b")
    rep = _article(db_session, a, "Actu", heures=5)
    _article(db_session, a, "Actu bis", rep=rep)
    _article(db_session, b, "Actu ter", rep=rep)
    seul = _article(db_session, b, "Autre")
    assert tailles_clusters(db_session, [rep.id, seul.id]) == {rep.id: 2, seul.id: 1}


def test_dj_mag_plus_informatif_devient_representant(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6,
                      relevance=59, scored_by=ScoredBy.CLAUDE)
    djmag = _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag)

    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)

    assert (stats.reelus, stats.changements) == (1, [(mixmag.id, djmag.id)])
    assert djmag.duplicate_of_id is None
    assert mixmag.duplicate_of_id == djmag.id


def test_l_election_est_idempotente(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6)
    _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag)
    index = construire_index(db_session)
    elire_representants(db_session, index, maintenant=MAINTENANT)
    assert elire_representants(db_session, index, maintenant=MAINTENANT).reelus == 0


def test_jamais_de_chaine_apres_reelection(db_session, fred):
    rep = _article(db_session, _source(db_session, "a"), "GTA soundtrack news", heures=8)
    moyen = _article(db_session, _source(db_session, "b"), "GTA VI soundtrack news again today", rep=rep)
    meilleur = _article(db_session, _source(db_session, "c"), DJMAG, rep=rep)
    elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)
    assert (rep.duplicate_of_id, moyen.duplicate_of_id, meilleur.duplicate_of_id) == (
        meilleur.id, meilleur.id, None,
    )


def test_une_source_primaire_passe_devant_un_media_plus_bavard(db_session):
    media = _article(db_session, _source(db_session, "presse"),
                     "Festival Astropolis devoile enfin toute la programmation complete", heures=6)
    festival = _source(db_session, "Astropolis (IG)", SourceCategorie.FESTIVAL, SourceType.INSTAGRAM, "astropolis")
    officiel = _article(db_session, festival, "Astropolis programmation devoilee", rep=media)
    elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)
    assert officiel.duplicate_of_id is None


def test_un_compte_media_instagram_cede_a_la_presse_a_egalite(db_session):
    ig = _source(db_session, "Guettapen (IG)", SourceCategorie.MEDIA, SourceType.INSTAGRAM, "guettapen")
    post = _article(db_session, ig, "Astropolis annonce sa programmation", heures=6)
    presse = _article(db_session, _source(db_session, "tsugi"), "Astropolis annonce programmation", rep=post)
    index = construire_index(db_session)
    assert cle_information(presse, index)[2] is True
    assert cle_information(post, index)[2] is False
    elire_representants(db_session, index, maintenant=MAINTENANT)
    assert presse.duplicate_of_id is None


@pytest.mark.parametrize("engagement", ["used", "post", "creneau"])
def test_un_representant_engage_ne_change_jamais(db_session, fred, engagement):
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6,
                      status=ArticleStatus.USED if engagement == "used" else ArticleStatus.NEW)
    djmag = _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag)
    if engagement == "post":
        db_session.add(Post(article_id=mixmag.id, article_title="t", article_url="u", caption="c", slides=[]))
    if engagement == "creneau":
        db_session.add(Creneau(publier_a=MAINTENANT + dt.timedelta(hours=3), article_id=mixmag.id,
                               etat=EtatCreneau.PROGRAMME))
    db_session.commit()
    assert representant_engage(db_session, mixmag)

    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)

    assert (stats.reelus, stats.verrouilles) == (0, 1)
    assert djmag.duplicate_of_id == mixmag.id


def test_un_representant_local_d_un_groupe_de_trois_sources_est_remis_a_noter(db_session):
    rep = _article(db_session, _source(db_session, "a"), "Actu commune longue et precise", heures=8,
                   relevance=25, scored_by=ScoredBy.LOCAL)
    rep.mentioned_artists = ["X"]
    db_session.commit()
    _article(db_session, _source(db_session, "b"), "Actu commune", rep=rep)
    _article(db_session, _source(db_session, "c"), "Actu commune", rep=rep)

    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)

    assert stats.a_rescorer == 1
    assert (rep.relevance_score, rep.scored_by, rep.mentioned_artists) == (None, None, None)


@pytest.mark.parametrize("scored_by, sources", [(ScoredBy.LOCAL, 2), (ScoredBy.CLAUDE, 3)])
def test_pas_de_remise_a_noter_hors_regle(db_session, scored_by, sources):
    noms = ["a", "b", "c"]
    rep = _article(db_session, _source(db_session, noms[0]), "Actu commune longue et precise", heures=8,
                   relevance=25, scored_by=scored_by)
    for i in range(2):
        source = _source(db_session, noms[1 + i]) if i < sources - 1 else rep.source
        _article(db_session, source, "Actu commune", rep=rep)
    assert elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT).a_rescorer == 0
    assert rep.relevance_score == 25


def test_un_plan_sans_appliquer_ne_touche_a_rien(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6)
    djmag = _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag)
    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT, appliquer=False)
    db_session.expire_all()
    assert stats.changements == [(mixmag.id, djmag.id)]
    assert db_session.get(Article, djmag.id).duplicate_of_id == mixmag.id


def test_la_borne_par_run(db_session, fred):
    for n in range(3):
        rep = _article(db_session, _source(db_session, f"m{n}"), MIXMAG, heures=6)
        _article(db_session, _source(db_session, f"d{n}"), DJMAG, heures=3, rep=rep)
    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT, limite=2)
    assert stats.reelus == 2


def test_les_doublons_hors_fenetre_sont_laisses(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=100)
    djmag = _article(db_session, _source(db_session, "djmag"), DJMAG, heures=90, rep=mixmag)
    elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)
    assert djmag.duplicate_of_id == mixmag.id


def test_le_reglage_par_defaut_borne_la_run(db_session, fred, monkeypatch):
    monkeypatch.setattr(settings, "cluster_reevaluations_max_par_run", 0)
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6)
    _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag)
    assert elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT).reelus == 0


def test_un_representant_engage_n_est_jamais_remis_a_noter(db_session):
    rep = _article(db_session, _source(db_session, "a"), "Actu commune longue et precise", heures=8,
                   relevance=25, scored_by=ScoredBy.LOCAL)
    _article(db_session, _source(db_session, "b"), "Actu commune", rep=rep)
    _article(db_session, _source(db_session, "c"), "Actu commune", rep=rep)
    db_session.add(Creneau(publier_a=MAINTENANT + dt.timedelta(hours=3), article_id=rep.id,
                           etat=EtatCreneau.PROGRAMME))
    db_session.commit()

    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT)

    assert stats.a_rescorer == 0
    assert (rep.relevance_score, rep.scored_by) == (25, ScoredBy.LOCAL)


def test_la_borne_est_exacte_meme_dans_un_seul_groupe(db_session, fred):
    # Reelection ET remise a noter possibles dans le meme groupe : une seule action.
    mixmag = _article(db_session, _source(db_session, "mixmag"), MIXMAG, heures=6)
    djmag = _article(db_session, _source(db_session, "djmag"), DJMAG, heures=3, rep=mixmag,
                     relevance=25, scored_by=ScoredBy.LOCAL)
    _article(db_session, _source(db_session, "tsugi"), "GTA soundtrack", heures=2, rep=mixmag)

    stats = elire_representants(db_session, construire_index(db_session), maintenant=MAINTENANT, limite=1)

    assert (stats.reelus, stats.a_rescorer) == (1, 0)
    assert djmag.duplicate_of_id is None
    assert djmag.relevance_score == 25
