"""yt-dlp au gel d'un reel : commande, budget, pause, classement des erreurs.

Aucun test ne lance yt-dlp ni ne sort sur Internet : l'executeur de
sous-processus est factice et rejoue ce que yt-dlp ecrirait.
"""

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.publish.preparation import PreparationDifferee, PreparationImpossible
from app.publish.ytdlp import (
    FICHIER_ETAT,
    INDISPONIBLE,
    MISE_A_JOUR,
    OPTIONS_INTERDITES,
    SYSTEMIQUE,
    RecuperateurYtdlp,
    YtdlpEnAttente,
    YtdlpIndisponible,
    classer_erreur,
    lire_etat,
)

MAINTENANT = dt.datetime(2026, 9, 20, 10, 0, tzinfo=dt.timezone.utc)
REEL = "https://www.instagram.com/reel/DAbc123/"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
# Le message reel de yt-dlp quand Instagram exige une session : il contient
# « not available » ET « login required » -- il doit rester systemique.
LOGIN = (
    "ERROR: [Instagram] DAbc123: Requested content is not available, rate-limit reached "
    "or login required. Use --cookies, --cookies-from-browser, --username and --password"
)


class FauxYtdlp:
    """Rejoue yt-dlp : ecrit le fichier demande par `-o` et imprime son chemin,
    ou echoue avec le message donne."""

    def __init__(self, *, code=0, stderr="", stdout=None, contenu=b"video-vp9", ecrire=True, lever=None):
        self.code, self.stderr, self.stdout = code, stderr, stdout
        self.contenu, self.ecrire, self.lever = contenu, ecrire, lever
        self.commandes = []

    def __call__(self, commande, *, capture_output, text, timeout):
        assert capture_output and text and timeout == 300
        self.commandes.append(commande)
        if self.lever is not None:
            raise self.lever
        chemin = Path(commande[commande.index("-o") + 1].replace("%(ext)s", "mp4"))
        stdout = self.stdout or ""
        if self.code == 0 and self.ecrire:
            chemin.write_bytes(self.contenu)
            stdout = f"{chemin}\n"
        return subprocess.CompletedProcess(commande, self.code, stdout=stdout, stderr=self.stderr)


def _recuperateur(tmp_path, executer, maintenant=MAINTENANT, budget=1):
    return RecuperateurYtdlp(
        chemin_etat=tmp_path / FICHIER_ETAT, horloge=lambda: maintenant, executer=executer,
        budget=budget, ffmpeg=lambda: FFMPEG,
    )


def test_rend_les_octets_et_ne_laisse_pas_la_source(tmp_path):
    """Le dossier du creneau sera servi par le tunnel : la source n'y reste pas."""
    dossier = tmp_path / "42"
    faux = FauxYtdlp()
    assert _recuperateur(tmp_path, faux)(REEL, dossier, 100_000_000) == b"video-vp9"
    assert list(dossier.iterdir()) == []
    assert lire_etat(tmp_path / FICHIER_ETAT)["dernier_telechargement"] == MAINTENANT.isoformat()


def test_la_commande(tmp_path):
    dossier = tmp_path / "42"
    commande = _recuperateur(tmp_path, FauxYtdlp()).commande(REEL, dossier, 1000)
    assert commande[:3] == [sys.executable, "-m", "yt_dlp"]
    for option in ("--ignore-config", "--no-cookies", "--no-cookies-from-browser", "--no-playlist"):
        assert option in commande
    assert commande[commande.index("--ffmpeg-location") + 1] == FFMPEG
    assert commande[commande.index("--max-filesize") + 1] == "1000"
    assert commande[commande.index("--merge-output-format") + 1] == "mp4"
    assert commande[commande.index("-o") + 1] == str(dossier / "source-ytdlp.%(ext)s")
    assert commande[-2:] == ["--", REEL]


