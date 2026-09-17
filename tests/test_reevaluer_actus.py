"""Rattraper les actus enterrees par l'ancienne decote et le pre-filtre.
Plan par defaut, aucune ecriture ; --apply re-note par Claude."""

import datetime as dt
import itertools

import pytest

import scripts.reevaluer_actus as script
from app.models import Article, ArticleStatus, ScoredBy, Source, SourceCategorie, SourceType
from app.pipeline.scoring import ArticleScore, Category
from app.sources.instagram import TITRE_PAR_DEFAUT

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
_numeros = itertools.count()


def _source(db, nom, categorie=SourceCategorie.MEDIA, type_=SourceType.RSS, handle=None):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{nom}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, relevance, scored_by=ScoredBy.CLAUDE, *, heures=5, rep=None, **champs):
    n = next(_numeros)
    moment = MAINTENANT - dt.timedelta(hours=heures)
    article = Article(source_id=source.id, url=f"https://a.example/{n}", title=titre, raw_hash=f"r{n:063d}",
                      published_at=moment, fetched_at=moment, relevance_score=relevance, scored_by=scored_by,
                      duplicate_of_id=rep.id if rep else None, **champs)
    db.add(article)
    db.commit()
    return article


@pytest.fixture()
def fred(db_session):
    return _source(db_session, "Fred again.. (IG)", SourceCategorie.ARTISTE, SourceType.INSTAGRAM, "fredagain")


def _interdit(article):
    raise AssertionError("aucun appel Claude en mode plan")


def test_le_plan_retient_les_victimes_probables(db_session, fred):
    presse = _source(db_session, "presse")
    proche = _article(db_session, presse, "Actu media proche du seuil", 55)
    cite = _article(db_session, presse, "GTA VI soundtrack featuring Fred again.. and more", 45)
    _article(db_session, presse, "Actu media quelconque", 45)
    local_cite = _article(db_session, presse, "Fred again.. annonce une date", 20, ScoredBy.LOCAL)
    rep_local = _article(db_session, presse, "Actu reprise partout", 20, ScoredBy.LOCAL)
    _article(db_session, _source(db_session, "b"), "Actu reprise", None, None, rep=rep_local)
    _article(db_session, _source(db_session, "c"), "Actu reprise", None, None, rep=rep_local)
    artiste = _source(db_session, "Artiste (IG)", SourceCategorie.ARTISTE, SourceType.INSTAGRAM, "artiste")
    _article(db_session, artiste, "Post d'artiste moyen", 45)
    _article(db_session, presse, "Deja publie Fred again..", 55, status=ArticleStatus.USED)
    _article(db_session, presse, "Trop vieux Fred again..", 55, heures=24 * 4)
    _article(db_session, presse, TITRE_PAR_DEFAUT, 20, ScoredBy.LOCAL)
    _article(db_session, presse, "Exclu Fred again..", 55, file_exclu_raison="saute depuis le dashboard")

    plan = script.planifier(db_session, MAINTENANT)

    assert {(r.article_id, r.raison) for r in plan} == {
        (proche.id, script.RAISON_MEDIA),
        (cite.id, script.RAISON_MEDIA),
        (local_cite.id, script.RAISON_ARTISTE),
        (rep_local.id, script.RAISON_CLUSTER),
    }
    # Artistes suivis d'abord, puis la taille du groupe.
    assert plan[0].artistes == 1


def test_la_limite_coupe_apres_le_tri(db_session, fred):
    presse = _source(db_session, "presse")
    _article(db_session, presse, "Actu media proche du seuil", 58)
    cite = _article(db_session, presse, "Fred again.. et la BO de GTA VI", 45)
    assert [r.article_id for r in script.planifier(db_session, MAINTENANT, limite=1)] == [cite.id]


def test_sans_apply_rien_n_est_ecrit(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"),
                      "Fred again.., PinkPantheress and more teased for GTA 6 soundtrack", 59, heures=6)
    djmag = _article(db_session, _source(db_session, "djmag"),
                     "GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more", 45,
                     heures=3, rep=mixmag)

    election, plan = script.reevaluer(db_session, MAINTENANT, scorer=_interdit,
                                      noter_en_attente=lambda db, scorer: _interdit(None))

    db_session.expire_all()
    assert election.changements == [(mixmag.id, djmag.id)]
    assert db_session.get(Article, djmag.id).duplicate_of_id == mixmag.id
    assert db_session.get(Article, mixmag.id).relevance_score == 59
    assert [r.article_id for r in plan] == [mixmag.id]


def test_apply_elit_note_les_en_attente_puis_re_note_le_plan(db_session, fred):
    mixmag = _article(db_session, _source(db_session, "mixmag"),
                      "Fred again.., PinkPantheress and more teased for GTA 6 soundtrack", 59, heures=6)
    djmag = _article(db_session, _source(db_session, "djmag"),
                     "GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more", 45,
                     heures=3, rep=mixmag)
    en_attente = []

    def claude(article):
        return ArticleScore(relevance=74, hotness=60, emergence=5, category=Category.ANNOUNCEMENT,
                            summary="La BO de GTA VI.", artists=["Fred again.."])

    election, plan = script.reevaluer(
        db_session, MAINTENANT, appliquer=True, scorer=claude,
        noter_en_attente=lambda db, scorer: en_attente.append(scorer),
    )

    assert election.reelus == 1
    assert djmag.duplicate_of_id is None
    assert en_attente == [claude]
    assert [(r.article_id, r.apres) for r in plan] == [(djmag.id, 74)]
    assert (djmag.relevance_score, djmag.scored_by) == (74, ScoredBy.CLAUDE)
