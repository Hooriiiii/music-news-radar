from app.pipeline.cluster_arbitrage import (
    Cache,
    EntreeArbitrage,
    VerdictArbitrage,
    cle_paire,
    ecrire_cache,
    lire_cache,
    make_claude_arbitre,
    marquer_manuel,
)
from app.models import Article


def _article(id_, title="Titre", summary=None):
    art = Article(id=id_, source_id=1, url=f"https://x/{id_}", title=title, raw_hash=f"{id_}",
                  summary=summary)
    return art


# -- cle de paire : independante de l'ordre --------------------------------


def test_cle_paire_est_independante_de_l_ordre():
    assert cle_paire(12, 40) == cle_paire(40, 12)


def test_cle_paire_distingue_des_paires_differentes():
    assert cle_paire(1, 2) != cle_paire(1, 3)


# -- cache : lecture / ecriture / manuel ------------------------------------


def test_le_cache_absent_rend_un_cache_vide(tmp_path):
    cache = lire_cache(tmp_path / "absent.json")
    assert cache.entrees == {}


def test_le_cache_corrompu_rend_un_cache_vide_sans_lever(tmp_path):
    chemin = tmp_path / "cluster_arbitrage.json"
    chemin.write_text("pas du json valide {{{")
    cache = lire_cache(chemin)
    assert cache.entrees == {}


def test_ecrire_puis_lire_le_cache_conserve_le_verdict(tmp_path):
    chemin = tmp_path / "cluster_arbitrage.json"

    cache = Cache({cle_paire(1, 2): EntreeArbitrage(True, "meme sortie", manuel=False)})
    ecrire_cache(cache, chemin)

    relu = lire_cache(chemin)
    entree = relu.entrees[cle_paire(1, 2)]
    assert entree.meme_actu is True
    assert entree.raison == "meme sortie"
    assert entree.manuel is False


def test_marquer_manuel_pose_un_verdict_marque_comme_tel(tmp_path):
    chemin = tmp_path / "cluster_arbitrage.json"
    marquer_manuel(5, 9, False, "deux actus distinctes", chemin)

    cache = lire_cache(chemin)
    entree = cache.entrees[cle_paire(5, 9)]
    assert entree.meme_actu is False
    assert entree.manuel is True


def test_marquer_manuel_ecrase_un_verdict_automatique_existant(tmp_path):
    chemin = tmp_path / "cluster_arbitrage.json"
    ecrire_cache(
        Cache({cle_paire(5, 9): EntreeArbitrage(True, "verdict automatique", manuel=False)}),
        chemin,
    )

    marquer_manuel(5, 9, False, "en fait non", chemin)

    entree = lire_cache(chemin).entrees[cle_paire(5, 9)]
    assert entree.manuel is True
    assert entree.meme_actu is False


def test_ecrire_cache_ne_leve_jamais_meme_sur_un_chemin_impossible():
    from pathlib import Path

    from app.pipeline.cluster_arbitrage import Cache

    # Un fichier existant utilise comme repertoire parent : mkdir echoue.
    impossible = Path(__file__)  # un fichier, pas un dossier
    ecrire_cache(Cache({}), impossible / "sous-dossier" / "cache.json")  # ne leve pas


# -- make_claude_arbitre : meme motif que make_claude_scorer / make_claude_juge


class _ClientFixe:
    def __init__(self, sortie_ou_exception):
        self._sortie = sortie_ou_exception

        class _Messages:
            @staticmethod
            def parse(**kwargs):
                if isinstance(sortie_ou_exception, Exception):
                    raise sortie_ou_exception
                return type("Reponse", (), {"parsed_output": sortie_ou_exception})()

        self.messages = _Messages()


def test_l_arbitre_rend_le_verdict_sur_une_reponse_conforme():
    client = _ClientFixe(VerdictArbitrage(meme_actu=True, raison="meme sortie"))
    arbitre = make_claude_arbitre(client)

    verdict = arbitre(_article(1, "A"), _article(2, "B"))

    assert verdict == (True, "meme sortie")


def test_l_arbitre_rend_none_sur_une_exception_reseau():
    client = _ClientFixe(RuntimeError("timeout"))
    arbitre = make_claude_arbitre(client)

    assert arbitre(_article(1, "A"), _article(2, "B")) is None


def test_l_arbitre_rend_none_quand_la_sortie_structuree_est_vide():
    client = _ClientFixe(None)
    arbitre = make_claude_arbitre(client)

    assert arbitre(_article(1, "A"), _article(2, "B")) is None
