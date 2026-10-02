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

from app.config import settings

logger = logging.getLogger(__name__)

# Specifications Reels. La resolution est la seule chose qu'Instagram n'ajustera
# pas a notre place : une video hors format est recadree par ses soins, sans
# qu'on maitrise ce qui est coupe.
FORMAT_REEL = (1080, 1920)
# Un element video de carrousel : Instagram recadre chaque element au ratio du
# PREMIER, la cover `kinda_post` publiee en 1080x1350 (4:5).
FORMAT_CARROUSEL = (1080, 1350)
CADENCE = 30
# Le fond floute : la video agrandie pour REMPLIR le cadre, puis floutee. C'est
# le codage visuel dominant sur Reels et TikTok, et il ne perd rien de l'image.
FLOU = 40
# Une seule frame par defaut : la cover sert de vignette de couverture, pas de
# carton d'introduction. Parametrable parce qu'Instagram reencode a l'import et
# qu'une frame unique peut ne pas survivre -- si c'est le cas, augmenter ici.
FRAMES_COVER = 1
DELAI_MAX_S = 300
MO = 1024 * 1024


class AssemblageIndisponible(RuntimeError):
    """ffmpeg manquant, ou entree illisible. Message affichable tel quel.

    `returncode` et `stderr` portent ce qu'a rendu le binaire quand il a tourne :
    l'appelant y distingue une machine a court de ressources (disque plein,
    memoire, processus tue) d'une entree fautive."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


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
            raise AssemblageIndisponible(
                f"video illisible : {resultat.stderr.strip()[:200]}",
                returncode=resultat.returncode, stderr=resultat.stderr[-4000:],
            )
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


def _cadre_floute(entree: str, format_: tuple[int, int], sortie: str) -> str:
    """La video entiere au centre, sur elle-meme agrandie et floutee. Commun au
    reel et a la video de carrousel : rien n'est perdu de l'image."""
    largeur, hauteur = format_
    return (
        # la video source -> fond floute qui REMPLIT le cadre...
        f"[{entree}]scale={largeur}:{hauteur}:force_original_aspect_ratio=increase,"
        f"crop={largeur}:{hauteur},boxblur={FLOU}:1[fond_{sortie}];"
        # ...et la video entiere, redimensionnee pour TENIR dedans, sans rien perdre
        f"[{entree}]scale={largeur}:{hauteur}:force_original_aspect_ratio=decrease,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2[avant_{sortie}];"
        f"[fond_{sortie}][avant_{sortie}]overlay=(W-w)/2:(H-h)/2,fps={CADENCE},setsar=1[{sortie}];"
    )


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
        # [1] la video source, cadree sur fond floute
        _cadre_floute("1:v", FORMAT_REEL, "v1")
        # [0] la cover : quelques frames, meme cadence et meme SAR pour que
        # `concat` les accepte cote a cote
        + f"[0:v]scale={largeur}:{hauteur},fps={CADENCE},setsar=1,"
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
    cover_png: bytes, video: bytes, frames_cover: int = FRAMES_COVER,
    *, poids_max: int | None = None,
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

        def commande(encodage: tuple[str, ...]) -> list[str]:
            return [
                ffmpeg, "-y",
                "-loop", "1", "-i", str(base / "cover.png"),
                "-i", str(base / "source.mp4"),
                # Deux silences : l'un pour la cover, l'autre melange a la source
                # pour lui GARANTIR une piste meme quand elle n'en a pas.
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-filter_complex", _filtre(frames_cover, source_a_audio),
                "-map", "[v]", "-map", "[a]",
                *encodage,
                str(sortie),
            ]

        return _encoder_sous_plafond(commande, sortie, poids_max)


def encodage_meta(*, debit_kbit: int | None = None) -> tuple[str, ...]:
    """Les flags d'encodage communs au reel et a la video de carrousel.

    La QUALITE est un reglage (`app/config.py`), pas une constante enfouie :
    sans `-crf`, x264 appliquait son defaut de distribution web (23), que
    personne n'avait choisi. Le raisonnement et la mesure sont dans la config,
    a cote des valeurs.

    `debit_kbit` n'est passe que par la REPRISE du garde-fou de poids (voir
    `_encoder_sous_plafond`), et il REMPLACE alors le crf par un debit MOYEN
    vise. Le cas nominal ne le voit jamais.

    Pourquoi un debit moyen et non un « capped CRF » (crf + `-maxrate`), qui
    aurait garde la qualite pilotee par le crf : mesure du 2026-10-02 sur du
    bruit, le test `test_le_garde_fou_tient_sur_une_vraie_video_incompressible`
    l'a attrape. Le VBV borne une FENETRE de `bufsize` bits, pas le fichier ;
    sur une video COURTE, cette fenetre couvre tout le fichier et ne contraint
    plus rien -- sortie de 22,7 Mo pour un plafond de 16,6 Mo. `-b:v` vise la
    moyenne sur TOUTE la duree, ce qui est exactement la question posee (« tenir
    sous N octets »), et `-maxrate`/`-bufsize` au meme debit empechent en plus un
    pic local de manger le budget.
    """
    flags = ["-c:v", "libx264", "-preset", settings.encodage_preset]
    if debit_kbit is None:
        flags += ["-crf", str(settings.encodage_crf)]
    else:
        flags += [
            "-b:v", f"{debit_kbit}k",
            "-maxrate", f"{debit_kbit}k",
            "-bufsize", f"{debit_kbit}k",
        ]
    flags += [
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", settings.encodage_audio_bitrate,
        # Borne la sortie sur le flux video : sans elle, le silence infini
        # d'anullsrc produirait un fichier qui ne se termine jamais.
        "-shortest",
        # GOP ferme et pas d'edit list : deux exigences ecrites de Meta pour
        # les reels (2026-09-17). ffmpeg ecrit une edit list par defaut pour
        # compenser l'amorcage AAC.
        "-flags", "+cgop",
        "-use_editlist", "0",
        "-movflags", "+faststart",
    ]
    return tuple(flags)


def _kbit(bitrate: str) -> int:
    """« 192k » -> 192. Un reglage illisible retombe sur la valeur par defaut
    plutot que de fausser le calcul du plafond."""
    texte = bitrate.strip().lower().removesuffix("k")
    try:
        return int(float(texte))
    except ValueError:
        logger.warning("bitrate audio illisible (%r), 192k retenu", bitrate)
        return 192


def debit_sous_plafond(poids_max: int, duree: float) -> int | None:
    """Le debit VIDEO, en kbit/s, qui tient un fichier de `duree` secondes sous
    `poids_max` octets -- audio et marge deduits.

    Rend None quand la question n'a pas de sens (duree inconnue ou nulle) :
    l'appelant garde alors la sortie telle quelle plutot que d'inventer.
    """
    if poids_max <= 0 or duree <= 0:
        return None
    total = poids_max * settings.encodage_poids_marge * 8 / duree / 1000
    video = int(total - _kbit(settings.encodage_audio_bitrate))
    # Le plancher, plutot que renoncer : l'alternative est l'exclusion.
    return max(video, settings.encodage_debit_min_kbit)


def _encoder_sous_plafond(commande, sortie: Path, poids_max: int | None) -> bytes:
    """Encode, et REENCODE sous un debit vise si la sortie est trop lourde.

    C'est le garde-fou dont la violation coute une ACTU et non une image molle :
    au-dela de `publication_reel_max_mo`, `preparation_reel.verifier_sortie`
    leve `PreparationImpossible`, qui exclut l'article DEFINITIVEMENT de la file
    (voir « Exclusion » dans CLAUDE.md). Baisser le crf de 23 a 20 reduit la
    marge ; on ne laisse donc plus ce depassement se produire en silence.

    La duree est MESUREE sur la sortie, jamais deduite de l'entree : la cover
    ajoute ses frames devant, et une source peut mentir.

    Et le debit vise ne suffit pas a lui seul : mesure du 2026-10-02, sur un clip
    court x264 DEPASSE sa consigne de ~26 % (son controle de debit ne converge
    pas en 60 frames). On CORRIGE donc proportionnellement au depassement mesure,
    au plus `encodage_reprises_max` fois -- un garde-fou qui se verifie plutot
    qu'un calcul auquel on fait confiance.

    Le garde-fou epuise rend quand meme la sortie : il ne remplace jamais un
    depassement par une exception, c'est `verifier_sortie` qui tranche.
    """
    plafond = settings.publication_reel_max_mo * MO if poids_max is None else poids_max
    octets = _executer(commande(encodage_meta()), sortie)
    if plafond <= 0 or len(octets) <= plafond:
        return octets
    try:
        duree = float(sonder(octets).get("duration") or 0.0)
    except (AssemblageIndisponible, KeyError, ValueError, TypeError):
        duree = 0.0
    debit = debit_sous_plafond(plafond, duree)
    if debit is None:
        # Duree inconnue : rien a calculer. On rend la sortie telle quelle et
        # les controles d'aval tranchent, exactement comme avant ce garde-fou.
        logger.warning(
            "sortie de %.0f Mo au-dessus du plafond de %.0f Mo, duree inconnue : "
            "pas de reprise possible", len(octets) / MO, plafond / MO,
        )
        return octets
    vise = plafond * settings.encodage_poids_marge
    for essai in range(1, max(settings.encodage_reprises_max, 1) + 1):
        logger.warning(
            "sortie de %.1f Mo au-dessus du plafond de %.0f Mo : reencodage %d/%d "
            "a %d kbit/s", len(octets) / MO, plafond / MO, essai,
            settings.encodage_reprises_max, debit,
        )
        octets = _executer(commande(encodage_meta(debit_kbit=debit)), sortie)
        if len(octets) <= plafond:
            return octets
        # Correction proportionnelle au depassement CONSTATE.
        suivant = max(int(debit * vise / len(octets)), settings.encodage_debit_min_kbit)
        if suivant >= debit:
            # Plancher atteint : insister ne ferait que bruler du temps.
            break
        debit = suivant
    logger.warning(
        "garde-fou de poids epuise : %.1f Mo pour un plafond de %.0f Mo",
        len(octets) / MO, plafond / MO,
    )
    return octets


def _executer(commande: list[str], sortie: Path) -> bytes:
    resultat = subprocess.run(commande, capture_output=True, text=True, timeout=DELAI_MAX_S)
    if resultat.returncode != 0 or not sortie.exists():
        # Les dernieres lignes seulement : une trace ffmpeg complete fait
        # des centaines de lignes et n'est pas affichable dans le dashboard.
        fin = "\n".join(resultat.stderr.strip().splitlines()[-4:])
        raise AssemblageIndisponible(
            f"assemblage impossible : {fin[:400]}",
            returncode=resultat.returncode, stderr=resultat.stderr[-4000:],
        )
    return sortie.read_bytes()


def transcoder_carrousel(video: bytes, *, poids_max: int | None = None) -> bytes:
    """Une video d'actu -> un element de carrousel 1080x1350, H.264/AAC, meme
    cadrage sur fond floute que le reel, piste audio garantie (silence si muette)."""
    ffmpeg = _exiger_ffmpeg("ffmpeg")
    source_a_audio = sonder(video)["has_audio"]
    with tempfile.TemporaryDirectory() as dossier:
        base = Path(dossier)
        (base / "source.mp4").write_bytes(video)
        sortie = base / "carrousel.mp4"
        audio = (
            "[0:a]aresample=44100,asetpts=PTS-STARTPTS[a]"
            if source_a_audio
            else "[1:a]asetpts=PTS-STARTPTS[a]"
        )
        def commande(encodage: tuple[str, ...]) -> list[str]:
            return [
                ffmpeg, "-y",
                "-i", str(base / "source.mp4"),
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-filter_complex", _cadre_floute("0:v", FORMAT_CARROUSEL, "v") + audio,
                "-map", "[v]", "-map", "[a]",
                *encodage,
                str(sortie),
            ]

        return _encoder_sous_plafond(commande, sortie, poids_max)
