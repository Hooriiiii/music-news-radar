"""Agrandissement du fond par super-resolution (Tache 6, 2026-09-23).

Aucun test n'invoque le vrai binaire upscayl-bin : `subprocess.run` est
toujours remplace par un faux qui ecrit l'octet attendu au bon endroit, ou
simule une panne -- la suite doit passer sur une machine ou Upscayl n'est pas
installe (c'etait d'ailleurs le cas de cette machine avant que l'utilisateur
ne l'installe pour la mesure du 2026-09-23).
"""

import io
import subprocess

import pytest
from PIL import Image

from app.content import upscale


def _png(largeur, hauteur):
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (5, 10, 15)).save(tampon, "PNG")
    return tampon.getvalue()


def _outils(tmp_path):
    """Un binaire et un dossier de modeles PRESENTS (fichiers vides : seule
    leur existence est verifiee avant de lancer un sous-processus)."""
    binaire = tmp_path / "upscayl-bin"
    binaire.write_bytes(b"")
    modeles = tmp_path / "models"
    modeles.mkdir()
    (modeles / "upscayl-lite-4x.param").write_bytes(b"")
    return binaire, modeles


def _chemin_sortie(cmd: list[str]) -> str:
    return cmd[cmd.index("-o") + 1]


def _faux_subprocess_run(*, sortie_px=(2640, 1760)):
    def run(cmd, **kwargs):
        with open(_chemin_sortie(cmd), "wb") as f:
            f.write(_png(*sortie_px))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    return run


# --- binaire_disponible -----------------------------------------------------


def test_binaire_disponible_vrai_quand_binaire_et_modeles_existent(tmp_path):
    binaire, modeles = _outils(tmp_path)
    assert upscale.binaire_disponible(binaire=binaire, modeles=modeles) is True


def test_binaire_disponible_faux_si_le_binaire_manque(tmp_path):
    _, modeles = _outils(tmp_path)
    assert upscale.binaire_disponible(binaire=tmp_path / "absent", modeles=modeles) is False


def test_binaire_disponible_faux_si_les_modeles_manquent(tmp_path):
    binaire, _ = _outils(tmp_path)
    assert upscale.binaire_disponible(binaire=binaire, modeles=tmp_path / "absent") is False


# --- agrandir : panne machine, jamais une exception opaque -----------------


def test_agrandir_leve_une_erreur_lisible_si_le_binaire_manque(tmp_path):
    _, modeles = _outils(tmp_path)
    with pytest.raises(upscale.UpscaleIndisponible, match="introuvable"):
        upscale.agrandir(b"x", binaire=tmp_path / "absent", modeles=modeles)


def test_agrandir_leve_une_erreur_lisible_si_les_modeles_manquent(tmp_path):
    binaire, _ = _outils(tmp_path)
    with pytest.raises(upscale.UpscaleIndisponible, match="modeles"):
        upscale.agrandir(b"x", binaire=binaire, modeles=tmp_path / "absent")


def test_agrandir_leve_sur_un_code_de_retour_non_nul(tmp_path, monkeypatch):
    binaire, modeles = _outils(tmp_path)

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"vulkan device not found")

    monkeypatch.setattr(upscale.subprocess, "run", run)
    with pytest.raises(upscale.UpscaleIndisponible, match="vulkan device not found"):
        upscale.agrandir(_png(660, 440), binaire=binaire, modeles=modeles)


def test_agrandir_leve_sur_une_sortie_absente(tmp_path, monkeypatch):
    """Code de retour a 0 mais rien n'a ete ecrit : jamais suppose un succes
    sur la seule foi du code de retour."""
    binaire, modeles = _outils(tmp_path)

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(upscale.subprocess, "run", run)
    with pytest.raises(upscale.UpscaleIndisponible, match="echec"):
        upscale.agrandir(_png(660, 440), binaire=binaire, modeles=modeles)


def test_agrandir_leve_sur_un_delai_depasse(tmp_path, monkeypatch):
    binaire, modeles = _outils(tmp_path)

    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(upscale.subprocess, "run", run)
    with pytest.raises(upscale.UpscaleIndisponible, match="delai"):
        upscale.agrandir(_png(660, 440), binaire=binaire, modeles=modeles, delai_max_s=1.0)


