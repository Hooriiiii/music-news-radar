"""L'essai de publication ne laisse pas de video ni d'images sur le disque."""

from types import SimpleNamespace

import pytest

import scripts.tester_publication as script
from app.config import settings


class _Session:
    def __init__(self, post):
        self.post = post

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, modele, identifiant):
        return self.post if modele is script.Post else None


def _essai(monkeypatch, template, publier):
    post = SimpleNamespace(id=12, article_id=None, template=template, caption="Legende", hashtags=[], credit=None)
    monkeypatch.setattr("sys.argv", ["tester_publication", "--post", "12"])
    monkeypatch.setattr(script, "attendre_base", lambda: None)
    monkeypatch.setattr(script, "SessionLocal", lambda: _Session(post))
    monkeypatch.setattr(script, "texte_legende", lambda post: "Legende")

    def preparer(post, article, dossier, **kwargs):
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "lourd.mp4").write_bytes(b"x" * 1000)
        return ["lourd.mp4"]

    monkeypatch.setattr(script, "preparer_fichiers", preparer)
    monkeypatch.setattr(script, "preparer_reel", preparer)
    monkeypatch.setattr(script, "publier_fichiers", publier)
    monkeypatch.setattr(script, "publier_reel", publier)
    return settings.publication_dir / "essais" / "12"


@pytest.mark.parametrize("template", ["kinda_post", "kinda_reel"])
def test_l_essai_efface_son_dossier(monkeypatch, template):
    dossier = _essai(monkeypatch, template, lambda *a, **k: "conteneur")
    script.main()
    assert not dossier.exists()


def test_un_essai_en_echec_efface_aussi_son_dossier(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Meta refuse")

    dossier = _essai(monkeypatch, "kinda_reel", refuse)
    with pytest.raises(RuntimeError, match="Meta refuse"):
        script.main()
    assert not dossier.exists()
