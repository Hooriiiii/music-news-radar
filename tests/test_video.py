"""L'assemblage du reel : cover en premiere frame, puis la video, en 1080x1920."""

import shutil
import subprocess

import pytest

import app.content.video as video_mod
from app.content.video import (
    FORMAT_REEL,
    AssemblageIndisponible,
    _cadence,
    assembler_reel,
    chemin_outil,
    exiger_outils,
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
    import app.content.video as mod

    # Neutraliser AUSSI les emplacements en dur : sinon le resultat depend de
    # ce qui est installe sur la machine qui execute les tests.
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(mod, "EMPLACEMENTS_CONNUS", ())
    with pytest.raises(AssemblageIndisponible) as exc:
        assembler_reel(b"", b"")
    assert "ffmpeg" in str(exc.value).lower()


def test_ffmpeg_est_trouve_hors_du_PATH_de_launchd(monkeypatch, tmp_path):
    """L'agent launchd n'a que /usr/bin:/bin:/usr/sbin:/sbin et le dossier d'uv.

    ffmpeg vit dans /opt/homebrew/bin : `which` y échoue alors qu'il EST
    installé. Constaté en vrai — l'export d'un post reel retombait sur cover et
    vidéo séparées, avec « ffmpeg est introuvable » dans les manquants, alors
    que l'assemblage marchait parfaitement depuis un terminal.
    """
    import app.content.video as mod

    faux = tmp_path / "ffmpeg"
    faux.write_text("")
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(mod, "EMPLACEMENTS_CONNUS", (str(tmp_path),))

    assert mod._exiger_ffmpeg("ffmpeg") == str(faux)


def test_binaire_introuvable_partout_reste_une_erreur_lisible(monkeypatch, tmp_path):
    import app.content.video as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(mod, "EMPLACEMENTS_CONNUS", (str(tmp_path / "vide"),))
    with pytest.raises(AssemblageIndisponible) as exc:
        mod._exiger_ffmpeg("ffmpeg")
    assert "brew install ffmpeg" in str(exc.value)


def test_cadence():
    assert _cadence("30/1") == 30.0
    assert _cadence("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert _cadence("0/0") == 0.0
    assert _cadence(None) == 0.0


def test_exiger_outils_sans_ffprobe(monkeypatch):
    monkeypatch.setattr(video_mod.shutil, "which", lambda nom: "/bin/ffmpeg" if nom == "ffmpeg" else None)
    monkeypatch.setattr(video_mod, "EMPLACEMENTS_CONNUS", ())
    with pytest.raises(AssemblageIndisponible, match="ffprobe"):
        exiger_outils()


def test_chemin_outil_cherche_hors_du_path(monkeypatch, tmp_path):
    (tmp_path / "ffmpeg").write_text("")
    monkeypatch.setattr(video_mod.shutil, "which", lambda nom: None)
    monkeypatch.setattr(video_mod, "EMPLACEMENTS_CONNUS", (str(tmp_path),))
    assert chemin_outil("ffmpeg") == str(tmp_path / "ffmpeg")


def _encodeur(nom):
    if ffmpeg_absent:
        return False
    sortie = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return nom in sortie.stdout


besoin_vp9 = pytest.mark.skipif(
    not _encodeur("libvpx-vp9"), reason="ffmpeg sans libvpx-vp9 -- source VP9 non fabricable ici"
)


@besoin_vp9
def test_une_source_vp9_ressort_en_h264_aac(tmp_path):
    """yt-dlp rend les reels Instagram en VP9 + AAC (mesure du 2026-09-17), et
    Meta n'accepte que H.264 ou HEVC : l'assemblage doit tout reencoder, jamais
    copier le flux video."""
    source = tmp_path / "source_vp9.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "500k",
         "-c:a", "aac", "-shortest", str(source)],
        capture_output=True, check=True,
    )
    assert sonder(source.read_bytes())["video_codec"] == "vp9"
    infos = sonder(assembler_reel(_cover(tmp_path), source.read_bytes()))
    assert (infos["video_codec"], infos["audio_codec"]) == ("h264", "aac")
    assert (infos["width"], infos["height"]) == FORMAT_REEL


def _boite(octets, nom):
    """Le contenu d'une boite MP4 de premier niveau, ou None."""
    position = 0
    while position + 8 <= len(octets):
        taille = int.from_bytes(octets[position:position + 4], "big")
        if octets[position + 4:position + 8] == nom:
            return octets[position:position + taille]
        if taille < 8:
            return None
        position += taille
    return None


@besoin_ffmpeg
def test_le_reel_respecte_les_specs_de_meta(tmp_path):
    """Specs Meta du 2026-09-17 : H.264/HEVC, AAC <= 48 kHz, 23-60 i/s, moov en
    tete, AUCUNE edit list. ffmpeg en ecrit une par defaut (amorcage AAC)."""
    sortie = assembler_reel(_cover(tmp_path), _video(tmp_path, 1280, 720, secondes=3))
    infos = sonder(sortie)
    assert (infos["video_codec"], infos["audio_codec"]) == ("h264", "aac")
    assert infos["sample_rate"] <= 48000
    assert 23 <= infos["fps"] <= 60
    moov = _boite(sortie, b"moov")
    assert moov is not None and sortie.find(b"moov") < sortie.find(b"mdat")
    assert b"elst" not in moov


# --- Video d'un carrousel ------------------------------------------------------------


@besoin_ffmpeg
@pytest.mark.parametrize("muette", [False, True])
def test_une_video_de_carrousel_sort_en_4_5_h264_aac(tmp_path, muette):
    """Instagram recadre chaque element au ratio du PREMIER, la cover 1080x1350 :
    la video est cadree d'avance sur fond floute, avec une piste audio garantie."""
    sortie = video_mod.transcoder_carrousel(_video(tmp_path, 1280, 720, muette=muette))
    infos = sonder(sortie)
    assert (infos["width"], infos["height"]) == video_mod.FORMAT_CARROUSEL == (1080, 1350)
    assert (infos["video_codec"], infos["audio_codec"]) == ("h264", "aac")
    assert infos["duration"] == pytest.approx(1.0, abs=0.2)


@besoin_ffmpeg
def test_une_video_de_carrousel_illisible_donne_une_erreur_lisible(tmp_path):
    with pytest.raises(AssemblageIndisponible):
        video_mod.transcoder_carrousel(b"pas une video")
