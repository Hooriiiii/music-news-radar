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

import pytest

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
    superviser,
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


# --- L'endpoint -------------------------------------------------------------


def test_l_endpoint_rend_tous_les_voyants_et_le_niveau(client):
    reponse = client.get("/supervision")
    assert reponse.status_code == 200
    corps = reponse.json()
    cles = {v["cle"] for v in corps["voyants"]}
    assert {
        "mode",
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
