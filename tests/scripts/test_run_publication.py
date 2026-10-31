import datetime as dt
import json
import os

import pytest

import scripts.run_publication as script
from app.config import settings
from app.publish.jeton import EtatJeton, charger, enregistrer
from app.publish.mode import enregistrer_mode, enregistrer_reels
from app.schedule.passage import FICHIER_PASSAGE, consigner_passage

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


def test_le_mode_du_fichier_dashboard_prime_sur_le_env(tmp_path, monkeypatch):
    """arret pose depuis le dashboard doit arreter l'agent meme si .env dit apercu."""
    def interdit():
        raise AssertionError("la base ne doit pas etre sollicitee : le fichier dit arret")

    monkeypatch.setattr(settings, "publication_mode", "apercu")
    monkeypatch.setattr(settings, "publication_mode_path", tmp_path / "publication.json")
    enregistrer_mode("arret", MAINTENANT, tmp_path / "publication.json")
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


def test_i2_le_compteur_sautes_est_visible_dans_le_log(tmp_path, monkeypatch, capsys):
    """Tour de correction 1/5 (I-2) : le saut faute de visuel de qualite doit
    etre visible au meme titre que les autres compteurs (composes, geles,
    publies...) -- un feed qui se tait sans rien dire est le pire des deux
    mondes (CLAUDE.md)."""
    def passage(*args, **kwargs):
        return script.RapportPublication(sautes=2)

    _agent_en_panne(tmp_path, monkeypatch, passage=passage)
    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT)
    script.main()

    assert "2 sautes" in capsys.readouterr().out


def test_2_5_le_compteur_reportes_est_visible_dans_le_log(tmp_path, monkeypatch, capsys):
    """Tour de correction 2/5, point 1 : un report qui se prolonge doit se
    voir AVANT de basculer en saut, distinct de `sautes`."""
    def passage(*args, **kwargs):
        return script.RapportPublication(reportes=3)

    _agent_en_panne(tmp_path, monkeypatch, passage=passage)
    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT)
    script.main()

    assert "3 reportes" in capsys.readouterr().out


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


# --- Outils reel branches au passage --------------------------------------------------


def _passages(tmp_path, monkeypatch):
    recus = []

    def passage(*args, **kwargs):
        recus.append(kwargs)
        return script.RapportPublication()

    _agent_en_panne(tmp_path, monkeypatch, passage=passage)
    return recus


@pytest.mark.usefixtures("reels_allumes")
def test_l_agent_branche_les_outils_des_reels(tmp_path, monkeypatch):
    recus = _passages(tmp_path, monkeypatch)
    script.main()
    (recu,) = recus
    assert recu["preparer_reel"].func is script.preparer_reel
    assert isinstance(recu["preparer_reel"].keywords["recuperer_ytdlp"], script.RecuperateurYtdlp)
    assert recu["publier_reel"] is script.publier_reel


@pytest.mark.usefixtures("reels_allumes")
def test_chaque_passage_a_son_propre_budget_yt_dlp(tmp_path, monkeypatch):
    recus = _passages(tmp_path, monkeypatch)
    script.main()
    script.main()
    premier, second = (r["preparer_reel"].keywords["recuperer_ytdlp"] for r in recus)
    assert premier is not second


@pytest.mark.usefixtures("reels_allumes")
def test_yt_dlp_coupe_par_le_env(tmp_path, monkeypatch):
    recus = _passages(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "publication_reel_ytdlp", False)
    script.main()
    assert recus[0]["preparer_reel"].keywords["recuperer_ytdlp"] is None


def test_sans_choix_au_dashboard_aucun_outil_reel(tmp_path, monkeypatch):
    """Opt-in : sans case cochee au dashboard, l'agent ne gele ni ne publie de reel."""
    recus = _passages(tmp_path, monkeypatch)
    script.main()
    assert (recus[0]["preparer_reel"], recus[0]["publier_reel"]) == (None, None)


def test_reels_coupes_au_dashboard(tmp_path, monkeypatch):
    """Les reels deja geles ne partent plus : sans publieur, ils finissent en MANQUE."""
    recus = _passages(tmp_path, monkeypatch)
    enregistrer_reels(False, MAINTENANT)
    script.main()
    assert (recus[0]["preparer_reel"], recus[0]["publier_reel"]) == (None, None)


def test_l_agent_relit_le_mode_avant_chaque_publication(tmp_path, monkeypatch):
    """I1 : un passage peut durer plus de 10 minutes ; le runner relit le mode
    du dashboard avant chaque reclamation, pas seulement au demarrage."""
    recus = _passages(tmp_path, monkeypatch)
    script.main()
    assert recus[0]["lire_mode"] is script.mode_effectif


# --- Le passage consigne (2026-10-30) ------------------------------------------
#
# Aucun voyant ne mesurait l'agent de publication : il pouvait mourir sous un
# bandeau vert. L'heure de fin s'ecrit donc A CHAQUE sortie du script, et cette
# ecriture ne doit jamais couter une publication.


class _FausseSession:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


def _etat_passage():
    return json.loads((settings.meta_token_path.parent / FICHIER_PASSAGE).read_text())


def _base_ok(monkeypatch):
    monkeypatch.setattr(settings, "publication_mode", "apercu")
    monkeypatch.setattr(script, "attendre_base", lambda: None)
    monkeypatch.setattr(script, "SessionLocal", _FausseSession)
    monkeypatch.setattr(script, "surveiller_jeton", lambda *a, **k: None)
    monkeypatch.setattr(script, "signaler_panne", lambda *a, **k: None)
    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT)


