import datetime as dt

from app.schedule.queue import Candidat, Place, arbitrer, score

T0 = dt.datetime(2026, 9, 20, 8, 0, tzinfo=dt.timezone.utc)


def _places(nombre, verrous=None):
    verrous = verrous or {}
    return [
        Place(T0 + dt.timedelta(hours=12 * rang), verrous.get(rang), rang in verrous)
        for rang in range(nombre)
    ]


def _candidat(article_id, relevance, hotness=0, age_heures=0, compose=False):
    return Candidat(article_id, relevance, hotness, T0 - dt.timedelta(hours=age_heures), compose)


def _ordre(affectation):
    return [affectation[moment] for moment in sorted(affectation)]


def test_le_meilleur_prend_la_place_la_plus_proche():
    candidats = [_candidat(1, 60), _candidat(2, 90), _candidat(3, 75)]
    assert _ordre(arbitrer(_places(3), candidats, T0)) == [2, 3, 1]


def test_une_place_verrouillee_garde_son_article_qui_n_est_pas_place_ailleurs():
    candidats = [_candidat(9, 99), _candidat(1, 70), _candidat(2, 60)]
    assert _ordre(arbitrer(_places(3, verrous={0: 9}), candidats, T0)) == [9, 1, 2]


def test_les_places_en_trop_restent_vides():
    assert _ordre(arbitrer(_places(3), [_candidat(1, 80)], T0)) == [1, None, None]


def test_les_moins_bons_sortent_de_la_file():
    """« ou alors on la met jamais »"""
    candidats = [_candidat(1, 50), _candidat(2, 90), _candidat(3, 70)]
    assert _ordre(arbitrer(_places(2), candidats, T0)) == [2, 3]


def test_l_urgence_compte():
    candidats = [_candidat(1, 80, hotness=0), _candidat(2, 60, hotness=100)]
    assert _ordre(arbitrer(_places(2), candidats, T0)) == [2, 1]


def test_une_actu_de_deux_jours_cede_la_place():
    candidats = [_candidat(1, 80, age_heures=48), _candidat(2, 60)]
    assert _ordre(arbitrer(_places(2), candidats, T0)) == [2, 1]


def test_un_titulaire_deja_compose_resiste_a_un_peu_mieux():
    """La composition est payee : on ne la jette pas pour dix points."""
    candidats = [_candidat(1, 70, compose=True), _candidat(2, 80)]
    assert _ordre(arbitrer(_places(2), candidats, T0)) == [1, 2]


def test_une_grosse_actu_detrone_le_titulaire_qui_recule_d_un_cran():
    candidats = [_candidat(1, 70, compose=True), _candidat(2, 90)]
    assert _ordre(arbitrer(_places(2), candidats, T0)) == [2, 1]


def test_egalite_departagee_par_identifiant():
    assert _ordre(arbitrer(_places(2), [_candidat(5, 70), _candidat(3, 70)], T0)) == [3, 5]


def test_un_age_inconnu_compte_pour_un_jour():
    assert score(Candidat(1, 80, 0, None), T0) == 65.0


def test_un_article_en_double_n_occupe_qu_une_place():
    """Un article ne peut occuper qu'une seule place, meme s'il apparait deux fois."""
    candidats = [_candidat(1, 50), _candidat(1, 90), _candidat(2, 60)]
    assert _ordre(arbitrer(_places(3), candidats, T0)) == [1, 2, None]
