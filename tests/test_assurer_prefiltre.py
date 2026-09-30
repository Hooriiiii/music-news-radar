"""L'agent qui tient le pre-filtre local debout.

Mesure du 2026-09-24 : LM Studio etait eteint et chaque passage horaire payait
le plein tarif chez Claude. Pire, le cas SOURNOIS : un modele charge avec le
contexte par defaut (4096) repond 400 sur les seuls articles LONGS -- il echoue
donc precisement sur ceux qui coutent le plus cher, et reussit sur les autres,
ce qui lui donne l'air de fonctionner.

Aucun test n'appelle le vrai binaire : `_lancer` est toujours remplace, comme
`rendre` et `telecharger` le sont deja pour Chromium et le reseau. La suite
passe sur une machine ou LM Studio n'est pas installe.
"""

import json
import subprocess
from pathlib import Path

import pytest

from scripts import assurer_prefiltre as ap

LMS = Path("/faux/lms")


def _resultat(code=0, sortie=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=sortie, stderr="")


def _charge(modele="qwen/qwen3-4b", contexte=16384):
    return json.dumps([{"identifier": modele, "modelKey": modele, "contextLength": contexte}])


@pytest.fixture()
def appels(monkeypatch):
    """Enregistre les commandes et rend des reponses scriptees."""
    journal = []
    reponses = {}

    def faux_lancer(lms, *arguments):
        journal.append(arguments)
        for prefixe, reponse in reponses.items():
            if arguments[: len(prefixe)] == prefixe:
                return reponse(arguments) if callable(reponse) else reponse
        return _resultat()

    monkeypatch.setattr(ap, "_lancer", faux_lancer)
    return journal, reponses


# -- lire l'etat -----------------------------------------------------------


def test_un_modele_charge_avec_assez_de_contexte_est_pret(appels):
    _, reponses = appels
    reponses[("ps", "--json")] = _resultat(sortie=_charge(contexte=16384))
    assert ap.contexte_charge(LMS, "qwen/qwen3-4b") == 16384


def test_un_contexte_trop_court_est_rapporte_tel_quel(appels):
    """Le cas sournois : le modele repond, mais 400 sur les articles longs."""
    _, reponses = appels
    reponses[("ps", "--json")] = _resultat(sortie=_charge(contexte=4096))
    assert ap.contexte_charge(LMS, "qwen/qwen3-4b") == 4096


def test_un_autre_modele_charge_ne_compte_pas(appels):
    _, reponses = appels
    reponses[("ps", "--json")] = _resultat(sortie=_charge(modele="gemma-4-12b-it"))
    assert ap.contexte_charge(LMS, "qwen/qwen3-4b") == 0


def test_un_serveur_injoignable_rend_None_et_non_False(appels):
    """« Je ne sais pas » n'est pas « ce n'est pas pret » : confondre les deux
    ferait decharger un modele sain des que `lms ps` echoue."""
    _, reponses = appels
    reponses[("ps", "--json")] = _resultat(code=1)
    assert ap.contexte_charge(LMS, "qwen/qwen3-4b") is None


def test_une_sortie_illisible_rend_None(appels):
    _, reponses = appels
    reponses[("ps", "--json")] = _resultat(sortie="pas du json")
    assert ap.contexte_charge(LMS, "qwen/qwen3-4b") is None


# -- agir ------------------------------------------------------------------


def test_tout_va_bien_ne_charge_ni_ne_decharge_RIEN(appels):
    """Le regime de croisiere doit etre gratuit : un agent qui tourne toutes
    les demi-heures ne doit ni recharger un modele sain ni ecrire un log. Il
    relit `ps` pour verifier l'empilement et les autres modeles -- des lectures,
    jamais une ecriture."""
    journal, reponses = appels
    reponses[("ps", "--json")] = _resultat(sortie=_charge())
    assert ap.assurer(LMS, "qwen/qwen3-4b", 16384, lambda *_: None) is True
    assert all(a[0] == "ps" for a in journal), journal


def test_un_serveur_eteint_est_demarre_puis_le_modele_charge(appels):
    journal, reponses = appels
    # Le serveur est eteint au premier `ps`, puis repond « rien de charge ».
    # La derniere reponse VAUT pour tous les appels suivants : compter les
    # lectures rendrait le test fragile a chaque garde ajoutee (empilement,
    # modeles etrangers) alors qu'aucune n'ecrit rien.
    etats = [_resultat(code=1), _resultat(sortie="[]")]

    def suivant(_):
        return etats.pop(0) if len(etats) > 1 else etats[0]

    reponses[("ps", "--json")] = suivant
    assert ap.assurer(LMS, "qwen/qwen3-4b", 16384, lambda *_: None) is True
    assert ("server", "start") in journal
    assert ("load", "qwen/qwen3-4b", "--context-length", "16384", "--parallel", "1") in journal


