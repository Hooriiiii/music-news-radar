"""Agent de publication (launchd, toutes les 10 minutes).

Usage :
    uv run python -m scripts.run_publication

En mode `arret` (defaut), il sort immediatement sans toucher la base. Sinon il
surveille le jeton Instagram, puis fait un passage de la file.

Une exception inattendue ne doit pas mourir en silence dans un log que personne
ne lit (l'agent digest a echoue cinq semaines ainsi) : elle est notifiee -- au
plus une fois par heure, sinon 6 notifications par heure pour la meme panne -- et
le script sort en code 1, que launchd enregistre et que `--status` affiche.
"""

import datetime as dt
import json
import logging
from functools import partial

from app.config import settings
from app.content.choix_visuels import choisir_visuels
from app.content.compose import make_claude_composer
from app.content.lecture_artistes import LecteurComptes
from app.db import BaseInjoignable, SessionLocal, attendre_base
from app.delivery.notify import notifier
from app.publish.jeton import JetonIndisponible, alerte_expiration, enregistrer, rafraichir_si_necessaire
from app.publish.mode import mode_effectif, reels_actifs
from app.publish.preparation import preparer_fichiers
from app.publish.preparation_reel import preparer_reel
from app.publish.publier import publier_fichiers, publier_reel
from app.publish.ytdlp import RecuperateurYtdlp
from app.schedule.runner import RapportPublication, tourner
from app.schedule.store import utc

# A cote du jeton : meme dossier applicatif, hors du depot.
FICHIER_PANNE = "publication_panne.json"
INTERVALLE_PANNE = dt.timedelta(hours=1)


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


def _compositeur_paresseux():
    # Construit au premier article a composer seulement : un client Anthropic
    # impossible a construire (cle absente) ne doit pas bloquer la publication
    # des creneaux deja geles.
    cache = []

    def composer(article):
        if not cache:
            cache.append(make_claude_composer())
        return cache[0](article)

    return composer


def outils_reel() -> dict:
    """Les outils reel d'UN passage.

    Coupes au dashboard (`reels_actifs`) : aucun outil, donc rien n'est gele ni
    publie en reel. Le recuperateur yt-dlp est NEUF a chaque passage : c'est lui
    qui porte le budget d'un telechargement par passage. `PUBLICATION_REEL_YTDLP`
    a false : pas de recuperateur, seules les videos directes de l'API partent.
    """
    if not reels_actifs():
        return {"preparer_reel": None, "publier_reel": None}
    recuperer = RecuperateurYtdlp() if settings.publication_reel_ytdlp else None
    return {
        "preparer_reel": partial(preparer_reel, recuperer_ytdlp=recuperer),
        "publier_reel": publier_reel,
    }


def choix_du_passage():
    """Le choix des visuels d'UN passage : un lecteur Graph neuf, qui porte le
    budget de lectures du passage (comme le recuperateur yt-dlp).

    L'horloge passee au lecteur est TOUJOURS aware UTC : sa frontiere de jour
    (budget quotidien) et sa pause de quota comparent des dates, et un naif
    venant d'une base SQLite ferait lever la comparaison."""
    lecteur = LecteurComptes()

    def choisir(db, article, format_, maintenant):
        return choisir_visuels(
            db, article, format_=format_, maintenant=utc(maintenant), lecteur=lecteur
        )

    return choisir


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    mode = mode_effectif()
    if mode == "arret":
        return
    # Lance par launchd, parfois au reveil du Mac avant que le DNS reponde.
    try:
        attendre_base()
    except BaseInjoignable as exc:
        print(f"[ERREUR] {exc}")
        raise SystemExit(1) from exc

    maintenant = _maintenant()
    en_panne = False
    # Deux blocs separes : un jeton illisible ne doit pas empecher de publier un
    # creneau deja gele (le jeton courant peut encore etre valide).
    try:
        surveiller_jeton(maintenant, notifier=notifier)
    except Exception as exc:  # noqa: BLE001
        en_panne = True
        signaler_panne(f"surveillance du jeton Instagram en panne : {exc}", maintenant)
    rapport = RapportPublication()
    try:
        with SessionLocal() as db:
            rapport = tourner(
                db, maintenant, mode=mode, composer=_compositeur_paresseux(),
                preparer=preparer_fichiers, publier=publier_fichiers, notifier=notifier,
                # Relu avant chaque reclamation : le dashboard peut repasser en
                # apercu ou en arret pendant un long passage.
                lire_mode=mode_effectif,
                choisir=choix_du_passage(),
                **outils_reel(),
            )
    except Exception as exc:  # noqa: BLE001
        en_panne = True
        signaler_panne(f"agent de publication en panne : {exc}", maintenant)

    compteurs = {
        "composes": rapport.composes, "geles": rapport.geles, "publies": rapport.publies,
        "apercus": rapport.apercus, "manques": rapport.manques, "echecs": rapport.echecs,
        # Tache 5, I-2 (tour de correction 1/5) : le saut faute de visuel de
        # bonne qualite doit etre VISIBLE au meme titre que les autres
        # compteurs -- un feed qui se tait sans rien dire est le pire des deux
        # mondes (CLAUDE.md).
        "sautes": rapport.sautes,
        # Tour de correction 2/5, point 1 : un report qui se prolonge doit se
        # voir AVANT de basculer en saut, distinct de `sautes`.
        "reportes": rapport.reportes,
    }
    # Silence quand rien ne se passe : le log ne grossit pas de 144 lignes vides par jour.
    if any(compteurs.values()) or rapport.erreurs:
        resume = ", ".join(f"{valeur} {nom}" for nom, valeur in compteurs.items() if valeur)
        print(f"[{maintenant:%d/%m %H:%M}] publication ({mode}) : {resume or 'rien'}")
        for erreur in rapport.erreurs:
            print(f"  [ERREUR] {erreur}")
    if en_panne:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
