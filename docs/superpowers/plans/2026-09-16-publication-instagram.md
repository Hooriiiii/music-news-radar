# Publication Instagram automatique (chantiers B et C) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kinda publie seul deux carrousels par jour sur Instagram, à une heure tirée au hasard dans une fenêtre du matin et une de l'après-midi, choisis dans le flux scoré, avec une file de trois jours pilotable depuis le dashboard.

**Architecture:** Deux paquets neufs. `app/publish/` sait publier UN post figé : normalisation JPEG, jeton, client `graph.instagram.com`, hébergement éphémère derrière un tunnel Cloudflare. `app/schedule/` décide QUOI publier et QUAND : créneaux déterministes et arbitrage en Python pur, persistance dans une table `creneaux`, et un `runner` appelé toutes les 10 minutes par un troisième agent launchd. Chaque étape coûteuse est repoussée au dernier moment utile : composition Claude à J-1, gel (rendu + téléchargements) à T-2h, publication à l'heure dans une limite de 90 minutes de retard.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic, httpx (sync), Pillow, Playwright/Chromium (rendu existant), `cloudflared` (Homebrew), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-11-publication-meta-design.md` — lire d'abord sa **révision du 2026-09-16** (connexion Instagram, pas de Facebook, pas de reels).

## Global Constraints

- **Pas de reel.** Uniquement des publications image seule ou carrousel, gabarit `kinda_post`. Aucun code du chemin `REELS`.
- API : `https://graph.instagram.com/{meta_api_version}`, `meta_api_version = "v23.0"` (vérifié en lecture seule le 2026-09-16 : v21.0 et v23.0 répondent 200). Jeton `IG…` en paramètre `access_token`.
- Règles Meta, citées de la documentation le 2026-09-16 : **JPEG uniquement**, **8 Mo maximum**, largeur **320 à 1440 px**, proportions **entre 4:5 et 1,91:1**, carrousel **10 éléments maximum**, légende **2200 caractères**, **30 hashtags** maximum, conteneur **expiré après 24 h** s'il n'est pas publié, statuts `EXPIRED`, `ERROR`, `FINISHED`, `IN_PROGRESS`, `PUBLISHED`, médias récupérés par Meta **en cURL sur une URL publique**.
- Fenêtres (spec, fuseau `Europe/Paris`) : matin **08:30-11:00**, après-midi **17:00-19:30**. **6 créneaux** d'avance (3 jours × 2). **Composition à J-1** (24 h), **gel à T-2h**, **retard maximal 90 minutes** : au-delà, on ne publie pas.
- Python sync, pas d'async. Tests sur SQLite in-memory (`tests/conftest.py`). **Aucun test ne sort sur Internet** (le `127.0.0.1` d'un serveur lancé par le test est autorisé).
- **SQLite rend des `datetime` naïfs** pour une colonne `DateTime(timezone=True)` : toujours passer par `app.schedule.store.utc()` avant de comparer ou d'utiliser une date lue en base. Toujours écrire des dates **UTC**.
- Enums en **VARCHAR + CHECK** (`native_enum=False`), jamais d'enum natif Postgres. Dans une migration écrite à la main, noms de contraintes entourés de `op.f()`.
- Colonnes JSON (`fichiers`, `slides`, `variants`…) : **réassigner en entier**, jamais de mutation en place.
- En-têtes HTTP en **ASCII pur**.
- Binaires Homebrew (`cloudflared`) : le PATH de launchd ne contient pas `/opt/homebrew/bin`. Résoudre le binaire par `shutil.which` **puis** par une liste `EMPLACEMENTS_CONNUS`, comme `app/content/video.py`.
- Tout point d'entrée planifié appelle `attendre_base()` en premier.
- Commentaires en français. Messages de commit en français **sans accents**, terminés par ces deux lignes :
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
  ```
- `uv run ruff check .` et `uv run pytest` passent à la fin de chaque tâche.
- **Rien n'est publié pendant l'implémentation.** Le mode par défaut est `arret`. Seul `scripts/diagnostic/tester_publication.py` (tâche 6) crée des conteneurs, qui ne sont pas publics et expirent seuls en 24 h. Passer en mode `reel` est une décision de l'utilisateur.

## Écarts assumés par rapport au design, et pourquoi

1. **Hébergement éphémère au lieu d'une route FastAPI permanente.** Le design prévoyait `/public/<jeton>/<fichier>` sur le dashboard, exposé en permanence par un tunnel. Or un tunnel nommé exige un domaine chez Cloudflare, et exposer le port 8000 exposerait toute l'API (y compris `DELETE /posts`). Ici, au moment de publier, un mini-serveur local ne sert QUE le dossier du post, un tunnel rapide `trycloudflare.com` l'expose, et les deux sont fermés dès la publication terminée. Conséquence : il n'y a plus de tunnel permanent à surveiller dans `diagnostiquer()` comme le prévoyait le design. Une panne de tunnel fait échouer la publication, qui passe en `ECHEC` et envoie une notification (tâche 11).
2. **Table `creneaux` au lieu de colonnes sur `posts`.** Un créneau porte un article bien avant qu'un post soit composé (la composition n'a lieu qu'à J-1) : l'affectation ne peut pas vivre sur une ligne `posts` qui n'existe pas encore.
3. **`articles.file_exclu_raison`.** Un article dont la composition ou le gel échoue, ou que l'utilisateur « saute », ne doit pas revenir à chaque run : la raison est écrite sur l'article et l'exclut de la file.
4. **La cover 3:4 est recadrée en 4:5.** La cover `kinda_post` fait 1080×1440, hors des proportions acceptées par Meta. `app/publish/images.py` la recadre au centre en 1080×1350 (45 px en haut et en bas). Le seuil est réglable (`publication_ratio_min`) : si un essai réel montre que Meta accepte le 3:4, il suffit de le passer à 0.75.
5. **Trois modes : `arret`, `apercu`, `reel`.** Publier est public et irréversible. `apercu` fait tout, y compris créer les conteneurs chez Meta, sauf l'appel `media_publish`.
6. **Le jeton rafraîchi vit dans un fichier JSON** (`meta_token_path`, droits `600`), le `.env` ne servant que de valeur initiale : le code ne réécrit jamais `.env`.
7. **Pas de mention `@` du compte source.** Le design la prévoyait pour les posts repris de comptes Instagram ; cette source est abandonnée. La légende cite l'article d'origine (`Source : <url>`), comme l'export zip.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `app/publish/__init__.py` (créer) | Paquet vide |
| `app/publish/images.py` (créer) | Octets d'image → JPEG conforme aux règles Meta |
| `app/publish/jeton.py` (créer) | Lecture, rafraîchissement, alerte d'expiration du jeton `IG` |
| `app/publish/instagram.py` (créer) | Client HTTP : conteneurs, attente de `FINISHED`, `media_publish` |
| `app/publish/hosting.py` (créer) | Serveur local sans listing + tunnel `cloudflared` éphémère |
| `app/content/legende.py` (créer) | Texte de légende commun à l'export et à la publication |
| `app/publish/preparation.py` (créer) | Le gel : cover rendue + photos, normalisées, écrites sur disque |
| `app/publish/publier.py` (créer) | Héberger un dossier figé et le passer au client Instagram |
| `scripts/diagnostic/tester_publication.py` (créer) | Essai de bout en bout SANS `media_publish` |
| `app/models.py` (modifier) | `EtatCreneau`, `Creneau`, `Article.file_exclu_raison` |
| `alembic/versions/0014_creneaux.py` (créer) | Table `creneaux`, colonne `file_exclu_raison` |
| `app/schedule/__init__.py` (créer) | Paquet vide |
| `app/schedule/slots.py` (créer) | Une date → deux heures, tirage déterministe |
| `app/schedule/queue.py` (créer) | Arbitrage pur : quels articles dans quelles places |
| `app/schedule/store.py` (créer) | Lien avec la base : créneaux, candidats, application de l'arbitrage |
| `app/schedule/runner.py` (créer) | Un passage : manqués, arbitrage, composition, gel, publication |
| `scripts/run_publication.py` (créer) | Point d'entrée de l'agent launchd |
| `scripts/install_agents.py` (modifier) | Troisième agent `com.musicnewsradar.publication` |
| `app/api/schedule.py` (créer) | `GET /schedule`, `PATCH /schedule/{id}`, `POST /schedule/rebuild` |
| `app/schemas.py` (modifier) | `CreneauOut`, `CreneauUpdate` |
| `app/main.py` (modifier) | Inclure le routeur |
| `app/static/dashboard.html` (modifier) | Panneau « Programmation » |
| `app/config.py` (modifier, au fil des tâches) | Réglages `meta_*` et `publication_*` |
| `CLAUDE.md` (modifier, tâche 12) | Documenter la publication |

---

## Partie B — Publier un post

### Task 1: Normaliser une image pour Meta

**Files:**
- Create: `app/publish/__init__.py`
- Create: `app/publish/images.py`
- Modify: `pyproject.toml` (Pillow devient une dépendance d'exécution)
- Test: `tests/publish/test_publish_images.py`

**Interfaces:**
- Consumes: rien
- Produces: `normaliser(octets: bytes, ratio_min: float = RATIO_MIN) -> bytes` (JPEG) ; exception `ImageInexploitable(ValueError)` ; constantes `RATIO_MIN = 0.8`, `RATIO_MAX = 1.91`, `LARGEUR_MAX = 1440`, `LARGEUR_MIN = 320`, `POIDS_MAX = 8 * 1024 * 1024`

- [ ] **Step 1: Rendre Pillow disponible à l'exécution**

Pillow n'est aujourd'hui que dans le groupe de dev. Run: `uv add pillow`
Expected: `pillow` apparaît dans `[project].dependencies` de `pyproject.toml`.

- [ ] **Step 2: Write the failing test**

```python
# tests/publish/test_publish_images.py
"""Les images que Meta acceptera : JPEG, 4:5 a 1,91:1, 1440 px de large au plus."""

import io

import pytest
from PIL import Image

from app.publish.images import ImageInexploitable, normaliser


def _image(largeur, hauteur, mode="RGB", format_="PNG"):
    couleur = (255, 0, 128, 0) if mode == "RGBA" else (255, 0, 128)
    tampon = io.BytesIO()
    Image.new(mode, (largeur, hauteur), couleur).save(tampon, format_)
    return tampon.getvalue()


def _dimensions(octets):
    image = Image.open(io.BytesIO(octets))
    return image.format, image.size, image.mode


def test_la_cover_3_4_est_recadree_en_4_5():
    """La cover kinda_post fait 1080x1440 (3:4), hors des proportions Meta."""
    assert _dimensions(normaliser(_image(1080, 1440))) == ("JPEG", (1080, 1350), "RGB")


def test_une_image_carree_reste_intacte():
    assert _dimensions(normaliser(_image(1080, 1080)))[1] == (1080, 1080)


def test_un_panoramique_est_recadre_a_1_91():
    assert _dimensions(normaliser(_image(2000, 500)))[1] == (955, 500)


def test_une_image_trop_large_est_reduite_a_1440():
    assert _dimensions(normaliser(_image(3000, 2000)))[1] == (1440, 960)


def test_un_png_transparent_devient_un_jpeg_rgb():
    format_, _, mode = _dimensions(normaliser(_image(1080, 1080, mode="RGBA")))
    assert (format_, mode) == ("JPEG", "RGB")


def test_le_seuil_de_proportion_est_reglable():
    """Si Meta accepte un jour le 3:4, publication_ratio_min passe a 0.75."""
    assert _dimensions(normaliser(_image(1080, 1440), ratio_min=0.75))[1] == (1080, 1440)


def test_des_octets_qui_ne_sont_pas_une_image_sont_refuses():
    with pytest.raises(ImageInexploitable):
        normaliser(b"<html>pas une image</html>")


def test_une_image_trop_petite_est_refusee():
    with pytest.raises(ImageInexploitable):
        normaliser(_image(100, 100))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/publish/test_publish_images.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.publish'`

- [ ] **Step 4: Write minimal implementation**

`app/publish/__init__.py` : fichier vide.

```python
# app/publish/images.py
"""Mettre une image aux regles de l'API de publication Instagram.

Regles citees de la documentation Meta le 2026-09-16 : JPEG uniquement, 8 Mo au
plus, largeur de 320 a 1440 px, proportions entre 4:5 et 1,91:1. Une image hors
de ces bornes fait echouer le conteneur -- et seulement au moment de publier,
c'est-a-dire trop tard. D'ou une normalisation systematique au gel.

La cover `kinda_post` (1080x1440, soit 3:4) est elle-meme hors bornes : elle est
recadree AU CENTRE en 1080x1350. 45 px partent en haut et en bas ; le logo (87 px
du haut) et le bloc d'accroche (76 px du bas) restent dans l'image.
"""

import io

from PIL import Image, ImageOps

RATIO_MIN = 4 / 5
RATIO_MAX = 1.91
LARGEUR_MIN = 320
LARGEUR_MAX = 1440
POIDS_MAX = 8 * 1024 * 1024
# Qualites essayees dans l'ordre, jusqu'a passer sous les 8 Mo.
QUALITES = (92, 85, 75, 65)
# Fond des images transparentes : le noir de la charte Kinda.
FOND = (0x1D, 0x1D, 0x1D)


class ImageInexploitable(ValueError):
    """L'image ne peut pas etre mise aux regles de Meta. Message affichable."""


def normaliser(octets: bytes, ratio_min: float = RATIO_MIN) -> bytes:
    try:
        image = Image.open(io.BytesIO(octets))
        image.load()
    except Exception as exc:  # noqa: BLE001 -- Pillow leve des types tres varies
        raise ImageInexploitable(f"image illisible : {exc}") from exc

    # Une photo de telephone porte souvent son orientation en EXIF : sans ca,
    # elle partirait couchee.
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        rgba = image.convert("RGBA")
        fond = Image.new("RGB", rgba.size, FOND)
        fond.paste(rgba, mask=rgba.getchannel("A"))
        image = fond

    largeur, hauteur = image.size
    if largeur < LARGEUR_MIN:
        raise ImageInexploitable(f"trop petite : {largeur} px de large, minimum {LARGEUR_MIN}")

    ratio = largeur / hauteur
    if ratio < ratio_min:
        # int() et pas round() : arrondir vers le haut donnerait une hauteur a
        # peine trop grande, donc un ratio a peine sous le minimum.
        nouvelle_hauteur = int(largeur / ratio_min)
        haut = (hauteur - nouvelle_hauteur) // 2
        image = image.crop((0, haut, largeur, haut + nouvelle_hauteur))
    elif ratio > RATIO_MAX:
        nouvelle_largeur = int(hauteur * RATIO_MAX)
        gauche = (largeur - nouvelle_largeur) // 2
        image = image.crop((gauche, 0, gauche + nouvelle_largeur, hauteur))

    if image.width > LARGEUR_MAX:
        nouvelle_hauteur = round(image.height * LARGEUR_MAX / image.width)
        image = image.resize((LARGEUR_MAX, nouvelle_hauteur), Image.LANCZOS)

    for qualite in QUALITES:
        tampon = io.BytesIO()
        image.save(tampon, "JPEG", quality=qualite, optimize=True)
        if tampon.tell() <= POIDS_MAX:
            return tampon.getvalue()
    raise ImageInexploitable("plus de 8 Mo meme compressee")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/publish/test_publish_images.py -v`
Expected: 8 passed

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock app/publish/__init__.py app/publish/images.py tests/publish/test_publish_images.py
git commit -F - <<'EOF'
feat(publication): normaliser les images aux regles de Meta

JPEG uniquement, 8 Mo, 320 a 1440 px de large, proportions 4:5 a 1,91:1. La
cover kinda_post (3:4) est recadree au centre en 1080x1350.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 2: Le jeton Instagram et son rafraîchissement

**Files:**
- Create: `app/publish/jeton.py`
- Modify: `app/config.py` (bloc `meta_*`, juste avant `# Livraison (étape 5)`)
- Modify: `.env.example`
- Test: `tests/publish/test_publish_jeton.py`

**Interfaces:**
- Consumes: `settings.meta_access_token`, `settings.meta_token_path`
- Produces:
  - `@dataclass EtatJeton(access_token: str, rafraichi_le: datetime | None = None, expire_le: datetime | None = None, echec_le: datetime | None = None, alerte_envoyee_le: date | None = None)`
  - `JetonIndisponible(RuntimeError)`
  - `charger(chemin: Path) -> EtatJeton`
  - `enregistrer(chemin: Path, etat: EtatJeton) -> None`
  - `rafraichir_si_necessaire(chemin: Path, maintenant: datetime, http=httpx) -> tuple[EtatJeton, str | None]` — le second élément est le message d'erreur du rafraîchissement, ou `None`
  - `alerte_expiration(etat: EtatJeton, maintenant: datetime) -> str | None`

- [ ] **Step 1: Ajouter la configuration**

Dans `app/config.py`, juste avant la ligne `    # Livraison (étape 5)` :

```python
    # --- Instagram (connexion Instagram, sans Facebook) ---
    # App Meta « Kinda Radar », compte kindanewsfrance declare testeur. Le jeton
    # du .env n'est que la valeur INITIALE : une fois rafraichi, le jeton courant
    # vit dans `meta_token_path` -- le code ne reecrit jamais .env.
    meta_access_token: str | None = None
    meta_ig_user_id: str | None = None
    # Verifie en lecture seule le 2026-09-16 : v21.0 et v23.0 repondent.
    meta_api_version: str = "v23.0"
    meta_token_path: Path = Path.home() / "Library/Application Support/Kinda/instagram_token.json"

```

Et en tête du fichier : `from pathlib import Path`.

Dans `.env.example`, ajouter :

```
# Instagram : jeton IG... (60 jours) et identifiant du compte, depuis l'app Meta
META_ACCESS_TOKEN=
META_IG_USER_ID=
```

- [ ] **Step 2: Write the failing test**

