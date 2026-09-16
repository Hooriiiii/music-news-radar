"""Exposer, le temps d'une publication, les fichiers d'un post sur une URL publique.

Meta telecharge lui-meme les images en cURL : il leur faut une URL publique. On
n'expose PAS le dashboard (le port 8000 porte toute l'API, y compris
`DELETE /posts`) et on n'ouvre rien en permanence. Au moment de publier :

1. un mini-serveur local ne sert QUE le dossier du post, sans listing ;
2. un tunnel rapide `cloudflared` (sous-domaine aleatoire de trycloudflare.com,
   sans compte ni domaine) l'expose ;
3. on attend que l'URL reponde vraiment (le DNS du sous-domaine suit avec
   quelques secondes de retard) ;
4. tout est ferme des la publication terminee.
"""

import contextlib
import functools
import http.server
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx

# launchd ne fournit qu'un PATH minimal, sans /opt/homebrew/bin : `which` y
# echoue alors que cloudflared est installe. Meme piege que ffmpeg dans
# app/content/video.py.
EMPLACEMENTS_CONNUS = ("/opt/homebrew/bin", "/usr/local/bin")
_URL_TUNNEL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
DELAI_URL_S = 45.0
DELAI_JOIGNABLE_S = 90.0
PAS_JOIGNABLE_S = 3.0


class HebergementIndisponible(RuntimeError):
    """cloudflared absent, tunnel non ouvert ou injoignable. Message affichable."""


def chemin_cloudflared() -> str:
    chemin = shutil.which("cloudflared")
    if chemin is None:
        chemin = next(
            (str(Path(d) / "cloudflared") for d in EMPLACEMENTS_CONNUS if (Path(d) / "cloudflared").exists()),
            None,
        )
    if chemin is None:
        raise HebergementIndisponible(
            "cloudflared est introuvable. La publication en a besoin pour exposer les "
            "images a Meta : `brew install cloudflared`."
        )
    return chemin


class _SansListe(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(404)
        return None

    def log_message(self, format, *args):  # noqa: A002 -- signature imposee
        pass


@contextlib.contextmanager
def serveur_local(dossier: Path):
    gestionnaire = functools.partial(_SansListe, directory=str(dossier))
    # 127.0.0.1 et port 0 : jamais expose au reseau local, port libre choisi par le systeme.
    serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), gestionnaire)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    try:
        yield serveur.server_address[1]
    finally:
        serveur.shutdown()
        serveur.server_close()


def extraire_url(lignes) -> str | None:
    for ligne in lignes:
        trouve = _URL_TUNNEL.search(ligne)
        if trouve:
            return trouve.group(0)
    return None


def _lire_url(processus, delai: float = DELAI_URL_S) -> str:
    # Lecture dans un fil : lire stdout directement bloquerait sans limite de temps.
    lignes: queue.Queue = queue.Queue()

    def lire():
        for ligne in processus.stdout:
            lignes.put(ligne)

    threading.Thread(target=lire, daemon=True).start()
    limite = time.monotonic() + delai
    while time.monotonic() < limite:
        try:
            ligne = lignes.get(timeout=0.5)
        except queue.Empty:
            if processus.poll() is not None:
                raise HebergementIndisponible("cloudflared s'est arrete avant d'ouvrir le tunnel") from None
            continue
        url = extraire_url([ligne])
        if url:
            return url
    raise HebergementIndisponible(f"cloudflared n'a pas fourni d'URL en {delai:.0f} s")


@contextlib.contextmanager
def tunnel(port: int, *, lancer=subprocess.Popen):
    processus = lancer(
        [chemin_cloudflared(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        yield _lire_url(processus)
    finally:
        processus.terminate()
        try:
            processus.wait(timeout=10)
        except subprocess.TimeoutExpired:
            processus.kill()


def attendre_joignable(
    url: str, *, get=httpx.get, dormir=time.sleep, delai: float = DELAI_JOIGNABLE_S
) -> None:
    ecoule = 0.0
    while ecoule < delai:
        try:
            if get(url, timeout=10).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        dormir(PAS_JOIGNABLE_S)
        ecoule += PAS_JOIGNABLE_S
    raise HebergementIndisponible(f"tunnel ouvert mais {url} injoignable apres {delai:.0f} s")


@contextlib.contextmanager
def heberger(dossier: Path, temoin: str):
    with serveur_local(dossier) as port, tunnel(port) as url:
        attendre_joignable(f"{url}/{temoin}")
        yield url
