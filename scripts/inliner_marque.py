"""Fabrique les deux URI `data:` de marque que `dashboard.html` porte en dur.

Pourquoi en dur, et pas servies : `app/main.py` rend le dashboard par un
`FileResponse` UNIQUE et ne monte aucun `StaticFiles`. Monter un repertoire
statique pour deux PNG de marque ajouterait une route, deux requetes reseau au
premier rendu (donc un logo qui apparait apres le texte) et une facon de se
tromper de plus (un 404 muet sur le logo). La page reste donc UN SEUL FICHIER,
autonome, sans CDN : la propriete qu'on aime.

Le cout est mesure et borne : les deux PNG de `app/static/marque/` sont la
SOURCE DE VERITE, et ce script en derive des versions ecrites pour l'ecran.
- Le logo est aplati a la creme EXACTE de la charte (#FEF1F7) : l'original porte
  7 648 couleurs distinctes (un anticrenelage autour d'un #FFF7FA a peine
  different), qui coutaient 30 ko de base64 pour rien. A plat : 13,7 ko, et la
  teinte devient celle de la charte plutot que celle du PNG.
- L'etoile devient un MASQUE (gris+alpha) : le CSS la recolore alors librement
  (rose d'accent), ce qu'un `<img>` ne permet pas. 1,9 ko.

Regenerer apres une refonte du logo :
    uv run python -m scripts.inliner_marque --apply
"""

from __future__ import annotations

import argparse
import base64
import io
import re
from pathlib import Path

from PIL import Image

RACINE = Path(__file__).resolve().parent.parent
MARQUE = RACINE / "app" / "static" / "marque"
DASHBOARD = RACINE / "app" / "static" / "dashboard.html"

# La creme de la charte, extraite du PDF de marque.
CREME = (0xFE, 0xF1, 0xF7)
LOGO_LARGEUR = 240  # affiche ~66 px de large : deux fois la densite, pas plus.
ETOILE_LARGEUR = 64


def _redimensionner(nom: str, largeur: int) -> Image.Image:
    im = Image.open(MARQUE / nom).convert("RGBA")
    hauteur = round(im.height * largeur / im.width)
    return im.resize((largeur, hauteur), Image.LANCZOS)


def _encoder(im: Image.Image) -> str:
    tampon = io.BytesIO()
    im.save(tampon, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(tampon.getvalue()).decode("ascii")


def logo_creme() -> str:
    """Le mot-symbole, aplati a la creme de la charte, alpha conserve."""
    im = _redimensionner("kinda-logo.png", LOGO_LARGEUR)
    plans = [Image.new("L", im.size, canal) for canal in CREME]
    return _encoder(Image.merge("RGBA", (*plans, im.getchannel("A"))))


def etoile_masque() -> str:
    """L'etoile en masque : le CSS decide de sa couleur."""
    im = _redimensionner("kinda-etoile.png", ETOILE_LARGEUR)
    blanc = Image.new("L", im.size, 0xFF)
    return _encoder(Image.merge("LA", (blanc, im.getchannel("A"))))


# Chaque signe a son point d'ancrage : l'etoile est une variable CSS (le masque
# doit pouvoir etre recolore), le logo est le `src` d'une `<img class=
# "logo-kinda">` -- une variable CSS ne peut pas porter d'`alt`.
MOTIFS = {
    "etoile": (
        re.compile(r"(  --etoile-kinda: url\()[^)]*(\);)"),
        etoile_masque,
    ),
    "logo": (
        re.compile(r'(<img class="logo-kinda" src=")[^"]*(")'),
        logo_creme,
    ),
}


def appliquer(texte: str) -> str:
    for nom, (motif, fabrique) in MOTIFS.items():
        nouveau, n = motif.subn(lambda m: m.group(1) + fabrique() + m.group(2), texte)
        if n != 1:
            raise SystemExit(f"{nom} : {n} occurrence(s) dans dashboard.html, 1 attendue")
        texte = nouveau
    return texte


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="reecrit dashboard.html")
    args = parser.parse_args()
    for nom, (_motif, fabrique) in MOTIFS.items():
        print(f"{nom} : {len(fabrique())} caracteres")
    if not args.apply:
        print("\n(plan seulement ; --apply pour reecrire dashboard.html)")
        return
    DASHBOARD.write_text(appliquer(DASHBOARD.read_text(encoding="utf-8")), encoding="utf-8")
    print("\ndashboard.html reecrit")


if __name__ == "__main__":
    main()
