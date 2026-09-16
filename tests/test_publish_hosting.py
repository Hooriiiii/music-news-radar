"""L'hebergement ephemere, sans jamais lancer le vrai cloudflared."""

import contextlib

import httpx
import pytest

import app.publish.hosting as mod
from app.publish.hosting import (
    HebergementIndisponible,
    attendre_joignable,
    chemin_cloudflared,
    extraire_url,
    serveur_local,
    tunnel,
)

LIGNES_CLOUDFLARED = [
    "2026-09-16T10:00:00Z INF Thank you for trying Cloudflare Tunnel.\n",
    "2026-09-16T10:00:01Z INF +----------------------------------------------------+\n",
    "2026-09-16T10:00:01Z INF |  https://calm-river-wave-bold.trycloudflare.com    |\n",
]


class FauxProcessus:
    def __init__(self, lignes, code=None):
        self.stdout = iter(lignes)
        self.code = code
        self.termine = False

    def poll(self):
        return self.code

    def terminate(self):
        self.termine = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.termine = True


def test_extraire_url_trouve_l_adresse_du_tunnel():
    assert extraire_url(LIGNES_CLOUDFLARED) == "https://calm-river-wave-bold.trycloudflare.com"
    assert extraire_url(["rien a voir\n"]) is None


def test_le_serveur_local_sert_les_fichiers_du_dossier(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"JPEG")
    with serveur_local(tmp_path) as port:
        reponse = httpx.get(f"http://127.0.0.1:{port}/a.jpg")
    assert (reponse.status_code, reponse.content) == (200, b"JPEG")
    assert reponse.headers["content-type"] == "image/jpeg"


def test_le_serveur_local_ne_liste_pas_le_dossier(tmp_path):
    """Le tunnel est public pendant quelques minutes : rien a lister."""
    (tmp_path / "a.jpg").write_bytes(b"x")
    with serveur_local(tmp_path) as port:
        assert httpx.get(f"http://127.0.0.1:{port}/").status_code == 404


def test_le_serveur_local_ne_sert_rien_hors_du_dossier(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    with serveur_local(public) as port:
        assert httpx.get(f"http://127.0.0.1:{port}/secret.txt").status_code == 404


def test_cloudflared_est_trouve_hors_du_path_de_launchd(tmp_path, monkeypatch):
    (tmp_path / "cloudflared").write_text("")
    monkeypatch.setattr(mod.shutil, "which", lambda nom: None)
    monkeypatch.setattr(mod, "EMPLACEMENTS_CONNUS", (str(tmp_path),))
    assert chemin_cloudflared() == str(tmp_path / "cloudflared")


def test_cloudflared_absent_dit_comment_l_installer(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda nom: None)
    monkeypatch.setattr(mod, "EMPLACEMENTS_CONNUS", ())
    with pytest.raises(HebergementIndisponible, match="brew install cloudflared"):
        chemin_cloudflared()


def test_le_tunnel_rend_son_url_puis_se_ferme(monkeypatch):
    monkeypatch.setattr(mod, "chemin_cloudflared", lambda: "/bin/cloudflared")
    processus = FauxProcessus(LIGNES_CLOUDFLARED)
    lances = []

    def lancer(commande, **kwargs):
        lances.append(commande)
        return processus

    with tunnel(8123, lancer=lancer) as url:
        assert url == "https://calm-river-wave-bold.trycloudflare.com"
        assert not processus.termine
    assert processus.termine
    assert lances == [["/bin/cloudflared", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8123"]]


def test_un_cloudflared_qui_s_arrete_sans_url_leve_et_se_ferme(monkeypatch):
    monkeypatch.setattr(mod, "chemin_cloudflared", lambda: "/bin/cloudflared")
    processus = FauxProcessus([], code=1)
    with pytest.raises(HebergementIndisponible, match="arrete"):
        with tunnel(8123, lancer=lambda commande, **kwargs: processus):
            pass
    assert processus.termine


def test_attendre_joignable_patiente_le_temps_que_le_dns_suive():
    reponses = [httpx.ConnectError("dns"), httpx.Response(404), httpx.Response(200)]

    def get(url, timeout=None):
        reponse = reponses.pop(0)
        if isinstance(reponse, Exception):
            raise reponse
        return reponse

    pauses = []
    attendre_joignable("https://x.trycloudflare.com/a.jpg", get=get, dormir=pauses.append)
    assert len(pauses) == 2


def test_attendre_joignable_abandonne_apres_le_delai():
    with pytest.raises(HebergementIndisponible, match="injoignable"):
        attendre_joignable(
            "https://x.trycloudflare.com/a.jpg",
            get=lambda url, timeout=None: httpx.Response(502),
            dormir=lambda s: None,
            delai=9,
        )


def test_heberger_combine_serveur_tunnel_et_verification(tmp_path, monkeypatch):
    (tmp_path / "a.jpg").write_bytes(b"x")
    ports = []

    @contextlib.contextmanager
    def faux_tunnel(port):
        ports.append(port)
        yield "https://t.trycloudflare.com"

    verifies = []
    monkeypatch.setattr(mod, "tunnel", faux_tunnel)
    monkeypatch.setattr(mod, "attendre_joignable", lambda url, **kwargs: verifies.append(url))

    with mod.heberger(tmp_path, "a.jpg") as url:
        assert url == "https://t.trycloudflare.com"
    assert verifies == ["https://t.trycloudflare.com/a.jpg"]
    assert ports[0] > 0
