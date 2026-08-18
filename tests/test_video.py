"""L'assemblage du reel : cover en premiere frame, puis la video, en 1080x1920."""

import shutil
import subprocess

import pytest

from app.content.video import (
    FORMAT_REEL,
    AssemblageIndisponible,
    assembler_reel,
    sonder,
)

ffmpeg_absent = shutil.which("ffmpeg") is None
besoin_ffmpeg = pytest.mark.skipif(ffmpeg_absent, reason="ffmpeg absent -- assemblage non testable ici")


def _video(tmp_path, largeur, hauteur, secondes=1, muette=False):
    """Une vraie petite video, generee par ffmpeg. Tester l'assemblage sur des
    doubles ne prouverait rien : ce qu'on veut verifier, ce sont les dimensions
    et les pistes du fichier PRODUIT."""
    chemin = tmp_path / f"src_{largeur}x{hauteur}{'_muette' if muette else ''}.mp4"
    commande = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"testsrc=size={largeur}x{hauteur}:rate=30:duration={secondes}",
    ]
    if not muette:
        commande += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={secondes}"]
    commande += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if not muette:
        commande += ["-c:a", "aac", "-shortest"]
    commande += [str(chemin)]
    subprocess.run(commande, capture_output=True, check=True)
    return chemin.read_bytes()


def _cover(tmp_path):
    chemin = tmp_path / "cover.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=pink:size=1080x1920", "-frames:v", "1", str(chemin)],
        capture_output=True, check=True,
    )
    return chemin.read_bytes()


@besoin_ffmpeg
def test_le_reel_sort_au_format_instagram(tmp_path):
    """1080x1920 quelle que soit la source : c'est la seule chose qu'Instagram
    n'ajustera pas a notre place."""
    sortie = assembler_reel(_cover(tmp_path), _video(tmp_path, 1280, 720))

    infos = sonder(sortie)
    assert (infos["width"], infos["height"]) == FORMAT_REEL


@besoin_ffmpeg
def test_une_video_deja_verticale_reste_intacte_en_dimensions(tmp_path):
    sortie = assembler_reel(_cover(tmp_path), _video(tmp_path, 1080, 1920))
    infos = sonder(sortie)
    assert (infos["width"], infos["height"]) == FORMAT_REEL


@besoin_ffmpeg
def test_le_reel_porte_une_piste_audio_meme_si_la_source_est_muette(tmp_path):
    """Une video X sans audio est courante. Sans piste, la concatenation echoue
    -- et un reel sans piste audio se comporte mal a l'import."""
    sortie = assembler_reel(_cover(tmp_path), _video(tmp_path, 1080, 1920, muette=True))
    assert sonder(sortie)["has_audio"] is True


@besoin_ffmpeg
def test_l_audio_de_la_source_est_conserve(tmp_path):
    sortie = assembler_reel(_cover(tmp_path), _video(tmp_path, 1080, 1920))
    assert sonder(sortie)["has_audio"] is True


@besoin_ffmpeg
def test_la_cover_ajoute_sa_duree_devant(tmp_path):
    """La cover est la premiere frame : la duree totale depasse celle de la
    source, sans la doubler."""
    source = _video(tmp_path, 1080, 1920, secondes=2)
    sortie = assembler_reel(_cover(tmp_path), source, frames_cover=15)

    duree_source = sonder(source)["duration"]
    duree_sortie = sonder(sortie)["duration"]
    assert duree_sortie > duree_source
    assert duree_sortie < duree_source + 1.5


@besoin_ffmpeg
def test_une_video_illisible_donne_une_erreur_lisible(tmp_path):
    """Pas une trace ffmpeg de 200 lignes : le dashboard doit pouvoir l'afficher."""
    with pytest.raises(AssemblageIndisponible):
        assembler_reel(_cover(tmp_path), b"ceci n'est pas une video")


def test_ffmpeg_absent_donne_une_erreur_explicite(monkeypatch):
    """Meme philosophie que Chromium pour le rendu : une dependance locale
    manquante se dit, elle ne se devine pas."""
    monkeypatch.setattr("app.content.video.shutil.which", lambda _: None)
    with pytest.raises(AssemblageIndisponible) as exc:
        assembler_reel(b"", b"")
    assert "ffmpeg" in str(exc.value).lower()
