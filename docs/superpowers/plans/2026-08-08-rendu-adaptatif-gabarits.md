# Rendu adaptatif des gabarits Kinda — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sortir toutes les règles de mise en page du CSS vers du Python testable, remplacer les trois paliers de réduction de police par un ajustement continu qui ne déborde jamais en silence, et ajouter le gabarit cover reel 1080×1920.

**Architecture:** Un nouveau module `app/content/layout.py` sans aucune dépendance (ni Chromium, ni Jinja, ni réseau, ni base) porte les gabarits en dataclasses gelées et la machine à états de l'ajustement. `render.py` pilote Chromium, qui ne fait que **mesurer** ; toutes les **décisions** sont prises en Python. Le CSS devient un squelette sans valeurs qui consomme des variables injectées depuis le gabarit.

**Tech Stack:** Python 3.14 / uv, Jinja2 + MarkupSafe, Playwright (Chromium headless), SQLAlchemy 2.0 + Alembic, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rendu-adaptatif-gabarits-design.md`

## Global Constraints

- Toutes les commandes passent par `uv run` (jamais `python` ou `pytest` nus).
- Lint : `uv run ruff check .` doit passer. `line-length = 100`, `target-version = "py311"`.
- **Commentaires et docstrings en français, SANS accents** (le reste du code de `app/content/` suit cette règle — voir `render.py`). Les chaînes de caractères destinées à l'affichage peuvent être accentuées.
- Les commentaires expliquent le **pourquoi**, pas le quoi. Densité de commentaires : celle du fichier voisin.
- SQLAlchemy 2.0 style (`Mapped` / `mapped_column`). Dans les migrations écrites à la main, entourer les noms de contraintes avec `op.f()`.
- Colonnes JSONB via `JSONVariant` (`app/models.py`) — JSON génériques sur SQLite pour les tests.
- **Réassigner une colonne JSON en entier**, jamais de mutation in place, sinon SQLAlchemy ne persiste pas.
- Les en-têtes HTTP doivent être en ASCII pur.
- Les tests qui ont besoin de Chromium portent le marqueur `@chromium_absent` déjà défini dans `tests/test_render.py`.
- Commit après chaque tâche, message en français, préfixe `feat(posts):` / `refactor(posts):` / `fix(posts):`.

## Valeurs de référence (relevées sur les maquettes)

Ces nombres viennent de la spec. Ils sont la vérité pour tout ce plan.

**`kinda_post` — 1080×1440**, frame Figma `234:8` :
corps 80 px · interlignage 1.15 · interlettrage -0.06 em · couleur `#FFF7FA` ·
bloc left 77 right 77 ancré en bas offset 76 · surlignage `rgba(253,139,195,.8)` ·
voile bas de la maquette : 817 px de haut à partir de y=623.

**`kinda_reel` — 1080×1920**, mesuré sur `exemples/[PREVIEW] - Cover Reels.png` :

| Mesure | Valeur |
|---|---|
| Bande rose (titre) | largeur **534**, hauteur **94**, y 731→824, non inclinée |
| Bande crème (sous-titre) | largeur **753**, hauteur ≈ **85**, y 831→910, **inclinée -0,6°** |
| Empilement | jointives, centrées chacune sur sa propre largeur |
| Centre vertical du bloc | ≈ 42,9 % de 1920 |
| Rose | `#FD8BC3` opaque · Crème `#FFF7FA` · Texte titre `#FFF7FA` · Texte sous-titre `#1D1D1D` |
| Voile | aucun |

---

## File Structure

| Fichier | Responsabilité |
|---|---|
| `app/content/layout.py` (créer) | Gabarits en dataclasses + machine à états de l'ajustement + génération des variables CSS. **Zéro dépendance.** |
| `app/content/render.py` (modifier) | Pilote Chromium : injecte, mesure, capture. Ne décide de rien. |
| `app/content/templates/kinda_post/style.css` (modifier) | Squelette sans valeurs de mise en page. |
| `app/content/templates/kinda_post/slide.html.j2` (modifier) | Accepte le bloc de variables. |
| `app/content/templates/kinda_reel/style.css` (créer) | Bandes empilées, inclinaison, pas de voile. |
| `app/content/templates/kinda_reel/slide.html.j2` (créer) | Deux rôles `title` / `subtitle`. |
| `app/content/templates/kinda_post/REFERENCE.md` (modifier) | Documente les deux gabarits et les PNG de référence. |
| `app/models.py` (modifier) | Colonne `render_report`. |
| `alembic/versions/0009_add_render_report.py` (créer) | La migration. |
| `app/schemas.py` (modifier) | `SlideIn` accepte les rôles du reel ; `PostOut` expose `render_report`. |
| `app/api/posts.py` (modifier) | `POST /posts/{id}/render` persiste le rapport. |
| `tests/test_layout.py` (créer) | Les règles, sans Chromium. |
| `tests/test_render.py` (modifier) | Géométrie, avec Chromium. |
| `tests/test_api_posts.py` (modifier ou créer) | Le rapport de rendu via l'API. |

---

## Task 1: `layout.py` — les gabarits et la décision d'ajustement

Le cœur du chantier, et la seule partie entièrement testable sans navigateur. Rien d'autre ne peut être écrit avant.

**Files:**
- Create: `app/content/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: rien.
- Produces:
  - `Typo(font_size: float, line_height: float, letter_spacing: float, color: str, background: str | None = None, mark_background: str | None = None, tilt: float = 0.0)`
  - `Zone(left: int, right: int, anchor: str, anchor_offset: int, align: str, max_lines: int, max_line_width: int, min_scale: float)`
  - `Gabarit(name: str, width: int, height: int, roles: dict[str, tuple[Typo, Zone]])`
  - `Mesure(line_widths: tuple[float, ...], height: float)` avec la propriété `lines -> int`
  - `Resultat(scale: float, lines: dict[str, int], overflow: bool)`
  - `tient(zone: Zone, mesure: Mesure) -> bool`
  - `Ajustement(gabarit: Gabarit)` avec `prochaine_echelle() -> float | None`, `enregistrer(mesures: dict[str, Mesure]) -> None`, `resultat -> Resultat`
  - `GABARIT_POST: Gabarit`
  - `hauteur_voile(gabarit: Gabarit, role: str, mesure: Mesure) -> float`
  - `variables_css(gabarit: Gabarit, scale: float, scrim_height: float | None = None) -> str`
  - `GABARITS: dict[str, Gabarit]`
  - Constantes : `SCRIM_MARGE = 465`, `VOILE_MINIMUM = 817`

- [ ] **Step 1: Write the failing tests for the fitting rule**

Create `tests/test_layout.py`:

```python
"""Les regles de mise en page, testees sans Chromium.

C'est tout l'interet de `layout.py` : les regles d'ajustement etaient
auparavant enfouies dans une boucle qui exigeait un navigateur, donc
invisibles en CI et impossibles a eprouver aux bornes.
"""

import pytest

from app.content.layout import (
    GABARIT_POST,
    SCRIM_MARGE,
    VOILE_MINIMUM,
    Ajustement,
    Gabarit,
    Mesure,
    Typo,
    Zone,
    hauteur_voile,
    tient,
    variables_css,
)


def _zone(**kwargs) -> Zone:
    defauts = dict(
        left=77, right=77, anchor="bottom", anchor_offset=76, align="left",
        max_lines=5, max_line_width=926, min_scale=0.7,
    )
    return Zone(**{**defauts, **kwargs})


def test_mesure_compte_ses_lignes():
    assert Mesure(line_widths=(900.0, 850.0, 400.0), height=276.0).lines == 3


def test_tient_accepte_ce_qui_respecte_les_deux_plafonds():
    assert tient(_zone(), Mesure(line_widths=(900.0, 400.0), height=184.0))


def test_tient_refuse_une_ligne_de_trop():
    mesure = Mesure(line_widths=(900.0,) * 6, height=552.0)
    assert not tient(_zone(max_lines=5), mesure)


def test_tient_refuse_une_ligne_trop_large():
    """Le plafond de largeur est ce qui empeche une bande de reel de toucher
    les bords -- une contrainte que le plafond de hauteur ne voit pas."""
    mesure = Mesure(line_widths=(950.0,), height=92.0)
    assert not tient(_zone(max_line_width=926), mesure)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.content.layout'`

- [ ] **Step 3: Write the dataclasses and `tient`**

Create `app/content/layout.py`:

