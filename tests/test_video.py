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


# --- Qualite d'encodage et garde-fou de poids ----------------------------------------
#
# Les trois pieges ffmpeg documentes en tete de `app/content/video.py` restent
# couverts par les tests ci-dessus, qui tournent sur de VRAIS fichiers : la piste
# audio fabriquee des deux cotes du `concat` (le reel muet, et le carrousel
# parametre `muette=True`), le filtre `concat` plutot que le demultiplexeur (une
# cover 1080x1920 assemblee a une source 1280x720 -- le demultiplexeur refuserait
# des flux si differents), et l'arrondi au pair (ces memes sources passent par les
# `scale=-2` du fond floute). `test_le_reel_respecte_les_specs_de_meta` garde le
# GOP ferme, l'absence d'edit list et le moov en tete.


def test_le_crf_est_explicite_et_vient_du_reglage(monkeypatch):
    """Sans `-crf`, x264 appliquait son defaut de distribution web (23) : une
    valeur que personne n'avait choisie. Elle est desormais un reglage."""
    monkeypatch.setattr(video_mod.settings, "encodage_crf", 20)
    monkeypatch.setattr(video_mod.settings, "encodage_preset", "medium")
    monkeypatch.setattr(video_mod.settings, "encodage_audio_bitrate", "192k")

    flags = video_mod.encodage_meta()

    assert flags[flags.index("-crf") + 1] == "20"
    assert flags[flags.index("-preset") + 1] == "medium"
    assert flags[flags.index("-b:a") + 1] == "192k"


def test_les_exigences_de_meta_survivent_au_reglage_de_qualite():
    """GOP ferme, pas d'edit list, faststart : elles ne sont pas negociables."""
    flags = video_mod.encodage_meta()
    assert ("-flags", "+cgop") == (flags[flags.index("-flags")], flags[flags.index("-flags") + 1])
    assert flags[flags.index("-use_editlist") + 1] == "0"
    assert flags[flags.index("-movflags") + 1] == "+faststart"
    assert "-shortest" in flags


def test_le_cas_nominal_ne_porte_aucun_plafond_de_debit():
    """Le plafond VBV degrade les passages charges : il ne doit apparaitre que
    sur la REPRISE, jamais sur l'encodage de tout le monde."""
    assert "-maxrate" not in video_mod.encodage_meta()
    assert "-bufsize" not in video_mod.encodage_meta()
    assert "-b:v" not in video_mod.encodage_meta()


def test_la_reprise_vise_un_debit_moyen_et_non_un_plafond_de_fenetre():
    """Le « capped CRF » (crf + `-maxrate`) ne tient PAS sur une video courte :
    le VBV borne une fenetre de `bufsize` bits, qui couvre alors tout le fichier.
    Mesure du 2026-10-02 : 22,7 Mo pour un plafond de 16,6 Mo. `-b:v` vise la
    moyenne sur toute la duree, ce qui est la question posee."""
    flags = video_mod.encodage_meta(debit_kbit=4000)
    assert flags[flags.index("-b:v") + 1] == "4000k"
    assert flags[flags.index("-maxrate") + 1] == "4000k"
    assert flags[flags.index("-bufsize") + 1] == "4000k"
    assert "-crf" not in flags, "un crf avec -b:v reprendrait la main sur le debit"


def test_le_debit_de_reprise_deduit_l_audio_et_la_marge(monkeypatch):
    monkeypatch.setattr(video_mod.settings, "encodage_audio_bitrate", "192k")
    monkeypatch.setattr(video_mod.settings, "encodage_poids_marge", 0.90)
    monkeypatch.setattr(video_mod.settings, "encodage_debit_min_kbit", 300)

    # 100 Mo pour 90 s : 100*1024*1024*0.9*8/90/1000 = 8388 kbit/s, moins l'audio.
    assert video_mod.debit_sous_plafond(100 * video_mod.MO, 90.0) == 8388 - 192


