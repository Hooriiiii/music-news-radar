"""Le bandeau de supervision, et surtout sa propriete NEGATIVE.

« Inconnu ne se degrade jamais en vert » est le seul invariant du module qu'un
`or OK` bien intentionne casserait sans faire echouer quoi que ce soit d'autre :
tous les autres tests du depot passeraient, et le bandeau afficherait « tout
tourne » sur une machine en panne -- exactement l'ecran qu'on vient de remplacer.
D'ou les quatre premiers tests, qui le verrouillent a chaque etage : la fonction
d'agregation, chaque lecteur de fichier, l'enveloppe de `superviser`, et le
dashboard lui-meme.
"""

import datetime as dt
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from app import supervision
from app.config import settings
from app.models import (
    Article,
    Creneau,
    EtatCreneau,
    FormatCreneau,
    Post,
    PostStatus,
    Source,
    SourceType,
)
from app.supervision import (
    ATTENTION,
    INCONNU,
    OK,
    PANNE,
    Voyant,
    lire_json,
    niveau_global,
    seuil_agent_publication,
    superviser,
    voyant_agent_publication,
    voyant_composition,
    voyant_creneaux,
    voyant_jeton_publication,
    voyant_jeton_sourcing,
    voyant_mode,
    voyant_passage,
    voyant_quota,
    voyant_sources,
)
from app.supervision import etat_sources as lire_etat_sources

