"""Exposer, le temps d'une publication, les fichiers d'un post sur une URL publique.

Meta telecharge lui-meme les images en cURL : il leur faut une URL publique. On
n'expose PAS le dashboard (le port 8000 porte toute l'API, y compris
`DELETE /posts`) et on n'ouvre rien en permanence. Au moment de publier :

1. un mini-serveur local ne sert QUE le dossier du post, sans listing ;
2. un tunnel rapide `cloudflared` (sous-domaine aleatoire de trycloudflare.com,
   sans compte ni domaine) l'expose ;
3. on attend que l'URL reponde vraiment -- PATIEMMENT : la propagation DNS d'un
   sous-domaine frais prend 16 a 66 s (mesure du 2026-10-02), et relancer le
   tunnel jetterait une adresse presque visible (voir `heberger`) ;
4. tout est ferme des la publication terminee.
"""

import contextlib
import functools
import http.server
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# launchd ne fournit qu'un PATH minimal, sans /opt/homebrew/bin : `which` y
# echoue alors que cloudflared est installe. Meme piege que ffmpeg dans
# app/content/video.py.
EMPLACEMENTS_CONNUS = ("/opt/homebrew/bin", "/usr/local/bin")
_URL_TUNNEL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
DELAI_URL_S = 45.0
# Patience accordee a UN sous-domaine. Mesure du 2026-10-02 (n=5, en direct) : la
# propagation DNS d'un sous-domaine trycloudflare frais prend 16 a 66 s, soit
# jusqu'a 73 % de l'ancien budget de 90 s -- et les 57 echecs mesures tombaient
# tous sous ces 90 s. Attendre une propagation n'est pas attendre une panne :
# 180 s, c'est 2,7 fois la pire propagation mesuree. Le reste du budget total
# (270 s, inchange) finance un RELANCEMENT, reserve aux pannes ou patienter n'est
# pas le remede (voir `heberger`).
DELAI_JOIGNABLE_S = 180.0
PAS_JOIGNABLE_S = 3.0
URL_DOH = "https://1.1.1.1/dns-query"


class HebergementIndisponible(RuntimeError):
    """cloudflared absent, tunnel non ouvert ou injoignable. Message affichable.

    C'est une panne de la MACHINE (binaire, reseau, infrastructure Cloudflare),
    jamais un defaut de l'article : `app/schedule/runner.py` s'en sert pour ne
    PAS exclure l'article de la file.
    """


class CloudflaredIntrouvable(HebergementIndisponible):
    """Le binaire manque sur la machine. Rien a reessayer : trois relancements ne
    l'installeraient pas. `heberger` la relaie telle quelle, immediatement."""


class TunnelInjoignable(HebergementIndisponible):
    """Le tunnel s'est ouvert mais son URL n'a pas repondu dans le budget.

    Panne transitoire : un tunnel rapide met parfois plus que le budget a etre
    visible du DNS public. La garantie du runner tient dans tous les cas : rien
    n'est parti chez Meta (le conteneur n'est cree qu'une fois le tunnel
    debout), donc retenter ne peut pas publier deux fois.

    Deux attributs portent la DECISION de `heberger`, et pas seulement le
    message : `dns_resolu` dit laquelle des deux pannes a eu lieu (le DNS public
    n'a jamais vu le sous-domaine -> patienter etait le remede, et il a ete
    epuise ; le sous-domaine est resolu mais ne rend jamais 200 -> c'est
    cloudflared ou le serveur local, patienter ne guerit rien, relancer peut).
    `ecoule` est le temps de patience consomme, pour que le budget total ne
    puisse pas etre depasse par une suite de reessais.
    """

    def __init__(self, message: str, *, dns_resolu: bool = False, ecoule: float = 0.0):
        super().__init__(message)
        self.dns_resolu = dns_resolu
        self.ecoule = ecoule


def chemin_cloudflared() -> str:
    chemin = shutil.which("cloudflared")
    if chemin is None:
        chemin = next(
            (str(Path(d) / "cloudflared") for d in EMPLACEMENTS_CONNUS if (Path(d) / "cloudflared").exists()),
            None,
        )
    if chemin is None:
        raise CloudflaredIntrouvable(
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


def _arreter(processus) -> None:
    """Tue le GROUPE de processus : cloudflared est lance dans sa propre session
    (`start_new_session`), et un essai suivant doit repartir sur une table rase --
    un enfant survivant garderait le tunnel precedent ouvert."""
    try:
        os.killpg(os.getpgid(processus.pid), signal.SIGTERM)
    except Exception:  # noqa: BLE001 -- pas de groupe (ou faux processus) : on se rabat
        processus.terminate()
    try:
        processus.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(processus.pid), signal.SIGKILL)
        processus.kill()


