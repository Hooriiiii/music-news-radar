import datetime as dt

from app.config import settings
from app.models import Article, ArticleStatus, Source, SourceType
from app.pipeline.cluster import link_duplicates, title_similarity

UTC = dt.timezone.utc


def _src(db, name):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.example/feed")
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


# -- similarité de titres -------------------------------------------------


def test_same_story_different_wording_is_similar():
    assert title_similarity(
        "Charlotte de Witte announces new EP on KNTXT",
        "Charlotte de Witte annonce un nouvel EP sur KNTXT",
    ) >= 0.6


def test_same_story_with_source_suffix_is_similar():
    assert title_similarity(
        "Astropolis 2027 devoile sa premiere vague d'artistes",
        "Astropolis 2027 : premiere vague d'artistes devoilee - Tsugi",
    ) >= 0.6


def test_different_stories_about_same_artist_are_not_similar():
    assert title_similarity(
        "Charlotte de Witte announces new EP on KNTXT",
        "Charlotte de Witte joue a Fort Boyard cet ete",
    ) < 0.6


def test_unrelated_titles_are_not_similar():
    assert title_similarity(
        "Les meilleurs festivals techno de l'ete 2026",
        "Aphex Twin ressort un album de 1994",
    ) < 0.6


# -- liaison en base ------------------------------------------------------


def test_links_duplicate_across_sources_to_the_earliest(db_session):
    mixmag = _src(db_session, "mixmag")
    tsugi = _src(db_session, "tsugi")
    first = _art(db_session, mixmag, n=1, title="Dour Festival unveils its 2027 lineup",
                 hours_ago=5)
    second = _art(db_session, tsugi, n=2, title="Dour Festival devoile son line-up 2027",
                  hours_ago=2)

    assert link_duplicates(db_session).total == 1
    db_session.refresh(first)
    db_session.refresh(second)
    assert first.duplicate_of_id is None  # le plus ancien reste le representant
    assert second.duplicate_of_id == first.id


def test_leaves_distinct_stories_alone(db_session):
    src = _src(db_session, "feed")
    a = _art(db_session, src, n=1, title="Boris Brejcha annonce une date aux Arenes de Frejus")
    b = _art(db_session, src, n=2, title="Les sorties electro de la semaine")

    assert link_duplicates(db_session).total == 0
    db_session.refresh(a)
    db_session.refresh(b)
    assert a.duplicate_of_id is None and b.duplicate_of_id is None


def test_is_idempotent(db_session):
    mixmag = _src(db_session, "mixmag")
    tsugi = _src(db_session, "tsugi")
    _art(db_session, mixmag, n=1, title="Dour Festival unveils its 2027 lineup", hours_ago=5)
    _art(db_session, tsugi, n=2, title="Dour Festival devoile son line-up 2027", hours_ago=2)

    assert link_duplicates(db_session).total == 1
    assert link_duplicates(db_session).total == 0  # deja lie -> plus rien a faire


def test_third_copy_points_to_the_same_representative(db_session):
    a_src, b_src, c_src = (_src(db_session, n) for n in ("a", "b", "c"))
    head = _art(db_session, a_src, n=1, title="Dour Festival unveils its 2027 lineup",
                hours_ago=6)
    second = _art(db_session, b_src, n=2, title="Dour Festival devoile son line-up 2027",
                  hours_ago=4)
    third = _art(db_session, c_src, n=3, title="Dour Festival : le line-up 2027 est tombe",
                 hours_ago=2)

    assert link_duplicates(db_session).total == 2
    db_session.refresh(second)
    db_session.refresh(third)
    # pas de chaine : tout pointe vers le representant, jamais vers un doublon
    assert second.duplicate_of_id == head.id
    assert third.duplicate_of_id == head.id


def test_ignores_articles_outside_the_window(db_session):
    a_src = _src(db_session, "a")
    b_src = _src(db_session, "b")
    _art(db_session, a_src, n=1, title="Dour Festival unveils its 2027 lineup", hours_ago=200)
    _art(db_session, b_src, n=2, title="Dour Festival devoile son line-up 2027", hours_ago=1)

    assert link_duplicates(db_session, window_hours=48).total == 0


# -- archive : une actu deja publiee ne ressort jamais ---------------------