```python
# tests/publish/test_publish_jeton.py
"""Le jeton IG expire a 60 jours : il se rafraichit, et il previent avant de mourir."""

import datetime as dt
import json

import httpx
import pytest

from app.config import settings
from app.publish.jeton import (
    EtatJeton,
    JetonIndisponible,
    alerte_expiration,
    charger,
    enregistrer,
    rafraichir_si_necessaire,
)

MAINTENANT = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.timezone.utc)


class FauxHttp:
    def __init__(self, reponse=None):
        self.reponse = reponse
        self.appels = []

    def get(self, url, params=None, timeout=None):
        self.appels.append((url, params))
        if self.reponse is None:
            raise AssertionError("aucun appel reseau n'etait attendu")
        return self.reponse


def test_sans_fichier_le_jeton_vient_du_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", " IGAAinitial ")
    assert charger(tmp_path / "jeton.json").access_token == "IGAAinitial"


def test_sans_fichier_ni_env_l_erreur_dit_quoi_faire(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", None)
    with pytest.raises(JetonIndisponible, match="META_ACCESS_TOKEN"):
        charger(tmp_path / "jeton.json")


def test_aller_retour_disque_et_droits_restreints(tmp_path):
    chemin = tmp_path / "sous" / "jeton.json"
    etat = EtatJeton("IGAAx", rafraichi_le=MAINTENANT, expire_le=MAINTENANT + dt.timedelta(days=60))
    enregistrer(chemin, etat)
    assert charger(chemin) == etat
    assert chemin.stat().st_mode & 0o777 == 0o600


def test_un_jeton_rafraichi_recemment_ne_declenche_aucun_appel(tmp_path):
    chemin = tmp_path / "jeton.json"
    enregistrer(chemin, EtatJeton("IGAAx", rafraichi_le=MAINTENANT - dt.timedelta(days=2)))
    etat, erreur = rafraichir_si_necessaire(chemin, MAINTENANT, http=FauxHttp())
    assert (etat.access_token, erreur) == ("IGAAx", None)


def test_un_jeton_jamais_rafraichi_est_rafraichi_et_enregistre(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "IGAAancien")
    chemin = tmp_path / "jeton.json"
    http = FauxHttp(httpx.Response(200, json={
        "access_token": "IGAAneuf", "token_type": "bearer", "expires_in": 5184000,
    }))

    etat, erreur = rafraichir_si_necessaire(chemin, MAINTENANT, http=http)

    assert erreur is None
    assert etat.access_token == "IGAAneuf"
    assert etat.expire_le == MAINTENANT + dt.timedelta(days=60)
    url, params = http.appels[0]
    assert url == "https://graph.instagram.com/refresh_access_token"
    assert params == {"grant_type": "ig_refresh_token", "access_token": "IGAAancien"}
    assert json.loads(chemin.read_text())["access_token"] == "IGAAneuf"


def test_un_refus_garde_l_ancien_jeton_et_rend_le_message(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "meta_access_token", "IGAAancien")
    chemin = tmp_path / "jeton.json"
    http = FauxHttp(httpx.Response(400, json={"error": {"message": "Session has expired"}}))

    etat, erreur = rafraichir_si_necessaire(chemin, MAINTENANT, http=http)

    assert etat.access_token == "IGAAancien"
    assert "Session has expired" in erreur
    assert charger(chemin).echec_le == MAINTENANT


def test_apres_un_echec_on_ne_reessaie_pas_avant_six_heures(tmp_path):
    """L'agent tourne toutes les 10 minutes : sans ce delai, un jeton mort
    produirait un appel refuse toutes les 10 minutes."""
    chemin = tmp_path / "jeton.json"
    enregistrer(chemin, EtatJeton("IGAAx", echec_le=MAINTENANT - dt.timedelta(hours=1)))
    etat, erreur = rafraichir_si_necessaire(chemin, MAINTENANT, http=FauxHttp())
    assert etat.access_token == "IGAAx"
    assert erreur is None


def test_l_alerte_se_declenche_a_sept_jours_de_l_expiration():
    loin = EtatJeton("x", expire_le=MAINTENANT + dt.timedelta(days=10))
    proche = EtatJeton("x", expire_le=MAINTENANT + dt.timedelta(days=5))
    assert alerte_expiration(loin, MAINTENANT) is None
    assert "5 jour" in alerte_expiration(proche, MAINTENANT)
    assert alerte_expiration(EtatJeton("x"), MAINTENANT) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/publish/test_publish_jeton.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.publish.jeton'`

- [ ] **Step 4: Write minimal implementation**

```python
# app/publish/jeton.py
"""Le jeton Instagram : le lire, le rafraichir, prevenir avant qu'il meure.

Un jeton `IG` longue duree vit 60 jours. Il se prolonge par
`GET graph.instagram.com/refresh_access_token`, possible seulement pour un jeton
age d'au moins 24 h et non expire. S'il meurt, la publication s'arrete -- en
silence si rien ne previent, comme l'agent digest pendant cinq semaines. D'ou le
rafraichissement hebdomadaire et l'alerte a J-7.

Le jeton du `.env` n'est que la valeur INITIALE. Le jeton courant est ecrit dans
un fichier JSON en droits 600 : le code ne reecrit jamais `.env`.
"""

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings

URL_RAFRAICHISSEMENT = "https://graph.instagram.com/refresh_access_token"
INTERVALLE_RAFRAICHISSEMENT = dt.timedelta(days=7)
# L'agent tourne toutes les 10 minutes : sans delai apres un echec, un jeton
# mort produirait un appel refuse toutes les 10 minutes.
DELAI_APRES_ECHEC = dt.timedelta(hours=6)
SEUIL_ALERTE = dt.timedelta(days=7)


class JetonIndisponible(RuntimeError):
    """Aucun jeton du tout. Message affichable."""


@dataclass
class EtatJeton:
    access_token: str
    rafraichi_le: dt.datetime | None = None
    expire_le: dt.datetime | None = None
    echec_le: dt.datetime | None = None
    alerte_envoyee_le: dt.date | None = None


def _date_heure(valeur: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(valeur) if valeur else None


def charger(chemin: Path) -> EtatJeton:
    if chemin.exists():
        donnees = json.loads(chemin.read_text())
        alerte = donnees.get("alerte_envoyee_le")
        return EtatJeton(
            access_token=donnees["access_token"],
            rafraichi_le=_date_heure(donnees.get("rafraichi_le")),
            expire_le=_date_heure(donnees.get("expire_le")),
            echec_le=_date_heure(donnees.get("echec_le")),
            alerte_envoyee_le=dt.date.fromisoformat(alerte) if alerte else None,
        )
    if not settings.meta_access_token:
        raise JetonIndisponible(
            "META_ACCESS_TOKEN manquant dans .env : generer un jeton dans l'app Meta "
            "« Kinda Radar », API Instagram, etape « Generer des tokens d'acces »"
        )
    return EtatJeton(access_token=settings.meta_access_token.strip())


def enregistrer(chemin: Path, etat: EtatJeton) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    donnees = {
        "access_token": etat.access_token,
        "rafraichi_le": etat.rafraichi_le.isoformat() if etat.rafraichi_le else None,
        "expire_le": etat.expire_le.isoformat() if etat.expire_le else None,
        "echec_le": etat.echec_le.isoformat() if etat.echec_le else None,
        "alerte_envoyee_le": etat.alerte_envoyee_le.isoformat() if etat.alerte_envoyee_le else None,
    }
    chemin.write_text(json.dumps(donnees, indent=2))
    # Un jeton de publication donne la main sur le compte : lisible par son seul
    # proprietaire.
    os.chmod(chemin, 0o600)


def rafraichir_si_necessaire(
    chemin: Path, maintenant: dt.datetime, http=httpx
) -> tuple[EtatJeton, str | None]:
    etat = charger(chemin)
    if etat.rafraichi_le and maintenant - etat.rafraichi_le < INTERVALLE_RAFRAICHISSEMENT:
        return etat, None
    if etat.echec_le and maintenant - etat.echec_le < DELAI_APRES_ECHEC:
        return etat, None

    try:
        reponse = http.get(
            URL_RAFRAICHISSEMENT,
            params={"grant_type": "ig_refresh_token", "access_token": etat.access_token},
            timeout=30,
        )
        donnees = reponse.json()
    except Exception as exc:  # noqa: BLE001 -- reseau ou JSON : meme traitement
        donnees, reponse = {"error": {"message": str(exc)}}, None

    if reponse is None or reponse.status_code >= 400 or "access_token" not in donnees:
        message = (donnees.get("error") or {}).get("message") or "reponse inattendue"
        etat.echec_le = maintenant
        enregistrer(chemin, etat)
        return etat, f"rafraichissement du jeton Instagram refuse : {message}"

    etat = EtatJeton(
        access_token=donnees["access_token"],
        rafraichi_le=maintenant,
        expire_le=maintenant + dt.timedelta(seconds=int(donnees.get("expires_in", 0))),
        alerte_envoyee_le=etat.alerte_envoyee_le,
    )
    enregistrer(chemin, etat)
    return etat, None


def alerte_expiration(etat: EtatJeton, maintenant: dt.datetime) -> str | None:
    if etat.expire_le is None:
        return None
    reste = etat.expire_le - maintenant
    if reste > SEUIL_ALERTE:
        return None
    return f"jeton Instagram : expire dans {max(reste.days, 0)} jour(s)"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/publish/test_publish_jeton.py -v`
Expected: 8 passed

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/config.py .env.example app/publish/jeton.py tests/publish/test_publish_jeton.py
git commit -F - <<'EOF'
feat(publication): lire, rafraichir et surveiller le jeton Instagram

Le jeton IG vit 60 jours. Rafraichissement hebdomadaire, pas de nouvel essai
avant six heures apres un echec, alerte a J-7. Le jeton courant vit dans un
fichier en droits 600 ; .env n'est que la valeur initiale.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 3: Le client Instagram

**Files:**
- Create: `app/publish/instagram.py`
- Test: `tests/publish/test_publish_instagram.py`

**Interfaces:**
- Consumes: `settings.meta_api_version` (tâche 2)
- Produces:
  - `ErreurInstagram(RuntimeError)`
  - `ClientInstagram(jeton: str, ig_user_id: str, *, http=None, dormir=time.sleep, version: str | None = None)`
  - `ClientInstagram.preparer_carrousel(urls: list[str], legende: str) -> str` — crée le conteneur (image seule si une URL, carrousel sinon), attend `FINISHED`, rend l'identifiant du conteneur. **Ne publie pas.**
  - `ClientInstagram.publier(conteneur_id: str) -> str` — `media_publish`, rend l'identifiant du média publié
  - constantes `CARROUSEL_MAX = 10`, `ATTENTE_PAS_S = 3.0`, `ATTENTE_MAX_S = 300.0`

- [ ] **Step 1: Write the failing test**

```python
# tests/publish/test_publish_instagram.py
"""Le client Instagram, sur un faux HTTP : aucun appel ne sort."""

import httpx
import pytest

import app.publish.instagram as mod
from app.publish.instagram import ClientInstagram, ErreurInstagram

BASE = "https://graph.instagram.com/v23.0"


class FauxHttp:
    """Rejoue des reponses selon (methode, chemin), et consigne les appels."""

    def __init__(self, statuts=("FINISHED",)):
        self.appels = []
        self.statuts = list(statuts)
        self.compteur = 0

    def request(self, methode, url, params=None):
        self.appels.append((methode, url.removeprefix(BASE + "/"), dict(params)))
        chemin = url.removeprefix(BASE + "/")
        if methode == "POST" and chemin.endswith("/media"):
            self.compteur += 1
            return httpx.Response(200, json={"id": f"c{self.compteur}"})
        if methode == "POST" and chemin.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "media999"})
        if methode == "GET":
            statut = self.statuts.pop(0) if len(self.statuts) > 1 else self.statuts[0]
            return httpx.Response(200, json={"status_code": statut, "status": "detail"})
        raise AssertionError(f"appel inattendu {methode} {url}")


def _client(http, **kwargs):
    return ClientInstagram("IGAAjeton", "17841", http=http, version="v23.0", dormir=lambda s: None, **kwargs)


def test_une_seule_image_donne_un_conteneur_image_avec_legende():
    http = FauxHttp()
    assert _client(http).preparer_carrousel(["https://t/a.jpg"], "Legende") == "c1"
    methode, chemin, params = http.appels[0]
    assert (methode, chemin) == ("POST", "17841/media")
    assert params["image_url"] == "https://t/a.jpg"
    assert params["caption"] == "Legende"
    assert params["access_token"] == "IGAAjeton"
    assert not any(c.endswith("media_publish") for _, c, _ in http.appels), "preparer ne publie pas"


def test_plusieurs_images_donnent_un_carrousel():
    http = FauxHttp()
    conteneur = _client(http).preparer_carrousel(["https://t/1.jpg", "https://t/2.jpg", "https://t/3.jpg"], "L")
    posts = [p for m, c, p in http.appels if m == "POST"]
    assert all(p.get("is_carousel_item") == "true" for p in posts[:3])
    assert posts[3]["media_type"] == "CAROUSEL"
    assert posts[3]["children"] == "c1,c2,c3"
    assert posts[3]["caption"] == "L"
    assert conteneur == "c4"


def test_au_dela_de_dix_images_on_tronque():
    http = FauxHttp()
    _client(http).preparer_carrousel([f"https://t/{i}.jpg" for i in range(12)], "L")
    enfants = [p for m, c, p in http.appels if m == "POST" and p.get("is_carousel_item")]
    assert len(enfants) == 10


def test_on_attend_finished_avant_de_rendre_la_main():
    """Publier un conteneur IN_PROGRESS echoue : le sondage n'est pas optionnel."""
    pauses = []
    http = FauxHttp(statuts=("IN_PROGRESS", "IN_PROGRESS", "FINISHED"))
    ClientInstagram("j", "17841", http=http, version="v23.0", dormir=pauses.append).preparer_carrousel(
        ["https://t/a.jpg"], "L"
    )
    assert len(pauses) == 2


def test_un_conteneur_en_erreur_leve():
    with pytest.raises(ErreurInstagram, match="ERROR"):
        _client(FauxHttp(statuts=("ERROR",))).preparer_carrousel(["https://t/a.jpg"], "L")


def test_un_conteneur_qui_ne_finit_jamais_leve(monkeypatch):
    monkeypatch.setattr(mod, "ATTENTE_MAX_S", 6.0)
    with pytest.raises(ErreurInstagram, match="IN_PROGRESS"):
        _client(FauxHttp(statuts=("IN_PROGRESS",))).preparer_carrousel(["https://t/a.jpg"], "L")


def test_le_message_d_erreur_de_meta_remonte():
    class HttpRefus(FauxHttp):
        def request(self, methode, url, params=None):
            return httpx.Response(400, json={"error": {
                "message": "Invalid parameter", "error_user_msg": "Format d'image non pris en charge",
            }})

    with pytest.raises(ErreurInstagram, match="Format d'image non pris en charge"):
        _client(HttpRefus()).preparer_carrousel(["https://t/a.png"], "L")


def test_publier_appelle_media_publish():
    http = FauxHttp()
    assert _client(http).publier("c4") == "media999"
    assert http.appels[-1] == ("POST", "17841/media_publish", {"creation_id": "c4", "access_token": "IGAAjeton"})


def test_aucune_image_est_une_erreur():
    with pytest.raises(ErreurInstagram, match="aucune image"):
        _client(FauxHttp()).preparer_carrousel([], "L")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/publish/test_publish_instagram.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.publish.instagram'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/publish/instagram.py
"""Publier sur Instagram par la connexion Instagram : conteneur, attente, publication.

Trois temps, toujours les memes : creer un conteneur (Meta telecharge les images
depuis leurs URL publiques), attendre qu'il passe a FINISHED, puis appeler
`media_publish`. Le deuxieme temps n'est pas optionnel : publier un conteneur
IN_PROGRESS echoue.

`preparer_carrousel` s'arrete AVANT la publication. C'est ce qui permet le mode
`apercu` et le script d'essai : un conteneur non publie n'est pas public et
expire seul en 24 h.
"""

import time

import httpx

from app.config import settings

BASE = "https://graph.instagram.com"
CARROUSEL_MAX = 10
ATTENTE_PAS_S = 3.0
ATTENTE_MAX_S = 300.0


class ErreurInstagram(RuntimeError):
    """Refus ou echec cote Meta. Message affichable."""


class ClientInstagram:
    def __init__(
        self,
        jeton: str,
        ig_user_id: str,
        *,
        http=None,
        dormir=time.sleep,
        version: str | None = None,
    ):
        self.jeton = jeton
        self.ig_user_id = ig_user_id
        self.http = http or httpx.Client(timeout=60)
        self.dormir = dormir
        self.base = f"{BASE}/{version or settings.meta_api_version}"

    def _appel(self, methode: str, chemin: str, params: dict) -> dict:
        reponse = self.http.request(
            methode, f"{self.base}/{chemin}", params={**params, "access_token": self.jeton}
        )
        try:
            donnees = reponse.json()
        except ValueError as exc:
            raise ErreurInstagram(f"{chemin} : reponse illisible (HTTP {reponse.status_code})") from exc
        if reponse.status_code >= 400 or "error" in donnees:
            erreur = donnees.get("error") or {}
            # error_user_msg est le texte destine a un humain quand Meta le fournit.
            detail = erreur.get("error_user_msg") or erreur.get("message") or f"HTTP {reponse.status_code}"
            raise ErreurInstagram(f"{chemin} : {detail}")
        return donnees

    def _conteneur(self, **params) -> str:
        return self._appel("POST", f"{self.ig_user_id}/media", params)["id"]

    def _attendre(self, conteneur_id: str) -> None:
        ecoule = 0.0
        while True:
            statut = self._appel("GET", conteneur_id, {"fields": "status_code,status"})
            code = statut.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise ErreurInstagram(f"conteneur {conteneur_id} en {code} : {statut.get('status', '')}")
            if ecoule >= ATTENTE_MAX_S:
                raise ErreurInstagram(
                    f"conteneur {conteneur_id} toujours {code} apres {ATTENTE_MAX_S:.0f} s"
                )
            self.dormir(ATTENTE_PAS_S)
            ecoule += ATTENTE_PAS_S

    def preparer_carrousel(self, urls: list[str], legende: str) -> str:
        if not urls:
            raise ErreurInstagram("aucune image a publier")
        urls = urls[:CARROUSEL_MAX]
        if len(urls) == 1:
            conteneur = self._conteneur(image_url=urls[0], caption=legende)
        else:
            enfants = [self._conteneur(image_url=url, is_carousel_item="true") for url in urls]
            conteneur = self._conteneur(
                media_type="CAROUSEL", children=",".join(enfants), caption=legende
            )
        self._attendre(conteneur)
        return conteneur

    def publier(self, conteneur_id: str) -> str:
        return self._appel("POST", f"{self.ig_user_id}/media_publish", {"creation_id": conteneur_id})["id"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/publish/test_publish_instagram.py -v`
Expected: 9 passed

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add app/publish/instagram.py tests/publish/test_publish_instagram.py
git commit -F - <<'EOF'
feat(publication): client Instagram, conteneurs puis publication

Image seule ou carrousel de 10 au plus, attente obligatoire de FINISHED, et
publication separee de la preparation pour permettre un essai sans publier.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 4: Héberger les fichiers le temps d'une publication

**Files:**
- Create: `app/publish/hosting.py`
- Test: `tests/publish/test_publish_hosting.py`

**Interfaces:**
- Consumes: rien
- Produces:
  - `HebergementIndisponible(RuntimeError)`
  - `chemin_cloudflared() -> str`
  - `extraire_url(lignes) -> str | None`
  - `serveur_local(dossier: Path)` — context manager, rend le port
  - `tunnel(port: int, *, lancer=subprocess.Popen)` — context manager, rend l'URL `https://….trycloudflare.com`
  - `attendre_joignable(url: str, *, get=httpx.get, dormir=time.sleep, delai: float = DELAI_JOIGNABLE_S) -> None`
  - `heberger(dossier: Path, temoin: str)` — context manager, rend l'URL publique de base ; `temoin` est un nom de fichier du dossier servant à vérifier que le tunnel répond
  - constante `EMPLACEMENTS_CONNUS = ("/opt/homebrew/bin", "/usr/local/bin")`

