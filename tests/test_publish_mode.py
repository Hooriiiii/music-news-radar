import datetime as dt
import json

from app.config import settings
from app.publish.mode import enregistrer_mode, enregistrer_reels, mode_effectif, reels_actifs, source_du_mode

MAINTENANT = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.timezone.utc)


def test_sans_fichier_le_mode_vient_du_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "publication_mode", "apercu")
    assert mode_effectif(tmp_path / "absent.json") == "apercu"


def test_le_fichier_prime_sur_le_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "publication_mode", "arret")
    chemin = tmp_path / "publication.json"
    enregistrer_mode("reel", MAINTENANT, chemin)
    assert mode_effectif(chemin) == "reel"


def test_un_fichier_corrompu_retombe_sur_le_env_sans_lever(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "publication_mode", "apercu")
    chemin = tmp_path / "publication.json"
    chemin.write_text("{ceci n'est pas du json")
    assert mode_effectif(chemin) == "apercu"


def test_un_mode_invalide_dans_le_fichier_retombe_sur_le_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "publication_mode", "apercu")
    chemin = tmp_path / "publication.json"
    chemin.write_text(json.dumps({"mode": "n_importe_quoi", "change_le": MAINTENANT.isoformat()}))
    assert mode_effectif(chemin) == "apercu"


def test_enregistrer_cree_le_dossier_et_ecrit_le_contenu(tmp_path):
    chemin = tmp_path / "sous_dossier" / "publication.json"
    enregistrer_mode("reel", MAINTENANT, chemin)
    donnees = json.loads(chemin.read_text())
    assert donnees == {"mode": "reel", "change_le": MAINTENANT.isoformat()}


def test_enregistrer_est_atomique_pas_de_fichier_temporaire_restant(tmp_path):
    chemin = tmp_path / "publication.json"
    enregistrer_mode("apercu", MAINTENANT, chemin)
    fichiers = list(tmp_path.iterdir())
    assert fichiers == [chemin]


def test_par_defaut_le_chemin_est_derive_de_meta_token_path(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "publication_mode_path", tmp_path / "publication.json")
    monkeypatch.setattr(settings, "publication_mode", "arret")
    enregistrer_mode("reel", MAINTENANT)
    assert mode_effectif() == "reel"


def test_sans_fichier_les_reels_sont_actifs(tmp_path):
    assert reels_actifs(tmp_path / "absent.json") is True


def test_couper_les_reels(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "publication_mode", "apercu")
    chemin = tmp_path / "publication.json"
    enregistrer_reels(False, MAINTENANT, chemin)
    assert reels_actifs(chemin) is False
    # Le fichier ne porte pas de mode : le mode reste celui de .env.
    assert (mode_effectif(chemin), source_du_mode(chemin)) == ("apercu", "env")


def test_les_deux_reglages_se_preservent(tmp_path):
    """Un seul fichier pour deux interrupteurs : ecrire l'un n'efface pas l'autre."""
    chemin = tmp_path / "publication.json"
    enregistrer_reels(False, MAINTENANT, chemin)
    enregistrer_mode("apercu", MAINTENANT, chemin)
    assert (mode_effectif(chemin), reels_actifs(chemin)) == ("apercu", False)
    enregistrer_reels(True, MAINTENANT, chemin)
    assert (mode_effectif(chemin), reels_actifs(chemin)) == ("apercu", True)
    assert list(tmp_path.iterdir()) == [chemin]


def test_une_valeur_de_reels_invalide_laisse_les_reels_actifs(tmp_path):
    chemin = tmp_path / "publication.json"
    chemin.write_text(json.dumps({"reels_actifs": "non"}))
    assert reels_actifs(chemin) is True
    chemin.write_text("{corrompu")
    assert reels_actifs(chemin) is True
