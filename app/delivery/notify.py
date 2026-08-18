"""Notification macOS — le canal d'alerte du socle local.

Pourquoi osascript et pas une dépendance : rien à installer, disponible sur
tout macOS, et le pipeline tourne de toute façon sur le Mac de l'utilisateur
depuis le passage au tout-local. `terminal-notifier` serait plus joli mais
ajouterait un paquet Homebrew à maintenir pour un gain cosmétique.

Règle non négociable de ce module : il ne lève JAMAIS. Un canal d'alerte qui
casse le pipeline qu'il surveille est pire que pas d'alerte du tout — c'est
exactement le genre de panne silencieuse que cette brique existe pour éviter.
"""

import logging
import shutil
import subprocess
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Le message vient de la BASE (noms de sources, messages d'erreur d'API) : il
# peut contenir des guillemets, qui fermeraient la chaîne AppleScript et
# feraient exécuter la suite comme du code. On échappe donc antislash d'abord,
# guillemet ensuite -- l'ordre compte, sinon on échapperait nos propres
# antislashs d'échappement.
def _echapper(texte: str) -> str:
    return texte.replace("\\", "\\\\").replace('"', '\\"')


# Le logo de la charte, affiche DANS la notification par terminal-notifier.
# `display notification` d'AppleScript n'a aucun parametre d'icone : ce qui
# s'affiche est celle de l'application qui poste. Une app maison portant le logo
# a ete tentee et abandonnee -- macOS n'accorde le droit de notifier qu'aux
# applications qu'il reconnait, et un applet signe en ad-hoc ne declenche meme
# pas la demande d'autorisation. Il faudrait un Developer ID paye a l'annee.
LOGO = Path(__file__).resolve().parent.parent / "content" / "assets" / "logo.png"


# launchd ne fournit qu'un PATH minimal -- sans /opt/homebrew/bin. `which` y
# echoue donc alors que le binaire EST installe, et l'agent retomberait sur
# osascript en perdant le logo, sans que rien ne le signale puisque le repli
# fonctionne. On regarde aussi les emplacements Homebrew usuels (arm64 puis
# Intel).
EMPLACEMENTS_CONNUS = (
    "/opt/homebrew/bin/terminal-notifier",
    "/usr/local/bin/terminal-notifier",
)


def _chemin_terminal_notifier() -> str | None:
    """Le binaire, s'il est la. Isole pour etre remplacable en test."""
    trouve = shutil.which("terminal-notifier")
    if trouve:
        return trouve
    for chemin in EMPLACEMENTS_CONNUS:
        if Path(chemin).exists():
            return chemin
    return None


def _via_terminal_notifier(binaire: str, titre: str, message: str, run) -> bool:
    commande = [binaire, "-title", titre, "-message", message]
    if LOGO.exists():
        commande += ["-contentImage", str(LOGO)]
    try:
        return run(commande, capture_output=True, text=True).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def notifier(titre: str, message: str, run=subprocess.run) -> bool:
    """Affiche une notification macOS. Rend True si elle est partie.

    Un message vide n'envoie rien : le socle est silencieux quand tout va bien,
    et une notification creuse apprendrait à l'utilisateur à les ignorer.
    """
    if not settings.notifications_enabled:
        return False
    if not message.strip():
        return False

    # terminal-notifier d'abord : c'est lui qui sait afficher le logo. Il vient
    # de Homebrew et peut disparaitre a une desinstallation, ou echouer : on
    # retombe alors sur osascript, present sur tout macOS. Le canal d'alerte ne
    # doit pas mourir avec une dependance optionnelle.
    binaire = _chemin_terminal_notifier()
    if binaire and _via_terminal_notifier(binaire, titre, message, run):
        return True

    script = (
        f'display notification "{_echapper(message)}" '
        f'with title "{_echapper(titre)}"'
    )
    try:
        resultat = run(["osascript", "-e", script], capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Notification impossible (%s) : %s", type(exc).__name__, message)
        return False

    if resultat.returncode != 0:
        logger.warning("osascript a échoué (%s) : %s", resultat.returncode, message)
        return False
    return True
