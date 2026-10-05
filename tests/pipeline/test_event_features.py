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
    NOUVEAUTE_SEUIL_REPRISE,
    acceleration,
    corroboration,
    demi_vie_pour,
    etalement_heures,
    freshness,
    freshness_depuis,
    nouveaute,
    velocity,
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


# --- Phase 2C, Tasks 1 et 2 -------------------------------------------------

UTC = dt.timezone.utc


def _t(heures: float) -> dt.datetime:
    """Un instant a `heures` heures d'une origine fixe -- aucune horloge
    reelle dans ces tests, c'est la propriete du module."""
    return dt.datetime(2026, 9, 25, 0, 0, tzinfo=UTC) + dt.timedelta(hours=heures)


class TestEtalement:
    def test_none_sous_deux_dates(self):
        assert etalement_heures([]) is None
        assert etalement_heures([_t(0)]) is None

    def test_les_dates_absentes_ne_comptent_pas(self):
        # Une observation sans date de publication est ECARTEE, jamais datee
        # de son ingestion : deux dates connues sur trois observations.
        assert etalement_heures([_t(0), None, _t(5)]) == pytest.approx(5.0)

    def test_une_seule_date_connue_ne_fait_pas_un_etalement(self):
        assert etalement_heures([_t(0), None, None]) is None

    def test_l_ordre_des_dates_est_indifferent(self):
        assert etalement_heures([_t(9), _t(0), _t(4)]) == pytest.approx(9.0)


class TestVelocity:
    def test_none_sous_deux_preuves_datees(self):
        assert velocity([_t(0)]) is None
        assert velocity([]) is None

    def test_none_quand_l_etalement_est_nul(self):
        # Deux articles publies a la meme seconde : le taux serait infini,
        # pas enorme.
        assert velocity([_t(0), _t(0)]) is None

    def test_none_sous_le_plancher_d_etalement(self):
        # Mesure du 2026-09-30 : sous une heure, le denominateur mesure la
        # granularite des horodatages, pas la propagation (7 200/h en tete).
        assert velocity([_t(0), _t(0.5)]) is None
        assert velocity([_t(0), _t(0), _t(0.01), _t(0.02)]) is None

    def test_jamais_zero_a_la_place_de_none(self):
        # La distinction qui rend un classement honnete : `None` veut dire
        # « pas assez de matiere », `0.0` voudrait dire « mesure a zero ».
        for absent in (velocity([]), velocity([_t(0)]), velocity([_t(0), _t(0)])):
            assert absent is None
            assert absent != 0.0

    def test_le_taux_compte_les_preuves_arrivees_apres_la_premiere(self):
        # 3 preuves sur 2 h : deux preuves sont arrivees apres l'ouverture de
        # la fenetre, soit 1,0/h -- et non 1,5/h.
        assert velocity([_t(0), _t(1), _t(2)]) == pytest.approx(1.0)

    def test_trois_sources_en_deux_heures_passent_devant_une_paire_en_une(self):
        # La raison du numerateur `n - 1`, verifiee sur donnees reelles :
        # avec `n`, les paires gagnent mecaniquement.
        trio = velocity([_t(0), _t(1.2), _t(2.43)])
        paire = velocity([_t(0), _t(1.24)])
        assert trio > paire

    def test_une_actu_froide_note_bien_moins_qu_une_actu_qui_explose(self):
        # Le but editorial du blueprint : separer « importante mais froide »
        # de « qui explose maintenant ».
        froide = velocity([_t(0), _t(40), _t(80)])
        chaude = velocity([_t(0), _t(1), _t(2), _t(3)])
        assert chaude > 10 * froide