MAINTENANT = dt.datetime(2026, 10, 2, 15, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _agent_cense_tourner(monkeypatch):
    # `arret` est le defaut du `.env` : le constat des creneaux depasses n'y accuse personne
    # (tests/socle/test_supervision_arret.py). Ici, l'agent est cense passer.
    monkeypatch.setattr(settings, "publication_mode", "reel")


def _voyant(niveau):
    return Voyant("x", "X", niveau, "v")


def _dossier():
    chemin = settings.meta_token_path.parent
    chemin.mkdir(parents=True, exist_ok=True)
    return chemin


def _ecrire(nom, donnees):
    chemin = _dossier() / nom
    chemin.write_text(donnees if isinstance(donnees, str) else json.dumps(donnees))
    return chemin


def _source(db, nom="Tsugi", etat=None, active=True):
    source = Source(name=nom, type=SourceType.RSS, url=f"https://{nom}.test/rss", active=active)
    source.state = etat
    db.add(source)
    db.commit()
    return source


# --- L'invariant : inconnu ne devient jamais vert ---------------------------


@pytest.mark.parametrize("autres", [[], [OK], [OK, ATTENTION], [ATTENTION, ATTENTION]])
def test_un_voyant_inconnu_empeche_le_vert_global(autres):
    voyants = [_voyant(n) for n in autres] + [_voyant(INCONNU)]
    assert niveau_global(voyants) != OK


def test_le_global_est_le_pire_et_la_panne_prime_sur_l_inconnu():
    assert niveau_global([_voyant(OK), _voyant(OK)]) == OK
    assert niveau_global([_voyant(OK), _voyant(ATTENTION)]) == ATTENTION
    assert niveau_global([_voyant(INCONNU), _voyant(ATTENTION)]) == INCONNU
    assert niveau_global([_voyant(INCONNU), _voyant(PANNE)]) == PANNE
    # Aucun voyant du tout n'est pas une bonne nouvelle : c'est l'ignorance.
    assert niveau_global([]) == INCONNU


def test_aucun_voyant_ne_rend_ok_sur_un_fichier_illisible(db_session):
    """Chaque lecteur d'etat, un par un, sur un JSON tronque."""
    for nom in ("publication.json", "quota_graph.json", "instagram_token.json"):
        _ecrire(nom, "{pas du json")
    monkeypatched = settings.meta_fb_access_token
    assert monkeypatched is None  # garde : le conftest neutralise le jeton reel
    _ecrire("sourcing_token_alert.json", "{pas du json")

    assert voyant_mode(MAINTENANT).niveau == INCONNU
    assert voyant_quota(MAINTENANT).niveau == INCONNU
    assert voyant_jeton_publication(MAINTENANT).niveau == INCONNU


def test_une_exception_imprevue_devient_un_voyant_inconnu(db_session, monkeypatch):
    """Et jamais un voyant ABSENT : une case vide se lit « rien a signaler »."""
    monkeypatch.setattr(
        "app.supervision.voyant_composition",
        lambda db, maintenant: (_ for _ in ()).throw(RuntimeError("colonne disparue")),
    )
    etat = superviser(db_session, MAINTENANT)
    composition = next(v for v in etat.voyants if v.cle == "composition")
    assert composition.niveau == INCONNU
    assert "RuntimeError" in composition.raison
    assert etat.niveau != OK


def test_lire_json_distingue_absent_et_illisible():
    chemin = _dossier() / "jamais_ecrit.json"
    assert lire_json(chemin).absent is True
    assert lire_json(chemin).erreur is None
    chemin.write_text("[]")
    assert lire_json(chemin).erreur is not None  # un tableau n'est pas un etat


# --- Les voyants, un par un -------------------------------------------------


def test_les_credits_epuises_sont_nommes_et_en_panne(db_session):
    db_session.add(
        Creneau(
            publier_a=MAINTENANT - dt.timedelta(hours=2),
            format=FormatCreneau.CARROUSEL,
            etat=EtatCreneau.LIBRE,
            erreur="composition interrompue : Error code: 400 - your credit balance is too low",
        )
    )
    db_session.commit()
    voyant = voyant_composition(db_session, MAINTENANT)
    assert voyant.niveau == PANNE
    assert "crédits Anthropic épuisés" in voyant.raison


def test_un_arriere_de_scoring_est_signale(db_session):
    """Le scoring note TOUT a chaque passage : un article sans note depuis plus
    de deux heures dit que le scoring ne tourne plus (la panne de credits)."""
    source = _source(db_session)
    db_session.add(
        Article(
            source_id=source.id,
            url="https://x.test/1",
            title="Sans note",
            raw_hash="h1",
            fetched_at=MAINTENANT - dt.timedelta(hours=5),
        )
    )
    db_session.commit()
    voyant = voyant_composition(db_session, MAINTENANT)
    assert voyant.niveau == ATTENTION
    assert "1" in voyant.valeur


def test_une_composition_recente_suffit_au_vert(db_session):
    db_session.add(
        Post(
            article_id=None,
            article_title="T",
            article_url="https://x.test",
            status=PostStatus.DRAFT,
            template="kinda_post",
            caption="c",
            slides=[],
            created_at=MAINTENANT - dt.timedelta(minutes=30),
        )
    )
    db_session.commit()
    assert voyant_composition(db_session, MAINTENANT).niveau == OK


def test_le_jeton_de_sourcing_est_inconnu_tant_qu_aucun_passage_ne_l_a_releve(monkeypatch):
    monkeypatch.setattr(settings, "meta_fb_access_token", "EAAtest")
    _dossier()
    voyant = voyant_jeton_sourcing(MAINTENANT)
    assert voyant.niveau == INCONNU
    assert "jamais relevée" in voyant.raison


def test_le_jeton_de_sourcing_relu_du_disque_ne_coute_aucun_appel(monkeypatch):
    monkeypatch.setattr(settings, "meta_fb_access_token", "EAAtest")
    _ecrire(
        "sourcing_token_alert.json",
        {
            "expire_le": (MAINTENANT + dt.timedelta(days=3)).isoformat(),
            "releve_le": MAINTENANT.isoformat(),
        },
    )
    voyant = voyant_jeton_sourcing(MAINTENANT)
    assert voyant.niveau == ATTENTION
    assert "3 j" == voyant.valeur
    assert "ne se renouvelle PAS tout seul" in voyant.raison


def test_un_jeton_de_publication_expire_est_une_panne():
    _ecrire(
        "instagram_token.json",
        {"access_token": "IGAA", "expire_le": (MAINTENANT - dt.timedelta(days=1)).isoformat()},
    )
    voyant = voyant_jeton_publication(MAINTENANT)
    assert voyant.niveau == PANNE and voyant.valeur == "expiré"


def test_une_pause_de_quota_en_cours_est_une_panne_avec_son_motif():
    _ecrire(
        "quota_graph.json",
        {
            "jusqu_a": (MAINTENANT + dt.timedelta(minutes=20)).isoformat(),
            "motif": "(#4) Application request limit reached",
            "declaree_par": "veille rapide",
        },
    )
    voyant = voyant_quota(MAINTENANT)
    assert voyant.niveau == PANNE
    assert "Application request limit reached" in voyant.raison


def test_une_pause_de_quota_echue_ne_sonne_plus():
    _ecrire(
        "quota_graph.json",
        {"jusqu_a": (MAINTENANT - dt.timedelta(hours=3)).isoformat(), "motif": "ancien"},
    )
    assert voyant_quota(MAINTENANT).niveau == OK


def test_une_panne_de_masse_des_sources_est_distinguee_d_un_flux_casse(db_session):
    for index in range(6):
        _source(
            db_session,
            nom=f"ig{index}",
            etat={"last_error": "Meta HTTP 403 (code 4)", "last_error_at": MAINTENANT.isoformat()},
        )
    _source(db_session, nom="ok", etat={"last_ok_at": MAINTENANT.isoformat()})
    etat = lire_etat_sources(db_session, MAINTENANT)
    voyant = voyant_sources(etat)
    assert voyant.niveau == PANNE
    assert "panne de masse" in voyant.raison
    assert any("Meta HTTP 403" in ligne for ligne in voyant.details)


def test_un_etat_de_source_corrompu_rend_inconnu_pas_vert(db_session):
    source = _source(db_session, nom="bizarre")
    source.state = "pas un dict"
    db_session.commit()
    assert voyant_sources(lire_etat_sources(db_session, MAINTENANT)).niveau == INCONNU


def test_le_dernier_passage_se_deduit_du_dernier_succes(db_session):
    _source(db_session, nom="frais", etat={"last_ok_at": (MAINTENANT - dt.timedelta(minutes=20)).isoformat()})
    etat = lire_etat_sources(db_session, MAINTENANT)
    assert voyant_passage(db_session, MAINTENANT, etat).niveau == OK
    vieux = lire_etat_sources(db_session, MAINTENANT + dt.timedelta(hours=6))
    assert voyant_passage(db_session, MAINTENANT + dt.timedelta(hours=6), vieux).niveau == PANNE


def test_sans_aucune_lecture_consignee_le_passage_est_inconnu(db_session):
    _source(db_session, nom="neuve", etat=None)
    etat = lire_etat_sources(db_session, MAINTENANT)
    assert voyant_passage(db_session, MAINTENANT, etat).niveau == INCONNU


def test_un_creneau_manque_sans_raison_est_dit_tel_quel(db_session):
    db_session.add_all(
        [
            Creneau(
                publier_a=MAINTENANT - dt.timedelta(hours=4),
                format=FormatCreneau.CARROUSEL,
                etat=EtatCreneau.MANQUE,
            ),
            Creneau(
                publier_a=MAINTENANT - dt.timedelta(hours=3),
                format=FormatCreneau.REEL,
                etat=EtatCreneau.PUBLIE,
            ),
        ]
    )
    db_session.commit()
    voyant = voyant_creneaux(db_session, MAINTENANT)
    assert voyant.niveau == PANNE  # la moitie de la journee manquee
    assert "sans raison enregistrée" in voyant.raison
    assert any("sans raison enregistrée" in ligne for ligne in voyant.details)


# --- L'agent de publication : passe-t-il ? (2026-10-30) ---------------------

LONGTEMPS = dt.timedelta(days=2)


def _passage(age, **champs):
    _ecrire(
        "publication_passage.json",
        {"fin_le": (MAINTENANT - age).isoformat(), "mode": "reel", "panne": None, **champs},
    )


def _reveil(monkeypatch, il_y_a):
    """`None` : la date de reprise de la machine est illisible."""
    valeur = None if il_y_a is None else MAINTENANT - il_y_a
    monkeypatch.setattr(supervision, "reprise_machine", lambda: valeur)


def test_le_seuil_de_l_agent_derive_des_reglages(monkeypatch):
    """Deux intervalles, plus ce qu'un passage peut legitimement attendre."""
    assert seuil_agent_publication() == dt.timedelta(seconds=2 * 600 + 660 + 270)
    monkeypatch.setattr(settings, "publication_intervalle_s", 60)
    assert seuil_agent_publication() == dt.timedelta(seconds=2 * 60 + 660 + 270)


def test_aucun_passage_consigne_vaut_inconnu_jamais_vert(monkeypatch):
    _reveil(monkeypatch, LONGTEMPS)
    assert voyant_agent_publication(MAINTENANT).niveau == INCONNU


@pytest.mark.parametrize(
    "contenu",
    ["{pas du json", "[]", {"mode": "reel"}, {"fin_le": "hier soir"}, {"fin_le": 12}],
)
def test_un_passage_illisible_vaut_inconnu(monkeypatch, contenu):
    _reveil(monkeypatch, LONGTEMPS)
    _ecrire("publication_passage.json", contenu)
    assert voyant_agent_publication(MAINTENANT).niveau == INCONNU


def test_un_passage_date_du_futur_vaut_inconnu(monkeypatch):
    """Une horloge qui a bouge ne doit pas donner un vert pour des annees."""
    _reveil(monkeypatch, LONGTEMPS)
    _passage(-dt.timedelta(hours=3))
    assert voyant_agent_publication(MAINTENANT).niveau == INCONNU


def test_un_passage_recent_est_vert_y_compris_en_mode_arret(monkeypatch):
    _reveil(monkeypatch, LONGTEMPS)
    _passage(dt.timedelta(minutes=8), mode="arret")
    voyant = voyant_agent_publication(MAINTENANT)
    assert voyant.niveau == OK
    assert voyant.valeur == "il y a 8 min"


def test_un_passage_recent_mais_en_panne_n_est_pas_vert(monkeypatch):
    _reveil(monkeypatch, LONGTEMPS)
    _passage(dt.timedelta(minutes=8), panne="base injoignable")
    voyant = voyant_agent_publication(MAINTENANT)
    assert voyant.niveau == ATTENTION
    assert "base injoignable" in voyant.raison


def test_un_passage_trop_vieux_sur_une_machine_eveillee_est_une_panne(monkeypatch):
    _reveil(monkeypatch, LONGTEMPS)
    _passage(seuil_agent_publication() + dt.timedelta(minutes=1))
    voyant = voyant_agent_publication(MAINTENANT)
    assert voyant.niveau == PANNE
    assert "launchd" in voyant.raison


def test_un_passage_long_ne_crie_pas(monkeypatch):
    """Juste sous le seuil : intervalle, attente de l'heure, tunnel."""
    _reveil(monkeypatch, LONGTEMPS)
    _passage(seuil_agent_publication() - dt.timedelta(seconds=1))
    assert voyant_agent_publication(MAINTENANT).niveau == OK


def test_au_reveil_le_bandeau_ne_crie_JAMAIS_au_loup(monkeypatch):
    """LE cas : Mac ferme une nuit, ecran rouvert. Le dashboard (KeepAlive) repond
    aussitot, l'agent n'a pas encore fini son premier passage, et le fichier a
    huit heures. Ce n'est ni une panne, ni un vert : on ne sait pas encore."""
    _reveil(monkeypatch, dt.timedelta(minutes=2))
    _passage(dt.timedelta(hours=8))
    voyant = voyant_agent_publication(MAINTENANT)
    assert voyant.niveau == INCONNU
    assert "réveil" in voyant.raison


def test_apres_le_delai_de_grace_du_reveil_c_est_une_panne(monkeypatch):
    """Le reveil n'excuse pas indefiniment : un agent mort se dit, une fois qu'il a
    eu le temps de passer depuis le reveil."""
    _reveil(monkeypatch, seuil_agent_publication() + dt.timedelta(seconds=1))
    _passage(dt.timedelta(hours=8))
    assert voyant_agent_publication(MAINTENANT).niveau == PANNE


def test_un_passage_vieux_sans_date_de_reveil_vaut_inconnu(monkeypatch):
    """On ne peut separer ni le sommeil de la mort : ni vert, ni loup."""
    _reveil(monkeypatch, None)
    _passage(dt.timedelta(hours=8))
    assert voyant_agent_publication(MAINTENANT).niveau == INCONNU


def _sysctl(monkeypatch, sortie=None, erreur=None):
    def run(*a, **k):
        if erreur:
            raise erreur
        return types.SimpleNamespace(stdout=sortie)

    monkeypatch.setattr(supervision.subprocess, "run", run)
    monkeypatch.setattr(supervision, "DEMARRAGE_PROCESSUS", dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))