- [ ] **Step 1: Write the failing test**

```python
# tests/publish/test_publish_hosting.py
"""L'hebergement ephemere, sans jamais lancer le vrai cloudflared."""

import contextlib

import httpx
import pytest

import app.publish.hosting as mod
from app.publish.hosting import (
    HebergementIndisponible,
    attendre_joignable,
    chemin_cloudflared,
    extraire_url,
    serveur_local,
    tunnel,
)

LIGNES_CLOUDFLARED = [
    "2026-09-16T10:00:00Z INF Thank you for trying Cloudflare Tunnel.\n",
    "2026-09-16T10:00:01Z INF +----------------------------------------------------+\n",
    "2026-09-16T10:00:01Z INF |  https://calm-river-wave-bold.trycloudflare.com    |\n",
]


class FauxProcessus:
    def __init__(self, lignes, code=None):
        self.stdout = iter(lignes)
        self.code = code
        self.termine = False

    def poll(self):
        return self.code

    def terminate(self):
        self.termine = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.termine = True


def test_extraire_url_trouve_l_adresse_du_tunnel():
    assert extraire_url(LIGNES_CLOUDFLARED) == "https://calm-river-wave-bold.trycloudflare.com"
    assert extraire_url(["rien a voir\n"]) is None


def test_le_serveur_local_sert_les_fichiers_du_dossier(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"JPEG")
    with serveur_local(tmp_path) as port:
        reponse = httpx.get(f"http://127.0.0.1:{port}/a.jpg")
    assert (reponse.status_code, reponse.content) == (200, b"JPEG")
    assert reponse.headers["content-type"] == "image/jpeg"


def test_le_serveur_local_ne_liste_pas_le_dossier(tmp_path):
    """Le tunnel est public pendant quelques minutes : rien a lister."""
    (tmp_path / "a.jpg").write_bytes(b"x")
    with serveur_local(tmp_path) as port:
        assert httpx.get(f"http://127.0.0.1:{port}/").status_code == 404


def test_le_serveur_local_ne_sert_rien_hors_du_dossier(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    with serveur_local(public) as port:
        assert httpx.get(f"http://127.0.0.1:{port}/secret.txt").status_code == 404


def test_cloudflared_est_trouve_hors_du_path_de_launchd(tmp_path, monkeypatch):
    (tmp_path / "cloudflared").write_text("")
    monkeypatch.setattr(mod.shutil, "which", lambda nom: None)
    monkeypatch.setattr(mod, "EMPLACEMENTS_CONNUS", (str(tmp_path),))
    assert chemin_cloudflared() == str(tmp_path / "cloudflared")


def test_cloudflared_absent_dit_comment_l_installer(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda nom: None)
    monkeypatch.setattr(mod, "EMPLACEMENTS_CONNUS", ())
    with pytest.raises(HebergementIndisponible, match="brew install cloudflared"):
        chemin_cloudflared()


def test_le_tunnel_rend_son_url_puis_se_ferme(monkeypatch):
    monkeypatch.setattr(mod, "chemin_cloudflared", lambda: "/bin/cloudflared")
    processus = FauxProcessus(LIGNES_CLOUDFLARED)
    lances = []

    def lancer(commande, **kwargs):
        lances.append(commande)
        return processus

    with tunnel(8123, lancer=lancer) as url:
        assert url == "https://calm-river-wave-bold.trycloudflare.com"
        assert not processus.termine
    assert processus.termine
    assert lances == [["/bin/cloudflared", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8123"]]


def test_un_cloudflared_qui_s_arrete_sans_url_leve_et_se_ferme(monkeypatch):
    monkeypatch.setattr(mod, "chemin_cloudflared", lambda: "/bin/cloudflared")
    processus = FauxProcessus([], code=1)
    with pytest.raises(HebergementIndisponible, match="arrete"):
        with tunnel(8123, lancer=lambda commande, **kwargs: processus):
            pass
    assert processus.termine


def test_attendre_joignable_patiente_le_temps_que_le_dns_suive():
    reponses = [httpx.ConnectError("dns"), httpx.Response(404), httpx.Response(200)]

    def get(url, timeout=None):
        reponse = reponses.pop(0)
        if isinstance(reponse, Exception):
            raise reponse
        return reponse

    pauses = []
    attendre_joignable("https://x.trycloudflare.com/a.jpg", get=get, dormir=pauses.append)
    assert len(pauses) == 2


def test_attendre_joignable_abandonne_apres_le_delai():
    with pytest.raises(HebergementIndisponible, match="injoignable"):
        attendre_joignable(
            "https://x.trycloudflare.com/a.jpg",
            get=lambda url, timeout=None: httpx.Response(502),
            dormir=lambda s: None,
            delai=9,
        )


def test_heberger_combine_serveur_tunnel_et_verification(tmp_path, monkeypatch):
    (tmp_path / "a.jpg").write_bytes(b"x")
    ports = []

    @contextlib.contextmanager
    def faux_tunnel(port):
        ports.append(port)
        yield "https://t.trycloudflare.com"

    verifies = []
    monkeypatch.setattr(mod, "tunnel", faux_tunnel)
    monkeypatch.setattr(mod, "attendre_joignable", lambda url, **kwargs: verifies.append(url))

    with mod.heberger(tmp_path, "a.jpg") as url:
        assert url == "https://t.trycloudflare.com"
    assert verifies == ["https://t.trycloudflare.com/a.jpg"]
    assert ports[0] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/publish/test_publish_hosting.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.publish.hosting'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/publish/hosting.py
"""Exposer, le temps d'une publication, les fichiers d'un post sur une URL publique.

Meta telecharge lui-meme les images en cURL : il leur faut une URL publique. On
n'expose PAS le dashboard (le port 8000 porte toute l'API, y compris
`DELETE /posts`) et on n'ouvre rien en permanence. Au moment de publier :

1. un mini-serveur local ne sert QUE le dossier du post, sans listing ;
2. un tunnel rapide `cloudflared` (sous-domaine aleatoire de trycloudflare.com,
   sans compte ni domaine) l'expose ;
3. on attend que l'URL reponde vraiment (le DNS du sous-domaine suit avec
   quelques secondes de retard) ;
4. tout est ferme des la publication terminee.
"""

import contextlib
import functools
import http.server
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx

# launchd ne fournit qu'un PATH minimal, sans /opt/homebrew/bin : `which` y
# echoue alors que cloudflared est installe. Meme piege que ffmpeg dans
# app/content/video.py.
EMPLACEMENTS_CONNUS = ("/opt/homebrew/bin", "/usr/local/bin")
_URL_TUNNEL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
DELAI_URL_S = 45.0
DELAI_JOIGNABLE_S = 90.0
PAS_JOIGNABLE_S = 3.0


class HebergementIndisponible(RuntimeError):
    """cloudflared absent, tunnel non ouvert ou injoignable. Message affichable."""


def chemin_cloudflared() -> str:
    chemin = shutil.which("cloudflared")
    if chemin is None:
        chemin = next(
            (str(Path(d) / "cloudflared") for d in EMPLACEMENTS_CONNUS if (Path(d) / "cloudflared").exists()),
            None,
        )
    if chemin is None:
        raise HebergementIndisponible(
            "cloudflared est introuvable. La publication en a besoin pour exposer les "
            "images a Meta : `brew install cloudflared`."
        )
    return chemin


class _SansListe(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(404)
        return None

    def log_message(self, format, *args):  # noqa: A002 -- signature imposee
        pass


@contextlib.contextmanager
def serveur_local(dossier: Path):
    gestionnaire = functools.partial(_SansListe, directory=str(dossier))
    # 127.0.0.1 et port 0 : jamais expose au reseau local, port libre choisi par le systeme.
    serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), gestionnaire)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    try:
        yield serveur.server_address[1]
    finally:
        serveur.shutdown()
        serveur.server_close()


def extraire_url(lignes) -> str | None:
    for ligne in lignes:
        trouve = _URL_TUNNEL.search(ligne)
        if trouve:
            return trouve.group(0)
    return None


def _lire_url(processus, delai: float = DELAI_URL_S) -> str:
    # Lecture dans un fil : lire stdout directement bloquerait sans limite de temps.
    lignes: queue.Queue = queue.Queue()

    def lire():
        for ligne in processus.stdout:
            lignes.put(ligne)

    threading.Thread(target=lire, daemon=True).start()
    limite = time.monotonic() + delai
    while time.monotonic() < limite:
        try:
            ligne = lignes.get(timeout=0.5)
        except queue.Empty:
            if processus.poll() is not None:
                raise HebergementIndisponible("cloudflared s'est arrete avant d'ouvrir le tunnel") from None
            continue
        url = extraire_url([ligne])
        if url:
            return url
    raise HebergementIndisponible(f"cloudflared n'a pas fourni d'URL en {delai:.0f} s")


@contextlib.contextmanager
def tunnel(port: int, *, lancer=subprocess.Popen):
    processus = lancer(
        [chemin_cloudflared(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        yield _lire_url(processus)
    finally:
        processus.terminate()
        try:
            processus.wait(timeout=10)
        except subprocess.TimeoutExpired:
            processus.kill()


def attendre_joignable(
    url: str, *, get=httpx.get, dormir=time.sleep, delai: float = DELAI_JOIGNABLE_S
) -> None:
    ecoule = 0.0
    while ecoule < delai:
        try:
            if get(url, timeout=10).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        dormir(PAS_JOIGNABLE_S)
        ecoule += PAS_JOIGNABLE_S
    raise HebergementIndisponible(f"tunnel ouvert mais {url} injoignable apres {delai:.0f} s")


@contextlib.contextmanager
def heberger(dossier: Path, temoin: str):
    with serveur_local(dossier) as port, tunnel(port) as url:
        attendre_joignable(f"{url}/{temoin}")
        yield url
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/publish/test_publish_hosting.py -v`
Expected: 11 passed

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add app/publish/hosting.py tests/publish/test_publish_hosting.py
git commit -F - <<'EOF'
feat(publication): heberger les images le temps d'une publication

Un mini-serveur local sans listing, expose par un tunnel cloudflared rapide,
ferme des la publication terminee. Le dashboard n'est jamais expose.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 5: La légende et le gel sur disque

**Files:**
- Create: `app/content/legende.py`
- Modify: `app/api/posts.py` (remplacer `_texte_legende` par `texte_legende`)
- Create: `app/publish/preparation.py`
- Modify: `app/config.py` (bloc `publication_*`, à la suite du bloc `meta_*`)
- Test: `tests/content/test_legende.py`, `tests/publish/test_publish_preparation.py`

**Interfaces:**
- Consumes: `normaliser`, `ImageInexploitable` (tâche 1) ; `render_post`, `RenduSlide`, `RenduIndisponible` (`app/content/render.py`) ; `telecharger` (`app/content/export.py`, rend `(octets | None, extension | raison)`)
- Produces:
  - `texte_legende(post) -> str` ; constantes `LEGENDE_MAX = 2200`, `HASHTAGS_MAX = 30`
  - `PreparationImpossible(RuntimeError)`
  - `urls_des_photos(post, article) -> list[str]`
  - `preparer_fichiers(post, article, dossier: Path, *, rendre=render_post, telecharger=telecharger_media, ratio_min: float | None = None) -> list[str]` — noms des JPEG écrits, cover en premier

- [ ] **Step 1: Ajouter la configuration**

Dans `app/config.py`, juste après la ligne `meta_token_path` ajoutée à la tâche 2 :

```python
    # --- Publication automatique ---
    # Les fichiers figes au gel (T-2h), un sous-dossier par creneau.
    publication_dir: Path = Path.home() / "Library/Application Support/Kinda/publication"
    # Meta exige des proportions entre 4:5 et 1,91:1 (documentation, 2026-09-16) :
    # la cover 3:4 est recadree en 4:5. A passer a 0.75 seulement si un essai reel
    # montre que Meta accepte le 3:4.
    publication_ratio_min: float = 0.8
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/content/test_legende.py
from types import SimpleNamespace

from app.content.legende import HASHTAGS_MAX, LEGENDE_MAX, texte_legende


def _post(caption="Une legende 🎧 ✨", hashtags=("#electro",), url="https://tsugi.fr/a"):
    return SimpleNamespace(caption=caption, hashtags=list(hashtags), article_url=url)


def test_legende_puis_hashtags_puis_source():
    assert texte_legende(_post()) == "Une legende 🎧 ✨\n\n#electro\n\nSource : https://tsugi.fr/a"


def test_sans_hashtags_pas_de_paragraphe_vide():
    assert texte_legende(_post(hashtags=())) == "Une legende 🎧 ✨\n\nSource : https://tsugi.fr/a"


def test_trente_hashtags_au_plus():
    """Au-dela, Meta refuse la publication."""
    texte = texte_legende(_post(hashtags=[f"#h{i}" for i in range(40)]))
    assert texte.count("#") == HASHTAGS_MAX


def test_une_legende_trop_longue_est_coupee_sans_perdre_la_source():
    texte = texte_legende(_post(caption="x" * 3000))
    assert len(texte) <= LEGENDE_MAX
    assert texte.endswith("Source : https://tsugi.fr/a")
    assert "…" in texte
```

```python
# tests/publish/test_publish_preparation.py
"""Le gel : ce qui partira chez Meta, fige sur disque avant l'heure."""

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.content.render import RenduIndisponible, RenduSlide
from app.publish.preparation import PreparationImpossible, preparer_fichiers, urls_des_photos


def _png(largeur, hauteur):
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (10, 20, 30)).save(tampon, "PNG")
    return tampon.getvalue()


def _rendre(overflow=False):
    def rendre(slides, template):
        rendre.appels.append((slides, template))
        return [RenduSlide(png=_png(1080, 1440), scale=1.0, lines={}, overflow=overflow)]

    rendre.appels = []
    return rendre


def _post(*medias):
    cover = {"kind": "cover", "headline": "**x**", "title": "**x**", "subtitle": "**x**",
             "image_url": "https://img/fond.jpg"}
    return SimpleNamespace(slides=[cover, *medias])


def _article(*urls):
    return SimpleNamespace(media=[{"type": "photo", "url": url} for url in urls])


def _telecharger(echecs=()):
    def telecharger(url):
        if url in echecs:
            return None, "HTTP 404"
        return _png(1200, 800), ".png"

    return telecharger


def test_la_cover_puis_les_photos_en_jpeg(tmp_path):
    rendre = _rendre()
    noms = preparer_fichiers(
        _post(), _article("https://img/1.jpg", "https://img/2.jpg"), tmp_path,
        rendre=rendre, telecharger=_telecharger(),
    )
    assert [nom[-6:] for nom in noms] == ["01.jpg", "02.jpg", "03.jpg"]
    for nom in noms:
        assert (tmp_path / nom).read_bytes()[:2] == b"\xff\xd8"
    assert Image.open(tmp_path / noms[0]).size == (1080, 1350)
    assert rendre.appels[0][1] == "kinda_post"


def test_une_photo_qui_ne_se_telecharge_pas_est_ignoree(tmp_path):
    noms = preparer_fichiers(
        _post(), _article("https://img/1.jpg", "https://img/2.jpg"), tmp_path,
        rendre=_rendre(), telecharger=_telecharger(echecs=("https://img/1.jpg",)),
    )
    assert len(noms) == 2


def test_une_cover_qui_deborde_n_est_jamais_publiee(tmp_path):
    with pytest.raises(PreparationImpossible, match="deborde"):
        preparer_fichiers(_post(), None, tmp_path, rendre=_rendre(overflow=True), telecharger=_telecharger())


def test_chromium_absent_est_une_preparation_impossible(tmp_path):
    def rendre(slides, template):
        raise RenduIndisponible("Chromium Playwright est absent")

    with pytest.raises(PreparationImpossible, match="Chromium"):
        preparer_fichiers(_post(), None, tmp_path, rendre=rendre, telecharger=_telecharger())


def test_dix_images_au_plus(tmp_path):
    urls = [f"https://img/{i}.jpg" for i in range(15)]
    noms = preparer_fichiers(_post(), _article(*urls), tmp_path, rendre=_rendre(), telecharger=_telecharger())
    assert len(noms) == 10


def test_un_nouveau_gel_efface_le_precedent(tmp_path):
    (tmp_path / "ancien_01.jpg").write_bytes(b"x")
    preparer_fichiers(_post(), None, tmp_path, rendre=_rendre(), telecharger=_telecharger())
    assert not (tmp_path / "ancien_01.jpg").exists()


def test_les_photos_viennent_des_slides_puis_de_l_article_sans_doublon():
    post = _post({"kind": "media", "type": "photo", "url": "https://img/a.jpg"},
                 {"kind": "media", "type": "video", "url": "https://img/v.mp4"})
    assert urls_des_photos(post, _article("https://img/a.jpg", "https://img/b.jpg")) == [
        "https://img/a.jpg", "https://img/b.jpg",
    ]
    assert urls_des_photos(post, None) == ["https://img/a.jpg"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/content/test_legende.py tests/publish/test_publish_preparation.py -v`
Expected: FAIL — `ModuleNotFoundError` sur `app.content.legende` et `app.publish.preparation`

- [ ] **Step 4: Write the implementation**

```python
# app/content/legende.py
"""Le texte publie sous un post : legende, hashtags, source.

Commun a l'export zip et a la publication automatique -- un seul endroit ou
respecter les limites de Meta : 2200 caracteres et 30 hashtags.
"""

LEGENDE_MAX = 2200
HASHTAGS_MAX = 30


def texte_legende(post) -> str:
    hashtags = (post.hashtags or [])[:HASHTAGS_MAX]
    parties = [post.caption]
    if hashtags:
        parties.append(" ".join(hashtags))
    parties.append(f"Source : {post.article_url}")
    texte = "\n\n".join(parties)
    if len(texte) <= LEGENDE_MAX:
        return texte
    # On coupe la legende elle-meme, jamais la source : citer l'editeur est le
    # minimum de bonne foi quand on reprend ses images.
    fin = "\n\n".join(parties[1:])
    place = LEGENDE_MAX - len(fin) - len("\n\n") - len("…")
    return post.caption[: max(place, 0)].rstrip() + "…" + "\n\n" + fin
```

Dans `app/api/posts.py` : supprimer la fonction `_texte_legende`, ajouter l'import `from app.content.legende import texte_legende`, et remplacer l'appel `_texte_legende(post)` par `texte_legende(post)` dans `exporter_post`.

