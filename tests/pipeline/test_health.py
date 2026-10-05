"""La supervision : ce qui doit déclencher une alerte, et ce qui ne doit pas."""

import datetime as dt

from app.config import settings
from app.pipeline.cluster import ClusterStats
from app.models import Source, SourceType
from app.pipeline.health import CREDITS_EPUISES, diagnostiquer, marquer_sante
from app.pipeline.ingest import IngestStats
from app.pipeline.scoring import ScoringStats


def _source(db, nom, state=None, active=True):
    s = Source(name=nom, type=SourceType.RSS, url=f"http://{nom}", active=active, state=state)
    db.add(s)
    db.commit()
    return s


def _il_y_a(jours):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=jours)).isoformat()


def test_tout_va_bien_ne_dit_rien(db_session):
    """Le socle est SILENCIEUX quand rien ne cloche : une alerte creuse apprend à
    ignorer le canal."""
    _source(db_session, "tsugi", state={"last_ok_at": _il_y_a(0)})
    problemes = diagnostiquer(db_session, [IngestStats(1, "tsugi", new=3)], ScoringStats(scored=5))
    assert problemes == []


def test_une_source_muette_depuis_trop_longtemps_est_signalee(db_session):
    _source(db_session, "x-radar", state={"last_ok_at": _il_y_a(settings.source_muette_jours + 1)})
    (probleme,) = diagnostiquer(db_session, [], ScoringStats(scored=1))
    assert "x-radar" in probleme


def test_une_source_calme_mais_recente_ne_l_est_pas(db_session):
    _source(db_session, "x-radar", state={"last_ok_at": _il_y_a(settings.source_muette_jours - 1)})
    assert diagnostiquer(db_session, [], ScoringStats(scored=1)) == []


def test_une_source_inactive_est_ignoree(db_session):
    """Désactivée à la main : son silence est voulu, pas une panne."""
    _source(db_session, "vieille", state={"last_ok_at": _il_y_a(99)}, active=False)
    assert diagnostiquer(db_session, [], ScoringStats(scored=1)) == []


def test_une_source_sans_historique_n_alerte_pas(db_session):
    """Source tout juste ajoutée : pas encore de `last_ok_at`, ce n'est pas une
    panne. Alerter ici ferait sonner chaque nouvelle source."""
    _source(db_session, "neuve", state=None)
    assert diagnostiquer(db_session, [], ScoringStats(scored=1)) == []


def test_le_scoring_en_echec_est_signale(db_session):
    (probleme,) = diagnostiquer(db_session, [], ScoringStats(scored=0, errors=4))
    assert "scoring" in probleme.lower()


def test_les_credits_epuises_sont_nommes_explicitement(db_session):
    """Ne pas laisser une panne de facturation passer pour une erreur réseau :
    c'est ce qui l'a rendue invisible pendant des jours."""
    stats = ScoringStats(scored=0, errors=2, derniere_erreur="credit balance is too low")
    (probleme,) = diagnostiquer(db_session, [], stats)
    assert CREDITS_EPUISES in probleme


def test_les_sources_en_erreur_sur_ce_run_sont_signalees(db_session):
    _source(db_session, "mixmag")
    problemes = diagnostiquer(
        db_session, [IngestStats(1, "mixmag", error="403 Forbidden")], ScoringStats(scored=1)
    )
    assert any("mixmag" in p for p in problemes)


def test_marquer_sante_pose_last_ok_at_sur_un_succes(db_session):
    s = _source(db_session, "tsugi")
    marquer_sante(s, IngestStats(s.id, "tsugi", new=2))
    assert s.state["last_ok_at"]
    assert "last_error" not in s.state


def test_marquer_sante_conserve_l_etat_existant(db_session):
    """`sources.state` porte AUSSI le since_id de l'adapter X : l'écraser
    ferait re-payer tous les tweets déjà lus."""
    s = _source(db_session, "x", state={"since_id": "1700", "user_id": "42"})
    marquer_sante(s, IngestStats(s.id, "x", new=1))
    assert s.state["since_id"] == "1700"
    assert s.state["user_id"] == "42"


def test_marquer_sante_enregistre_l_erreur_sans_effacer_le_dernier_succes(db_session):
    """Le dernier succès reste : c'est lui qui dit DEPUIS QUAND la source est
    muette, l'erreur du jour ne le remplace pas."""
    s = _source(db_session, "mixmag", state={"last_ok_at": _il_y_a(2)})
    ancien = s.state["last_ok_at"]
    marquer_sante(s, IngestStats(s.id, "mixmag", error="403"))
    assert s.state["last_ok_at"] == ancien
    assert "403" in s.state["last_error"]


# --- Câblage dans le run ---


def test_run_pipeline_notifie_quand_ca_va_mal(db_session):
    """La chaîne complète : un problème détecté doit REELLEMENT partir."""
    from app.pipeline.run import run_pipeline

    envoyees = []
    _source(db_session, "morte", state={"last_ok_at": _il_y_a(99)})

    run_pipeline(
        db_session,
        ingest=lambda db: [],
        cluster=lambda db: ClusterStats(),
        score=lambda db: ScoringStats(scored=1),
        record=lambda db: 0,
        alert=lambda db: None,
        purge=lambda db, jours: 0,
        notify=lambda titre, message: envoyees.append(message) or True,
    )

    assert envoyees, "un probleme detecte doit declencher une notification"
    assert "morte" in envoyees[0]


def test_run_pipeline_reste_muet_quand_tout_va_bien(db_session):
    from app.pipeline.run import run_pipeline

    envoyees = []
    _source(db_session, "ok", state={"last_ok_at": _il_y_a(0)})

    run_pipeline(
        db_session,
        ingest=lambda db: [],
        cluster=lambda db: ClusterStats(),
        score=lambda db: ScoringStats(scored=3),
        record=lambda db: 0,
        alert=lambda db: None,
        purge=lambda db, jours: 0,
        notify=lambda titre, message: envoyees.append(message) or True,
    )

    assert envoyees == []


def test_un_notifieur_casse_ne_casse_pas_le_run(db_session):
    """Le canal d'alerte est la DERNIERE chose qui doit pouvoir tuer le run
    qu'il surveille."""
    from app.pipeline.run import run_pipeline

    _source(db_session, "morte", state={"last_ok_at": _il_y_a(99)})

    def notifieur_casse(titre, message):
        raise RuntimeError("osascript a disparu")

    rapport = run_pipeline(
        db_session,
        ingest=lambda db: [],
        cluster=lambda db: ClusterStats(),
        score=lambda db: ScoringStats(scored=1),
        record=lambda db: 0,
        alert=lambda db: None,
        purge=lambda db, jours: 0,
        notify=notifieur_casse,
    )
    assert rapport.problemes, "le diagnostic doit rester dans le rapport"
