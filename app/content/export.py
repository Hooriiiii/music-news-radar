"""Construction du zip d'export : la cover rendue plus les medias bruts de l'actu.

`render_post` ne rend plus que la cover (slide 1) -- les photos/videos de l'actu
(`kind: "media"`) sont publiees TELLES QUELLES, jamais passees a Chromium. Ce
module telecharge ces medias et assemble l'archive prete a deposer dans Instagram.
"""

import io
import zipfile

from app.content.media import VIDEO
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx

from app.content.render import _USER_AGENT

# 80 Mo couvre largement une photo ou une courte video de reel ; au-dela c'est
# soit une erreur de source, soit un fichier qu'Instagram refuserait de toute
# facon -- pas la peine de le rapatrier en entier pour le decouvrir.
PLAFOND_OCTETS = 80 * 1024 * 1024
DELAI_S = 120.0

# Instagram refuse un fichier dont l'extension ne correspond pas au contenu
# reel : la table part du Content-Type, seule source fiable independamment de
# ce que l'URL affiche (une CDN sert souvent des URLs sans suffixe).
_EXTENSIONS_PAR_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}


def _extension(content_type: str | None, url: str) -> str:
    """Devine l'extension : Content-Type, puis suffixe de l'URL, puis `.bin`.

    Le dernier repli (`.bin`) signale un fichier dont on ne sait rien -- c'est
    a l'appelant d'en faire une ligne dans `medias_manquants.txt`, cette
    fonction ne fait que la meilleure devinette.
    """
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime in _EXTENSIONS_PAR_TYPE:
            return _EXTENSIONS_PAR_TYPE[mime]
    suffixe = PurePosixPath(urlparse(url).path).suffix
    if suffixe and len(suffixe) <= 5:
        return suffixe
    return ".bin"


def telecharger(
    url: str,
    plafond: int = PLAFOND_OCTETS,
    delai: float = DELAI_S,
    client: httpx.Client | None = None,
) -> tuple[bytes | None, str | None]:
    """Le contenu et son extension devinee, ou None et la raison de l'echec.

    Le deuxieme element sert double emploi expres : extension sur succes,
    raison lisible sur echec -- l'appelant discrimine juste sur `contenu is
    None`, sans avoir a porter un troisieme champ partout.
    """
    gere_client = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        with client.stream(
            "GET", url, headers={"User-Agent": _USER_AGENT}, timeout=delai
        ) as reponse:
            reponse.raise_for_status()
            morceaux = bytearray()
            for chunk in reponse.iter_bytes():
                morceaux.extend(chunk)
                # Verifie EN COURS de lecture, pas sur Content-Length : ce
                # dernier peut etre absent ou mensonger, un flux de 2 Go
                # passerait sinon.
                if len(morceaux) > plafond:
                    return None, f"depasse le plafond de {plafond} octets"
            extension = _extension(reponse.headers.get("content-type"), url)
            return bytes(morceaux), extension
    except Exception as exc:  # noqa: BLE001 -- tout echec reseau part en texte lisible
        return None, str(exc)
    finally:
        if gere_client:
            client.close()


def _video_du_reel(post) -> dict | None:
    """La slide video qu'un reel doit absorber, ou None.

    Seul le gabarit reel assemble : un post est un carrousel d'images, sa video
    eventuelle reste un media a part.
    """
    if getattr(post, "template", None) != "kinda_reel":
        return None
    return next(
        (s for s in post.slides if s.get("kind") == "media" and s.get("type") == VIDEO),
        None,
    )


def construire_zip(
    post,
    cover_pngs: list[bytes],
    texte_legende: str,
    telecharger=telecharger,
    assembler=None,
) -> bytes:
    """Assemble l'archive : covers puis medias, dans l'ordre de publication.

    Un media qui echoue ne fait PAS echouer le zip -- meme philosophie que
    l'image de secours du rendu (app/content/render.py::fetch_image_data_uri) :
    un export partiel vaut mieux qu'une erreur, surtout a 23 h.
    """
    if assembler is None:
        from app.content.video import assembler_reel

        assembler = assembler_reel

    manquants: list[str] = []
    tampon = io.BytesIO()
    # La video que l'assemblage a absorbee : on ne la remet pas a part, elle
    # doublerait le poids du zip et laisserait croire a deux medias a publier.
    absorbee = None

    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        numero = 1
        slide_video = _video_du_reel(post)
        if slide_video is not None and cover_pngs:
            octets, raison = telecharger(slide_video["url"])
            if octets is None:
                manquants.append(f"{slide_video['url']} : {raison}")
            else:
                try:
                    archive.writestr(f"{numero:02d}_reel.mp4", assembler(cover_pngs[0], octets))
                    absorbee = slide_video["url"]
                    numero += 1
                except Exception as exc:
                    # Un export partiel vaut mieux qu'une erreur, surtout a 23 h :
                    # on retombe sur cover + video separees, en NOMMANT l'echec.
                    manquants.append(f"assemblage du reel impossible : {exc}")

        for png in cover_pngs:
            archive.writestr(f"{numero:02d}_cover.png", png)
            numero += 1

        for slide in post.slides:
            if slide.get("kind") != "media":
                continue
            if slide.get("url") == absorbee:
                continue
            url = slide["url"]
            contenu, extension_ou_raison = telecharger(url)
            if contenu is None:
                manquants.append(f"{url} : {extension_ou_raison}")
                continue
            extension = extension_ou_raison
            if extension == ".bin":
                # Le fichier est bien dans le zip, mais son extension est une
                # devinette a vide -- un fichier silencieusement inutilisable
                # dans le zip est pire qu'un probleme nomme.
                manquants.append(
                    f"{url} : extension inconnue (.bin), le fichier peut ne pas "
                    "s'importer dans Instagram"
                )
            label = slide.get("type", "media")
            archive.writestr(f"{numero:02d}_{label}{extension}", contenu)
            numero += 1

        # Fichier absent quand la liste est vide : un fichier vide dans le zip
        # ferait croire a un probleme la ou il n'y en a pas.
        if manquants:
            archive.writestr("medias_manquants.txt", "\n".join(manquants))

        archive.writestr("caption.txt", texte_legende)

    return tampon.getvalue()