```python
"""Les regles de mise en page des gabarits Kinda, en Python pur.

Ce module ne connait NI Chromium, NI Jinja, NI le reseau, NI la base : il ne
manipule que de la geometrie et de la typographie. C'est deliberé -- les regles
d'ajustement vivaient auparavant en dur dans `style.css` et dans une boucle de
`render.py` qui exigeait un navigateur, donc introuvables en CI.

La repartition des roles avec `render.py` est stricte :
  Chromium MESURE (lignes rendues, largeur de chacune)
  Python DECIDE  (echelle a essayer, acceptation, abandon)
Chromium ne prend aucune decision.
"""

from dataclasses import dataclass
from typing import Literal

# Marge relevee sur la maquette du post : le voile bas fait 817 px et commence a
# y=623 ; le bloc d'accroche (276 px) est ancre a 76 px du bas. La marge entre le
# haut du voile et le haut du bloc vaut donc 817 - 276 - 76 = 465. C'est elle qu'on
# conserve quand le bloc grandit, pour que le texte reste dans la partie dense.
SCRIM_MARGE = 465
# Plancher : jamais un voile plus court que celui de la maquette, meme pour une
# accroche d'une seule ligne -- le degrade fait partie du design, pas seulement de
# la lisibilite.
VOILE_MINIMUM = 817


@dataclass(frozen=True)
class Typo:
    """Les regles typographiques d'un role de texte."""

    font_size: float          # px, valeur de la maquette
    line_height: float        # multiplicateur
    letter_spacing: float     # em
    color: str
    # Bande de fond qui epouse le texte (cover reel). None = pas de bande.
    background: str | None = None
    # Fond du <mark> (surlignage rose du post). None = pas de surlignage.
    mark_background: str | None = None
    # Degres. Negatif = antihoraire. Releve a -0.6 sur la bande creme du reel.
    tilt: float = 0.0


@dataclass(frozen=True)
class Zone:
    """Ou le texte a le droit de vivre, et jusqu'ou il peut grandir."""

    left: int
    right: int
    anchor: Literal["top", "bottom", "center"]
    anchor_offset: int
    align: Literal["left", "center"]
    # Plafond de HAUTEUR : au-dela, on reduit la police.
    max_lines: int
    # Plafond de LARGEUR : ce qui empeche une bande de reel de toucher les bords.
    max_line_width: int
    # Plancher absolu de reduction. En dessous, on declare le debordement.
    min_scale: float


@dataclass(frozen=True)
class Gabarit:
    """Un format de sortie complet. `roles` est ordonne et fait foi : les cles
    des slides en base doivent correspondre."""

    name: str
    width: int
    height: int
    roles: dict[str, tuple[Typo, Zone]]


@dataclass(frozen=True)
class Mesure:
    """Ce que Chromium a mesure pour un role, a une echelle donnee.

    Une largeur PAR LIGNE RENDUE, pas une largeur de bloc : c'est ce dont on a
    besoin pour les bandes du reel, qui epousent chacune leur ligne.
    """

    line_widths: tuple[float, ...]
    height: float

    @property
    def lines(self) -> int:
        return len(self.line_widths)


def tient(zone: Zone, mesure: Mesure) -> bool:
    """Vrai si la mesure respecte les DEUX plafonds de la zone."""
    return mesure.lines <= zone.max_lines and all(
        largeur <= zone.max_line_width for largeur in mesure.line_widths
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_layout.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing tests for the fitting state machine**

Append to `tests/test_layout.py`:

```python
def _gabarit_essai(**kwargs) -> Gabarit:
    typo = Typo(font_size=80, line_height=1.15, letter_spacing=-0.06, color="#FFF7FA")
    return Gabarit(name="essai", width=1080, height=1440, roles={"headline": (typo, _zone(**kwargs))})


def test_ajustement_accepte_la_maquette_sans_reduire():
    """Le regime NORMAL : la police ne bouge pas. Une seule mesure suffit."""
    ajustement = Ajustement(_gabarit_essai())

    assert ajustement.prochaine_echelle() == 1.0
    ajustement.enregistrer({"headline": Mesure(line_widths=(900.0, 400.0), height=184.0)})

    assert ajustement.prochaine_echelle() is None
    assert ajustement.resultat.scale == 1.0
    assert ajustement.resultat.overflow is False
    assert ajustement.resultat.lines == {"headline": 2}


def test_ajustement_essaie_le_plancher_avant_de_dichotomiser():
    """Tester le plancher en deuxieme mesure permet de detecter tout de suite le
    cas desespere, au lieu de gaspiller cinq mesures pour y arriver."""
    ajustement = Ajustement(_gabarit_essai(max_lines=3, min_scale=0.7))

    assert ajustement.prochaine_echelle() == 1.0
    ajustement.enregistrer({"headline": Mesure(line_widths=(900.0,) * 5, height=460.0)})

    assert ajustement.prochaine_echelle() == 0.7


def test_ajustement_declare_le_debordement_si_le_plancher_ne_tient_pas():
    """Le comportement qui remplace le `logger.warning` suivi d'un rendu coupe :
    on rend quand meme, au plancher, mais le resultat est MARQUE."""
    ajustement = Ajustement(_gabarit_essai(max_lines=3, min_scale=0.7))

    ajustement.prochaine_echelle()
    ajustement.enregistrer({"headline": Mesure(line_widths=(900.0,) * 8, height=736.0)})
    assert ajustement.prochaine_echelle() == 0.7
    ajustement.enregistrer({"headline": Mesure(line_widths=(900.0,) * 6, height=552.0)})

    assert ajustement.prochaine_echelle() is None
    assert ajustement.resultat.scale == 0.7
    assert ajustement.resultat.overflow is True


def test_ajustement_dichotomise_vers_la_plus_grande_echelle_qui_tient():
    """On veut la plus GRANDE echelle acceptable, pas la premiere qui passe :
    les trois paliers 1.0/0.9/0.8 sautaient 10% d'un coup, ce qui se voit."""
    gabarit = _gabarit_essai(max_lines=3, min_scale=0.5)
    ajustement = Ajustement(gabarit)

    # Simulation : le nombre de lignes decroit quand l'echelle decroit.
    def mesure_pour(echelle: float) -> Mesure:
        lignes = 3 if echelle <= 0.8 else 5
        return Mesure(line_widths=(900.0,) * lignes, height=92.0 * lignes)

    essais: list[float] = []
    while (echelle := ajustement.prochaine_echelle()) is not None:
        essais.append(echelle)
        ajustement.enregistrer({"headline": mesure_pour(echelle)})

    assert essais[0] == 1.0
    assert essais[1] == 0.5
    assert ajustement.resultat.overflow is False
    # La dichotomie converge juste sous le seuil de 0.8, sans le depasser.
    assert 0.75 <= ajustement.resultat.scale <= 0.8


def test_ajustement_exige_que_TOUS_les_roles_tiennent():
    """Une seule echelle par slide : le bloc reduit d'un bloc, ce qui preserve
    les proportions entre titre et sous-titre du reel."""
    typo = Typo(font_size=86, line_height=1.1, letter_spacing=-0.04, color="#FFF7FA")
    gabarit = Gabarit(
        name="essai_reel", width=1080, height=1920,
        roles={
            "title": (typo, _zone(max_lines=2, max_line_width=926)),
            "subtitle": (typo, _zone(max_lines=2, max_line_width=926)),
        },
    )
    ajustement = Ajustement(gabarit)
    ajustement.prochaine_echelle()
    ajustement.enregistrer({
        "title": Mesure(line_widths=(534.0,), height=94.0),        # tient
        "subtitle": Mesure(line_widths=(980.0,), height=85.0),     # trop large
    })

    assert ajustement.prochaine_echelle() is not None
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_layout.py -v`
Expected: FAIL — `ImportError: cannot import name 'Ajustement'`

- [ ] **Step 7: Implement `Resultat` and `Ajustement`**

Append to `app/content/layout.py`:

```python
@dataclass(frozen=True)
class Resultat:
    """Ce que l'ajustement a retenu. `overflow` est le drapeau qui remonte
    jusqu'au dashboard : un post marque ne doit pas etre publie tel quel."""

    scale: float
    lines: dict[str, int]
    overflow: bool


# Bornee : la dichotomie converge vite, et au-dela le probleme n'est pas la taille
# de police mais l'accroche elle-meme.
DICHOTOMIE_MAX = 4
# En deca de cet ecart d'echelle, la difference n'est plus visible a l'oeil.
DICHOTOMIE_TOLERANCE = 0.02


class Ajustement:
    """Machine a etats de l'ajustement. Recoit des mesures, rend l'echelle
    suivante a essayer, ou None quand c'est fini.

    Trois etages, dans cet ordre :
      1. l'echelle de la maquette (1.0). Si ca tient, on s'arrete la -- c'est le
         regime normal, et `HEADLINE_MAX = 110` dans `compose.py` fait qu'une
         accroche depasse rarement 4 lignes ;
      2. le PLANCHER (`min_scale`). Le tester en deuxieme detecte tout de suite le
         cas desespere au lieu de gaspiller la dichotomie pour y arriver ;
      3. la dichotomie entre le plancher (qui tient) et 1.0 (qui ne tient pas),
         pour trouver la plus GRANDE echelle acceptable.

    Elle suppose la monotonie : reduire la police ne peut pas ajouter de lignes ni
    elargir une ligne. Vrai en pratique, faux en theorie sur des cas pathologiques
    de cesure -- d'ou la borne `DICHOTOMIE_MAX`, qui garantit la terminaison.
    """

    def __init__(self, gabarit: Gabarit) -> None:
        self._gabarit = gabarit
        # max et non min : `min_scale` est un plancher PAR ROLE, et le plancher
        # liant pour la slide est le plus RESTRICTIF des deux. Prendre le minimum
        # autoriserait la dichotomie a descendre sous le plancher declare d'un
        # role sans jamais lever `overflow`.
        self._plancher = max(zone.min_scale for _, zone in gabarit.roles.values())
        self._etape = "maquette"
        self._essai: float | None = None
        self._bas = self._plancher          # borne basse : tient (a confirmer)
        self._haut = 1.0                    # borne haute : ne tient pas
        self._dichotomies = 0
        self._meilleur: tuple[float, dict[str, int]] | None = None
        self._dernier: tuple[float, dict[str, int]] | None = None
        self._fini = False

    def prochaine_echelle(self) -> float | None:
        if self._fini:
            return None
        if self._etape == "maquette":
            self._essai = 1.0
        elif self._etape == "plancher":
            self._essai = self._plancher
        else:
            self._essai = (self._bas + self._haut) / 2
        return self._essai

    def enregistrer(self, mesures: dict[str, Mesure]) -> None:
        """Enregistre le resultat de la mesure a l'echelle rendue par
        `prochaine_echelle()` et avance la machine."""
        if self._essai is None:  # pragma: no cover -- usage incorrect
            raise RuntimeError("enregistrer() avant prochaine_echelle()")

        echelle = self._essai
        lignes = {role: mesure.lines for role, mesure in mesures.items()}
        # TOUS les roles doivent tenir : une seule echelle par slide, pour que le
        # bloc reduise d'un bloc et garde ses proportions internes.
        ok = all(
            tient(self._gabarit.roles[role][1], mesure) for role, mesure in mesures.items()
        )
        self._dernier = (echelle, lignes)
        if ok and (self._meilleur is None or echelle > self._meilleur[0]):
            self._meilleur = (echelle, lignes)

        if self._etape == "maquette":
            if ok:
                self._fini = True
            else:
                self._etape = "plancher"
            return

        if self._etape == "plancher":
            if not ok:
                # Meme au plancher ca deborde : inutile de dichotomiser.
                self._fini = True
            else:
                self._etape = "dichotomie"
                self._bas = echelle
            return

        if ok:
            self._bas = echelle
        else:
            self._haut = echelle
        self._dichotomies += 1
        if self._dichotomies >= DICHOTOMIE_MAX or self._haut - self._bas < DICHOTOMIE_TOLERANCE:
            self._fini = True

    @property
    def resultat(self) -> Resultat:
        if self._meilleur is not None:
            echelle, lignes = self._meilleur
            return Resultat(scale=echelle, lines=lignes, overflow=False)
        # Rien n'a tenu : on rend quand meme, au plancher, mais MARQUE. On ne
        # produit jamais une image coupee en silence.
        echelle, lignes = self._dernier if self._dernier else (self._plancher, {})
        return Resultat(scale=echelle, lines=lignes, overflow=True)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_layout.py -v`
Expected: PASS (9 tests)

- [ ] **Step 9: Write the failing tests for `GABARIT_POST`, `hauteur_voile` and `variables_css`**

Append to `tests/test_layout.py`:

```python
def test_le_gabarit_post_porte_les_valeurs_de_la_maquette():
    """Les valeurs de la frame Figma 234:8. Un test-sentinelle : si quelqu'un les
    change sans repasser par REFERENCE.md, ce test le dit."""
    typo, zone = GABARIT_POST.roles["headline"]

    assert (GABARIT_POST.width, GABARIT_POST.height) == (1080, 1440)
    assert typo.font_size == 80
    assert typo.line_height == 1.15
    assert typo.letter_spacing == -0.06
    assert typo.color == "#FFF7FA"
    assert zone.anchor == "bottom"
    assert (zone.left, zone.right, zone.anchor_offset) == (77, 77, 76)


