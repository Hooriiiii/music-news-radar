"""La fiche d'edition d'un post : UNE liste, et un selecteur qui ne ment pas.

Deux defauts repris de l'utilisateur le 2026-10-05, avec leur mesure.

**TROIS listes montraient le meme visuel** -- « Visuels retenus » (les actifs),
« Médias de l'actu (carrousel) » (des cases a cocher) et « Ce que Meta recevra ».
Les deux premieres editaient le BROUILLON et ignoraient la liste publiee : cocher
une case pouvait faire DISPARAITRE de la publication un visuel retenu a la
composition. Decision de l'utilisateur : il n'en reste qu'une, la liste finale.
Ce fichier verrouille ce que les deux sections supprimees portaient d'utile et qui
devait se retrouver dans la liste unique.

**Le selecteur de format MENTAIT.** Post 176 : `template = "kinda_reel"` en base,
et l'ecran montrait POST actif, REELS grise « pas d'URL directe vers la vidéo ».
Deux causes : aucun des deux boutons ne portait `on` (donc l'oeil lisait le
non-grise comme l'actif), et `raisonPasDeReel` ne mirrorait que
`media.videos_exploitables` en ignorant `media.video_recuperable` -- la question
que le serveur, lui, pose (`store.source_video_reel` rend "ytdlp" pour cet
article, et `raison_ineligible_reel` rend None).
"""

from pathlib import Path

from tests.dashboard.test_dashboard_ecritures import (
    ARTICLE,
    ARTICLE_REEL,
    POST,
    _demarrer_serveur,
    chromium_absent,
)

DASHBOARD = Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html"
PAGE = DASHBOARD.read_text(encoding="utf-8")


def _fiche(post: dict, article: dict, lire):
    from playwright.sync_api import sync_playwright

    journal: list = []
    serveur = _demarrer_serveur(PAGE, journal)
    port = serveur.server_address[1]
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch()
            page = navigateur.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("typeof renderPostDetail === 'function'")
            page.evaluate("([p, a]) => renderPostDetail(p, a, null)", [post, article])
            resultat = lire(page)
            navigateur.close()
        return resultat
    finally:
        serveur.shutdown()


# --- Le selecteur de format ------------------------------------------------


@chromium_absent
def test_un_post_reel_affiche_REELS_comme_le_format_ACTIF():
    """Et jamais POST : l'etat affiche est celui de la base, ou il n'est pas."""
    post = dict(POST, template="kinda_reel")
    etat = _fiche(post, ARTICLE_REEL, lambda page: {
        "reel_actif": "on" in (page.get_attribute("#tpl-reel", "class") or "").split(),
        "post_actif": "on" in (page.get_attribute("#tpl-post", "class") or "").split(),
        "reel_grise": page.get_attribute("#tpl-reel", "disabled") is not None,
    })
    assert etat["reel_actif"], etat
    assert not etat["post_actif"], etat
    assert not etat["reel_grise"], etat


@chromium_absent
def test_une_video_sans_URL_directe_mais_recuperable_par_ytdlp_laisse_REELS_ouvert():
    """LE cas du post 176, et la moitie du mensonge. Meta omet `media_url` des
    reels a musique protegee : la video n'a pas d'URL, le permalien est un
    `/reel/`, et yt-dlp ira la chercher AU GEL -- le serveur juge l'article
    eligible. Griser REELS la-dessus annonce impossible ce que le pipeline fait
    tous les jours."""
    post = dict(POST, template="kinda_post")
    etat = _fiche(post, ARTICLE_REEL, lambda page: {
        "grise": page.get_attribute("#tpl-reel", "disabled") is not None,
        "raison": page.query_selector("#reel-reason") is not None,
    })
    assert not etat["grise"], etat
    assert not etat["raison"], etat


@chromium_absent
def test_sans_aucune_video_REELS_reste_grise_AVEC_sa_raison():
    """La regle « un controle desactive imprime toujours sa raison » ne bouge
    pas : c'est le faux positif qu'on a retire, pas le garde-fou."""
    etat = _fiche(POST, ARTICLE, lambda page: {
        "grise": page.get_attribute("#tpl-reel", "disabled") is not None,
        "raison": page.text_content("#reel-reason") or "",
    })
    assert etat["grise"], etat
    assert "aucune vidéo dans cette actu" in etat["raison"], etat


@chromium_absent
def test_un_reel_dont_l_actu_a_perdu_sa_video_le_DIT_sans_se_contredire():
    """Troisieme cas, et celui qu'on ne peut pas resoudre en grisant : le post EST
    un reel et l'actu n'offre plus de video. Griser le format courant mentirait
    sur la base ; se taire cacherait un vrai probleme. On dit ce qui va se passer
    -- le gel LIBERE le creneau, il n'exclut pas l'article."""
    post = dict(POST, template="kinda_reel")
    etat = _fiche(post, ARTICLE, lambda page: {
        "grise": page.get_attribute("#tpl-reel", "disabled") is not None,
        "actif": "on" in (page.get_attribute("#tpl-reel", "class") or "").split(),
        "raison": page.text_content("#reel-reason") or "",
    })
    assert not etat["grise"], etat
    assert etat["actif"], etat
    assert "Le gel libérera le créneau" in etat["raison"], etat


# --- UNE seule liste --------------------------------------------------------


