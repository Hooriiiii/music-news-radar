"""Agent de publication (launchd, toutes les 10 minutes).

Usage :
    uv run python -m scripts.run_publication

En mode `arret` (defaut), il sort immediatement sans toucher la base. Sinon il
surveille le jeton Instagram, puis fait un passage de la file.
"""

import datetime as dt
import logging

from app.config import settings
from app.content.compose import make_claude_composer
from app.db import BaseInjoignable, SessionLocal, attendre_base
from app.delivery.notify import notifier
from app.publish.jeton import JetonIndisponible, alerte_expiration, enregistrer, rafraichir_si_necessaire
from app.publish.preparation import preparer_fichiers
from app.publish.publier import publier_fichiers
from app.schedule.runner import tourner


def surveiller_jeton(maintenant: dt.datetime, notifier=notifier) -> None:
    try:
        etat, erreur = rafraichir_si_necessaire(settings.meta_token_path, maintenant)
    except JetonIndisponible as exc:
        # Pas de notification ici : la publication echouera et previendra, elle.
        print(f"[ERREUR] {exc}")
        return
    alerte = erreur or alerte_expiration(etat, maintenant)
    if alerte and etat.alerte_envoyee_le != maintenant.date():
        notifier("Kinda", alerte)
        etat.alerte_envoyee_le = maintenant.date()
        enregistrer(settings.meta_token_path, etat)


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    mode = settings.publication_mode
    if mode == "arret":
        return
    # Lance par launchd, parfois au reveil du Mac avant que le DNS reponde.
    try:
        attendre_base()
    except BaseInjoignable as exc:
        print(f"[ERREUR] {exc}")
        raise SystemExit(1) from exc

    maintenant = dt.datetime.now(dt.timezone.utc)
    surveiller_jeton(maintenant)
    with SessionLocal() as db:
        rapport = tourner(
            db, maintenant, mode=mode, composer=make_claude_composer(),
            preparer=preparer_fichiers, publier=publier_fichiers, notifier=notifier,
        )

    compteurs = {
        "composes": rapport.composes, "geles": rapport.geles, "publies": rapport.publies,
        "apercus": rapport.apercus, "manques": rapport.manques, "echecs": rapport.echecs,
    }
    # Silence quand rien ne se passe : le log ne grossit pas de 144 lignes vides par jour.
    if any(compteurs.values()) or rapport.erreurs:
        resume = ", ".join(f"{valeur} {nom}" for nom, valeur in compteurs.items() if valeur)
        print(f"[{maintenant:%d/%m %H:%M}] publication ({mode}) : {resume or 'rien'}")
        for erreur in rapport.erreurs:
            print(f"  [ERREUR] {erreur}")


if __name__ == "__main__":
    main()
