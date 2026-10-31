"""Echanger deux creneaux sur Chromium reel : le glissement, a la souris et au doigt.

Un vrai serveur FastAPI sur une vraie base SQLite : le geste ecrit, et c'est la base qu'on
relit. Le glissement est joue par de vrais evenements (souris Playwright, `Input.dispatchTouchEvent`
pour le doigt), pas par des appels de fonction. Regles : tests/api/test_echanger.py.
"""

import datetime as dt
import os
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Article, Creneau, EtatCreneau, FormatCreneau, Post, Source, SourceType
from tests.dashboard.test_dashboard_verification_humaine import chromium_absent

PAGE = (Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")
# A regarder a l'oeil : KINDA_CAPTURES=/un/dossier uv run pytest tests/dashboard/test_dashboard_echanger.py
CAPTURES = os.environ.get("KINDA_CAPTURES")
COVER = {"kind": "cover", "headline": "**A**", "title": "**T**", "subtitle": "**S**", "image_url": "https://img/1.jpg"}
ASSETS = [{"type": "photo", "url": "https://img/1.jpg", "origine": "article", "compte": "@tsugi", "article_id": 1}]


def test_decaler_a_quitte_l_ecran():
    assert "Décaler" not in PAGE and "decaler" not in PAGE and "⏬" not in PAGE


def test_pas_de_drag_and_drop_html5_qui_ne_part_pas_au_doigt():
    assert "draggable" not in PAGE and "ondragstart" not in PAGE


class Monde:
    def __init__(self, fabrique):
        self.fabrique = fabrique
        self.ids = {}

    def ligne(self, nom):
        with self.fabrique() as db:
            c = db.get(Creneau, self.ids[nom])
            return c.article_id, c.post_id, c.epingle, c.etat

    def titre(self, nom):
        article = self.ligne(nom)[0]
        if article is None:
            return None
        with self.fabrique() as db:
            return db.get(Article, article).title


@pytest.fixture()
def monde(tmp_path, monkeypatch):
    """Quatre lignes a venir : deux actus sans post, un libre, un gele, et un compose."""
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
    url = f"sqlite:///{tmp_path / 'console.db'}"
    moteur = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(moteur)
    fabrique = sessionmaker(bind=moteur, autoflush=False, expire_on_commit=False)
    maintenant = dt.datetime.now(dt.timezone.utc)
    m = Monde(fabrique)
    with fabrique() as db:
        source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
        db.add(source)
        db.flush()

        def actu(n):
            article = Article(source_id=source.id, url=f"https://t/{n}", title=f"Actu {n}", raw_hash=f"h{n}",
                              relevance_score=80, published_at=maintenant,
                              media=[{"type": "photo", "url": f"https://img/{n}.jpg"}])
            db.add(article)
            db.flush()
            return article

        def creneau(nom, heures, article=None, post=None, etat=None, epingle=False):
            ligne = Creneau(publier_a=maintenant + dt.timedelta(hours=heures), format=FormatCreneau.CARROUSEL,
                            article_id=article.id if article else None, post_id=post.id if post else None,
                            etat=etat or (EtatCreneau.PROGRAMME if article else EtatCreneau.LIBRE), epingle=epingle)
            db.add(ligne)
            db.flush()
            m.ids[nom] = ligne.id

        a, b, g, c = actu("A"), actu("B"), actu("Gelee"), actu("Composee")
        post = Post(article_id=c.id, article_title=c.title, article_url=c.url, caption="x", hashtags=[],
                    template="kinda_post", slides=[COVER], assets=ASSETS)
        db.add(post)
        db.flush()
        creneau("a", 3, a)
        creneau("gele", 4, g, etat=EtatCreneau.GELE)
        creneau("b", 5, b)
        creneau("libre", 6)
        creneau("compose", 7, c, post, epingle=True)
        db.commit()

    def session():
        with fabrique() as db:
            yield db

    app.dependency_overrides[get_db] = session
    serveur = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off"))
    fil = threading.Thread(target=serveur.run, daemon=True)
    fil.start()
    while not serveur.started:
        time.sleep(0.02)
    m.url = f"http://127.0.0.1:{serveur.servers[0].sockets[0].getsockname()[1]}"
    yield m
    serveur.should_exit = True
    fil.join(5)
    app.dependency_overrides.clear()
    moteur.dispose()


@pytest.fixture()
def navigateur():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


def _ouvrir(navigateur, monde, largeur=1440, hauteur=900, tactile=False):
    contexte = navigateur.new_context(viewport={"width": largeur, "height": hauteur}, has_touch=tactile, is_mobile=tactile)
    page = contexte.new_page()
    page.erreurs = []
    page.on("pageerror", lambda e: page.erreurs.append(str(e)))
    page.on("dialog", lambda d: (page.erreurs.append("dialogue : " + d.message), d.dismiss()))
    page.goto(f"{monde.url}/#programmation")
    page.wait_for_selector("#schedule-list [data-creneau]")
    return page


def _rangee(page, monde, nom):
    return page.locator(f'#schedule-list [data-creneau="{monde.ids[nom]}"]')


def _centre(locator):
    boite = locator.bounding_box()
    return boite["x"] + boite["width"] / 2, boite["y"] + boite["height"] / 2


def _prendre(page, monde, nom):
    x, y = _centre(_rangee(page, monde, nom).locator(".slot-poignee"))
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 10, y + 10, steps=3)
    page.wait_for_selector(".cible-permise, .cible-interdite")


