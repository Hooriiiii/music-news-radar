import datetime as dt

import scripts.run_publication as script
from app.config import settings
from app.publish.jeton import EtatJeton, charger, enregistrer

MAINTENANT = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.timezone.utc)


def _jeton(tmp_path, monkeypatch, expire_dans_jours):
    chemin = tmp_path / "jeton.json"
    monkeypatch.setattr(settings, "meta_token_path", chemin)
    # Rafraichi la veille : aucun appel reseau ne part.
    enregistrer(chemin, EtatJeton(
        "IGAAx",
        rafraichi_le=MAINTENANT - dt.timedelta(days=1),
        expire_le=MAINTENANT + dt.timedelta(days=expire_dans_jours),
    ))
    return chemin


def test_l_expiration_proche_ne_previent_qu_une_fois_par_jour(tmp_path, monkeypatch):
    """L'agent passe toutes les 10 minutes : sans cette memoire, 144 notifications par jour."""
    chemin = _jeton(tmp_path, monkeypatch, expire_dans_jours=3)
    notifications = []
    for _ in range(3):
        script.surveiller_jeton(MAINTENANT, notifier=lambda titre, message: notifications.append(message))
    assert len(notifications) == 1
    assert charger(chemin).alerte_envoyee_le == MAINTENANT.date()


def test_un_jeton_sain_ne_previent_pas(tmp_path, monkeypatch):
    _jeton(tmp_path, monkeypatch, expire_dans_jours=40)
    notifications = []
    script.surveiller_jeton(MAINTENANT, notifier=lambda titre, message: notifications.append(message))
    assert notifications == []


def test_sans_aucun_jeton_l_agent_le_dit_sans_planter(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(settings, "meta_token_path", tmp_path / "absent.json")
    monkeypatch.setattr(settings, "meta_access_token", None)
    script.surveiller_jeton(MAINTENANT, notifier=lambda titre, message: None)
    assert "META_ACCESS_TOKEN" in capsys.readouterr().out


def test_en_arret_l_agent_ne_touche_meme_pas_la_base(monkeypatch):
    def interdit():
        raise AssertionError("la base ne doit pas etre sollicitee en mode arret")

    monkeypatch.setattr(settings, "publication_mode", "arret")
    monkeypatch.setattr(script, "attendre_base", interdit)
    script.main()
