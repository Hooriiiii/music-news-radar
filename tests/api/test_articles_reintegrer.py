"""L'exclusion de la file a enfin un retour en arriere.

`articles.file_exclu_raison` refuse l'article PARTOUT (`store._articles_publiables`),
il est ecrit par « 🚫 Sauter », par le gel et par la composition -- et aucun
endpoint ne le remettait a NULL. Le seul recours etait un UPDATE a la main.
"""

import datetime as dt

from app.models import Article, ArticleStatus, Source, SourceType
from app.pipeline.cluster import RAISON_DOUBLON_PREFIXE


def _source(db):
    source = Source(name="Presse", type=SourceType.RSS, url="https://example.com/feed.xml")
    db.add(source)
    db.commit()
    return source


def _article(db, source, *, raison=None, hash_="h1", duplicate_of_id=None):
    article = Article(
        source_id=source.id,
        url=f"https://example.com/{hash_}",
        title="Une actu",
        published_at=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
        relevance_score=70,
        status=ArticleStatus.NEW,
        raw_hash=hash_,
        file_exclu_raison=raison,
        duplicate_of_id=duplicate_of_id,
    )
    db.add(article)
    db.commit()
    return article


def test_la_raison_d_exclusion_est_exposee_par_l_api(client, db_session):
    """Sans ce champ, la grille ne peut pas montrer qu'un article est hors file."""
    source = _source(db_session)
    _article(db_session, source, raison="saute depuis le dashboard")

    (vu,) = client.get("/articles").json()
    assert vu["file_exclu_raison"] == "saute depuis le dashboard"


def test_reintegrer_leve_l_exclusion(client, db_session):
    source = _source(db_session)
    article = _article(db_session, source, raison="gel impossible : image ecartee")

    reponse = client.post(f"/articles/{article.id}/reintegrer")
    assert reponse.status_code == 200
    assert reponse.json()["file_exclu_raison"] is None
    db_session.refresh(article)
    assert article.file_exclu_raison is None


def test_reintegrer_est_idempotent(client, db_session):
    """L'UI peut avoir une grille d'une seconde en retard : deux clics ne sont
    pas une erreur."""
    source = _source(db_session)
    article = _article(db_session, source, raison="saute depuis le dashboard")

    assert client.post(f"/articles/{article.id}/reintegrer").status_code == 200
    assert client.post(f"/articles/{article.id}/reintegrer").status_code == 200


def test_reintegrer_un_article_inconnu_rend_404(client):
    assert client.post("/articles/4242/reintegrer").status_code == 404


def test_une_exclusion_de_dedup_est_refusee_et_nomme_l_outil_qui_tient(client, db_session):
    """Effacer la raison seule ne tient pas : `link_duplicates` relie l'article
    au passage suivant (docs/reference/dedup-et-retention.md). Mentir sur ce point serait pire que refuser."""
    source = _source(db_session)
    representant = _article(db_session, source, hash_="rep")
    article = _article(
        db_session,
        source,
        hash_="dup",
        raison=f"{RAISON_DOUBLON_PREFIXE} (article {representant.id}, similarite 0.85)",
        duplicate_of_id=representant.id,
    )

    reponse = client.post(f"/articles/{article.id}/reintegrer")
    assert reponse.status_code == 409
    assert "marquer_doublon_manuel" in reponse.json()["detail"]
    db_session.refresh(article)
    assert article.file_exclu_raison is not None


def test_une_exclusion_de_gel_sur_un_doublon_se_leve_quand_meme(client, db_session):
    """Ce qui est refuse est la raison POSEE PAR LA DEDUP, pas le fait d'etre un
    doublon : une exclusion de gel se leve, le lien de doublon continue sa vie."""
    source = _source(db_session)
    representant = _article(db_session, source, hash_="rep2")
    article = _article(
        db_session,
        source,
        hash_="dup2",
        raison="gel impossible : le post n'a pas de visuel publiable",
        duplicate_of_id=representant.id,
    )

    assert client.post(f"/articles/{article.id}/reintegrer").status_code == 200
    db_session.refresh(article)
    assert article.file_exclu_raison is None
    assert article.duplicate_of_id == representant.id
