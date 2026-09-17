"""Recuperer la video d'un post Instagram avec yt-dlp, au gel d'un reel seulement.

Pourquoi : Meta omet `media_url` des medias qui contiennent de la musique
protegee -- presque tous les reels d'artistes. Essai du 2026-09-17 : yt-dlp
2026.08.19 a recupere 3 reels publics sur 3 sans connexion (1080x1920, VP9 +
AAC, 15 a 35 s) ; la version Homebrew 2025.12.08 echouait (« login required »).
D'ou une dependance uv EPINGLEE, lancee par `sys.executable -m yt_dlp` -- jamais
un binaire du PATH, jamais `uvx ...@latest`.

Regles decidees avec l'utilisateur (2026-09-17), qui a accepte les risques
(CGU Instagram, fragilite, detection de droits, limitation de l'IP) :
- jamais de connexion ni de cookies : `--ignore-config --no-cookies
  --no-cookies-from-browser` toujours, aucune option d'identification ;
- au gel seulement, jamais a l'ingestion, jamais hors d'instagram.com ;
- un telechargement par passage de l'agent au plus ; apres une panne, 60 minutes
  de pause, etat dans `ytdlp_etat.json` a cote des autres fichiers d'etat.

Trois issues, trois traitements par le runner :
- `PreparationImpossible` : video indisponible, supprimee ou privee -- faute de
  l'ARTICLE, exclu, pas de pause (l'outil, lui, fonctionne) ;
- `YtdlpIndisponible` : tout le reste (session exigee, 429, erreur inconnue,
  delai) -- panne SYSTEMIQUE, notifiee une fois, pause de 60 minutes ;
- `YtdlpEnAttente` : pause en cours ou budget du passage epuise -- une raison
  sur le creneau, aucune notification.
"""

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from app.config import settings
from app.content.media import est_lien_instagram
from app.content.video import chemin_outil
from app.publish.preparation import PreparationDifferee, PreparationImpossible

FICHIER_ETAT = "ytdlp_etat.json"
PAUSE_APRES_ECHEC = dt.timedelta(minutes=60)
TELECHARGEMENTS_PAR_PASSAGE = 1
DELAI_MAX_S = 300
MISE_A_JOUR = "uv lock --upgrade-package yt-dlp && uv sync"
MO = 1024 * 1024
NOM_SOURCE = "source-ytdlp"

INDISPONIBLE = "indisponible"
SYSTEMIQUE = "systemique"

# Toujours passees. `--ignore-config` d'abord : un fichier de configuration
# yt-dlp de l'utilisateur pourrait sinon reintroduire des cookies.
OPTIONS_FIXES = (
    "--ignore-config",
    "--no-cookies",
    "--no-cookies-from-browser",
    "--no-playlist",
    "--no-progress",
    "--no-warnings",
    "--retries", "1",
    "--socket-timeout", "30",
    # Un MP4 tout fait si Instagram en propose un, sinon la meilleure video et le
    # meilleur audio fusionnes en MP4 (VP9 + AAC mesure le 2026-09-17).
    "-f", "b[ext=mp4]/bv*+ba/b",
    "--merge-output-format", "mp4",
)
# Jamais, sous aucune forme (test sentinelle).
OPTIONS_INTERDITES = frozenset({
    "--cookies", "--cookies-from-browser", "--username", "-u", "--password", "-p",
    "--netrc", "--netrc-location", "--netrc-cmd", "--video-password",
    "--ap-username", "--ap-password", "--client-certificate",
})

# Session, limitation, format : jamais la faute de la video. Teste AVANT
# l'indisponibilite -- le message de session d'Instagram dit aussi « not available ».
_SYSTEMIQUE = re.compile(
    r"login|log in|sign in|registered users|rate.?limit|too many requests|\b429\b|cookies|"
    r"checkpoint|requested format",
    re.IGNORECASE,
)
_INDISPONIBLE = re.compile(
    r"unavailable|isn't available|is not available|has been (?:removed|deleted)|was deleted|"
    r"\bprivate\b|does not exist|\b404\b",
    re.IGNORECASE,
)
_TROP_LOURDE = re.compile(r"larger than max-filesize", re.IGNORECASE)


class YtdlpIndisponible(RuntimeError):
    """yt-dlp ne fonctionne pas (session exigee, limitation, version perimee...).
    Panne systemique : rien n'est exclu. Message affichable."""


class YtdlpEnAttente(PreparationDifferee):
    """Pause apres une panne, ou budget du passage epuise."""


def classer_erreur(sortie: str) -> str:
    if _SYSTEMIQUE.search(sortie):
        return SYSTEMIQUE
    if _INDISPONIBLE.search(sortie):
        return INDISPONIBLE
    # Dans le doute, la pause : exclure un article a tort est definitif.
    return SYSTEMIQUE


def lire_etat(chemin: Path) -> dict:
    try:
        donnees = json.loads(chemin.read_text())
    except Exception:  # noqa: BLE001 -- absent, illisible : pas d'etat, jamais fatal
        return {}
    return donnees if isinstance(donnees, dict) else {}


def _ecrire_etat(chemin: Path, donnees: dict) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    provisoire = chemin.with_suffix(chemin.suffix + ".tmp")
    provisoire.write_text(json.dumps(donnees))
    os.replace(provisoire, chemin)


def _date(valeur) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(valeur) if valeur else None
    except (TypeError, ValueError):
        return None