def _vers(page, monde, nom):
    x, y = _centre(_rangee(page, monde, nom))
    page.mouse.move(x, y, steps=5)
    page.wait_for_timeout(50)


def _glisser(page, monde, depuis, vers):
    _prendre(page, monde, depuis)
    _vers(page, monde, vers)
    page.mouse.up()


def _sans_debordement(page):
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1


def _photo(page, nom):
    if CAPTURES:
        page.screenshot(path=str(Path(CAPTURES) / f"{nom}.png"), full_page=False)


# --- 1. A la souris ------------------------------------------------------------------


@chromium_absent
def test_glissement_a_la_souris_echange_et_annonce(navigateur, monde):
    page = _ouvrir(navigateur, monde)
    avant_a, avant_b = monde.ligne("a")[0], monde.ligne("b")[0]

    _prendre(page, monde, "a")
    _vers(page, monde, "b")
    # Pendant : la cible permise ressort, la gelee est grisee AVEC sa raison, en texte.
    assert "cible-survolee" in _rangee(page, monde, "b").get_attribute("class")
    gele = _rangee(page, monde, "gele")
    assert "cible-interdite" in gele.get_attribute("class")
    assert "gelé" in gele.locator(".raison-glisse").inner_text()
    assert gele.locator(".raison-glisse").is_visible()
    _sans_debordement(page)
    _photo(page, "glissement-1440")
    page.mouse.up()

    page.wait_for_selector(".echange-resultat")
    assert monde.ligne("a")[:3] == (avant_b, None, True)
    assert monde.ligne("b")[:3] == (avant_a, None, True)
    assert "Échangés et épinglés :" in _rangee(page, monde, "a").locator(".echange-resultat").inner_text()
    assert "↔" in page.inner_text(".echange-resultat")
    assert _rangee(page, monde, "a").locator(".slot-titre").inner_text() == "Actu B"
    assert page.locator(".raison-glisse, .fantome-glisse").count() == 0
    assert page.erreurs == []
    _sans_debordement(page)
    _photo(page, "resultat-1440")


@chromium_absent
def test_annuler_refait_l_echange_inverse(navigateur, monde):
    page = _ouvrir(navigateur, monde)
    avant_a, avant_b = monde.ligne("a")[0], monde.ligne("b")[0]
    _glisser(page, monde, "a", "b")
    page.wait_for_selector(".annuler-echange")

    page.click(".annuler-echange")

    page.wait_for_function("document.querySelector('.echange-resultat').textContent.includes('annulé')")
    assert monde.ligne("a")[0] == avant_a and monde.ligne("b")[0] == avant_b
    page.wait_for_function(f"""document.querySelector('[data-creneau="{monde.ids['a']}"] .slot-titre').textContent === 'Actu A'""")
    assert page.erreurs == []