```python
# app/publish/preparation.py
"""Le gel : tout ce qu'une publication va envoyer, fige sur disque avant l'heure.

A T-2h, on rend la cover, on telecharge les photos de l'actu et on les met aux
regles de Meta. Deux raisons de le faire en avance plutot qu'a l'heure : une
panne (Chromium absent, image morte) laisse le temps de remplacer l'article, et
l'heure de publication ne depend plus de la lenteur d'un telechargement.

Ordre du carrousel : la cover d'abord, puis les photos de l'actu. Pas de video :
la publication ne fait pas de reel.
"""

import secrets
from pathlib import Path

from app.config import settings
from app.content.export import telecharger as telecharger_media
from app.content.render import RenduIndisponible, render_post
from app.publish.images import ImageInexploitable, normaliser

CARROUSEL_MAX = 10


class PreparationImpossible(RuntimeError):
    """Le post ne peut pas etre fige. Message affichable."""


def urls_des_photos(post, article) -> list[str]:
    urls = [
        slide["url"]
        for slide in post.slides[1:]
        if slide.get("kind") == "media" and slide.get("type") == "photo" and slide.get("url")
    ]
    if article is not None:
        urls += [m["url"] for m in article.media or [] if m.get("type") == "photo" and m.get("url")]
    vues: set[str] = set()
    return [url for url in urls if not (url in vues or vues.add(url))]


def preparer_fichiers(
    post,
    article,
    dossier: Path,
    *,
    rendre=render_post,
    telecharger=telecharger_media,
    ratio_min: float | None = None,
) -> list[str]:
    ratio = settings.publication_ratio_min if ratio_min is None else ratio_min
    dossier.mkdir(parents=True, exist_ok=True)
    for ancien in dossier.glob("*.jpg"):
        ancien.unlink()

    try:
        rendus = rendre(post.slides, "kinda_post")
    except RenduIndisponible as exc:
        raise PreparationImpossible(str(exc)) from exc
    if not rendus:
        raise PreparationImpossible("la cover n'a pas pu etre rendue")
    if rendus[0].overflow:
        raise PreparationImpossible("accroche trop longue : la cover deborde")
    try:
        images = [normaliser(rendus[0].png, ratio)]
    except ImageInexploitable as exc:
        raise PreparationImpossible(f"cover inexploitable : {exc}") from exc

    for url in urls_des_photos(post, article):
        if len(images) >= CARROUSEL_MAX:
            break
        octets, _ = telecharger(url)
        if octets is None:
            continue
        try:
            images.append(normaliser(octets, ratio))
        except ImageInexploitable:
            continue

    # Prefixe aleatoire : les fichiers seront publics quelques minutes, leurs noms
    # ne doivent pas se deviner.
    prefixe = secrets.token_urlsafe(12)
    noms = []
    for rang, jpeg in enumerate(images, start=1):
        nom = f"{prefixe}_{rang:02d}.jpg"
        (dossier / nom).write_bytes(jpeg)
        noms.append(nom)
    return noms
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/content/test_legende.py tests/publish/test_publish_preparation.py tests/api/test_posts_api.py tests/content/test_export.py -v`
Expected: tout passe (les tests d'export existants confirment que la légende n'a pas changé)

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/content/legende.py app/api/posts.py app/publish/preparation.py tests/content/test_legende.py tests/publish/test_publish_preparation.py
git commit -F - <<'EOF'
feat(publication): figer un post sur disque avant l'heure

Cover rendue puis photos de l'actu, normalisees en JPEG, dix au plus. La
legende devient commune a l'export et a la publication, bornee aux limites
de Meta.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 6: Publier un dossier figé, et l'essayer pour de vrai sans rien publier

**Files:**
- Create: `app/publish/publier.py`
- Create: `scripts/diagnostic/tester_publication.py`
- Test: `tests/publish/test_publish_publier.py`

**Interfaces:**
- Consumes: `ClientInstagram`, `ErreurInstagram` (tâche 3) ; `charger` (tâche 2) ; `heberger` (tâche 4) ; `preparer_fichiers` (tâche 5) ; `texte_legende` (tâche 5)
- Produces:
  - `client_par_defaut() -> ClientInstagram`
  - `publier_fichiers(dossier: Path, noms: list[str], legende: str, publier_vraiment: bool, *, client=None, heberger=None) -> str` — identifiant du **conteneur** si `publier_vraiment` est faux, du **média publié** sinon

- [ ] **Step 1: Write the failing test**

```python
# tests/publish/test_publish_publier.py
import contextlib

import pytest

from app.config import settings
from app.publish.instagram import ErreurInstagram
from app.publish.publier import publier_fichiers


class FauxClient:
    def __init__(self, journal):
        self.journal = journal

    def preparer_carrousel(self, urls, legende):
        self.journal.append(("preparer", urls, legende))
        return "conteneur1"

    def publier(self, conteneur_id):
        self.journal.append(("publier", conteneur_id))
        return "media1"


def _heberger(journal):
    @contextlib.contextmanager
    def heberger(dossier, temoin):
        journal.append(("ouvre", temoin))
        yield "https://t.trycloudflare.com"
        journal.append(("ferme",))

    return heberger


def _fichiers(tmp_path, *noms):
    for nom in noms:
        (tmp_path / nom).write_bytes(b"x")
    return list(noms)


def test_en_apercu_on_s_arrete_au_conteneur(tmp_path):
    journal = []
    noms = _fichiers(tmp_path, "p_01.jpg", "p_02.jpg")
    resultat = publier_fichiers(tmp_path, noms, "L", False, client=FauxClient(journal), heberger=_heberger(journal))
    assert resultat == "conteneur1"
    assert journal == [
        ("ouvre", "p_01.jpg"),
        ("preparer", ["https://t.trycloudflare.com/p_01.jpg", "https://t.trycloudflare.com/p_02.jpg"], "L"),
        ("ferme",),
    ]


def test_en_reel_le_tunnel_reste_ouvert_jusqu_a_la_publication(tmp_path):
    journal = []
    noms = _fichiers(tmp_path, "p_01.jpg")
    resultat = publier_fichiers(tmp_path, noms, "L", True, client=FauxClient(journal), heberger=_heberger(journal))
    assert resultat == "media1"
    assert [etape[0] for etape in journal] == ["ouvre", "preparer", "publier", "ferme"]


def test_rien_a_publier_est_une_erreur(tmp_path):
    with pytest.raises(ErreurInstagram, match="aucun fichier"):
        publier_fichiers(tmp_path, [], "L", True, client=FauxClient([]), heberger=_heberger([]))


def test_un_fichier_fige_disparu_est_une_erreur(tmp_path):
    with pytest.raises(ErreurInstagram, match="introuvables"):
        publier_fichiers(tmp_path, ["absent.jpg"], "L", True, client=FauxClient([]), heberger=_heberger([]))


def test_sans_identifiant_instagram_l_erreur_dit_quoi_faire(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "meta_ig_user_id", None)
    noms = _fichiers(tmp_path, "p_01.jpg")
    with pytest.raises(ErreurInstagram, match="META_IG_USER_ID"):
        publier_fichiers(tmp_path, noms, "L", True, heberger=_heberger([]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/publish/test_publish_publier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.publish.publier'`

- [ ] **Step 3: Write the implementation**

```python
# app/publish/publier.py
"""Publier un dossier fige : l'heberger, puis passer par l'API Instagram.

Le tunnel reste ouvert jusqu'a la FIN : Meta telecharge les images pendant la
creation du conteneur, et `media_publish` doit suivre avant la fermeture.
"""

from pathlib import Path

from app.config import settings
from app.publish import hosting
from app.publish.instagram import ClientInstagram, ErreurInstagram
from app.publish.jeton import charger


def client_par_defaut() -> ClientInstagram:
    if not settings.meta_ig_user_id:
        raise ErreurInstagram("META_IG_USER_ID manquant dans .env")
    return ClientInstagram(charger(settings.meta_token_path).access_token, settings.meta_ig_user_id)


def publier_fichiers(
    dossier: Path,
    noms: list[str],
    legende: str,
    publier_vraiment: bool,
    *,
    client=None,
    heberger=None,
) -> str:
    if not noms:
        raise ErreurInstagram("rien a publier : aucun fichier fige")
    manquants = [nom for nom in noms if not (dossier / nom).exists()]
    if manquants:
        raise ErreurInstagram(f"fichiers figes introuvables : {', '.join(manquants)}")
    client = client or client_par_defaut()
    heberger = heberger or hosting.heberger
    with heberger(dossier, noms[0]) as base:
        conteneur = client.preparer_carrousel([f"{base}/{nom}" for nom in noms], legende)
        if not publier_vraiment:
            return conteneur
        return client.publier(conteneur)
```

```python
# scripts/diagnostic/tester_publication.py
"""Essai de bout en bout SANS publier : gel reel, tunnel reel, conteneur reel.

Rien n'est publie : le conteneur cree chez Meta n'est pas public et expire seul
en 24 h. C'est l'essai qui valide pour de vrai le format des images, le tunnel
et le jeton, avant toute publication.

Usage :
    uv run python -m scripts.diagnostic.tester_publication --post 12
"""

import argparse

from app.config import settings
from app.content.legende import texte_legende
from app.db import BaseInjoignable, SessionLocal, attendre_base
from app.models import Article, Post
from app.publish.preparation import preparer_fichiers
from app.publish.publier import publier_fichiers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post", type=int, required=True)
    args = parser.parse_args()
    try:
        attendre_base()
    except BaseInjoignable as exc:
        raise SystemExit(f"[ERREUR] {exc}") from exc

    with SessionLocal() as db:
        post = db.get(Post, args.post)
        if post is None:
            raise SystemExit(f"post {args.post} introuvable")
        article = db.get(Article, post.article_id) if post.article_id else None
        dossier = settings.publication_dir / "essais" / str(post.id)
        noms = preparer_fichiers(post, article, dossier)
        print(f"{len(noms)} image(s) figee(s) dans {dossier}")
        conteneur = publier_fichiers(dossier, noms, texte_legende(post), publier_vraiment=False)
        print(f"conteneur {conteneur} FINISHED chez Meta -- rien n'a ete publie")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/publish/test_publish_publier.py -v`
Expected: 5 passed

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Installer cloudflared si absent**

Run: `which cloudflared || ls /opt/homebrew/bin/cloudflared || brew install cloudflared`
Expected: un chemin vers `cloudflared`. Pas de `sudo`.

- [ ] **Step 7: Essai réel, sans publier**

Choisir un post existant au gabarit `kinda_post` :
Run: `uv run python -c "from app.db import SessionLocal; from app.models import Post; db=SessionLocal(); print([(p.id, p.template, p.article_title[:40]) for p in db.query(Post).order_by(Post.id.desc()).limit(8)])"`

Si Chromium manque dans ce worktree (erreur « Chromium Playwright est absent »), lancer d'abord `uv run playwright install chromium`.

Puis, avec l'identifiant d'un post `kinda_post` :
Run: `uv run python -m scripts.diagnostic.tester_publication --post <ID>`
Expected: `conteneur <nombre> FINISHED chez Meta -- rien n'a ete publie`

Si Meta refuse le format, le tunnel ou le jeton, **s'arrêter et rapporter le message exact** : c'est précisément ce que cet essai doit révéler avant d'aller plus loin. Ne jamais appeler `publier_fichiers(..., publier_vraiment=True)` ici.

- [ ] **Step 8: Commit**

```bash
git add app/publish/publier.py scripts/diagnostic/tester_publication.py tests/publish/test_publish_publier.py
git commit -F - <<'EOF'
feat(publication): publier un dossier fige, et l'essayer sans publier

Le tunnel reste ouvert jusqu'a la fin de media_publish. Le script d'essai
cree un vrai conteneur chez Meta sans jamais le publier.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

## Partie C — Décider quoi publier, et quand

### Task 7: La table `creneaux` et l'exclusion d'un article

**Files:**
- Modify: `app/models.py` (enum `EtatCreneau` après `PostStatus` ; colonne `Article.file_exclu_raison` après `duplicate_of_id` ; classe `Creneau` après `Post`)
- Create: `alembic/versions/0014_creneaux.py`
- Test: `tests/schedule/test_schedule_modeles.py`

**Interfaces:**
- Consumes: rien
- Produces:
  - `EtatCreneau` : `LIBRE="libre"`, `PROGRAMME="programme"`, `GELE="gele"`, `PUBLIE="publie"`, `APERCU="apercu"`, `ECHEC="echec"`, `MANQUE="manque"`
  - `Creneau` : `id`, `publier_a` (UTC, unique), `article_id`, `post_id`, `etat`, `epingle`, `fichiers` (liste JSON de noms), `ig_media_id`, `erreur`, `gele_le`, `publie_le`, `cree_le`
  - `Article.file_exclu_raison: str | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/schedule/test_schedule_modeles.py
import datetime as dt

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Article, Creneau, EtatCreneau, Source, SourceType

HEURE = dt.datetime(2026, 9, 20, 7, 12, tzinfo=dt.timezone.utc)


def test_un_creneau_neuf_est_libre_et_non_epingle(db_session):
    creneau = Creneau(publier_a=HEURE)
    db_session.add(creneau)
    db_session.commit()
    assert (creneau.etat, creneau.epingle, creneau.article_id, creneau.fichiers) == (
        EtatCreneau.LIBRE, False, None, None,
    )


def test_deux_creneaux_ne_partagent_jamais_la_meme_heure(db_session):
    db_session.add_all([Creneau(publier_a=HEURE), Creneau(publier_a=HEURE)])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_un_article_n_est_pas_exclu_par_defaut(db_session):
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://t/a", title="A", raw_hash="h")
    db_session.add(article)
    db_session.commit()
    assert article.file_exclu_raison is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/schedule/test_schedule_modeles.py -v`
Expected: FAIL — `ImportError: cannot import name 'Creneau'`

- [ ] **Step 3: Write the models**

Dans `app/models.py`, après `class PostStatus` :

```python
class EtatCreneau(str, enum.Enum):
    """Ou en est un creneau de publication.

    LIBRE et PROGRAMME bougent encore au gre de l'arbitrage. Tous les autres sont
    definitifs : GELE (fichiers prets, plus aucun remplacement), PUBLIE, APERCU
    (conteneur cree chez Meta mais non publie, mode apercu), ECHEC (la
    publication a ete tentee et refusee) et MANQUE (l'heure est passee de plus de
    90 minutes sans publication, typiquement le Mac endormi).
    """

    LIBRE = "libre"
    PROGRAMME = "programme"
    GELE = "gele"
    PUBLIE = "publie"
    APERCU = "apercu"
    ECHEC = "echec"
    MANQUE = "manque"
```

Dans `class Article`, juste après la colonne `duplicate_of_id` :

```python
    # Raison pour laquelle l'article ne doit plus etre propose a la file de
    # publication : composition ou gel impossible, ou « sauter » depuis le
    # dashboard. Null = eligible. Sans cette trace, un article en defaut
    # reviendrait a chaque passage de l'agent, toutes les 10 minutes.
    file_exclu_raison: Mapped[str | None] = mapped_column(Text)
```

Après `class Post` :

```python
class Creneau(Base):
    """Une heure de publication, et ce qui doit y partir.

    Table a part et non colonnes sur `posts` : un creneau porte un article bien
    avant qu'un post existe, puisque la composition (appel Claude paye) n'a lieu
    qu'a J-1. `publier_a` est UNIQUE : deux creneaux a la meme heure publieraient
    deux fois.
    """

    __tablename__ = "creneaux"

    id: Mapped[int] = mapped_column(primary_key=True)
    publier_a: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), unique=True, index=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id", ondelete="SET NULL"))
    etat: Mapped[EtatCreneau] = mapped_column(
        Enum(
            EtatCreneau, name="etat_creneau", native_enum=False, length=20,
            values_callable=_values,
        ),
        default=EtatCreneau.LIBRE,
        server_default="libre",
        index=True,
    )
    # Epingle depuis le dashboard : l'arbitrage n'y touche plus.
    epingle: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Noms des JPEG figes, dans l'ordre du carrousel. Reassigner la liste ENTIERE.
    fichiers: Mapped[list | None] = mapped_column(JSONVariant)
    ig_media_id: Mapped[str | None] = mapped_column(String(100))
    erreur: Mapped[str | None] = mapped_column(Text)
    gele_le: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    publie_le: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cree_le: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/schedule/test_schedule_modeles.py -v`
Expected: 3 passed

- [ ] **Step 5: Write the migration**

```python
# alembic/versions/0014_creneaux.py
"""creneaux : la file de publication, et l'exclusion d'un article de cette file

Un creneau porte un article avant qu'un post soit compose -- d'ou une table et
non des colonnes sur `posts`. `articles.file_exclu_raison` empeche un article en
defaut de revenir a chaque passage de l'agent de publication.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

ETATS = ("libre", "programme", "gele", "publie", "apercu", "echec", "manque")


def upgrade() -> None:
    op.add_column("articles", sa.Column("file_exclu_raison", sa.Text(), nullable=True))
    op.create_table(
        "creneaux",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("publier_a", sa.DateTime(timezone=True), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("post_id", sa.Integer(), nullable=True),
        sa.Column("etat", sa.String(length=20), server_default="libre", nullable=False),
        sa.Column("epingle", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("fichiers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ig_media_id", sa.String(length=100), nullable=True),
        sa.Column("erreur", sa.Text(), nullable=True),
        sa.Column("gele_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publie_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_creneaux")),
        # SET NULL : la purge d'un article ou la suppression d'un brouillon ne
        # doit jamais effacer l'historique des publications.
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name=op.f("fk_creneaux_articles_article_id"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["post_id"], ["posts.id"],
            name=op.f("fk_creneaux_posts_post_id"), ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "etat IN ('" + "', '".join(ETATS) + "')", name=op.f("ck_creneaux_etat_creneau")
        ),
    )
    op.create_index(op.f("ix_creneaux_publier_a"), "creneaux", ["publier_a"], unique=True)
    op.create_index(op.f("ix_creneaux_article_id"), "creneaux", ["article_id"])
    op.create_index(op.f("ix_creneaux_etat"), "creneaux", ["etat"])


def downgrade() -> None:
    op.drop_index(op.f("ix_creneaux_etat"), table_name="creneaux")
    op.drop_index(op.f("ix_creneaux_article_id"), table_name="creneaux")
    op.drop_index(op.f("ix_creneaux_publier_a"), table_name="creneaux")
    op.drop_table("creneaux")
    op.drop_column("articles", "file_exclu_raison")
```

- [ ] **Step 6: Vérifier la migration sur un cluster jetable, jamais sur Neon**

La base de l'utilisateur (Neon) ne doit PAS être migrée par l'implémenteur. Procédure éprouvée du dépôt (le chemin du socket doit rester court, sinon Postgres refuse de démarrer) :

```bash
D="$(mktemp -d)"; S=/tmp/mnrpg; mkdir -p "$S"
/Library/PostgreSQL/15/bin/initdb -D "$D/data" -U postgres -A trust >/dev/null
# -w : pg_ctl attend que le serveur accepte les connexions (pas de sleep).
/Library/PostgreSQL/15/bin/pg_ctl -w -D "$D/data" -o "-p 55432 -k $S" -l "$D/log" start
/Library/PostgreSQL/15/bin/createdb -h 127.0.0.1 -p 55432 -U postgres mnr_test
export DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:55432/mnr_test"
uv run alembic upgrade head
uv run alembic check
uv run alembic downgrade 0013
uv run alembic upgrade head
unset DATABASE_URL
/Library/PostgreSQL/15/bin/pg_ctl -D "$D/data" stop
rm -rf "$D" "$S"
```

Expected: `upgrade head` passe jusqu'à `0014`, `alembic check` affiche `No new upgrade operations detected.`, le `downgrade` puis le nouvel `upgrade` passent sans erreur.

- [ ] **Step 7: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 8: Commit**

```bash
git add app/models.py alembic/versions/0014_creneaux.py tests/schedule/test_schedule_modeles.py
git commit -F - <<'EOF'
feat(file): table creneaux et exclusion d'un article de la file

Un creneau porte un article avant que le post soit compose. Une raison
d'exclusion empeche un article en defaut de revenir a chaque passage.
Migration verifiee sur un cluster jetable, non appliquee a Neon.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 8: Les créneaux, tirés une fois pour toutes

**Files:**
- Create: `app/schedule/__init__.py`
- Create: `app/schedule/slots.py`
- Modify: `app/config.py` (suite du bloc `publication_*`)
- Test: `tests/schedule/test_schedule_slots.py`

**Interfaces:**
- Consumes: `settings.publication_fuseau`, `settings.publication_fenetre_matin`, `settings.publication_fenetre_apres_midi`
- Produces:
  - `horaires_du_jour(jour: date) -> list[datetime]` — deux heures UTC, matin puis après-midi
  - `prochains_horaires(maintenant: datetime, nombre: int) -> list[datetime]` — les `nombre` prochaines heures strictement après `maintenant`, triées

- [ ] **Step 1: Ajouter la configuration**

Dans `app/config.py`, à la suite de `publication_ratio_min` :

```python
    # Fenetres de publication, heure de Paris. L'heure exacte est tiree au hasard
    # dans chaque fenetre, pour ne pas publier comme une horloge.
    publication_fuseau: str = "Europe/Paris"
    publication_fenetre_matin: str = "08:30-11:00"
    publication_fenetre_apres_midi: str = "17:00-19:30"
    # 3 jours x 2 creneaux.
    publication_creneaux: int = 6
```

- [ ] **Step 2: Write the failing test**

```python
# tests/schedule/test_schedule_slots.py
import datetime as dt
from zoneinfo import ZoneInfo

from app.schedule.slots import horaires_du_jour, prochains_horaires

PARIS = ZoneInfo("Europe/Paris")


def _local(moment):
    return moment.astimezone(PARIS)


def test_deux_horaires_par_jour_dans_leurs_fenetres():
    for decalage in range(40):
        jour = dt.date(2026, 9, 20) + dt.timedelta(days=decalage)
        matin, apres_midi = horaires_du_jour(jour)
        assert _local(matin).date() == jour == _local(apres_midi).date()
        assert dt.time(8, 30) <= _local(matin).time() <= dt.time(11, 0)
        assert dt.time(17, 0) <= _local(apres_midi).time() <= dt.time(19, 30)


def test_le_tirage_est_deterministe_pour_une_meme_date():
    """Sinon l'heure serait retiree a chaque passage de l'agent et glisserait :
    un creneau pourrait n'etre jamais atteint."""
    assert horaires_du_jour(dt.date(2026, 9, 20)) == horaires_du_jour(dt.date(2026, 9, 20))


