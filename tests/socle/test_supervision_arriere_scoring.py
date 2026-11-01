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
`tests/pipeline/test_cluster_reflexif.py`) est, lui, bloque pour de bon -- et le filtre
`duplicate_of_id IS NULL` du compteur le rendait invisible. Les deux cas ne
doivent pas disparaitre du meme geste.

`db_session.autoflush = False` : le reglage de PRODUCTION (`app/db.py`).
"""

import datetime as dt

import pytest

from app.models import Article, Source, SourceType
from app.pipeline.scoring import ArticleScore, Category, score_pending
from app.supervision import (
    ATTENTION,
    OK,
    PANNE,
    articles_jamais_atteints_par_le_scoring,
    articles_leur_propre_doublon,
    voyant_composition,
)

UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def _solde_connu():
    """Depuis le 2026-10-31, un solde jamais consigne vaut INCONNU. Ces tests parlent
    d'autre chose : le dernier appel a Claude a reussi."""
    from app.pipeline.credits_anthropic import consigner_succes

    consigner_succes(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))


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
    article plus recent a bien recu sa note, donc le scoring a tourne depuis.
    Il reste sous le repere, et n'est donc pas compte, quel que soit l'ordre de
    `score_pending` : le passage suivant le notera.

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


# -- `score_pending` note du plus recent au plus ancien ----------------------


def _note(_article=None):
    return ArticleScore(
        relevance=70, hotness=40, emergence=10, category=Category.ANNOUNCEMENT, imprint=None,
        summary="s", artists=[],
    )


def _au_fond(db, source, *, heures, rang):
    article = _article(db, source, heures=heures, rang=rang)
    article.summary = "Un resume assez long pour ne pas passer pour un post quasi vide."
    db.commit()
    return article


def test_apres_un_vrai_passage_un_scoring_qui_s_arrete_reste_signale(db_session):
    """Ce que le voyant affirme tient avec le nouvel ordre : les articles ingeres apres
    l'arret depassent le repere, et sont comptes."""
    db_session.autoflush = False
    source = _source(db_session)
    _au_fond(db_session, source, heures=10, rang=1)
    _au_fond(db_session, source, heures=9, rang=2)
    assert score_pending(db_session, scorer=_note, prefilter=None).scored == 2

    _au_fond(db_session, source, heures=5, rang=3)  # ingere apres l'arret
    _au_fond(db_session, source, heures=3, rang=4)

    assert articles_jamais_atteints_par_le_scoring(db_session, _maintenant()) == 2


def test_limite_connue_un_arriere_ancien_reste_invisible_tant_que_le_frais_est_note(db_session):
    """LA LIMITE, ecrite pour qu'on ne la decouvre pas : deux articles vieux de 5 et 8 h
    que le passage n'a pas atteints, sous un article frais qui a sa note. Le compteur dit
    0. Il ne peut pas les distinguer d'un doublon libere par la purge, ni d'un article
    remis a noter : rien dans le schema ne date « devenu notable ». Le passage horaire
    sans limite les reprend ; si ce test rougit parce que le compteur les voit, relire
    le faux positif du 2026-10-02 avant de s'en feliciter."""
    db_session.autoflush = False
    source = _source(db_session)
    _au_fond(db_session, source, heures=8, rang=1)
    _au_fond(db_session, source, heures=5, rang=2)
    _au_fond(db_session, source, heures=1, rang=3)

    assert score_pending(db_session, scorer=_note, prefilter=None, limit=1).scored == 1

    assert articles_jamais_atteints_par_le_scoring(db_session, _maintenant()) == 0
    assert score_pending(db_session, scorer=_note, prefilter=None).scored == 2


def test_un_refus_de_credits_en_cours_de_passage_ne_passe_pas_par_cette_limite(db_session):
    """Le cas le plus cher de la limite : le frais est note, puis les credits tombent.
    Le compteur ne voit pas l'arriere, mais le voyant dit la panne par le fichier d'etat."""
    db_session.autoflush = False
    source = _source(db_session)
    _au_fond(db_session, source, heures=8, rang=1)
    _au_fond(db_session, source, heures=5, rang=2)
    _au_fond(db_session, source, heures=1, rang=3)
    appels = []

    def scorer(_article):
        appels.append(1)
        if len(appels) == 2:
            raise RuntimeError("Your credit balance is too low to access the Anthropic API.")
        return _note()

    score_pending(db_session, scorer=scorer, prefilter=None)

    maintenant = _maintenant()
    assert articles_jamais_atteints_par_le_scoring(db_session, maintenant) == 0
    voyant = voyant_composition(db_session, maintenant)
    assert voyant.niveau == PANNE
    assert voyant.valeur == "crédits épuisés"


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
