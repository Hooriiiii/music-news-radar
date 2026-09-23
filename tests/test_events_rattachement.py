"""Phase 2A, Task 2 : la dedup alimente les evenements (`app/pipeline/events.py`).

Chaque test passe par `link_duplicates` (jamais par une ecriture directe de
`duplicate_of_id`) : c'est `cluster.lier()` qui appelle `events.attacher`, et
c'est ce chemin reel qu'il faut prouver correct. Le schema lui-meme (colonnes,
contrainte unique, survie a la purge) est deja verifie par `tests/test_events.py`
(Task 1) ; ce fichier verifie le RATTACHEMENT.
"""

import datetime as dt

from sqlalchemy import select

from app.models import Article, ArticleStatus, Event, EventObservation, Source, SourceCategorie, SourceType
from app.pipeline.cluster import link_duplicates
from app.pipeline.cluster_arbitrage import marquer_manuel
from app.pipeline.comptes_artistes import construire_index
from app.pipeline.election import elire_representants

UTC = dt.timezone.utc


def _src(db, name, *, categorie=None, source_group_id=None):
    src = Source(
        name=name, type=SourceType.RSS, url=f"https://{name}.example/feed",
        categorie=categorie, source_group_id=source_group_id,
    )
    db.add(src)
    db.commit()
    return src


def _art(db, src, *, n, title, hours_ago=1, status=ArticleStatus.NEW):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.example/{n}",
        title=title,
        raw_hash=f"{n:064d}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago),
        status=status,
    )
    db.add(art)
    db.commit()
    return art


def _events(db):
    return db.scalars(select(Event)).all()


def _observations(db, event_id):
    return db.scalars(
        select(EventObservation).where(EventObservation.event_id == event_id)
    ).all()


def _sans_fuseau(moment: dt.datetime) -> dt.datetime:
    """SQLite rend des datetime naifs : les comparaisons de ce fichier portent
    sur la VALEUR, jamais sur la presence d'un fuseau."""
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


# -- creation et extension ---------------------------------------------------


def test_fresh_link_creates_one_event_with_both_observations(db_session):
    db_session.autoflush = False
    # `_TABLES_PRESENTES` (`app/pipeline/events.py`) est desormais cle PAR
    # ENGINE (tour de correction 2, point 2) : la fixture `db_session` cree un
    # engine SQLite neuf a chaque test, donc ce test repasse TOUJOURS par
    # l'introspection de table (`_tables_disponibles`), sans reinitialisation
    # manuelle -- exactement le chemin que le bug de persistance (tour de
    # correction 1) empruntait.
    mixmag = _src(db_session, "mixmag")
    tsugi = _src(db_session, "tsugi")
    first = _art(db_session, mixmag, n=1, title="Dour Festival unveils its 2027 lineup",
                 hours_ago=5)
    second = _art(db_session, tsugi, n=2, title="Dour Festival devoile son line-up 2027",
                  hours_ago=2)

    link_duplicates(db_session)

    # Persistance REELLE (commit + refresh, pas seulement l'etat en memoire
    # d'objets qu'`expire_on_commit=False` ne rafraichit jamais tout seul) :
    # `attacher` a fait une introspection de table entre le flush de
    # `duplicate_of_id` et le commit final -- une regression qui la ferait
    # a nouveau passer par une connexion A PART romprait CETTE assertion sans
    # toucher aux autres, qui ne lisent que des objets fraichement crees.
    db_session.commit()
    db_session.refresh(second)
    assert second.duplicate_of_id == first.id

    evenements = _events(db_session)
    assert len(evenements) == 1
    event = evenements[0]
    observations = _observations(db_session, event.id)
    assert {o.article_id for o in observations} == {first.id, second.id}
    assert event.source_count == 2
    assert event.platform_count == 1  # les deux sont des flux RSS
    assert event.independent_source_count == 2  # aucun source_group_id partage
    assert _sans_fuseau(event.first_seen_at) == _sans_fuseau(first.published_at)
    assert _sans_fuseau(event.last_seen_at) == _sans_fuseau(second.published_at)
    assert event.titre == first.title  # le representant au moment de l'attache


