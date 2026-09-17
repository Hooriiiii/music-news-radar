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
from urllib.parse import urlsplit

import httpx

# launchd ne fournit qu'un PATH minimal, sans /opt/homebrew/bin : `which` y
# echoue alors que cloudflared est installe. Meme piege que ffmpeg dans
# app/content/video.py.
EMPLACEMENTS_CONNUS = ("/opt/homebrew/bin", "/usr/local/bin")
_URL_TUNNEL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
DELAI_URL_S = 45.0
DELAI_JOIGNABLE_S = 90.0
PAS_JOIGNABLE_S = 3.0
URL_DOH = "https://1.1.1.1/dns-query"


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


def resoudre_publiquement(hote: str, *, client) -> list[str]:
    try:
        reponse = client.get(
            URL_DOH,
            params={"name": hote, "type": "A"},
            headers={"accept": "application/dns-json"},
        )
        donnees = reponse.json()
    except Exception:  # noqa: BLE001 -- reseau ou JSON illisible : pas d'adresse
        return []
    return [reponse_dns["data"] for reponse_dns in donnees.get("Answer", []) if reponse_dns.get("type") == 1]


def attendre_joignable(
    url: str, *, client=None, dormir=time.sleep, delai: float = DELAI_JOIGNABLE_S
) -> None:
    # Le sous-domaine trycloudflare.com est fraichement cree : le resolveur
    # systeme de macOS met en cache un NXDOMAIN observe une fraction de
    # seconde avant que le DNS ne suive, et le sert ensuite pendant plus de
    # 55 s meme quand `curl` (qui ne partage pas ce cache) obtient deja un
    # 200. Meta, lui, resout depuis ses propres serveurs et n'a jamais ce
    # probleme : la seule question qui compte est « le DNS PUBLIC voit-il
    # deja le sous-domaine ? », jamais ce que macOS a en cache. D'ou une
    # resolution forcee par DNS-over-HTTPS (Cloudflare 1.1.1.1), et une
    # requete adressee directement a l'IP (Host + SNI poses a la main) pour
    # ne jamais repasser par `socket.getaddrinfo`.
    morceaux = urlsplit(url)
    hote, chemin = morceaux.hostname, morceaux.path or "/"
    propre = client is None
    client = client or httpx.Client(timeout=10)
    try:
        ecoule = 0.0
        while ecoule < delai:
            adresses = resoudre_publiquement(hote, client=client)
            if adresses:
                try:
                    reponse = client.request(
                        "GET",
                        f"https://{adresses[0]}{chemin}",
                        headers={"Host": hote},
                        extensions={"sni_hostname": hote},
                    )
                    if reponse.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
            dormir(PAS_JOIGNABLE_S)
            ecoule += PAS_JOIGNABLE_S
        raise HebergementIndisponible(f"tunnel ouvert mais {url} injoignable apres {delai:.0f} s")
    finally:
        if propre:
            client.close()


@contextlib.contextmanager
def heberger(dossier: Path, temoin: str):
    with serveur_local(dossier) as port, tunnel(port) as url:
        attendre_joignable(f"{url}/{temoin}")
        yield url