def test_agrandir_leve_si_le_binaire_n_est_pas_executable(tmp_path, monkeypatch):
    binaire, modeles = _outils(tmp_path)

    def run(cmd, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(upscale.subprocess, "run", run)
    with pytest.raises(upscale.UpscaleIndisponible, match="inexecutable"):
        upscale.agrandir(_png(660, 440), binaire=binaire, modeles=modeles)


def test_agrandir_ne_laisse_rien_sur_le_disque_apres_un_echec(tmp_path, monkeypatch):
    """Le dossier temporaire de travail est nettoye meme apres une panne."""
    binaire, modeles = _outils(tmp_path)
    avant = set(tmp_path.iterdir())

    def run(cmd, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(upscale.subprocess, "run", run)
    with pytest.raises(upscale.UpscaleIndisponible):
        upscale.agrandir(_png(660, 440), binaire=binaire, modeles=modeles)

    assert set(tmp_path.iterdir()) == avant


# --- agrandir : succes -------------------------------------------------------


def test_agrandir_rend_les_octets_produits_par_l_outil(tmp_path, monkeypatch):
    binaire, modeles = _outils(tmp_path)
    monkeypatch.setattr(upscale.subprocess, "run", _faux_subprocess_run(sortie_px=(2640, 1760)))

    resultat = upscale.agrandir(_png(660, 440), binaire=binaire, modeles=modeles)

    assert Image.open(io.BytesIO(resultat)).size == (2640, 1760)


def test_agrandir_passe_le_modele_et_l_echelle_configures(tmp_path, monkeypatch):
    binaire, modeles = _outils(tmp_path)
    captures = []

    def run(cmd, **kwargs):
        captures.append(cmd)
        with open(_chemin_sortie(cmd), "wb") as f:
            f.write(_png(100, 100))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(upscale.subprocess, "run", run)
    upscale.agrandir(_png(10, 10), binaire=binaire, modeles=modeles, modele="upscayl-lite-4x", echelle=4)

    cmd = captures[0]
    assert cmd[0] == str(binaire)
    assert "-n" in cmd and cmd[cmd.index("-n") + 1] == "upscayl-lite-4x"
    assert "-s" in cmd and cmd[cmd.index("-s") + 1] == "4"
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == str(modeles)


# --- agrandir_fond : reduction apres coup -----------------------------------


def test_agrandir_fond_reduit_un_resultat_trop_grand(tmp_path, monkeypatch):
    """4x sur 660x440 donne 2640x1760 -- bien plus que ce qu'aucun gabarit
    n'exige (1080x1920 au maximum, le reel) : `agrandir_fond` le ramene sous
    la borne, sans jamais re-agrandir."""
    binaire, modeles = _outils(tmp_path)
    monkeypatch.setattr(upscale.subprocess, "run", _faux_subprocess_run(sortie_px=(2640, 1760)))

    resultat = upscale.agrandir_fond(
        _png(660, 440), binaire=binaire, modeles=modeles, sortie_max_px=1920
    )

    largeur, hauteur = Image.open(io.BytesIO(resultat)).size
    assert max(largeur, hauteur) <= 1920
    # Aspect ratio conserve (660/440 = 1,5).
    assert abs(largeur / hauteur - 660 / 440) < 0.01


def test_agrandir_fond_ne_grandit_jamais_un_resultat_deja_petit(tmp_path, monkeypatch):
    binaire, modeles = _outils(tmp_path)
    monkeypatch.setattr(upscale.subprocess, "run", _faux_subprocess_run(sortie_px=(800, 600)))

    resultat = upscale.agrandir_fond(
        _png(200, 150), binaire=binaire, modeles=modeles, sortie_max_px=1920
    )

    assert Image.open(io.BytesIO(resultat)).size == (800, 600)


def test_agrandir_fond_rend_du_jpeg(tmp_path, monkeypatch):
    """Le fond est ecrit dans le cache disque comme les autres copies locales
    (JPEG), pas le PNG brut que rend l'outil."""
    binaire, modeles = _outils(tmp_path)
    monkeypatch.setattr(upscale.subprocess, "run", _faux_subprocess_run(sortie_px=(1200, 800)))

    resultat = upscale.agrandir_fond(_png(660, 440), binaire=binaire, modeles=modeles)

    assert Image.open(io.BytesIO(resultat)).format == "JPEG"


def test_agrandir_fond_leve_upscale_indisponible_sur_une_sortie_corrompue(tmp_path, monkeypatch):
    """Revue, tour de correction 1/5 : une sortie d'`upscayl-bin` illisible
    par PIL (fichier tronque, format inattendu) doit lever
    `UpscaleIndisponible`, PAS une exception PIL brute -- sinon
    `_agrandir_fond_si_marque` (qui n'attrape que `UpscaleIndisponible`) la
    laisse filer jusqu'au `except Exception` de `runner._geler` (« gel
    interrompu », notification, format suspendu) pour ce qui n'est qu'une
    panne d'outil comme les autres."""
    binaire, modeles = _outils(tmp_path)

    def run(cmd, **kwargs):
        with open(_chemin_sortie(cmd), "wb") as f:
            f.write(b"pas une image")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(upscale.subprocess, "run", run)
    with pytest.raises(upscale.UpscaleIndisponible, match="illisible"):
        upscale.agrandir_fond(_png(660, 440), binaire=binaire, modeles=modeles)
