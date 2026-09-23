"""Tests du module PUR `app/pipeline/event_features.py` (Phase 2A, Task 3).

Aucun de ces tests ne touche une base ni le reseau -- c'est la garantie que le
module doit tenir. Les valeurs de controle viennent du blueprint
(`docs/Kinda France _ systeme de scoring optimise et blueprint
d'implementation.pdf`), cite dans le plan
`docs/superpowers/plans/2026-09-23-phase-2a-evenements.md`.
"""

import datetime as dt

import pytest

from app.pipeline.event_features import (
    DEMI_VIE_DEFAUT_HEURES,
    EventFeatures,
    calculer_features,
    corroboration,
    demi_vie_pour,
    freshness,
    freshness_depuis,
)


class TestFreshness:
    def test_controles_du_blueprint_avec_demi_vie_8h(self):
        # h = 8 : t=1 -> ~92, t=8 -> 50, t=24 -> 12.5 (valeurs du blueprint)
        assert freshness(1, demi_vie_heures=8) == pytest.approx(92, abs=1)
        assert freshness(8, demi_vie_heures=8) == pytest.approx(50, abs=0.01)
        assert freshness(24, demi_vie_heures=8) == pytest.approx(12.5, abs=0.01)

    def test_a_t_zero_la_fraicheur_est_maximale(self):
        assert freshness(0, demi_vie_heures=8) == pytest.approx(100)

    def test_decroit_strictement_avec_le_temps(self):
        h = 8
        valeurs = [freshness(t, demi_vie_heures=h) for t in (0, 1, 4, 8, 24, 72)]
        assert valeurs == sorted(valeurs, reverse=True)
        assert len(set(valeurs)) == len(valeurs)

    def test_temps_negatif_est_ramene_a_zero(self):
        # Horloge legerement en avance sur first_seen_at : pas de fraicheur > 100.
        assert freshness(-5, demi_vie_heures=8) == pytest.approx(100)

    def test_demi_vie_nulle_ou_negative_leve(self):
        with pytest.raises(ValueError):
            freshness(1, demi_vie_heures=0)
        with pytest.raises(ValueError):
            freshness(1, demi_vie_heures=-1)

    def test_demi_vie_par_defaut_utilisee_si_omise(self):
        assert freshness(DEMI_VIE_DEFAUT_HEURES) == pytest.approx(50, abs=0.01)


class TestFreshnessDepuis:
    def test_calcule_depuis_deux_datetimes_aware(self):
        premiere_fois = dt.datetime(2026, 9, 24, 0, 0, tzinfo=dt.timezone.utc)
        maintenant = dt.datetime(2026, 9, 24, 8, 0, tzinfo=dt.timezone.utc)
        assert freshness_depuis(premiere_fois, maintenant, demi_vie_heures=8) == pytest.approx(
            50, abs=0.01
        )

    def test_maintenant_avant_first_seen_at_ne_leve_pas(self):
        premiere_fois = dt.datetime(2026, 9, 24, 8, 0, tzinfo=dt.timezone.utc)
        maintenant = dt.datetime(2026, 9, 24, 0, 0, tzinfo=dt.timezone.utc)
        assert freshness_depuis(premiere_fois, maintenant, demi_vie_heures=8) == pytest.approx(100)

    def test_naive_contre_aware_leve(self):
        # Une confusion aware/naive ne doit jamais etre avalee en silence.
        premiere_fois = dt.datetime(2026, 9, 24, 0, 0)
        maintenant = dt.datetime(2026, 9, 24, 8, 0, tzinfo=dt.timezone.utc)
        with pytest.raises(TypeError):
            freshness_depuis(premiere_fois, maintenant, demi_vie_heures=8)