def test_a_used_article_outside_the_window_catches_a_fresh_repeat(db_session):
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    published = _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
                     hours_ago=5 * 24, status=ArticleStatus.USED)
    repeat = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                 hours_ago=1)

    assert link_duplicates(db_session).total == 1
    db_session.refresh(published)
    db_session.refresh(repeat)
    assert repeat.duplicate_of_id == published.id


def test_a_used_representative_is_never_marked_a_duplicate_itself(db_session):
    # L'archive ne pretend jamais qu'une actu deja publiee est le doublon d'une
    # autre plus recente -- elle ne sert que de representant possible.
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    published = _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
                     hours_ago=5 * 24, status=ArticleStatus.USED)
    _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027", hours_ago=1)

    link_duplicates(db_session)
    db_session.refresh(published)
    assert published.duplicate_of_id is None


def test_archive_window_bounds_the_comparison(db_session, monkeypatch):
    monkeypatch.setattr(settings, "cluster_archive_jours", 30)
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
         hours_ago=40 * 24, status=ArticleStatus.USED)
    repeat = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                 hours_ago=1)

    assert link_duplicates(db_session).total == 0
    db_session.refresh(repeat)
    assert repeat.duplicate_of_id is None


def test_archive_candidates_are_tried_oldest_first(db_session):
    # Tour de correction 1/5 : la requete sur l'archive doit trier par date
    # (comme celle sur les articles frais), sinon le representant retenu
    # depend de l'ordre physique des lignes -- non garanti par SQL, encore
    # moins par Postgres/Neon en production. Insertion dans l'ordre qui
    # echouait : le plus RECENT (4j) prend l'id le plus PETIT, le plus ANCIEN
    # (6j, "vu le plus tot") prend l'id le plus grand.
    src_recent = _src(db_session, "recent-src")
    src_old = _src(db_session, "old-src")
    new_src = _src(db_session, "tsugi")
    _art(db_session, src_recent, n=1, title="Astropolis 2027 devoile son line-up",
         hours_ago=4 * 24, status=ArticleStatus.USED)
    plus_ancien = _art(db_session, src_old, n=2, title="Astropolis 2027 : le line-up est devoile",
                       hours_ago=6 * 24, status=ArticleStatus.USED)
    repeat = _art(db_session, new_src, n=3, title="Astropolis 2027 : line-up devoile aujourd'hui",
                 hours_ago=1)

    link_duplicates(db_session)
    db_session.refresh(repeat)
    assert repeat.duplicate_of_id == plus_ancien.id


def test_archive_matching_is_idempotent(db_session):
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
         hours_ago=5 * 24, status=ArticleStatus.USED)
    _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027", hours_ago=1)

    assert link_duplicates(db_session).total == 1
    assert link_duplicates(db_session).total == 0


def test_a_used_article_that_is_itself_a_duplicate_is_not_reused_as_a_representative(db_session):
    # Le representant racine du groupe archive doit etre atteint, jamais un
    # membre archive qui pointe deja vers un autre.
    root_src = _src(db_session, "mixmag")
    other_src = _src(db_session, "dj-mag")
    new_src = _src(db_session, "tsugi")
    root = _art(db_session, root_src, n=1, title="Dour Festival unveils its 2027 lineup",
               hours_ago=6 * 24, status=ArticleStatus.USED)
    already_linked = _art(db_session, other_src, n=2, title="Dour Festival line-up 2027 revealed",
                          hours_ago=5 * 24, status=ArticleStatus.USED)
    already_linked.duplicate_of_id = root.id
    db_session.commit()
    repeat = _art(db_session, new_src, n=3, title="Dour Festival devoile son line-up 2027",
                 hours_ago=1)

    link_duplicates(db_session)
    db_session.refresh(repeat)
    assert repeat.duplicate_of_id == root.id


# -- archive : le seuil propre, mesure contre les faux positifs -------------
# Trois paires RELEVEES en revue le 2026-09-21 : ce sont des actus DIFFERENTES
# que le seuil de 0,65 eprouve sur 48 h rapprochait quand meme, une fois
# applique a des semaines d'archive d'un feed centre sur quelques artistes
# recurrents. Le lien est irreversible (l'article sort de la file), d'ou un
# seuil propre a l'archive.