def _derniere_erreur(sortie: str) -> str:
    lignes = [ligne.strip() for ligne in sortie.splitlines() if ligne.strip()]
    erreurs = [ligne for ligne in lignes if ligne.startswith("ERROR:")]
    return ((erreurs or lignes or ["erreur sans message"])[-1])[:300]


class RecuperateurYtdlp:
    """Un par passage de l'agent : c'est lui qui porte le budget de telechargements."""

    def __init__(
        self,
        *,
        chemin_etat: Path | None = None,
        horloge=None,
        executer=subprocess.run,
        budget: int = TELECHARGEMENTS_PAR_PASSAGE,
        ffmpeg=None,
    ) -> None:
        self.chemin_etat = chemin_etat or settings.meta_token_path.parent / FICHIER_ETAT
        self.horloge = horloge or (lambda: dt.datetime.now(dt.timezone.utc))
        self.executer = executer
        self.restants = budget
        # yt-dlp fusionne video et audio avec ffmpeg, qu'il ne trouve pas dans le
        # PATH de launchd : on lui donne le chemin, cherche comme pour l'assemblage.
        self.ffmpeg = ffmpeg or (lambda: chemin_outil("ffmpeg"))

    def commande(self, url: str, dossier: Path, plafond: int) -> list[str]:
        return [
            sys.executable, "-m", "yt_dlp",
            *OPTIONS_FIXES,
            "--max-filesize", str(plafond),
            "--ffmpeg-location", self.ffmpeg(),
            "--print", "after_move:filepath", "--no-simulate",
            "-o", str(dossier / f"{NOM_SOURCE}.%(ext)s"),
            "--", url,
        ]

    def _echec(self, maintenant: dt.datetime, message: str) -> None:
        reprise = maintenant + PAUSE_APRES_ECHEC
        _ecrire_etat(self.chemin_etat, {
            **lire_etat(self.chemin_etat),
            "pause_jusqu_a": reprise.isoformat(),
            "derniere_erreur": message[:300],
            "echec_le": maintenant.isoformat(),
        })
        raise YtdlpIndisponible(
            f"yt-dlp en panne ({message}) : pause de {PAUSE_APRES_ECHEC.seconds // 60} minutes. "
            f"Si ca persiste, mettre yt-dlp a jour : {MISE_A_JOUR}"
        )

    def _fichier_produit(self, stdout: str, dossier: Path) -> Path | None:
        lignes = [ligne.strip() for ligne in stdout.splitlines() if ligne.strip()]
        if lignes:
            chemin = Path(lignes[-1])
            if chemin.parent.resolve() == dossier.resolve() and chemin.is_file():
                return chemin
        produits = [p for p in dossier.glob(f"{NOM_SOURCE}.*") if p.is_file()]
        return produits[0] if len(produits) == 1 else None

    def __call__(self, url: str, dossier: Path, plafond: int) -> bytes:
        if not est_lien_instagram(url):
            raise PreparationImpossible("yt-dlp n'est lance que sur un lien instagram.com")
        maintenant = self.horloge()
        etat = lire_etat(self.chemin_etat)
        pause = _date(etat.get("pause_jusqu_a"))
        if pause is not None and maintenant < pause:
            raise YtdlpEnAttente(
                f"yt-dlp en pause jusqu'a {pause:%H:%M} UTC apres une panne "
                f"({etat.get('derniere_erreur') or 'sans message'})"
            )
        if self.restants <= 0:
            raise YtdlpEnAttente("yt-dlp : un telechargement par passage au plus, repris au passage suivant")
        # Construite AVANT de consommer le budget : ffmpeg absent leve ici
        # (AssemblageIndisponible, panne systemique) sans rien ecrire.
        commande = self.commande(url, dossier, plafond)
        self.restants -= 1
        dossier.mkdir(parents=True, exist_ok=True)
        for ancien in dossier.glob(f"{NOM_SOURCE}.*"):
            ancien.unlink()

        try:
            resultat = self.executer(commande, capture_output=True, text=True, timeout=DELAI_MAX_S)
        except subprocess.TimeoutExpired:
            self._echec(maintenant, f"delai de {DELAI_MAX_S} s depasse")
        except OSError as exc:
            self._echec(maintenant, f"lancement impossible : {exc}")
        sortie = f"{resultat.stderr or ''}\n{resultat.stdout or ''}"

        if resultat.returncode != 0:
            if classer_erreur(sortie) == INDISPONIBLE:
                raise PreparationImpossible(
                    f"video Instagram indisponible (yt-dlp) : {_derniere_erreur(sortie)}"
                )
            self._echec(maintenant, _derniere_erreur(sortie))

        fichier = self._fichier_produit(resultat.stdout or "", dossier)
        if fichier is None:
            if _TROP_LOURDE.search(sortie):
                raise PreparationImpossible(f"video source trop lourde : plus de {plafond / MO:.0f} Mo")
            self._echec(maintenant, "yt-dlp n'a produit aucun fichier")
        try:
            octets = fichier.read_bytes()
        finally:
            # Le dossier du creneau sera servi par le tunnel : la source n'y reste pas.
            fichier.unlink(missing_ok=True)
        if len(octets) > plafond:
            raise PreparationImpossible(f"video source trop lourde : plus de {plafond / MO:.0f} Mo")
        _ecrire_etat(self.chemin_etat, {
            **etat, "pause_jusqu_a": None, "dernier_telechargement": maintenant.isoformat(),
        })
        return octets