def test_le_tirage_change_d_un_jour_a_l_autre():
    matins = {
        _local(horaires_du_jour(dt.date(2026, 9, 1) + dt.timedelta(days=i))[0]).time()
        for i in range(10)
    }
    assert len(matins) > 1


def test_les_horaires_sont_en_utc():
    assert all(h.tzinfo == dt.timezone.utc for h in horaires_du_jour(dt.date(2026, 9, 20)))


def test_le_jour_du_changement_d_heure_reste_dans_les_fenetres():
    matin, apres_midi = horaires_du_jour(dt.date(2026, 10, 25))
    assert dt.time(8, 30) <= _local(matin).time() <= dt.time(11, 0)
    assert dt.time(17, 0) <= _local(apres_midi).time() <= dt.time(19, 30)


def test_prochains_horaires_futurs_et_dans_l_ordre():
    maintenant = dt.datetime(2026, 9, 20, 12, 0, tzinfo=PARIS).astimezone(dt.timezone.utc)
    horaires = prochains_horaires(maintenant, 6)
    assert len(horaires) == 6
    assert horaires == sorted(horaires)
    assert all(h > maintenant for h in horaires)
    assert _local(horaires[0]).date() == dt.date(2026, 9, 20)
    assert _local(horaires[1]).date() == dt.date(2026, 9, 21)


def test_apres_la_fenetre_du_soir_on_passe_au_lendemain():
    maintenant = dt.datetime(2026, 9, 20, 20, 0, tzinfo=PARIS).astimezone(dt.timezone.utc)
    assert _local(prochains_horaires(maintenant, 1)[0]).date() == dt.date(2026, 9, 21)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/schedule/test_schedule_slots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schedule'`

- [ ] **Step 4: Write minimal implementation**

`app/schedule/__init__.py` : fichier vide.

```python
# app/schedule/slots.py
"""Les creneaux de publication : une date, deux heures, tirees une fois pour toutes.

L'heure est tiree dans chaque fenetre avec une GRAINE DERIVEE DE LA DATE. C'est
la decision centrale du module : sans elle, l'agent (qui passe toutes les 10
minutes) retirerait une heure a chaque passage et elle glisserait -- un creneau
pourrait n'etre jamais atteint. Avec elle, recalculer a 9 h et a 10 h redonne le
meme 14 h 37.

Python pur : aucune base, aucun reseau. Tout sort en UTC.
"""

import datetime as dt
import random
from zoneinfo import ZoneInfo

from app.config import settings


def _fenetre(texte: str) -> tuple[dt.time, dt.time]:
    debut, fin = texte.split("-")
    return dt.time.fromisoformat(debut), dt.time.fromisoformat(fin)


def horaires_du_jour(jour: dt.date) -> list[dt.datetime]:
    fuseau = ZoneInfo(settings.publication_fuseau)
    horaires = []
    fenetres = (settings.publication_fenetre_matin, settings.publication_fenetre_apres_midi)
    for rang, fenetre in enumerate(fenetres):
        debut, fin = _fenetre(fenetre)
        ouverture = dt.datetime.combine(jour, debut, tzinfo=fuseau)
        fermeture = dt.datetime.combine(jour, fin, tzinfo=fuseau)
        minutes = int((fermeture - ouverture).total_seconds() // 60)
        # Graine en chaine : son hachage est stable d'une execution a l'autre.
        tirage = random.Random(f"kinda-{jour.isoformat()}-{rang}").randint(0, minutes)
        horaires.append((ouverture + dt.timedelta(minutes=tirage)).astimezone(dt.timezone.utc))
    return horaires


def prochains_horaires(maintenant: dt.datetime, nombre: int) -> list[dt.datetime]:
    jour = maintenant.astimezone(ZoneInfo(settings.publication_fuseau)).date()
    horaires: list[dt.datetime] = []
    while len(horaires) < nombre:
        horaires += [h for h in horaires_du_jour(jour) if h > maintenant]
        jour += dt.timedelta(days=1)
    return horaires[:nombre]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/schedule/test_schedule_slots.py -v`
Expected: 7 passed

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/schedule/__init__.py app/schedule/slots.py tests/schedule/test_schedule_slots.py
git commit -F - <<'EOF'
feat(file): deux creneaux par jour, tires une fois pour toutes

Heure aleatoire dans les fenetres du matin et de l'apres-midi, graine derivee
de la date pour que l'heure ne glisse pas d'un passage a l'autre.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 9: L'arbitrage

**Files:**
- Create: `app/schedule/queue.py`
- Test: `tests/schedule/test_schedule_queue.py`

**Interfaces:**
- Consumes: rien
- Produces:
  - `@dataclass(frozen=True) Candidat(article_id: int, relevance: int, hotness: int, publie_le: datetime | None, compose: bool = False)`
  - `@dataclass(frozen=True) Place(publier_a: datetime, article_id: int | None, verrouillee: bool)`
  - `score(candidat: Candidat, maintenant: datetime) -> float`
  - `arbitrer(places: list[Place], candidats: list[Candidat], maintenant: datetime) -> dict[datetime, int | None]` — une entrée par place
  - constantes `POIDS_HOTNESS = 0.25`, `PENALITE_PAR_JOUR = 15.0`, `MARGE_TITULAIRE = 15.0`

- [ ] **Step 1: Write the failing test**

```python
# tests/schedule/test_schedule_queue.py
import datetime as dt

from app.schedule.queue import Candidat, Place, arbitrer, score

T0 = dt.datetime(2026, 9, 20, 8, 0, tzinfo=dt.timezone.utc)


def _places(nombre, verrous=None):
    verrous = verrous or {}
    return [
        Place(T0 + dt.timedelta(hours=12 * rang), verrous.get(rang), rang in verrous)
        for rang in range(nombre)
    ]


def _candidat(article_id, relevance, hotness=0, age_heures=0, compose=False):
    return Candidat(article_id, relevance, hotness, T0 - dt.timedelta(hours=age_heures), compose)


def _ordre(affectation):
    return [affectation[moment] for moment in sorted(affectation)]


def test_le_meilleur_prend_la_place_la_plus_proche():
    candidats = [_candidat(1, 60), _candidat(2, 90), _candidat(3, 75)]
    assert _ordre(arbitrer(_places(3), candidats, T0)) == [2, 3, 1]


def test_une_place_verrouillee_garde_son_article_qui_n_est_pas_place_ailleurs():
    candidats = [_candidat(9, 99), _candidat(1, 70), _candidat(2, 60)]
    assert _ordre(arbitrer(_places(3, verrous={0: 9}), candidats, T0)) == [9, 1, 2]


def test_les_places_en_trop_restent_vides():
    assert _ordre(arbitrer(_places(3), [_candidat(1, 80)], T0)) == [1, None, None]


def test_les_moins_bons_sortent_de_la_file():
    """« ou alors on la met jamais »"""
    candidats = [_candidat(1, 50), _candidat(2, 90), _candidat(3, 70)]
    assert _ordre(arbitrer(_places(2), candidats, T0)) == [2, 3]


def test_l_urgence_compte():
    candidats = [_candidat(1, 80, hotness=0), _candidat(2, 60, hotness=100)]
    assert _ordre(arbitrer(_places(2), candidats, T0)) == [2, 1]


def test_une_actu_de_deux_jours_cede_la_place():
    candidats = [_candidat(1, 80, age_heures=48), _candidat(2, 60)]
    assert _ordre(arbitrer(_places(2), candidats, T0)) == [2, 1]


def test_un_titulaire_deja_compose_resiste_a_un_peu_mieux():
    """La composition est payee : on ne la jette pas pour dix points."""
    candidats = [_candidat(1, 70, compose=True), _candidat(2, 80)]
    assert _ordre(arbitrer(_places(2), candidats, T0)) == [1, 2]


def test_une_grosse_actu_detrone_le_titulaire_qui_recule_d_un_cran():
    candidats = [_candidat(1, 70, compose=True), _candidat(2, 90)]
    assert _ordre(arbitrer(_places(2), candidats, T0)) == [2, 1]


def test_egalite_departagee_par_identifiant():
    assert _ordre(arbitrer(_places(2), [_candidat(5, 70), _candidat(3, 70)], T0)) == [3, 5]


def test_un_age_inconnu_compte_pour_un_jour():
    assert score(Candidat(1, 80, 0, None), T0) == 65.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/schedule/test_schedule_queue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schedule.queue'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/schedule/queue.py
"""L'arbitrage : quels articles dans quelles places. Python pur, aucune base.

A chaque passage, les places libres sont reattribuees : les meilleurs candidats
prennent les places les plus proches. Une grosse actu qui arrive prend donc la
premiere place et DECALE la precedente d'un cran ; ce qui ne tient plus dans les
places sort de la file -- « ou alors on la met jamais ».

Le score : pertinence, plus une part d'urgence (hotness), moins une penalite
d'age. Un article deja compose (appel Claude paye) garde un avantage : on ne
jette pas une composition pour quelques points.
"""

import datetime as dt
from dataclasses import dataclass

POIDS_HOTNESS = 0.25
PENALITE_PAR_JOUR = 15.0
MARGE_TITULAIRE = 15.0
AGE_INCONNU_JOURS = 1.0


@dataclass(frozen=True)
class Candidat:
    article_id: int
    relevance: int
    hotness: int
    publie_le: dt.datetime | None
    compose: bool = False


@dataclass(frozen=True)
class Place:
    publier_a: dt.datetime
    article_id: int | None
    verrouillee: bool


def score(candidat: Candidat, maintenant: dt.datetime) -> float:
    if candidat.publie_le is None:
        age_jours = AGE_INCONNU_JOURS
    else:
        age_jours = max((maintenant - candidat.publie_le).total_seconds() / 86400, 0.0)
    return candidat.relevance + POIDS_HOTNESS * candidat.hotness - PENALITE_PAR_JOUR * age_jours


def arbitrer(
    places: list[Place], candidats: list[Candidat], maintenant: dt.datetime
) -> dict[dt.datetime, int | None]:
    affectation = {p.publier_a: p.article_id for p in places if p.verrouillee}
    pris = {article_id for article_id in affectation.values() if article_id is not None}
    libres = sorted((p for p in places if not p.verrouillee), key=lambda p: p.publier_a)
    classement = sorted(
        (c for c in candidats if c.article_id not in pris),
        key=lambda c: (-(score(c, maintenant) + (MARGE_TITULAIRE if c.compose else 0.0)), c.article_id),
    )
    for rang, place in enumerate(libres):
        affectation[place.publier_a] = classement[rang].article_id if rang < len(classement) else None
    return affectation
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/schedule/test_schedule_queue.py -v`
Expected: 10 passed

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add app/schedule/queue.py tests/schedule/test_schedule_queue.py
git commit -F - <<'EOF'
feat(file): arbitrer les articles entre les creneaux

Pertinence, urgence et age decident de l'ordre ; une grosse actu decale les
autres d'un cran, un article deja compose garde un avantage, et les places
verrouillees ne bougent pas.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 10: Relier l'arbitrage à la base

**Files:**
- Create: `app/schedule/store.py`
- Modify: `app/config.py` (suite du bloc `publication_*`)
- Test: `tests/schedule/test_schedule_store.py`

**Interfaces:**
- Consumes: `Creneau`, `EtatCreneau`, `Article.file_exclu_raison` (tâche 7) ; `prochains_horaires` (tâche 8) ; `Candidat`, `Place`, `arbitrer` (tâche 9)
- Produces:
  - `utc(moment: datetime | None) -> datetime | None`
  - `VERROUILLES: frozenset[EtatCreneau]`, `MODIFIABLES: frozenset[EtatCreneau]`
  - `synchroniser_creneaux(db, maintenant) -> list[Creneau]` — les créneaux futurs, triés
  - `candidats_eligibles(db, maintenant) -> list[Candidat]`
  - `post_a_reprendre(db, article_id: int) -> int | None`
  - `appliquer_arbitrage(db, maintenant) -> list[Creneau]`

- [ ] **Step 1: Ajouter la configuration**

Dans `app/config.py`, à la suite de `publication_creneaux` :

```python
    # Eligibilite a la file : la barre du digest, et une actu de moins de 3 jours.
    publication_min_relevance: int = 60
    publication_fraicheur_jours: int = 3
```

- [ ] **Step 2: Write the failing test**

```python
# tests/schedule/test_schedule_store.py
import datetime as dt

from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, Source, SourceType
from app.schedule.slots import prochains_horaires
from app.schedule.store import (
    appliquer_arbitrage,
    candidats_eligibles,
    synchroniser_creneaux,
    utc,
)

MAINTENANT = dt.datetime(2026, 9, 20, 10, 0, tzinfo=dt.timezone.utc)


def _source(db):
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db.add(source)
    db.commit()
    return source


def _article(db, source, n, relevance=80, age_heures=2, media=True, **champs):
    article = Article(
        source_id=source.id, url=f"https://t/{n}", title=f"Actu {n}", raw_hash=f"h{n}",
        relevance_score=relevance, hotness_score=20,
        published_at=MAINTENANT - dt.timedelta(hours=age_heures),
        media=[{"type": "photo", "url": f"https://img/{n}.jpg"}] if media else None,
        **champs,
    )
    db.add(article)
    db.commit()
    return article


def _post(db, article):
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                caption="Legende", slides=[{"kind": "cover"}])
    db.add(post)
    db.commit()
    return post


def test_synchroniser_cree_six_creneaux_une_seule_fois(db_session):
    synchroniser_creneaux(db_session, MAINTENANT)
    creneaux = synchroniser_creneaux(db_session, MAINTENANT)
    assert len(creneaux) == 6
    assert db_session.query(Creneau).count() == 6
    assert [utc(c.publier_a) for c in creneaux] == prochains_horaires(MAINTENANT, 6)


def test_un_creneau_futur_hors_grille_est_retire_sauf_s_il_est_epingle(db_session):
    hors_grille = MAINTENANT + dt.timedelta(hours=1, minutes=1)
    epingle = MAINTENANT + dt.timedelta(hours=1, minutes=2)
    db_session.add_all([Creneau(publier_a=hors_grille), Creneau(publier_a=epingle, epingle=True)])
    db_session.commit()
    heures = {utc(c.publier_a) for c in synchroniser_creneaux(db_session, MAINTENANT)}
    assert hors_grille not in heures
    assert epingle in heures


def test_seuls_les_articles_publiables_sont_candidats(db_session):
    source = _source(db_session)
    bon = _article(db_session, source, 1)
    _article(db_session, source, 2, relevance=40)
    _article(db_session, source, 3, duplicate_of_id=bon.id)
    _article(db_session, source, 4, status=ArticleStatus.USED)
    _article(db_session, source, 5, file_exclu_raison="saute depuis le dashboard")
    _article(db_session, source, 6, age_heures=24 * 5)
    _article(db_session, source, 7, media=False)
    assert [c.article_id for c in candidats_eligibles(db_session, MAINTENANT)] == [bon.id]


def test_un_article_deja_compose_est_signale(db_session):
    source = _source(db_session)
    article = _article(db_session, source, 1)
    _post(db_session, article)
    (candidat,) = candidats_eligibles(db_session, MAINTENANT)
    assert candidat.compose is True


def test_appliquer_remplit_dans_l_ordre_et_laisse_le_reste_libre(db_session):
    source = _source(db_session)
    moyen = _article(db_session, source, 1, relevance=70)
    meilleur = _article(db_session, source, 2, relevance=90)
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert [c.article_id for c in creneaux] == [meilleur.id, moyen.id, None, None, None, None]
    assert [c.etat for c in creneaux[:3]] == [EtatCreneau.PROGRAMME, EtatCreneau.PROGRAMME, EtatCreneau.LIBRE]


def test_appliquer_ne_touche_pas_un_creneau_gele(db_session):
    source = _source(db_session)
    ancien = _article(db_session, source, 1, relevance=60)
    _article(db_session, source, 2, relevance=95)
    premier = synchroniser_creneaux(db_session, MAINTENANT)[0]
    premier.article_id, premier.etat = ancien.id, EtatCreneau.GELE
    db_session.commit()
    creneaux = appliquer_arbitrage(db_session, MAINTENANT)
    assert (creneaux[0].article_id, creneaux[0].etat) == (ancien.id, EtatCreneau.GELE)


def test_appliquer_reprend_le_post_deja_compose(db_session):
    source = _source(db_session)
    article = _article(db_session, source, 1)
    post = _post(db_session, article)
    assert appliquer_arbitrage(db_session, MAINTENANT)[0].post_id == post.id


def test_un_article_retenu_par_un_creneau_passe_n_est_pas_programme_deux_fois(db_session):
    """Mac reveille en retard : le creneau de 7 h 12 est passe mais encore dans
    les 90 minutes. Son article ne doit pas etre place aussi sur un creneau futur."""
    source = _source(db_session)
    article = _article(db_session, source, 1)
    db_session.add(Creneau(publier_a=MAINTENANT - dt.timedelta(minutes=20),
                           article_id=article.id, etat=EtatCreneau.PROGRAMME))
    db_session.commit()
    assert all(c.article_id is None for c in appliquer_arbitrage(db_session, MAINTENANT))


def test_deux_passages_donnent_la_meme_file(db_session):
    source = _source(db_session)
    for n in range(3):
        _article(db_session, source, n, relevance=70 + n)
    premiere = [c.article_id for c in appliquer_arbitrage(db_session, MAINTENANT)]
    seconde = [c.article_id for c in appliquer_arbitrage(db_session, MAINTENANT)]
    assert premiere == seconde
    assert db_session.query(Creneau).count() == 6
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/schedule/test_schedule_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schedule.store'`

