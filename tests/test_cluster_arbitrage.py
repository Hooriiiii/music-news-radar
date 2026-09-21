import pytest

from app.pipeline.cluster_arbitrage import (
    Cache,
    EntreeArbitrage,
    RAISON_MAX_CARACTERES,
    VerdictArbitrage,
    cle_paire,
    ecrire_cache,
    fusionner_et_ecrire,
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


# -- Tour de correction 1/5 : raison tronquee, pas rejetee ------------------


def test_i1_une_raison_trop_longue_est_tronquee_pas_rejetee():
    """Le SDK (0.116.0) retire `maxLength` du schema envoye au modele -- la
    contrainte n'etait donc appliquee QUE cote client, apres facturation. Une
    reponse au verdict par ailleurs valide ne doit plus jamais etre rejetee
    pour la longueur d'un champ qui n'est qu'une trace de supervision."""
    verdict = VerdictArbitrage(meme_actu=True, raison="x" * 1000)
    assert len(verdict.raison) == RAISON_MAX_CARACTERES


def test_i1_une_raison_courte_n_est_pas_touchee():
    verdict = VerdictArbitrage(meme_actu=False, raison="deux annonces distinctes")
    assert verdict.raison == "deux annonces distinctes"


# -- Tour de correction 1/5 (I2) : fusion juste avant l'ecriture ------------


def test_i2_fusionner_et_ecrire_garde_un_verdict_manuel_pose_entre_temps(tmp_path):
    """Le coeur du remede I2 : un verdict manuel present sur le DISQUE au
    moment de l'ecriture doit gagner sur la decision automatique de CE
    passage pour la MEME paire, meme si le passage l'ignorait au moment de sa
    propre lecture initiale."""
    chemin = tmp_path / "cluster_arbitrage.json"
    marquer_manuel(1, 2, False, "pose entre-temps par un autre processus", chemin)

    fusionner_et_ecrire({cle_paire(1, 2): EntreeArbitrage(True, "verdict automatique")}, {}, chemin)

    entree = lire_cache(chemin).entrees[cle_paire(1, 2)]
    assert entree.manuel is True
    assert entree.meme_actu is False


def test_i2_fusionner_et_ecrire_garde_les_entrees_non_concernees(tmp_path):
    chemin = tmp_path / "cluster_arbitrage.json"
    marquer_manuel(9, 9, False, "sans rapport", chemin)  # une autre paire, deja sur le disque

    fusionner_et_ecrire({cle_paire(1, 2): EntreeArbitrage(True, "verdict automatique")}, {}, chemin)

    cache = lire_cache(chemin)
    assert cache.entrees[cle_paire(1, 2)].meme_actu is True
    assert cache.entrees[cle_paire(9, 9)].manuel is True


def test_i2_les_tentatives_survivent_a_la_fusion(tmp_path):
    chemin = tmp_path / "cluster_arbitrage.json"
    fusionner_et_ecrire({}, {cle_paire(1, 2): 2}, chemin)

    cache = lire_cache(chemin)
    assert cache.tentatives[cle_paire(1, 2)] == 2


def test_i2_un_verdict_manuel_efface_le_compteur_de_tentatives_de_la_meme_paire(tmp_path):
    """Un verdict manuel rend un compteur d'echecs obsolete : `link_duplicates`
    ne doit plus jamais retenter une paire que l'utilisateur vient de trancher."""
    chemin = tmp_path / "cluster_arbitrage.json"
    marquer_manuel(1, 2, False, "tranchee a la main", chemin)

    fusionner_et_ecrire({}, {cle_paire(1, 2): 1}, chemin)

    cache = lire_cache(chemin)
    assert cle_paire(1, 2) not in cache.tentatives


# -- Tour de correction 1/5 (I3) : marquer_manuel leve sur un echec d'ecriture


def test_i3_marquer_manuel_leve_si_l_ecriture_echoue(tmp_path):
    impossible = tmp_path / "fichier-existant"
    impossible.write_text("un fichier, pas un dossier")
    chemin = impossible / "sous-dossier" / "cluster_arbitrage.json"

    with pytest.raises(Exception):  # noqa: B017 -- n'importe quelle panne d'ecriture
        marquer_manuel(1, 2, False, "raison", chemin)


def test_i3_marquer_manuel_ne_leve_pas_quand_l_ecriture_reussit(tmp_path):
    chemin = tmp_path / "cluster_arbitrage.json"
    marquer_manuel(1, 2, False, "raison", chemin)  # ne leve pas
    assert lire_cache(chemin).entrees[cle_paire(1, 2)].manuel is True


# -- Tour de correction 1/5 (I2) : temporaire nomme par processus -----------


def test_i2_ecrire_cache_n_utilise_plus_un_nom_de_temporaire_fixe(tmp_path):
    """Deux ecritures qui se recouvrent dans le temps ne doivent pas se
    marcher dessus : `tempfile.mkstemp` garantit un nom unique par appel,
    contrairement a l'ancien `chemin.with_suffix(\".tmp\")`."""
    chemin = tmp_path / "cluster_arbitrage.json"
    ecrire_cache(Cache({cle_paire(1, 2): EntreeArbitrage(True, "a")}), chemin)
    fichiers_avant = set(tmp_path.iterdir())
    ecrire_cache(Cache({cle_paire(3, 4): EntreeArbitrage(False, "b")}), chemin)
    # Aucun fichier temporaire ne doit trainer apres coup (succes ou echec).
    fichiers_apres = set(tmp_path.iterdir())
    assert fichiers_apres == fichiers_avant  # que le fichier final, rien de plus


# -- Residu ferme au tour de correction 2/5 : absent != illisible -----------


def test_residu_lire_cache_traite_absent_et_illisible_pareil_lecture_seule(tmp_path):
    """`lire_cache` (lecture SEULE) peut continuer a confondre les deux --
    c'est `_lire_pour_ecriture`, utilisee juste avant une ECRITURE, qui doit
    les distinguer."""
    absent = lire_cache(tmp_path / "absent.json")
    chemin_corrompu = tmp_path / "corrompu.json"
    chemin_corrompu.write_text("pas du json {{{")
    corrompu = lire_cache(chemin_corrompu)
    assert absent.entrees == corrompu.entrees == {}


def test_residu_fusionner_et_ecrire_n_ecrase_pas_un_cache_illisible(tmp_path):
    """Le coeur du residu : un fichier PRESENT mais illisible doit annuler
    l'ecriture entiere, jamais se faire ecraser par un cache vide -- sinon un
    seul fichier corrompu efface tous les verdicts existants, manuels
    compris."""
    chemin = tmp_path / "cluster_arbitrage.json"
    contenu_corrompu = "{ceci n'est pas du json valide"
    chemin.write_text(contenu_corrompu)

    fusionner_et_ecrire({cle_paire(1, 2): EntreeArbitrage(True, "verdict automatique")}, {}, chemin)

    assert chemin.read_text() == contenu_corrompu  # intact : rien n'a ete ecrit


def test_residu_fusionner_et_ecrire_fonctionne_normalement_sur_fichier_absent(tmp_path):
    """Un fichier ABSENT reste le cas normal du premier passage : l'ecriture
    a bien lieu."""
    chemin = tmp_path / "cluster_arbitrage.json"
    fusionner_et_ecrire({cle_paire(1, 2): EntreeArbitrage(True, "verdict automatique")}, {}, chemin)
    assert lire_cache(chemin).entrees[cle_paire(1, 2)].meme_actu is True


def test_residu_marquer_manuel_leve_sur_un_cache_present_mais_illisible(tmp_path):
    """Meme garde-fou que I3, pour une raison differente : `marquer_manuel`
    ne doit jamais ecrire un cache reconstruit a partir de rien par-dessus un
    fichier corrompu, qui contenait peut-etre d'autres verdicts manuels."""
    chemin = tmp_path / "cluster_arbitrage.json"
    chemin.write_text("pas du json {{{")

    with pytest.raises(Exception):  # noqa: B017 -- n'importe quelle panne de lecture
        marquer_manuel(1, 2, False, "raison", chemin)

    # Le fichier corrompu n'a pas ete touche -- un humain peut encore le lire
    # ou le reparer a la main.
    assert chemin.read_text() == "pas du json {{{"