def test_jamais_de_cookies_ni_de_connexion(tmp_path):
    """Decision de l'utilisateur (2026-09-17) : jamais de session Instagram."""
    commande = _recuperateur(tmp_path, FauxYtdlp()).commande(REEL, tmp_path, 1000)
    options = {morceau.split("=", 1)[0] for morceau in commande}
    assert not options & OPTIONS_INTERDITES
    assert {"--cookies", "--cookies-from-browser", "--username", "--password", "--netrc"} <= OPTIONS_INTERDITES


def test_un_seul_telechargement_par_passage(tmp_path):
    faux = FauxYtdlp()
    recuperer = _recuperateur(tmp_path, faux)
    recuperer(REEL, tmp_path / "1", 100_000_000)
    with pytest.raises(YtdlpEnAttente, match="un telechargement par passage") as attente:
        recuperer(REEL, tmp_path / "2", 100_000_000)
    assert isinstance(attente.value, PreparationDifferee)
    assert len(faux.commandes) == 1


def test_un_nouveau_passage_a_un_nouveau_budget(tmp_path):
    faux = FauxYtdlp()
    _recuperateur(tmp_path, faux)(REEL, tmp_path / "1", 100_000_000)
    _recuperateur(tmp_path, faux)(REEL, tmp_path / "2", 100_000_000)
    assert len(faux.commandes) == 2


def test_une_video_supprimee_est_une_faute_de_l_article_sans_pause(tmp_path):
    faux = FauxYtdlp(code=1, stderr="ERROR: [Instagram] DAbc123: This content isn't available")
    with pytest.raises(PreparationImpossible, match="indisponible"):
        _recuperateur(tmp_path, faux)(REEL, tmp_path / "1", 100_000_000)
    assert lire_etat(tmp_path / FICHIER_ETAT).get("pause_jusqu_a") is None
    # Le passage suivant peut telecharger : l'outil fonctionne, c'est la video qui manque.
    _recuperateur(tmp_path, FauxYtdlp())(REEL, tmp_path / "2", 100_000_000)


@pytest.mark.parametrize(
    "stderr",
    [LOGIN, "ERROR: HTTP Error 429: Too Many Requests", "ERROR: [Instagram] DAbc123: Unable to extract data"],
)
def test_le_reste_est_une_panne_systemique_avec_pause(tmp_path, stderr):
    with pytest.raises(YtdlpIndisponible) as panne:
        _recuperateur(tmp_path, FauxYtdlp(code=1, stderr=stderr))(REEL, tmp_path / "1", 100_000_000)
    assert MISE_A_JOUR in str(panne.value)
    etat = lire_etat(tmp_path / FICHIER_ETAT)
    assert etat["pause_jusqu_a"] == (MAINTENANT + dt.timedelta(minutes=60)).isoformat()
    assert etat["derniere_erreur"]


def test_pendant_la_pause_on_attend_sans_lancer_yt_dlp(tmp_path):
    with pytest.raises(YtdlpIndisponible):
        _recuperateur(tmp_path, FauxYtdlp(code=1, stderr=LOGIN))(REEL, tmp_path / "1", 100_000_000)
    faux = FauxYtdlp()
    plus_tard = MAINTENANT + dt.timedelta(minutes=30)
    with pytest.raises(YtdlpEnAttente, match="pause"):
        _recuperateur(tmp_path, faux, plus_tard)(REEL, tmp_path / "1", 100_000_000)
    assert faux.commandes == []
    apres = MAINTENANT + dt.timedelta(minutes=61)
    assert _recuperateur(tmp_path, faux, apres)(REEL, tmp_path / "1", 100_000_000) == b"video-vp9"
    assert lire_etat(tmp_path / FICHIER_ETAT).get("pause_jusqu_a") is None


def test_une_attente_ne_consomme_pas_le_budget(tmp_path):
    (tmp_path / FICHIER_ETAT).write_text(
        json.dumps({"pause_jusqu_a": (MAINTENANT + dt.timedelta(minutes=5)).isoformat()})
    )
    faux = FauxYtdlp()
    recuperer = _recuperateur(tmp_path, faux)
    with pytest.raises(YtdlpEnAttente):
        recuperer(REEL, tmp_path / "1", 100_000_000)
    assert recuperer.restants == 1


