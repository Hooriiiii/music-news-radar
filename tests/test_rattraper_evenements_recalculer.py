"""`scripts/rattraper_evenements.py --recalculer` : propager un changement de
regroupement des sources (`scripts/grouper_sources.py`) aux `Event` DEJA en
base -- ils ne se recalculent jamais tout seuls (`independent_source_count`
et les autres compteurs sont STOCKES, voir le docstring du module).
"""

import datetime as dt

from sqlalchemy import select

from app.models import Article, ArticleStatus, Event, EventObservation, Source, SourceRole, SourceType
from scripts.rattraper_evenements import recalculer_tout

UTC = dt.timezone.utc


def _src(db, name, type_=SourceType.RSS, *, source_group_id=None):
    src = Source(
        name=name, type=type_, url=f"https://exemple.test/{name}",
        source_group_id=source_group_id,
    )
    db.add(src)
    db.commit()
    return src


def _event(db, *, titre="Un evenement"):
    now = dt.datetime.now(UTC)
    ev = Event(titre=titre, first_seen_at=now, last_seen_at=now)
    db.add(ev)
    db.commit()
    return ev


def _observation(db, event, src, *, moment, article=None):
    obs = EventObservation(
        event_id=event.id, article_id=article.id if article else None,
        platform=src.type, source_id=src.id, source_role=SourceRole.PRIMARY,
        published_at=moment, observed_at=moment,
        title="Titre", url=f"https://archive.test/{src.id}", source_name=src.name,
    )
    db.add(obs)
    db.commit()
    return obs


def _colonnes(obj):
    # `vars()` porte aussi l'etat interne SQLAlchemy (`_sa_instance_state`) --
    # on ne garde que les colonnes reelles.
    return {k: v for k, v in vars(obj).items() if not k.startswith("_sa_")}


def _empreinte(db):
    """Snapshot complet et ordonne des tables que le recalcul ne doit PAS
    toucher -- toute colonne d'`Article` ou d'`EventObservation`."""
    return (
        [_colonnes(a) for a in db.scalars(select(Article).order_by(Article.id))],
        [_colonnes(o) for o in db.scalars(select(EventObservation).order_by(EventObservation.id))],
    )


def test_plan_ne_modifie_rien_sans_apply(db_session):
    db_session.autoflush = False
    rss = _src(db_session, "Billboard (RSS)", source_group_id=46)
    ig = _src(db_session, "Billboard (IG)", SourceType.INSTAGRAM, source_group_id=46)
    event = _event(db_session, titre="Kacey Musgraves aux VMAs")
    maintenant = dt.datetime.now(UTC)
    _observation(db_session, event, rss, moment=maintenant)
    _observation(db_session, event, ig, moment=maintenant)
    # Compte perime, comme si le groupement avait ete pose sans propagation.
    event.source_count = 2
    event.independent_source_count = 2
    db_session.commit()

    rapport = recalculer_tout(db_session, appliquer=False)

    assert rapport.total_evenements == 1
    assert len(rapport.lignes) == 1
    assert any("independent_source_count" in d for d in rapport.lignes[0].diffs)

    db_session.refresh(event)
    assert event.independent_source_count == 2  # rien ecrit


def test_apply_corrige_le_compteur_perime(db_session):
    db_session.autoflush = False
    rss = _src(db_session, "Billboard (RSS)", source_group_id=46)
    ig = _src(db_session, "Billboard (IG)", SourceType.INSTAGRAM, source_group_id=46)
    event = _event(db_session, titre="Kacey Musgraves aux VMAs")
    maintenant = dt.datetime.now(UTC)
    _observation(db_session, event, rss, moment=maintenant)
    _observation(db_session, event, ig, moment=maintenant)
    event.source_count = 2
    event.independent_source_count = 2
    db_session.commit()

    rapport = recalculer_tout(db_session, appliquer=True)

    assert len(rapport.lignes) == 1
    db_session.refresh(event)
    assert event.source_count == 2
    assert event.independent_source_count == 1  # meme media, corrige