def test_la_reprise_est_le_plus_recent_du_reveil_et_du_demarrage(monkeypatch):
    # Format reel de `sysctl -n` (observe sur la machine, 2026-10-30). `usec` ne
    # doit pas etre pris pour `sec`.
    _sysctl(
        monkeypatch,
        "{ sec = 1793390420, usec = 782724 } Fri Oct 30 21:00:20 2026\n"
        "{ sec = 1793372898, usec = 196348 } Fri Oct 30 16:08:18 2026\n",
    )
    assert supervision.reprise_machine().timestamp() == 1793390420


def test_une_machine_jamais_endormie_reprend_a_son_demarrage(monkeypatch):
    _sysctl(monkeypatch, "{ sec = 0, usec = 0 }\n{ sec = 1793372898, usec = 196348 }\n")
    assert supervision.reprise_machine().timestamp() == 1793372898


def test_l_ouverture_de_session_compte_comme_une_reprise(monkeypatch):
    """Demarre puis reste a l'ecran de connexion : les agents launchd ne tournent
    qu'apres l'ouverture de session, donc `kern.boottime` serait trop ancien."""
    _sysctl(monkeypatch, "{ sec = 1000, usec = 0 }\n{ sec = 900, usec = 0 }\n")
    connexion = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(supervision, "DEMARRAGE_PROCESSUS", connexion)
    assert supervision.reprise_machine() == connexion


