"""Branchement de `scripts/grouper_sources.py` sur le passage horaire
(`app.pipeline.source_groups.regrouper_sources_et_propager`).

Trois choses a prouver : le cas normal ne coute rien de plus (aucun groupe a
poser -> zero requete de propagation), le cas ou un groupe est pose propage
bien aux evenements CONCERNES (pas un recalcul complet), et une panne a
l'interieur de l'etape ne remonte jamais -- le passage horaire complet doit se
derouler normalement autour d'elle.
"""

import datetime as dt

from app.models import (
    Article,
    ArticleStatus,
    Event,
    EventObservation,
    Source,
    SourceRole,
    SourceType,
)
from app.pipeline.run import run_pipeline
from app.pipeline.source_groups import regrouper_sources_et_propager

UTC = dt.timezone.utc


def _source(db, name, type_=SourceType.RSS):
    src = Source(name=name, type=type_, url=f"https://{name.lower()}.example/feed")
    db.add(src)
    db.commit()
    return src


def _article(db, src, *, n, title="Titre", days_ago=0):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.example/{n}",
        title=title,
        raw_hash=f"{n:064d}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(days=days_ago),
        status=ArticleStatus.NEW,
    )
    db.add(art)
    db.commit()
    return art


def _event(db, *, titre="Un evenement", independent_source_count=2):
    now = dt.datetime.now(UTC)
    ev = Event(
        titre=titre,
        first_seen_at=now,
        last_seen_at=now,
        source_count=2,
        platform_count=2,
        independent_source_count=independent_source_count,
    )
    db.add(ev)
    db.commit()
    return ev


def _observation(db, event, article, source):
    obs = EventObservation(
        event_id=event.id,
        article_id=article.id,
        platform=source.type,
        source_id=source.id,
        source_role=SourceRole.SECONDARY,
        published_at=article.published_at,
        title=article.title,
        url=article.url,
        source_name=source.name,
    )
    db.add(obs)
    db.commit()
    return obs


def test_rien_a_regrouper_ne_touche_pas_aux_evenements(db_session):
    """Le cas normal : le mapping est deja applique, donc aucun plan --
    fonction rendue sans toucher `events`/`event_observations`."""
    db_session.autoflush = False
    rss = _source(db_session, "Mixmag", SourceType.RSS)
    ig = _source(db_session, "Mixmag (IG)", SourceType.INSTAGRAM)
    cible = min(rss.id, ig.id)
    rss.source_group_id = cible
    ig.source_group_id = cible
    db_session.commit()

    stats = regrouper_sources_et_propager(db_session)

    assert stats.sources_regroupees == 0
    assert stats.evenements_recalcules == 0
    assert stats.erreur is None


def test_un_groupe_pose_propage_seulement_aux_evenements_concernes(db_session):
    db_session.autoflush = False
    rss = _source(db_session, "Mixmag", SourceType.RSS)
    ig = _source(db_session, "Mixmag (IG)", SourceType.INSTAGRAM)
    # Une source non concernee, avec son propre evenement : ne doit pas etre
    # touchee par le recalcul cible.
    autre = _source(db_session, "Radio Nova", SourceType.RSS)

    art_rss = _article(db_session, rss, n=1, title="Une actu Mixmag")
    art_ig = _article(db_session, ig, n=2, title="Une actu Mixmag")
    art_autre = _article(db_session, autre, n=3, title="Autre chose")

    event_concerne = _event(db_session, titre="Actu Mixmag", independent_source_count=2)
    _observation(db_session, event_concerne, art_rss, rss)
    _observation(db_session, event_concerne, art_ig, ig)

    event_intact = _event(db_session, titre="Sans rapport", independent_source_count=1)
    _observation(db_session, event_intact, art_autre, autre)
    valeurs_intactes_avant = (
        event_intact.source_count,
        event_intact.platform_count,
        event_intact.independent_source_count,
    )

    stats = regrouper_sources_et_propager(db_session)

    assert stats.sources_regroupees == 2
    assert stats.evenements_recalcules == 1
    assert stats.erreur is None

    db_session.refresh(rss)
    db_session.refresh(ig)
    assert rss.source_group_id == ig.source_group_id == min(rss.id, ig.id)

    db_session.refresh(event_concerne)
    # Les deux canaux du meme media ne comptent plus que pour UNE seule
    # confirmation independante.
    assert event_concerne.independent_source_count == 1

    db_session.refresh(event_intact)
    assert (
        event_intact.source_count,
        event_intact.platform_count,
        event_intact.independent_source_count,
    ) == valeurs_intactes_avant