def test_idempotent(db_session):
    db_session.autoflush = False
    rss = _src(db_session, "Tsugi")
    ig = _src(db_session, "Tsugi (IG)", SourceType.INSTAGRAM)
    event = _event(db_session)
    maintenant = dt.datetime.now(UTC)
    _observation(db_session, event, rss, moment=maintenant)
    _observation(db_session, event, ig, moment=maintenant)

    recalculer_tout(db_session, appliquer=True)
    rapport_second_passage = recalculer_tout(db_session, appliquer=True)

    assert rapport_second_passage.lignes == []


def test_evenement_deja_a_jour_ne_bouge_pas(db_session):
    """Un evenement dont le compte est deja correct ne remonte aucune ligne
    -- verifie que le rapport liste bien ce qui CHANGERAIT, pas tout."""
    db_session.autoflush = False
    src = _src(db_session, "Mixmag")
    event = _event(db_session)
    maintenant = dt.datetime.now(UTC)
    _observation(db_session, event, src, moment=maintenant)
    # Compteurs deja a jour (comme apres un premier `--recalculer --apply`) --
    # sans appeler `_recalculer` d'abord, `Event` garde ses defauts a 0 et le
    # test prouverait l'inverse de ce qu'il annonce.
    recalculer_tout(db_session, appliquer=True)

    rapport = recalculer_tout(db_session, appliquer=False)

    assert rapport.total_evenements == 1
    assert rapport.lignes == []


def test_ne_touche_que_les_compteurs_d_event_empreinte_avant_apres(db_session):
    """Preuve par empreinte : aucun `Article` ni aucune `EventObservation`
    n'est cree, supprime ou modifie par le recalcul -- seules les colonnes de
    `Event` bougent, et les dates ne se remettent pas a bouger pour rien."""
    db_session.autoflush = False
    rss = _src(db_session, "DJ Mag")  # pas encore groupees
    ig = _src(db_session, "DJ Mag (IG)", SourceType.INSTAGRAM)
    article = Article(
        source_id=rss.id, url="https://djmag.example/1", title="Une actu",
        raw_hash=f"{1:064d}", published_at=dt.datetime.now(UTC), status=ArticleStatus.NEW,
    )
    db_session.add(article)
    db_session.commit()
    event = _event(db_session)
    maintenant = dt.datetime.now(UTC)
    _observation(db_session, event, rss, moment=maintenant, article=article)
    _observation(db_session, event, ig, moment=maintenant)

    # Etat de reference : un premier recalcul, avant tout groupement --
    # `first_seen_at`/`last_seen_at`/`independent_source_count` sont deja
    # corrects (2 sources non groupees).
    recalculer_tout(db_session, appliquer=True)
    assert event.independent_source_count == 2
    premier_seen_avant = event.first_seen_at
    dernier_seen_avant = event.last_seen_at

    # `scripts/grouper_sources.py --apply` vient de tourner : les deux
    # sources partagent maintenant un groupe. C'est CE recalcul-la que le
    # test observe.
    rss.source_group_id = rss.id
    ig.source_group_id = rss.id
    db_session.commit()

    avant = _empreinte(db_session)

    rapport = recalculer_tout(db_session, appliquer=True)

    apres = _empreinte(db_session)
    assert avant == apres  # aucun Article, aucune EventObservation touche

    # Pas de refresh ici : `expire_on_commit=False` sur cette session (comme
    # en production), donc `event` porte deja les valeurs ecrites par
    # `_recalculer` -- un refresh relirait des DateTime SQLite qui perdent
    # leur tzinfo et fausserait la comparaison sans rien dire du vrai bug.
    assert len(rapport.lignes) == 1
    assert event.independent_source_count == 1  # le compteur, lui, a change
    assert event.first_seen_at == premier_seen_avant  # jamais recule ni avance
    assert event.last_seen_at == dernier_seen_avant


def test_aucun_evenement_ne_plante_pas(db_session):
    rapport = recalculer_tout(db_session, appliquer=False)
    assert rapport.total_evenements == 0
    assert rapport.lignes == []