def test_hauteur_voile_retrouve_la_valeur_de_la_maquette():
    """Le bloc de reference fait 3 lignes = 276 px, et le voile de la maquette
    817 px. La formule doit retomber exactement dessus, sinon elle n'est pas
    une generalisation de la maquette mais une invention."""
    mesure = Mesure(line_widths=(900.0, 880.0, 400.0), height=276.0)

    assert hauteur_voile(GABARIT_POST, "headline", mesure) == pytest.approx(817.0)


def test_hauteur_voile_grandit_avec_le_bloc():
    """La consequence obligatoire du choix « bloc extensible » : sans ca, une
    accroche de 5 lignes sort du degrade dense et devient illisible sur une
    photo claire."""
    mesure = Mesure(line_widths=(900.0,) * 5, height=460.0)

    assert hauteur_voile(GABARIT_POST, "headline", mesure) == pytest.approx(1001.0)


def test_hauteur_voile_ne_descend_jamais_sous_la_maquette():
    mesure = Mesure(line_widths=(400.0,), height=92.0)

    assert hauteur_voile(GABARIT_POST, "headline", mesure) == VOILE_MINIMUM


def test_variables_css_expose_les_valeurs_du_gabarit():
    css = variables_css(GABARIT_POST, scale=0.85, scrim_height=900.0)

    assert "--scale: 0.85;" in css
    assert "--headline-font-size: 80px;" in css
    assert "--headline-letter-spacing: -0.06em;" in css
    assert "--headline-offset: 76px;" in css
    assert "--scrim-height: 900px;" in css


def test_variables_css_omet_ce_que_le_gabarit_ne_definit_pas():
    """Le reel n'a pas de voile ; le post n'a pas de bande de fond. Une variable
    ecrite a vide donnerait un `background: ;` silencieusement invalide."""
    css = variables_css(GABARIT_POST, scale=1.0)

    assert "--scrim-height" not in css
    assert "--headline-background" not in css
```

- [ ] **Step 10: Run the tests to verify they fail**

Run: `uv run pytest tests/test_layout.py -v`
Expected: FAIL — `ImportError: cannot import name 'GABARIT_POST'`

- [ ] **Step 11: Implement `GABARIT_POST`, `hauteur_voile`, `variables_css`, `GABARITS`**

Append to `app/content/layout.py`:

```python
# --- Les gabarits ---------------------------------------------------------
# Valeurs relevees dans Figma (frame 234:8) -- voir
# templates/kinda_post/REFERENCE.md pour la provenance et la procedure de
# re-extraction. Ne JAMAIS les modifier a l'aveugle.
GABARIT_POST = Gabarit(
    name="kinda_post",
    width=1080,
    height=1440,
    roles={
        "headline": (
            Typo(
                font_size=80,
                line_height=1.15,
                letter_spacing=-0.06,
                color="#FFF7FA",
                mark_background="rgba(253, 139, 195, 0.8)",
            ),
            Zone(
                left=77,
                right=77,
                anchor="bottom",
                anchor_offset=76,
                align="left",
                # Plafond calibre a la tache 8. 5 lignes laisse la photo respirer ;
                # `HEADLINE_MAX = 110` dans compose.py plafonne deja a ~4 lignes.
                max_lines=5,
                max_line_width=926,   # 1080 - 2 x 77
                min_scale=0.7,
            ),
        ),
    },
)

GABARITS: dict[str, Gabarit] = {GABARIT_POST.name: GABARIT_POST}


def gabarit(nom: str) -> Gabarit:
    """Leve KeyError avec un message lisible plutot qu'un KeyError nu."""
    try:
        return GABARITS[nom]
    except KeyError:
        raise KeyError(f"gabarit inconnu : {nom!r} (connus : {sorted(GABARITS)})") from None


def hauteur_voile(gabarit_: Gabarit, role: str, mesure: Mesure) -> float:
    """Hauteur du voile bas, fonction de la hauteur mesuree du bloc.

    Sans cela, le choix « bloc extensible » sortirait le texte du degrade dense
    des qu'une accroche depasse trois lignes : illisible sur une photo claire,
    et invisible sur la maquette dont le bas est deja sombre.
    """
    _, zone = gabarit_.roles[role]
    return max(VOILE_MINIMUM, mesure.height + zone.anchor_offset + SCRIM_MARGE)


def variables_css(gabarit_: Gabarit, scale: float, scrim_height: float | None = None) -> str:
    """Les valeurs du gabarit, en variables CSS. Le CSS n'a plus de nombres.

    Les variables absentes ne sont PAS ecrites a vide : un `background: ;`
    serait silencieusement invalide, et le rendu partirait sans bande sans que
    rien ne le signale.
    """
    lignes = [
        f"--slide-width: {gabarit_.width}px;",
        f"--slide-height: {gabarit_.height}px;",
        f"--scale: {scale};",
    ]
    for role, (typo, zone) in gabarit_.roles.items():
        lignes += [
            f"--{role}-font-size: {typo.font_size}px;",
            f"--{role}-line-height: {typo.line_height};",
            f"--{role}-letter-spacing: {typo.letter_spacing}em;",
            f"--{role}-color: {typo.color};",
            f"--{role}-left: {zone.left}px;",
            f"--{role}-right: {zone.right}px;",
            f"--{role}-offset: {zone.anchor_offset}px;",
            f"--{role}-align: {zone.align};",
            f"--{role}-tilt: {typo.tilt}deg;",
        ]
        if typo.background is not None:
            lignes.append(f"--{role}-background: {typo.background};")
        if typo.mark_background is not None:
            lignes.append(f"--{role}-mark-background: {typo.mark_background};")
    if scrim_height is not None:
        # round et non int : la hauteur vient d'une mesure Chromium fractionnaire
        # des la tache 3, et tronquer 817.6 en 817 rognerait le voile d'un pixel.
        lignes.append(f"--scrim-height: {round(scrim_height)}px;")
    return ":root {\n  " + "\n  ".join(lignes) + "\n}"
```

- [ ] **Step 12: Run the full suite and lint**

Run: `uv run pytest tests/test_layout.py -v && uv run ruff check .`
Expected: PASS (15 tests), lint clean

- [ ] **Step 13: Commit**

```bash
git add app/content/layout.py tests/test_layout.py
git commit -m "feat(posts): les regles de mise en page sortent du CSS vers layout.py

Les valeurs (corps 80px, interlignage 1.15, plafond 320px, paliers
1.0/0.9/0.8) etaient en dur, eparpillees entre style.css et render.py :
ni parametrables, ni testables sans navigateur.

layout.py les porte en dataclasses gelees, avec la machine a etats de
l'ajustement. Zero dependance : ni Chromium, ni Jinja, ni reseau, ni base.
Chromium mesurera, Python decide.

Trois etages : l'echelle de la maquette, puis le plancher (teste en second
pour detecter tout de suite le cas desespere), puis la dichotomie vers la
plus grande echelle acceptable -- les paliers de 10% se voyaient a l'oeil."
```

---

## Task 2: le CSS devient un squelette sans valeurs

Le gabarit pilote le CSS. Le rendu du post doit rester **visuellement identique** : c'est un refactoring, pas un changement de design.

**Files:**
- Modify: `app/content/templates/kinda_post/style.css`
- Modify: `app/content/templates/kinda_post/slide.html.j2`
- Modify: `app/content/render.py` (`build_document`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `layout.gabarit`, `layout.variables_css`, `layout.GABARIT_POST`
- Produces: `build_document(gabarit: Gabarit, textes: dict[str, str], image_data_uri: str, scale: float = 1.0, scrim_height: float | None = None) -> str`

- [ ] **Step 1: Write the failing test**

Replace `test_la_feuille_de_style_nest_pas_echappee_par_le_gabarit` in `tests/test_render.py` and add a new test. The imports at the top of the file become:

```python
import struct
from pathlib import Path

import pytest

