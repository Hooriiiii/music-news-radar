import datetime as dt

import pytest

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


# --- Panne inattendue : sortie non nulle et notification dedoublonnee ---------------------


class _SessionFactice:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


def _agent_en_panne(tmp_path, monkeypatch, *, jeton=None, passage=None):
    monkeypatch.setattr(settings, "publication_mode", "apercu")
    monkeypatch.setattr(settings, "meta_token_path", tmp_path / "jeton.json")
    monkeypatch.setattr(script, "attendre_base", lambda: None)
    monkeypatch.setattr(script, "SessionLocal", _SessionFactice)
    monkeypatch.setattr(script, "make_claude_composer", lambda: None)
    monkeypatch.setattr(script, "surveiller_jeton", jeton or (lambda maintenant, **kwargs: None))
    monkeypatch.setattr(script, "tourner", passage or (lambda *a, **k: script.RapportPublication()))
    notifications = []
    monkeypatch.setattr(script, "notifier", lambda titre, message: notifications.append(message))
    return notifications


def _lever(message):
    def lever(*args, **kwargs):
        raise RuntimeError(message)
    return lever


def test_un_passage_en_panne_sort_en_erreur_et_previent_une_fois_par_heure(tmp_path, monkeypatch):
    notifications = _agent_en_panne(tmp_path, monkeypatch, passage=_lever("base cassee"))
    for minutes in (0, 10, 50):
        monkeypatch.setattr(script, "_maintenant", lambda m=minutes: MAINTENANT + dt.timedelta(minutes=m))
        with pytest.raises(SystemExit) as sortie:
            script.main()
        assert sortie.value.code == 1
    assert len(notifications) == 1 and "base cassee" in notifications[0]
    # L'etat vit a cote du jeton.
    assert (tmp_path / script.FICHIER_PANNE).exists()

    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT + dt.timedelta(minutes=70))
    with pytest.raises(SystemExit):
        script.main()
    assert len(notifications) == 2


def test_un_jeton_en_panne_n_empeche_pas_le_passage(tmp_path, monkeypatch):
    passages = []

    def passage(*args, **kwargs):
        passages.append(1)
        return script.RapportPublication()

    notifications = _agent_en_panne(tmp_path, monkeypatch, jeton=_lever("disque plein"), passage=passage)
    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT)
    with pytest.raises(SystemExit) as sortie:
        script.main()
    assert sortie.value.code == 1
    assert passages == [1]
    assert len(notifications) == 1 and "disque plein" in notifications[0]


def test_un_compositeur_impossible_a_construire_n_empeche_pas_le_passage(tmp_path, monkeypatch):
    """Sans cle Anthropic, les creneaux deja geles doivent partir quand meme."""
    passages = []

    def passage(db, maintenant, *, composer, **kwargs):
        passages.append(composer)
        return script.RapportPublication()

    _agent_en_panne(tmp_path, monkeypatch, passage=passage)
    monkeypatch.setattr(script, "make_claude_composer", _lever("cle absente"))
    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT)
    script.main()
    (composer,) = passages
    with pytest.raises(RuntimeError, match="cle absente"):
        composer(object())
