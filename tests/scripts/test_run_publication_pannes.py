"""Seules les pannes d'ETAPE alimentent `panne`, sans jamais un message d'exception.

`split(" : ")[0]` rendait « 31/10 09:03 » pour une faute d'article, et une exclusion
ordinaire allumait « le dernier passage n'a pas abouti ».
"""

import datetime as dt

import scripts.run_publication as script
from app.schedule import runner
from tests.scripts.test_run_publication import _base_ok, _etat_passage


def _passage_avec(monkeypatch, rapport):
    _base_ok(monkeypatch)
    monkeypatch.setattr(script, "tourner", lambda *a, **k: rapport)
    script.main()
    return _etat_passage()["panne"]


def test_une_faute_d_article_n_allume_pas_le_voyant_de_l_agent(monkeypatch):
    rapport = script.RapportPublication()
    rapport.erreurs.append("31/10 09:03 : composition impossible : le texte")
    assert _passage_avec(monkeypatch, rapport) is None


def test_une_panne_d_etape_est_rapportee(monkeypatch):
    rapport = script.RapportPublication()
    rapport.pannes.append("etape gel en panne (RuntimeError)")
    assert _passage_avec(monkeypatch, rapport) == "etape gel en panne (RuntimeError)"


def test_le_vrai_tourner_ne_rapporte_que_l_etape_et_le_type(db_session, monkeypatch):
    def en_panne(*_a, **_k):
        raise RuntimeError("access_token=SECRET")

    monkeypatch.setattr(runner, "_marquer_manques", en_panne)

    rapport = runner.tourner(
        db_session, dt.datetime.now(dt.timezone.utc), mode="apercu", composer=None, preparer=None,
        publier=None, notifier=lambda *a: None,
    )

    assert rapport.pannes == ["etape retards en panne (RuntimeError)"]
    assert any("SECRET" in erreur for erreur in rapport.erreurs), "le log garde le message"
