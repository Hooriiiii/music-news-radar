"""L'hebergement ephemere, sans jamais lancer le vrai cloudflared."""

import contextlib
import socket

import httpx
import pytest

import app.publish.hosting as mod
from app.publish.hosting import (
    HebergementIndisponible,
    attendre_joignable,
    chemin_cloudflared,
    extraire_url,
    resoudre_publiquement,
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


def _reponse_dns(adresses):
    return httpx.Response(200, json={"Answer": [{"type": 1, "data": ip} for ip in adresses]})


class FauxClientDns:
    """Un client DoH+sonde jetable : jamais de vrai reseau, jamais le resolveur systeme."""

    def __init__(self, sequence_dns, sequence_sondes=None):
        self.sequence_dns = sequence_dns
        self.sequence_sondes = sequence_sondes if sequence_sondes is not None else []
        self.appels_dns = []
        self.appels_sonde = []

    def get(self, url, params=None, headers=None):
        assert url == mod.URL_DOH
        self.appels_dns.append(params)
        reponse = self.sequence_dns.pop(0)
        if isinstance(reponse, Exception):
            raise reponse
        return reponse

    def request(self, methode, url, headers=None, extensions=None):
        self.appels_sonde.append((methode, url, headers, extensions))
        reponse = self.sequence_sondes.pop(0)
        if isinstance(reponse, Exception):
            raise reponse
        return reponse


def test_attendre_joignable_patiente_que_le_dns_public_suive_puis_sonde():
    client = FauxClientDns(
        sequence_dns=[_reponse_dns([]), _reponse_dns([]), _reponse_dns(["203.0.113.9"])],
        sequence_sondes=[httpx.Response(200)],
    )
    pauses = []
    attendre_joignable("https://x.trycloudflare.com/a.jpg", client=client, dormir=pauses.append)
    assert len(pauses) == 2
    methode, url, headers, extensions = client.appels_sonde[0]
    assert (methode, url) == ("GET", "https://203.0.113.9/a.jpg")
    assert headers == {"Host": "x.trycloudflare.com"}
    assert extensions == {"sni_hostname": "x.trycloudflare.com"}


def test_attendre_joignable_abandonne_apres_le_delai():
    client = FauxClientDns(sequence_dns=[_reponse_dns([]) for _ in range(10)])
    with pytest.raises(HebergementIndisponible, match="injoignable"):
        attendre_joignable(
            "https://x.trycloudflare.com/a.jpg",
            client=client,
            dormir=lambda s: None,
            delai=9,
        )


def test_attendre_joignable_n_utilise_jamais_le_resolveur_systeme(monkeypatch):
    def echoue(*args, **kwargs):
        raise AssertionError("le resolveur systeme ne doit jamais etre appele")

    monkeypatch.setattr(socket, "getaddrinfo", echoue)
    client = FauxClientDns(
        sequence_dns=[_reponse_dns(["203.0.113.9"])],
        sequence_sondes=[httpx.Response(200)],
    )
    attendre_joignable("https://x.trycloudflare.com/a.jpg", client=client, dormir=lambda s: None)


def test_resoudre_publiquement_renvoie_une_liste_vide_si_le_doh_echoue():
    client = FauxClientDns(sequence_dns=[httpx.ConnectError("reseau coupe")])
    assert resoudre_publiquement("x.trycloudflare.com", client=client) == []


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


def _tunnel_compte(ouverts, fermes):
    """Un tunnel factice qui numerote ses ouvertures : un tunnel rapide recoit un
    NOUVEAU sous-domaine a chaque lancement."""

    @contextlib.contextmanager
    def faux_tunnel(port):
        numero = len(ouverts) + 1
        url = f"https://t{numero}.trycloudflare.com"
        ouverts.append(url)
        try:
            yield url
        finally:
            fermes.append(url)

    return faux_tunnel


def test_heberger_reessaie_avec_un_nouveau_tunnel_quand_le_premier_est_injoignable(tmp_path, monkeypatch):
    (tmp_path / "a.jpg").write_bytes(b"x")
    ouverts, fermes, verifies = [], [], []
    monkeypatch.setattr(mod, "tunnel", _tunnel_compte(ouverts, fermes))

    def verifier(url, **kwargs):
        verifies.append(url)
        if len(verifies) == 1:
            raise mod.TunnelInjoignable(f"tunnel ouvert mais {url} injoignable apres 90 s")

    monkeypatch.setattr(mod, "attendre_joignable", verifier)

    with mod.heberger(tmp_path, "a.jpg", essais=3) as url:
        assert url == "https://t2.trycloudflare.com"
        # Le premier tunnel est ferme AVANT d'en ouvrir un second.
        assert fermes == ["https://t1.trycloudflare.com"]
    assert ouverts == ["https://t1.trycloudflare.com", "https://t2.trycloudflare.com"]
    assert fermes == ouverts
    assert verifies == ["https://t1.trycloudflare.com/a.jpg", "https://t2.trycloudflare.com/a.jpg"]


def test_heberger_abandonne_apres_le_nombre_d_essais_et_ferme_tout(tmp_path, monkeypatch):
    (tmp_path / "a.jpg").write_bytes(b"x")
    ouverts, fermes = [], []
    monkeypatch.setattr(mod, "tunnel", _tunnel_compte(ouverts, fermes))

    def verifier(url, **kwargs):
        raise mod.TunnelInjoignable(f"tunnel ouvert mais {url} injoignable apres 90 s")

    monkeypatch.setattr(mod, "attendre_joignable", verifier)
    with pytest.raises(mod.TunnelInjoignable, match="3 essai"):
        with mod.heberger(tmp_path, "a.jpg", essais=3):
            raise AssertionError("aucun tunnel n'etait joignable")
    assert len(ouverts) == 3
    assert fermes == ouverts


def test_heberger_prend_le_nombre_d_essais_dans_la_configuration(tmp_path, monkeypatch):
    (tmp_path / "a.jpg").write_bytes(b"x")
    ouverts, fermes = [], []
    monkeypatch.setattr(mod, "tunnel", _tunnel_compte(ouverts, fermes))
    monkeypatch.setattr(mod, "attendre_joignable", _injoignable)
    monkeypatch.setattr(mod.settings, "publication_tunnel_essais", 2)
    with pytest.raises(mod.TunnelInjoignable):
        with mod.heberger(tmp_path, "a.jpg"):
            pass
    assert len(ouverts) == 2


def _injoignable(url, **kwargs):
    raise mod.TunnelInjoignable(f"tunnel ouvert mais {url} injoignable")


def test_un_tunnel_injoignable_est_une_panne_d_hebergement():
    """Le runner s'en sert pour NE PAS exclure l'article : un tunnel injoignable
    est une panne machine, pas la faute de l'actu."""
    assert issubclass(mod.TunnelInjoignable, HebergementIndisponible)
    client = FauxClientDns(sequence_dns=[_reponse_dns([]) for _ in range(10)])
    with pytest.raises(mod.TunnelInjoignable):
        attendre_joignable(
            "https://x.trycloudflare.com/a.jpg", client=client, dormir=lambda s: None, delai=3
        )


def test_le_tunnel_est_tue_par_groupe_de_processus(monkeypatch):
    """cloudflared est lance dans sa propre session : le groupe entier est tue,
    sinon un enfant survivrait et garderait le port."""
    monkeypatch.setattr(mod, "chemin_cloudflared", lambda: "/bin/cloudflared")
    processus = FauxProcessus(LIGNES_CLOUDFLARED)
    processus.pid = 4242
    options = {}
    tues = []
    monkeypatch.setattr(mod.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(mod.os, "killpg", lambda groupe, signal_: tues.append((groupe, signal_)))

    def lancer(commande, **kwargs):
        options.update(kwargs)
        return processus

    with tunnel(8123, lancer=lancer):
        pass
    assert options["start_new_session"] is True
    assert tues == [(4242, mod.signal.SIGTERM)]
