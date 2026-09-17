from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "static" / "dashboard.html"


def test_le_dashboard_porte_le_selecteur_de_mode_de_publication():
    html = DASHBOARD.read_text()
    assert 'fetch("/publication/mode")' in html
    assert 'data-mode="arret"' in html
    assert 'data-mode="apercu"' in html
    assert 'data-mode="reel"' in html