@chromium_absent
def test_la_fiche_ne_porte_plus_qu_UNE_liste_de_visuels():
    """Trois listes du meme visuel, dont deux editaient le brouillon sans savoir
    ce qui part. Il n'en reste qu'une."""
    post = dict(
        POST,
        assets=[{"origine": "artiste", "compte": "@charli_xcx", "type": "photo",
                 "url": "https://exemple.test/a.jpg", "publie_le": None,
                 "permalink": "https://instagram.com/p/AAA/"}],
        slides=[POST["slides"][0],
                {"kind": "media", "type": "photo", "url": "https://exemple.test/a.jpg"}],
    )
    article = dict(ARTICLE, media=[{"type": "photo", "url": "https://exemple.test/m.jpg"}])
    etat = _fiche(post, article, lambda page: {
        "listes": page.evaluate(
            "document.querySelectorAll('#post-detail .media-list, #medias-actu').length"
        ),
        "elements": page.evaluate(
            "document.querySelectorAll('#elements-liste .element-ligne').length"
        ),
        "titres": page.text_content("#post-detail") or "",
    })
    assert etat["listes"] == 0, "plus aucune des deux listes concurrentes"
    assert etat["elements"] == 2, "la cover plus l'unique media"
    assert "Visuels retenus" not in etat["titres"]
    assert "Médias de l'actu" not in etat["titres"]


@chromium_absent
def test_la_provenance_d_un_visuel_survit_a_la_fusion():
    """« Visuels retenus » portait seule le compte, la date et le permalien du
    post d'origine. Les perdre avec la section serait perdre la seule facon de
    verifier d'ou vient une image avant de la publier."""
    post = dict(
        POST,
        assets=[{"origine": "artiste", "compte": "@charli_xcx", "type": "photo",
                 "url": "https://exemple.test/a.jpg", "publie_le": "2026-10-01T10:00:00Z",
                 "permalink": "https://instagram.com/p/AAA/"}],
        slides=[POST["slides"][0],
                {"kind": "media", "type": "photo", "url": "https://exemple.test/a.jpg",
                 "origine": "artiste", "compte": "@charli_xcx"}],
    )
    texte = _fiche(post, ARTICLE, lambda page: page.text_content("#elements-liste") or "")
    lien = _fiche(post, ARTICLE, lambda page: page.get_attribute(
        "#elements-liste a", "href"))
    assert "@charli_xcx" in texte
    assert "compte d'un artiste suivi" in texte
    assert lien == "https://instagram.com/p/AAA/"


@chromium_absent
def test_un_post_sans_creneau_reste_editable():
    """`GET /relecture` ne rend `candidats` que pour un post POSE sur un creneau.
    Les cases « Médias de l'actu » etaient jusqu'ici le SEUL chemin d'edition
    pour un brouillon qui n'est sur aucun creneau : les supprimer sans reserve
    locale l'aurait rendu inmodifiable."""
    article = dict(ARTICLE, media=[{"type": "photo", "url": "https://exemple.test/m.jpg"}])
    etat = _fiche(POST, article, lambda page: {
        "options": page.evaluate("document.querySelectorAll('#el-ajouter option').length"),
        "actif": page.is_enabled("#el-ajouter"),
    })
    assert etat["options"] == 2, "l'entete plus le media de l'actu"
    assert etat["actif"]


@chromium_absent
def test_un_visuel_de_compte_media_n_entre_jamais_dans_la_reserve():
    """Decision du 2026-09-17, et elle ne depend pas du serveur ici : sans
    creneau, la reserve est calculee dans le navigateur."""
    article = dict(ARTICLE, media=[{"type": "photo", "url": "https://exemple.test/m.jpg"}],
                   visuels_interdits=True)
    etat = _fiche(POST, article, lambda page: {
        "options": page.evaluate("document.querySelectorAll('#el-ajouter option').length"),
        "texte": page.text_content("#post-detail") or "",
    })
    assert etat["options"] == 1, "l'entete seule"
    assert "visuel d'un compte média : interdit" in etat["texte"]


# --- Le texte : une zone d'edition, un apercu REPLIE ------------------------


@chromium_absent
def test_les_compteurs_restent_visibles_et_l_apercu_est_replie():
    """La legende s'affichait DEUX FOIS. L'apercu se replie ; les COMPTEURS, non
    -- ils signalent un depassement avant qu'il coute un post."""
    import datetime as dt

    plus_tard = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)
    depart = {
        "creneau_id": 1, "post_id": POST["id"], "publier_a": plus_tard.isoformat(),
        "format": "carrousel", "etat": "programme", "accroche": "x",
        "legende_publiee": "Le texte exact qui part.", "legende_longueur": 24,
        "legende_maximum": 2150, "hashtags_publies": ["#a"], "hashtags_maximum": 30,
        "credit": "@x", "fond": None, "fond_origine": "article", "elements_figes": None,
        "elements": None, "candidats": [], "liste_arretee": False,
        "editable": True, "raison_non_editable": None, "reouvrable": True,
        "raison_non_reouvrable": None, "minutes_restantes": 180,
    }

    def lire(page):
        page.evaluate("(d) => { departsParPost = new Map([[d.post_id, d]]); }", depart)
        page.evaluate("([p, a]) => renderPostDetail(p, a, null)", [POST, ARTICLE])
        return {
            "mesures": page.is_visible(".depart-mesures"),
            "replie": page.evaluate(
                "!document.querySelector('.apercu-publie').open"
            ),
            "apercu_cache": not page.is_visible(".depart-apercu"),
            "zones": page.evaluate("document.querySelectorAll('#edit-caption').length"),
        }

    etat = _fiche(POST, ARTICLE, lire)
    assert etat["mesures"], "les compteurs ne se cachent jamais"
    assert etat["replie"] and etat["apercu_cache"], "l'apercu est replie par defaut"
    assert etat["zones"] == 1, "une seule zone d'edition de la legende"
