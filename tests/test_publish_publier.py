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