@contextlib.contextmanager
def tunnel(port: int, *, lancer=subprocess.Popen):
    processus = lancer(
        [chemin_cloudflared(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        yield _lire_url(processus)
    finally:
        _arreter(processus)


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
    # DEUX pannes se cachaient sous le meme message « injoignable apres 90 s » :
    # le DNS public qui ne voit toujours pas le sous-domaine, et le sous-domaine
    # resolu dont la requete ne rend jamais 200. Elles n'appellent pas la meme
    # reponse -- patienter plus longtemps d'un cote, regarder cloudflared et le
    # serveur local de l'autre -- et 57 echecs complets en deux semaines
    # (mesure du 2026-10-02, 11 creneaux manques) n'avaient rien appris la-dessus.
    # Le temps annonce est desormais celui du MUR : la boucle ne compte que ses
    # sommeils, donc « apres 90 s » n'etait pas la duree reellement ecoulee.
    depart = time.monotonic()
    interrogations = 0
    resolu_en: float | None = None
    dernier_refus: str | None = None
    try:
        ecoule = 0.0
        while ecoule < delai:
            adresses = resoudre_publiquement(hote, client=client)
            interrogations += 1
            if adresses:
                if resolu_en is None:
                    resolu_en = time.monotonic() - depart
                try:
                    reponse = client.request(
                        "GET",
                        f"https://{adresses[0]}{chemin}",
                        headers={"Host": hote},
                        extensions={"sni_hostname": hote},
                    )
                    if reponse.status_code == 200:
                        return
                    dernier_refus = f"HTTP {reponse.status_code}"
                except httpx.HTTPError as exc:
                    dernier_refus = type(exc).__name__
            dormir(PAS_JOIGNABLE_S)
            ecoule += PAS_JOIGNABLE_S
        if resolu_en is None:
            cause = f"DNS public ne l'a jamais vu ({interrogations} interrogation(s))"
        else:
            cause = (
                f"DNS public l'a vu en {resolu_en:.0f} s mais aucune reponse 200"
                f" (dernier refus : {dernier_refus or 'aucun'})"
            )
        mur = time.monotonic() - depart
        raise TunnelInjoignable(
            f"tunnel ouvert mais {url} injoignable apres {mur:.0f} s : {cause}",
            dns_resolu=resolu_en is not None,
            # Le temps COMPTE (somme des sommeils), pas l'horloge murale : c'est
            # lui qui borne le budget de `heberger`, et il reste exact quand les
            # tests injectent `dormir`.
            ecoule=ecoule,
        )
    finally:
        if propre:
            client.close()


@contextlib.contextmanager
def heberger(
    dossier: Path,
    temoin: str,
    *,
    essais: int | None = None,
    delai: float = DELAI_JOIGNABLE_S,
    budget: float | None = None,
):
    """Le dossier expose derriere un tunnel JOIGNABLE, ou rien.

    PATIENCE plutot que reessais (2026-10-02). L'ancienne strategie ouvrait trois
    tunnels ENTIERS de 90 s, donc trois sous-domaines : chaque reessai jetait une
    adresse probablement sur le point d'etre visible et repartait de zero. Or la
    mesure dit que l'attente est une PROPAGATION DNS (16 a 66 s, n=5), pas une
    panne -- la reprise travaillait contre elle-meme. Desormais on attend
    longuement sur UN sous-domaine (`delai`, 180 s) et on ne relance que ce qui le
    merite :

    - le tunnel n'a JAMAIS demarre (cloudflared mort au lancement, aucune URL) :
      il n'y a pas d'adresse a attendre, on relance sans toucher au budget ;
    - le sous-domaine est RESOLU mais ne rend jamais 200 (`dns_resolu`) :
      patienter ne guerit pas cette panne-la, un tunnel neuf peut.

    Quand le DNS public n'a jamais vu le sous-domaine, en revanche, la patience
    ETAIT le remede et elle a ete epuisee : on abandonne le passage plutot que
    d'offrir a un sous-domaine neuf un reste de budget trop court pour sa propre
    propagation. Le runner retentera au passage suivant, sans exclure l'article.

    Borne de temps INCHANGEE : le budget total de patience vaut
    `essais` x `delai` d'avant (3 x 90 = 270 s), et le nombre de tunnels lances
    reste plafonne a `essais` -- donc la lecture d'URL (`DELAI_URL_S`) est bornee
    comme avant elle aussi. Rien ne peut faire deborder les 90 minutes de retard
    au-dela desquelles un creneau devient MANQUE.

    Le serveur local vit pour tout le `with` ; seul le tunnel est relance. Le
    conteneur Meta n'est cree que DANS ce `with`, donc apres qu'un tunnel repond :
    un relancement ne peut jamais publier deux fois.
    """
    essais = essais if essais is not None else settings.publication_tunnel_essais
    tunnels_max = max(essais, 1)
    budget = budget if budget is not None else settings.publication_tunnel_budget_s
    reste = float(budget)
    # Le detail du DERNIER essai voyage jusqu'au message final : c'est lui que le
    # runner inscrit dans `creneaux.erreur`, donc la seule trace qui survivra a la
    # rotation des logs. Sans lui, la base disait « aucun tunnel joignable » et il
    # fallait recroiser `publication.err.log` pour savoir ce qui avait echoue.
    dernier: str | None = None
    lances = 0
    with serveur_local(dossier) as port:
        while lances < tunnels_max and reste > 0:
            lances += 1
            with contextlib.ExitStack() as ouvert:
                try:
                    url = ouvert.enter_context(tunnel(port))
                except CloudflaredIntrouvable:
                    # Rien a reessayer : le binaire ne va pas apparaitre.
                    raise
                except HebergementIndisponible as exc:
                    # Jamais demarre : aucune adresse n'attend sa propagation.
                    logger.warning("tunnel %d/%d non demarre : %s", lances, tunnels_max, exc)
                    dernier = str(exc)
                    continue
                patience = min(delai, reste)
                try:
                    attendre_joignable(f"{url}/{temoin}", delai=patience)
                except TunnelInjoignable as exc:
                    # Sortie de l'ExitStack : le tunnel est tue (par GROUPE) avant
                    # le suivant.
                    logger.warning("tunnel %d/%d : %s", lances, tunnels_max, exc)
                    dernier = str(exc)
                    reste -= exc.ecoule or patience
                    if exc.dns_resolu:
                        continue
                    break
                logger.info("tunnel joignable au tunnel %d/%d : %s", lances, tunnels_max, url)
                yield url
                return
    detail = f" ; dernier essai : {dernier}" if dernier else ""
    raise TunnelInjoignable(
        f"aucun tunnel joignable : {lances} tunnel(s), {budget:.0f} s de patience{detail}"
    )