@pytest.mark.parametrize(
    "erreur", [FileNotFoundError("sysctl"), subprocess.TimeoutExpired("sysctl", 2), PermissionError("non")]
)
def test_un_sysctl_en_panne_rend_inconnu(monkeypatch, erreur):
    _sysctl(monkeypatch, erreur=erreur)
    assert supervision.reprise_machine() is None


def test_une_sortie_de_sysctl_incomprise_rend_inconnu(monkeypatch):
    _sysctl(monkeypatch, "kern.waketime: format inattendu\n")
    assert supervision.reprise_machine() is None


def test_une_reprise_dans_le_futur_vaut_inconnu(monkeypatch):
    """Elle ne doit pas se lire comme « la machine vient de se reveiller »."""
    monkeypatch.setattr(supervision, "reprise_machine", lambda: MAINTENANT + LONGTEMPS)
    _passage(dt.timedelta(hours=8))
    assert voyant_agent_publication(MAINTENANT).niveau == INCONNU


def test_le_bandeau_n_ecrit_rien_en_lisant(monkeypatch):
    _reveil(monkeypatch, LONGTEMPS)
    dossier = _dossier()
    avant = sorted(p.name for p in dossier.iterdir())
    voyant_agent_publication(MAINTENANT)
    assert sorted(p.name for p in dossier.iterdir()) == avant


