"""Le predicat « video exploitable », sans reseau ni base.

Il decide si le switch REELS apparait. Une video non exploitable doit dire
POURQUOI : un bouton grise sans explication est une enigme, et les trois causes
(pas de video, pas d'URL directe, trop longue) ne se depannent pas pareil.
"""

from app.content.media import (
    DUREE_MAX_REEL_MS,
    raison_pas_de_reel,
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