def test_le_passage_est_consigne_meme_en_mode_arret(monkeypatch):
    """L'agent passe, meme sans rien a faire : sinon, repasser en reel depuis
    l'arret se ferait sans savoir qu'il est mort depuis un mois."""
    monkeypatch.setattr(settings, "publication_mode", "arret")
    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT)
    script.main()
    etat = _etat_passage()
    assert etat["fin_le"] == MAINTENANT.isoformat()
    assert etat["mode"] == "arret"
    assert etat["panne"] is None


def test_un_passage_normal_est_consigne_sans_panne(monkeypatch):
    _base_ok(monkeypatch)
    monkeypatch.setattr(script, "tourner", lambda *a, **k: script.RapportPublication())
    script.main()
    assert _etat_passage()["panne"] is None
    assert _etat_passage()["mode"] == "apercu"


def test_la_consignation_vient_APRES_la_publication(monkeypatch):
    """Point d'ecriture : la FIN. Rien n'est ecrit avant ou pendant `tourner`, donc
    cette ecriture ne peut ni retarder ni empecher une publication."""
    _base_ok(monkeypatch)
    vu = []

    def tourner(*a, **k):
        vu.append((settings.meta_token_path.parent / FICHIER_PASSAGE).exists())
        return script.RapportPublication()

    monkeypatch.setattr(script, "tourner", tourner)
    script.main()
    assert vu == [False]
    assert _etat_passage()["fin_le"]


def test_une_base_injoignable_consigne_le_passage_et_sa_panne(monkeypatch):
    monkeypatch.setattr(settings, "publication_mode", "apercu")
    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT)

    def injoignable():
        raise script.BaseInjoignable("DNS")

    monkeypatch.setattr(script, "attendre_base", injoignable)
    with pytest.raises(SystemExit):
        script.main()
    assert "base injoignable" in _etat_passage()["panne"]


def test_un_tourner_qui_leve_consigne_le_passage_et_sa_panne(monkeypatch):
    _base_ok(monkeypatch)

    def casse(*a, **k):
        raise RuntimeError("access_token=SECRET")

    monkeypatch.setattr(script, "tourner", casse)
    with pytest.raises(SystemExit):
        script.main()
    panne = _etat_passage()["panne"]
    assert "RuntimeError" in panne
    # Le message d'une exception peut porter une URL avec son jeton, et ce champ
    # s'affiche : seul le TYPE y entre.
    assert "SECRET" not in panne


def test_un_jeton_qui_leve_consigne_la_panne_sans_empecher_le_passage(monkeypatch):
    _base_ok(monkeypatch)
    passes = []

    def casse(*a, **k):
        raise ValueError("jeton")

    monkeypatch.setattr(script, "surveiller_jeton", casse)
    monkeypatch.setattr(script, "tourner", lambda *a, **k: passes.append(1) or script.RapportPublication())
    with pytest.raises(SystemExit):
        script.main()
    assert passes == [1]
    assert "ValueError" in _etat_passage()["panne"]


def test_une_etape_en_panne_ne_se_lit_pas_comme_un_passage_sain(monkeypatch):
    """`tourner` avale les pannes d'etape : sans ce report, un agent dont TOUTES
    les etapes echouent afficherait « il y a 2 min » en vert."""
    _base_ok(monkeypatch)
    rapport = script.RapportPublication()
    rapport.erreurs.append("etape publication en panne : access_token=SECRET")
    rapport.pannes.append("etape publication en panne (RuntimeError)")
    monkeypatch.setattr(script, "tourner", lambda *a, **k: rapport)
    script.main()
    panne = _etat_passage()["panne"]
    assert "etape publication en panne" in panne
    assert "SECRET" not in panne


def test_une_erreur_imprevue_avant_tout_consigne_quand_meme(monkeypatch):
    monkeypatch.setattr(script, "_maintenant", lambda: MAINTENANT)

    def casse():
        raise RuntimeError("mode illisible")

    monkeypatch.setattr(script, "mode_effectif", casse)
    with pytest.raises(RuntimeError):
        script.main()
    assert "RuntimeError" in _etat_passage()["panne"]


@pytest.mark.parametrize("erreur", [OSError("disque plein"), PermissionError("refus"), ValueError("bizarre")])
def test_une_panne_de_l_ecriture_ne_fait_jamais_echouer_le_passage(monkeypatch, erreur):
    _base_ok(monkeypatch)
    publies = []
    monkeypatch.setattr(
        script, "tourner", lambda *a, **k: publies.append(1) or script.RapportPublication()
    )

    def replace(*a, **k):
        raise erreur

    monkeypatch.setattr("app.schedule.passage.os.replace", replace)
    script.main()  # ne leve pas : le passage a publie, il sort en 0
    assert publies == [1]
    assert not list(settings.meta_token_path.parent.glob("*.tmp")), "temporaire abandonne"


def test_une_ecriture_ratee_ne_masque_pas_la_sortie_en_panne(monkeypatch):
    """Dans l'autre sens : le code 1 que launchd enregistre doit survivre."""
    _base_ok(monkeypatch)

    def casse(*a, **k):
        raise RuntimeError("x")

    def replace(*a, **k):
        raise OSError("disque plein")

    monkeypatch.setattr(script, "tourner", casse)
    monkeypatch.setattr("app.schedule.passage.os.replace", replace)
    with pytest.raises(SystemExit) as sortie:
        script.main()
    assert sortie.value.code == 1


def test_le_temporaire_est_nomme_par_processus(monkeypatch):
    """Meme regle que tous les fichiers d'etat a cote du jeton : un `.tmp` fixe
    serait ecrit a deux si un passage manuel croise l'agent."""
    vus = []
    vrai = os.replace

    def replace(source, cible):
        vus.append(str(source))
        vrai(source, cible)

    monkeypatch.setattr("app.schedule.passage.os.replace", replace)
    assert consigner_passage(MAINTENANT, "reel") is True
    assert str(os.getpid()) in vus[0]