@chromium_absent
def test_annuler_refuse_si_l_occupant_a_change_entre_temps(navigateur, monde):
    """L'arbitrage remplit le creneau libere : Annuler n'echange pas une actu jamais vue."""
    page = _ouvrir(navigateur, monde)
    _glisser(page, monde, "a", "libre")
    page.wait_for_selector(".annuler-echange")
    with monde.fabrique() as db:
        source = db.query(Source).first()
        nouvelle = Article(source_id=source.id, url="https://t/N", title="Actu Nouvelle", raw_hash="hN", relevance_score=80,
                           published_at=dt.datetime.now(dt.timezone.utc), media=[{"type": "photo", "url": "https://img/N.jpg"}])
        db.add(nouvelle)
        db.flush()
        db.get(Creneau, monde.ids["a"]).article_id = nouvelle.id
        db.get(Creneau, monde.ids["a"]).etat = EtatCreneau.PROGRAMME
        db.commit()

    page.click(".annuler-echange")

    page.wait_for_selector(".echange-resultat.erreur")
    assert "Annulation refusée" in page.inner_text(".echange-resultat")
    assert monde.titre("libre") == "Actu A"
    assert monde.titre("a") == "Actu Nouvelle"


@chromium_absent
def test_un_gele_n_a_pas_de_poignee_et_refuse_comme_cible(navigateur, monde):
    page = _ouvrir(navigateur, monde)
    assert _rangee(page, monde, "gele").locator(".slot-poignee").count() == 0
    assert _rangee(page, monde, "gele").locator(".echanger-avec").count() == 0
    avant = monde.ligne("a"), monde.ligne("gele")

    _glisser(page, monde, "a", "gele")

    page.wait_for_selector(".refus-ligne")
    assert "gelé" in _rangee(page, monde, "a").locator(".refus-ligne").inner_text()
    assert (monde.ligne("a"), monde.ligne("gele")) == avant
    assert page.erreurs == []


@chromium_absent
def test_un_creneau_passe_n_offre_ni_poignee_ni_echange(navigateur, monde):
    with monde.fabrique() as db:
        passe = Creneau(publier_a=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1), etat=EtatCreneau.LIBRE)
        db.add(passe)
        db.commit()
        monde.ids["passe"] = passe.id
    page = _ouvrir(navigateur, monde)

    assert _rangee(page, monde, "passe").locator(".slot-poignee, .echanger-avec").count() == 0
    assert _rangee(page, monde, "a").locator(".slot-poignee").count() == 1


@chromium_absent
def test_deplacer_un_post_compose_demande_confirmation_et_nomme_les_deux(navigateur, monde):
    page = _ouvrir(navigateur, monde)
    avant = monde.ligne("a")

    _glisser(page, monde, "a", "compose")

    page.wait_for_selector(".prog-validation")
    texte = page.inner_text(".prog-validation")
    assert "Actu A" in texte and "Actu Composee" in texte and "déjà composée" in texte
    assert monde.ligne("a") == avant, "rien n'est ecrit avant la confirmation"
    _photo(page, "confirmation-1440")

    page.click(".prog-validation .confirmer")

    page.wait_for_selector(".echange-resultat")
    assert monde.titre("a") == "Actu Composee"
    assert monde.ligne("compose")[0] == avant[0]
    assert page.erreurs == []


@chromium_absent
def test_renoncer_a_la_confirmation_ne_change_rien(navigateur, monde):
    page = _ouvrir(navigateur, monde)
    avant = monde.ligne("a"), monde.ligne("compose")
    _glisser(page, monde, "a", "compose")
    page.wait_for_selector(".prog-validation")

    page.click(".prog-validation button:not(.confirmer)")

    page.wait_for_selector(".prog-validation", state="detached")
    assert (monde.ligne("a"), monde.ligne("compose")) == avant
    assert _rangee(page, monde, "a").locator(".prog-selecteur").count() == 0


