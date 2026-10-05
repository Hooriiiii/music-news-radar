"""La charte Kinda dans la console, et la propriete qu'elle met en danger.

Porter la marque dans le dashboard, c'est remplacer un violet generique par le
ROSE de Kinda. Le gain est mesurable : 4,37:1 pour le violet sur le fond sombre
(sous le seuil de 4,5), 8,60:1 pour le rose. Mais le rose cree un risque que le
violet n'avait pas, et c'est lui que ce fichier verrouille.

**Le rouge d'alerte ne doit jamais se confondre avec l'accent.** Rose et rouge
sont voisins : si une raison de panne s'ecrit dans une teinte proche du rose,
rien ne casse a l'ecran -- la page reste belle, les tests de structure restent
verts, et la supervision devient simplement aveugle. C'est la violation la plus
COUTEUSE et la plus SILENCIEUSE que la refonte de couleur rende possible, parce
que la supervision est ce que l'audit UX a fait construire en premier.

Deux garde-fous, et une preuve par mutation pour chacun :
1. les jetons d'alerte restent a la teinte rouge, loin du rose ;
2. aucune regle dont le selecteur parle de panne ne cite un jeton d'accent.

Le reste du fichier mesure les CONTRASTES de chaque paire texte/fond que la
charte introduit -- un seuil franchi par inadvertance a la prochaine retouche
de palette rougit ici et pas a l'oeil.
"""

from __future__ import annotations

import colorsys
import re
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html"
PAGE = DASHBOARD.read_text(encoding="utf-8")


# --- Mesures, en WCAG 2.x --------------------------------------------------
def _canal(v: int) -> float:
    c = v / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hexa: str) -> float:
    h = hexa.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _canal(r) + 0.7152 * _canal(g) + 0.0722 * _canal(b)