FAUX_POSITIFS = [
    ("Charlotte de Witte annonce un nouvel EP",
     "Charlotte de Witte annonce une tournee mondiale"),
    ("Fred again.. sort un nouveau morceau", "Fred again.. sort un nouvel album"),
    ("Les Transmusicales devoilent leur programmation",
     "Les Transmusicales devoilent une nouvelle vague"),
]


def test_le_seuil_d_archive_epargne_les_trois_faux_positifs_mesures(db_session):
    for index, (publie, nouveau) in enumerate(FAUX_POSITIFS):
        old_src = _src(db_session, f"media-{index}")
        new_src = _src(db_session, f"presse-{index}")
        _art(db_session, old_src, n=100 + index * 2, title=publie,
             hours_ago=5 * 24, status=ArticleStatus.USED)
        reprise = _art(db_session, new_src, n=101 + index * 2, title=nouveau, hours_ago=1)

        link_duplicates(db_session)

        db_session.refresh(reprise)
        assert reprise.duplicate_of_id is None, f"faux positif sur « {nouveau} »"


def test_la_vraie_reprise_reste_attrapee_par_le_seuil_d_archive(db_session):
    """Le seuil doit epargner les trois paires SANS vider la regle : la meme
    actu reprise par une autre source, traduite, est toujours rapprochee."""
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    publie = _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
                  hours_ago=5 * 24, status=ArticleStatus.USED)
    reprise = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                   hours_ago=1)

    link_duplicates(db_session)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id == publie.id


def test_la_fenetre_d_archive_par_defaut_est_d_une_semaine(db_session):
    """Une reprise ressort dans la semaine ; au-dela, le risque de faux positif
    depasse le service rendu."""
    assert settings.cluster_archive_jours == 7
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
         hours_ago=10 * 24, status=ArticleStatus.USED)
    reprise = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                   hours_ago=1)

    link_duplicates(db_session)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None


def test_les_liens_d_archive_sont_comptes_a_part(db_session):
    """Un lien frais et un lien d'archive n'ont ni le meme risque ni le meme
    sens : les confondre dans un seul entier rend un faux positif d'archive
    invisible a la supervision."""
    a_src, b_src, old_src = (_src(db_session, n) for n in ("a", "b", "mixmag"))
    _art(db_session, a_src, n=1, title="Aphex Twin annonce un concert a Berlin", hours_ago=6)
    _art(db_session, b_src, n=2, title="Aphex Twin annonce un concert a Berlin en octobre",
         hours_ago=4)
    _art(db_session, old_src, n=3, title="Dour Festival unveils its 2027 lineup",
         hours_ago=5 * 24, status=ArticleStatus.USED)
    _art(db_session, _src(db_session, "tsugi"), n=4,
         title="Dour Festival devoile son line-up 2027", hours_ago=1)

    stats = link_duplicates(db_session)

    assert stats.frais == 1
    assert stats.archive == 1
    assert stats.total == 2


def test_un_lien_d_archive_laisse_une_trace_lisible_sur_l_article(db_session):
    """Sans trace, un faux positif disparait : l'article sort de la file et
    rien n'en dit la raison. `file_exclu_raison` est le champ que le projet
    utilise deja pour ca."""
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    publie = _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
                  hours_ago=5 * 24, status=ArticleStatus.USED)
    reprise = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                   hours_ago=1)

    link_duplicates(db_session)

    db_session.refresh(reprise)
    assert reprise.file_exclu_raison is not None
    assert str(publie.id) in reprise.file_exclu_raison
    assert "publiee" in reprise.file_exclu_raison


def test_un_lien_frais_ne_pose_aucune_trace_d_exclusion(db_session):
    """Un doublon ordinaire n'est pas exclu de la file : c'est son representant
    qui y va, et l'exclusion serait un contresens."""
    a_src, b_src = _src(db_session, "a"), _src(db_session, "b")
    _art(db_session, a_src, n=1, title="Dour Festival unveils its 2027 lineup", hours_ago=6)
    second = _art(db_session, b_src, n=2, title="Dour Festival devoile son line-up 2027",
                  hours_ago=4)

    link_duplicates(db_session)

    db_session.refresh(second)
    assert second.file_exclu_raison is None
