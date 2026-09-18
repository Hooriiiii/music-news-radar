"""Agent de veille rapide (launchd, toutes les 5 minutes).

Usage :
    uv run python -m scripts.run_veille

Il n'ingere QUE les comptes Instagram de `VEILLE_COMPTES`, puis regroupe et score
le petit lot qu'il vient de ramener. Ni illustration ni purge : elles restent au
passage horaire de `run_pipeline`, qui continue de lire tout le reste.

Silencieux quand il n'y a rien -- 288 passages par jour rempliraient le log de
lignes vides. Une exception inattendue est notifiee (au plus une fois par heure,
etat a cote du jeton) et le script sort en code 1, que launchd enregistre et que
`install_agents --status` affiche : c'est ainsi que l'agent `digest` a pu echouer
cinq semaines sans que personne ne le voie.
"""

import datetime as dt
import json
import logging

from app.config import settings
from app.db import BaseInjoignable, SessionLocal, attendre_base
from app.delivery.notify import notifier
from app.pipeline.veille import run_veille

FICHIER_PANNE = "veille_panne.json"
INTERVALLE_PANNE = dt.timedelta(hours=1)


def _maintenant() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def signaler_panne(message: str, maintenant: dt.datetime) -> None:
    print(f"[ERREUR] {message}")
    chemin = settings.meta_token_path.parent / FICHIER_PANNE
    try:
        derniere = dt.datetime.fromisoformat(json.loads(chemin.read_text())["notifiee_le"])
    except Exception:  # noqa: BLE001 -- absent ou illisible : on previent
        derniere = None
    if derniere is not None and maintenant - derniere < INTERVALLE_PANNE:
        return
    try:
        notifier("Kinda", message)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps({"notifiee_le": maintenant.isoformat()}))
    except Exception:  # noqa: BLE001 -- la notification ne doit pas masquer la panne
        logging.getLogger(__name__).exception("notification de panne impossible")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Avant toute chose : lance par launchd, parfois au reveil du Mac avant que
    # le DNS reponde.
    try:
        attendre_base()
    except BaseInjoignable as exc:
        print(f"[ERREUR] {exc}")
        raise SystemExit(1) from exc

    maintenant = _maintenant()
    try:
        with SessionLocal() as db:
            rapport = run_veille(db, maintenant)
    except Exception as exc:  # noqa: BLE001 -- une panne muette est le pire cas
        signaler_panne(f"agent de veille en panne : {exc}", maintenant)
        raise SystemExit(1) from exc

    if rapport.en_pause:
        print(f"[{maintenant:%d/%m %H:%M}] veille en pause (quota Graph)")
        return
    if not (rapport.nouveaux or rapport.erreurs or rapport.plafond_atteint):
        return

    morceaux = [f"{rapport.nouveaux} nouveaux", f"{rapport.appels} appels"]
    if rapport.doublons:
        morceaux.append(f"{rapport.doublons} doublons")
    if rapport.scoring is not None:
        morceaux.append(f"{rapport.scoring.scored} scorés")
    print(f"[{maintenant:%d/%m %H:%M}] veille : {', '.join(morceaux)}")
    if rapport.plafond_atteint:
        print(
            f"  ⚠ plafond horaire atteint "
            f"({settings.veille_appels_max_par_heure} appels Graph)"
        )
    for erreur in rapport.erreurs:
        print(f"  [ERREUR] {erreur}")


if __name__ == "__main__":
    main()