- [ ] **Step 4: Write minimal implementation**

```python
# app/schedule/store.py
"""Le lien entre l'arbitrage, en Python pur, et la base.

Trois gestes : tenir a jour les creneaux futurs, lister les articles publiables,
et ecrire le resultat de l'arbitrage. Toute date lue en base passe par `utc()` :
SQLite rend des datetime naifs, et comparer un naif a un aware leve une erreur.
"""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, PostStatus
from app.schedule.queue import Candidat, Place, arbitrer
from app.schedule.slots import prochains_horaires

# Un creneau dans l'un de ces etats ne bouge plus.
VERROUILLES = frozenset({
    EtatCreneau.GELE, EtatCreneau.PUBLIE, EtatCreneau.APERCU, EtatCreneau.ECHEC, EtatCreneau.MANQUE,
})
MODIFIABLES = frozenset({EtatCreneau.LIBRE, EtatCreneau.PROGRAMME})


def utc(moment: dt.datetime | None) -> dt.datetime | None:
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.timezone.utc)


def synchroniser_creneaux(db: Session, maintenant: dt.datetime) -> list[Creneau]:
    attendus = prochains_horaires(maintenant, settings.publication_creneaux)
    existants = {
        utc(c.publier_a): c
        for c in db.scalars(select(Creneau).where(Creneau.publier_a > maintenant))
    }
    for heure in attendus:
        if heure not in existants:
            db.add(Creneau(publier_a=heure))
    # Un creneau futur qui n'est plus dans la grille (fenetres changees en
    # config) disparait, sauf s'il est epingle ou deja engage.
    for heure, creneau in existants.items():
        if heure not in attendus and creneau.etat in MODIFIABLES and not creneau.epingle:
            db.delete(creneau)
    db.commit()
    return list(
        db.scalars(select(Creneau).where(Creneau.publier_a > maintenant).order_by(Creneau.publier_a))
    )


def _a_une_image(article: Article) -> bool:
    # Sans image, la cover partirait sur le fond de secours : pas publiable.
    return any(
        (m.get("type") == "photo" and m.get("url")) or m.get("preview_url")
        for m in article.media or []
    )


def candidats_eligibles(db: Session, maintenant: dt.datetime) -> list[Candidat]:
    depuis = maintenant - dt.timedelta(days=settings.publication_fraicheur_jours)
    articles = db.scalars(
        select(Article)
        .where(Article.relevance_score >= settings.publication_min_relevance)
        .where(Article.duplicate_of_id.is_(None))
        .where(Article.status != ArticleStatus.USED)
        .where(Article.file_exclu_raison.is_(None))
        .where(func.coalesce(Article.published_at, Article.fetched_at) >= depuis)
        .order_by(Article.relevance_score.desc(), Article.id)
        .limit(60)
    ).all()
    composes = set(
        db.scalars(
            select(Post.article_id).where(Post.status != PostStatus.PUBLISHED, Post.article_id.is_not(None))
        )
    )
    return [
        Candidat(a.id, a.relevance_score, a.hotness_score or 0, utc(a.published_at or a.fetched_at), a.id in composes)
        for a in articles
        if _a_une_image(a)
    ]


def post_a_reprendre(db: Session, article_id: int) -> int | None:
    return db.scalar(
        select(Post.id)
        .where(Post.article_id == article_id, Post.status != PostStatus.PUBLISHED)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(1)
    )


def appliquer_arbitrage(db: Session, maintenant: dt.datetime) -> list[Creneau]:
    creneaux = synchroniser_creneaux(db, maintenant)
    # Un creneau passe mais encore dans le delai de retard garde son article :
    # sans ce filtre, le meme article serait aussi programme sur un creneau futur.
    occupes = set(
        db.scalars(
            select(Creneau.article_id).where(
                Creneau.publier_a <= maintenant,
                Creneau.etat.in_([EtatCreneau.PROGRAMME, EtatCreneau.GELE]),
                Creneau.article_id.is_not(None),
            )
        )
    )
    candidats = [c for c in candidats_eligibles(db, maintenant) if c.article_id not in occupes]
    places = [
        Place(utc(c.publier_a), c.article_id, c.epingle or c.etat in VERROUILLES) for c in creneaux
    ]
    affectation = arbitrer(places, candidats, maintenant)
    for creneau in creneaux:
        if creneau.epingle or creneau.etat in VERROUILLES:
            continue
        article_id = affectation.get(utc(creneau.publier_a))
        if article_id != creneau.article_id:
            creneau.article_id = article_id
            creneau.post_id = post_a_reprendre(db, article_id) if article_id else None
        creneau.etat = EtatCreneau.PROGRAMME if article_id else EtatCreneau.LIBRE
    db.commit()
    return creneaux
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/schedule/test_schedule_store.py -v`
Expected: 9 passed

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/schedule/store.py tests/schedule/test_schedule_store.py
git commit -F - <<'EOF'
feat(file): tenir les creneaux et appliquer l'arbitrage en base

Six creneaux futurs, des candidats publiables (pertinents, recents, illustres,
non exclus), et une affectation qui respecte les creneaux engages.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 11: Un passage de l'agent de publication

**Files:**
- Create: `app/schedule/runner.py`
- Modify: `app/config.py` (fin du bloc `publication_*`)
- Test: `tests/schedule/test_schedule_runner.py`

**Interfaces:**
- Consumes: `appliquer_arbitrage`, `post_a_reprendre`, `utc` (tâche 10) ; `texte_legende` (tâche 5) ; `create_post`, `CompositionError` (`app/content/compose.py`)
- Produces:
  - `MODES = ("arret", "apercu", "reel")`
  - `@dataclass RapportPublication(composes=0, geles=0, publies=0, apercus=0, manques=0, echecs=0, erreurs: list[str])`
  - `tourner(db, maintenant, *, mode: str, composer, preparer, publier, notifier) -> RapportPublication` où :
    - `composer(article) -> PostDraft`
    - `preparer(post, article, dossier: Path) -> list[str]` (signature de `preparer_fichiers`)
    - `publier(dossier: Path, noms: list[str], legende: str, publier_vraiment: bool) -> str` (signature de `publier_fichiers`)
    - `notifier(titre: str, message: str)`

- [ ] **Step 1: Ajouter la configuration**

Dans `app/config.py`, à la suite de `publication_fraicheur_jours` :

```python
    # arret : l'agent ne fait rien. apercu : tout, jusqu'au conteneur cree chez
    # Meta, SAUF la publication. reel : publie. Publier est public et
    # irreversible : le passage a « reel » est une decision de l'utilisateur.
    publication_mode: Literal["arret", "apercu", "reel"] = "arret"
    # La composition (appel Claude paye) n'a lieu qu'a J-1 : on ne paie jamais
    # un post qui se fait doubler avant demain.
    publication_composition_heures: int = 24
    # Gel a T-2h : rendu et telechargements faits d'avance.
    publication_gel_heures: int = 2
    # Au-dela, on ne publie pas : un Mac rouvert le soir lacherait sinon la
    # journee entiere d'un coup.
    publication_retard_max_minutes: int = 90
```

Et en tête du fichier : `from typing import Literal`.

- [ ] **Step 2: Write the failing test**

```python
# tests/schedule/test_schedule_runner.py
"""Le passage de l'agent, avec une horloge injectee et des outils factices."""

import datetime as dt

import pytest

from app.config import settings
from app.content.compose import CompositionError, CoverVariante, PostDraft
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, PostStatus, Source, SourceType
from app.publish.instagram import ErreurInstagram
from app.publish.preparation import PreparationImpossible
from app.schedule.runner import tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import utc

BASE = dt.datetime(2026, 9, 20, 4, 0, tzinfo=dt.timezone.utc)
H0 = prochains_horaires(BASE, 1)[0]


def _draft():
    variante = CoverVariante(headline="**Accroche**", title="**Titre**", subtitle="**Sous**")
    return PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=["#electro"])


class Outils:
    def __init__(self):
        self.composes, self.prepares, self.publies, self.notifications = [], [], [], []
        self.erreur_composition = self.erreur_preparation = self.erreur_publication = None

    def composer(self, article):
        self.composes.append(article.id)
        if self.erreur_composition:
            raise self.erreur_composition
        return _draft()

    def preparer(self, post, article, dossier):
        self.prepares.append(post.id)
        if self.erreur_preparation:
            raise self.erreur_preparation
        return ["p_01.jpg"]

    def publier(self, dossier, noms, legende, publier_vraiment):
        self.publies.append((noms, publier_vraiment))
        if self.erreur_publication:
            raise self.erreur_publication
        return "ig42"

    def notifier(self, titre, message):
        self.notifications.append(message)

    def tourner(self, db, maintenant, mode="reel"):
        return tourner(db, maintenant, mode=mode, composer=self.composer, preparer=self.preparer,
                       publier=self.publier, notifier=self.notifier)


@pytest.fixture()
def article(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://t/1", title="Actu", raw_hash="h1",
                      relevance_score=90, hotness_score=30, published_at=BASE - dt.timedelta(hours=1),
                      media=[{"type": "photo", "url": "https://img/1.jpg"}])
    db_session.add(article)
    db_session.commit()
    return article


def _creneau_h0(db):
    return next(c for c in db.query(Creneau).all() if utc(c.publier_a) == H0)


def test_en_arret_l_agent_ne_fait_rien(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3), mode="arret")
    assert db_session.query(Creneau).count() == 0
    assert outils.composes == []


def test_la_chaine_complete_en_mode_reel(db_session, article):
    outils = Outils()

    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    assert outils.composes == [article.id]
    creneau = _creneau_h0(db_session)
    assert (creneau.etat, creneau.post_id is not None) == (EtatCreneau.PROGRAMME, True)
    assert outils.prepares == []

    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    assert (creneau.etat, creneau.fichiers) == (EtatCreneau.GELE, ["p_01.jpg"])

    rapport = outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    assert outils.publies == [(["p_01.jpg"], True)]
    assert (creneau.etat, creneau.ig_media_id, rapport.publies) == (EtatCreneau.PUBLIE, "ig42", 1)
    assert db_session.get(Post, creneau.post_id).status == PostStatus.PUBLISHED
    assert db_session.get(Article, article.id).status == ArticleStatus.USED


def test_en_apercu_rien_n_est_publie(db_session, article):
    outils = Outils()
    for decalage in (-180, -60, 20):
        outils.tourner(db_session, H0 + dt.timedelta(minutes=decalage), mode="apercu")
    creneau = _creneau_h0(db_session)
    assert outils.publies == [(["p_01.jpg"], False)]
    assert creneau.etat == EtatCreneau.APERCU
    assert db_session.get(Post, creneau.post_id).status == PostStatus.DRAFT
    assert db_session.get(Article, article.id).status == ArticleStatus.NEW


def test_au_dela_de_90_minutes_de_retard_on_ne_publie_pas(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    rapport = outils.tourner(db_session, H0 + dt.timedelta(minutes=120))
    assert outils.publies == []
    assert (_creneau_h0(db_session).etat, rapport.manques) == (EtatCreneau.MANQUE, 1)


def test_un_mac_reveille_en_retard_gele_et_publie_dans_le_meme_passage(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 + dt.timedelta(minutes=30))
    assert (len(outils.prepares), len(outils.publies)) == (1, 1)
    assert _creneau_h0(db_session).etat == EtatCreneau.PUBLIE


def test_une_publication_refusee_previent(db_session, article):
    outils = Outils()
    outils.erreur_publication = ErreurInstagram("jeton expire")
    for decalage in (-180, -60, 20):
        outils.tourner(db_session, H0 + dt.timedelta(minutes=decalage))
    creneau = _creneau_h0(db_session)
    assert creneau.etat == EtatCreneau.ECHEC
    assert "jeton expire" in creneau.erreur
    assert len(outils.notifications) == 1 and "jeton expire" in outils.notifications[0]


def test_une_composition_impossible_exclut_l_article(db_session, article):
    outils = Outils()
    outils.erreur_composition = CompositionError("sortie invalide")
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    assert "composition" in db_session.get(Article, article.id).file_exclu_raison
    creneau = _creneau_h0(db_session)
    assert (creneau.etat, creneau.article_id) == (EtatCreneau.LIBRE, None)


def test_une_panne_de_credits_n_exclut_pas_l_article(db_session, article):
    outils = Outils()
    outils.erreur_composition = RuntimeError("Your credit balance is too low")
    rapport = outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    assert db_session.get(Article, article.id).file_exclu_raison is None
    assert "credit" in _creneau_h0(db_session).erreur
    assert rapport.erreurs


def test_un_gel_impossible_exclut_l_article_et_libere_le_creneau(db_session, article):
    outils = Outils()
    outils.erreur_preparation = PreparationImpossible("accroche trop longue : la cover deborde")
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    assert "deborde" in db_session.get(Article, article.id).file_exclu_raison
    assert _creneau_h0(db_session).etat == EtatCreneau.LIBRE


def test_on_ne_compose_que_les_creneaux_a_moins_de_24_heures(db_session, article):
    for n in range(2, 9):
        db_session.add(Article(source_id=article.source_id, url=f"https://t/{n}", title=f"Actu {n}",
                               raw_hash=f"h{n}", relevance_score=80, published_at=BASE,
                               media=[{"type": "photo", "url": f"https://img/{n}.jpg"}]))
    db_session.commit()
    outils = Outils()
    maintenant = H0 - dt.timedelta(hours=3)
    outils.tourner(db_session, maintenant)
    attendus = [h for h in prochains_horaires(maintenant, 6) if h <= maintenant + dt.timedelta(hours=24)]
    assert 0 < len(outils.composes) == len(attendus) < 6
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/schedule/test_schedule_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schedule.runner'`

- [ ] **Step 4: Write minimal implementation**