from app.content.layout import GABARIT_POST
from app.content.render import build_document, fetch_image_data_uri, render_post
```

Note: `SLIDE_WIDTH` / `SLIDE_HEIGHT` / `render_module` disappear from the imports — the dimensions now come from the gabarit. Replace every `SLIDE_WIDTH` by `GABARIT_POST.width` and every `SLIDE_HEIGHT` by `GABARIT_POST.height` in the existing tests of this file.

Add:

```python
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
    css = (
        Path("app/content/templates/kinda_post/style.css").read_text(encoding="utf-8")
    )
    # On ignore la police embarquee en base64 et les commentaires.
    corps = "\n".join(
        ligne for ligne in css.splitlines()
        if "base64" not in ligne and not ligne.strip().startswith(("/*", "*", "--"))
    )

    for valeur in ("80px", "-0.06em", "1.15", "77px", "76px", "253, 139, 195"):
        assert valeur not in corps, f"{valeur} est encore en dur dans style.css"
```

And keep the anti-escaping test, adapted to the new signature:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `build_document()` takes the old signature; `test_le_css_ne_contient_plus_de_valeurs_de_mise_en_page` fails on `80px`.

- [ ] **Step 3: Rewrite `slide.html.j2`**

Replace `app/content/templates/kinda_post/slide.html.j2` with:

```html
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Kinda post</title>
<style>{{ variables }}</style>
<style>{{ css }}</style>
</head>
<body>
  <div id="slide">
    <img id="photo" src="{{ image_data_uri }}" alt="">
    <div id="scrim"></div>
    <img id="logo" src="{{ logo_data_uri }}" alt="">
    <div id="headline">{{ textes.headline }}</div>
  </div>
</body>
</html>
```

Two `<style>` blocks and not one: the variables come from Python at every render, the skeleton is a static file. Keeping them apart makes the injected block readable in a dump of the document.

Note: the inline `style="--scale: ..."` disappears — `--scale` now lives in the injected `:root` block.

- [ ] **Step 4: Rewrite `style.css` as a value-free skeleton**

In `app/content/templates/kinda_post/style.css`, keep the `@font-face` block (with its embedded base64 font) **byte for byte**, and keep every explanatory comment — they carry the Figma provenance. Replace only the layout values. The rules become:

```css
body {
  width: var(--slide-width);
  height: var(--slide-height);
  overflow: hidden;
  background: #000;
  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;
}

#slide {
  position: relative;
  width: var(--slide-width);
  height: var(--slide-height);
  overflow: hidden;
  background: #000;
}

/* Le voile bas n'a plus de hauteur figee : `--scrim-height` suit la hauteur
   mesuree du bloc d'accroche. Sans cela, une accroche de 5 lignes sortirait du
   degrade le plus dense -- invisible sur la maquette dont le bas est deja
   sombre, illisible sur une photo claire.

   La valeur de repli 817px est celle de la maquette, et elle n'est PAS un
   provisoire : un `var()` sans repli invalide la propriete ENTIERE, pas
   seulement son calque -- les trois degrades passeraient en plein cadre, sans
   erreur ni test rouge. Le repli garantit qu'un oubli degrade vers la maquette
   et non vers n'importe quoi. */
#scrim {
  position: absolute;
  inset: 0;
  background-repeat: no-repeat;
  background-image:
    linear-gradient(to bottom, rgba(0, 0, 0, 0), var(--voile-noir)),
    linear-gradient(to bottom, var(--voile-rose-debut), var(--voile-rose)),
    linear-gradient(to bottom, var(--voile-noir), rgba(0, 0, 0, 0));
  background-size:
    var(--slide-width) var(--scrim-height, 817px),
    var(--slide-width) var(--scrim-height, 817px),
    var(--slide-width) var(--voile-haut-hauteur);
  background-position:
    0 calc(var(--slide-height) - var(--scrim-height, 817px)),
    0 calc(var(--slide-height) - var(--scrim-height, 817px)),
    0 0;
}

#headline {
  position: absolute;
  left: var(--headline-left);
  right: var(--headline-right);
  bottom: var(--headline-offset);
  text-align: var(--headline-align);
  font-family: "KindaDisplay";
  font-weight: 700;
  font-size: calc(var(--headline-font-size) * var(--scale));
  line-height: var(--headline-line-height);
  letter-spacing: var(--headline-letter-spacing);
  color: var(--headline-color);
  overflow-wrap: break-word;
  word-break: break-word;
  hyphens: none;
}

#headline mark {
  color: inherit;
  background-color: transparent;
  background-image: linear-gradient(
    var(--headline-mark-background), var(--headline-mark-background)
  );
  background-size: 100% 0.9em;
  background-position: 0 55%;
  background-repeat: no-repeat;
  padding: 0 0.0375em;
  box-decoration-break: clone;
  -webkit-box-decoration-break: clone;
}
```

Two deliberate exceptions to "no values in the CSS", to be commented in the file:
- `727px` for the **top** scrim — it does not depend on the text, it is pure decoration.
- the three gradient colour stops — they are a fixed decorative recipe, not a layout rule. The `rgba(253, 139, 195, ...)` values of the gradients are therefore excluded from the sentinel test: the test only looks for the bare string `253, 139, 195`, so **update the sentinel test to drop that entry** if these stops remain, or move the colours into variables. **Choose: move them into variables** so the sentinel stays strict. Add to `Gabarit` nothing new — add them to `variables_css` as fixed output:

```python
    # Recette decorative des voiles, fixe : elle ne depend pas du texte. Elle passe
    # quand meme par une variable pour que la sentinelle de style.css reste stricte
    # -- une valeur en dur dans la feuille est une regle qui echappe aux tests.
    lignes += [
        "--voile-noir: rgba(0, 0, 0, 0.8);",
        "--voile-rose: rgba(253, 139, 195, 0.3);",
        "--voile-haut-hauteur: 727px;",
    ]
```

and in the CSS use `var(--voile-noir)`, `var(--voile-rose)`, `var(--voile-haut-hauteur)`. Add a test in `tests/test_layout.py`:

```python
def test_variables_css_expose_la_recette_des_voiles():
    css = variables_css(GABARIT_POST, scale=1.0)

    assert "--voile-noir: rgba(0, 0, 0, 0.8);" in css
    assert "--voile-haut-hauteur: 727px;" in css
```

- [ ] **Step 5: Rewrite `build_document` in `render.py`**

Replace the existing `build_document` with:

```python
def build_document(
    # `gabarit_` et non `gabarit` : le module importe deja une FONCTION `gabarit()`,
    # que ce nom masquerait dans tout le corps.
    gabarit_: Gabarit,
    textes: dict[str, str],
    image_data_uri: str,
    scale: float = 1.0,
    scrim_height: float | None = None,
) -> str:
    """Assemble le HTML autonome d'une slide (feuille, police et images inlinees).

    Sortie par une fonction a part pour etre verifiable sans Chromium : c'est ici
    que se joue l'echappement de la feuille de style, dont un ratage ne se voit
    sur le PNG que si on connait deja la bonne police.
    """
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    # Markup et non str : l'autoescape du gabarit transformerait les guillemets
    # de la feuille en `&#34;`, ce qui casse SILENCIEUSEMENT `font-family:
    # "KindaDisplay"` et `format("woff2")` -- l'@font-face n'est alors pas
    # enregistre du tout et Chromium rend l'accroche dans sa police par defaut
    # (un serif), avec d'autres retours a la ligne que le gabarit Figma.
    css = Markup((TEMPLATES_DIR / gabarit_.name / "style.css").read_text(encoding="utf-8"))
    return env.get_template(f"{gabarit_.name}/slide.html.j2").render(
        css=css,
        variables=Markup(variables_css(gabarit_, scale, scrim_height)),
        logo_data_uri=_logo_data_uri(),
        image_data_uri=image_data_uri,
        textes={role: Markup(to_html(textes[role])) for role in gabarit_.roles},
    )
```

Add to the imports of `render.py`:

```python
from app.content.layout import Gabarit, hauteur_voile, variables_css
```

Delete the module constants `SLIDE_WIDTH`, `SLIDE_HEIGHT`, `MAX_HEADLINE_HEIGHT`, `SCALE_STEPS` — they are now in the gabarit. Adapt `_render_one` / `render_post` minimally so the file still imports (the real rewrite is Task 3): make `render_post` build `gabarit = layout.gabarit(template)` and call `build_document(gabarit, slide, image_uri)`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_render.py tests/test_layout.py -v && uv run ruff check .`
Expected: PASS

- [ ] **Step 7: Verify the render did NOT change visually**

This is a refactoring: the PNG must be identical to before. Run:

```bash
uv run python - <<'PY'
from pathlib import Path
from app.content.render import render_post
slides = [{"headline": "Nico Moreno, Mosimann, Cassius... et **c'est ce week-end** aux **Vieilles Charrues**", "image_url": None}]
png = render_post(slides)[0]
png = png.png if hasattr(png, "png") else png
Path("/tmp/apres_refactoring.png").write_bytes(png)
print("ecrit, ", len(png), "octets")
PY
```

Open `/tmp/apres_refactoring.png` and compare with `exemples/[PREVIEW] - Post information.png`: same three lines, same left margin, same font. Differences in the photo and in the text are normal (different photo, different headline); a difference in **font**, in **line breaks** or in **left margin** means the variables are not reaching the CSS.

- [ ] **Step 8: Commit**

```bash
git add app/content/templates/kinda_post/ app/content/render.py app/content/layout.py tests/
git commit -m "refactor(posts): le CSS du post devient un squelette sans valeurs

style.css ne contient plus de nombres de mise en page : il consomme des
variables que Python injecte depuis le Gabarit. Un test-sentinelle echoue
si une valeur revient en dur -- une valeur dans la feuille est une regle
qui echappe a layout.py, donc aux tests.

La recette des voiles passe aussi par des variables, pour que la
sentinelle reste stricte. Le voile bas prend une hauteur variable, que la
tache suivante branchera sur la mesure du bloc.

Rendu inchange : refactoring, pas changement de design."
```

