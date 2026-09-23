"""Agrandissement du fond de cover par super-resolution (Tache 6, 2026-09-23).

Barreau AJOUTE juste avant le saut de la Tache 5 : photo de l'actu -> compte
source -> artiste cite -> recherche web -> **agrandissement du meilleur
candidat** -> saut. Motive par le post 76 (l'ADE et Shlomo, 2026-09-21) :
fond de 660x440 (3,07x pour remplir le gabarit), et le CDN (URL signee) rend
EXACTEMENT la meme image avec ou sans ses parametres de redimensionnement --
aucune version plus grande n'existe a aller chercher, ni chez ce candidat ni
ailleurs sur ce post. La super-resolution n'agrandit pas, elle INVENTE le
detail manquant -- c'est pourquoi elle ne touche QUE le fond de la cover
(recouvert d'un voile sombre et du texte de l'accroche, ou l'invention est
invisible), jamais un media du carrousel ni la video d'un reel, qui sont
DOCUMENTAIRES.

Outil : `upscayl-bin`, le backend CLI headless d'Upscayl
(github.com/upscayl/upscayl-ncnn), PAS le depot officiel
`Real-ESRGAN-ncnn-vulkan` cite par le plan -- verifie fige a sa version de
2022 (aucune release depuis), alors qu'upscayl-ncnn publie encore en
2025-12. Meme famille de modeles ncnn Real-ESRGAN, meme interface CLI
(`-i/-o/-s/-m/-n`), binaire autonome sans dependance Python -- meme patron
que ffmpeg, cloudflared et yt-dlp. Verifie sur cette machine (utilisateur,
2026-09-23) : 660x440 -> 2640x1760 en 1,1 a 10,7 s selon le modele et la
charge, GPU sollicite via Vulkan/MoltenVK (Metal en dessous), aucun blocage
Gatekeeper (le binaire vient du bundle d'une app signee).

Trois regles de robustesse, dans l'esprit de `app/content/video.py` :

1. **Binaire ou modeles absents = panne MACHINE, jamais une exclusion ni un
   saut silencieux.** `UpscaleIndisponible` en est le signal ; l'appelant
   (le gel) retombe sur le comportement d'avant l'agrandissement -- ici, le
   candidat echoue toujours le plancher, donc c'est le saut de la Tache 5,
   pas une exclusion.
2. **On n'agrandit que ce qui en vaut la peine** (`visuels_upscale_
   source_min_px`) : sous ce cote minimal, meme le facteur 4 ne repasse pas
   `cover_fond_upscale_max` sur le gabarit le plus exigeant (reel,
   1080x1920) -- lancer l'outil serait chauffer le GPU pour rien.
3. **Le resultat repasse par le plancher, sans exception.** Cette fonction
   ne le verifie PAS elle-meme (elle ne connait pas la cible du gabarit a
   l'appel) : c'est a l'appelant de mesurer le resultat et de sauter s'il ne
   suffit toujours pas -- l'outil ne doit jamais devenir un moyen de
   contourner la regle de qualite.
"""

import io
import logging
import subprocess
import tempfile
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class UpscaleIndisponible(RuntimeError):
    """Binaire absent, modeles absents, ou echec de l'agrandissement --
    PANNE MACHINE, jamais un defaut de l'article. Message affichable."""


def binaire_disponible(
    *, binaire: Path | None = None, modeles: Path | None = None
) -> bool:
    """Vrai si l'outil est utilisable TEL QUEL, sans lancer de sous-processus.

    Sert au gel pour decider, sans cout, si le barreau d'agrandissement a la
    moindre chance d'aboutir -- une pure lecture disque, comme
    `video._exiger_ffmpeg` avant de lancer ffmpeg."""
    binaire = binaire if binaire is not None else settings.visuels_upscale_binaire
    modeles = modeles if modeles is not None else settings.visuels_upscale_modeles
    return Path(binaire).is_file() and Path(modeles).is_dir()


