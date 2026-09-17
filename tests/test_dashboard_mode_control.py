from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "static" / "dashboard.html"


def test_le_dashboard_porte_le_selecteur_de_mode_de_publication():
    html = DASHBOARD.read_text()
    assert 'fetch("/publication/mode")' in html
    assert 'data-mode="arret"' in html
    assert 'data-mode="apercu"' in html
    assert 'data-mode="reel"' in html


def test_le_panneau_programmation_montre_le_format_et_l_offre_de_reels():
    html = DASHBOARD.read_text()
    assert 'fetch("/schedule/offre-reels")' in html
    assert 'id="offre-reels"' in html
    assert "format-pill" in html
    assert "FORMATS_CRENEAU" in html


def test_le_panneau_programmation_porte_l_interrupteur_des_reels():
    html = DASHBOARD.read_text()
    assert 'id="reels-actifs"' in html
    assert 'fetch("/publication/reels")' in html
    assert 'fetch("/publication/reels", {' in html


def test_la_case_des_reels_est_decochee_par_defaut():
    """Reels en opt-in : jamais cochee sans reponse explicite de l'API."""
    html = DASHBOARD.read_text()
    assert '<input type="checkbox" id="reels-actifs"> Reels auto' in html
    assert "caseReels.checked = etat.actifs === true;" in html
    assert "caseReels.checked = false;" in html


def test_le_detail_d_un_post_sait_lire_une_video_sans_url():
    """Sentinelle : une video sans URL (reel Instagram sans media_url) s'affiche
    par sa vignette et n'est pas cochable comme slide."""
    html = DASHBOARD.read_text()
    assert 'const sansUrl = m.type === "video" && !m.url;' in html
    assert "m.preview_url || (m.type === \"photo\" ? m.url : null)" in html


def test_un_creneau_reclame_recemment_s_affiche_en_cours_de_publication():
    """Pendant l'attente de l'heure, le creneau est en ECHEC de reclamation : le
    dashboard dit « Publication en cours », sans detail d'erreur."""
    html = DASHBOARD.read_text()
    assert "Publication en cours" in html
    assert '"publication interrompue avant confirmation"' in html
    assert "20 * 60 * 1000" in html
