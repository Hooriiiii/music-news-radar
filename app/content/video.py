"""Assemblage du reel : la cover en premiere frame, puis la video, en 1080x1920.

Pur orchestrateur ffmpeg -- ni base, ni reseau, ni Chromium. Meme partage des
roles que `layout.py` avec `render.py` : ce module ne connait que des octets
d'entree et des octets de sortie.

Trois pieges de ffmpeg sont traites ici, et chacun casse l'assemblage :

1. Une image fixe n'a PAS de piste audio, et une video X en est souvent
   depourvue elle aussi. Le filtre `concat` refuse d'assembler deux flux dont
   l'un porte une piste et l'autre non : on fabrique donc un silence des deux
   cotes, systematiquement.
2. On passe par le FILTRE `concat` et non par le demultiplexeur : ce dernier
   exige des flux strictement identiques (codec, resolution, cadence) et casse
   au premier ecart, ce qui est la norme entre une image fixe et une video X.
3. Les dimensions impaires font echouer libx264 (`yuv420p` exige des multiples
   de 2). Le format de sortie etant fixe a 1080x1920, le probleme ne peut venir
   que des calculs intermediaires du fond floute -- d'ou les `-2` dans `scale`,
   qui arrondissent au pair le plus proche.
"""

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Specifications Reels. La resolution est la seule chose qu'Instagram n'ajustera
# pas a notre place : une video hors format est recadree par ses soins, sans
# qu'on maitrise ce qui est coupe.
FORMAT_REEL = (1080, 1920)
CADENCE = 30
# Le fond floute : la video agrandie pour REMPLIR le cadre, puis floutee. C'est
# le codage visuel dominant sur Reels et TikTok, et il ne perd rien de l'image.
FLOU = 40
# Une seule frame par defaut : la cover sert de vignette de couverture, pas de
# carton d'introduction. Parametrable parce qu'Instagram reencode a l'import et
# qu'une frame unique peut ne pas survivre -- si c'est le cas, augmenter ici.
FRAMES_COVER = 1
DELAI_MAX_S = 300


class AssemblageIndisponible(RuntimeError):
    """ffmpeg manquant, ou entree illisible. Message affichable tel quel."""


# launchd ne fournit qu'un PATH minimal -- /usr/bin, /bin, /usr/sbin, /sbin et
# le dossier d'uv. `/opt/homebrew/bin` n'y est PAS, donc `which` echoue alors que
# ffmpeg est bien installe : l'export d'un post reel retombait sur cover et video
# separees depuis l'agent, tout en marchant parfaitement depuis un terminal.
# Meme piege que pour le notifieur -- c'est un motif de ce projet, pas un cas.
EMPLACEMENTS_CONNUS = ("/opt/homebrew/bin", "/usr/local/bin")


def _exiger_ffmpeg(nom: str) -> str:
    chemin = shutil.which(nom)
    if chemin is None:
        chemin = next(
            (str(Path(d) / nom) for d in EMPLACEMENTS_CONNUS if (Path(d) / nom).exists()),
            None,
        )
    if chemin is None:
        raise AssemblageIndisponible(
            f"{nom} est introuvable. L'assemblage des reels a besoin de ffmpeg : "
            "`brew install ffmpeg`."
        )
    return chemin


def exiger_outils() -> None:
    """ffmpeg ET ffprobe, avant tout travail. Leur absence est une panne de la
    machine, pas un defaut de l'article : l'appelant doit pouvoir la distinguer
    d'une video illisible, qui leve la meme exception plus tard."""
    _exiger_ffmpeg("ffmpeg")
    _exiger_ffmpeg("ffprobe")


def chemin_outil(nom: str) -> str:
    """Le chemin d'un binaire ffmpeg, pour un autre outil qui ne saurait pas le
    trouver depuis launchd (yt-dlp, `--ffmpeg-location`)."""
    return _exiger_ffmpeg(nom)


def _cadence(texte: str | None) -> float:
    """`avg_frame_rate` de ffprobe (« 30000/1001 ») en images par seconde."""
    try:
        numerateur, denominateur = (texte or "0/0").split("/")
        return float(numerateur) / float(denominateur) if float(denominateur) else 0.0
    except ValueError:
        return 0.0


