import io
import math
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.content.layout import GABARIT_POST
from app.content.render import RenduIndisponible, build_document, fetch_image_data_uri, render_post


def _chromium_playwright_present() -> bool:
    """Le Chromium de Playwright n'est pas sur le PATH : on cherche son cache."""
    caches = [
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    return any(c.exists() and any(c.glob("chromium-*")) for c in caches)


chromium_absent = pytest.mark.skipif(
    not _chromium_playwright_present(),
    reason="Chromium Playwright absent (CI) -- rendu non testable ici",
)


def _png_size(data: bytes) -> tuple[int, int]:
    """Lit largeur/hauteur dans l'entete IHDR d'un PNG."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_fetch_image_data_uri_retombe_sur_le_fallback_si_url_absente():
    uri = fetch_image_data_uri(None)
    assert uri.startswith("data:image/")


def test_fetch_image_data_uri_retombe_sur_le_fallback_si_url_cassee():
    """Une photo introuvable ne doit jamais faire echouer un post."""
    uri = fetch_image_data_uri("https://exemple.invalid/introuvable.jpg")
    assert uri.startswith("data:image/")


def test_le_document_porte_les_variables_du_gabarit():
    """Le CSS n'a plus de valeurs de mise en page : si les variables ne sont pas
    injectees, il rend un gabarit vide sans lever d'erreur."""
    document = build_document(
        GABARIT_POST, {"headline": "Une **accroche**"}, "data:image/jpeg;base64,AAA"
    )

    assert "--headline-font-size: 80px;" in document
    assert "--scale: 1.0;" in document
    assert "<mark>accroche</mark>" in document


def test_le_css_ne_contient_plus_de_valeurs_de_mise_en_page():
    """Sentinelle du refactoring : une valeur qui revient en dur dans la feuille
    est une regle qui echappe a layout.py, donc aux tests."""
    css = Path("app/content/templates/kinda_post/style.css").read_text(encoding="utf-8")
    # On ignore la police embarquee en base64 et les commentaires.
    corps = "\n".join(
        ligne
        for ligne in css.splitlines()
        if "base64" not in ligne and not ligne.strip().startswith(("/*", "*", "--"))
    )

    for valeur in ("80px", "-0.06em", "1.15", "77px", "76px", "253, 139, 195"):
        assert valeur not in corps, f"{valeur} est encore en dur dans style.css"

    # Un bloc `:root` dans la feuille rouvrirait la porte : une valeur en dur
    # derriere un nom de variable reste une valeur en dur, et le filtre de ce
    # test ignore justement les lignes `--`. Toutes les variables viennent de
    # Python, aucune n'est declaree ici.
    assert ":root" not in css


def test_la_feuille_de_style_nest_pas_echappee_par_le_gabarit():
    """L'autoescape de Jinja s'applique aussi a `{{ css }}` dans <style>.

    Il transforme les guillemets en `&#34;`, ce qui rend `font-family:
    "KindaDisplay"` et `format("woff2")` invalides : l'@font-face n'est pas
    enregistre, Chromium rend l'accroche dans son serif par defaut et coupe les
    lignes ailleurs que le gabarit Figma. Aucune erreur, aucun test de taille de
    PNG ne bronche -- d'ou cette verification sur le document lui-meme.
    """
    document = build_document(
        GABARIT_POST, {"headline": "Une **accroche**"}, "data:image/jpeg;base64,AAA"
    )

    assert 'font-family: "KindaDisplay"' in document
    assert 'format("woff2")' in document
    assert "&#34;" not in document


@chromium_absent
def test_la_police_du_gabarit_est_bien_celle_qui_rend_laccroche():
    """Preuve de bout en bout : la police embarquee est chargee ET utilisee.

    Le test precedent lit le HTML ; celui-ci verifie ce que Chromium en fait --
    face enregistree, chargee, et employee pour l'accroche. Un repli silencieux
    sur le serif par defaut donnerait une face absente et une largeur de texte
    differente.
    """
    from playwright.sync_api import sync_playwright

    document = build_document(
        GABARIT_POST, {"headline": "Vieilles Charrues"}, fetch_image_data_uri(None)
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": GABARIT_POST.width, "height": GABARIT_POST.height}
            )
            page.set_content(document, wait_until="load")
            page.evaluate("document.fonts.ready")
            faces = page.evaluate("[...document.fonts].map(f => [f.family, f.status])")
            largeur_kinda = page.evaluate(
                "(() => {"
                "  const r = document.createRange();"
                "  r.selectNodeContents(document.getElementById('headline'));"
                "  return r.getBoundingClientRect().width; })()"
            )
            largeur_repli = page.evaluate(
                "(() => {"
                "  document.getElementById('headline').style.fontFamily = 'serif';"
                "  const r = document.createRange();"
                "  r.selectNodeContents(document.getElementById('headline'));"
                "  return r.getBoundingClientRect().width; })()"
            )
        finally:
            browser.close()

    assert faces == [["KindaDisplay", "loaded"]]
    assert largeur_kinda != pytest.approx(largeur_repli, abs=1.0)


@chromium_absent
def test_le_voile_garde_la_geometrie_de_la_maquette_sans_scrim_height():
    """Un `var()` sans repli n'echoue pas silencieusement -- il INVALIDE toute
    la propriete au calcul, pas seulement le calque qui le porte.

    Rien ne le signale a la lecture du HTML : pas d'erreur, pas de warning, et
    aucune assertion de taille de PNG ne bronche (le viewport impose deja
    1080x1440, quelle que soit la geometrie du voile a l'interieur). Seule la
    valeur CALCULEE (`getComputedStyle`) le revele -- avant le repli, Chromium
    rendait `background-size: auto, auto, auto` et le voile passait en plein
    cadre au lieu des bandes localisees de la maquette. Cette tache (2) ne
    fournit jamais `scrim_height` (cable par la tache 3) : c'est exactement le
    cas que le repli doit couvrir dans le regime actuel.
    """
    from playwright.sync_api import sync_playwright

    document = build_document(
        GABARIT_POST, {"headline": "Vieilles Charrues"}, "data:image/jpeg;base64,AAA"
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": GABARIT_POST.width, "height": GABARIT_POST.height}
            )
            page.set_content(document, wait_until="load")
            taille = page.evaluate(
                "getComputedStyle(document.getElementById('scrim')).backgroundSize"
            )
        finally:
            browser.close()

    assert "auto" not in taille, f"le voile est en plein cadre (repli absent) : {taille!r}"
    assert "817px" in taille


@chromium_absent
def test_render_post_produit_un_png_par_slide():
    slides = [
        {"headline": "**Vieilles Charrues** : le line-up", "image_url": None},
        {"headline": "Rendez-vous **ce week-end**", "image_url": None},
    ]
    rendus = render_post(slides)

    assert len(rendus) == 2
    for rendu in rendus:
        assert _png_size(rendu.png) == (GABARIT_POST.width, GABARIT_POST.height)
        assert len(rendu.png) > 10_000


@chromium_absent
def test_le_comptage_de_lignes_est_exact_sur_l_accroche_de_reference():
    """L'accroche de la maquette Figma tombe sur TROIS lignes.

    Le comptage passe par `Range.getClientRects()`, un rectangle par ligne
    rendue. Diviser la hauteur par l'interlignage serait faux : un accent ou une
    descendante change la hauteur de boite. Et un `<mark>` produit PLUSIEURS
    rectangles sur une meme ligne -- s'ils ne sont pas regroupes par ordonnee, le
    compte explose.
    """
    accroche = (
        "Nico Moreno, Mosimann, Cassius... et **c'est ce week-end** "
        "aux **Vieilles Charrues**"
    )
    (rendu,) = render_post([{"headline": accroche, "image_url": None}])

    assert rendu.lines == {"headline": 3}
    assert rendu.scale == 1.0
    assert rendu.overflow is False


@chromium_absent
def test_une_accroche_trop_longue_declenche_la_reduction():
    """Le regime de secours. La police est reduite, mais le rendu sort."""
    longue = " ".join(["**Nico Moreno** et Mosimann aux Vieilles Charrues"] * 4)
    (rendu,) = render_post([{"headline": longue, "image_url": None}])

    assert rendu.scale < 1.0
    assert rendu.lines["headline"] <= GABARIT_POST.roles["headline"][1].max_lines


@chromium_absent
def test_un_texte_intenable_est_marque_et_non_publie_en_silence():
    """Ce qui remplace le `logger.warning` suivi d'un rendu coupe. Le drapeau
    remonte jusqu'au dashboard."""
    intenable = " ".join(["**Vieilles Charrues** Nico Moreno Mosimann Cassius"] * 20)
    (rendu,) = render_post([{"headline": intenable, "image_url": None}])

    assert rendu.overflow is True
    assert rendu.scale == GABARIT_POST.roles["headline"][1].min_scale
    assert _png_size(rendu.png) == (GABARIT_POST.width, GABARIT_POST.height)


@chromium_absent
def test_la_cover_reel_rend_une_bande_par_role():
    """La STRUCTURE du gabarit : deux roles, une bande chacun, aux dimensions de
    la toile. Les LARGEURS de bande (534 / 753) sont verifiees a la tache 5,
    apres calibration -- les asserter ici ferait echouer cette tache pour une
    raison qui ne la concerne pas."""
    from app.content.layout import GABARIT_REEL

    slide = {
        "title": "**Gregoire Cliquet**",
        "subtitle": "au festival des **vieilles Charrues**",
        "image_url": None,
    }
    (rendu,) = render_post([slide], "kinda_reel")

    assert rendu.lines == {"title": 1, "subtitle": 1}
    assert rendu.overflow is False
    assert _png_size(rendu.png) == (GABARIT_REEL.width, GABARIT_REEL.height)


@chromium_absent
def test_un_titre_de_reel_trop_long_passe_sur_une_deuxieme_bande():
    """Le choix retenu : la police ne se reduit pas, le titre passe sur une
    deuxieme bande rose. L'empilement de bandes EST le langage du gabarit."""
    slide = {
        "title": "**Gregoire Cliquet** et Nico Moreno en cloture",
        "subtitle": "au festival des **vieilles Charrues**",
        "image_url": None,
    }
    (rendu,) = render_post([slide], "kinda_reel")

    assert rendu.lines["title"] == 2
    assert rendu.scale == 1.0


def _plus_long_segment(ligne: np.ndarray) -> int:
    """Longueur du plus long segment de True consecutifs dans une ligne 1D."""
    if not ligne.any():
        return 0
    bornes = np.diff(np.concatenate(([0], ligne.astype(np.int8), [0])))
    debuts = np.where(bornes == 1)[0]
    fins = np.where(bornes == -1)[0]
    return int((fins - debuts).max())


def _hauteurs_de_bande(png: bytes) -> dict[str, float]:
    """Isole les pixels de bande au pixel pres et rend leur hauteur.

    Le seuil de couleur seul ne suffit pas pour la bande creme : le texte du
    TITRE est peint dans la meme couleur (#FFF7FA) que le FOND du sous-titre --
    et le logo aussi. Un masque de couleur brut sur toute l'image confond donc
    les glyphes du titre et le logo avec la bande creme, et gonfle la boite
    englobante de plusieurs centaines de pixels (verifie en calibrant cette
    tache). La bande rose n'a pas ce probleme (rien d'autre n'est peint en rose
    sur le rendu), donc sa boite englobante brute suffit -- et sert de repere
    pour exclure du masque creme tout ce qui est au niveau ou au-dessus du
    titre, ce qui ecarte le logo (bien plus haut) et les glyphes du titre (sur
    les memes lignes que sa bande) d'un seul coup.

    Cette exclusion SUPPOSE le titre rendu au-dessus du sous-titre (l'ordre des
    roles dans `kinda_reel/slide.html.j2`) et que les glyphes du titre ne
    debordent jamais sous sa propre bande. Si un futur changement de gabarit
    (ordre des roles, `max_lines`, `line_height`) casse cette hypothese, le
    masque rognerait le haut de la vraie bande creme et la mesure ressortirait
    plus petite -- SANS erreur. Le garde-fou ci-dessous le detecte : dans la
    zone exclue (a l'ecart d'une marge pres du bord, cf. plus bas), les glyphes
    du titre ne produisent que des traits de lettres (segments horizontaux
    courts), alors qu'une vraie bande rognee laisserait un remplissage plein
    (segment large, comparable a la largeur de la bande).
    """
    image = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    r, g, b = image[:, :, 0], image[:, :, 1], image[:, :, 2]

    rose = (abs(r - 253) < 22) & (abs(g - 139) < 34) & (abs(b - 195) < 28)
    ys_rose, _ = np.where(rose)
    hauteur_rose = float(ys_rose.max() - ys_rose.min() + 1)

    creme = (r > 245) & (g > 240) & (b > 240)
    limite = int(ys_rose.max()) + 1
    creme_sous_le_titre = creme.copy()
    creme_sous_le_titre[:limite, :] = False
    ys_creme, xs_creme = np.where(creme_sous_le_titre)
    if ys_creme.size == 0:
        # Cas extreme de la meme hypothese cassee : plus AUCUN pixel creme ne
        # subsiste sous le bas de la bande rose (rangee ys_rose.max()) une fois
        # la zone exclue -- le sous-titre n'est plus rendu en dessous du tout.
        raise AssertionError(
            f"aucun pixel creme sous la bande rose (rangee {ys_rose.max()}) : "
            "le titre n'est plus au-dessus du sous-titre, l'exclusion de cette "
            "fonction n'est plus valable"
        )
    largeur_creme = float(xs_creme.max() - xs_creme.min() + 1)
    boite_creme = float(ys_creme.max() - ys_creme.min() + 1)

    # Garde-fou : un segment plein dans la zone exclue (>= moitie de la largeur
    # mesuree) ne peut pas etre un trait de lettre -- c'est la vraie bande qui
    # deborde dans la zone qu'on vient d'exclure. Marge de 20px avant le bord :
    # le bord haut de la bande creme est INCLINE (0,6 deg), donc son coin
    # remonte legerement dans la zone exclue meme quand tout va bien (deplace-
    # ment attendu ~= largeur x sin(0,6 deg), quelques pixels) -- un effet
    # geometrique normal du design (bandes qui se touchent), pas un signe
    # d'ordre casse. Sans cette marge, le garde-fou se declenche a CHAQUE rendu
    # sain, ce qui n'est pas un garde-fou mais un test qui echoue toujours.
    MARGE_INCLINAISON = 20
    zone_exclue = creme[: max(limite - MARGE_INCLINAISON, 0), :]
    plus_long = max((_plus_long_segment(ligne) for ligne in zone_exclue), default=0)
    if plus_long >= largeur_creme / 2:
        raise AssertionError(
            f"segment creme de {plus_long}px a plus de {MARGE_INCLINAISON}px "
            f"au-dessus du bas de la bande rose (rangee {ys_rose.max()}), alors "
            f"que la bande creme mesuree ne fait que {largeur_creme:.0f}px de "
            "large : le titre n'est plus au-dessus du sous-titre (ou deborde "
            "sous sa bande), l'exclusion de cette fonction n'est plus valable"
        )

    # La bande creme est inclinee : sa boite englobante est plus haute que la
    # bande elle-meme, de largeur x sin(angle).
    hauteur_creme = boite_creme - largeur_creme * math.sin(math.radians(0.6))

    return {"rose": hauteur_rose, "creme": hauteur_creme}


@chromium_absent
def test_les_largeurs_de_bande_retombent_sur_la_maquette():
    """Les valeurs mesurees au pixel sur exemples/[PREVIEW] - Cover Reels.png :
    bande rose 534 px, bande creme 753 px.

    Tolerance large (25 px) et non stricte : la police embarquee est un substitut
    metriquement compatible d'Arial Narrow, pas Arial Narrow elle-meme. Exiger le
    pixel exact rendrait ce test faux pour une raison qui n'est pas un defaut.
    """
    from app.content.render import mesurer_bandes

    largeurs = mesurer_bandes({
        "title": "**Gregoire Cliquet**",
        "subtitle": "au festival des **vieilles Charrues**",
        "image_url": None,
    })

    assert largeurs["title"][0] == pytest.approx(534, abs=25)
    assert largeurs["subtitle"][0] == pytest.approx(753, abs=25)


@chromium_absent
def test_les_hauteurs_de_bande_retombent_sur_la_maquette():
    """Hauteurs mesurees : 94 px pour la bande rose, 89 px pour la creme.

    Ce test remplace l'ancienne assertion `abs(titre.font_size -
    sous_titre.font_size) <= 8`, qui reposait sur une deduction des corps a
    partir des glyphes de la maquette -- deduction que les largeurs rendues ont
    dementie. La hauteur de bande, elle, depend du corps et du padding mais PAS
    de l'interlettrage : c'est la mesure qui pin le corps sans ambiguite.
    """
    (rendu,) = render_post([{
        "title": "**Gregoire Cliquet**",
        "subtitle": "au festival des **vieilles Charrues**",
        "image_url": None,
    }], "kinda_reel")

    hauteurs = _hauteurs_de_bande(rendu.png)
    assert hauteurs["rose"] == pytest.approx(94, abs=8)
    # La boite englobante de la bande creme est plus haute que la bande : son
    # inclinaison ajoute largeur x sin(0,6 deg) ~ 8 px. `_hauteurs_de_bande` le
    # corrige ; comparer la boite brute pousserait a rapetisser une bande juste.
    assert hauteurs["creme"] == pytest.approx(89, abs=8)


def test_chromium_absent_donne_une_erreur_lisible(monkeypatch):
    """Le rendu exige Chromium Playwright, que le dashboard n'installe pas. Une
    trace d'exception au fond de Playwright ne dit pas quoi faire ; ce message,
    si."""
    import app.content.render as render_module

    def faux_playwright():
        raise Exception("Executable doesn't exist at /path/chrome")

    monkeypatch.setattr(render_module, "sync_playwright", faux_playwright)

    with pytest.raises(RenduIndisponible, match="playwright install chromium"):
        render_post([{"headline": "Une **accroche**", "image_url": None}])


def test_erreur_de_rendu_normale_n_est_pas_masquee(monkeypatch):
    """La discrimination compte : une erreur SANS le signal Playwright (bug de
    gabarit, erreur Jinja, etc.) ne doit jamais devenir RenduIndisponible -- ca
    enverrait qui debugue dans la mauvaise direction (installer Chromium alors
    que Chromium est deja la)."""
    import app.content.render as render_module

    class FauxPlaywright:
        def __enter__(self):
            raise ValueError("gabarit invalide -- rien a voir avec Chromium")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(render_module, "sync_playwright", lambda: FauxPlaywright())

    with pytest.raises(ValueError, match="gabarit invalide"):
        render_post([{"headline": "Une **accroche**", "image_url": None}])
