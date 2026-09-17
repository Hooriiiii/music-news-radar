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
- au gel seulement, jamais a l'ingestion, jamais hors d'instagram.com en https ;
- un telechargement par passage de l'agent au plus ; apres une panne, 60 minutes
  de pause, etat dans `ytdlp_etat.json` a cote des autres fichiers d'etat.

Trois issues, trois traitements par le runner :
- `PreparationImpossible` : video indisponible, supprimee ou privee -- faute de
  l'ARTICLE, exclu, pas de pause (l'outil, lui, fonctionne). Aussi : trois
  pannes de suite sur le MEME lien, pour qu'un article maudit ne bloque pas la
  file indefiniment ;
- `YtdlpIndisponible` : tout le reste (session exigee, 429, 5xx, erreur reseau,
  erreur inconnue, delai) -- panne SYSTEMIQUE, notifiee une fois, pause de
  60 minutes ;
- `YtdlpEnAttente` : pause en cours ou budget du passage epuise -- une raison
  sur le creneau, aucune notification.
"""

import datetime as dt
import json
import os
import re
import signal
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
# Pannes systemiques de suite sur un meme permalink avant d'exclure l'article.
ECHECS_MAX_PAR_LIEN = 3
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
# Jamais, sous aucune forme (test sentinelle, et verifie a chaque commande).
OPTIONS_INTERDITES = frozenset({
    "--cookies", "--cookies-from-browser", "--username", "-u", "--password", "-p",
    "--netrc", "--netrc-location", "--netrc-cmd", "--video-password",
    "--ap-username", "--ap-password", "--client-certificate",
})

# Session, limitation, format, serveur, reseau : jamais la faute de la video.
# Teste AVANT l'indisponibilite -- le message de session d'Instagram dit aussi
# « not available », un 503 dit « Service Unavailable », une socket occupee
# « Resource temporarily unavailable ».
_SYSTEMIQUE = re.compile(
    r"login|log in|sign in|registered users|rate.?limit|too many requests|\b429\b|cookies|"
    r"checkpoint|requested format|HTTP Error 5\d\d|service unavailable|"
    r"temporarily unavailable|errno",
    re.IGNORECASE,
)
_INDISPONIBLE = re.compile(
    r"unavailable|isn't available|is not available|has been (?:removed|deleted)|was deleted|"
    r"\bprivate\b|does not exist|HTTP Error 404",
    re.IGNORECASE,
)
_TROP_LOURDE = re.compile(r"larger than max-filesize", re.IGNORECASE)
_FLUX_AVANT_FUSION = re.compile(rf"^{NOM_SOURCE}\.f\d+\.")


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


def _tuer_groupe(processus: subprocess.Popen) -> None:
    try:
        os.killpg(processus.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def executer_en_groupe(commande: list[str], *, capture_output: bool, text: bool, timeout: float):
    """`subprocess.run`, mais le sous-processus part dans sa propre session : au
    delai, on tue tout le GROUPE -- le ffmpeg que yt-dlp lance pour fusionner
    video et audio meurt avec lui, au lieu de survivre en orphelin."""
    sortie = subprocess.PIPE if capture_output else None
    with subprocess.Popen(
        commande, stdout=sortie, stderr=sortie, text=text, start_new_session=True
    ) as processus:
        try:
            stdout, stderr = processus.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _tuer_groupe(processus)
            processus.communicate()
            raise
        except BaseException:
            _tuer_groupe(processus)
            raise
    return subprocess.CompletedProcess(commande, processus.returncode, stdout, stderr)


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
        date = dt.datetime.fromisoformat(valeur) if valeur else None
    except (TypeError, ValueError):
        return None
    # Une date naive (etat ecrit a la main) est lue en UTC : jamais de TypeError
    # a la comparaison avec l'horloge, qui est en UTC.
    if date is not None and date.tzinfo is None:
        date = date.replace(tzinfo=dt.timezone.utc)
    return date


def _echecs(etat: dict) -> dict[str, int]:
    brut = etat.get("echecs")
    if not isinstance(brut, dict):
        return {}
    return {url: n for url, n in brut.items() if isinstance(url, str) and isinstance(n, int)}


def _derniere_erreur(sortie: str) -> str:
    lignes = [ligne.strip() for ligne in sortie.splitlines() if ligne.strip()]
    erreurs = [ligne for ligne in lignes if ligne.startswith("ERROR:")]
    return ((erreurs or lignes or ["erreur sans message"])[-1])[:300]


def _effacer_sources(dossier: Path) -> None:
    """Le dossier du creneau sera servi par le tunnel : aucune source, entiere ou
    partielle (`.part`, flux video ou audio avant fusion), n'y reste."""
    if not dossier.is_dir():
        return
    for fichier in dossier.glob(f"{NOM_SOURCE}.*"):
        fichier.unlink(missing_ok=True)


