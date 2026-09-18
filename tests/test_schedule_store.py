import datetime as dt

import pytest

from app.config import settings
from app.content.media import entree
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, Source, SourceType
from app.schedule.slots import prochains_horaires
from app.schedule.store import (
    appliquer_arbitrage,
    candidats_eligibles,
    synchroniser_creneaux,
    utc,
)


@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    # Ces tests decrivent la file carrousel seule ; les reels ont leurs propres fichiers.
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)


MAINTENANT = dt.datetime(2026, 9, 20, 10, 0, tzinfo=dt.timezone.utc)


def _source(db):
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db.add(source)
    db.commit()
    return source


def _article(db, source, n, relevance=80, age_heures=2, media=True, **champs):
    article = Article(
        source_id=source.id, url=f"https://t/{n}", title=f"Actu {n}", raw_hash=f"h{n}",
        relevance_score=relevance, hotness_score=20,
        published_at=MAINTENANT - dt.timedelta(hours=age_heures),
        media=[{"type": "photo", "url": f"https://img/{n}.jpg"}] if media else None,
        **champs,
    )
    db.add(article)
    db.commit()
    return article


def _post(db, article):
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                caption="Legende", slides=[{"kind": "cover"}])
    db.add(post)
    db.commit()
    return post


def test_synchroniser_cree_neuf_creneaux_une_seule_fois(db_session):
    synchroniser_creneaux(db_session, MAINTENANT)
    creneaux = synchroniser_creneaux(db_session, MAINTENANT)
    assert len(creneaux) == 9
    assert db_session.query(Creneau).count() == 9
    assert [utc(c.publier_a) for c in creneaux] == prochains_horaires(MAINTENANT, 9)


def test_passer_de_six_a_neuf_creneaux_complete_la_file_sans_la_casser(db_session, monkeypatch):
    """Le cas de la montee en cadence : une file deja programmee sur l'ancien
    compte doit se COMPLETER, pas se vider ni se dupliquer."""
    monkeypatch.setattr(settings, "publication_creneaux", 6)
    avant = synchroniser_creneaux(db_session, MAINTENANT)
    assert len(avant) == 6
    avant[0].etat = EtatCreneau.GELE
    avant[1].epingle = True
    db_session.commit()
    gele, epingle = utc(avant[0].publier_a), utc(avant[1].publier_a)

    monkeypatch.setattr(settings, "publication_creneaux", 9)
    apres = synchroniser_creneaux(db_session, MAINTENANT)

    heures = [utc(c.publier_a) for c in apres]
    assert len(heures) == 9
    assert len(set(heures)) == 9, "aucune collision d'horaire"
    assert heures == sorted(heures)
    assert heures == prochains_horaires(MAINTENANT, 9)
    # Le gele et l'epingle sont les MEMES lignes, pas des doublons recrees.
    conserves = {c.id for c in avant[:2]}
    assert conserves <= {c.id for c in apres}
    assert gele in heures and epingle in heures


def test_un_creneau_futur_hors_grille_est_retire_sauf_s_il_est_epingle(db_session):
    hors_grille = MAINTENANT + dt.timedelta(hours=1, minutes=1)
    epingle = MAINTENANT + dt.timedelta(hours=1, minutes=2)
    db_session.add_all([Creneau(publier_a=hors_grille), Creneau(publier_a=epingle, epingle=True)])
    db_session.commit()
    heures = {utc(c.publier_a) for c in synchroniser_creneaux(db_session, MAINTENANT)}
    assert hors_grille not in heures
    assert epingle in heures


def test_seuls_les_articles_publiables_sont_candidats(db_session):
    source = _source(db_session)
    bon = _article(db_session, source, 1)
    _article(db_session, source, 2, relevance=40)
    _article(db_session, source, 3, duplicate_of_id=bon.id)
    _article(db_session, source, 4, status=ArticleStatus.USED)
    _article(db_session, source, 5, file_exclu_raison="saute depuis le dashboard")
    _article(db_session, source, 6, age_heures=24 * 5)
    _article(db_session, source, 7, media=False)
    assert [c.article_id for c in candidats_eligibles(db_session, MAINTENANT)] == [bon.id]


