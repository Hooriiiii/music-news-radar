"""Le canal d'alerte : notification macOS via osascript."""

import subprocess


from app.config import settings
from app.delivery.notify import notifier


class FauxRun:
    def __init__(self, returncode=0, exc=None):
        self.returncode = returncode
        self.exc = exc
        self.commandes = []

    def __call__(self, commande, **kwargs):
        self.commandes.append(commande)
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(commande, self.returncode, "", "")


def test_notifier_appelle_osascript_avec_le_titre_et_le_message(monkeypatch):
    faux = FauxRun()
    monkeypatch.setattr(settings, "notifications_enabled", True)
    monkeypatch.setattr("app.delivery.notify._chemin_terminal_notifier", lambda: None)

    assert notifier("Kinda", "3 sources muettes", run=faux) is True

    (commande,) = faux.commandes
    assert commande[0] == "osascript"
    script = commande[-1]
    assert "3 sources muettes" in script
    assert "Kinda" in script


def test_les_guillemets_du_message_ne_cassent_pas_le_script(monkeypatch):
    """Un nom de source contenant un guillemet fermerait la chaîne AppleScript et
    ferait executer n'importe quoi. Le message vient de la base, pas de nous."""
    faux = FauxRun()
    monkeypatch.setattr(settings, "notifications_enabled", True)
    monkeypatch.setattr("app.delivery.notify._chemin_terminal_notifier", lambda: None)

    notifier("Kinda", 'source "X • radar" muette', run=faux)

    script = faux.commandes[0][-1]
    assert '\\"' in script, "les guillemets doivent etre echappes"


def test_un_osascript_en_echec_ne_leve_pas(monkeypatch):
    """Un canal d'alerte casse ne doit JAMAIS casser le pipeline qu'il surveille."""
    monkeypatch.setattr(settings, "notifications_enabled", True)
    assert notifier("Kinda", "test", run=FauxRun(returncode=1)) is False


def test_osascript_absent_ne_leve_pas(monkeypatch):
    """Sur un Linux, ou sans osascript : on logue et on continue."""
    monkeypatch.setattr(settings, "notifications_enabled", True)
    assert notifier("Kinda", "test", run=FauxRun(exc=FileNotFoundError())) is False


def test_notifications_desactivees_n_appellent_rien(monkeypatch):
    faux = FauxRun()
    monkeypatch.setattr(settings, "notifications_enabled", False)

    assert notifier("Kinda", "test", run=faux) is False
    assert faux.commandes == []


def test_un_message_vide_n_envoie_rien(monkeypatch):
    """Silence quand tout va bien : pas de notification creuse."""
    faux = FauxRun()
    monkeypatch.setattr(settings, "notifications_enabled", True)

    assert notifier("Kinda", "", run=faux) is False
    assert faux.commandes == []


# --- terminal-notifier : le logo Kinda dans la notification ---


def test_terminal_notifier_est_prefere_et_porte_le_logo(monkeypatch, tmp_path):
    faux = FauxRun()
    monkeypatch.setattr(settings, "notifications_enabled", True)
    monkeypatch.setattr("app.delivery.notify._chemin_terminal_notifier", lambda: "/tn")

    assert notifier("Kinda", "2 sources muettes", run=faux) is True

    (commande,) = faux.commandes
    assert commande[0] == "/tn"
    assert "-contentImage" in commande, "le logo doit etre passe"
    assert commande[commande.index("-message") + 1] == "2 sources muettes"


def test_repli_sur_osascript_quand_terminal_notifier_est_absent(monkeypatch):
    """Le binaire vient de Homebrew : il peut disparaitre a une desinstallation.
    Le canal d'alerte ne doit pas mourir avec lui."""
    faux = FauxRun()
    monkeypatch.setattr(settings, "notifications_enabled", True)
    monkeypatch.setattr("app.delivery.notify._chemin_terminal_notifier", lambda: None)

    assert notifier("Kinda", "test", run=faux) is True
    assert faux.commandes[0][0] == "osascript"


def test_repli_sur_osascript_si_terminal_notifier_echoue(monkeypatch):
    """Un binaire present mais casse ne doit pas faire perdre l'alerte."""
    monkeypatch.setattr(settings, "notifications_enabled", True)
    monkeypatch.setattr("app.delivery.notify._chemin_terminal_notifier", lambda: "/tn")

    appels = []

    def run(commande, **kwargs):
        appels.append(commande)
        code = 1 if commande[0] == "/tn" else 0
        return subprocess.CompletedProcess(commande, code, "", "")

    assert notifier("Kinda", "test", run=run) is True
    assert [c[0] for c in appels] == ["/tn", "osascript"]
