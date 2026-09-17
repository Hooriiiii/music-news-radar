from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "static" / "dashboard.html"


def test_le_dashboard_porte_le_selecteur_de_mode_de_publication():
    html = DASHBOARD.read_text()
    assert 'fetch("/publication/mode")' in html
    assert 'data-mode="arret"' in html
    assert 'data-mode="apercu"' in html
    assert 'data-mode="reel"' in html


def test_le_detail_d_un_post_sait_lire_une_video_sans_url():
    """Sentinelle : une video sans URL (reel Instagram sans media_url) s'affiche
    par sa vignette et n'est pas cochable comme slide."""
    html = DASHBOARD.read_text()
    assert 'const sansUrl = m.type === "video" && !m.url;' in html
    assert "m.preview_url || (m.type === \"photo\" ? m.url : null)" in html
