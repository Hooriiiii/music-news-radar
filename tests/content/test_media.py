"""Le predicat « video exploitable », sans reseau ni base.

Il decide si le switch REELS apparait. Une video non exploitable doit dire
POURQUOI : un bouton grise sans explication est une enigme, et les trois causes
(pas de video, pas d'URL directe, trop longue) ne se depannent pas pareil.
"""

import pytest

from app.content.media import (
    DUREE_MAX_REEL_MS,
    entree,
    est_lien_instagram,
    lien_de_reel,
    raison_pas_de_reel,
    video_recuperable,
    videos_exploitables,
)


def _video(**kwargs) -> dict:
    base = {
        "type": "video",
        "url": "https://video.twimg.com/vid.mp4",
        "duration_ms": 31_000,
        "width": 1280,
        "height": 720,
    }
    return {**base, **kwargs}


def _photo() -> dict:
    return {"type": "photo", "url": "https://pbs.twimg.com/photo.jpg"}


def test_une_video_courte_avec_url_directe_est_exploitable():
    assert videos_exploitables([_photo(), _video()]) == [_video()]
    assert raison_pas_de_reel([_video()]) is None


def test_aucun_media_ou_photos_seules_ne_donnent_pas_de_reel():
    assert videos_exploitables([]) == []
    assert videos_exploitables([_photo()]) == []
    assert "aucune video" in raison_pas_de_reel([_photo()])
    assert "aucune video" in raison_pas_de_reel([])


def test_une_video_sans_url_directe_nest_pas_exploitable():
    """Cas d'un RSS qui ne donne qu'un lien de page : on ne peut rien telecharger."""
    sans_url = _video(url=None)

    assert videos_exploitables([sans_url]) == []
    assert "URL directe" in raison_pas_de_reel([sans_url])


def test_une_video_trop_longue_nest_pas_exploitable_et_la_raison_dit_la_duree():
    trop_longue = _video(duration_ms=240_000)

    assert videos_exploitables([trop_longue]) == []
    raison = raison_pas_de_reel([trop_longue])
    assert "90" in raison and "240" in raison or "4 min" in raison


def test_une_duree_inconnue_passe():
    """Une source qui ne donne pas la duree ne doit pas faire perdre la video : on
    prefere proposer le reel et laisser l'utilisateur constater, plutot que de
    masquer le switch sur une donnee absente."""
    assert videos_exploitables([_video(duration_ms=None)]) == [_video(duration_ms=None)]


def test_la_borne_de_duree_est_inclusive():
    assert videos_exploitables([_video(duration_ms=DUREE_MAX_REEL_MS)])
    assert not videos_exploitables([_video(duration_ms=DUREE_MAX_REEL_MS + 1)])


SANS_URL = entree("video", None, preview_url="https://cdn/vignette.jpg")


@pytest.mark.parametrize(
    "url, attendu",
    [
        ("https://www.instagram.com/reel/DAbc123/", True),
        ("https://instagram.com/reels/DAbc123/", True),
        ("https://www.instagram.com/p/DAbc123/", False),
        ("https://www.instagram.com/reel/", False),
        ("https://evil.example/www.instagram.com/reel/DAbc123/", False),
        ("https://instagram.com.evil.example/reel/DAbc123/", False),
        (None, False),
    ],
)
def test_lien_de_reel(url, attendu):
    assert lien_de_reel(url) is attendu


def test_est_lien_instagram():
    assert est_lien_instagram("https://www.instagram.com/p/A/")
    assert not est_lien_instagram("https://tsugi.fr/a")
    assert not est_lien_instagram(None)
    # https seulement : yt-dlp ne part jamais en clair, ni sur un autre schema.
    assert not est_lien_instagram("http://www.instagram.com/reel/A/")
    assert not est_lien_instagram("ftp://www.instagram.com/reel/A/")


def test_une_video_sans_url_n_est_toujours_pas_exploitable_directement():
    """Le switch REELS MANUEL reste grise : l'export du dashboard ne lance pas
    yt-dlp. Seuls les reels automatiques la recuperent, au gel."""
    assert videos_exploitables([SANS_URL]) == []
    assert "URL directe" in raison_pas_de_reel([SANS_URL])


@pytest.mark.parametrize(
    "media, permalink, attendu",
    [
        ([SANS_URL], "https://www.instagram.com/p/AAA/", True),
        ([SANS_URL], "https://www.instagram.com/reel/AAA/", True),
        ([], "https://www.instagram.com/reel/AAA/", True),
        ([entree("photo", "https://img/1.jpg")], "https://www.instagram.com/reel/AAA/", True),
        # Carrousel : yt-dlp rendrait une liste, on ne saurait pas quel element prendre.
        ([entree("photo", "https://img/1.jpg"), SANS_URL], "https://www.instagram.com/p/AAA/", False),
        ([entree("photo", "https://img/1.jpg")], "https://www.instagram.com/p/AAA/", False),
        # yt-dlp n'est jamais lance hors d'Instagram.
        ([SANS_URL], "https://tsugi.fr/a", False),
        ([SANS_URL], None, False),
    ],
)
def test_video_recuperable(media, permalink, attendu):
    assert video_recuperable(media, permalink) is attendu


# --- La duree lue dans l'URL du CDN Instagram (2026-09-21) -------------------
#
# Mesure du jour : les 642 videos Instagram de la base ont `duration_ms` a None
# (l'adapter ne remplit pas le champ, Graph ne le donne pas), et les 642 portent
# pourtant leur duree dans le parametre `efg` de leur URL signee. Sans cette
# lecture, une video de 190 s passe le predicat « duree inconnue », se fait
# composer (Claude paye) et n'echoue qu'au gel, apres telechargement.

def _url_cdn(duree_s: int | None) -> str:
    import base64
    import json

    charge = {"venc_tag": "xpv_progressive", "xpv_asset_id": 1, "urlgen_source": "www"}
    if duree_s is not None:
        charge["duration_s"] = duree_s
    efg = base64.b64encode(json.dumps(charge).encode()).decode().rstrip("=")
    return f"https://scontent-cdg4-3.cdninstagram.com/o1/v/t16/f2/m84/AQ.mp4?_nc_cat=110&efg={efg}&ccb=17-1"


def test_la_duree_est_lue_dans_l_url_du_cdn_instagram():
    m = entree("video", _url_cdn(190))
    assert m["duration_ms"] == 190_000
    assert videos_exploitables([m]) == []
    assert "190 s" in raison_pas_de_reel([m])


def test_une_duree_fournie_par_la_source_fait_foi():
    # La sonde ffprobe et l'API X sont plus sures que l'URL : on ne les ecrase pas.
    m = entree("video", _url_cdn(190), duration_ms=12_000)
    assert m["duration_ms"] == 12_000


def test_une_url_sans_duree_lisible_reste_inconnue():
    for url in (_url_cdn(None), "https://video.twimg.com/vid.mp4", "https://x.test/a?efg=pas-du-base64"):
        assert entree("video", url)["duration_ms"] is None


def test_la_duree_de_l_url_ne_vaut_que_pour_une_video():
    assert entree("photo", _url_cdn(190))["duration_ms"] is None