def test_un_delai_depasse_est_systemique(tmp_path):
    faux = FauxYtdlp(lever=subprocess.TimeoutExpired("yt_dlp", 300))
    with pytest.raises(YtdlpIndisponible, match="delai"):
        _recuperateur(tmp_path, faux)(REEL, tmp_path / "1", 100_000_000)
    assert lire_etat(tmp_path / FICHIER_ETAT)["pause_jusqu_a"]


def test_aucun_fichier_produit_est_systemique(tmp_path):
    with pytest.raises(YtdlpIndisponible, match="aucun fichier"):
        _recuperateur(tmp_path, FauxYtdlp(ecrire=False))(REEL, tmp_path / "1", 100_000_000)


def test_une_video_trop_lourde_est_une_faute_de_l_article(tmp_path):
    faux = FauxYtdlp(ecrire=False, stdout="[download] File is larger than max-filesize (2000 bytes > 1000 bytes). Aborting.")
    with pytest.raises(PreparationImpossible, match="trop lourde"):
        _recuperateur(tmp_path, faux)(REEL, tmp_path / "1", 1000)


def test_des_octets_au_dela_du_plafond_sont_refuses(tmp_path):
    """--max-filesize ne sait rien quand la taille n'est pas annoncee."""
    with pytest.raises(PreparationImpossible, match="trop lourde"):
        _recuperateur(tmp_path, FauxYtdlp(contenu=b"x" * 2000))(REEL, tmp_path / "1", 1000)
    assert list((tmp_path / "1").iterdir()) == []


def test_yt_dlp_n_est_jamais_lance_hors_d_instagram(tmp_path):
    faux = FauxYtdlp()
    recuperer = _recuperateur(tmp_path, faux)
    with pytest.raises(PreparationImpossible, match="instagram"):
        recuperer("https://evil.example/www.instagram.com/reel/A/", tmp_path / "1", 1000)
    assert (faux.commandes, recuperer.restants) == ([], 1)


def test_un_etat_illisible_ne_bloque_pas(tmp_path):
    (tmp_path / FICHIER_ETAT).write_text("{pas du json")
    assert _recuperateur(tmp_path, FauxYtdlp())(REEL, tmp_path / "1", 100_000_000) == b"video-vp9"


def test_l_etat_vit_a_cote_du_jeton():
    assert RecuperateurYtdlp().chemin_etat == settings.meta_token_path.parent / "ytdlp_etat.json"


@pytest.mark.parametrize(
    "sortie, attendu",
    [
        ("ERROR: [Instagram] DAbc123: This content isn't available", INDISPONIBLE),
        ("ERROR: [Instagram] DAbc123: Video unavailable", INDISPONIBLE),
        ("ERROR: [Instagram] DAbc123: This post has been deleted", INDISPONIBLE),
        ("ERROR: [Instagram] DAbc123: This account is private", INDISPONIBLE),
        ("ERROR: HTTP Error 404: Not Found", INDISPONIBLE),
        # Un code court qui contient 429 ou 404 n'est pas un code HTTP.
        ("ERROR: [Instagram] DC429x: This content isn't available", INDISPONIBLE),
        ("ERROR: [Instagram] DC404x: Unable to extract data", SYSTEMIQUE),
        (LOGIN, SYSTEMIQUE),
        ("ERROR: HTTP Error 429: Too Many Requests", SYSTEMIQUE),
        ("ERROR: [Instagram] DAbc123: Requested format is not available", SYSTEMIQUE),
        ("ERROR: quelque chose d'inattendu", SYSTEMIQUE),
        ("", SYSTEMIQUE),
    ],
)
def test_classer_erreur(sortie, attendu):
    assert classer_erreur(sortie) == attendu


def test_yt_dlp_est_une_dependance_epinglee():
    """Sentinelle : la version Homebrew 2025.12.08 echouait sur « login required »."""
    from yt_dlp.version import __version__

    assert __version__ >= "2026.08.19"