class TestCorroboration:
    @pytest.mark.parametrize(
        ("n", "attendu"),
        [(1, 28), (2, 49), (3, 63), (5, 81), (8, 93)],
    )
    def test_controles_du_blueprint(self, n, attendu):
        assert corroboration(n) == pytest.approx(attendu, abs=1)

    def test_zero_source_rend_zero(self):
        assert corroboration(0) == pytest.approx(0)

    def test_n_negatif_est_ramene_a_zero(self):
        assert corroboration(-3) == pytest.approx(0)

    def test_croit_avec_n_et_tend_vers_100(self):
        valeurs = [corroboration(n) for n in (0, 1, 2, 3, 5, 8, 50)]
        assert valeurs == sorted(valeurs)
        assert valeurs[-1] < 100
        assert valeurs[-1] == pytest.approx(100, abs=0.5)


class TestDemiViePour:
    def test_type_inconnu_rend_le_defaut(self):
        assert demi_vie_pour("un_type_qui_n_existe_pas") == DEMI_VIE_DEFAUT_HEURES

    def test_type_none_rend_le_defaut(self):
        assert demi_vie_pour(None) == DEMI_VIE_DEFAUT_HEURES

    def test_type_connu_rend_sa_demi_vie(self):
        assert demi_vie_pour("drama") == 3.0
        assert demi_vie_pour("interview") == 72.0

    def test_defaut_personnalise(self):
        assert demi_vie_pour(None, defaut=12.0) == 12.0


class TestCalculerFeatures:
    def test_assemble_toutes_les_features_sans_toucher_a_la_base(self):
        premiere_fois = dt.datetime(2026, 9, 24, 0, 0, tzinfo=dt.timezone.utc)
        maintenant = dt.datetime(2026, 9, 24, 8, 0, tzinfo=dt.timezone.utc)
        features = calculer_features(
            first_seen_at=premiere_fois,
            maintenant=maintenant,
            source_count=3,
            platform_count=2,
            independent_source_count=2,
            event_type=None,
            demi_vie_heures=8,
        )
        assert isinstance(features, EventFeatures)
        assert features.freshness == pytest.approx(50, abs=0.01)
        assert features.corroboration == pytest.approx(49, abs=1)
        assert features.source_count == 3
        assert features.platform_count == 2
        assert features.independent_source_count == 2

    def test_source_count_et_platform_count_ne_sont_jamais_recalcules(self):
        # Des valeurs volontairement incoherentes (independent > source, ce
        # qui n'arriverait jamais en pratique) doivent traverser TELLES
        # QUELLES : ce module lit, il ne corrige ni ne revalide Task 2.
        premiere_fois = dt.datetime(2026, 9, 24, 0, 0, tzinfo=dt.timezone.utc)
        features = calculer_features(
            first_seen_at=premiere_fois,
            maintenant=premiere_fois,
            source_count=1,
            platform_count=5,
            independent_source_count=9,
        )
        assert features.source_count == 1
        assert features.platform_count == 5
        assert features.independent_source_count == 9

    def test_demi_vie_resolue_depuis_event_type_quand_non_fournie_explicitement(self):
        premiere_fois = dt.datetime(2026, 9, 24, 0, 0, tzinfo=dt.timezone.utc)
        maintenant = premiere_fois + dt.timedelta(hours=3)
        features = calculer_features(
            first_seen_at=premiere_fois,
            maintenant=maintenant,
            source_count=1,
            platform_count=1,
            independent_source_count=1,
            event_type="drama",
        )
        # demi-vie "drama" = 3h -> a t=3h, fraicheur = 50
        assert features.freshness == pytest.approx(50, abs=0.01)

    def test_event_type_null_utilise_le_defaut(self):
        premiere_fois = dt.datetime(2026, 9, 24, 0, 0, tzinfo=dt.timezone.utc)
        maintenant = premiere_fois + dt.timedelta(hours=DEMI_VIE_DEFAUT_HEURES)
        features = calculer_features(
            first_seen_at=premiere_fois,
            maintenant=maintenant,
            source_count=1,
            platform_count=1,
            independent_source_count=1,
        )
        assert features.freshness == pytest.approx(50, abs=0.01)
