"""Regroupement des sources qui sont le meme media sur plusieurs canaux
(`scripts/grouper_sources.py`) et la preuve du piege qu'il corrige dans
`app/pipeline/events.py::_recalculer`."""

import datetime as dt


from app.models import Event, EventObservation, Source, SourceRole, SourceType
from app.pipeline import events
from scripts.grouper_sources import GROUPES, grouper_sources

UTC = dt.timezone.utc


def _source(db, name, type_, *, source_group_id=None):
    src = Source(name=name, type=type_, url=f"https://exemple.test/{name}", source_group_id=source_group_id)
    db.add(src)
    db.commit()
    return src


def _observation(db, event, src, *, platform, moment):
    obs = EventObservation(
        event_id=event.id, article_id=None, platform=platform,
        source_id=src.id, source_role=SourceRole.PRIMARY,
        published_at=moment, observed_at=moment,
        title="Kacey Musgraves aux VMAs", url=f"https://archive.test/{src.id}",
        source_name=src.name,
    )
    db.add(obs)
    db.commit()
    return obs


# -- le piege : les DEUX membres doivent porter la MEME valeur -------------


def test_meme_media_sur_deux_canaux_compte_pour_une_seule_source_independante(db_session):
    """Reproduit le cas Kacey Musgraves / Billboard : un evenement observe par
    Billboard (RSS) et Billboard (IG), les deux portant le meme
    `source_group_id` -- `independent_source_count` doit tomber a 1 alors que
    `source_count` reste a 2."""
    db_session.autoflush = False
    rss = _source(db_session, "Billboard (RSS)", SourceType.RSS, source_group_id=46)
    ig = _source(db_session, "Billboard (IG)", SourceType.INSTAGRAM, source_group_id=46)

    maintenant0 = dt.datetime.now(UTC)
    event = Event(titre="Kacey Musgraves aux VMAs", first_seen_at=maintenant0, last_seen_at=maintenant0)
    db_session.add(event)
    db_session.commit()

    maintenant = dt.datetime.now(UTC)
    _observation(db_session, event, rss, platform=SourceType.RSS, moment=maintenant)
    _observation(db_session, event, ig, platform=SourceType.INSTAGRAM, moment=maintenant)

    events._recalculer(db_session, event)

    assert event.source_count == 2
    assert event.independent_source_count == 1


def test_sans_groupe_les_deux_canaux_comptent_pour_deux_sources_independantes(db_session):
    """Le meme scenario, mais SANS `source_group_id` : c'est le bug mesure en
    production -- deux confirmations au lieu d'une."""
    db_session.autoflush = False
    rss = _source(db_session, "Billboard (RSS)", SourceType.RSS)
    ig = _source(db_session, "Billboard (IG)", SourceType.INSTAGRAM)

    maintenant0 = dt.datetime.now(UTC)
    event = Event(titre="Kacey Musgraves aux VMAs", first_seen_at=maintenant0, last_seen_at=maintenant0)
    db_session.add(event)
    db_session.commit()

    maintenant = dt.datetime.now(UTC)
    _observation(db_session, event, rss, platform=SourceType.RSS, moment=maintenant)
    _observation(db_session, event, ig, platform=SourceType.INSTAGRAM, moment=maintenant)

    events._recalculer(db_session, event)

    assert event.source_count == 2
    assert event.independent_source_count == 2


def test_demi_groupe_ne_sert_a_rien(db_session):
    """Si un seul des deux membres porte le groupe, les deux tombent sous des
    cles differentes (`("groupe", id)` contre `("source", id)`) -- le
    regroupement ne doit JAMAIS etre pose a moitie par le script (verifie plus
    bas), mais on prouve ici que _recalculer lui-meme ne rattrape rien si
    c'est le cas."""
    db_session.autoflush = False
    rss = _source(db_session, "Billboard (RSS)", SourceType.RSS, source_group_id=46)
    ig = _source(db_session, "Billboard (IG)", SourceType.INSTAGRAM)  # groupe absent

    maintenant0 = dt.datetime.now(UTC)
    event = Event(titre="Kacey Musgraves aux VMAs", first_seen_at=maintenant0, last_seen_at=maintenant0)
    db_session.add(event)
    db_session.commit()

    maintenant = dt.datetime.now(UTC)
    _observation(db_session, event, rss, platform=SourceType.RSS, moment=maintenant)
    _observation(db_session, event, ig, platform=SourceType.INSTAGRAM, moment=maintenant)

    events._recalculer(db_session, event)

    assert event.independent_source_count == 2  # le regroupement a moitie ne sert a rien


# -- scripts/grouper_sources.py --------------------------------------------


def test_plan_ne_modifie_rien_sans_apply(db_session):
    db_session.autoflush = False
    rss = _source(db_session, "Mixmag", SourceType.RSS)
    ig = _source(db_session, "Mixmag (IG)", SourceType.INSTAGRAM)

    plan, absents = grouper_sources(db_session, appliquer=False)

    assert len(plan) == 2
    db_session.refresh(rss)
    db_session.refresh(ig)
    assert rss.source_group_id is None
    assert ig.source_group_id is None


def test_apply_pose_la_meme_valeur_sur_les_deux_membres_reference_comprise(db_session):
    """Le piege central du script : la reference (plus petit id) doit AUSSI
    recevoir la valeur, pas seulement l'autre membre."""
    db_session.autoflush = False
    rss = _source(db_session, "Mixmag", SourceType.RSS)
    ig = _source(db_session, "Mixmag (IG)", SourceType.INSTAGRAM)
    assert rss.id < ig.id

    plan, absents = grouper_sources(db_session, appliquer=True)

    db_session.refresh(rss)
    db_session.refresh(ig)
    assert rss.source_group_id == rss.id
    assert ig.source_group_id == rss.id
    assert not any('absente de la base' in a and 'Mixmag' in a for a in absents)


def test_idempotent(db_session):
    db_session.autoflush = False
    _source(db_session, "Tsugi", SourceType.RSS)
    _source(db_session, "Tsugi (IG)", SourceType.INSTAGRAM)

    grouper_sources(db_session, appliquer=True)
    plan_second_passage, _ = grouper_sources(db_session, appliquer=True)

    assert plan_second_passage == []


def test_ne_jamais_ecraser_un_groupe_pose_a_la_main(db_session):
    db_session.autoflush = False
    rss = _source(db_session, "DJ Mag", SourceType.RSS)
    ig = _source(db_session, "DJ Mag (IG)", SourceType.INSTAGRAM, source_group_id=999)

    plan, _ = grouper_sources(db_session, appliquer=True)

    db_session.refresh(rss)
    db_session.refresh(ig)
    assert ig.source_group_id == 999  # jamais touche
    assert rss.source_group_id is None  # le groupe visait 999, jamais pose seul
    assert plan == []


def test_membre_absent_signale_sans_planter(db_session):
    db_session.autoflush = False
    _source(db_session, "Tsugi", SourceType.RSS)  # "Tsugi (IG)" absente

    plan, absents = grouper_sources(db_session, appliquer=False)

    assert plan == []  # un seul membre trouve : rien a regrouper
    assert any("Tsugi (IG)" in msg for msg in absents)


def test_base_vide_ne_plante_pas(db_session):
    plan, absents = grouper_sources(db_session, appliquer=False)
    assert plan == []
    assert len(absents) == len(GROUPES) * 2
