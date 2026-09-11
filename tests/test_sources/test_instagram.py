import datetime as dt

import pytest
from sqlalchemy import select

from app.config import settings
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
    assert "Dates et billets en bio." in items[0].summary


def test_le_summary_porte_les_metriques_d_engagement():
    """Sans ca, un post a 8000 likes et un post a 3 likes du meme compte
    arrivent indistinguables au scoring -- constat de relecture #3."""
    items = make_adapter().parse(PAYLOAD)
    assert "1200 likes" in items[0].summary
    assert "30 commentaires" in items[0].summary
    assert "49000 abonnes" in items[0].summary
    assert "8000 likes" in items[1].summary
    assert "210 commentaires" in items[1].summary


def test_la_date_est_aware_en_utc():
    """Le reste du projet stocke des datetimes AWARE (rss.py, x_api.py,
    `DateTime(timezone=True)` en base) : un naif ferait planter la comparaison
    de retention dans app/pipeline/ingest.py a chaque item."""
    items = make_adapter().parse(PAYLOAD)
    assert items[0].published_at == dt.datetime(2026, 9, 10, 14, 30, tzinfo=dt.UTC)
    assert items[0].published_at.tzinfo is not None


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


class FauxReponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_l_url_cible_le_compte_et_demande_les_bons_champs(monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "17841400000000000")
    url, params = make_adapter().construire_url()

    assert url.endswith("/17841400000000000")
    assert "business_discovery.username(tsugimag)" in params["fields"]
    for champ in ("permalink", "caption", "timestamp", "media_url", "media_type"):
        assert champ in params["fields"]
    assert params["access_token"] == "TOK"


def test_le_carrousel_est_demande_explicitement(monkeypatch):
    """Sans `children`, un carrousel revient sans aucun media."""
    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    _, params = make_adapter().construire_url()
    assert "children" in params["fields"]


def test_fetch_sans_token_dit_quoi_faire(monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", None)
    with pytest.raises(RuntimeError, match="META_ACCESS_TOKEN"):
        make_adapter().fetch()


def test_fetch_note_le_dernier_post_vu(monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    adapter = make_adapter()
    monkeypatch.setattr(adapter, "_appeler", lambda: PAYLOAD)

    adapter.fetch()
    assert adapter.source.state["dernier_post"] == "2026-09-10T14:30:00+00:00"


def test_fetch_ne_rend_que_les_posts_plus_recents_que_l_etat(monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    adapter = make_adapter()
    adapter.source.state = {"dernier_post": "2026-09-09T22:05:00+00:00"}
    monkeypatch.setattr(adapter, "_appeler", lambda: PAYLOAD)

    items = adapter.fetch()
    assert [i.url for i in items] == ["https://www.instagram.com/p/AAA/"]


def test_l_etat_est_reassigne_en_entier(monkeypatch):
    """Piege SQLAlchemy documente : muter un JSONB en place ne persiste pas."""
    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    adapter = make_adapter()
    etat_initial = {"autre": "valeur"}
    adapter.source.state = etat_initial
    monkeypatch.setattr(adapter, "_appeler", lambda: PAYLOAD)

    adapter.fetch()
    assert adapter.source.state is not etat_initial
    assert adapter.source.state["autre"] == "valeur"


def test_une_source_instagram_se_cree_et_se_relit(db_session):
    """De bout en bout sur la base : le type passe le CHECK, l'adapter se
    resout, l'etat se persiste."""
    from app.sources import get_adapter

    source = Source(name="Tsugi IG", type=SourceType.INSTAGRAM, url="ig:tsugimag")
    db_session.add(source)
    db_session.commit()

    relue = db_session.get(Source, source.id)
    adapter = get_adapter(relue)
    assert isinstance(adapter, InstagramAdapter)
    assert adapter.compte == "tsugimag"

    adapter.source.state = {"dernier_post": "2026-09-10T14:30:00"}
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Source, source.id).state["dernier_post"] == "2026-09-10T14:30:00"


def test_ingest_source_insere_les_articles(db_session, monkeypatch):
    """Traverse le VRAI chemin d'ingestion (app/pipeline/ingest.py), pas parse()
    ou fetch() en direct -- c'est l'angle mort qui a laisse passer le constat #1
    (datetime naif) : `ingest_source` applique le filtre de retention a chaque
    item, qui compare `item.published_at` a un `datetime.now(UTC)` aware. Ce
    test DOIT echouer si `_date()` redevient naif (verifie en remettant
    temporairement le `.replace(tzinfo=None)`)."""
    from app.pipeline.ingest import ingest_source

    monkeypatch.setattr(settings, "meta_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    # Les items du payload de reference sont figes en 2026-09 : on aligne la
    # fenetre de retention dessus pour ne pas dependre de la date du jour.
    monkeypatch.setattr(settings, "retention_days", 3650)

    source = Source(name="Tsugi IG", type=SourceType.INSTAGRAM, url="ig:tsugimag")
    db_session.add(source)
    db_session.commit()

    adapter = InstagramAdapter(source)
    monkeypatch.setattr(adapter, "_appeler", lambda: PAYLOAD)

    stats = ingest_source(db_session, source, adapter=adapter)

    assert stats.new == 2
    assert stats.error is None
    from app.models import Article

    urls = set(db_session.scalars(select(Article.url)))
    assert "https://www.instagram.com/p/AAA/" in urls
    assert "https://www.instagram.com/reel/BBB/" in urls
