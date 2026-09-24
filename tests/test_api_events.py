"""`GET /events` et `GET /events/{id}` (Phase 2A, Task 4) : lecture seule.

Rien ici ne doit ecrire quoi que ce soit -- aucune route POST/PATCH/DELETE
n'existe sur `/events`, voir la note "Ce qu'il ne faut PAS faire" du plan.

Tour de correction 1 : `db_session.autoflush = False` dans chaque test, par
coherence avec le reste de la Phase 2A (`app/db.py:40` est le reglage de
production ; sans risque ici -- ces chemins sont en lecture seule -- mais un
test qui tourne en autoflush par defaut a deja masque un defaut de production
dans ce depot, voir CLAUDE.md).
"""

import datetime as dt

from app.models import Article, Event, EventObservation, Source, SourceRole, SourceType

UTC = dt.timezone.utc


def _src(db, name="Feed"):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.test/f")
    db.add(src)
    db.commit()
    return src


def _article(db, src, *, n, relevance=None, duplicate_of_id=None, days_ago=0):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.test/{n}",
        title=f"Titre {n}",
        raw_hash=f"{n:064d}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(days=days_ago),
        relevance_score=relevance,
        duplicate_of_id=duplicate_of_id,
    )
    db.add(art)
    db.commit()
    return art


def _event(db, *, titre="Un evenement", first_seen_at=None, last_seen_at=None,
           source_count=1, platform_count=1, independent_source_count=1):
    now = dt.datetime.now(UTC)
    ev = Event(
        titre=titre,
        first_seen_at=first_seen_at or now,
        last_seen_at=last_seen_at or now,
        source_count=source_count,
        platform_count=platform_count,
        independent_source_count=independent_source_count,
    )
    db.add(ev)
    db.commit()
    return ev


def _observation(db, event, src, *, n=1, article=None, platform=SourceType.RSS, role=SourceRole.PRIMARY):
    obs = EventObservation(
        event_id=event.id,
        article_id=article.id if article else None,
        platform=platform,
        source_id=src.id if src else None,
        source_role=role,
        published_at=article.published_at if article else dt.datetime.now(UTC),
        title=article.title if article else f"Titre {n}",
        url=article.url if article else (f"https://{src.name}.test/{n}" if src else f"https://archive.test/{n}"),
        source_name=src.name if src else "Source disparue",
    )
    db.add(obs)
    db.commit()
    return obs


def test_list_events_tri_par_last_seen_at(client, db_session):
    db_session.autoflush = False
    src = _src(db_session)
    plus_ancien = _event(
        db_session, titre="Vieux",
        first_seen_at=dt.datetime.now(UTC) - dt.timedelta(hours=10),
        last_seen_at=dt.datetime.now(UTC) - dt.timedelta(hours=5),
    )
    plus_recent = _event(db_session, titre="Recent")
    _observation(db_session, plus_ancien, src)
    _observation(db_session, plus_recent, src)

    reponse = client.get("/events")
    assert reponse.status_code == 200
    titres = [e["titre"] for e in reponse.json()]
    assert titres == ["Recent", "Vieux"]


def test_list_events_tri_par_source_count(client, db_session):
    db_session.autoflush = False
    src = _src(db_session)
    faible = _event(db_session, titre="Une source", source_count=1, independent_source_count=1)
    fort = _event(db_session, titre="Trois sources", source_count=3, independent_source_count=3)
    _observation(db_session, faible, src)
    _observation(db_session, fort, src)

    reponse = client.get("/events", params={"sort": "source_count"})
    titres = [e["titre"] for e in reponse.json()]
    assert titres == ["Trois sources", "Une source"]


def test_list_events_porte_les_features_calculees(client, db_session):
    db_session.autoflush = False
    src = _src(db_session)
    event = _event(
        db_session,
        titre="Frais et corrobore",
        first_seen_at=dt.datetime.now(UTC),
        independent_source_count=3,
    )
    _observation(db_session, event, src)

    corps = client.get("/events").json()[0]
    assert corps["source_count"] == 1
    assert corps["independent_source_count"] == 3
    # 3 sources independantes -> K ~= 63 (barreme du blueprint), avec une
    # marge large pour ne pas coupler ce test a la formule exacte.
    assert 60 <= corps["corroboration"] <= 66
    # Vu a l'instant : quasi neuf, quelle que soit la demi-vie retenue.
    assert corps["freshness"] > 90