---

## Task 3: mesure par Chromium et boucle d'ajustement

Le remplacement du cœur : trois paliers → mesure exacte des lignes et dichotomie.

**Files:**
- Modify: `app/content/render.py`
- Modify: `app/api/posts.py` (les appelants de `render_post`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `layout.Ajustement`, `layout.Mesure`, `layout.gabarit`, `build_document`
- Produces:
  - `RenduSlide(png: bytes, scale: float, lines: dict[str, int], overflow: bool)` (dataclass gelée)
  - `render_post(slides: list[dict], template: str = "kinda_post") -> list[RenduSlide]`
  - `mesurer_role(page, role: str) -> Mesure`

- [ ] **Step 1: Write the failing tests**

In `tests/test_render.py`, **delete** `test_render_post_reduit_la_police_sur_une_accroche_maximale` (the `scale_log` hack it relied on disappears — `RenduSlide` now carries the information as a first-class citizen) and add:

```python
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
```

Also adapt `test_render_post_produit_un_png_par_slide`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `AttributeError: 'bytes' object has no attribute 'lines'`

- [ ] **Step 3: Write the measurement JS and `mesurer_role`**

Add to `app/content/render.py`:

```python
# Regroupement par ordonnee et non un rectangle = une ligne : un `<mark>` coupe la
# ligne en PLUSIEURS rectangles (avant, dans, apres le surlignage). Sans
# regroupement, une accroche de 3 lignes avec deux segments surlignes compterait
# 7 lignes et declencherait une reduction injustifiee.
# La tolerance de 4 px absorbe les ecarts d'ordonnee entre fragments d'une meme
# ligne (accents, `background-position` du mark).
_MESURE_JS = """
(role) => {
  const el = document.getElementById(role);
  const style = getComputedStyle(el);
  // Un role a bande de fond est un element EN LIGNE : ses propres rectangles
  // incluent le padding et le fond, donc ils SONT les bandes -- c'est ce qu'on
  // veut mesurer. Un role en bloc n'a qu'un rectangle : il faut passer par un
  // Range sur son contenu pour obtenir une ligne par ligne rendue.
  const rects = style.display === 'inline'
    ? [...el.getClientRects()]
    : (() => { const r = document.createRange(); r.selectNodeContents(el);
               return [...r.getClientRects()]; })();

  const lignes = [];
  for (const rect of rects.filter(r => r.width > 0 && r.height > 0)
                          .sort((a, b) => a.top - b.top)) {
    const derniere = lignes[lignes.length - 1];
    if (derniere && Math.abs(rect.top - derniere.top) < 4) {
      derniere.gauche = Math.min(derniere.gauche, rect.left);
      derniere.droite = Math.max(derniere.droite, rect.right);
      derniere.bas = Math.max(derniere.bas, rect.bottom);
    } else {
      lignes.push({top: rect.top, bas: rect.bottom, gauche: rect.left, droite: rect.right});
    }
  }
  if (!lignes.length) return {widths: [], height: 0};
  return {
    widths: lignes.map(l => l.droite - l.gauche),
    height: lignes[lignes.length - 1].bas - lignes[0].top,
  };
}
"""


def mesurer_role(page, role: str) -> Mesure:
    """Mesure un role : une largeur par ligne rendue, plus la hauteur du bloc."""
    brut = page.evaluate(_MESURE_JS, role)
    return Mesure(line_widths=tuple(brut["widths"]), height=float(brut["height"]))
```

- [ ] **Step 4: Rewrite `_render_one` and `render_post`**

Replace both in `app/content/render.py`:

```python
@dataclass(frozen=True)
class RenduSlide:
    """Le PNG et le rapport de ce qui s'est passe pour le produire.

    Le rapport n'est pas un detail de test : `overflow` est ce qui permet au
    dashboard de signaler un post en defaut plutot que de laisser publier une
    image coupee.
    """

    png: bytes
    scale: float
    lines: dict[str, int]
    overflow: bool


def _render_one(page, gabarit_: Gabarit, textes: dict[str, str], image_uri: str) -> RenduSlide:
    """Rend une slide : mesure, ajuste, puis capture.

    Deux passes AU MINIMUM, et c'est structurel : la hauteur du voile depend de la
    hauteur mesuree du bloc, qu'on ne connait qu'apres un premier rendu.
    """
    ajustement = Ajustement(gabarit_)
    mesures: dict[str, Mesure] = {}
    while (echelle := ajustement.prochaine_echelle()) is not None:
        page.set_content(build_document(gabarit_, textes, image_uri, echelle), wait_until="load")
        page.evaluate("document.fonts.ready")
        mesures = {role: mesurer_role(page, role) for role in gabarit_.roles}
        ajustement.enregistrer(mesures)

    resultat = ajustement.resultat
    if resultat.overflow:
        logger.warning(
            "texte intenable meme a l'echelle %.2f, slide marquee en defaut : %r",
            resultat.scale, textes,
        )

    # Passe finale : l'echelle retenue, et le voile cale sur la hauteur du bloc.
    role_principal = next(iter(gabarit_.roles))
    voile = (
        hauteur_voile(gabarit_, role_principal, mesures[role_principal])
        if role_principal in mesures
        else None
    )
    page.set_content(
        build_document(gabarit_, textes, image_uri, resultat.scale, voile), wait_until="load"
    )
    page.evaluate("document.fonts.ready")
    return RenduSlide(
        png=page.screenshot(type="png"),
        scale=resultat.scale,
        lines=resultat.lines,
        overflow=resultat.overflow,
    )


def render_post(slides: list[dict], template: str = "kinda_post") -> list[RenduSlide]:
    """Rend chaque slide du post. Un seul Chromium pour tout le post."""
    gabarit_ = gabarit(template)
    rendus: list[RenduSlide] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": gabarit_.width, "height": gabarit_.height},
                device_scale_factor=1,
            )
            for slide in slides:
                image_uri = fetch_image_data_uri(slide.get("image_url"))
                textes = {role: slide[role] for role in gabarit_.roles}
                rendus.append(_render_one(page, gabarit_, textes, image_uri))
        finally:
            browser.close()
    return rendus
```

Update the imports of `render.py`:

```python
from dataclasses import dataclass
...
from app.content.layout import Ajustement, Gabarit, Mesure, gabarit, hauteur_voile, variables_css
```

Note on naming: `render.py` imports a **function** `gabarit()` from `layout`, so no parameter or local may be called `gabarit` — it would shadow the function throughout that body. Every gabarit-typed parameter and local is therefore named `gabarit_`, in `build_document`, `_render_one`, `render_post` and `mesurer_bandes` alike. Follow exactly the naming above; it is not a stylistic preference but the reason the code works when someone later adds a `gabarit(...)` call inside one of these bodies.

- [ ] **Step 5: Update the callers in `app/api/posts.py`**

Two call sites return bytes today. Modify:

```python
@router.get("/posts/{post_id}/slides/{index}.png")
def rendre_slide(post_id: int, index: int, db: Session = Depends(get_db)):
    post = _post_ou_404(db, post_id)
    if index < 1 or index > len(post.slides):
        raise HTTPException(status_code=404, detail="Slide not found")
    rendu = render_post([post.slides[index - 1]], post.template)[0]
    return Response(content=rendu.png, media_type="image/png")
```

and in `exporter_post`:

```python
    rendus = render_post(post.slides, post.template)

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for numero, rendu in enumerate(rendus, start=1):
            archive.writestr(f"slide_{numero:02d}.png", rendu.png)
        archive.writestr("caption.txt", _texte_legende(post))
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS (the whole suite — `tests/test_api.py` must stay green)

- [ ] **Step 7: Commit**

```bash
git add app/content/render.py app/api/posts.py tests/test_render.py
git commit -m "feat(posts): ajustement continu et voile qui suit le bloc

Les trois paliers 1.0/0.9/0.8 avaient deux defauts : un saut de 10% se
voit, et apres 0.8 on rendait QUAND MEME l'image debordee en se contentant
d'un log. Remplaces par une dichotomie sur l'echelle, et par un drapeau
`overflow` que le dashboard pourra montrer.

Le comptage de lignes passe par Range.getClientRects(), regroupe par
ordonnee : un <mark> coupe une ligne en plusieurs rectangles, et sans
regroupement une accroche de 3 lignes en compterait 7.

Le voile bas se cale sur la hauteur mesuree du bloc -- consequence
obligatoire du bloc extensible, sinon une accroche de 5 lignes sort du
degrade dense et devient illisible sur une photo claire.

render_post rend des RenduSlide et non des bytes : le rapport remplace le
parametre scale_log, qui n'existait que pour les tests."
```

---

## Task 4: le gabarit `kinda_reel`

**Files:**
- Create: `app/content/templates/kinda_reel/slide.html.j2`
- Create: `app/content/templates/kinda_reel/style.css`
- Modify: `app/content/layout.py` (`GABARIT_REEL`, `GABARITS`)
- Modify: `app/content/render.py` (le voile est optionnel)
- Test: `tests/test_layout.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: tout ce qui précède
- Produces: `GABARIT_REEL: Gabarit` avec les rôles `title` et `subtitle`, enregistré dans `GABARITS` sous `"kinda_reel"`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_layout.py`:

```python
def test_le_gabarit_reel_porte_les_valeurs_mesurees_sur_la_maquette():
    from app.content.layout import GABARIT_REEL

    titre, zone_titre = GABARIT_REEL.roles["title"]
    sous_titre, zone_sous_titre = GABARIT_REEL.roles["subtitle"]

    assert (GABARIT_REEL.width, GABARIT_REEL.height) == (1080, 1920)
    assert titre.background == "#FD8BC3"
    assert titre.color == "#FFF7FA"
    assert sous_titre.background == "#FFF7FA"
    assert sous_titre.color == "#1D1D1D"
    # La bande rose est DROITE, la creme est inclinee : confirme par l'utilisateur
    # comme voulu (effet autocollant), et mesure a 0,6 deg antihoraire.
    assert titre.tilt == 0.0
    assert sous_titre.tilt == -0.6
    # Les deux corps sont PROCHES, contrairement a ce que l'oeil suggere : les
    # glyphes mesurent 83 px (titre) et 64 px (sous-titre), ce qui ramene les deux
    # dans les mid-80 px. Coder « titre 1.4x » ferait diverger le rendu.
    assert abs(titre.font_size - sous_titre.font_size) <= 8
    # Bloc centre : 42,9 % de 1920.
    assert zone_titre.anchor == "center"
    assert zone_titre.align == "center"
    assert zone_sous_titre.align == "center"
    # Deux bandes par role au maximum, puis reduction.
    assert zone_titre.max_lines == 2
    assert zone_sous_titre.max_lines == 2
```

Append to `tests/test_render.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_layout.py tests/test_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'GABARIT_REEL'`

- [ ] **Step 3: Add `GABARIT_REEL` to `layout.py`**

Insert before `GABARITS` and update `GABARITS`:

```python
# Valeurs MESUREES AU PIXEL sur `exemples/[PREVIEW] - Cover Reels.png`, faute de
# frame Figma extraite. Voir REFERENCE.md pour le releve complet.
#
# Deux pieges que l'oeil tend ici, et qui sont documentes parce qu'ils ont deja
# failli etre codes de travers :
#   - les deux corps sont PROCHES (mid-80 px), pas 1.4x. Ce qui fait paraitre le
#     titre plus gros, c'est sa bande plus haute (94 vs 85) et ses capitales
#     larges face a un sous-titre plein de lettres etroites ;
#   - la bande rose est droite, la creme est inclinee de 0,6 deg antihoraire.
#     Confirme comme voulu par le proprietaire du design.
GABARIT_REEL = Gabarit(
    name="kinda_reel",
    width=1080,
    height=1920,
    roles={
        "title": (
            Typo(
                font_size=86,
                line_height=1.1,
                letter_spacing=-0.04,
                color="#FFF7FA",
                background="#FD8BC3",
                tilt=0.0,
            ),
            Zone(
                left=77, right=77, anchor="center", anchor_offset=0, align="center",
                max_lines=2, max_line_width=926, min_scale=0.7,
            ),
        ),
        "subtitle": (
            Typo(
                font_size=80,
                line_height=1.1,
                letter_spacing=-0.04,
                color="#1D1D1D",
                background="#FFF7FA",
                tilt=-0.6,
            ),
            Zone(
                left=77, right=77, anchor="center", anchor_offset=0, align="center",
                max_lines=2, max_line_width=926, min_scale=0.7,
            ),
        ),
    },
)

GABARITS: dict[str, Gabarit] = {g.name: g for g in (GABARIT_POST, GABARIT_REEL)}
```

Add a gabarit-level constant for the vertical centre of the block. Add a field to `Gabarit`:

```python
@dataclass(frozen=True)
class Gabarit:
    name: str
    width: int
    height: int
    roles: dict[str, tuple[Typo, Zone]]
    # Position verticale du bloc de texte, en fraction de la hauteur. Mesuree a
    # 42,9 % sur la cover reel ; inutilisee quand les zones sont ancrees en bas.
    block_center: float | None = None
```

`GABARIT_REEL` takes `block_center=0.429`. Add `--block-center: 42.9%;` to `variables_css` when `block_center is not None`, and a test:

```python
def test_variables_css_expose_le_centre_du_bloc_quand_il_existe():
    from app.content.layout import GABARIT_REEL

    assert "--block-center: 42.9%;" in variables_css(GABARIT_REEL, scale=1.0)
    assert "--block-center" not in variables_css(GABARIT_POST, scale=1.0)
```

- [ ] **Step 4: Create the reel template**

`app/content/templates/kinda_reel/slide.html.j2`:

```html
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Kinda cover reel</title>
<style>{{ variables }}</style>
<style>{{ css }}</style>
</head>
<body>
  <div id="slide">
    <img id="photo" src="{{ image_data_uri }}" alt="">
    <img id="logo" src="{{ logo_data_uri }}" alt="">
    <div id="bandes">
      <div class="ligne"><span id="title">{{ textes.title }}</span></div>
      <div class="ligne ligne--subtitle"><span id="subtitle">{{ textes.subtitle }}</span></div>
    </div>
  </div>
</body>
</html>
```

`app/content/templates/kinda_reel/style.css`:

```css
/* Cover reel Kinda -- 1080x1920. Valeurs MESUREES sur
   exemples/[PREVIEW] - Cover Reels.png (voir REFERENCE.md).

   Feuille AUTONOME : la police est embarquee en base64 ci-dessous, comme pour le
   post. Aucune URL externe, aucune police systeme -- une police systeme ferait
   diverger le rendu d'une machine a l'autre silencieusement.

   PAS DE VOILE ici, contrairement au post : le contraste vient des bandes
   elles-memes. */

/* Copier ICI le bloc @font-face complet de kinda_post/style.css, a l'identique
   (police Liberation Sans Narrow Bold en base64). */

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  width: var(--slide-width);
  height: var(--slide-height);
  overflow: hidden;
  background: #000;
  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;
}