def test_une_duree_inconnue_ne_fabrique_aucun_debit():
    """Ne pas savoir vaut mieux qu'inventer : l'appelant garde alors la sortie
    telle quelle et les controles d'aval tranchent, comme avant ce garde-fou."""
    assert video_mod.debit_sous_plafond(100 * video_mod.MO, 0.0) is None
    assert video_mod.debit_sous_plafond(0, 90.0) is None


def test_le_debit_de_reprise_ne_descend_pas_sous_le_plancher(monkeypatch):
    monkeypatch.setattr(video_mod.settings, "encodage_debit_min_kbit", 300)
    # Un plafond absurde pour la duree : on encode au plancher plutot que de
    # renoncer -- l'alternative est l'exclusion definitive de l'article.
    assert video_mod.debit_sous_plafond(1 * video_mod.MO, 90.0) == 300


def test_un_bitrate_audio_illisible_ne_leve_pas(monkeypatch):
    monkeypatch.setattr(video_mod.settings, "encodage_audio_bitrate", "n'importe quoi")
    assert video_mod.debit_sous_plafond(100 * video_mod.MO, 90.0) == 8388 - 192


def _encodeur_factice(monkeypatch, tailles, duree=90.0):
    """Remplace ffmpeg et ffprobe : on verifie la DECISION, pas l'encodage."""
    appels = []

    def executer(commande, sortie):
        appels.append(commande)
        return b"x" * tailles[min(len(appels) - 1, len(tailles) - 1)]

    monkeypatch.setattr(video_mod, "_executer", executer)
    monkeypatch.setattr(video_mod, "sonder", lambda _: {"has_audio": True, "duration": duree})
    monkeypatch.setattr(video_mod, "_exiger_ffmpeg", lambda _: "/bin/ffmpeg")
    return appels


def test_une_sortie_sous_le_plafond_n_est_pas_reencodee(monkeypatch):
    appels = _encodeur_factice(monkeypatch, [10])
    video_mod.assembler_reel(b"png", b"mp4", poids_max=1000)
    assert len(appels) == 1
    assert "-maxrate" not in appels[0]


def test_une_sortie_trop_lourde_est_reencodee_sous_plafond_au_lieu_de_passer(monkeypatch):
    """LA propriete du chantier : au-dela de `publication_reel_max_mo`,
    `preparation_reel.verifier_sortie` leve `PreparationImpossible`, qui EXCLUT
    l'article a vie. Baisser le crf reduit la marge, donc le depassement ne doit
    plus se produire en silence."""
    appels = _encodeur_factice(monkeypatch, [2000, 500])
    sortie = video_mod.assembler_reel(b"png", b"mp4", poids_max=1000)

    assert len(appels) == 2, "la reprise n'a pas eu lieu"
    assert "-b:v" in appels[1]
    assert len(sortie) == 500


def test_la_video_de_carrousel_partage_le_meme_garde_fou(monkeypatch):
    """`encodage_meta` est partage par les deux gabarits : le garde-fou aussi."""
    appels = _encodeur_factice(monkeypatch, [2000, 500])
    video_mod.transcoder_carrousel(b"mp4", poids_max=1000)
    assert len(appels) == 2
    assert "-b:v" in appels[1]


def test_sans_duree_mesurable_la_sortie_trop_lourde_passe_comme_avant(monkeypatch):
    """Le garde-fou ne doit pas remplacer un depassement par une exception :
    sans duree, il n'y a rien a calculer et l'aval tranche comme avant."""
    appels = _encodeur_factice(monkeypatch, [2000], duree=0.0)
    sortie = video_mod.assembler_reel(b"png", b"mp4", poids_max=1000)
    assert len(appels) == 1
    assert len(sortie) == 2000


def test_le_plafond_par_defaut_est_celui_que_le_gel_controle(monkeypatch):
    """Un second reglage divergerait du controle de `verifier_sortie` : on lit
    `publication_reel_max_mo`, le meme nombre."""
    monkeypatch.setattr(video_mod.settings, "publication_reel_max_mo", 1)
    appels = _encodeur_factice(monkeypatch, [2 * video_mod.MO, 10])
    video_mod.assembler_reel(b"png", b"mp4")
    assert len(appels) == 2