def contraste(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    haut, bas = max(la, lb), min(la, lb)
    return (haut + 0.05) / (bas + 0.05)


def teinte(hexa: str) -> float:
    """La teinte en degres (0 = rouge pur, 330 = le rose de la charte)."""
    h = hexa.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


def ecart_de_teinte(a: str, b: str) -> float:
    d = abs(teinte(a) - teinte(b)) % 360
    return min(d, 360 - d)


# --- Lecture de la feuille de style ---------------------------------------
def jetons(page: str = PAGE) -> dict[str, str]:
    """Les variables de couleur declarees dans `:root`."""
    bloc = page[page.index(":root {") : page.index("* { box-sizing")]
    return {n: v.lower() for n, v in re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;", bloc)}


# Les teintes mesurees au pixel sur le PDF de marque.
ROSE = "#fb8ac2"
CREME = "#fef1f7"


def test_la_console_porte_le_rose_de_la_marque_et_sa_creme():
    j = jetons()
    assert j["--accent"] == ROSE
    assert j["--text"] == CREME
    assert j["--creme"] == CREME
    # Le violet generique ne doit plus gouverner quoi que ce soit.
    assert "#7c5cff" not in re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)


# --- Garde-fou 1 : le rouge d'alerte reste du rouge ------------------------
ALERTES = ("--hot", "--alerte", "--alerte-douce")
ECART_MIN = 25.0  # degres : en deca, rose et rouge se confondent a l'ecran


def ecarts_des_alertes(page: str = PAGE) -> dict[str, float]:
    j = jetons(page)
    return {nom: ecart_de_teinte(j[nom], j["--accent"]) for nom in ALERTES}


def test_le_rouge_d_alerte_ne_devient_jamais_rose():
    j = jetons()
    for nom, ecart in ecarts_des_alertes().items():
        # Teinte 0 = rouge pur. Le rose de la charte est a 330.
        assert teinte(j[nom]) <= 20 or teinte(j[nom]) >= 345, (nom, j[nom])
        assert ecart >= ECART_MIN, f"{nom} ({j[nom]}) n'est qu'a {ecart:.1f} du rose"


def test_la_mesure_rougit_si_une_alerte_prend_la_teinte_de_l_accent():
    """PREUVE PAR MUTATION du garde-fou 1.

    On remet dans la page une alerte rose -- exactement la regression
    redoutee : rien ne casse, la couleur est jolie, et une panne devient
    indistinguable de l'accent. La mesure doit la refuser.
    """
    mutee = PAGE.replace("--alerte: #ff7b7b;", f"--alerte: {ROSE};")
    assert mutee != PAGE, "la mutation n'a rien remplace : le jeton a change de nom"
    assert ecarts_des_alertes(mutee)["--alerte"] < ECART_MIN


# --- Garde-fou 2 : aucune regle de panne ne cite un jeton d'accent ---------
MOTS_DE_PANNE = ("panne", "alerte", "echec", "erreur", "warning", "exclu", "danger", "ko")
JETONS_D_ACCENT = ("var(--accent)", "var(--accent-clair)", "var(--accent-doux)")
# La seule exception, et elle est ecrite plutot que devinee : dans la zone
# d'erreur, `.pourquoi` porte la RAISON (en rouge) et `.relance` est le bouton
# qui relance le chargement. Le second est une ACTION a l'interieur d'un etat de
# panne, pas le signal de panne : il a le droit d'etre a l'accent, comme tout
# bouton principal. Toute autre regle doit rougir.
TOLEREES = {".zone-erreur .relance"}


def regles_de_panne_citant_l_accent(page: str = PAGE) -> list[str]:
    css = page[page.index("<style>") : page.index("</style>")]
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # les commentaires parlent des deux
    fautives = []
    for selecteur, corps in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        s = selecteur.lower()
        if not any(mot in s for mot in MOTS_DE_PANNE):
            continue
        if selecteur.strip() in TOLEREES:
            continue
        # `border-color: var(--accent)` au survol reste neutre : c'est le TEXTE
        # et le FOND d'un etat de panne qui ne doivent pas virer a l'accent.
        for declaration in corps.split(";"):
            propriete = declaration.split(":")[0].strip()
            if propriete not in {"color", "background", "background-color"}:
                continue
            if any(jeton in declaration for jeton in JETONS_D_ACCENT):
                fautives.append(f"{selecteur.strip()} {{ {declaration.strip()} }}")
    return fautives


def test_aucune_regle_de_panne_ne_s_ecrit_dans_l_accent():
    assert regles_de_panne_citant_l_accent() == []


def test_la_mesure_rougit_si_une_raison_de_panne_passe_a_l_accent():
    """PREUVE PAR MUTATION du garde-fou 2."""
    mutee = PAGE.replace(
        ".sup-raison.niv-panne { color: var(--alerte-douce); }",
        ".sup-raison.niv-panne { color: var(--accent); }",
    )
    assert mutee != PAGE, "la mutation n'a rien remplace : la regle a change"
    assert regles_de_panne_citant_l_accent(mutee)


# --- Les contrastes de chaque paire introduite ----------------------------
TEXTE_NORMAL = 4.5
COMPOSANT = 3.0


@pytest.mark.parametrize(
    ("avant", "arriere", "seuil"),
    [
        ("--text", "--bg", TEXTE_NORMAL),  # la creme sur le fond sombre
        ("--text", "--panel", TEXTE_NORMAL),
        ("--accent", "--bg", TEXTE_NORMAL),  # le rose en texte et en bordure
        ("--accent", "--panel", TEXTE_NORMAL),
        ("--accent-clair", "--bg", TEXTE_NORMAL),
        ("--accent-clair", "--panel", TEXTE_NORMAL),
        ("--accent-clair", "--accent-doux", TEXTE_NORMAL),
        ("--accent", "--accent-doux", COMPOSANT),
        ("--creme", "--accent-doux", TEXTE_NORMAL),
        # Le piege contre-intuitif : sur du rose PLEIN, le texte est sombre.
        ("--sur-accent", "--accent", TEXTE_NORMAL),
        ("--sur-accent", "--hot", TEXTE_NORMAL),
        # Une panne ne doit jamais perdre en lisibilite pour gagner en teinte.
        ("--alerte", "--bg", TEXTE_NORMAL),
        ("--alerte", "--alerte-fond", TEXTE_NORMAL),
        ("--alerte-douce", "--bg", TEXTE_NORMAL),
        ("--alerte-douce", "--panel", TEXTE_NORMAL),
        ("--hot", "--bg", TEXTE_NORMAL),
    ],
)
def test_contraste_suffisant(avant: str, arriere: str, seuil: float):
    j = jetons()
    mesure = contraste(j[avant], j[arriere])
    assert mesure >= seuil, f"{avant} sur {arriere} : {mesure:.2f}:1 (seuil {seuil})"


def test_le_blanc_sur_le_rose_plein_resterait_interdit():
    """Le chiffre qui justifie `--sur-accent`, garde a vue."""
    assert contraste("#ffffff", jetons()["--accent"]) < TEXTE_NORMAL


# --- Les signes de marque -------------------------------------------------
def test_le_logo_remplace_le_titre_textuel_sans_perdre_son_nom_accessible():
    assert '<img class="logo-kinda" src="data:image/png;base64,' in PAGE
    assert 'alt="Kinda"' in PAGE
    corps = PAGE[PAGE.index("</style>") :]
    assert "🎧 Kinda" not in corps


def test_l_etoile_est_un_masque_recolorable_et_reste_rare():
    assert "--etoile-kinda: url(data:image/png;base64," in PAGE
    css = PAGE[PAGE.index("<style>") : PAGE.index("</style>")]
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # Deux emplois seulement : l'etat vide et la variante retenue. Un outil de
    # travail ne se decore pas, il se signe.
    assert css.count("mask: var(--etoile-kinda)") == 6  # 3 emplois x (mask + -webkit-mask)


def test_la_page_ne_depend_d_aucun_reseau_pour_s_afficher():
    """Ni police web, ni CDN : le dashboard reste un seul fichier autonome."""
    corps = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
    assert "fonts.googleapis" not in corps
    assert "@import" not in corps
    assert "@font-face" not in corps