def test_third_member_extends_the_same_event_and_recomputes_counts(db_session):
    db_session.autoflush = False
    a = _src(db_session, "media-a")
    b = _src(db_session, "media-b")
    c = _src(db_session, "media-c")
    plus_ancien = _art(db_session, a, n=1, title="Boris Brejcha announces Arenes de Frejus show",
                        hours_ago=6)
    _art(db_session, b, n=2, title="Boris Brejcha annonce un concert aux Arenes de Frejus",
         hours_ago=4)
    link_duplicates(db_session)
    (event,) = _events(db_session)
    assert event.source_count == 2

    # Un troisieme article, dans un passage SEPARE, rejoint le meme groupe.
    _art(db_session, c, n=3, title="Boris Brejcha : concert annonce aux Arenes de Frejus",
         hours_ago=1)
    link_duplicates(db_session)

    (event_apres,) = _events(db_session)
    assert event_apres.id == event.id  # toujours le MEME evenement, pas un second
    observations = _observations(db_session, event_apres.id)
    assert len(observations) == 3
    assert event_apres.source_count == 3
    assert event_apres.independent_source_count == 3
    assert _sans_fuseau(event_apres.first_seen_at) == _sans_fuseau(plus_ancien.published_at)


def test_leaves_distinct_stories_without_any_event(db_session):
    db_session.autoflush = False
    src = _src(db_session, "feed")
    _art(db_session, src, n=1, title="Boris Brejcha annonce une date aux Arenes de Frejus")
    _art(db_session, src, n=2, title="Les sorties electro de la semaine")

    link_duplicates(db_session)

    assert _events(db_session) == []


# -- idempotence --------------------------------------------------------------


def test_replaying_a_pass_does_not_duplicate_observations(db_session):
    db_session.autoflush = False
    mixmag = _src(db_session, "mixmag")
    tsugi = _src(db_session, "tsugi")
    _art(db_session, mixmag, n=1, title="Dour Festival unveils its 2027 lineup", hours_ago=5)
    _art(db_session, tsugi, n=2, title="Dour Festival devoile son line-up 2027", hours_ago=2)

    link_duplicates(db_session)
    (event_1,) = _events(db_session)
    observations_1 = _observations(db_session, event_1.id)

    # Rejouer le meme passage (comme le ferait un passage de veille sans rien
    # de neuf) ne doit rien dupliquer, ni creer un second evenement.
    link_duplicates(db_session)
    link_duplicates(db_session)

    evenements = _events(db_session)
    assert len(evenements) == 1
    observations_2 = _observations(db_session, evenements[0].id)
    assert len(observations_2) == len(observations_1) == 2
    assert {o.id for o in observations_2} == {o.id for o in observations_1}


# -- liens d'archive -----------------------------------------------------------


def test_archive_link_adds_an_observation_to_the_published_event(db_session):
    """Un article frais relie a une actu deja PUBLIEE (donc potentiellement
    deja purgee ailleurs) est une observation de plus sur le meme evenement."""
    db_session.autoflush = False
    ancien_src = _src(db_session, "media-ancien")
    publie = _art(
        db_session, ancien_src, n=1, title="Dour Festival annonce son line-up 2027",
        hours_ago=5 * 24, status=ArticleStatus.USED,
    )
    nouveau_src = _src(db_session, "media-nouveau")
    frais = _art(
        db_session, nouveau_src, n=2, title="Dour Festival devoile son line-up 2027",
        hours_ago=1,
    )

    stats = link_duplicates(db_session)
    assert stats.archive == 1

    (event,) = _events(db_session)
    observations = _observations(db_session, event.id)
    assert {o.article_id for o in observations} == {publie.id, frais.id}


