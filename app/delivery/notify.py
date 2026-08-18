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
import subprocess

from app.config import settings

logger = logging.getLogger(__name__)

# Le message vient de la BASE (noms de sources, messages d'erreur d'API) : il
# peut contenir des guillemets, qui fermeraient la chaîne AppleScript et
# feraient exécuter la suite comme du code. On échappe donc antislash d'abord,
# guillemet ensuite -- l'ordre compte, sinon on échapperait nos propres
# antislashs d'échappement.
def _echapper(texte: str) -> str:
    return texte.replace("\\", "\\\\").replace('"', '\\"')


# PAS de logo dans la notification, et c'est un choix documente.
#
# `display notification` d'AppleScript n'a aucun parametre d'icone : ce qui
# s'affiche est celle de l'application qui poste. La contourner demande une
# autre application, et les deux voies ont ete essayees puis ABANDONNEES :
#
#   - un applet AppleScript maison portant le logo : macOS n'accorde le droit
#     de notifier qu'aux applications qu'il reconnait, et un applet signe en
#     ad-hoc ne recoit pas l'autorisation ni meme la demande. Il faudrait un
#     Developer ID paye a l'annee.
#   - `terminal-notifier` (Homebrew) et son `-contentImage` : il reste absent
#     de com.apple.ncprefs, donc macOS enregistre ses notifications sans jamais
#     les AFFICHER. Et surtout il rend 0 dans ce cas -- aucun repli ne peut se
#     declencher, le code croit avoir alerte et l'alerte est perdue. Pour un
#     canal de supervision c'est le pire defaut possible, precisement celui que
#     ce module existe pour eliminer.
#
# Verifie a l'ecran par l'utilisateur, pas seulement dans le journal du centre
# de notifications : celui-ci enregistre la DEMANDE, pas l'affichage.

def notifier(titre: str, message: str, run=subprocess.run) -> bool:
    """Affiche une notification macOS. Rend True si elle est partie.

    Un message vide n'envoie rien : le socle est silencieux quand tout va bien,
    et une notification creuse apprendrait à l'utilisateur à les ignorer.
    """
    if not settings.notifications_enabled:
        return False
    if not message.strip():
        return False

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
