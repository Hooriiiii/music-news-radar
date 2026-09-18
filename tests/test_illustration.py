"""Le fond d'une cover : une illustration, jamais une affiche.

Aucun appel reseau, aucun appel Claude : le juge est toujours injecte, et le
telechargement remplace par un faux. C'est la contrainte du module lui-meme --
il doit degrader proprement sans l'un ni l'autre.
"""

import datetime as dt
import io
import json

import pytest
from PIL import Image

from app.config import settings
from app.content import illustration
from app.content.illustration import (
    Verdict,
    choisir_illustration,
    lire_cache,
    make_claude_juge,
    reduire,
)

MAINTENANT = dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.timezone.utc)

AFFICHE = "https://exemple.test/affiche.jpg"
SCENE = "https://exemple.test/scene.jpg"
PORTRAIT = "https://exemple.test/portrait.jpg"

VERDICTS = {
    AFFICHE: Verdict(affiche=True, sujet="autre", note=15),
    SCENE: Verdict(affiche=False, sujet="scene", note=80),
    PORTRAIT: Verdict(affiche=False, sujet="portrait", note=60),
}


def _jpeg(largeur: int = 40, hauteur: int = 50) -> bytes:
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (20, 30, 40)).save(tampon, format="JPEG")
    return tampon.getvalue()


@pytest.fixture(autouse=True)
def _sans_reseau(monkeypatch):
    """Toute image candidate se telecharge instantanement et sans sortir."""
    monkeypatch.setattr(
        illustration, "telecharger_media", lambda url, *a, **k: (_jpeg(), ".jpg")
    )


def _juge_espion(urls_jugees: list[str], candidats: list[str]):
    """Un juge qui rend les verdicts attendus et NOTE combien de fois il est
    appele -- le cout est la moitie du sujet de ce module."""
    appels: list[int] = []

    def juger(images):
        appels.append(len(images))
        # Les images arrivent dans l'ordre des URL non encore connues.
        return [VERDICTS[url] for url in urls_jugees]

    return juger, appels


def test_une_affiche_en_premier_laisse_la_place_a_la_photo_de_scene(tmp_path):
    """Le cas qui justifie tout : un compte de festival publie son affiche de
    line-up en tete de carrousel, et c'est elle qui faisait le fond."""
    candidats = [AFFICHE, SCENE, PORTRAIT]
    juge, appels = _juge_espion(candidats, candidats)

    choisi = choisir_illustration(
        candidats, juge=juge, maintenant=MAINTENANT, chemin_cache=tmp_path / "c.json"
    )

    assert choisi == SCENE
    assert appels == [3]


def test_un_verdict_en_cache_ne_coute_aucun_appel(tmp_path):
    chemin = tmp_path / "c.json"
    candidats = [AFFICHE, SCENE]
    juge, appels = _juge_espion(candidats, candidats)
    assert choisir_illustration(
        candidats, juge=juge, maintenant=MAINTENANT, chemin_cache=chemin
    ) == SCENE

    def juge_interdit(images):  # pragma: no cover -- il ne doit jamais tourner
        raise AssertionError("le juge a ete rappele alors que le cache repondait")

    choisi = choisir_illustration(
        candidats, juge=juge_interdit, maintenant=MAINTENANT, chemin_cache=chemin
    )

    assert choisi == SCENE
    assert appels == [2]


def test_un_juge_en_panne_laisse_le_comportement_d_avant(tmp_path):
    """Une panne d'API ne doit jamais couter une composition : on rend None,
    et l'appelant garde son premier candidat."""

    def juge_casse(images):
        raise RuntimeError("502 Bad Gateway")

    assert (
        choisir_illustration(
            [AFFICHE, SCENE], juge=juge_casse, maintenant=MAINTENANT,
            chemin_cache=tmp_path / "c.json",
        )
        is None
    )


def test_un_seul_candidat_ne_se_juge_pas(tmp_path):
    """Il n'y a rien a classer : payer un appel pour confirmer l'unique image
    disponible serait une depense sans consequence possible."""

    def juge_interdit(images):  # pragma: no cover
        raise AssertionError("juge appele sans choix a faire")

    assert (
        choisir_illustration(
            [AFFICHE], juge=juge_interdit, maintenant=MAINTENANT,
            chemin_cache=tmp_path / "c.json",
        )
        is None
    )


def test_tout_affiche_rend_la_moins_mauvaise(tmp_path):
    """Mieux vaut une cover sur une affiche qu'un creneau perdu : le repli vers
    une photo d'artiste suivi vit en amont, dans `choix_visuels`."""
    deuxieme = "https://exemple.test/affiche2.jpg"
    verdicts = [
        Verdict(affiche=True, sujet="autre", note=10),
        Verdict(affiche=True, sujet="autre", note=18),
    ]

    choisi = choisir_illustration(
        [AFFICHE, deuxieme], juge=lambda images: verdicts, maintenant=MAINTENANT,
        chemin_cache=tmp_path / "c.json",
    )

    assert choisi == deuxieme