# --- Un creneau passe et jamais traite ---------------------------------------


def _creneau(db, retard, etat, **champs):
    creneau = Creneau(
        publier_a=MAINTENANT - retard, format=FormatCreneau.CARROUSEL, etat=etat, **champs
    )
    db.add(creneau)
    db.commit()
    return creneau


def _au_dela(marge=dt.timedelta(minutes=1)):
    """Depasse le retard tolere ET le temps laisse a l'agent pour le constater."""
    return dt.timedelta(minutes=settings.publication_retard_max_minutes) + seuil_agent_publication() + marge


@pytest.mark.parametrize("etat", [EtatCreneau.PROGRAMME, EtatCreneau.GELE])
def test_un_creneau_passe_jamais_traite_est_une_panne(db_session, monkeypatch, etat):
    """Le « 0/6 publiés » vert : MANQUE n'est ecrit que par l'agent, donc un agent
    mort laissait ces creneaux hors de tous les compteurs."""
    _reveil(monkeypatch, LONGTEMPS)
    _creneau(db_session, _au_dela(), etat)
    voyant = voyant_creneaux(db_session, MAINTENANT)
    assert voyant.niveau == PANNE
    assert "agent" in voyant.raison


def test_un_creneau_dans_le_retard_tolere_reste_vert(db_session, monkeypatch):
    _reveil(monkeypatch, LONGTEMPS)
    _creneau(
        db_session,
        dt.timedelta(minutes=settings.publication_retard_max_minutes - 5),
        EtatCreneau.GELE,
    )
    assert voyant_creneaux(db_session, MAINTENANT).niveau == OK