def agrandir(
    octets: bytes,
    *,
    binaire: Path | None = None,
    modeles: Path | None = None,
    modele: str | None = None,
    echelle: int | None = None,
    delai_max_s: float | None = None,
) -> bytes:
    """Le contenu AGRANDI (PNG), via `upscayl-bin`.

    Leve `UpscaleIndisponible` sans avoir rien laisse sur le disque (dossier
    temporaire) : binaire ou modeles absents, sous-processus introuvable ou
    tue par le delai, code de retour non nul, ou sortie absente/vide -- dans
    tous les cas un message affichable, jamais une exception opaque.

    Ne mesure ni la taille source ni le resultat contre un plancher : cette
    fonction ne fait QUE lancer l'outil, la decision « est-ce que ca vaut le
    plancher » appartient a l'appelant, qui seul connait la cible du
    gabarit publie."""
    binaire = Path(binaire) if binaire is not None else settings.visuels_upscale_binaire
    modeles = Path(modeles) if modeles is not None else settings.visuels_upscale_modeles
    if not binaire.is_file():
        raise UpscaleIndisponible(
            f"upscayl-bin introuvable ({binaire}) : installer Upscayl "
            "(https://upscayl.org) ou regler VISUELS_UPSCALE_BINAIRE."
        )
    if not modeles.is_dir():
        raise UpscaleIndisponible(f"dossier de modeles Upscayl introuvable : {modeles}")
    modele = modele or settings.visuels_upscale_modele
    echelle = echelle if echelle is not None else settings.visuels_upscale_echelle
    delai_max_s = (
        delai_max_s if delai_max_s is not None else settings.visuels_upscale_delai_max_s
    )
    with tempfile.TemporaryDirectory() as dossier:
        entree = Path(dossier) / "entree.png"
        sortie = Path(dossier) / "sortie.png"
        entree.write_bytes(octets)
        try:
            resultat = subprocess.run(
                [
                    str(binaire),
                    "-i", str(entree),
                    "-o", str(sortie),
                    "-s", str(echelle),
                    "-m", str(modeles),
                    "-n", modele,
                ],
                capture_output=True,
                timeout=delai_max_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise UpscaleIndisponible(
                f"agrandissement abandonne : delai de {delai_max_s:.0f} s depasse"
            ) from exc
        except OSError as exc:
            raise UpscaleIndisponible(f"upscayl-bin inexecutable : {exc}") from exc
        if resultat.returncode != 0 or not sortie.is_file() or sortie.stat().st_size == 0:
            detail = resultat.stderr.decode(errors="replace").strip()[:300]
            raise UpscaleIndisponible(
                f"agrandissement en echec (code {resultat.returncode}){' : ' + detail if detail else ''}"
            )
        return sortie.read_bytes()


def _reduire_si_besoin(octets: bytes, borne_px: int) -> bytes:
    """Redimensionne (SANS jamais agrandir) pour que le plus grand cote ne
    depasse pas `borne_px`, et reencode en JPEG.

    4x sur 660x440 donne 2640x1760 : bien plus que ce qu'aucun gabarit
    publie n'exige (1080x1920 au maximum, le reel). Porter cette taille
    brute jusqu'au rendu Chromium et au cache disque coute pour rien -- la
    reduire ici, une seule fois, avant l'ecriture en cache."""
    from PIL import Image

    with Image.open(io.BytesIO(octets)) as image:
        image = image.convert("RGB")
        if max(image.size) > borne_px:
            image.thumbnail((borne_px, borne_px))
        tampon = io.BytesIO()
        image.save(tampon, format="JPEG", quality=90)
        return tampon.getvalue()


def agrandir_fond(
    octets: bytes,
    *,
    sortie_max_px: int | None = None,
    **options,
) -> bytes:
    """`agrandir`, puis reduit le resultat a une taille raisonnable pour un
    fond de cover (JPEG). Point d'entree public du gel -- voir `agrandir`
    pour les erreurs qu'elle peut lever (`UpscaleIndisponible`)."""
    brut = agrandir(octets, **options)
    borne = sortie_max_px if sortie_max_px is not None else settings.visuels_upscale_sortie_max_px
    return _reduire_si_besoin(brut, borne)