def test_une_image_inaccessible_ne_fait_pas_perdre_les_autres(tmp_path, monkeypatch):
    """Une URL signee du CDN Instagram expire : son candidat est saute, les
    autres sont juges quand meme."""
    monkeypatch.setattr(
        illustration,
        "telecharger_media",
        lambda url, *a, **k: (None, "404") if url == AFFICHE else (_jpeg(), ".jpg"),
    )
    juge, appels = _juge_espion([SCENE], [AFFICHE, SCENE])

    choisi = choisir_illustration(
        [AFFICHE, SCENE], juge=juge, maintenant=MAINTENANT, chemin_cache=tmp_path / "c.json"
    )

    assert choisi == SCENE
    assert appels == [1]


def test_un_candidat_jamais_juge_passe_devant_une_affiche_averee(tmp_path):
    """Ne pas savoir vaut mieux que savoir que c'est une affiche."""
    inconnu = "https://exemple.test/inconnu.jpg"
    connus = {AFFICHE: VERDICTS[AFFICHE]}
    illustration.ecrire_cache(
        illustration.Cache(connus), MAINTENANT, tmp_path / "c.json"
    )

    # Le juge ne rend rien pour l'inconnu : il reste au verdict neutre.
    choisi = choisir_illustration(
        [AFFICHE, inconnu], juge=lambda images: [Verdict(affiche=False, sujet="autre", note=50)],
        maintenant=MAINTENANT, chemin_cache=tmp_path / "c.json",
    )

    assert choisi == inconnu


def test_le_cache_expire_apres_son_delai(tmp_path):
    chemin = tmp_path / "c.json"
    illustration.ecrire_cache(illustration.Cache({SCENE: VERDICTS[SCENE]}), MAINTENANT, chemin)

    frais = lire_cache(MAINTENANT + dt.timedelta(days=1), chemin)
    perime = lire_cache(
        MAINTENANT + dt.timedelta(days=settings.illustration_cache_jours + 1), chemin
    )

    assert SCENE in frais.verdicts
    assert perime.verdicts == {}


def test_un_cache_corrompu_vaut_un_cache_vide(tmp_path):
    chemin = tmp_path / "c.json"
    chemin.write_text("{ ceci n'est pas du JSON", encoding="utf-8")

    assert lire_cache(MAINTENANT, chemin).verdicts == {}


def test_le_cache_est_ecrit_atomiquement(tmp_path):
    chemin = tmp_path / "sous" / "c.json"
    illustration.ecrire_cache(illustration.Cache({SCENE: VERDICTS[SCENE]}), MAINTENANT, chemin)

    contenu = json.loads(chemin.read_text(encoding="utf-8"))

    assert contenu["version"] == illustration.CACHE_VERSION
    assert contenu["verdicts"][SCENE]["sujet"] == "scene"
    assert not list(chemin.parent.glob("*.tmp"))


def test_reduire_borne_la_surface_donc_le_cout_en_jetons():
    """Le cout d'une image est proportionnel a sa surface : une photo de presse
    non reduite coute quinze fois sa vignette."""
    grande = io.BytesIO()
    Image.new("RGB", (2000, 1333), (200, 30, 40)).save(grande, format="JPEG")

    petite = Image.open(io.BytesIO(reduire(grande.getvalue(), taille=512)))

    assert max(petite.size) == 512
    assert petite.format == "JPEG"


def test_la_vision_coupee_ne_juge_rien(tmp_path, monkeypatch):
    """Coupe-circuit de `.env` : sans juge injecte et vision eteinte, on rend le
    comportement d'avant plutot qu'un appel."""
    monkeypatch.setattr(settings, "illustration_vision_enabled", False)

    assert (
        choisir_illustration(
            [AFFICHE, SCENE], maintenant=MAINTENANT, chemin_cache=tmp_path / "c.json"
        )
        is None
    )


def test_le_juge_refuse_un_appariement_qui_glisse():
    """Un verdict de moins et toutes les notes glissent d'un rang : une affiche
    prendrait celle d'une photo. On prefere ne rien savoir."""

    class FauxClient:
        class messages:  # noqa: N801
            @staticmethod
            def parse(**kwargs):
                class Reponse:
                    parsed_output = illustration.Verdicts(verdicts=[VERDICTS[SCENE]])

                return Reponse()

    with pytest.raises(ValueError, match="1 verdicts pour 2 images"):
        make_claude_juge(FauxClient())([_jpeg(), _jpeg()])


def test_le_juge_envoie_une_image_par_candidat():
    envoyes = {}

    class FauxClient:
        class messages:  # noqa: N801
            @staticmethod
            def parse(**kwargs):
                envoyes.update(kwargs)

                class Reponse:
                    parsed_output = illustration.Verdicts(
                        verdicts=[VERDICTS[SCENE], VERDICTS[AFFICHE]]
                    )

                return Reponse()

    verdicts = make_claude_juge(FauxClient())([_jpeg(), _jpeg()])

    contenu = envoyes["messages"][0]["content"]
    assert [bloc["type"] for bloc in contenu] == ["text", "image", "text", "image"]
    assert envoyes["model"] == settings.visuels_model
    assert [v.sujet for v in verdicts] == ["scene", "autre"]
