import datetime as dt

from app.config import settings
from app.models import Article, ArticleStatus, Source, SourceType
from app.pipeline.cluster import link_duplicates, title_similarity
from app.pipeline.cluster_arbitrage import lire_cache, marquer_manuel

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


# -- Task 7 : Claude tranche les reprises douteuses ------------------------
# Paire "ratee" reelle (archive 0.40, recouvrement 0.80) : sous le seuil
# certain (0.8) mais dans la bande douteuse (>= 0.65). C'est exactement le cas
# que la Task 7 corrige -- une vraie reprise qu'aucun seuil ne pouvait a la
# fois attraper et epargner des faux positifs.
TITRE_PUBLIE_DOUTEUX = "Boris Brejcha annonce son premier album"
TITRE_REPRISE_DOUTEUSE = (
    "Boris Brejcha devoile son premier album Apex attendu en decembre chez KNTXT"
)


def _paire_douteuse(db_session, *, hours_ago_publie=5 * 24, hours_ago_reprise=1):
    old_src = _src(db_session, "media-publie")
    new_src = _src(db_session, "media-reprise")
    publie = _art(db_session, old_src, n=201, title=TITRE_PUBLIE_DOUTEUX,
                  hours_ago=hours_ago_publie, status=ArticleStatus.USED)
    reprise = _art(db_session, new_src, n=202, title=TITRE_REPRISE_DOUTEUSE,
                   hours_ago=hours_ago_reprise)
    return publie, reprise


def _arbitre_fixe(verdicts: dict):
    """Un faux arbitre : verdicts indexes par paire d'ids (ordre indifferent),
    valeur `(meme_actu, raison)` ou `None` (panne). Garde la liste des paires
    reellement appelees, pour verifier qu'une paire n'est jamais redemandee."""
    appels = []

    def arbitre(publie, nouveau):
        appels.append(tuple(sorted((publie.id, nouveau.id))))
        return verdicts.get(tuple(sorted((publie.id, nouveau.id))))

    arbitre.appels = appels
    return arbitre


def test_la_bande_douteuse_sans_arbitre_ne_lie_rien_et_n_appelle_personne(db_session):
    """Le defaut de `run_veille` : `arbitre=None` -- le doute favorise la
    publication, jamais un lien par defaut."""
    publie, reprise = _paire_douteuse(db_session)

    stats = link_duplicates(db_session)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert stats.arbitres == 0
    assert stats.arbitrage_pannes == 0


def test_la_bande_certaine_ne_declenche_jamais_l_arbitre(db_session):
    """Un score au-dessus du seuil certain se lie sans demander -- l'arbitre
    n'est reserve qu'a la bande ou les deux mesures se contredisent."""
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    publie = _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
                  hours_ago=5 * 24, status=ArticleStatus.USED)
    reprise = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                   hours_ago=1)
    arbitre = _arbitre_fixe({})

    link_duplicates(db_session, arbitre=arbitre)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id == publie.id
    assert arbitre.appels == []


def test_l_arbitre_dit_meme_actu_et_relie(db_session):
    publie, reprise = _paire_douteuse(db_session)
    arbitre = _arbitre_fixe({(publie.id, reprise.id): (True, "meme sortie, deux sources")})

    stats = link_duplicates(db_session, arbitre=arbitre)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id == publie.id
    assert stats.arbitres == 1
    assert reprise.file_exclu_raison is not None
    assert "Claude" in reprise.file_exclu_raison
    assert "meme sortie, deux sources" in reprise.file_exclu_raison


def test_l_arbitre_dit_actus_differentes_et_ne_relie_jamais(db_session):
    """Les faux positifs mesures (Task 7) : quand Claude tranche "different",
    la paire n'est jamais liee, quel que soit le score de recouvrement."""
    publie, reprise = _paire_douteuse(db_session)
    arbitre = _arbitre_fixe({(publie.id, reprise.id): (False, "deux annonces distinctes")})

    stats = link_duplicates(db_session, arbitre=arbitre)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert stats.arbitres == 0
    assert stats.archive == 0