def test_get_event_avec_ses_observations(client, db_session):
    db_session.autoflush = False
    src = _src(db_session, name="Mixmag")
    event = _event(db_session, titre="Un evenement detaille")
    obs = _observation(db_session, event, src, n=1)

    reponse = client.get(f"/events/{event.id}")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["titre"] == "Un evenement detaille"
    assert len(corps["observations"]) == 1
    assert corps["observations"][0]["id"] == obs.id
    assert corps["observations"][0]["source_name"] == "Mixmag"


def test_get_event_inconnu_404(client, db_session):
    db_session.autoflush = False
    assert client.get("/events/999999").status_code == 404


def test_observation_archivee_reste_visible_sans_article(client, db_session):
    """Meme apres purge de l'article (FK SET NULL), l'observation denormalisee
    reste lisible via l'API -- c'est tout le point de l'archive."""
    db_session.autoflush = False
    event = _event(db_session, titre="Preuve archivee")
    obs = _observation(db_session, event, src=None, n=7)
    assert obs.article_id is None

    corps = client.get(f"/events/{event.id}").json()
    assert corps["observations"][0]["article_id"] is None
    assert corps["observations"][0]["title"] == "Titre 7"


def test_aucune_route_d_ecriture_sur_events():
    """2A observe, elle ne decide rien -- aucun verbe d'ecriture ne doit exister
    sur `/events`. Passe par le schema OpenAPI plutot que par `app.routes` --
    cette version de FastAPI n'y expose les sous-routeurs inclus qu'enveloppes
    (`_IncludedRouter`), sans `path`/`methods` a plat."""
    from app.main import app

    chemins = app.openapi()["paths"]
    methodes = set()
    for chemin, verbes in chemins.items():
        if chemin.startswith("/events"):
            methodes |= set(v.upper() for v in verbes)
    assert "get" in {m.lower() for m in methodes}
    assert not (methodes & {"POST", "PATCH", "PUT", "DELETE"})


# --- Tour de correction 1 : la pertinence de l'article representant --------


def test_relevance_representant_depuis_l_article_non_duplique(client, db_session):
    """Le representant est l'article du groupe dont `duplicate_of_id IS NULL` --
    seul point d'ecriture, `cluster.py`. Sa pertinence doit ressortir sur
    l'evenement, A COTE de la corroboration."""
    db_session.autoflush = False
    src = _src(db_session)
    representant = _article(db_session, src, n=1, relevance=72, duplicate_of_id=None)
    membre = _article(db_session, src, n=2, relevance=None, duplicate_of_id=representant.id)
    event = _event(db_session, titre="Annonce avec doublon")
    _observation(db_session, event, src, article=representant)
    _observation(db_session, event, src, article=membre)

    corps = client.get("/events").json()[0]
    assert corps["relevance_representant"] == 72

    corps_detail = client.get(f"/events/{event.id}").json()
    assert corps_detail["relevance_representant"] == 72


def test_relevance_representant_absente_si_article_purge(client, db_session):
    """L'article representant a disparu (purge a 14 j, FK SET NULL) : la
    pertinence est absente (`None`), jamais devinee -- l'evenement et son titre
    fige survivent, seule la pertinence redevient inconnue."""
    db_session.autoflush = False
    event = _event(db_session, titre="Un evenement dont le representant a disparu")
    _observation(db_session, event, src=None, n=1)  # article_id deja NULL

    corps = client.get(f"/events/{event.id}").json()
    assert corps["relevance_representant"] is None


def test_relevance_representant_ignore_les_membres_non_representants(client, db_session):
    """Si seul un membre (duplicate_of_id non NULL) subsiste en base -- le
    representant a ete purge en premier -- la pertinence reste `None` plutot
    que d'emprunter celle d'un membre qui n'est pas la source du fait."""
    db_session.autoflush = False
    src = _src(db_session)
    membre = _article(db_session, src, n=1, relevance=55, duplicate_of_id=999999)
    event = _event(db_session, titre="Representant disparu, membre restant")
    _observation(db_session, event, src, article=membre)

    corps = client.get(f"/events/{event.id}").json()
    assert corps["relevance_representant"] is None
