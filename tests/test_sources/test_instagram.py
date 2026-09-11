import datetime as dt

import pytest

from app.models import Source, SourceType
from app.sources.instagram import InstagramAdapter, titre_depuis_caption

# Forme reelle d'une reponse business_discovery : le compte cible est imbrique
# sous la cle `business_discovery` de NOTRE compte.
PAYLOAD = {
    "business_discovery": {
        "username": "tsugimag",
        "followers_count": 49000,
        "media": {
            "data": [
                {
                    "id": "1",
                    "caption": "Laurent Garnier annonce une tournee. Dates et billets en bio.",
                    "media_type": "IMAGE",
                    "media_url": "https://cdn.example/photo.jpg",
                    "permalink": "https://www.instagram.com/p/AAA/",
                    "timestamp": "2026-09-10T14:30:00+0000",
                    "like_count": 1200,
                    "comments_count": 30,
                },
                {
                    "id": "2",
                    "caption": "Le live de Sara Landry hier soir",
                    "media_type": "VIDEO",
                    "media_url": "https://cdn.example/video.mp4",
                    "thumbnail_url": "https://cdn.example/vignette.jpg",
                    "permalink": "https://www.instagram.com/reel/BBB/",
                    "timestamp": "2026-09-09T22:05:00+0000",
                    "like_count": 8000,
                    "comments_count": 210,
                },
            ]
        },
    }
}


def make_adapter(url="ig:tsugimag"):
    source = Source(id=1, name="Tsugi IG", type=SourceType.INSTAGRAM, url=url)
    return InstagramAdapter(source)


@pytest.mark.parametrize(
    "url, attendu",
    [
        ("ig:tsugimag", "tsugimag"),
        ("ig:@tsugimag", "tsugimag"),
        ("IG:TsugiMag", "TsugiMag"),
        ("  ig:tsugimag  ", "tsugimag"),
        ("https://www.instagram.com/tsugimag/", "tsugimag"),
    ],
)
def test_le_compte_se_lit_sous_plusieurs_formes(url, attendu):
    assert make_adapter(url).compte == attendu


def test_parse_rend_un_item_par_media():
    items = make_adapter().parse(PAYLOAD)
    assert len(items) == 2


def test_l_url_est_le_permalink_pas_le_media():
    """Le permalink est stable et citable ; media_url est une URL CDN signee qui
    expire. C'est le permalink qui doit servir de cle de dedup."""
    items = make_adapter().parse(PAYLOAD)
    assert items[0].url == "https://www.instagram.com/p/AAA/"


def test_le_titre_est_la_premiere_phrase_de_la_caption():
    items = make_adapter().parse(PAYLOAD)
    assert items[0].title == "Laurent Garnier annonce une tournee."


def test_le_summary_garde_la_caption_entiere():
    items = make_adapter().parse(PAYLOAD)
    assert items[0].summary.endswith("Dates et billets en bio.")


def test_la_date_est_en_utc_naive():
    items = make_adapter().parse(PAYLOAD)
    assert items[0].published_at == dt.datetime(2026, 9, 10, 14, 30)
    assert items[0].published_at.tzinfo is None


def test_une_photo_donne_un_media_type_photo():
    media = make_adapter().parse(PAYLOAD)[0].media
    assert media == [
        {
            "type": "photo",
            "url": "https://cdn.example/photo.jpg",
            "preview_url": None,
            "duration_ms": None,
            "width": None,
            "height": None,
        }
    ]


def test_une_video_porte_son_type_et_sa_vignette():
    """Le type `video` est ce qui rend le format REELS decidable en aval --
    contrairement a X, Instagram le donne directement."""
    media = make_adapter().parse(PAYLOAD)[1].media
    assert media[0]["type"] == "video"
    assert media[0]["url"] == "https://cdn.example/video.mp4"
    assert media[0]["preview_url"] == "https://cdn.example/vignette.jpg"


def test_un_carrousel_rend_tous_ses_enfants():
    payload = {
        "business_discovery": {
            "media": {
                "data": [
                    {
                        "id": "3",
                        "caption": "Le line-up complet",
                        "media_type": "CAROUSEL_ALBUM",
                        "permalink": "https://www.instagram.com/p/CCC/",
                        "timestamp": "2026-09-08T10:00:00+0000",
                        "children": {
                            "data": [
                                {"media_type": "IMAGE", "media_url": "https://cdn.example/1.jpg"},
                                {
                                    "media_type": "VIDEO",
                                    "media_url": "https://cdn.example/2.mp4",
                                    "thumbnail_url": "https://cdn.example/2.jpg",
                                },
                            ]
                        },
                    }
                ]
            }
        }
    }
    media = make_adapter().parse(payload)[0].media
    assert [m["type"] for m in media] == ["photo", "video"]


def test_un_post_sans_permalink_est_ignore():
    """Sans URL, pas de cle de dedup : l'article serait ingere a chaque run."""
    payload = {
        "business_discovery": {
            "media": {"data": [{"id": "4", "caption": "x", "media_type": "IMAGE"}]}
        }
    }
    assert make_adapter().parse(payload) == []


def test_un_payload_sans_business_discovery_rend_une_liste_vide():
    """Cas du compte non professionnel : Meta ne renvoie pas la cle."""
    assert make_adapter().parse({}) == []


@pytest.mark.parametrize(
    "caption, attendu",
    [
        ("Une phrase. Une autre.", "Une phrase."),
        ("Pas de point final", "Pas de point final"),
        ("Ligne une\nLigne deux", "Ligne une"),
        ("", "Post Instagram"),
        (None, "Post Instagram"),
        ("#techno #rave", "Post Instagram"),
        ("A" * 400, "A" * 197 + "..."),
    ],
)
def test_titre_depuis_caption(caption, attendu):
    assert titre_depuis_caption(caption) == attendu