@besoin_ffmpeg
def test_le_garde_fou_tient_sur_une_vraie_video_incompressible(tmp_path):
    """Un garde-fou non declenche n'est pas un garde-fou. Du bruit : la source la
    plus lourde qui existe a encoder. Le plafond est reduit pour que la suite
    reste rapide -- la mesure au plafond REEL de 100 Mo sur 90 s de bruit est
    hors suite (voir le rapport du 2026-10-02)."""
    brut = tmp_path / "bruit.raw"
    largeur, hauteur, secondes = 480, 854, 2
    brut.write_bytes(__import__("os").urandom(largeur * hauteur * 3 // 2 * 30 * secondes))
    source = tmp_path / "bruit.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
         "-s", f"{largeur}x{hauteur}", "-r", "30", "-i", str(brut),
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14",
         "-pix_fmt", "yuv420p", str(source)],
        capture_output=True, check=True,
    )
    octets = source.read_bytes()

    sans_garde_fou = video_mod.transcoder_carrousel(octets, poids_max=-1)
    plafond = len(sans_garde_fou) // 2
    assert plafond > 0

    avec_garde_fou = video_mod.transcoder_carrousel(octets, poids_max=plafond)

    assert len(avec_garde_fou) <= plafond, (
        f"{len(avec_garde_fou)} octets pour un plafond de {plafond}"
    )
    # Et la sortie reste conforme : le garde-fou ne sauve pas le poids en
    # cassant les specs que le gel controle juste apres.
    infos = sonder(avec_garde_fou)
    assert (infos["width"], infos["height"]) == video_mod.FORMAT_CARROUSEL
    assert (infos["video_codec"], infos["audio_codec"]) == ("h264", "aac")


def test_la_reprise_corrige_le_depassement_constate(monkeypatch):
    """x264 depasse sa consigne sur un clip court : on ne fait pas confiance au
    calcul, on mesure et on corrige."""
    M = video_mod.MO
    appels = _encodeur_factice(monkeypatch, [40 * M, 30 * M, 15 * M])
    monkeypatch.setattr(video_mod.settings, "encodage_reprises_max", 3)

    sortie = video_mod.assembler_reel(b"png", b"mp4", poids_max=20 * M)

    assert len(appels) == 3
    debits = [int(c[c.index("-b:v") + 1].removesuffix("k")) for c in appels[1:]]
    assert debits[1] < debits[0], "la seconde reprise n'a pas baisse le debit"
    assert len(sortie) == 15 * M


def test_un_garde_fou_epuise_rend_la_sortie_au_lieu_de_lever(monkeypatch):
    """Le garde-fou ne remplace jamais un depassement par une exception : il rend
    la derniere sortie et laisse `verifier_sortie` trancher, comme avant."""
    M = video_mod.MO
    appels = _encodeur_factice(monkeypatch, [40 * M, 35 * M, 30 * M])
    monkeypatch.setattr(video_mod.settings, "encodage_reprises_max", 2)

    sortie = video_mod.assembler_reel(b"png", b"mp4", poids_max=20 * M)

    assert len(appels) == 3  # l'encodage nominal, puis les deux reprises
    assert len(sortie) == 30 * M


def test_le_plancher_de_debit_arrete_la_boucle(monkeypatch):
    """Au plancher, insister ne ferait que bruler du temps d'encodage."""
    monkeypatch.setattr(video_mod.settings, "encodage_debit_min_kbit", 300)
    monkeypatch.setattr(video_mod.settings, "encodage_reprises_max", 5)
    appels = _encodeur_factice(monkeypatch, [10**9], duree=3600.0)

    video_mod.assembler_reel(b"png", b"mp4", poids_max=1000)

    assert len(appels) == 2, "la boucle a tourne au-dela du plancher"