#slide {
  position: relative;
  width: var(--slide-width);
  height: var(--slide-height);
  overflow: hidden;
  background: #000;
}

#photo {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
}

/* Meme geometrie que sur le post, et elle vient des memes variables : le logo
   est identique d'un gabarit a l'autre, donc sa position n'a pas a etre ecrite
   deux fois. */
#logo {
  position: absolute;
  right: var(--logo-right);
  top: var(--logo-top);
  width: var(--logo-width);
  height: auto;
}

/* Le bloc de texte : centre verticalement sur --block-center, chaque bande
   centree horizontalement sur SA propre largeur (mesure : centres a 529 et 537
   pour une toile centree a 540). */
#bandes {
  position: absolute;
  left: var(--title-left);
  right: var(--title-right);
  top: var(--block-center);
  transform: translateY(-50%);
}

.ligne { text-align: center; }

/* Les bandes EPOUSENT le texte : un span en ligne, pas un bloc.
   box-decoration-break: clone est ce qui fait qu'un titre trop long produit DEUX
   bandes empilees au lieu d'un fond etire a travers le retour a la ligne -- c'est
   la regle d'adaptation retenue pour ce gabarit. */
#title, #subtitle {
  display: inline;
  box-decoration-break: clone;
  -webkit-box-decoration-break: clone;
  font-family: "KindaDisplay";
  font-weight: 700;
  padding: 0.06em 0.12em;
}

#title {
  font-size: calc(var(--title-font-size) * var(--scale));
  line-height: var(--title-line-height);
  letter-spacing: var(--title-letter-spacing);
  color: var(--title-color);
  background-color: var(--title-background);
}

#subtitle {
  font-size: calc(var(--subtitle-font-size) * var(--scale));
  line-height: var(--subtitle-line-height);
  letter-spacing: var(--subtitle-letter-spacing);
  color: var(--subtitle-color);
  background-color: var(--subtitle-background);
}

/* L'inclinaison porte sur le BLOC de ligne : `transform` ne s'applique pas a un
   element en ligne. Confirme comme voulu (effet autocollant) : la bande rose est
   droite, la creme penche de 0,6 deg antihoraire. */
.ligne--subtitle { transform: rotate(var(--subtitle-tilt)); }

/* Le <mark> du post existe aussi ici : une accroche de reel peut surligner un
   segment. Sur une bande deja coloree, le surlignage inverse le fond. */
#title mark, #subtitle mark {
  color: inherit;
  background: transparent;
}
```

- [ ] **Step 5: Make the scrim optional in `render.py`**

The reel has no `#scrim`. In `_render_one`, only compute `hauteur_voile` when the template has a scrim. The simplest reliable signal: the presence of `--scrim-height` in the CSS is irrelevant — decide from the gabarit. Add to `Gabarit`:

```python
    # Le post a trois degrades pour rendre le texte lisible ; la cover reel n'en a
    # pas, son contraste vient des bandes.
    voile: bool = True
```

`GABARIT_REEL` takes `voile=False`. In `_render_one`:

```python
    voile = None
    if gabarit.voile:
        role_principal = next(iter(gabarit.roles))
        voile = hauteur_voile(gabarit, role_principal, mesures[role_principal])
```

- [ ] **Step 6: Add `mesurer_bandes` to `render.py`**

A test helper that is also useful for calibration:

```python
def mesurer_bandes(slide: dict, template: str = "kinda_reel") -> dict[str, list[float]]:
    """Les largeurs de bande reellement rendues, role par role.

    Sert a la calibration : les corps et paddings exacts ne se deduisent pas par
    inversion des mesures de la maquette, ils se calent en rendant puis en
    comparant.
    """
    gabarit_ = gabarit(template)
    image_uri = fetch_image_data_uri(slide.get("image_url"))
    textes = {role: slide[role] for role in gabarit_.roles}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": gabarit_.width, "height": gabarit_.height},
                device_scale_factor=1,
            )
            page.set_content(build_document(gabarit_, textes, image_uri), wait_until="load")
            page.evaluate("document.fonts.ready")
            return {
                role: list(mesurer_role(page, role).line_widths) for role in gabarit_.roles
            }
        finally:
            browser.close()
```

- [ ] **Step 7: Copy the embedded font into the reel stylesheet**

Run:

```bash
uv run python - <<'PY'
import re
from pathlib import Path
base = Path("app/content/templates")
post = (base / "kinda_post" / "style.css").read_text(encoding="utf-8")
bloc = re.search(r"@font-face \{.*?\}", post, re.DOTALL).group(0)
reel_path = base / "kinda_reel" / "style.css"
reel = reel_path.read_text(encoding="utf-8")
marque = "/* Copier ICI le bloc @font-face complet de kinda_post/style.css, a l'identique\n   (police Liberation Sans Narrow Bold en base64). */"
assert marque in reel, "marque de remplacement introuvable"
reel_path.write_text(reel.replace(marque, bloc), encoding="utf-8")
print("bloc @font-face copie,", len(bloc), "caracteres")
PY
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS. The two reel tests assert **structure only** (one band per role, PNG dimensions, second band on a long title) — not the band widths, which are calibrated in Task 5. If a reel test fails here, the template is wrong, not the calibration: fix it before committing.

Record the current band widths for the next task (they are not asserted yet, only observed):

```bash
uv run python - <<'PY'
from app.content.render import mesurer_bandes
print(mesurer_bandes({
    "title": "**Gregoire Cliquet**",
    "subtitle": "au festival des **vieilles Charrues**",
    "image_url": None,
}))
PY
```

- [ ] **Step 9: Commit**

```bash
git add app/content/layout.py app/content/render.py app/content/templates/kinda_reel/ tests/
git commit -m "feat(posts): gabarit cover reel 1080x1920

Second gabarit, et une logique de texte differente du post : deux bandes
empilees qui epousent le texte, centrees, au lieu d'un bloc ancre en bas.
Pas de voile -- le contraste vient des bandes.

Un titre trop long ne reduit pas sa police : il passe sur une deuxieme bande,
via box-decoration-break: clone. L'empilement de bandes EST le langage du
gabarit.

Valeurs mesurees au pixel sur la maquette. Deux pieges documentes dans le
code parce qu'ils ont failli etre codes de travers : les deux corps sont
proches (mid-80 px) et non 1.4x comme l'oeil le suggere, et la bande creme
est inclinee de 0,6 deg alors que la rose est droite."
```

---

## Task 5: calibration du reel contre la maquette

La seule tâche du plan qui n'est pas déterministe : on ajuste des nombres jusqu'à retomber sur les mesures.

**Files:**
- Modify: `app/content/layout.py` (`GABARIT_REEL` : `font_size`, `letter_spacing`, `line_height`)
- Modify: `app/content/templates/kinda_reel/style.css` (`padding` des bandes)
- Test: `tests/test_render.py` (ajouter le test de largeur de bande)

**Interfaces:**
- Consumes: `mesurer_bandes`
- Produces: rien de nouveau — des valeurs calibrées.

- [ ] **Step 1: Measure the current state**

```bash
uv run python - <<'PY'
from app.content.render import mesurer_bandes
slide = {
    "title": "**Gregoire Cliquet**",
    "subtitle": "au festival des **vieilles Charrues**",
    "image_url": None,
}
largeurs = mesurer_bandes(slide)
print("title   :", largeurs["title"], "-> cible 534")
print("subtitle:", largeurs["subtitle"], "-> cible 753")
PY
```

- [ ] **Step 2: Adjust `font_size` proportionally**

The band width is roughly proportional to `font_size`. If `title` measures 500 for a target of 534, multiply `font_size` by `534/500`. Adjust `title` and `subtitle` **independently** — they are two distinct roles. Re-run Step 1 and iterate until both are within ±10 px.

Keep `abs(title.font_size - subtitle.font_size) <= 8` (the test of Task 4): if calibration pushes the two apart by more than 8 px, that means the maquette really does use two different sizes and the test's tolerance must be widened — **document why in the commit** rather than forcing the values.

- [ ] **Step 3: Check the band heights**

```bash
uv run python - <<'PY'
from pathlib import Path
from app.content.render import render_post
slide = {
    "title": "**Gregoire Cliquet**",
    "subtitle": "au festival des **vieilles Charrues**",
    "image_url": None,
}
Path("/tmp/reel.png").write_bytes(render_post([slide], "kinda_reel")[0].png)
print("ecrit /tmp/reel.png")
PY

uv run --with pillow --with numpy python - <<'PY'
from PIL import Image
import numpy as np
a = np.asarray(Image.open("/tmp/reel.png").convert("RGB")).astype(int)
r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
rose = (abs(r - 253) < 22) & (abs(g - 139) < 34) & (abs(b - 195) < 28)
ys, xs = np.where(rose)
print(f"bande rose : x {xs.min()}-{xs.max()} (l={xs.max()-xs.min()+1}, cible 534)")
print(f"             y {ys.min()}-{ys.max()} (h={ys.max()-ys.min()+1}, cible 94)")
PY
```

Adjust the vertical `padding` of `#title, #subtitle` in `kinda_reel/style.css` until the pink band height lands within ±6 px of 94. The horizontal padding affects the width — re-run Step 1 after any change.

- [ ] **Step 4: Compare visually**

Open `/tmp/reel.png` side by side with `exemples/[PREVIEW] - Cover Reels.png`. Check: vertical position of the block, gap (or absence of gap) between the two bands, tilt of the cream band, logo position.

- [ ] **Step 5: Add the band-width test, now that the values are calibrated**

Append to `tests/test_render.py`:

```python
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
```

Run: `uv run pytest tests/test_render.py -v && uv run ruff check .`
Expected: PASS, band widths included. If this test fails, go back to Step 2 — it is the gate of the calibration, not an obstacle to work around.

- [ ] **Step 6: Commit**

```bash
git add app/content/layout.py app/content/templates/kinda_reel/style.css tests/test_render.py
git commit -m "fix(posts): calibration du gabarit reel sur les mesures de la maquette

Les corps et paddings ne se deduisent pas par inversion des mesures : la
police est un substitut metriquement compatible d'Arial Narrow, pas Arial
Narrow. Cales en rendant puis en comparant : bande rose 534 x 94, bande
creme 753 de large."
```

---

## Task 6: la colonne `render_report`

Ce qui rend le drapeau `overflow` visible ailleurs que dans un log.

**Files:**
- Modify: `app/models.py`
- Create: `alembic/versions/0009_add_render_report.py`
- Modify: `app/schemas.py`
- Modify: `app/api/posts.py`
- Test: `tests/test_api_posts.py`

**Interfaces:**
- Consumes: `RenduSlide`
- Produces: `Post.render_report: Mapped[dict | None]`, endpoint `POST /posts/{post_id}/render`

Forme du rapport :

```json
{"slides": [{"scale": 1.0, "lines": {"headline": 3}, "overflow": false}]}
```

- [ ] **Step 1: Write the failing test**

Create (or extend) `tests/test_api_posts.py`:

```python
"""L'API des posts. Sans Chromium : on remplace le rendu par un faux.

Le but n'est pas de tester le rendu ici -- tests/test_render.py le fait -- mais
que le RAPPORT arrive bien en base. C'est lui qui permet au dashboard de signaler
un post en defaut plutot que de laisser publier une image coupee.
"""

import app.api.posts as posts_module
from app.content.render import RenduSlide
from app.models import Post, PostStatus


def _post_en_base(db_session) -> Post:
    post = Post(
        article_id=None,
        article_title="Les Vieilles Charrues devoilent leur line-up",
        article_url="https://exemple.test/charrues",
        source_name="Tsugi",
        status=PostStatus.DRAFT,
        template="kinda_post",
        caption="Une legende",
        hashtags=["#electro"],
        slides=[
            {"headline": "**Vieilles Charrues** : le line-up", "image_url": None},
            {"headline": "Rendez-vous **ce week-end**", "image_url": None},
        ],
    )
    db_session.add(post)
    db_session.commit()
    return post


def test_render_persiste_le_rapport(client, db_session, monkeypatch):
    post = _post_en_base(db_session)

    def faux_render(slides, template="kinda_post"):
        return [
            RenduSlide(png=b"PNG", scale=1.0, lines={"headline": 3}, overflow=False),
            RenduSlide(png=b"PNG", scale=0.7, lines={"headline": 6}, overflow=True),
        ]

    monkeypatch.setattr(posts_module, "render_post", faux_render)

    reponse = client.post(f"/posts/{post.id}/render")

    assert reponse.status_code == 200
    rapport = reponse.json()
    assert rapport["slides"][0] == {"scale": 1.0, "lines": {"headline": 3}, "overflow": False}
    assert rapport["slides"][1]["overflow"] is True

    db_session.refresh(post)
    assert post.render_report == rapport


def test_render_sur_un_post_inconnu_rend_404(client):
    assert client.post("/posts/999/render").status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_api_posts.py -v`
Expected: FAIL — `AttributeError: 'Post' object has no attribute 'render_report'`

- [ ] **Step 3: Add the column to the model**

In `app/models.py`, class `Post`, after `slides` :

```python
    # Rapport du dernier rendu : par slide, l'echelle retenue, le nombre de lignes
    # et un drapeau de debordement. Nullable : un post jamais rendu n'en a pas.
    # C'est ce qui permet au dashboard de signaler un post en defaut au lieu de
    # laisser publier une image coupee.
    # Reassigner le dict ENTIER, jamais de mutation in place.
    render_report: Mapped[dict | None] = mapped_column(JSONVariant)
```

- [ ] **Step 4: Write the migration**

Create `alembic/versions/0009_add_render_report.py`:

```python
"""add render_report : le rapport du dernier rendu d'un post

Le rendu peut aboutir a une image debordee malgre la reduction de police. On ne
veut pas que ce cas passe en silence : le rapport porte un drapeau `overflow`
que le dashboard montre.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("render_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("posts", "render_report")
```

- [ ] **Step 5: Expose it in the schemas**

In `app/schemas.py`, add to `PostOut` after `slides` :

```python
    render_report: dict | None = None
```

- [ ] **Step 6: Add the endpoint**

In `app/api/posts.py`, after `rendre_slide` :