def test_observation_survives_article_purge_with_denormalized_fields(db_session):
    """Meme philosophie qu'`artist_mentions` : la preuve reste lisible une fois
    l'article supprime. SQLite n'applique pas ON DELETE (voir
    tests/test_retention.py), donc on verifie la SURVIE de la ligne et
    l'integrite des champs denormalises, pas la mise a NULL en elle-meme."""
    db_session.autoflush = False
    src = _src(db_session, "tsugi")
    article = _art(db_session, src, n=1, title="Astropolis 2027 devoile sa premiere vague",
                   hours_ago=3)
    autre_src = _src(db_session, "mixmag")
    _art(db_session, autre_src, n=2, title="Astropolis 2027 : premiere vague d'artistes devoilee",
         hours_ago=1)
    link_duplicates(db_session)
    (event,) = _events(db_session)

    db_session.delete(article)
    db_session.commit()

    survivantes = _observations(db_session, event.id)
    assert len(survivantes) == 2
    preuve = next(o for o in survivantes if o.title == "Astropolis 2027 devoile sa premiere vague")
    assert preuve.url == article.url
    assert preuve.source_name == src.name


# -- independent_source_count respecte source_group_id -------------------------


def test_independent_source_count_respects_source_group_id(db_session):
    """Deux sources qui partagent un groupe (depeche reprise) comptent pour UNE
    seule source independante -- l'exemple du blueprint."""
    db_session.autoflush = False
    afp_1 = _src(db_session, "site-un", source_group_id=42)
    afp_2 = _src(db_session, "site-deux", source_group_id=42)
    autre = _src(db_session, "site-trois")

    _art(db_session, afp_1, n=1, title="Nouveau festival techno annonce a Rennes", hours_ago=5)
    _art(db_session, afp_2, n=2, title="Nouveau festival techno annonce a Rennes cet ete",
         hours_ago=4)
    _art(db_session, autre, n=3, title="Rennes accueille un nouveau festival techno cet ete",
         hours_ago=3)

    link_duplicates(db_session)

    (event,) = _events(db_session)
    assert event.source_count == 3
    assert event.independent_source_count == 2  # {groupe 42, autre.id}


# -- membre deja lie AVANT la mise en service de ce module (tour de correction 1, point 2) --


def test_previously_linked_member_becomes_an_observation_when_a_third_joins(db_session):
    """Reproduit la mesure de production : R et M sont deja lies (comme les
    550 groupes existants au moment de la mise en service, tous crees par
    l'ancien code, donc sans aucune observation), un troisieme article N
    rejoint le groupe. `attacher` ne doit pas se limiter aux deux articles que
    CET appel a `lier()` touche (N et R) -- il doit relire le groupe ENTIER et
    donner a M, invisible de cet appel, l'observation qu'il n'a jamais eue."""
    db_session.autoflush = False
    src_r = _src(db_session, "media-r")
    src_m = _src(db_session, "media-m")
    src_n = _src(db_session, "media-n")

    r = _art(db_session, src_r, n=1, title="Boris Brejcha announces Arenes de Frejus show",
             hours_ago=6)
    # M est deja lie a R, mais AUCUN evenement n'existe : exactement l'etat
    # d'un groupe forme avant que ce module n'existe.
    m = _art(db_session, src_m, n=2, title="Boris Brejcha : concert deja annonce", hours_ago=5)
    m.duplicate_of_id = r.id
    db_session.commit()
    assert _events(db_session) == []

    n = _art(db_session, src_n, n=3, title="Boris Brejcha annonce un concert aux Arenes de Frejus",
             hours_ago=1)

    link_duplicates(db_session)

    (event,) = _events(db_session)
    observations = _observations(db_session, event.id)
    assert {o.article_id for o in observations} == {r.id, m.id, n.id}
    assert event.source_count == 3  # PAS 2 : M n'est jamais oublie


# -- fusion d'evenements (le piege de l'aplatissement) --------------------------