@chromium_absent
def test_lacher_hors_d_une_ligne_ou_echap_n_echange_rien(navigateur, monde):
    page = _ouvrir(navigateur, monde)
    avant = monde.ligne("a")
    _prendre(page, monde, "a")
    page.mouse.move(5, 5)
    page.mouse.up()
    page.wait_for_timeout(150)
    assert monde.ligne("a") == avant
    assert page.locator(".fantome-glisse").count() == 0

    _prendre(page, monde, "a")
    _vers(page, monde, "b")
    page.keyboard.press("Escape")
    page.mouse.up()
    page.wait_for_timeout(150)
    assert monde.ligne("a") == avant
    assert page.evaluate("document.body.classList.contains('glissement-actif')") is False
    assert page.erreurs == []


@chromium_absent
def test_un_refus_du_serveur_au_depot_s_affiche_et_la_liste_revient_au_reel(navigateur, monde):
    """Le gel tombe APRES la lecture des cibles, avant le depot : le serveur juge."""
    page = _ouvrir(navigateur, monde)
    _prendre(page, monde, "a")
    _vers(page, monde, "b")
    with monde.fabrique() as db:
        db.get(Creneau, monde.ids["b"]).etat = EtatCreneau.GELE
        db.commit()
    page.mouse.up()

    page.wait_for_selector(".refus-ligne")
    assert "gelé" in _rangee(page, monde, "a").locator(".refus-ligne").inner_text()
    assert _rangee(page, monde, "b").locator(".status-pill").inner_text() == "Gelé"
    assert page.locator(".echange-resultat").count() == 0
    assert page.erreurs == []


@chromium_absent
def test_un_rechargement_n_interrompt_pas_un_glissement(navigateur, monde):
    page = _ouvrir(navigateur, monde)
    _prendre(page, monde, "a")
    poignee = page.evaluate_handle(f"""document.querySelector('[data-creneau="{monde.ids['a']}"] .slot-poignee')""")

    page.evaluate("loadSchedulePanel()")
    page.wait_for_timeout(250)

    assert poignee.evaluate("el => el.isConnected") is True
    assert page.evaluate("document.body.classList.contains('glissement-actif')") is True
    _vers(page, monde, "b")
    page.mouse.up()
    page.wait_for_selector(".echange-resultat")
    assert monde.titre("a") == "Actu B"
    assert page.locator(".fantome-glisse").count() == 0


@chromium_absent
def test_une_lecture_partie_avant_le_glissement_ne_l_interrompt_pas_a_son_retour(navigateur, monde):
    """La fenetre que la garde d'entree ne voit pas : la liste revient en plein geste."""
    page = _ouvrir(navigateur, monde)
    en_attente = []
    page.route("**/schedule", lambda route: en_attente.append(route))
    page.evaluate("void loadSchedulePanel()")
    while not en_attente:
        page.wait_for_timeout(20)
    _prendre(page, monde, "a")
    poignee = page.evaluate_handle(f"""document.querySelector('[data-creneau="{monde.ids['a']}"] .slot-poignee')""")

    en_attente[0].continue_()
    page.wait_for_timeout(250)

    assert poignee.evaluate("el => el.isConnected") is True
    page.keyboard.press("Escape")
    page.mouse.up()


@chromium_absent
def test_un_rechargement_garde_le_focus_sur_sa_ligne(navigateur, monde):
    page = _ouvrir(navigateur, monde)
    _rangee(page, monde, "b").locator(".echanger-avec").focus()

    page.evaluate("loadSchedulePanel()")
    page.wait_for_timeout(250)

    assert page.evaluate("document.activeElement.className") == "echanger-avec"
    assert page.evaluate("document.activeElement.closest('[data-creneau]').dataset.creneau") == str(monde.ids["b"])


# --- 2. Au doigt, a 390 px ---------------------------------------------------------


def _doigt(page, type_, x=None, y=None):
    points = [{"x": x, "y": y}] if x is not None else []
    page.client.send("Input.dispatchTouchEvent", {"type": type_, "touchPoints": points})


def _cdp(page):
    page.client = page.context.new_cdp_session(page)