```python
@router.post("/posts/{post_id}/render")
def rendre_post(post_id: int, db: Session = Depends(get_db)):
    """Rend toutes les slides et PERSISTE le rapport.

    En POST et pas en GET : c'est une ecriture. Les endpoints de rendu en GET
    restent purs -- ils servent l'image, ils ne consignent rien.
    """
    post = _post_ou_404(db, post_id)
    rendus = render_post(post.slides, post.template)
    rapport = {
        "slides": [
            {"scale": rendu.scale, "lines": rendu.lines, "overflow": rendu.overflow}
            for rendu in rendus
        ]
    }
    # Reassignation ENTIERE : une mutation in place ne serait pas persistee.
    post.render_report = rapport
    db.commit()
    return rapport
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS

- [ ] **Step 8: Check model/migration parity**

Run: `uv run alembic upgrade head && uv run alembic check`
Expected: `No new upgrade operations detected.`

If no database is reachable, note it and skip — do not invent a result.

- [ ] **Step 9: Commit**

```bash
git add app/models.py app/schemas.py app/api/posts.py alembic/versions/0009_add_render_report.py tests/test_api_posts.py
git commit -m "feat(posts): colonne render_report, le debordement devient visible

Un texte intenable meme au plancher de reduction produisait un log et une
image coupee. Le rapport (echelle, lignes, drapeau overflow par slide) est
persiste par POST /posts/{id}/render et expose par l'API : le dashboard
peut signaler un post en defaut.

En POST et pas en GET parce que c'est une ecriture -- les endpoints de
rendu en GET restent purs."
```

---

## Task 7: erreur explicite quand Chromium est absent

Petit, mais c'est la différence entre un outil fiable et un outil pénible.

**Files:**
- Modify: `app/content/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Produces: `RenduIndisponible(RuntimeError)`

- [ ] **Step 1: Write the failing test**

```python
def test_chromium_absent_donne_une_erreur_lisible(monkeypatch):
    """Le rendu exige Chromium Playwright, que le dashboard n'installe pas. Une
    trace d'exception au fond de Playwright ne dit pas quoi faire ; ce message,
    si."""
    import app.content.render as render_module
    from app.content.render import RenduIndisponible

    def faux_playwright():
        raise Exception("Executable doesn't exist at /path/chrome")

    monkeypatch.setattr(render_module, "sync_playwright", faux_playwright)

    with pytest.raises(RenduIndisponible, match="playwright install chromium"):
        render_post([{"headline": "Une **accroche**", "image_url": None}])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_render.py::test_chromium_absent_donne_une_erreur_lisible -v`
Expected: FAIL — `ImportError: cannot import name 'RenduIndisponible'`

- [ ] **Step 3: Implement**

In `app/content/render.py`:

```python
class RenduIndisponible(RuntimeError):
    """Levee quand Chromium Playwright n'est pas installe localement."""
```

and wrap the body of `render_post` (and of `mesurer_bandes`) :

```python
    try:
        with sync_playwright() as playwright:
            ...
    except RenduIndisponible:
        raise
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            raise RenduIndisponible(
                "Chromium Playwright est absent : lancer `uv run playwright install chromium`"
            ) from exc
        raise
```

- [ ] **Step 4: Surface it in the API**

In `app/api/posts.py`, add to the three render endpoints (`rendre_slide`, `rendre_post`, `exporter_post`) :

```python
    try:
        rendus = render_post(post.slides, post.template)
    except RenduIndisponible as exc:
        # 503 et pas 500 : la cause est une dependance locale manquante, pas un bug.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
```

Import `RenduIndisponible` from `app.content.render`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/content/render.py app/api/posts.py tests/test_render.py
git commit -m "feat(posts): erreur lisible quand Chromium Playwright est absent

Le dashboard n'installe pas Chromium. Une trace au fond de Playwright ne
dit pas quoi faire ; RenduIndisponible porte la commande a lancer, et
l'API rend un 503 (dependance locale manquante) et non un 500."
```

---

## Task 8: calibrer le plafond de lignes et documenter les deux gabarits

La dernière valeur devinée du plan devient une valeur mesurée.

**Files:**
- Modify: `app/content/layout.py` (`max_lines` du post, si la mesure le demande)
- Modify: `app/content/templates/kinda_post/REFERENCE.md`
- Test: `tests/test_layout.py`

- [ ] **Step 1: Render headlines of increasing length**

```bash
uv run python - <<'PY'
from pathlib import Path
from app.content.render import render_post

base = "**Nico Moreno** et Mosimann aux Vieilles Charrues ce week-end a Carhaix"
for lignes_visees in range(2, 8):
    accroche = " ".join([base] * max(1, lignes_visees // 2))[: 40 * lignes_visees]
    if "**" not in accroche:
        accroche = "**Kinda** " + accroche
    rendu = render_post([{"headline": accroche, "image_url": None}])[0]
    chemin = Path(f"/tmp/plafond_{rendu.lines['headline']}_lignes.png")
    chemin.write_bytes(rendu.png)
    print(f"{rendu.lines['headline']} lignes, echelle {rendu.scale} -> {chemin}")
PY
```

- [ ] **Step 2: Decide the ceiling by looking at the images**

Open the produced files. The criterion: **from how many lines does the photo stop being readable as an image?** Below that number, the accroche is acceptable. Above, reduction is preferable.

If the answer is not 5, change `max_lines` in `GABARIT_POST` **and** the sentinel test in `tests/test_layout.py`, and write the measured justification in the code comment — replace the current "Plafond calibre a la tache 8" comment with the real reasoning, in the style of the existing `MAX_HEADLINE_HEIGHT` comment in the pre-refactoring `render.py` (which stated its measurements: 276 px at three lines, 368 px at four).

- [ ] **Step 3: Update `REFERENCE.md`**

`app/content/templates/kinda_post/REFERENCE.md` currently only documents the post. Add:

1. a section **« Gabarit `kinda_reel` »** with the measurement table from this plan, stating that the source is `exemples/[PREVIEW] - Cover Reels.png` and **not** an extracted Figma frame — and that a frame should be extracted at the next design pass;
2. a section **« Références visuelles »** naming the two PNGs of `exemples/` as the comparison references, with the instruction to re-compare after any template change;
3. an update of the **variables contract**: `build_document` now consumes `variables` and `textes.<role>`, and the ids `#headline` (post) / `#title`, `#subtitle` (reel) are consumed by the measurement JS — changing them breaks the line counting silently;
4. a note that the layout values now live in `app/content/layout.py` and that `style.css` must stay value-free (a test enforces it).

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/content/layout.py app/content/templates/kinda_post/REFERENCE.md tests/test_layout.py
git commit -m "docs(posts): plafond de lignes mesure et REFERENCE.md des deux gabarits

Le plafond du post etait une estimation ; il est desormais cale en rendant
des accroches de longueur croissante et en regardant a partir de quand la
photo cesse d'exister.

REFERENCE.md documente les deux gabarits, nomme les PNG d'exemples/ comme
references de comparaison visuelle, et acte que les valeurs de mise en page
vivent dans layout.py -- style.css doit rester sans valeurs, un test le
verifie."
```

---

## Self-Review

**Spec coverage:**

| Exigence de la spec | Tâche |
|---|---|
| `layout.py`, dataclasses gelées, zéro dépendance | 1 |
| Couture Chromium mesure / Python décide | 1 (décision) + 3 (mesure) |
| `Range.getClientRects()` pour le comptage | 3 |
| Trois étages : maquette → plancher → dichotomie | 1 |
| Plafond largeur + hauteur | 1 (règle) + 4 (valeurs reel) |
| `overflow` plutôt qu'un log silencieux | 1, 3, 6 |
| Voile qui suit la hauteur du bloc, rendu 2 passes | 1 (formule) + 3 (câblage) |
| CSS squelette sans valeurs | 2 |
| Gabarit `kinda_post` 1080×1440, valeurs Figma | 1 |
| Gabarit `kinda_reel` 1080×1920, valeurs mesurées | 4 |
| Titre long → deuxième bande rose | 4 |
| Inclinaison -0,6° du sous-titre | 4 |
| Rôles à la place de l'accroche unique | 2 (`textes`) + 4 (deux rôles) |
| Colonne `render_report` + migration | 6 |
| Chromium absent → erreur explicite | 7 |
| Tests unitaires purs + géométrie + anti-échappement | 1, 3, 4 |
| PNG d'`exemples/` en références documentées | 8 |
| Calibration du plafond de lignes | 8 |

Aucune exigence sans tâche.

**Hors périmètre, conformément à la spec :** composition automatique des titres de reel, sélection de vidéos, file quotidienne, feedback, modèle local, socle launchd.

**Placeholder scan:** deux tâches sont non déterministes par nature — la 5 (calibration des bandes) et la 8 (plafond de lignes). Toutes deux fournissent le **script de mesure** et le **critère de décision**, pas un « à ajuster ».

Aucun test n'est marqué `xfail` ni laissé rouge entre deux tâches : la tâche 4 n'asserte que la **structure** du gabarit reel (un bandeau par rôle, dimensions du PNG, seconde bande sur un titre long), et la tâche 5 ajoute le test de **largeur** une fois les valeurs calibrées. Chaque tâche laisse donc la suite verte, ce qui est la condition pour que la revue par tâche ait un sens.

**Type consistency:** `Mesure(line_widths, height)` / `.lines` ; `Resultat(scale, lines, overflow)` ; `RenduSlide(png, scale, lines, overflow)` ; `render_post -> list[RenduSlide]` ; `build_document(gabarit, textes, image_data_uri, scale, scrim_height)`. Le champ `lines` est un `dict[str, int]` partout, y compris dans le JSON de `render_report`. La fonction `gabarit(nom)` et les variables locales `gabarit_` ne se marchent pas dessus (noté explicitement en tâche 3).

**Point de vigilance pour l'exécutant :** la tâche 2 change la signature de `build_document` et supprime `SLIDE_WIDTH` / `SLIDE_HEIGHT` / `MAX_HEADLINE_HEIGHT` / `SCALE_STEPS`. `tests/test_render.py` doit être adapté dans la même tâche, sinon la suite est rouge entre deux commits.