def test_un_conflit_de_groupe_deja_pose_ne_leve_pas_et_ne_propage_rien(db_session):
    """Comportement herite de `grouper_sources.py` : un `source_group_id`
    deja pose differemment n'est jamais ecrase -- ici, rien de neuf a
    propager."""
    db_session.autoflush = False
    rss = _source(db_session, "Mixmag", SourceType.RSS)
    ig = _source(db_session, "Mixmag (IG)", SourceType.INSTAGRAM)
    rss.source_group_id = 999999  # pose a la main, different de la cible attendue
    db_session.commit()

    stats = regrouper_sources_et_propager(db_session)

    assert stats.sources_regroupees == 0
    assert stats.evenements_recalcules == 0
    db_session.refresh(rss)
    db_session.refresh(ig)
    assert rss.source_group_id == 999999
    assert ig.source_group_id is None


def test_etape_qui_leve_ne_bloque_pas_la_propagation(db_session, monkeypatch):
    """Simule une panne interne (par exemple une table absente qui ferait
    lever une exception imprevue plutot que de degrader proprement) : la
    fonction doit rendre un rapport avec une erreur, jamais lever."""
    import app.pipeline.source_groups as source_groups_module

    def _casse(db, *, appliquer):
        raise RuntimeError("panne imprevue")

    monkeypatch.setattr(source_groups_module, "grouper_sources", _casse)

    stats = regrouper_sources_et_propager(db_session)

    assert stats.erreur is not None
    assert stats.sources_regroupees == 0


def test_run_pipeline_expose_les_compteurs_de_groupement(db_session, monkeypatch):
    from app.config import settings
    from app.pipeline.cluster import ClusterStats
    from app.pipeline.election import ElectionStats
    from app.pipeline.scoring import ScoringStats

    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "discord_webhook_url", None)

    vus = []

    def grouper(db):
        vus.append("groupement")
        from app.pipeline.source_groups import GroupementStats

        return GroupementStats(sources_regroupees=2, evenements_recalcules=1)

    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        cluster=lambda db: vus.append("cluster") or ClusterStats(),
        elire=lambda db: ElectionStats(),
        score=lambda db: ScoringStats(),
        record=lambda db: 0,
        illustrer=lambda db: type("S", (), {"illustres": 0})(),
        purge=lambda db, days: 0,
        grouper_sources=grouper,
    )

    assert vus == ["groupement", "cluster"]  # AVANT le cluster
    assert report.groupement_sources.sources_regroupees == 2
    assert report.groupement_sources.evenements_recalcules == 1


def test_run_pipeline_survit_a_une_panne_du_groupement_de_sources(db_session, monkeypatch):
    """La garantie centrale : une etape de groupement qui leve ne doit
    JAMAIS interrompre le passage horaire -- dedup, scoring et purge doivent
    se derouler normalement et produire les memes decisions qu'un passage sans
    cette etape."""
    from app.config import settings
    from app.pipeline.cluster import ClusterStats
    from app.pipeline.election import ElectionStats
    from app.pipeline.scoring import ScoringStats

    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "discord_webhook_url", None)

    def grouper_qui_leve(db):
        raise RuntimeError("boom")

    calls = []

    report = run_pipeline(
        db_session,
        ingest=lambda db: calls.append("ingest") or [],
        cluster=lambda db: calls.append("cluster") or ClusterStats(),
        elire=lambda db: calls.append("elire") or ElectionStats(),
        score=lambda db: calls.append("score") or ScoringStats(),
        record=lambda db: calls.append("record") or 0,
        illustrer=lambda db: calls.append("illustrer") or type("S", (), {"illustres": 0})(),
        purge=lambda db, days: calls.append("purge") or 3,
        grouper_sources=grouper_qui_leve,
    )

    # `run_pipeline` protege l'appel a `grouper_sources` par un try/except
    # (seconde ceinture, en plus de celle de `regrouper_sources_et_propager`
    # elle-meme -- testee separement dans
    # `test_etape_qui_leve_ne_bloque_pas_la_propagation`) : meme si
    # l'injection leve, le reste du passage se deroule normalement.
    # `score` n'apparait pas : ANTHROPIC_API_KEY est absente pour ce test
    # (comme dans les autres tests de `run_pipeline`), donc l'etape est deja
    # sautee independamment du groupement de sources.
    assert calls == ["ingest", "cluster", "elire", "illustrer", "record", "purge"]
    assert report.purged == 3