@chromium_absent
def test_au_doigt_le_glissement_part_de_la_poignee(navigateur, monde):
    page = _ouvrir(navigateur, monde, largeur=390, hauteur=844, tactile=True)
    _cdp(page)
    _rangee(page, monde, "a").locator(".slot-poignee").scroll_into_view_if_needed()
    x, y = _centre(_rangee(page, monde, "a").locator(".slot-poignee"))
    _doigt(page, "touchStart", x, y)
    for i in range(1, 5):
        _doigt(page, "touchMove", x + i, y + i * 3)
    page.wait_for_selector(".cible-permise")
    assert page.evaluate("document.body.classList.contains('glissement-actif')")
    _sans_debordement(page)
    _photo(page, "glissement-390")
    # La cible est sous le pli : le doigt va au bord, la page defile, puis il la vise.
    _doigt(page, "touchMove", x, 830)
    page.wait_for_function(
        f"""document.querySelector('[data-creneau="{monde.ids['b']}"]').getBoundingClientRect().bottom < 760""")
    _doigt(page, "touchMove", x, 700)
    page.wait_for_timeout(50)
    tx, ty = _centre(_rangee(page, monde, "b"))
    _doigt(page, "touchMove", tx, ty)
    page.wait_for_timeout(50)
    _doigt(page, "touchEnd")

    page.wait_for_selector(".echange-resultat")
    assert monde.titre("a") == "Actu B" and monde.titre("b") == "Actu A"
    assert page.erreurs == []
    _sans_debordement(page)
    _photo(page, "resultat-390")


@chromium_absent
def test_au_doigt_hors_de_la_poignee_la_page_defile_et_rien_ne_glisse(navigateur, monde):
    page = _ouvrir(navigateur, monde, largeur=390, hauteur=500, tactile=True)
    _cdp(page)
    avant = monde.ligne("a")
    x, y = _centre(_rangee(page, monde, "b").locator(".slot-titre"))
    y = min(y, 450)
    depart = page.evaluate("scrollY")
    _doigt(page, "touchStart", x, y)
    for i in range(1, 12):
        _doigt(page, "touchMove", x, y - i * 15)
    page.wait_for_timeout(60)
    assert page.evaluate("document.body.classList.contains('glissement-actif')") is False
    _doigt(page, "touchEnd")
    page.wait_for_timeout(100)
    assert page.evaluate("scrollY") > depart
    assert monde.ligne("a") == avant


@chromium_absent
def test_le_defilement_automatique_pres_du_bord(navigateur, monde):
    page = _ouvrir(navigateur, monde, largeur=1440, hauteur=420)
    _prendre(page, monde, "a")
    depart = page.evaluate("scrollY")
    page.mouse.move(600, 415, steps=2)
    page.wait_for_timeout(350)
    assert page.evaluate("scrollY") > depart
    page.keyboard.press("Escape")
    page.mouse.up()


# --- 3. Sans glisser ----------------------------------------------------------------


@chromium_absent
def test_echanger_avec_au_clavier_sans_glisser(navigateur, monde):
    page = _ouvrir(navigateur, monde)
    avant_a = monde.ligne("a")[0]
    rangee = _rangee(page, monde, "a")
    rangee.locator(".echanger-avec").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".prog-selecteur .prog-jour")

    # Les impossibles sont la, desactives, avec leur raison a cote.
    gele = rangee.locator(".prog-ligne", has=page.locator(".prog-raison")).first
    assert "gelé" in gele.locator(".prog-raison").inner_text()
    assert gele.locator(".prog-option").is_disabled()
    # La premiere cible possible, au clavier jusqu'au bout.
    rangee.locator(".prog-option:enabled").first.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".prog-validation .confirmer")
    assert "Actu A" in page.inner_text(".prog-validation")
    page.locator(".prog-validation .confirmer").focus()
    page.keyboard.press("Enter")

    page.wait_for_selector(".echange-resultat")
    assert monde.ligne("b")[0] == avant_a
    assert page.erreurs == []


@chromium_absent
@pytest.mark.parametrize("largeur", [1440, 390])
def test_le_selecteur_ne_deborde_pas(navigateur, monde, largeur):
    page = _ouvrir(navigateur, monde, largeur=largeur, tactile=largeur == 390)
    _rangee(page, monde, "a").locator(".echanger-avec").click()
    page.wait_for_selector(".prog-selecteur .prog-jour")
    _sans_debordement(page)
    _photo(page, f"selecteur-{largeur}")
    assert page.erreurs == []
