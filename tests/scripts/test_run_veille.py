"""L'agent de veille rapide, teste sans base ni reseau.

Meme contrat que `run_publication` : silencieux quand il n'y a rien (288 passages
par jour ne doivent pas ecrire 288 lignes), et une panne inattendue qui sort en
code 1 avec une notification dedoublonnee par heure -- l'agent digest a echoue
cinq semaines dans un log que personne ne lisait.
"""

import datetime as dt

import pytest

import scripts.run_veille as script
from app.config import settings
from app.pipeline.veille import RapportVeille

MAINTENANT = dt.datetime(2026, 9, 18, 10, 0, tzinfo=dt.timezone.utc)


class _SessionFactice:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


def _agent(tmp_path, monkeypatch, passage=None):
    monkeypatch.setattr(settings, "meta_token_path", tmp_path / "jeton.json")
    monkeypatch.setattr(script, "attendre_base", lambda: None)
    monkeypatch.setattr(script, "SessionLocal", _SessionFactice)
    monkeypatch.setattr(script, "run_veille", passage or (lambda *a, **k: RapportVeille()))
    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT)
    notifications = []
    monkeypatch.setattr(script, "notifier", lambda titre, message: notifications.append(message))
    return notifications


def test_la_base_est_attendue_avant_toute_chose(tmp_path, monkeypatch, capsys):
    """Un agent launchd part parfois au reveil du Mac, avant que le DNS reponde :
    l'agent `digest` a echoue chaque jour pendant cinq semaines pour cela."""
    from app.db import BaseInjoignable

    _agent(tmp_path, monkeypatch)

    def injoignable():
        raise BaseInjoignable("base injoignable")

    monkeypatch.setattr(script, "attendre_base", injoignable)
    monkeypatch.setattr(script, "run_veille", lambda *a, **k: pytest.fail("trop tot"))

    with pytest.raises(SystemExit) as sortie:
        script.main()

    assert sortie.value.code == 1
    assert "injoignable" in capsys.readouterr().out


def test_un_passage_sans_rien_ne_dit_rien(tmp_path, monkeypatch, capsys):
    _agent(tmp_path, monkeypatch)

    script.main()

    assert capsys.readouterr().out == ""


def test_un_passage_qui_ramene_quelque_chose_le_dit(tmp_path, monkeypatch, capsys):
    from app.pipeline.ingest import IngestStats

    rapport = RapportVeille(ingest=[IngestStats(1, "STMPD RCRDS", fetched=3, new=2)], appels=2)
    _agent(tmp_path, monkeypatch, passage=lambda *a, **k: rapport)

    script.main()

    imprime = capsys.readouterr().out
    assert "2 nouveaux" in imprime
    assert "2 appels" in imprime


def test_une_pause_de_quota_est_dite(tmp_path, monkeypatch, capsys):
    _agent(tmp_path, monkeypatch, passage=lambda *a, **k: RapportVeille(en_pause=True))

    script.main()

    assert "pause" in capsys.readouterr().out


def test_une_panne_sort_en_erreur_et_previent_une_fois_par_heure(tmp_path, monkeypatch):
    def lever(*args, **kwargs):
        raise RuntimeError("Meta injoignable")

    notifications = _agent(tmp_path, monkeypatch, passage=lever)

    for minutes in (0, 5, 50):
        monkeypatch.setattr(
            script, "_maintenant", lambda m=minutes: MAINTENANT + dt.timedelta(minutes=m)
        )
        with pytest.raises(SystemExit) as sortie:
            script.main()
        assert sortie.value.code == 1

    assert len(notifications) == 1 and "Meta injoignable" in notifications[0]
    assert (tmp_path / script.FICHIER_PANNE).exists()

    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT + dt.timedelta(minutes=70))
    with pytest.raises(SystemExit):
        script.main()
    assert len(notifications) == 2