def test_un_creneau_tout_juste_depasse_n_est_pas_encore_une_panne(db_session, monkeypatch):
    """L'agent sain ne passe que toutes les 10 min : pendant ce laps, le creneau
    depasse n'accuse personne. Il n'est pas vert pour autant."""
    _reveil(monkeypatch, LONGTEMPS)
    _creneau(
        db_session,
        dt.timedelta(minutes=settings.publication_retard_max_minutes + 1),
        EtatCreneau.PROGRAMME,
    )
    assert voyant_creneaux(db_session, MAINTENANT).niveau == ATTENTION


def test_au_reveil_un_creneau_depasse_n_accuse_pas_l_agent(db_session, monkeypatch):
    """Mac ferme toute la matinee : le creneau a bien ete rate (ATTENTION, c'est
    vrai), mais l'agent n'a pas encore pu l'ecrire -- pas de panne."""
    _reveil(monkeypatch, dt.timedelta(minutes=2))
    _creneau(db_session, _au_dela(), EtatCreneau.GELE)
    voyant = voyant_creneaux(db_session, MAINTENANT)
    assert voyant.niveau == ATTENTION
    assert "réveil" in voyant.raison


def test_sans_date_de_reveil_un_creneau_depasse_vaut_inconnu(db_session, monkeypatch):
    _reveil(monkeypatch, None)
    _creneau(db_session, _au_dela(), EtatCreneau.GELE)
    assert voyant_creneaux(db_session, MAINTENANT).niveau == INCONNU


def test_un_creneau_de_la_veille_jamais_traite_se_voit_aussi(db_session, monkeypatch):
    """Le voyant s'appelle « du jour », mais un agent mort depuis hier soir ne doit
    pas redevenir vert a minuit."""
    _reveil(monkeypatch, LONGTEMPS)
    _creneau(db_session, dt.timedelta(hours=30), EtatCreneau.PROGRAMME)
    assert voyant_creneaux(db_session, MAINTENANT).niveau == PANNE


def test_le_depasse_ne_masque_pas_une_journee_manquee_plus_grave(db_session, monkeypatch):
    """Le pire des deux, jamais le dernier ecrit."""
    _reveil(monkeypatch, dt.timedelta(minutes=2))
    _creneau(db_session, dt.timedelta(hours=9), EtatCreneau.MANQUE, erreur="retard")
    _creneau(db_session, _au_dela(), EtatCreneau.GELE)
    voyant = voyant_creneaux(db_session, MAINTENANT)
    assert voyant.niveau == PANNE  # un sur deux manque : la regle d'avant, intacte
    assert "manqué" in voyant.raison


# --- Preuve negative, par mutation -------------------------------------------