def test_merging_a_pre_existing_group_into_an_archive_link_keeps_one_event(db_session, tmp_path):
    """Reproduit `test_i2` de `test_cluster.py` : D represente deja P depuis un
    passage anterieur ; un verdict manuel relie ensuite D a l'archive R. Les
    DEUX evenements prealables (celui de D+P, celui que R aurait pu creer) ne
    doivent jamais coexister -- un seul evenement doit porter les trois.

    Q est ajoute pour forcer la VRAIE fusion (`events._fusionner`) : sans lui,
    R n'a encore aucun evenement au moment ou D le rejoint, et `attacher`
    prend le chemin plus simple « adopter l'evenement de D » -- jamais celui
    qui deplace les observations d'un evenement existant vers un autre. Q,
    plus ancien que D, cree l'evenement de R AVANT que D ne le rejoigne : les
    deux groupes preexistants (R+Q, D+P) doivent alors fusionner en un seul."""
    db_session.autoflush = False
    chemin = tmp_path / "cluster_arbitrage.json"

    src_d, src_p = _src(db_session, "media-d"), _src(db_session, "media-p")
    d = _art(db_session, src_d, n=1, title="Dour Festival unveils its 2027 lineup", hours_ago=6)
    p = _art(db_session, src_p, n=2, title="Dour Festival devoile son line-up 2027", hours_ago=4)
    link_duplicates(db_session, cache_chemin=chemin)
    db_session.refresh(p)
    assert p.duplicate_of_id == d.id

    (event_avant,) = _events(db_session)
    assert {o.article_id for o in _observations(db_session, event_avant.id)} == {d.id, p.id}

    src_r, src_q = _src(db_session, "media-r"), _src(db_session, "media-q")
    r = _art(db_session, src_r, n=3, title="Dour Festival annonce son line-up 2027",
             hours_ago=5 * 24, status=ArticleStatus.USED)
    # Q, plus ancien que D (7h) : traite AVANT D dans la passe, il cree
    # l'evenement de R en premier -- condition necessaire a la vraie fusion.
    q = _art(db_session, src_q, n=4, title="Dour Festival annonce son line-up 2027", hours_ago=7)
    marquer_manuel(d.id, r.id, True, "confirme a la main", chemin)

    link_duplicates(db_session, cache_chemin=chemin)

    db_session.refresh(d)
    db_session.refresh(p)
    db_session.refresh(q)
    assert d.duplicate_of_id == r.id
    assert p.duplicate_of_id == r.id  # jamais P -> D -> R
    assert q.duplicate_of_id == r.id

    evenements = _events(db_session)
    assert len(evenements) == 1  # jamais deux evenements pour un seul groupe
    observations = _observations(db_session, evenements[0].id)
    assert {o.article_id for o in observations} == {d.id, p.id, q.id, r.id}
    assert evenements[0].source_count == 4
    # Tour de correction 2, point 1 : la fusion garde l'evenement CIBLE (ici
    # celui de D+P, cree en premier -- plus petit id) mais son TITRE doit
    # suivre le REPRESENTANT FINAL (R), jamais rester celui du representant
    # d'origine de la cible (D) -- mesure en revue : l'evenement affichait
    # « Dour Festival unveils its 2027 lineup » (D) alors que R en est le
    # representant. La Task 4 affiche ce champ tel quel au dashboard.
    assert evenements[0].titre == r.title


# -- reelection : le titre suit, jamais un second evenement --------------------


def test_reelection_updates_the_event_title_without_creating_a_second_event(db_session):
    db_session.autoflush = False
    mixmag = _src(db_session, "Mixmag", categorie=SourceCategorie.MEDIA)
    djmag = _src(db_session, "DJ Mag", categorie=SourceCategorie.MEDIA)
    vague = _art(
        db_session, mixmag, n=1,
        title="Fred again.. and more teased for GTA 6 soundtrack", hours_ago=3,
    )
    precis = _art(
        db_session, djmag, n=2,
        title="GTA VI full soundtrack announced featuring Fred again.., more", hours_ago=2,
    )

    link_duplicates(db_session)
    db_session.refresh(precis)
    assert precis.duplicate_of_id == vague.id  # le plus ancien reste representant

    (event,) = _events(db_session)
    assert event.titre == vague.title

    index = construire_index(db_session)
    stats = elire_representants(db_session, index, maintenant=dt.datetime.now(UTC))
    assert stats.reelus == 1

    db_session.refresh(vague)
    db_session.refresh(precis)
    assert precis.duplicate_of_id is None  # devenu le nouveau representant
    assert vague.duplicate_of_id == precis.id

    evenements = _events(db_session)
    assert len(evenements) == 1  # toujours le meme evenement
    assert evenements[0].id == event.id
    assert evenements[0].titre == precis.title  # le titre a suivi la reelection
    observations = _observations(db_session, evenements[0].id)
    assert {o.article_id for o in observations} == {vague.id, precis.id}
