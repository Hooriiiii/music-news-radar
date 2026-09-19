"""La pause de quota Graph, partagee par la veille et le passage horaire.

Elle existe a cause d'une panne mesuree le 2026-09-19 : un appel refuse pour
quota consomme quand meme du quota, si bien qu'insister entretenait le
depassement (751 refus dans `pipeline.log`, jusqu'a 116 sources comptees « en
erreur » par run). Ces tests tiennent ce que le fichier d'etat doit garantir :
une pause qui survit au processus, deux agents qui ne se marchent pas dessus,
une notification par pause, et une PUBLICATION qui n'est jamais suspendue.

Aucun appel reseau : rien ici ne parle a Meta.
"""

import datetime as dt
import json

from app.config import settings
from app.pipeline.quota_graph import chemin_etat, declarer_quota, pause_en_cours

T0 = dt.datetime(2026, 9, 19, 11, 0, tzinfo=dt.timezone.utc)


def _muet(*_args):
    return True


def test_une_pause_declaree_est_relue_par_l_autre_agent():
    declarer_quota(T0, "code 4", "ingestion horaire", notify=_muet)

    pause = pause_en_cours(T0 + dt.timedelta(minutes=5))

    assert pause is not None
    assert pause.declaree_par == "ingestion horaire"
    assert pause.jusqu_a == T0 + dt.timedelta(minutes=settings.veille_pause_quota_minutes)


def test_une_pause_echue_ne_retient_plus_rien():
    declarer_quota(T0, "code 4", "veille rapide", notify=_muet)

    fin = T0 + dt.timedelta(minutes=settings.veille_pause_quota_minutes)
    assert pause_en_cours(fin) is None
    # Et elle n'est pas EFFACEE : reconnaitre une pause echue a sa date evite
    # une ecriture en lecture, donc la course ou un agent effacerait la pause
    # que l'autre vient de poser.
    assert chemin_etat().exists()


def test_deux_pauses_qui_se_croisent_gardent_la_plus_tardive():
    """Le quota est toujours epuise : une seconde pause ne doit jamais
    RACCOURCIR celle qui court."""
    declarer_quota(T0, "code 4", "veille rapide", notify=_muet)

    declarer_quota(T0 - dt.timedelta(minutes=20), "code 4", "ingestion horaire", notify=_muet)

    pause = pause_en_cours(T0)
    assert pause.jusqu_a == T0 + dt.timedelta(minutes=settings.veille_pause_quota_minutes)


def test_une_seule_notification_par_pause():
    notifications = []
    declarer_quota(T0, "code 4", "ingestion horaire", notify=lambda *a: notifications.append(a))
    declarer_quota(
        T0 + dt.timedelta(minutes=5), "code 4", "veille rapide",
        notify=lambda *a: notifications.append(a),
    )

    assert len(notifications) == 1
    # La pause suivante, elle, se signale de nouveau : une panne qui revient
    # apres coup est une information neuve.
    declarer_quota(
        T0 + dt.timedelta(hours=2), "code 4", "ingestion horaire",
        notify=lambda *a: notifications.append(a),
    )
    assert len(notifications) == 2


def test_la_notification_ne_porte_jamais_le_jeton():
    notifications = []
    declarer_quota(T0, "code 4", "ingestion horaire", notify=lambda *a: notifications.append(a))

    assert "access_token" not in notifications[0][1]


def test_un_etat_illisible_vaut_aucune_pause():
    """On prefere un appel de trop a une ingestion eteinte pour de bon."""
    chemin = chemin_etat()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("{ pas du json")

    assert pause_en_cours(T0) is None


def test_l_ecriture_est_atomique_et_propre_au_processus():
    """Deux agents ecrivent ce fichier, l'un toutes les heures, l'autre toutes
    les cinq minutes : un `.tmp` commun serait ecrit a deux mains."""
    declarer_quota(T0, "code 4", "veille rapide", notify=_muet)

    dossier = chemin_etat().parent
    assert json.loads(chemin_etat().read_text())["declaree_par"] == "veille rapide"
    assert [f.name for f in dossier.iterdir() if f.name.startswith("quota_graph.json.")] == []


def test_la_publication_n_est_jamais_suspendue_par_cette_pause():
    """Rater une publication programmee coute plus cher que de sauter une heure
    d'ingestion : aucun module de publication ne consulte cette pause.

    Verifie sur les IMPORTS et non sur le comportement : c'est la seule facon
    qu'un futur `from ... import pause_en_cours` pose dans le runner de
    publication fasse echouer un test plutot que de passer inapercu.
    """
    import ast
    import pathlib

    import app

    racine = pathlib.Path(app.__file__).parent
    for chemin in list((racine / "publish").rglob("*.py")) + list(
        (racine / "schedule").rglob("*.py")
    ):
        arbre = ast.parse(chemin.read_text())
        modules = [
            n.module
            for n in ast.walk(arbre)
            if isinstance(n, ast.ImportFrom) and n.module
        ]
        assert not any("quota_graph" in m for m in modules), chemin
