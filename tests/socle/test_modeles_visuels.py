"""Ou vit le choix des visuels : posts.assets, et le lien artiste -> compte suivi."""

from app.config import settings
from app.models import Artist, Post, Source, SourceCategorie, SourceType


def test_un_post_garde_ses_actifs(db_session):
    actifs = [{"type": "photo", "url": "https://cdn/a.jpg", "origine": "artiste", "compte": "@fredagain"}]
    post = Post(article_title="T", article_url="https://t/1", caption="L", slides=[], assets=actifs)
    db_session.add(post)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Post, post.id).assets == actifs


def test_un_post_sans_actifs_vaut_null(db_session):
    post = Post(article_title="T", article_url="https://t/1", caption="L", slides=[])
    db_session.add(post)
    db_session.commit()
    assert db_session.get(Post, post.id).assets is None


def test_une_fiche_artiste_se_lie_a_un_compte_suivi(db_session):
    import datetime as dt

    source = Source(name="Fred again.. (IG)", type=SourceType.INSTAGRAM, url="ig:fredagain",
                    categorie=SourceCategorie.ARTISTE)
    db_session.add(source)
    db_session.commit()
    maintenant = dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc)
    artist = Artist(name="Fred again..", slug="fred again..", first_seen_at=maintenant,
                    last_seen_at=maintenant, source_instagram_id=source.id)
    db_session.add(artist)
    db_session.commit()
    assert db_session.get(Artist, artist.id).source_instagram_id == source.id


def test_les_reglages_ont_les_defauts_du_design():
    assert settings.cluster_sources_claude == 3
    assert settings.cluster_reevaluations_max_par_run == 10
    assert (settings.visuels_artiste_fenetre_jours, settings.visuels_artiste_generique_jours) == (10, 5)
    assert settings.visuels_max_photos == 3
    assert (settings.visuels_lectures_max_par_passage, settings.visuels_lectures_max_par_jour) == (2, 24)
    assert settings.visuels_pause_quota_minutes == 60


def test_le_cache_des_tests_n_est_jamais_le_vrai(tmp_path):
    assert "Application Support" not in str(settings.visuels_cache_dir)
