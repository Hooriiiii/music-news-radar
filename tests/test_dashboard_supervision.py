"""Sentinelles du bandeau : ce qui se verifie sans navigateur.

Le rendu (hierarchie, espacement, troncature) se constate dans un navigateur et
nulle part ailleurs -- la methode l'exige. Ce que ces tests tiennent, c'est la
PROPRIETE : aucun chemin du JS ne doit pouvoir afficher du vert sans l'avoir lu
sur le serveur.
"""

from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "static" / "dashboard.html"


def test_le_bandeau_est_dans_l_entete_donc_visible_sur_tous_les_panneaux():
    html = DASHBOARD.read_text()
    entete = html.split("<header>", 1)[1].split("</header>", 1)[0]
    assert 'id="supervision"' in entete
    assert 'id="sup-pills"' in entete and 'id="sup-raisons"' in entete
    assert 'fetch("/supervision")' in html


def test_le_bandeau_part_en_inconnu_et_jamais_en_vert():
    """Un voyant vert par defaut sur une machine en panne est pire que rien."""
    html = DASHBOARD.read_text()
    assert 'class="supervision niv-inconnu"' in html
    # Le niveau vient du serveur : aucun repli sur "ok" cote navigateur -- tous
    # les replis du JS du bandeau valent "inconnu".
    assert 'niv-ok"' not in html.split('<section id="supervision"', 1)[1].split("</section>", 1)[0]
    bloc = html.split("const SUP_ICONES", 1)[1].split("async function chargerSupervision", 1)[0]
    assert ': "inconnu";' in bloc
    assert ': "ok";' not in bloc and '|| "ok"' not in bloc


def test_une_requete_en_echec_laisse_le_bandeau_inconnu_avec_sa_raison():
    html = DASHBOARD.read_text()
    assert "function supInconnu(" in html
    assert 'niveau: "inconnu"' in html
    assert "supervision injoignable" in html


def test_un_niveau_inconnu_du_serveur_est_respecte_tel_quel():
    html = DASHBOARD.read_text()
    assert 'const niveau = SUP_ICONES[etat.niveau] ? etat.niveau : "inconnu";' in html


def test_chaque_voyant_non_vert_imprime_sa_raison_a_l_ecran():
    """Adjacente et visible, jamais seulement dans un `title`."""
    html = DASHBOARD.read_text()
    assert 'if (v.niveau === "ok" || !v.raison) continue;' in html
    assert "ligne.querySelector(\"span\").textContent = v.raison;" in html


def test_le_panneau_sources_affiche_la_sante_de_chaque_source():
    html = DASHBOARD.read_text()
    assert "s.last_error" in html and "s.last_ok_at" in html
    assert "jamais lue avec succès" in html