def test_panne_de_propagation_annule_aussi_le_groupement_deja_pose(db_session, monkeypatch):
    """Preuve par MUTATION de l'atomicite (tour de correction 2, 2026-09-24).

    Avant le correctif, `grouper_sources(appliquer=True)` committait seule,
    puis `recalculer_tout` committait ou annulait seule : une panne PENDANT
    le recalcul laissait les `source_group_id` deja committes, et le
    `rollback` de l'exception n'annulait QUE les compteurs. Le passage suivant
    trouvait alors un plan VIDE (sources deja alignees) et ne retentait plus
    jamais la propagation -- les compteurs restaient faux pour de bon.

    Ce test injecte la panne exactement a cet endroit (apres l'assignation
    des `source_group_id`, pendant le recalcul) et prouve les DEUX moities de
    la garantie : (1) le groupement lui-meme est annule, pas seulement les
    compteurs -- sinon le second appel ci-dessous verrait un plan vide et
    `sources_regroupees == 0` ; (2) le second appel, sans panne, retente tout
    depuis zero et reussit entierement."""
    db_session.autoflush = False
    rss = _source(db_session, "Mixmag", SourceType.RSS)
    ig = _source(db_session, "Mixmag (IG)", SourceType.INSTAGRAM)
    art_rss = _article(db_session, rss, n=1, title="Une actu Mixmag")
    art_ig = _article(db_session, ig, n=2, title="Une actu Mixmag")
    event = _event(db_session, titre="Actu Mixmag", independent_source_count=2)
    _observation(db_session, event, art_rss, rss)
    _observation(db_session, event, art_ig, ig)

    def recalcul_qui_casse(*args, **kwargs):
        raise RuntimeError("connexion perdue pendant la propagation")

    monkeypatch.setattr("app.pipeline.events.recalculer_tout", recalcul_qui_casse)

    stats1 = regrouper_sources_et_propager(db_session)

    assert stats1.erreur is not None
    assert stats1.sources_regroupees == 2  # le plan a bien ete calcule...
    db_session.refresh(rss)
    db_session.refresh(ig)
    # ... mais RIEN n'est reste committe : le groupement est annule avec les
    # compteurs, pas seulement les compteurs.
    assert rss.source_group_id is None
    assert ig.source_group_id is None
    db_session.refresh(event)
    assert event.independent_source_count == 2  # inchange lui aussi

    monkeypatch.undo()  # `recalculer_tout` retrouve son vrai comportement

    stats2 = regrouper_sources_et_propager(db_session)

    assert stats2.erreur is None
    assert stats2.sources_regroupees == 2  # RETENTE, pas un plan vide
    assert stats2.evenements_recalcules == 1
    db_session.refresh(rss)
    db_session.refresh(ig)
    assert rss.source_group_id == ig.source_group_id == min(rss.id, ig.id)
    db_session.refresh(event)
    assert event.independent_source_count == 1


def test_avertissements_sont_journalises(db_session, caplog):
    """Une source renommee au dashboard (le canal Instagram de Mixmag
    n'existe plus sous ce nom) fait tomber son groupe sous deux membres
    trouves : le groupe est saute -- `stats.avertissements` doit porter
    l'info, et elle doit etre journalisee, pas seulement calculee en
    silence."""
    db_session.autoflush = False
    _source(db_session, "Mixmag", SourceType.RSS)  # canal IG absent

    with caplog.at_level("WARNING", logger="app.pipeline.source_groups"):
        stats = regrouper_sources_et_propager(db_session)

    assert stats.avertissements
    assert any("avertissement" in message for message in caplog.messages)


def test_erreur_de_groupement_rejoint_les_problemes_notifies(db_session, monkeypatch):
    """`stats.erreur` doit rejoindre `report.problemes` pour declencher une
    notification -- sans cette ligne, une panne de regroupement des sources
    ne serait jamais signalee, meme forme que l'agent digest qui a echoue
    chaque jour pendant cinq semaines sans que rien ne le dise."""
    from app.config import settings
    from app.pipeline.cluster import ClusterStats
    from app.pipeline.election import ElectionStats
    from app.pipeline.scoring import ScoringStats
    from app.pipeline.source_groups import GroupementStats

    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "discord_webhook_url", None)

    notifications = []

    report = run_pipeline(
        db_session,
        ingest=lambda db: [],
        cluster=lambda db: ClusterStats(),
        elire=lambda db: ElectionStats(),
        score=lambda db: ScoringStats(),
        record=lambda db: 0,
        illustrer=lambda db: type("S", (), {"illustres": 0})(),
        purge=lambda db, days: 0,
        grouper_sources=lambda db: GroupementStats(erreur="connexion perdue"),
        notify=lambda titre, message: notifications.append((titre, message)),
    )

    assert any("regroupement de sources" in p for p in report.problemes)
    assert any("regroupement de sources" in message for _, message in notifications)
