"""La mise a l'ecart temporaire (`app/schedule/pause_visuels.py`), au niveau
module : robustesse (jamais d'exception) et fusion a l'ecriture.
"""

import datetime as dt
import json

from app.schedule.pause_visuels import (
    Etat,
    _ecrire,
    _lire,
    articles_en_pause,
    marquer_saute,
    reinitialiser_report,
    signaler_report,
)

UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


def test_marquer_saute_et_articles_en_pause(tmp_path):
    chemin = tmp_path / "visuel_saute.json"
    fin = marquer_saute(1, MAINTENANT, duree=dt.timedelta(minutes=30), chemin=chemin)

    assert 1 in articles_en_pause(MAINTENANT + dt.timedelta(minutes=10), chemin)
    assert 1 in articles_en_pause(fin - dt.timedelta(seconds=1), chemin)
    assert 1 not in articles_en_pause(fin, chemin)


def test_les_pauses_echues_sont_purgees_a_la_prochaine_ecriture(tmp_path):
    chemin = tmp_path / "visuel_saute.json"
    marquer_saute(1, MAINTENANT, duree=dt.timedelta(minutes=1), chemin=chemin)
    marquer_saute(2, MAINTENANT + dt.timedelta(minutes=5), duree=dt.timedelta(minutes=30), chemin=chemin)

    donnees = json.loads(chemin.read_text())
    assert set(donnees["pauses"]) == {"2"}


# --- Point 4 (tour de correction 2/5) : une horloge naive ne fait jamais tomber le module


def test_2_5_un_horodatage_naif_sur_le_disque_ne_leve_jamais(tmp_path):
    """Un fichier corrompu ou pose par un bug amont peut porter un
    horodatage NAIF (sans fuseau). Compare a un `maintenant` conscient du
    fuseau, ca leve normalement un `TypeError` -- inatteignable aujourd'hui,
    mais ce module est appele depuis `store._articles_publiables`, donc
    l'arbitrage, `candidats_eligibles` ET `offre_reels` tomberaient tous les
    trois. `articles_en_pause` doit rendre un resultat, jamais lever."""
    chemin = tmp_path / "visuel_saute.json"
    chemin.write_text(json.dumps({"pauses": {"1": "2026-09-22T12:00:00"}, "reports": {}}))

    # Ne leve pas : l'entree illisible-en-comparaison est traitee comme
    # perimee (l'article redevient eligible) plutot que de faire tomber
    # l'appelant.
    assert articles_en_pause(MAINTENANT, chemin) == set()


def test_2_5_un_horodatage_naif_n_empeche_pas_de_lire_les_autres_entrees(tmp_path):
    """Une seule entree illisible ne doit pas eclipser les autres pauses,
    valides celles-la."""
    chemin = tmp_path / "visuel_saute.json"
    chemin.write_text(json.dumps({
        "pauses": {
            "1": "2026-09-22T12:00:00",  # naif : illisible en comparaison
            "2": (MAINTENANT + dt.timedelta(hours=1)).isoformat(),  # valide
        },
        "reports": {},
    }))

    assert articles_en_pause(MAINTENANT, chemin) == {2}


# --- Point 5 (tour de correction 2/5) : fusion a l'ecriture, jamais un ecrasement


def test_2_5_marquer_saute_ne_perd_pas_une_pause_posee_entre_temps(tmp_path):
    """Reproduit le scenario mesure par la revue : un autre agent pose la
    pause de l'article 2 (simule ici en ecrivant directement sur le disque,
    exactement comme les tests de `cluster_arbitrage.fusionner_et_ecrire`,
    Tache 7) ; notre propre appel (pour l'article 1) ne doit PAS l'effacer en
    ecrivant a partir d'un instantane pris plus tot."""
    chemin = tmp_path / "visuel_saute.json"
    # « B » pose sa pause en premier, directement sur le disque.
    fin_2 = MAINTENANT + dt.timedelta(hours=1)
    _ecrire(chemin, Etat({2: fin_2}, {}))

    # « A » (nous) pose la notre pour un AUTRE article.
    marquer_saute(1, MAINTENANT, duree=dt.timedelta(minutes=30), chemin=chemin)

    en_pause = articles_en_pause(MAINTENANT, chemin)
    assert 1 in en_pause
    assert 2 in en_pause, "la pause posee par l'autre agent ne doit pas disparaitre"


def test_2_5_signaler_report_ne_perd_pas_une_pause_posee_entre_temps(tmp_path):
    chemin = tmp_path / "visuel_saute.json"
    fin_2 = MAINTENANT + dt.timedelta(hours=1)
    _ecrire(chemin, Etat({2: fin_2}, {}))

    signaler_report(1, MAINTENANT, seuil=10, chemin=chemin)

    assert 2 in articles_en_pause(MAINTENANT, chemin)
    assert _lire(chemin).reports.get(1) == 1


def test_2_5_reinitialiser_report_ne_perd_pas_une_pause_posee_entre_temps(tmp_path):
    chemin = tmp_path / "visuel_saute.json"
    signaler_report(1, MAINTENANT, seuil=10, chemin=chemin)
    fin_2 = MAINTENANT + dt.timedelta(hours=1)
    _ecrire(chemin, Etat({2: fin_2}, _lire(chemin).reports))

    reinitialiser_report(1, MAINTENANT, chemin=chemin)

    assert 2 in articles_en_pause(MAINTENANT, chemin)
    assert 1 not in _lire(chemin).reports


# --- Le compteur de reports consecutifs


def test_signaler_report_bascule_en_pause_au_seuil(tmp_path):
    chemin = tmp_path / "visuel_saute.json"
    for _ in range(2):
        assert signaler_report(1, MAINTENANT, seuil=3, chemin=chemin) is False
    assert signaler_report(1, MAINTENANT, seuil=3, chemin=chemin) is True
    assert 1 in articles_en_pause(MAINTENANT, chemin)


def test_reinitialiser_report_casse_la_suite(tmp_path):
    chemin = tmp_path / "visuel_saute.json"
    signaler_report(1, MAINTENANT, seuil=3, chemin=chemin)
    signaler_report(1, MAINTENANT, seuil=3, chemin=chemin)
    reinitialiser_report(1, MAINTENANT, chemin=chemin)

    # Repart de zero : deux reports de plus ne suffisent pas a atteindre le seuil de 3.
    assert signaler_report(1, MAINTENANT, seuil=3, chemin=chemin) is False
    assert signaler_report(1, MAINTENANT, seuil=3, chemin=chemin) is False
    assert 1 not in articles_en_pause(MAINTENANT, chemin)