```python
# app/schedule/runner.py
"""Un passage de l'agent de publication, toutes les 10 minutes.

Dans l'ordre :
1. les creneaux depasses de plus de 90 minutes passent en MANQUE -- sans ce
   garde-fou, un Mac rouvert le soir publierait la journee entiere d'un coup ;
2. l'arbitrage reattribue les creneaux libres ;
3. composition (appel Claude paye) des creneaux a moins de 24 h ;
4. gel des creneaux a moins de 2 h : rendu et telechargements sur disque ;
5. publication des creneaux geles dont l'heure est venue.

Un article en defaut (composition ou gel impossible) est EXCLU de la file et son
creneau libere. Une panne systemique (credits, reseau) n'exclut rien : la faute
n'est pas a l'article.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.content.compose import CompositionError, create_post
from app.content.legende import texte_legende
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, PostStatus
from app.schedule.store import appliquer_arbitrage, post_a_reprendre, utc

logger = logging.getLogger(__name__)

MODES = ("arret", "apercu", "reel")


@dataclass
class RapportPublication:
    composes: int = 0
    geles: int = 0
    publies: int = 0
    apercus: int = 0
    manques: int = 0
    echecs: int = 0
    erreurs: list[str] = field(default_factory=list)


def _heure_locale(moment: dt.datetime) -> str:
    return utc(moment).astimezone(ZoneInfo(settings.publication_fuseau)).strftime("%d/%m %H:%M")


def _liberer(creneau: Creneau, raison: str) -> None:
    creneau.article_id = None
    creneau.post_id = None
    creneau.etat = EtatCreneau.LIBRE
    creneau.erreur = raison[:1000]


def _exclure(db, creneau: Creneau, raison: str, rapport: RapportPublication) -> None:
    article = db.get(Article, creneau.article_id) if creneau.article_id else None
    if article is not None:
        article.file_exclu_raison = raison[:500]
    rapport.erreurs.append(f"{_heure_locale(creneau.publier_a)} : {raison}")
    _liberer(creneau, raison)
    db.commit()


def _marquer_manques(db, maintenant: dt.datetime, rapport: RapportPublication) -> None:
    limite = maintenant - dt.timedelta(minutes=settings.publication_retard_max_minutes)
    en_retard = db.scalars(
        select(Creneau).where(
            Creneau.publier_a < limite,
            Creneau.etat.in_([EtatCreneau.LIBRE, EtatCreneau.PROGRAMME, EtatCreneau.GELE]),
        )
    ).all()
    for creneau in en_retard:
        if creneau.article_id is not None:
            rapport.manques += 1
        creneau.etat = EtatCreneau.MANQUE
    db.commit()


def _composer(db, maintenant: dt.datetime, composer, rapport: RapportPublication) -> bool:
    horizon = maintenant + dt.timedelta(hours=settings.publication_composition_heures)
    creneaux = db.scalars(
        select(Creneau)
        .where(
            Creneau.etat == EtatCreneau.PROGRAMME,
            Creneau.post_id.is_(None),
            Creneau.article_id.is_not(None),
            Creneau.publier_a <= horizon,
        )
        .order_by(Creneau.publier_a)
    ).all()
    exclusions = False
    for creneau in creneaux:
        article = db.get(Article, creneau.article_id)
        if article is None:
            _liberer(creneau, "article purge")
            db.commit()
            continue
        post_id = post_a_reprendre(db, article.id)
        if post_id is None:
            try:
                draft = composer(article)
            except CompositionError as exc:
                _exclure(db, creneau, f"composition impossible : {exc}", rapport)
                exclusions = True
                continue
            except Exception as exc:  # noqa: BLE001 -- credits, reseau : panne systemique
                creneau.erreur = f"composition interrompue : {exc}"[:1000]
                rapport.erreurs.append(creneau.erreur)
                db.commit()
                # Inutile d'enchainer les appels sur une panne qui touchera tous les articles.
                break
            post_id = create_post(db, article, draft).id
            rapport.composes += 1
        creneau.post_id = post_id
        creneau.erreur = None
        db.commit()
    return exclusions


def _geler(db, maintenant: dt.datetime, preparer, rapport: RapportPublication) -> bool:
    seuil = maintenant + dt.timedelta(hours=settings.publication_gel_heures)
    creneaux = db.scalars(
        select(Creneau)
        .where(
            Creneau.etat == EtatCreneau.PROGRAMME,
            Creneau.post_id.is_not(None),
            Creneau.publier_a <= seuil,
        )
        .order_by(Creneau.publier_a)
    ).all()
    exclusions = False
    for creneau in creneaux:
        post = db.get(Post, creneau.post_id)
        if post is None:
            _liberer(creneau, "post supprime")
            db.commit()
            continue
        article = db.get(Article, creneau.article_id) if creneau.article_id else None
        try:
            noms = preparer(post, article, settings.publication_dir / str(creneau.id))
        except Exception as exc:  # noqa: BLE001 -- tout echec de gel exclut l'article
            _exclure(db, creneau, f"gel impossible : {exc}", rapport)
            exclusions = True
            continue
        creneau.fichiers = noms
        creneau.etat = EtatCreneau.GELE
        creneau.gele_le = maintenant
        creneau.erreur = None
        db.commit()
        rapport.geles += 1
    return exclusions


def _publier(db, maintenant: dt.datetime, mode: str, publier, notifier, rapport: RapportPublication) -> None:
    creneaux = db.scalars(
        select(Creneau)
        .where(Creneau.etat == EtatCreneau.GELE, Creneau.publier_a <= maintenant)
        .order_by(Creneau.publier_a)
    ).all()
    for creneau in creneaux:
        post = db.get(Post, creneau.post_id) if creneau.post_id else None
        try:
            if post is None:
                raise RuntimeError("post supprime apres le gel")
            identifiant = publier(
                settings.publication_dir / str(creneau.id),
                creneau.fichiers or [],
                texte_legende(post),
                mode == "reel",
            )
        except Exception as exc:  # noqa: BLE001 -- tout echec doit se voir
            creneau.etat = EtatCreneau.ECHEC
            creneau.erreur = str(exc)[:1000]
            db.commit()
            rapport.echecs += 1
            notifier("Kinda", f"publication de {_heure_locale(creneau.publier_a)} en echec : {exc}")
            continue
        creneau.ig_media_id = identifiant
        creneau.publie_le = maintenant
        creneau.erreur = None
        if mode == "reel":
            creneau.etat = EtatCreneau.PUBLIE
            post.status = PostStatus.PUBLISHED
            post.published_at = maintenant
            article = db.get(Article, post.article_id) if post.article_id else None
            if article is not None:
                # USED protege l'article de la purge et l'ecarte de la file.
                article.status = ArticleStatus.USED
            rapport.publies += 1
        else:
            creneau.etat = EtatCreneau.APERCU
            rapport.apercus += 1
        db.commit()


def tourner(db, maintenant: dt.datetime, *, mode: str, composer, preparer, publier, notifier) -> RapportPublication:
    if mode not in MODES:
        raise ValueError(f"mode de publication inconnu : {mode!r}, attendu {MODES}")
    rapport = RapportPublication()
    if mode == "arret":
        return rapport
    _marquer_manques(db, maintenant, rapport)
    appliquer_arbitrage(db, maintenant)
    exclusions = _composer(db, maintenant, composer, rapport)
    exclusions = _geler(db, maintenant, preparer, rapport) or exclusions
    if exclusions:
        # Le creneau libere se remplit tout de suite ; sa composition viendra au
        # passage suivant.
        appliquer_arbitrage(db, maintenant)
    _publier(db, maintenant, mode, publier, notifier, rapport)
    return rapport
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/schedule/test_schedule_runner.py -v`
Expected: 10 passed

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/schedule/runner.py tests/schedule/test_schedule_runner.py
git commit -F - <<'EOF'
feat(file): un passage de l'agent, de l'arbitrage a la publication

Composition a J-1, gel a T-2h, publication dans les 90 minutes ou jamais.
Un article en defaut est exclu, une panne systemique n'exclut rien, un echec
de publication previent. Trois modes : arret, apercu, reel.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 12: L'agent de publication

**Files:**
- Create: `scripts/run_publication.py`
- Modify: `scripts/install_agents.py`
- Modify: `CLAUDE.md`
- Test: `tests/scripts/test_run_publication.py`, `tests/scripts/test_install_agents.py`

**Interfaces:**
- Consumes: `tourner`, `RapportPublication` (tâche 11) ; `rafraichir_si_necessaire`, `alerte_expiration`, `enregistrer`, `JetonIndisponible` (tâche 2) ; `preparer_fichiers` (tâche 5) ; `publier_fichiers` (tâche 6) ; `make_claude_composer` (`app/content/compose.py`) ; `notifier` (`app/delivery/notify.py`)
- Produces:
  - `surveiller_jeton(maintenant: datetime, notifier=notifier) -> None`
  - `main() -> None`
  - dans `scripts/install_agents.py` : `LABEL_PUBLICATION = "com.musicnewsradar.publication"`, `INTERVALLE_PUBLICATION = 600`, `plist_publication(chemins: Chemins) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/scripts/test_run_publication.py
import datetime as dt

import scripts.run_publication as script
from app.config import settings
from app.publish.jeton import EtatJeton, charger, enregistrer

MAINTENANT = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.timezone.utc)


def _jeton(tmp_path, monkeypatch, expire_dans_jours):
    chemin = tmp_path / "jeton.json"
    monkeypatch.setattr(settings, "meta_token_path", chemin)
    # Rafraichi la veille : aucun appel reseau ne part.
    enregistrer(chemin, EtatJeton(
        "IGAAx",
        rafraichi_le=MAINTENANT - dt.timedelta(days=1),
        expire_le=MAINTENANT + dt.timedelta(days=expire_dans_jours),
    ))
    return chemin


def test_l_expiration_proche_ne_previent_qu_une_fois_par_jour(tmp_path, monkeypatch):
    """L'agent passe toutes les 10 minutes : sans cette memoire, 144 notifications par jour."""
    chemin = _jeton(tmp_path, monkeypatch, expire_dans_jours=3)
    notifications = []
    for _ in range(3):
        script.surveiller_jeton(MAINTENANT, notifier=lambda titre, message: notifications.append(message))
    assert len(notifications) == 1
    assert charger(chemin).alerte_envoyee_le == MAINTENANT.date()


def test_un_jeton_sain_ne_previent_pas(tmp_path, monkeypatch):
    _jeton(tmp_path, monkeypatch, expire_dans_jours=40)
    notifications = []
    script.surveiller_jeton(MAINTENANT, notifier=lambda titre, message: notifications.append(message))
    assert notifications == []


def test_sans_aucun_jeton_l_agent_le_dit_sans_planter(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(settings, "meta_token_path", tmp_path / "absent.json")
    monkeypatch.setattr(settings, "meta_access_token", None)
    script.surveiller_jeton(MAINTENANT, notifier=lambda titre, message: None)
    assert "META_ACCESS_TOKEN" in capsys.readouterr().out


def test_en_arret_l_agent_ne_touche_meme_pas_la_base(monkeypatch):
    def interdit():
        raise AssertionError("la base ne doit pas etre sollicitee en mode arret")

    monkeypatch.setattr(settings, "publication_mode", "arret")
    monkeypatch.setattr(script, "attendre_base", interdit)
    script.main()
```

Dans `tests/scripts/test_install_agents.py`, ajouter `INTERVALLE_PUBLICATION`, `LABEL_PUBLICATION` et `plist_publication` à l'import depuis `scripts.install_agents`, puis à la fin du fichier :

```python
def test_l_agent_de_publication_passe_toutes_les_dix_minutes(chemins):
    """Une precision de +/- 10 minutes sur l'heure de publication, sans rien
    couter : l'agent ne score rien et ne compose qu'a J-1."""
    p = plist_publication(chemins)
    assert p["Label"] == LABEL_PUBLICATION
    assert "scripts.run_publication" in p["ProgramArguments"]
    assert p["StartInterval"] == INTERVALLE_PUBLICATION == 600


def test_l_agent_de_publication_est_installe_et_desinstalle_avec_les_autres(chemins):
    assert LABEL_PUBLICATION in plists(chemins)
    executeur = FauxExecuteur()
    installer(chemins, executer=executeur)
    executeur.commandes.clear()
    desinstaller(chemins, executer=executeur)
    assert LABEL_PUBLICATION in set(_labels_de(executeur.commandes, "bootout"))
    assert not (chemins.launch_agents / f"{LABEL_PUBLICATION}.plist").exists()


def test_la_rotation_couvre_les_logs_de_publication(chemins):
    conf = conf_rotation(chemins)
    for suffixe in (".log", ".err.log"):
        assert str(chemins.logs / f"publication{suffixe}") in conf
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scripts/test_run_publication.py tests/scripts/test_install_agents.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.run_publication'` et `ImportError` sur `LABEL_PUBLICATION`

- [ ] **Step 3: Write the entry point**

```python
# scripts/run_publication.py
"""Agent de publication (launchd, toutes les 10 minutes).

Usage :
    uv run python -m scripts.run_publication

En mode `arret` (defaut), il sort immediatement sans toucher la base. Sinon il
surveille le jeton Instagram, puis fait un passage de la file.
"""

import datetime as dt
import logging

from app.config import settings
from app.content.compose import make_claude_composer
from app.db import BaseInjoignable, SessionLocal, attendre_base
from app.delivery.notify import notifier
from app.publish.jeton import JetonIndisponible, alerte_expiration, enregistrer, rafraichir_si_necessaire
from app.publish.preparation import preparer_fichiers
from app.publish.publier import publier_fichiers
from app.schedule.runner import tourner


def surveiller_jeton(maintenant: dt.datetime, notifier=notifier) -> None:
    try:
        etat, erreur = rafraichir_si_necessaire(settings.meta_token_path, maintenant)
    except JetonIndisponible as exc:
        # Pas de notification ici : la publication echouera et previendra, elle.
        print(f"[ERREUR] {exc}")
        return
    alerte = erreur or alerte_expiration(etat, maintenant)
    if alerte and etat.alerte_envoyee_le != maintenant.date():
        notifier("Kinda", alerte)
        etat.alerte_envoyee_le = maintenant.date()
        enregistrer(settings.meta_token_path, etat)


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    mode = settings.publication_mode
    if mode == "arret":
        return
    # Lance par launchd, parfois au reveil du Mac avant que le DNS reponde.
    try:
        attendre_base()
    except BaseInjoignable as exc:
        print(f"[ERREUR] {exc}")
        raise SystemExit(1) from exc

    maintenant = dt.datetime.now(dt.timezone.utc)
    surveiller_jeton(maintenant)
    with SessionLocal() as db:
        rapport = tourner(
            db, maintenant, mode=mode, composer=make_claude_composer(),
            preparer=preparer_fichiers, publier=publier_fichiers, notifier=notifier,
        )

    compteurs = {
        "composes": rapport.composes, "geles": rapport.geles, "publies": rapport.publies,
        "apercus": rapport.apercus, "manques": rapport.manques, "echecs": rapport.echecs,
    }
    # Silence quand rien ne se passe : le log ne grossit pas de 144 lignes vides par jour.
    if any(compteurs.values()) or rapport.erreurs:
        resume = ", ".join(f"{valeur} {nom}" for nom, valeur in compteurs.items() if valeur)
        print(f"[{maintenant:%d/%m %H:%M}] publication ({mode}) : {resume or 'rien'}")
        for erreur in rapport.erreurs:
            print(f"  [ERREUR] {erreur}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add the third agent**

Dans `scripts/install_agents.py` :

1. Sous `LABEL_DASHBOARD` : `LABEL_PUBLICATION = "com.musicnewsradar.publication"`
2. Sous `INTERVALLE_PIPELINE` :

```python
# Dix minutes : c'est la precision de l'heure de publication. L'agent ne coute
# rien a ce rythme -- il ne score pas, et ne compose qu'a J-1.
INTERVALLE_PUBLICATION = 600
```

3. `BASES_LOG = ("pipeline", "dashboard", "publication")`
4. Après `plist_dashboard` :

```python
def plist_publication(chemins: Chemins) -> dict:
    """La file de publication. En mode `arret` (defaut), le script sort
    immediatement : l'installer ne publie rien tant que l'utilisateur n'a pas
    choisi `apercu` ou `reel` dans .env."""
    return {
        **_commun(chemins, LABEL_PUBLICATION, "publication"),
        "ProgramArguments": [
            str(chemins.uv), "run", "python", "-m", "scripts.run_publication",
        ],
        "StartInterval": INTERVALLE_PUBLICATION,
        "RunAtLoad": True,
    }
```

5. Dans `plists()`, ajouter `LABEL_PUBLICATION: plist_publication(chemins),`.
6. Dans `desinstaller()`, remplacer `for label in (LABEL_PIPELINE, LABEL_DASHBOARD, *LABELS_OBSOLETES):` par `for label in (*plists(chemins), *LABELS_OBSOLETES):`.
7. Dans `statut()`, remplacer `for label in (LABEL_PIPELINE, LABEL_DASHBOARD):` par `for label in plists(chemins):`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/scripts/test_run_publication.py tests/scripts/test_install_agents.py -v`
Expected: tout passe

- [ ] **Step 6: Documenter dans CLAUDE.md**

Dans la liste des agents (section « Scheduler »), après l'entrée `com.musicnewsradar.dashboard` :

```markdown
  - `com.musicnewsradar.publication` — `run_publication` toutes les **10 minutes** :
    la file de publication Instagram. Sort immédiatement en mode `arret`, le défaut
    (`PUBLICATION_MODE` dans `.env`).
```

Juste avant `## Illustration des articles` :

```markdown
## Publication automatique (`app/publish/`, `app/schedule/`)

Kinda publie seul deux carrousels par jour, choisis dans le flux scoré. Pas de reel :
aucune source ne fournit plus de vidéo (X à sec, source Instagram abandonnée).
Design : `docs/superpowers/specs/2026-09-11-publication-meta-design.md`, révision du
2026-09-16.

**Connexion Instagram, sans Facebook.** App Meta « Kinda Radar », compte
`kindanewsfrance` déclaré testeur : une app non revue publie sur ses comptes
testeurs, ce qui suffit. Jeton `IG…` de 60 jours, rafraîchi chaque semaine dans
`meta_token_path` (droits 600) ; `.env` n'est que la valeur initiale. Alerte à J-7.

**La file.** 6 créneaux (3 jours × 2), heure tirée dans 08:30-11:00 et 17:00-19:30
(Paris) avec une graine dérivée de la date — sinon l'heure glisserait à chaque
passage. Le coût dicte le calendrier : composition Claude à J-1, gel (rendu et
téléchargements) à T-2h, publication à l'heure. **Au-delà de 90 minutes de retard,
on ne publie pas** : un Mac rouvert le soir lâcherait sinon la journée d'un coup.

**Trois modes** (`PUBLICATION_MODE`) : `arret`, `apercu` (tout, y compris le
conteneur chez Meta, sauf `media_publish`), `reel`. Publier est public et
irréversible : passer à `reel` est une décision de l'utilisateur. En `apercu`, un
article reste éligible et revient donc sur les créneaux suivants.

**Règles Meta vérifiées dans la documentation (2026-09-16)** : JPEG uniquement,
8 Mo, 320 à 1440 px de large, proportions 4:5 à 1,91:1, carrousel de 10, légende de
2200 caractères et 30 hashtags. La cover `kinda_post` (3:4) est donc recadrée au
centre en 1080×1350 par `app/publish/images.py` (`publication_ratio_min`).

**Hébergement éphémère.** Meta télécharge les images en cURL. Au moment de publier
seulement, un mini-serveur local sert le dossier du post (sans listing) et un tunnel
rapide `cloudflared` l'expose ; tout est fermé après `media_publish`. Le dashboard
n'est jamais exposé. `cloudflared` est cherché hors du PATH de launchd, comme ffmpeg.

**Exclusion.** Un article dont la composition ou le gel échoue, ou que l'utilisateur
« saute », reçoit `articles.file_exclu_raison` et ne revient plus. Une panne
systémique (crédits Anthropic, réseau) n'exclut rien.

Essai sans publier : `uv run python -m scripts.diagnostic.tester_publication --post <ID>`.
```

- [ ] **Step 7: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 8: Commit**

