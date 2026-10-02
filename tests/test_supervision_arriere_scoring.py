"""Le voyant « composition » criait au loup, et un voyant qui ment est pire
que pas de voyant.

Mesure du 2026-10-02 : le bandeau affichait « 4 article(s) attendent une note
depuis plus de 2 h alors que chaque passage horaire note tout : scoring
interrompu » alors que le passage precedent venait de noter 57 articles sans
une seule erreur. Les quatre etaient d'anciens DOUBLONS -- un doublon n'est
jamais note, c'est l'economie que `link_duplicates` existe pour faire -- dont
le representant venait d'etre purge : `articles.duplicate_of_id` est
`ON DELETE SET NULL`, donc la purge les a rendus notables APRES le scoring du
meme passage. Leur `fetched_at` avait 24 h, leur attente reelle quelques
minutes, et le passage suivant les a notes.

L'autre moitie du meme sujet : un article REFLEXIF (defaut du meme jour, voir
`tests/test_cluster_reflexif.py`) est, lui, bloque pour de bon -- et le filtre
`duplicate_of_id IS NULL` du compteur le rendait invisible. Les deux cas ne
doivent pas disparaitre du meme geste.

`db_session.autoflush = False` : le reglage de PRODUCTION (`app/db.py`).
"""

import datetime as dt

from app.models import Article, Source, SourceType
from app.supervision import (
    ATTENTION,
    OK,
    articles_jamais_atteints_par_le_scoring,
    articles_leur_propre_doublon,
    voyant_composition,
)

UTC = dt.timezone.utc


def _maintenant():
    """Jamais une date figee : deux fixtures calees sur une constante ont pourri
    la suite ce mois-ci."""
    return dt.datetime.now(UTC)


def _source(db):
    source = Source(name="media", type=SourceType.RSS, url="https://media/feed")
    db.add(source)
    db.commit()
    return source


def _article(db, source, *, heures, note=None, duplicate_of_id=None, rang=0):
    article = Article(
        source_id=source.id,
        url=f"https://media/{rang}",
        title=f"Titre {rang}",
        raw_hash=f"h{rang}",
        fetched_at=_maintenant() - dt.timedelta(hours=heures),
        relevance_score=note,
        duplicate_of_id=duplicate_of_id,
    )
    db.add(article)
    db.commit()
    return article


def test_un_ancien_doublon_libere_par_la_purge_n_est_pas_un_scoring_en_panne(db_session):
    """LE faux positif mesure. L'article sans note est le PLUS ANCIEN : un
    article plus recent a bien recu sa note, donc le scoring a tourne depuis --
    et le seul ordre de `score_pending` (`fetched_at` croissant, sans limite)
    prouve qu'il n'a jamais eu l'occasion de voir celui-la, parce qu'il etait
    encore un doublon a ce moment-la.

    Preuve par mutation : retirer le filtre `fetched_at > repere` de
    `articles_jamais_atteints_par_le_scoring` fait repasser ce test au rouge.
    """
    db_session.autoflush = False
    source = _source(db_session)
    _article(db_session, source, heures=24, note=None, rang=1)  # libere a l'instant
    _article(db_session, source, heures=1, note=70, rang=2)  # note au dernier passage
    db_session.commit()

    assert articles_jamais_atteints_par_le_scoring(db_session, _maintenant()) == 0


def test_un_scoring_reellement_arrete_est_toujours_signale(db_session):
    """La contrepartie, sans laquelle le correctif serait un bandeau eteint :
    pendant une panne, les articles les plus RECENTS sont ceux qui n'ont pas de
    note -- leur `fetched_at` depasse le dernier deja note."""
    db_session.autoflush = False
    source = _source(db_session)
    _article(db_session, source, heures=10, note=70, rang=1)  # avant la panne
    _article(db_session, source, heures=5, note=None, rang=2)  # pendant
    _article(db_session, source, heures=3, note=None, rang=3)  # pendant
    db_session.commit()

    maintenant = _maintenant()
    assert articles_jamais_atteints_par_le_scoring(db_session, maintenant) == 2
    voyant = voyant_composition(db_session, maintenant)
    assert voyant.niveau == ATTENTION
    assert "2" in voyant.valeur


def test_sans_aucun_article_note_le_doute_ne_se_degrade_pas_en_vert(db_session):
    """Regle 1 du module : une absence de repere vaut « on ne sait pas », donc
    on compte tout. Sinon la toute premiere panne -- celle ou RIEN n'a jamais
    ete note -- serait justement celle qu'on ne verrait pas."""
    db_session.autoflush = False
    source = _source(db_session)
    _article(db_session, source, heures=5, note=None, rang=1)
    db_session.commit()

    assert articles_jamais_atteints_par_le_scoring(db_session, _maintenant()) == 1


def test_un_doublon_ordinaire_n_est_jamais_compte(db_session):
    """Un doublon encore lie n'attend pas une note : il n'en aura jamais, par
    construction, et c'est voulu."""
    db_session.autoflush = False
    source = _source(db_session)
    representant = _article(db_session, source, heures=6, note=70, rang=1)
    _article(db_session, source, heures=5, note=None, duplicate_of_id=representant.id, rang=2)
    db_session.commit()

    assert articles_jamais_atteints_par_le_scoring(db_session, _maintenant()) == 0


# -- l'autre moitie : le reflexif, qui doit RESTER visible -------------------


def test_un_article_son_propre_doublon_est_signale_et_nomme(db_session):
    """Il est bloque pour de bon, et le filtre `duplicate_of_id IS NULL` du
    compteur de retard ne peut pas le voir : il lui faut son propre compteur,
    son propre message et la commande de reparation."""
    db_session.autoflush = False
    source = _source(db_session)
    article = _article(db_session, source, heures=28, note=None, rang=1)
    article.duplicate_of_id = article.id
    db_session.commit()

    maintenant = _maintenant()
    assert articles_leur_propre_doublon(db_session) == 1
    # Il n'est PAS compte comme un retard de scoring : ce n'est pas le scoring
    # qui est en cause, et le dire serait un message faux.
    assert articles_jamais_atteints_par_le_scoring(db_session, maintenant) == 0

    voyant = voyant_composition(db_session, maintenant)
    assert voyant.niveau == ATTENTION
    assert "eux-mêmes comme doublon" in voyant.raison
    assert "reparer_doublons_reflexifs" in voyant.raison


def test_un_reflexif_reste_visible_en_detail_meme_derriere_une_panne_plus_grave(db_session):
    """Une corruption ne doit pas etre masquee par une panne passagere qui
    prend le titre du voyant."""
    db_session.autoflush = False
    source = _source(db_session)
    _article(db_session, source, heures=10, note=70, rang=1)
    _article(db_session, source, heures=3, note=None, rang=2)  # vrai arriere
    reflexif = _article(db_session, source, heures=28, note=None, rang=3)
    reflexif.duplicate_of_id = reflexif.id
    db_session.commit()

    voyant = voyant_composition(db_session, _maintenant())
    assert "scoring interrompu" in voyant.raison  # la panne prend le titre
    assert any("leur propre doublon" in detail for detail in voyant.details)


def test_une_base_saine_reste_au_vert(db_session):
    db_session.autoflush = False
    source = _source(db_session)
    _article(db_session, source, heures=3, note=70, rang=1)
    db_session.commit()

    voyant = voyant_composition(db_session, _maintenant())
    assert voyant.niveau in (OK, ATTENTION)
    assert articles_leur_propre_doublon(db_session) == 0
    assert articles_jamais_atteints_par_le_scoring(db_session, _maintenant()) == 0