def _module_mute(monkeypatch, avant, apres):
    """`app/supervision.py` recompile avec UNE ligne changee."""
    source = Path(supervision.__file__).read_text(encoding="utf-8")
    assert avant in source, "la mutation ne trouve plus sa cible : le code a change de forme"
    module = types.ModuleType("supervision_mutee")
    monkeypatch.setitem(sys.modules, "supervision_mutee", module)
    exec(compile(source.replace(avant, apres), "supervision_mutee", "exec"), module.__dict__)
    module.reprise_machine = lambda: MAINTENANT - LONGTEMPS
    return module


@pytest.mark.parametrize(
    ("avant", "apres", "age", "attendu"),
    [
        # Sans ce test, « trop vieux » pourrait redevenir vert sans rien faire rougir.
        ("    if age < seuil:\n", "    if True:\n", dt.timedelta(days=3), PANNE),
        # Un fichier absent peint en vert : l'`or OK` bien intentionne.
        (
            "    if lecture.absent:\n        return Voyant(cle, libelle, INCONNU,",
            "    if lecture.absent:\n        return Voyant(cle, libelle, OK,",
            None,
            INCONNU,
        ),
    ],
)
def test_le_voyant_de_l_agent_rougit_sous_mutation(monkeypatch, avant, apres, age, attendu):
    _reveil(monkeypatch, LONGTEMPS)
    if age is not None:
        _passage(age)
    assert voyant_agent_publication(MAINTENANT).niveau == attendu
    mute = _module_mute(monkeypatch, avant, apres)
    assert mute.voyant_agent_publication(MAINTENANT).niveau == OK


def test_le_reveil_sans_garde_crierait_au_loup_preuve_par_mutation(monkeypatch):
    """Retirer la garde du reveil : le meme fichier passe en PANNE."""
    _reveil(monkeypatch, dt.timedelta(minutes=2))
    _passage(dt.timedelta(hours=8))
    assert voyant_agent_publication(MAINTENANT).niveau == INCONNU
    mute = _module_mute(
        monkeypatch,
        "    return maintenant - reprise >= seuil_agent_publication()\n",
        "    return True\n",
    )
    mute.reprise_machine = lambda: MAINTENANT - dt.timedelta(minutes=2)
    assert mute.voyant_agent_publication(MAINTENANT).niveau == PANNE


def test_le_creneau_jamais_traite_rougit_sous_mutation(db_session, monkeypatch):
    """Retirer le constat : retour exact au « 0/6 publiés » vert."""
    _reveil(monkeypatch, LONGTEMPS)
    _creneau(db_session, _au_dela(), EtatCreneau.GELE)
    assert voyant_creneaux(db_session, MAINTENANT).niveau == PANNE
    mute = _module_mute(monkeypatch, "    if not orphelins:\n", "    if True:\n")
    assert mute.voyant_creneaux(db_session, MAINTENANT).niveau == OK


# --- L'endpoint -------------------------------------------------------------


def test_l_endpoint_rend_tous_les_voyants_et_le_niveau(client):
    reponse = client.get("/supervision")
    assert reponse.status_code == 200
    corps = reponse.json()
    cles = {v["cle"] for v in corps["voyants"]}
    assert {
        "mode",
        "agent_publication",
        "composition",
        "creneaux",
        "passage",
        "sources",
        "quota",
        "pauses",
        "jeton_publication",
        "jeton_sourcing",
    } <= cles
    assert corps["niveau"] in {"ok", "attention", "panne", "inconnu"}
    # Base vide, aucun fichier d'etat : rien ne permet de dire « tout va bien ».
    assert corps["niveau"] != "ok"


def test_l_api_sources_expose_la_sante(client, db_session):
    _source(
        db_session,
        nom="Mixmag",
        etat={"last_error": "HTTP 403", "last_error_at": MAINTENANT.isoformat()},
    )
    ligne = client.get("/sources").json()[0]
    assert ligne["last_error"] == "HTTP 403"
    assert ligne["last_error_at"] is not None
    assert ligne["last_ok_at"] is None
