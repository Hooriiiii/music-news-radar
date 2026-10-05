"""Le classement des creneaux manques par cause, sur des logs fabriques.

Toutes les dates sont RELATIVES a maintenant : deux tests de ce depot ont
pourri ce mois-ci sur une date figee.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.models import Creneau, EtatCreneau, FormatCreneau
from scripts.diagnostic.diagnostiquer_creneaux import (
    ABSENCE,
    INCONNUE_GELE,
    SANS_POST,
    TUNNEL,
    YTDLP,
    Journaux,
    classer,
    examiner,
)

FUSEAU = ZoneInfo(settings.publication_fuseau)


def _journaux(tmp_path, publication: str = "", pipeline: str = "") -> Journaux:
    (tmp_path / "publication.log").write_text(publication)
    (tmp_path / "pipeline.log").write_text(pipeline)
    return Journaux(tmp_path, aujourdhui=dt.date.today())


def _entete(moment: dt.datetime) -> str:
    # Le log est horodate en UTC, alors que les heures de creneaux y sont
    # affichees en heure de Paris : le piege que le script desamorce.
    return f"[{moment:%d/%m %H:%M}] publication (reel) : rien\n"


@pytest.fixture()
def heure():
    # Une heure ronde dans le passe proche, hors de toute fenetre de rattrapage.
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)).replace(microsecond=0)


def _creneau(heure, **champs):
    defauts = {
        "id": 1,
        "publier_a": heure,
        "format": FormatCreneau.CARROUSEL,
        "etat": EtatCreneau.MANQUE,
        "post_id": 7,
        "gele_le": heure,
        "erreur": None,
    }
    return Creneau(**{**defauts, **champs})


def test_sans_aucun_passage_de_l_agent_c_est_une_absence_de_la_machine(tmp_path, heure):
    journaux = _journaux(tmp_path)
    cause, passages, traces = classer(_creneau(heure), journaux, FUSEAU)
    assert (cause, passages, traces) == (ABSENCE, 0, 0)


def test_un_passage_de_pipeline_sans_passage_de_publication_reste_une_absence(tmp_path, heure):
    """Nuance a garder VISIBLE : la machine etait eveillee, mais l'agent de
    publication n'est pas passe. C'est le compteur `traces` qui le dit, pas une
    requalification en panne -- on ne devine pas pourquoi le passage a saute."""
    journaux = _journaux(tmp_path, pipeline=_entete(heure + dt.timedelta(minutes=5)))
    cause, passages, traces = classer(_creneau(heure), journaux, FUSEAU)
    assert (cause, passages, traces) == (ABSENCE, 0, 1)


def test_un_report_de_tunnel_nomme_le_creneau_dans_le_log(tmp_path, heure):
    """La seule facon de rattacher un echec de tunnel a un creneau apres coup :
    le log nomme le creneau en heure LOCALE, la base le garde en UTC."""
    locale = heure.astimezone(FUSEAU)
    log = (
        _entete(heure + dt.timedelta(minutes=20))
        + f"  [ERREUR] publication de {locale:%d/%m %H:%M} reportee : aucun tunnel joignable apres 3 essai(s)\n"
    )
    journaux = _journaux(tmp_path, publication=log)
    cause, passages, _ = classer(_creneau(heure), journaux, FUSEAU)
    assert (cause, passages) == (TUNNEL, 1)


def test_une_erreur_d_hebergement_en_base_suffit_sans_le_log(tmp_path, heure):
    """Depuis que `_raison_manque` conserve la cause, la base seule repond."""
    journaux = _journaux(tmp_path, publication=_entete(heure + dt.timedelta(minutes=20)))
    creneau = _creneau(heure, erreur="hebergement indisponible : aucun tunnel joignable — creneau manque : gele")
    assert classer(creneau, journaux, FUSEAU)[0] == TUNNEL


def test_une_panne_yt_dlp_est_distinguee(tmp_path, heure):
    journaux = _journaux(tmp_path, publication=_entete(heure))
    creneau = _creneau(heure, format=FormatCreneau.REEL, gele_le=None,
                       erreur="gel differe : yt-dlp en pause jusqu'a 21:21 UTC")
    assert classer(creneau, journaux, FUSEAU)[0] == YTDLP


def test_un_gele_que_l_agent_a_vu_sans_cause_connue_est_signale_comme_tel(tmp_path, heure):
    journaux = _journaux(tmp_path, publication=_entete(heure + dt.timedelta(minutes=10)))
    assert classer(_creneau(heure), journaux, FUSEAU)[0] == INCONNUE_GELE


def test_un_creneau_sans_post_est_distingue_d_un_creneau_gele(tmp_path, heure):
    journaux = _journaux(tmp_path, publication=_entete(heure + dt.timedelta(minutes=10)))
    creneau = _creneau(heure, post_id=None, gele_le=None)
    assert classer(creneau, journaux, FUSEAU)[0] == SANS_POST


def test_un_passage_hors_de_la_fenetre_de_rattrapage_ne_compte_pas(tmp_path, heure):
    """La fenetre va de la reclamation anticipee a la limite de retard : un
    passage plus tard n'aurait de toute facon rien pu publier."""
    trop_tard = heure + dt.timedelta(minutes=settings.publication_retard_max_minutes + 30)
    journaux = _journaux(tmp_path, publication=_entete(trop_tard))
    assert classer(_creneau(heure), journaux, FUSEAU)[0] == ABSENCE


def test_examiner_ne_touche_jamais_la_base(db_session, tmp_path, heure, monkeypatch):
    """Lecture seule : c'est la propriete qui rend le script lancable en
    production sans precaution."""
    db_session.autoflush = False
    monkeypatch.setattr("scripts.diagnostic.diagnostiquer_creneaux.DOSSIER_LOGS", tmp_path)
    (tmp_path / "publication.log").write_text("")
    db_session.add(_creneau(heure, id=None, erreur="hebergement indisponible : rien"))
    db_session.add(_creneau(heure + dt.timedelta(hours=1), id=None, etat=EtatCreneau.PUBLIE, erreur=None))
    db_session.commit()

    resultat = examiner(db_session, jours=None)

    causes = [ligne["cause"] for ligne in resultat["lignes"]]
    assert causes.count(TUNNEL) == 1 and causes.count(None) == 1
    # Un meme post sur deux creneaux : le cas qui a consomme six creneaux
    # d'affilee en production (post 140).
    assert resultat["repetes"] == {7: 2}
    assert not db_session.dirty and not db_session.new