class RecuperateurYtdlp:
    """Un par passage de l'agent : c'est lui qui porte le budget de telechargements."""

    def __init__(
        self,
        *,
        chemin_etat: Path | None = None,
        horloge=None,
        executer=executer_en_groupe,
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
        commande = [
            sys.executable, "-m", "yt_dlp",
            *OPTIONS_FIXES,
            "--max-filesize", str(plafond),
            "--ffmpeg-location", self.ffmpeg(),
            "--print", "after_move:filepath", "--no-simulate",
            "-o", str(dossier / f"{NOM_SOURCE}.%(ext)s"),
            "--", url,
        ]
        interdites = {morceau.split("=", 1)[0] for morceau in commande} & OPTIONS_INTERDITES
        if interdites:
            raise ValueError(f"options interdites pour yt-dlp : {', '.join(sorted(interdites))}")
        return commande

    def _echec(self, maintenant: dt.datetime, url: str | None, message: str) -> None:
        """Toujours leve. `url` None : panne qui ne dit rien du lien (lancement
        impossible), non comptee contre l'article."""
        etat = lire_etat(self.chemin_etat)
        echecs = _echecs(etat)
        exclure = False
        if url is not None:
            echecs[url] = echecs.get(url, 0) + 1
            exclure = echecs[url] >= ECHECS_MAX_PAR_LIEN
            if exclure:
                del echecs[url]
        # La pause est posee meme quand l'article est exclu : l'erreur avait
        # l'air systemique, l'IP merite le repos.
        _ecrire_etat(self.chemin_etat, {
            **etat,
            "pause_jusqu_a": (maintenant + PAUSE_APRES_ECHEC).isoformat(),
            "derniere_erreur": message[:300],
            "echec_le": maintenant.isoformat(),
            "echecs": echecs,
        })
        if exclure:
            raise PreparationImpossible(
                f"video Instagram indisponible (yt-dlp) : {ECHECS_MAX_PAR_LIEN} pannes de suite "
                f"sur ce lien, derniere : {message}"
            )
        raise YtdlpIndisponible(
            f"yt-dlp en panne ({message}) : pause de {PAUSE_APRES_ECHEC.seconds // 60} minutes. "
            f"Si ca persiste, mettre yt-dlp a jour : {MISE_A_JOUR}"
        )

    def _oublier(self, url: str) -> None:
        etat = lire_etat(self.chemin_etat)
        echecs = _echecs(etat)
        if url in echecs:
            del echecs[url]
            _ecrire_etat(self.chemin_etat, {**etat, "echecs": echecs})

    def _fichier_produit(self, stdout: str, dossier: Path) -> Path | None:
        lignes = [ligne.strip() for ligne in stdout.splitlines() if ligne.strip()]
        if lignes:
            chemin = Path(lignes[-1])
            if chemin.parent.resolve() == dossier.resolve() and chemin.is_file():
                return chemin
        # Ni un morceau `.part`, ni un flux avant fusion (`source-ytdlp.f140.m4a`) :
        # seulement un fichier final.
        produits = sorted(
            p for p in dossier.glob(f"{NOM_SOURCE}.*")
            if p.is_file() and p.suffix != ".part" and not _FLUX_AVANT_FUSION.match(p.name)
        )
        mp4 = [p for p in produits if p.suffix.lower() == ".mp4"]
        if len(mp4) == 1:
            return mp4[0]
        return produits[0] if len(produits) == 1 else None

    def _telecharger(self, commande: list[str], url: str, dossier: Path, plafond: int, maintenant) -> bytes:
        try:
            resultat = self.executer(commande, capture_output=True, text=True, timeout=DELAI_MAX_S)
        except subprocess.TimeoutExpired:
            self._echec(maintenant, url, f"delai de {DELAI_MAX_S} s depasse")
        except OSError as exc:
            self._echec(maintenant, None, f"lancement impossible : {exc}")
        sortie = f"{resultat.stderr or ''}\n{resultat.stdout or ''}"

        if resultat.returncode != 0:
            if classer_erreur(sortie) == INDISPONIBLE:
                self._oublier(url)
                raise PreparationImpossible(
                    f"video Instagram indisponible (yt-dlp) : {_derniere_erreur(sortie)}"
                )
            self._echec(maintenant, url, _derniere_erreur(sortie))

        # Le refus de taille d'abord : un flux deja telecharge avant l'abandon ne
        # doit pas passer pour la video.
        if _TROP_LOURDE.search(sortie):
            raise PreparationImpossible(f"video source trop lourde : plus de {plafond / MO:.0f} Mo")
        fichier = self._fichier_produit(resultat.stdout or "", dossier)
        if fichier is None:
            self._echec(maintenant, url, "yt-dlp n'a produit aucun fichier")
        # La taille avant la lecture : ne jamais charger en memoire un fichier
        # dont on sait deja qu'il sera refuse.
        if fichier.stat().st_size > plafond:
            raise PreparationImpossible(f"video source trop lourde : plus de {plafond / MO:.0f} Mo")
        octets = fichier.read_bytes()
        if len(octets) > plafond:
            raise PreparationImpossible(f"video source trop lourde : plus de {plafond / MO:.0f} Mo")
        return octets

    def attente(self) -> YtdlpEnAttente | None:
        """L'attente a lever maintenant (pause ou budget epuise), sans rien lancer
        ni rien consommer : le gel la verifie avant de rendre la cover."""
        etat = lire_etat(self.chemin_etat)
        pause = _date(etat.get("pause_jusqu_a"))
        if pause is not None and self.horloge() < pause:
            return YtdlpEnAttente(
                f"yt-dlp en pause jusqu'a {pause:%H:%M} UTC apres une panne "
                f"({etat.get('derniere_erreur') or 'sans message'})"
            )
        if self.restants <= 0:
            return YtdlpEnAttente("yt-dlp : un telechargement par passage au plus, repris au passage suivant")
        return None

    def __call__(self, url: str, dossier: Path, plafond: int) -> bytes:
        if not est_lien_instagram(url):
            raise PreparationImpossible("yt-dlp n'est lance que sur un lien https instagram.com")
        maintenant = self.horloge()
        attente = self.attente()
        if attente is not None:
            raise attente
        # Construite AVANT de consommer le budget : ffmpeg absent leve ici
        # (AssemblageIndisponible, panne systemique) sans rien ecrire.
        commande = self.commande(url, dossier, plafond)
        self.restants -= 1
        dossier.mkdir(parents=True, exist_ok=True)
        _effacer_sources(dossier)
        try:
            octets = self._telecharger(commande, url, dossier, plafond, maintenant)
        finally:
            _effacer_sources(dossier)

        etat = lire_etat(self.chemin_etat)
        echecs = _echecs(etat)
        echecs.pop(url, None)
        _ecrire_etat(self.chemin_etat, {
            **etat,
            "pause_jusqu_a": None,
            "dernier_telechargement": maintenant.isoformat(),
            "echecs": echecs,
        })
        return octets
