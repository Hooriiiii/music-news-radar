"""Le nettoyage d'une URL de media. Zero dependance : stdlib seule.

Une URL qui traverse du HTML en ressort ECHAPPEE : `&` y devient `&amp;`, parce
que c'est la regle d'un attribut HTML (`<img src="...&amp;...">`,
`<meta content="...&amp;...">`). Stockee telle quelle, elle n'est plus la meme
URL -- et le CDN d'Instagram, dont les liens signes portent `_nc_cat`, `ccb`,
`oh` et `oe` separes par des `&`, la refuse.

C'est ce qui a fait publier le reel du 2026-09-17 20:24 sur le fond de secours du
gabarit : Chromium demandait l'URL echappee, recevait une erreur, et
`fetch_image_data_uri` retombait EN SILENCE sur `fallback.jpg`.

Deux fonctions, deux usages :
- `url_propre` : la forme canonique d'une URL, posee des l'ingestion ;
- `url_telechargeable` : la meme, plus la garantie qu'on peut aller la chercher
  sur le reseau (http/https seulement -- ni `file://`, qui ouvrirait n'importe
  quel fichier de la machine, ni `javascript:` ou `data:`, qui n'ont rien a faire
  dans une requete sortante).
"""

import html
from urllib.parse import urlsplit

SCHEMAS_RESEAU = ("http", "https")

# Un double echappement (`&amp;amp;`) existe des qu'un HTML deja echappe repasse
# dans un echappeur. Une borne, plutot qu'une boucle libre : `&amp;` ne se
# reconstruit pas indefiniment, mais on ne veut pas dependre de cette intuition.
_PASSES_MAX = 3


def url_propre(url: str | None) -> str | None:
    """L'URL debarrassee de ses entites HTML et de ses espaces, ou None si vide."""
    if not url:
        return None
    propre = url.strip()
    for _ in range(_PASSES_MAX):
        suivant = html.unescape(propre)
        if suivant == propre:
            break
        propre = suivant
    return propre or None


def url_telechargeable(url: str | None) -> str | None:
    """L'URL nettoyee si l'on peut aller la chercher en http(s), sinon None."""
    propre = url_propre(url)
    if propre is None or urlsplit(propre).scheme.lower() not in SCHEMAS_RESEAU:
        return None
    return propre