def sonder(video: bytes) -> dict:
    """Dimensions, duree et presence d'une piste audio. Sert aux tests et au
    dashboard, qui doit pouvoir afficher ce qu'il s'apprete a publier."""
    ffprobe = _exiger_ffmpeg("ffprobe")
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / "sonde.mp4"
        chemin.write_bytes(video)
        resultat = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(chemin)],
            capture_output=True, text=True, timeout=DELAI_MAX_S,
        )
        if resultat.returncode != 0:
            raise AssemblageIndisponible(f"video illisible : {resultat.stderr.strip()[:200]}")
        donnees = json.loads(resultat.stdout)

    flux_video = next((f for f in donnees["streams"] if f["codec_type"] == "video"), None)
    if flux_video is None:
        raise AssemblageIndisponible("le fichier ne contient aucun flux video")
    flux_audio = next((f for f in donnees["streams"] if f["codec_type"] == "audio"), None)
    return {
        "width": flux_video["width"],
        "height": flux_video["height"],
        "duration": float(donnees["format"].get("duration", 0)),
        "has_audio": flux_audio is not None,
        # Ce que Meta controle : codecs, echantillonnage, cadence.
        "video_codec": flux_video.get("codec_name"),
        "audio_codec": flux_audio.get("codec_name") if flux_audio else None,
        "sample_rate": int(flux_audio["sample_rate"]) if flux_audio and flux_audio.get("sample_rate") else None,
        "fps": _cadence(flux_video.get("avg_frame_rate")),
    }


def _filtre(frames_cover: int, source_a_audio: bool) -> str:
    """Le graphe de filtres, en une chaine. Ecrit ici et pas en dur ailleurs :
    c'est la seule regle de mise en forme du reel.

    `source_a_audio` n'est pas un detail : un `amix` conditionnel ne marche pas,
    car un graphe qui reference `[1:a]` sur une video muette est INVALIDE, pas
    seulement vide. On sonde donc la source avant, et on cable soit sa piste,
    soit un silence de meme duree.
    """
    largeur, hauteur = FORMAT_REEL
    return (
        # [1] la video source -> fond floute qui REMPLIT le cadre...
        f"[1:v]scale={largeur}:{hauteur}:force_original_aspect_ratio=increase,"
        f"crop={largeur}:{hauteur},boxblur={FLOU}:1[fond];"
        # ...et la video entiere, redimensionnee pour TENIR dedans, sans rien perdre
        f"[1:v]scale={largeur}:{hauteur}:force_original_aspect_ratio=decrease,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2[avant];"
        f"[fond][avant]overlay=(W-w)/2:(H-h)/2,fps={CADENCE},setsar=1[v1];"
        # [0] la cover : quelques frames, meme cadence et meme SAR pour que
        # `concat` les accepte cote a cote
        f"[0:v]scale={largeur}:{hauteur},fps={CADENCE},setsar=1,"
        f"trim=end_frame={frames_cover},setpts=PTS-STARTPTS[v0];"
        # les deux silences : sans eux, concat refuse d'assembler
        f"[2:a]atrim=end={frames_cover / CADENCE},asetpts=PTS-STARTPTS[a0];"
        + (
            "[1:a]aresample=44100,asetpts=PTS-STARTPTS[a1];"
            if source_a_audio
            # Silence borne a la duree de la video : `-shortest` ne suffirait pas,
            # anullsrc etant infini et concat prenant la piste telle quelle.
            else "[3:a]asetpts=PTS-STARTPTS[a1];"
        )
        + "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )


def assembler_reel(
    cover_png: bytes, video: bytes, frames_cover: int = FRAMES_COVER
) -> bytes:
    """Cover + video -> un MP4 1080x1920 pret a publier."""
    ffmpeg = _exiger_ffmpeg("ffmpeg")
    # Sonder AVANT d'assembler : le graphe differe selon que la source porte une
    # piste audio, et une video X en est souvent depourvue.
    source_a_audio = sonder(video)["has_audio"]

    with tempfile.TemporaryDirectory() as dossier:
        base = Path(dossier)
        (base / "cover.png").write_bytes(cover_png)
        (base / "source.mp4").write_bytes(video)
        sortie = base / "reel.mp4"

        commande = [
            ffmpeg, "-y",
            "-loop", "1", "-i", str(base / "cover.png"),
            "-i", str(base / "source.mp4"),
            # Deux silences : l'un pour la cover, l'autre melange a la source
            # pour lui GARANTIR une piste meme quand elle n'en a pas.
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-filter_complex", _filtre(frames_cover, source_a_audio),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            # Borne la sortie sur le flux video : sans elle, le silence infini
            # d'anullsrc produirait un fichier qui ne se termine jamais.
            "-shortest",
            # GOP ferme et pas d'edit list : deux exigences ecrites de Meta pour
            # les reels (2026-09-17). ffmpeg ecrit une edit list par defaut pour
            # compenser l'amorcage AAC.
            "-flags", "+cgop",
            "-use_editlist", "0",
            "-movflags", "+faststart",
            str(sortie),
        ]
        resultat = subprocess.run(commande, capture_output=True, text=True, timeout=DELAI_MAX_S)
        if resultat.returncode != 0 or not sortie.exists():
            # Les dernieres lignes seulement : une trace ffmpeg complete fait
            # des centaines de lignes et n'est pas affichable dans le dashboard.
            fin = "\n".join(resultat.stderr.strip().splitlines()[-4:])
            raise AssemblageIndisponible(f"assemblage impossible : {fin[:400]}")
        return sortie.read_bytes()