def test_un_article_deja_compose_est_signale(db_session):
    source = _source(db_session)
    article = _article(db_session, source, 1)
    _post(db_session, article)
    (candidat,) = candidats_eligibles(db_session, MAINTENANT)
    assert candidat.compose is True


def test_appliquer_remplit_dans_l_ordre_et_laisse_le_reste_libre(db_session):
    source = _source(db_session)
    moyen = _article(db_session, source, 1, relevance=70)
    meilleur = _article(db_session, source, 2, relevance=90)
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert [c.article_id for c in creneaux] == [meilleur.id, moyen.id] + [None] * 7
    assert [c.etat for c in creneaux[:3]] == [EtatCreneau.PROGRAMME, EtatCreneau.PROGRAMME, EtatCreneau.LIBRE]


def test_appliquer_ne_touche_pas_un_creneau_gele(db_session):
    source = _source(db_session)
    ancien = _article(db_session, source, 1, relevance=60)
    _article(db_session, source, 2, relevance=95)
    premier = synchroniser_creneaux(db_session, MAINTENANT)[0]
    premier.article_id, premier.etat = ancien.id, EtatCreneau.GELE
    db_session.commit()
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert (creneaux[0].article_id, creneaux[0].etat) == (ancien.id, EtatCreneau.GELE)


def test_appliquer_reprend_le_post_deja_compose(db_session):
    source = _source(db_session)
    article = _article(db_session, source, 1)
    post = _post(db_session, article)
    assert appliquer_arbitrage(db_session, MAINTENANT)[0].post_id == post.id


def test_un_article_retenu_par_un_creneau_passe_n_est_pas_programme_deux_fois(db_session):
    """Mac reveille en retard : le creneau de 7 h 12 est passe mais encore dans
    les 90 minutes. Son article ne doit pas etre place aussi sur un creneau futur."""
    source = _source(db_session)
    article = _article(db_session, source, 1)
    db_session.add(Creneau(publier_a=MAINTENANT - dt.timedelta(minutes=20),
                           article_id=article.id, etat=EtatCreneau.PROGRAMME))
    db_session.commit()
    assert all(c.article_id is None for c in appliquer_arbitrage(db_session, MAINTENANT))


def test_deux_passages_donnent_la_meme_file(db_session):
    source = _source(db_session)
    for n in range(3):
        _article(db_session, source, n, relevance=70 + n)
    premiere = [c.article_id for c in appliquer_arbitrage(db_session, MAINTENANT)]
    seconde = [c.article_id for c in appliquer_arbitrage(db_session, MAINTENANT)]
    assert premiere == seconde
    assert db_session.query(Creneau).count() == 9


def test_un_article_deja_parti_en_apercu_ou_publie_n_est_plus_candidat(db_session):
    source = _source(db_session)
    apercu = _article(db_session, source, 1)
    publie = _article(db_session, source, 2)
    libre = _article(db_session, source, 3)
    passe = MAINTENANT - dt.timedelta(hours=5)
    db_session.add_all([
        Creneau(publier_a=passe, article_id=apercu.id, etat=EtatCreneau.APERCU),
        Creneau(publier_a=passe - dt.timedelta(hours=1), article_id=publie.id, etat=EtatCreneau.PUBLIE),
    ])
    db_session.commit()
    assert [c.article_id for c in candidats_eligibles(db_session, MAINTENANT)] == [libre.id]


def test_un_reel_sans_media_url_reste_candidat_carrousel_par_sa_vignette(db_session):
    article = _article(db_session, _source(db_session), 1, media=False)
    article.media = [entree("video", None, preview_url="https://cdn/v.jpg")]
    db_session.commit()
    assert [c.article_id for c in candidats_eligibles(db_session, MAINTENANT)] == [article.id]