class TestAcceleration:
    def test_none_sous_trois_preuves_datees(self):
        # 825 evenements sur 901 : le dire vaut mieux que rendre 0.
        assert acceleration([_t(0), _t(10)]) is None
        assert acceleration([_t(0), None, _t(10)]) is None

    def test_none_sous_le_plancher_d_etalement(self):
        assert acceleration([_t(0), _t(0.1), _t(0.2)]) is None

    def test_positive_quand_la_seconde_moitie_est_plus_dense(self):
        # 1 preuve puis 3 : l'actu monte.
        assert acceleration([_t(0), _t(20), _t(22), _t(24)]) == pytest.approx(0.5)

    def test_negative_quand_la_rafale_etait_au_debut(self):
        # Le profil « a explose puis s'eteint » (Olivia Rodrigo, -0,64 en
        # production).
        assert acceleration([_t(0), _t(1), _t(2), _t(24)]) == pytest.approx(-0.5)

    def test_bornee_dans_moins_un_plus_un(self):
        for dates in ([_t(0), _t(1), _t(2), _t(30)], [_t(0), _t(29), _t(30)]):
            assert -1.0 <= acceleration(dates) <= 1.0

    def test_un_nombre_impair_de_preuves_penche_vers_le_haut(self):
        # Propriete a connaitre avant de lire un signe : la preuve exactement
        # au milieu compte pour la SECONDE moitie, donc trois preuves
        # equidistantes valent +0,33 et non 0. Le biais est de +1/n au pire,
        # il s'efface quand n grandit, et il est FIXE -- deux evenements de
        # meme effectif restent comparables.
        assert acceleration([_t(0), _t(12), _t(24)]) == pytest.approx(1 / 3)

    def test_une_repartition_reguliere_est_a_plat(self):
        # Quatre preuves equidistantes : deux de chaque cote, acceleration 0 --
        # et ce zero-la est une VRAIE mesure, pas une absence de matiere.
        assert acceleration([_t(0), _t(8), _t(16), _t(24)]) == pytest.approx(0.0)


class TestNouveaute:
    def test_reprise_avereee_vaut_zero(self):
        assert nouveaute(similarite_max=0.9, profondeur_archive_heures=None) == 0.0

    def test_une_reprise_trouvee_vaut_meme_sans_recul(self):
        # La dissymetrie : une archive courte ne peut pas INVENTER une reprise,
        # donc un fait positif n'a pas besoin de profondeur.
        assert nouveaute(similarite_max=1.0, profondeur_archive_heures=1.0) == 0.0

    def test_absence_de_reprise_sans_recul_vaut_none(self):
        # Elle ne peut produire qu'une seule erreur : declarer neuf ce dont
        # elle n'a pas vu l'original.
        assert nouveaute(similarite_max=0.0, profondeur_archive_heures=48.0) is None

    def test_absence_de_reprise_avec_recul_vaut_cent(self):
        assert nouveaute(similarite_max=0.0, profondeur_archive_heures=200.0) == 100.0

    def test_profondeur_inconnue_vaut_none(self):
        assert nouveaute(similarite_max=0.1, profondeur_archive_heures=None) is None

    def test_similarite_inconnue_n_est_pas_une_nouveaute_maximale(self):
        """Tour de correction 2 : ce test attendait 100.0, et c'etait le
        defaut le plus grave de la phase -- la valeur NON CALCULABLE devenait
        la valeur MAXIMALE, donc la tete de n'importe quel classement. Un
        titre sans jeton significatif n'a ete compare a RIEN ; `None` est la
        seule reponse honnete. Mesure : 5 evenements de production
        recevaient 100.0 par ce chemin (« AIR FT. », « IVE EP », « . »)."""
        assert nouveaute(similarite_max=None, profondeur_archive_heures=200.0) is None
        assert nouveaute(similarite_max=None, profondeur_archive_heures=None) is None

    def test_interpolation_dans_la_bande_douteuse(self):
        milieu = nouveaute(similarite_max=0.725, profondeur_archive_heures=200.0)
        assert milieu == pytest.approx(50.0)
        assert 0 < nouveaute(similarite_max=0.79, profondeur_archive_heures=200.0) < 20

    def test_le_seuil_de_reprise_est_celui_de_la_dedup_d_archive(self):
        # 0,8 = `cluster_archive_seuil`, deja mesure sur des paires reelles :
        # une seule mesure pour une seule question.
        from app.config import settings

        assert NOUVEAUTE_SEUIL_REPRISE == settings.cluster_archive_seuil


class TestCalculerFeaturesAvec2C:
    def test_sans_entrees_2c_les_trois_features_sont_none(self):
        f = calculer_features(
            first_seen_at=_t(0),
            maintenant=_t(1),
            source_count=1,
            platform_count=1,
            independent_source_count=1,
        )
        assert f.velocity is None
        assert f.acceleration is None
        assert f.nouveaute is None

    def test_les_entrees_2c_remontent_dans_le_paquet(self):
        f = calculer_features(
            first_seen_at=_t(0),
            maintenant=_t(1),
            source_count=3,
            platform_count=2,
            independent_source_count=3,
            dates_publication=[_t(0), _t(1), _t(2), _t(3)],
            similarite_max=0.0,
            profondeur_archive_heures=200.0,
        )
        assert f.velocity == pytest.approx(1.0)
        assert f.acceleration == pytest.approx(0.0)
        assert f.nouveaute == 100.0