```bash
git add scripts/run_publication.py scripts/install_agents.py CLAUDE.md tests/scripts/test_run_publication.py tests/scripts/test_install_agents.py
git commit -F - <<'EOF'
feat(publication): agent launchd de publication, toutes les dix minutes

Surveille le jeton (alerte une fois par jour), fait un passage de la file, et
sort sans toucher la base en mode arret, le defaut. Documente dans CLAUDE.md.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 13: L'API de la file

**Files:**
- Create: `app/api/schedule.py`
- Modify: `app/schemas.py`
- Modify: `app/main.py`
- Test: `tests/api/test_schedule_api.py`

**Interfaces:**
- Consumes: `Creneau`, `EtatCreneau` (tâche 7) ; `MODIFIABLES`, `appliquer_arbitrage`, `post_a_reprendre`, `utc` (tâche 10)
- Produces:
  - `GET /schedule` → `list[CreneauOut]` (créneaux depuis 24 h, triés)
  - `POST /schedule/rebuild` → `list[CreneauOut]`
  - `PATCH /schedule/{creneau_id}` avec **une seule** action parmi `{"epingle": bool}`, `{"article_id": int}`, `{"sauter": true}`, `{"decaler": true}` → `CreneauOut` ; 409 si le créneau est gelé ou passé ; 422 si zéro ou plusieurs actions
  - schémas `CreneauOut`, `CreneauUpdate`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_schedule_api.py
import datetime as dt

from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Source, SourceType


def _maintenant():
    return dt.datetime.now(dt.timezone.utc)


def _articles(db, *pertinences):
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db.add(source)
    db.commit()
    articles = []
    for n, relevance in enumerate(pertinences):
        article = Article(source_id=source.id, url=f"https://t/{n}", title=f"Actu {n}", raw_hash=f"h{n}",
                          relevance_score=relevance, published_at=_maintenant(),
                          media=[{"type": "photo", "url": f"https://img/{n}.jpg"}])
        db.add(article)
        articles.append(article)
    db.commit()
    return articles


def _creneau(db, heures, article=None, etat=None):
    creneau = Creneau(
        publier_a=_maintenant() + dt.timedelta(hours=heures),
        article_id=article.id if article else None,
        etat=etat or (EtatCreneau.PROGRAMME if article else EtatCreneau.LIBRE),
    )
    db.add(creneau)
    db.commit()
    return creneau


def test_la_file_montre_l_article_de_chaque_creneau(client, db_session):
    (article,) = _articles(db_session, 80)
    _creneau(db_session, 5, article)
    (vue,) = client.get("/schedule").json()
    assert (vue["article_title"], vue["relevance"], vue["etat"]) == ("Actu 0", 80, "programme")


def test_epingler(client, db_session):
    (article,) = _articles(db_session, 80)
    creneau = _creneau(db_session, 5, article)
    assert client.patch(f"/schedule/{creneau.id}", json={"epingle": True}).json()["epingle"] is True


def test_remplacer_epingle_et_libere_l_ancien_creneau_de_l_article(client, db_session):
    a, b = _articles(db_session, 80, 70)
    premier, second = _creneau(db_session, 5, a), _creneau(db_session, 17, b)
    vue = client.patch(f"/schedule/{second.id}", json={"article_id": a.id}).json()
    assert (vue["article_id"], vue["epingle"]) == (a.id, True)
    db_session.refresh(premier)
    assert (premier.article_id, premier.etat) == (None, EtatCreneau.LIBRE)


def test_sauter_exclut_l_article_de_la_file(client, db_session):
    (article,) = _articles(db_session, 80)
    creneau = _creneau(db_session, 5, article)
    vue = client.patch(f"/schedule/{creneau.id}", json={"sauter": True}).json()
    assert vue["article_id"] is None
    db_session.refresh(article)
    assert article.file_exclu_raison == "saute depuis le dashboard"


def test_decaler_echange_avec_le_creneau_suivant_et_epingle(client, db_session):
    a, b = _articles(db_session, 80, 70)
    premier, second = _creneau(db_session, 5, a), _creneau(db_session, 17, b)
    vue = client.patch(f"/schedule/{premier.id}", json={"decaler": True}).json()
    assert (vue["article_id"], vue["epingle"]) == (b.id, True)
    db_session.refresh(second)
    assert (second.article_id, second.epingle) == (a.id, True)


def test_un_creneau_gele_ne_se_modifie_plus(client, db_session):
    (article,) = _articles(db_session, 80)
    creneau = _creneau(db_session, 1, article, etat=EtatCreneau.GELE)
    assert client.patch(f"/schedule/{creneau.id}", json={"epingle": True}).status_code == 409


def test_une_seule_action_a_la_fois(client, db_session):
    (article,) = _articles(db_session, 80)
    creneau = _creneau(db_session, 5, article)
    reponse = client.patch(f"/schedule/{creneau.id}", json={"epingle": True, "sauter": True})
    assert reponse.status_code == 422


def test_decaler_le_dernier_creneau_est_refuse(client, db_session):
    (article,) = _articles(db_session, 80)
    creneau = _creneau(db_session, 5, article)
    assert client.patch(f"/schedule/{creneau.id}", json={"decaler": True}).status_code == 409


def test_remplacer_par_un_article_deja_publie_est_refuse(client, db_session):
    (article,) = _articles(db_session, 80)
    article.status = ArticleStatus.USED
    db_session.commit()
    creneau = _creneau(db_session, 5)
    assert client.patch(f"/schedule/{creneau.id}", json={"article_id": article.id}).status_code == 409


def test_recalculer_cree_la_file(client, db_session):
    reponse = client.post("/schedule/rebuild")
    assert reponse.status_code == 200
    assert len(reponse.json()) == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_schedule_api.py -v`
Expected: FAIL — 404 sur `/schedule`

- [ ] **Step 3: Write the schemas, the router and wire it**

Dans `app/schemas.py`, compléter l'import : `from app.models import ArticleStatus, EtatCreneau, PostStatus, SourceType`, puis à la fin du fichier :

```python
class CreneauOut(BaseModel):
    id: int
    publier_a: dt.datetime
    etat: EtatCreneau
    epingle: bool
    article_id: int | None
    article_title: str | None = None
    relevance: int | None = None
    post_id: int | None
    ig_media_id: str | None
    erreur: str | None


class CreneauUpdate(BaseModel):
    """Une seule action par appel : le routeur refuse en 422 zero ou plusieurs."""

    epingle: bool | None = None
    article_id: int | None = None
    sauter: bool = False
    decaler: bool = False
```

```python
# app/api/schedule.py
"""La file de publication, pilotable depuis le dashboard.

Seuls les creneaux LIBRE ou PROGRAMME et encore a venir se modifient : un creneau
gele a deja ses fichiers prets, et changer son article a T-2h publierait autre
chose que ce qui a ete fige. Toute action manuelle EPINGLE le creneau, sinon
l'arbitrage suivant la defait dans les 10 minutes.
"""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Article, ArticleStatus, Creneau, EtatCreneau
from app.schedule.store import MODIFIABLES, appliquer_arbitrage, post_a_reprendre, utc
from app.schemas import CreneauOut, CreneauUpdate

router = APIRouter(tags=["schedule"])


def _maintenant() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _vue(db: Session, creneau: Creneau) -> CreneauOut:
    article = db.get(Article, creneau.article_id) if creneau.article_id else None
    return CreneauOut(
        id=creneau.id,
        publier_a=utc(creneau.publier_a),
        etat=creneau.etat,
        epingle=creneau.epingle,
        article_id=creneau.article_id,
        article_title=article.title if article else None,
        relevance=article.relevance_score if article else None,
        post_id=creneau.post_id,
        ig_media_id=creneau.ig_media_id,
        erreur=creneau.erreur,
    )


def _liste(db: Session, maintenant: dt.datetime) -> list[CreneauOut]:
    depuis = maintenant - dt.timedelta(hours=24)
    creneaux = db.scalars(select(Creneau).where(Creneau.publier_a >= depuis).order_by(Creneau.publier_a))
    return [_vue(db, creneau) for creneau in creneaux]


def _reprogrammer(creneau: Creneau) -> None:
    creneau.etat = EtatCreneau.PROGRAMME if creneau.article_id else EtatCreneau.LIBRE
    creneau.epingle = creneau.article_id is not None


@router.get("/schedule", response_model=list[CreneauOut])
def lister_file(db: Session = Depends(get_db)):
    return _liste(db, _maintenant())


@router.post("/schedule/rebuild", response_model=list[CreneauOut])
def recalculer_file(db: Session = Depends(get_db)):
    maintenant = _maintenant()
    appliquer_arbitrage(db, maintenant)
    return _liste(db, maintenant)


@router.patch("/schedule/{creneau_id}", response_model=CreneauOut)
def modifier_creneau(creneau_id: int, payload: CreneauUpdate, db: Session = Depends(get_db)):
    creneau = db.get(Creneau, creneau_id)
    if creneau is None:
        raise HTTPException(status_code=404, detail="Creneau introuvable")
    maintenant = _maintenant()
    if creneau.etat not in MODIFIABLES or utc(creneau.publier_a) <= maintenant:
        raise HTTPException(status_code=409, detail="Creneau gele ou passe : il ne se modifie plus")
    actions = [payload.epingle is not None, payload.article_id is not None, payload.sauter, payload.decaler]
    if sum(actions) != 1:
        raise HTTPException(status_code=422, detail="Une seule action a la fois : epingle, article_id, sauter ou decaler")

    if payload.epingle is not None:
        creneau.epingle = payload.epingle
    elif payload.article_id is not None:
        article = db.get(Article, payload.article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="Article introuvable")
        if article.status == ArticleStatus.USED:
            raise HTTPException(status_code=409, detail="Article deja publie")
        # Un article n'occupe qu'un creneau a la fois.
        for autre in db.scalars(
            select(Creneau).where(
                Creneau.article_id == article.id,
                Creneau.id != creneau.id,
                Creneau.etat.in_(MODIFIABLES),
                Creneau.publier_a > maintenant,
            )
        ):
            autre.article_id = None
            autre.post_id = None
            _reprogrammer(autre)
        # Le choisir a la main le reintegre s'il avait ete exclu.
        article.file_exclu_raison = None
        creneau.article_id = article.id
        creneau.post_id = post_a_reprendre(db, article.id)
        _reprogrammer(creneau)
    elif payload.sauter:
        article = db.get(Article, creneau.article_id) if creneau.article_id else None
        if article is not None:
            article.file_exclu_raison = "saute depuis le dashboard"
        creneau.article_id = None
        creneau.post_id = None
        creneau.etat = EtatCreneau.LIBRE
        creneau.epingle = False
    else:
        suivant = db.scalar(
            select(Creneau)
            .where(Creneau.publier_a > creneau.publier_a, Creneau.etat.in_(MODIFIABLES))
            .order_by(Creneau.publier_a)
            .limit(1)
        )
        if suivant is None:
            raise HTTPException(status_code=409, detail="Aucun creneau modifiable apres celui-ci")
        creneau.article_id, suivant.article_id = suivant.article_id, creneau.article_id
        creneau.post_id, suivant.post_id = suivant.post_id, creneau.post_id
        _reprogrammer(creneau)
        _reprogrammer(suivant)

    db.commit()
    vue = _vue(db, creneau)
    if payload.sauter:
        # Le creneau libere se remplit tout de suite avec le candidat suivant.
        appliquer_arbitrage(db, maintenant)
    return vue
```

Dans `app/main.py` : `from app.api import articles, artists, digests, posts, schedule, sources` et `app.include_router(schedule.router)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_schedule_api.py -v`
Expected: 10 passed

- [ ] **Step 5: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 6: Commit**

```bash
git add app/api/schedule.py app/schemas.py app/main.py tests/api/test_schedule_api.py
git commit -F - <<'EOF'
feat(file): API de la file, epingler, remplacer, decaler, sauter

Seuls les creneaux libres ou programmes et a venir se modifient, une action a
la fois, et toute action manuelle epingle le creneau pour que l'arbitrage ne
la defasse pas.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

### Task 14: Le panneau « Programmation » du dashboard

**Files:**
- Modify: `app/static/dashboard.html`
- Test: `tests/api/test_api.py`

**Interfaces:**
- Consumes: `GET /schedule`, `POST /schedule/rebuild`, `PATCH /schedule/{id}` (tâche 13)
- Produces: un chip `data-preset="schedule"` et une section `#schedule-panel`

- [ ] **Step 1: Write the failing test**

À la fin de `tests/api/test_api.py` :

```python
def test_le_dashboard_porte_le_panneau_de_programmation(client):
    html = client.get("/").text
    assert 'data-preset="schedule"' in html
    assert 'id="schedule-panel"' in html
    assert 'fetch("/schedule")' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_api.py::test_le_dashboard_porte_le_panneau_de_programmation -v`
Expected: FAIL — `AssertionError`

- [ ] **Step 3: Write the panel**

Dans `app/static/dashboard.html` :

1. CSS, juste après la règle `#posts-panel { … }` :

```css
  #schedule-panel { max-width: 900px; margin: 0 auto; padding: 20px; }
  .slot-actions { display: flex; gap: 4px; }
  .slot-actions button { background: none; border: 1px solid #2c2c38; border-radius: 6px; cursor: pointer; padding: 2px 6px; }
  .status-pill.libre { background: #23232d; color: var(--muted); }
  .status-pill.programme { background: #241f3d; color: #b9a6ff; }
  .status-pill.gele { background: #1f2f3d; color: #8fd0ff; }
  .status-pill.publie { background: #1d3325; color: #7ee2a0; }
  .status-pill.apercu { background: #33301d; color: #e8d27a; }
  .status-pill.echec { background: #3d1f23; color: #ff9aa5; }
  .status-pill.manque { background: #2d2323; color: #c9a3a3; }
```

2. Chip, juste avant `<button class="chip" data-preset="posts">🖼 Posts</button>` :

```html
    <button class="chip" data-preset="schedule">🗓 Programmation</button>
```

3. Section, juste après la fermeture de `<section id="posts-panel" …>…</section>` :

```html
<section id="schedule-panel" hidden>
  <div class="posts-bar">
    <button id="schedule-rebuild">🔄 Recalculer la file</button>
  </div>
  <div id="schedule-list"></div>
</section>
```

4. Dans le gestionnaire des chips, déclarer `const schedulePanelEl = document.getElementById("schedule-panel");` à côté de `postsPanelEl`, ajouter `schedulePanelEl.hidden = true;` à la ligne qui masque les autres panneaux, et une branche :

```js
  } else if (preset === "schedule") {
    schedulePanelEl.hidden = false;
    loadSchedulePanel();
```

avant le `} else {` final.

5. Fonctions, juste avant `async function loadPostsPanel()` :

```js
// --- Panneau Programmation ---

const ETATS_CRENEAU = {
  libre: "Libre", programme: "Programmé", gele: "Gelé", publie: "Publié",
  apercu: "Aperçu, non publié", echec: "Échec", manque: "Manqué",
};
const CRENEAUX_MODIFIABLES = new Set(["libre", "programme"]);

async function loadSchedulePanel() {
  const list = document.getElementById("schedule-list");
  list.innerHTML = "Chargement…";
  let creneaux;
  try {
    const reponse = await fetch("/schedule");
    if (!reponse.ok) throw new Error(await reponse.text());
    creneaux = await reponse.json();
  } catch (erreur) {
    list.innerHTML = '<div id="empty">Échec du chargement de la file.</div>';
    document.getElementById("count").textContent = "";
    return;
  }
  document.getElementById("count").textContent = `${creneaux.length} créneau${creneaux.length > 1 ? "x" : ""}`;
  if (!creneaux.length) {
    list.innerHTML = '<div id="empty">Aucun créneau : cliquez sur « Recalculer la file ».</div>';
    return;
  }
  list.innerHTML = "";
  for (const c of creneaux) {
    const row = document.createElement("div");
    row.className = "post-row";
    row.innerHTML = `
      <div class="grow"><div class="hl"></div><div class="sub"></div></div>
      <span class="status-pill"></span>
      <span class="slot-actions"></span>`;
    row.querySelector(".hl").textContent = c.article_title || "(créneau libre)";
    const details = [fmtDate(c.publier_a)];
    if (c.relevance != null) details.push(`pertinence ${c.relevance}`);
    if (c.epingle) details.push("📌 épinglé");
    if (c.erreur) details.push(`⚠ ${c.erreur}`);
    row.querySelector(".sub").textContent = details.join(" · ");
    const pastille = row.querySelector(".status-pill");
    pastille.classList.add(c.etat);
    pastille.textContent = ETATS_CRENEAU[c.etat] || c.etat;
    if (CRENEAUX_MODIFIABLES.has(c.etat)) {
      const actions = row.querySelector(".slot-actions");
      const bouton = (libelle, titre, donnees) => {
        const b = document.createElement("button");
        b.textContent = libelle;
        b.title = titre;
        b.onclick = () => modifierCreneau(c.id, donnees);
        actions.appendChild(b);
      };
      bouton(c.epingle ? "📍" : "📌", c.epingle ? "Désépingler" : "Épingler", { epingle: !c.epingle });
      bouton("✏️", "Remplacer par un autre article (identifiant)", "remplacer");
      if (c.article_id) {
        bouton("⏬", "Décaler au créneau suivant", { decaler: true });
        bouton("🚫", "Sauter cet article", { sauter: true });
      }
    }
    list.appendChild(row);
  }
}

async function modifierCreneau(id, donnees) {
  if (donnees === "remplacer") {
    const saisie = prompt("Identifiant de l'article à programmer ici :");
    if (!saisie) return;
    donnees = { article_id: Number(saisie) };
  }
  const reponse = await fetch(`/schedule/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(donnees),
  });
  if (!reponse.ok) {
    const corps = await reponse.json().catch(() => ({}));
    alert("Action refusée : " + (corps.detail || reponse.status));
  }
  loadSchedulePanel();
}

document.getElementById("schedule-rebuild").onclick = async () => {
  const reponse = await fetch("/schedule/rebuild", { method: "POST" });
  if (!reponse.ok) alert("Échec du recalcul de la file : " + reponse.status);
  loadSchedulePanel();
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_api.py -v`
Expected: tout passe

- [ ] **Step 5: Vérifier le panneau à l'écran, sur une base jetable**

La base Neon n'a pas encore la table `creneaux` (migration non appliquée) : on vérifie sur une base SQLite jetable, sans toucher au dashboard installé sur le port 8000.

```bash
DB="$(mktemp -d)/file.db"
DATABASE_URL="sqlite:///$DB" uv run python -c "from app.db import Base, engine; import app.models; Base.metadata.create_all(engine)"
DATABASE_URL="sqlite:///$DB" nohup uv run uvicorn app.main:app --host 127.0.0.1 --port 8010 > /tmp/dash8010.log 2>&1 &
# --retry-connrefused : attend que uvicorn ecoute, sans sleep.
curl -s --retry 20 --retry-delay 1 --retry-connrefused -X POST http://127.0.0.1:8010/schedule/rebuild | head -c 300
```

Expected: un JSON de 6 créneaux `"etat":"libre"`.

Puis capture d'écran du panneau (ne cliquer sur aucun bouton d'action : ils peuvent ouvrir `prompt()`/`alert()`) :

```bash
uv run python - <<'EOF'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    page = p.chromium.launch().new_page(viewport={"width": 1200, "height": 900})
    page.goto("http://127.0.0.1:8010/")
    page.click('button[data-preset="schedule"]')
    page.wait_for_selector("#schedule-list .post-row")
    page.screenshot(path="/tmp/panneau_programmation.png", full_page=True)
EOF
```

Ouvrir `/tmp/panneau_programmation.png` et vérifier : six lignes « (créneau libre) », une date par ligne, pastille « Libre », boutons 📌 et ✏️. Arrêter ensuite le serveur : `pkill -f "uvicorn app.main:app --host 127.0.0.1 --port 8010"`.

- [ ] **Step 6: Lint et suite complète**

Run: `uv run ruff check . && uv run pytest -q`
Expected: tout passe

- [ ] **Step 7: Commit**

```bash
git add app/static/dashboard.html tests/api/test_api.py
git commit -F - <<'EOF'
feat(dashboard): panneau Programmation de la file de publication

Six creneaux avec heure, article, pertinence et etat, et les actions
epingler, remplacer, decaler, sauter sur les creneaux encore modifiables.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Dvb254Kc9aGX1aTK64nMyM
EOF
```

---

## Mise en service — décisions de l'utilisateur

Aucune de ces étapes n'est exécutée par un agent d'implémentation. Tant qu'elles ne sont pas faites, rien n'est publié : le mode par défaut est `arret`.

1. **Appliquer la migration 0014 sur Neon** : `uv run alembic upgrade head` (vérifiée sur cluster jetable à la tâche 7, réversible par `alembic downgrade 0013`).
2. **Redéployer les agents** : `uv run python -m scripts.install_agents --apply`, puis reposer la rotation des logs avec la commande `sudo` qu'affiche `--status` (la conf change : nouveaux fichiers `publication.log`).
3. **Vérifier les crédits Anthropic** : sans eux, pas de composition, donc des créneaux programmés qui ne se gèlent jamais.
4. **Passer en aperçu** : `PUBLICATION_MODE=apercu` dans `.env`. Observer un ou deux jours dans le panneau Programmation : des créneaux doivent passer « Programmé » → « Gelé » → « Aperçu, non publié ».
5. **Publier pour de vrai** : `PUBLICATION_MODE=reel`.

Indépendant de ce plan : couper les sources X mortes (`docs/superpowers/plans/2026-09-11-source-instagram.md`).