def test_un_contexte_trop_court_est_DECHARGE_avant_rechargement(appels):
    """`load` ne change pas le contexte d'un modele deja en memoire : sans le
    decharger d'abord, on croit avoir repare et les 400 continuent."""
    journal, reponses = appels
    reponses[("ps", "--json")] = _resultat(sortie=_charge(contexte=4096))
    assert ap.assurer(LMS, "qwen/qwen3-4b", 16384, lambda *_: None) is True
    assert journal.index(("unload", "qwen/qwen3-4b")) < journal.index(
        ("load", "qwen/qwen3-4b", "--context-length", "16384", "--parallel", "1")
    )


def test_un_chargement_en_echec_rend_False(appels):
    _, reponses = appels
    reponses[("ps", "--json")] = _resultat(sortie="[]")
    reponses[("load",)] = _resultat(code=1)
    assert ap.assurer(LMS, "qwen/qwen3-4b", 16384, lambda *_: None) is False


def test_un_demarrage_de_serveur_en_echec_rend_False_sans_charger(appels):
    journal, reponses = appels
    reponses[("ps", "--json")] = _resultat(code=1)
    reponses[("server", "start")] = _resultat(code=1)
    assert ap.assurer(LMS, "qwen/qwen3-4b", 16384, lambda *_: None) is False
    assert not any(a[0] == "load" for a in journal)


# -- trouver le binaire ----------------------------------------------------


def test_lms_est_cherche_HORS_du_path_de_launchd(monkeypatch, tmp_path):
    """launchd ne fournit qu'un PATH minimal et `lms` vit chez l'utilisateur --
    meme piege que ffmpeg et cloudflared."""
    faux = tmp_path / "lms"
    faux.write_text("#!/bin/sh\n")
    monkeypatch.setattr(ap.shutil, "which", lambda _: None)
    monkeypatch.setattr(ap, "EMPLACEMENTS_CONNUS", (tmp_path / "absent", faux))
    assert ap.trouver_lms() == faux


def test_sans_binaire_nulle_part_on_rend_None(monkeypatch, tmp_path):
    monkeypatch.setattr(ap.shutil, "which", lambda _: None)
    monkeypatch.setattr(ap, "EMPLACEMENTS_CONNUS", (tmp_path / "absent",))
    assert ap.trouver_lms() is None


# -- la memoire : empilement et modeles etrangers ---------------------------


def _charges(*specs):
    return json.dumps(
        [
            {"identifier": ident, "modelKey": cle, "contextLength": ctx, "sizeBytes": 2_000_000_000}
            for ident, cle, ctx in specs
        ]
    )


def test_deux_instances_du_meme_modele_sont_comptees(appels):
    """`lms load` n'REMPLACE pas, il EMPILE : deux appels chargent deux copies
    et donc deux fois la memoire. Constate sur une machine de 16 Go."""
    _, reponses = appels
    reponses[("ps", "--json")] = _resultat(
        sortie=_charges(
            ("qwen/qwen3-1.7b", "qwen/qwen3-1.7b", 8192),
            ("qwen/qwen3-1.7b:2", "qwen/qwen3-1.7b", 8192),
        )
    )
    assert ap.instances_chargees(LMS, "qwen/qwen3-1.7b") == 2


def test_un_empilement_est_defait_avant_de_repartir(appels):
    journal, reponses = appels
    reponses[("ps", "--json")] = _resultat(
        sortie=_charges(
            ("qwen/qwen3-1.7b", "qwen/qwen3-1.7b", 8192),
            ("qwen/qwen3-1.7b:2", "qwen/qwen3-1.7b", 8192),
        )
    )
    assert ap.assurer(LMS, "qwen/qwen3-1.7b", 8192, lambda *_: None) is True
    assert ("unload", "--all") in journal
    assert ("load", "qwen/qwen3-1.7b", "--context-length", "8192", "--parallel", "1") in journal


def test_un_autre_modele_est_SIGNALE_et_jamais_decharge(appels, capsys):
    """LM Studio est aussi l'outil de l'utilisateur : un agent qui decharge ce
    qu'il n'a pas charge se battrait contre lui. On rend le cout visible, la
    decision reste humaine."""
    journal, reponses = appels
    reponses[("ps", "--json")] = _resultat(
        sortie=_charges(
            ("qwen/qwen3-1.7b", "qwen/qwen3-1.7b", 8192),
            ("gemma-4-12b-it", "gemma-4-12b-it", 8192),
        )
    )
    assert ap.assurer(LMS, "qwen/qwen3-1.7b", 8192, lambda *_: None) is True
    assert not any(a[0] == "unload" for a in journal), journal
    assert "gemma-4-12b-it" in capsys.readouterr().out


def test_un_seul_modele_ne_declenche_aucun_avertissement(appels, capsys):
    _, reponses = appels
    reponses[("ps", "--json")] = _resultat(
        sortie=_charges(("qwen/qwen3-1.7b", "qwen/qwen3-1.7b", 8192))
    )
    assert ap.assurer(LMS, "qwen/qwen3-1.7b", 8192, lambda *_: None) is True
    assert "occupent la memoire" not in capsys.readouterr().out