def test_une_paire_arbitree_n_est_jamais_redemandee(db_session):
    publie, reprise = _paire_douteuse(db_session)
    arbitre = _arbitre_fixe({(publie.id, reprise.id): (False, "deux annonces distinctes")})

    link_duplicates(db_session, arbitre=arbitre)
    link_duplicates(db_session, arbitre=arbitre)

    assert arbitre.appels == [tuple(sorted((publie.id, reprise.id)))]  # un seul appel


def test_une_panne_de_l_arbitre_ne_cree_aucun_lien_et_n_est_pas_memorisee(db_session):
    publie, reprise = _paire_douteuse(db_session)
    arbitre = _arbitre_fixe({})  # rend None : aucun verdict connu -> panne simulee

    stats = link_duplicates(db_session, arbitre=arbitre)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert stats.arbitrage_pannes == 1
    # Rien de memorise : le passage suivant redemande.
    stats2 = link_duplicates(db_session, arbitre=arbitre)
    assert stats2.arbitrage_pannes == 1
    assert len(arbitre.appels) == 2


def test_le_plafond_d_appels_par_passage_est_respecte(db_session):
    """Un deuxieme article douteux, sans budget restant, n'appelle pas
    l'arbitre : le doute gagne, comme si `arbitre` etait absent."""
    publie, reprise = _paire_douteuse(db_session)
    autre_src = _src(db_session, "media-reprise-2")
    autre_reprise = _art(
        db_session, autre_src, n=203,
        title="Boris Brejcha devoile une tournee mondiale autour de son premier album",
        hours_ago=1,
    )
    arbitre = _arbitre_fixe(
        {
            (publie.id, reprise.id): (True, "meme sortie"),
            (publie.id, autre_reprise.id): (True, "ne devrait jamais etre lu"),
        }
    )

    stats = link_duplicates(db_session, arbitre=arbitre, arbitrage_max=1)

    db_session.refresh(reprise)
    db_session.refresh(autre_reprise)
    assert reprise.duplicate_of_id == publie.id
    assert autre_reprise.duplicate_of_id is None
    assert stats.arbitres == 1
    assert len(arbitre.appels) == 1


def test_un_verdict_manuel_different_prime_sur_la_bande_certaine(db_session):
    """Le coeur de la reparation : un verdict manuel "different" survit meme
    a un score qui atteindrait le seuil certain. L'ancienne procedure
    (effacer `duplicate_of_id`/`file_exclu_raison`) ne suffisait pas -- le
    passage suivant reliait a nouveau tant que l'article restait publiable."""
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    publie = _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
                  hours_ago=5 * 24, status=ArticleStatus.USED)
    reprise = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                   hours_ago=1)
    marquer_manuel(publie.id, reprise.id, False, "deux festivals distincts, homonymes")

    link_duplicates(db_session)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None


def test_un_verdict_manuel_meme_actu_relie_sans_appeler_l_arbitre(db_session):
    publie, reprise = _paire_douteuse(db_session)
    marquer_manuel(publie.id, reprise.id, True, "confirme a la main")
    arbitre = _arbitre_fixe({})

    stats = link_duplicates(db_session, arbitre=arbitre)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id == publie.id
    assert arbitre.appels == []
    assert stats.archive == 1
    assert "manuel" in reprise.file_exclu_raison


def test_le_verdict_manuel_est_relu_independamment_de_l_ordre_des_ids(db_session):
    """La cle de paire est independante de l'ordre : marquer (a, b) protege
    aussi la paire vue comme (b, a)."""
    publie, reprise = _paire_douteuse(db_session)
    marquer_manuel(reprise.id, publie.id, False, "ordre inverse")

    link_duplicates(db_session)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None


def test_le_cache_d_arbitrage_persiste_sur_disque(db_session, tmp_path):
    chemin = tmp_path / "cluster_arbitrage.json"
    publie, reprise = _paire_douteuse(db_session)
    arbitre = _arbitre_fixe({(publie.id, reprise.id): (True, "meme sortie")})

    link_duplicates(db_session, arbitre=arbitre, cache_chemin=chemin)

    cache = lire_cache(chemin)
    entree = cache.entrees[f"{min(publie.id, reprise.id)}:{max(publie.id, reprise.id)}"]
    assert entree.meme_actu is True
    assert entree.manuel is False
