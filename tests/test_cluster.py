import datetime as dt

import pytest

from app.config import settings
from app.models import Article, ArticleStatus, Source, SourceType
from app.pipeline.cluster import link_duplicates, title_similarity
from app.pipeline.cluster_arbitrage import cle_paire, lire_cache, marquer_manuel

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


def test_les_trois_faux_positifs_tombent_dans_la_bande_douteuse_et_l_arbitre_les_epargne(
    db_session,
):
    """Les trois FAUX_POSITIFS ci-dessus sont tous dans la bande DOUTEUSE
    (archive < 0.8, recouvrement >= 0.65) : demande explicite de la revue,
    distincte du test `test_l_arbitre_dit_actus_differentes_et_ne_relie_jamais`
    plus bas, qui tourne sur une vraie REPRISE (Boris Brejcha), pas un faux
    positif. Avec un arbitre qui dit "different" sur les trois, aucun lien."""
    for index, (publie_titre, nouveau_titre) in enumerate(FAUX_POSITIFS):
        old_src = _src(db_session, f"media-arbitre-{index}")
        new_src = _src(db_session, f"presse-arbitre-{index}")
        publie = _art(db_session, old_src, n=300 + index * 2, title=publie_titre,
                      hours_ago=5 * 24, status=ArticleStatus.USED)
        reprise = _art(db_session, new_src, n=301 + index * 2, title=nouveau_titre, hours_ago=1)
        arbitre = _arbitre_fixe({(publie.id, reprise.id): (False, "deux actus distinctes")})

        stats = link_duplicates(db_session, arbitre=arbitre)

        db_session.refresh(reprise)
        assert reprise.duplicate_of_id is None, f"faux positif sur « {nouveau_titre} »"
        assert arbitre.appels == [tuple(sorted((publie.id, reprise.id)))]
        assert stats.arbitres == 0


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


# -- Tour de correction 1/5 (revue sur le vrai SDK) -------------------------


def test_m1_un_verdict_manuel_vaut_aussi_entre_deux_articles_frais(db_session):
    """Minor M1 : la branche "frais" (deux articles de la fenetre de 48h, hors
    archive) ne lisait pas le cache -- un verdict manuel y etait ignore, alors
    que le script annonce "ne sera plus jamais rapprochee". L'utilisateur a le
    droit de defaire n'importe quel lien, pas seulement un lien d'archive."""
    src_a, src_b = _src(db_session, "a"), _src(db_session, "b")
    premier = _art(db_session, src_a, n=1, title="Dour Festival unveils its 2027 lineup",
                   hours_ago=6)
    second = _art(db_session, src_b, n=2, title="Dour Festival devoile son line-up 2027",
                  hours_ago=4)
    marquer_manuel(premier.id, second.id, False, "deux festivals distincts, homonymes")

    link_duplicates(db_session)

    db_session.refresh(second)
    assert second.duplicate_of_id is None


def test_m1_un_verdict_manuel_meme_actu_relie_deux_articles_frais(db_session):
    src_a, src_b = _src(db_session, "a"), _src(db_session, "b")
    premier = _art(db_session, src_a, n=1, title="Les sorties electro de la semaine",
                   hours_ago=6)
    second = _art(db_session, src_b, n=2, title="Un article totalement different",
                  hours_ago=4)
    marquer_manuel(premier.id, second.id, True, "confirme a la main malgre des titres eloignes")

    stats = link_duplicates(db_session)

    db_session.refresh(second)
    assert second.duplicate_of_id == premier.id
    assert stats.frais == 0  # ce n'est pas passe par la mesure de recouvrement
    assert stats.archive == 1  # compte comme un lien "hors mesure ordinaire"


def test_un_verdict_manuel_survit_a_plusieurs_passages_horaires(db_session, tmp_path):
    """Le coeur de la reparation, sur plusieurs passages : un verdict manuel
    pose une fois doit tenir indefiniment, pas seulement au passage suivant."""
    from app.config import settings as reglages

    chemin = tmp_path / "cluster_arbitrage.json"
    reglages_avant = reglages.cluster_arbitrage_cache_path
    reglages.cluster_arbitrage_cache_path = chemin
    try:
        old_src = _src(db_session, "mixmag")
        new_src = _src(db_session, "tsugi")
        publie = _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
                      hours_ago=5 * 24, status=ArticleStatus.USED)
        reprise = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                       hours_ago=1)
        marquer_manuel(publie.id, reprise.id, False, "deux festivals distincts")

        for _ in range(4):
            link_duplicates(db_session)
            db_session.refresh(reprise)
            assert reprise.duplicate_of_id is None
    finally:
        reglages.cluster_arbitrage_cache_path = reglages_avant


def test_un_verdict_manuel_pose_par_le_script_survit_aux_passages_suivants(
    db_session, tmp_path, monkeypatch
):
    """Meme preuve que ci-dessus, mais en passant reellement par
    `scripts.marquer_doublon_manuel` -- l'outil que l'utilisateur emploie pour
    de vrai, pas un appel direct a `marquer_manuel`."""
    from app.config import settings as reglages
    from scripts.marquer_doublon_manuel import appliquer as appliquer_script

    monkeypatch.setattr(reglages, "cluster_arbitrage_cache_path", tmp_path / "cache.json")
    old_src = _src(db_session, "mixmag")
    new_src = _src(db_session, "tsugi")
    publie = _art(db_session, old_src, n=1, title="Dour Festival unveils its 2027 lineup",
                  hours_ago=5 * 24, status=ArticleStatus.USED)
    reprise = _art(db_session, new_src, n=2, title="Dour Festival devoile son line-up 2027",
                   hours_ago=1)
    # D'abord lie automatiquement (bande certaine), comme un vrai faux positif.
    link_duplicates(db_session)
    db_session.refresh(reprise)
    assert reprise.duplicate_of_id == publie.id

    appliquer_script(db_session, publie.id, reprise.id, meme_actu=False,
                      raison="deux festivals distincts, homonymes")

    for _ in range(3):
        link_duplicates(db_session)
        db_session.refresh(reprise)
        assert reprise.duplicate_of_id is None


def test_i2_un_verdict_manuel_pose_pendant_le_passage_n_est_pas_efface(db_session, tmp_path):
    """Reproduit en revue : `link_duplicates` relit le cache une fois au debut
    et le reecrivait en bloc a la fin -- un verdict manuel pose PENDANT ce
    passage (par un AUTRE processus, ex. `scripts.marquer_doublon_manuel`, sur
    une paire qui n'a rien a voir avec celle en cours d'arbitrage) disparaissait
    a l'ecriture finale. L'arbitre factice simule cette ecriture concurrente."""
    chemin = tmp_path / "cluster_arbitrage.json"
    publie, reprise = _paire_douteuse(db_session)

    def arbitre_qui_ecrit_une_autre_paire_pendant_l_appel(publie_art, nouveau_art):
        # Simule un `scripts.marquer_doublon_manuel` lance par un AUTRE
        # processus PENDANT que ce passage-ci arbitre encore une autre paire.
        marquer_manuel(9001, 9002, False, "posee par un autre processus pendant le passage",
                        chemin)
        return (True, "meme sortie")

    link_duplicates(
        db_session, arbitre=arbitre_qui_ecrit_une_autre_paire_pendant_l_appel, cache_chemin=chemin
    )

    db_session.refresh(reprise)
    # La paire realement arbitree ce passage se comporte normalement.
    assert reprise.duplicate_of_id == publie.id
    # Le verdict manuel pose PENDANT l'appel, sur une AUTRE paire, doit
    # survivre a l'ecriture de fin de passage -- sans la relecture tardive
    # (`fusionner_et_ecrire`), la reecriture en bloc de l'ancien code
    # l'aurait efface (le disque, au moment de cette ecriture, ne contenait
    # QUE cette entree manuelle -- rien d'autre a perdre).
    cache = lire_cache(chemin)
    entree = cache.entrees["9001:9002"]
    assert entree.manuel is True
    assert entree.meme_actu is False


def test_i1_une_paire_qui_echoue_ne_prive_pas_indefiniment_les_paires_jamais_tentees(
    db_session,
):
    """Reproduit en revue : sans priorite aux paires jamais tentees, une paire
    qui echoue TOUJOURS (bug de schema, ou toute autre panne systematique)
    consommerait le budget de CHAQUE passage tant qu'elle reste la plus
    ancienne -- privant une seconde paire, parfaitement valide, de tout appel.
    Deux passages, budget de 1 : le premier est necessairement "perdu" sur la
    paire cassee (rien ne permet de savoir a l'avance qu'elle va echouer) ;
    mais des le second passage, elle est deja en echec (tentative enregistree)
    et cede la priorite du budget a la paire jamais tentee."""
    src1a, src1b = _src(db_session, "casse-a"), _src(db_session, "casse-b")
    publie1 = _art(db_session, src1a, n=1, title=TITRE_PUBLIE_DOUTEUX,
                   hours_ago=6 * 24, status=ArticleStatus.USED)
    reprise1 = _art(db_session, src1b, n=2, title=TITRE_REPRISE_DOUTEUSE, hours_ago=2)

    src2a, src2b = _src(db_session, "valide-a"), _src(db_session, "valide-b")
    publie2 = _art(db_session, src2a, n=3, title="Charlotte de Witte annonce un nouvel EP",
                   hours_ago=5 * 24, status=ArticleStatus.USED)
    reprise2 = _art(db_session, src2b, n=4,
                    title="Charlotte de Witte annonce un EP sur son label KNTXT", hours_ago=1)

    appels = []

    def arbitre(publie_art, nouveau_art):
        appels.append((publie_art.id, nouveau_art.id))
        if publie_art.id == publie1.id:
            return None  # panne systematique sur cette paire
        return (True, "meme sortie")

    # Passage 1 : la paire cassee, plus ancienne, consomme le seul appel.
    stats1 = link_duplicates(db_session, arbitre=arbitre, arbitrage_max=1)
    db_session.refresh(reprise1)
    db_session.refresh(reprise2)
    assert reprise1.duplicate_of_id is None
    assert reprise2.duplicate_of_id is None
    assert stats1.arbitrage_pannes == 1

    # Passage 2 : la paire cassee a desormais une tentative enregistree, donc
    # differee -- le budget va a la paire jamais tentee.
    stats2 = link_duplicates(db_session, arbitre=arbitre, arbitrage_max=1)
    db_session.refresh(reprise1)
    db_session.refresh(reprise2)
    assert reprise2.duplicate_of_id == publie2.id
    assert reprise1.duplicate_of_id is None
    assert appels == [
        (publie1.id, reprise1.id),  # passage 1
        (publie2.id, reprise2.id),  # passage 2 : la cassee a ete differee
    ]
    assert stats2.arbitres == 1


def test_i1_une_paire_est_abandonnee_apres_le_plafond_de_tentatives(db_session, tmp_path):
    chemin = tmp_path / "cluster_arbitrage.json"
    publie, reprise = _paire_douteuse(db_session)
    arbitre = _arbitre_fixe({})  # rend toujours None (panne)

    for _ in range(3):
        link_duplicates(
            db_session, arbitre=arbitre, cache_chemin=chemin, tentatives_max=3
        )

    assert len(arbitre.appels) == 3
    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None

    # Un quatrieme passage n'appelle plus jamais l'arbitre sur cette paire :
    # elle est abandonnee, sans qu'aucun verdict n'ait ete memorise pour ca.
    link_duplicates(db_session, arbitre=arbitre, cache_chemin=chemin, tentatives_max=3)
    assert len(arbitre.appels) == 3
    cache = lire_cache(chemin)
    assert cle_paire(publie.id, reprise.id) not in cache.entrees


def test_i3_marquer_manuel_leve_si_l_ecriture_echoue(tmp_path):
    # Un fichier existant utilise comme repertoire parent : impossible d'y
    # ecrire quoi que ce soit.
    impossible = tmp_path / "fichier-existant"
    impossible.write_text("je suis un fichier, pas un dossier")
    chemin = impossible / "sous-dossier" / "cluster_arbitrage.json"

    with pytest.raises(Exception):  # noqa: B017 -- n'importe quelle panne d'ecriture
        marquer_manuel(1, 2, False, "raison", chemin)


# -- Tour de correction 2/5 --------------------------------------------------


def test_i2_cas_2_l_utilisateur_corrige_pendant_que_claude_repond_meme_actu(
    db_session, tmp_path, monkeypatch
):
    """LE defaut le plus important du chantier (revue avec le VRAI script) :
    l'utilisateur marque "different" PENDANT l'appel a l'arbitre pour la MEME
    paire, et Claude repond "meme actu". Sans la relecture juste avant de
    poser le lien, celui-ci etait pose et commite dans le MEME passage --
    l'utilisateur voyait "actus differentes" affiche, mais l'article restait
    enterre avec une `file_exclu_raison` qui citait Claude.

    NI le script NI `link_duplicates` ne reçoivent de chemin explicite ici --
    exactement comme en production (`dedupliquer_avec_arbitrage` n'en passe
    jamais) : les deux passent par `settings.cluster_arbitrage_cache_path`,
    isole par la fixture autouse. Un `cache_chemin` explicite aurait pu
    masquer le defaut en pointant le script ailleurs que `link_duplicates`."""
    from scripts.marquer_doublon_manuel import appliquer as appliquer_script

    monkeypatch.setattr(settings, "cluster_arbitrage_cache_path", tmp_path / "cluster_arbitrage.json")
    publie, reprise = _paire_douteuse(db_session)

    def arbitre_pendant_lequel_l_utilisateur_corrige(publie_art, nouveau_art):
        # Simule le VRAI script lance par l'utilisateur PENDANT cet appel --
        # a cet instant, rien n'est encore lie en base (l'appel a l'arbitre
        # n'a pas encore rendu la main a `link_duplicates`).
        message = appliquer_script(
            db_session, publie_art.id, nouveau_art.id, meme_actu=False,
            raison="deux annonces distinctes, verifie a la main",
        )
        assert "lien defait" not in message  # rien a defaire : pas encore lie
        return (True, "meme sortie")  # Claude, lui, dit le contraire

    link_duplicates(db_session, arbitre=arbitre_pendant_lequel_l_utilisateur_corrige)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert reprise.file_exclu_raison is None


def test_i2_cas_2_auto_reparation_delie_un_lien_deja_pose_a_chaque_passage(
    db_session, tmp_path
):
    """La deuxieme moitie du remede : un lien deja present EN BASE (pose par
    une course non prevue, un crash entre deux ecritures, ou une version
    anterieure du code) dont la paire porte un verdict manuel "different" est
    defait a CHAQUE passage -- pas seulement empeche a l'avenir. Teste sur
    PLUSIEURS passages, avec le VRAI script pour poser le verdict manuel."""
    from scripts.marquer_doublon_manuel import appliquer as appliquer_script

    chemin = tmp_path / "cluster_arbitrage.json"
    publie, reprise = _paire_douteuse(db_session)
    # Simule un lien deja pose par une source quelconque (course, crash,
    # ancienne version du code) -- SANS passer par `link_duplicates`.
    reprise.duplicate_of_id = publie.id
    reprise.file_exclu_raison = "doublon d'une actu deja publiee (article X, tranche par Claude : X)"
    db_session.commit()

    appliquer_script(
        db_session, publie.id, reprise.id, meme_actu=False,
        raison="deux annonces distinctes",
    )
    # Le script lui-meme a deja defait le lien (il le trouve pose) : on le
    # reproduit "casse" pour prouver que L'AUTO-REPARATION de link_duplicates
    # rattrape aussi le cas ou le script n'a pas pu le faire (fichier ecrit,
    # base non touchee -- un crash entre les deux, par exemple).
    reprise.duplicate_of_id = publie.id
    reprise.file_exclu_raison = "doublon d'une actu deja publiee (article X, tranche par Claude : X)"
    db_session.commit()

    for _ in range(3):
        link_duplicates(db_session, cache_chemin=chemin)
        db_session.refresh(reprise)
        assert reprise.duplicate_of_id is None
        assert reprise.file_exclu_raison is None
        # Rejoue la casse pour le passage suivant : la reparation doit
        # converger a CHAQUE passage, pas seulement la premiere fois.
        reprise.duplicate_of_id = publie.id
        reprise.file_exclu_raison = "casse a nouveau"
        db_session.commit()


def test_m_le_representant_reste_le_plus_ancien_meme_apres_un_report(db_session, tmp_path):
    """Mineur trouve en revue (tour de correction 2/5) : un article dont la
    paire d'archive est REPORTEE (deja en echec, sous le plafond) doit rester
    un representant POSSIBLE pendant la passe 1 -- sinon un article frais plus
    RECENT qui le reprend devient representant a sa place, et le plus ancien
    finit par se lier a lui (l'inverse de la docstring de `link_duplicates`,
    "le representant est l'article vu le PLUS TOT").

    Le report exige une tentative deja enregistree AVANT que le second article
    frais n'existe -- d'ou les DEUX passages : le premier isole `plus_ancien`
    (seul candidat contre l'archive, echoue), le second introduit
    `plus_recent` (meme histoire, mais hors bande douteuse contre l'archive
    prise seule) exactement au moment ou `plus_ancien` est reporte."""
    chemin = tmp_path / "cluster_arbitrage.json"
    old_src = _src(db_session, "media-publie")
    new_src = _src(db_session, "media-plus-ancien")
    publie = _art(db_session, old_src, n=1, title=TITRE_PUBLIE_DOUTEUX,
                  hours_ago=5 * 24, status=ArticleStatus.USED)
    plus_ancien = _art(db_session, new_src, n=2, title=TITRE_REPRISE_DOUTEUSE, hours_ago=3)
    arbitre = _arbitre_fixe({})  # panne systematique sur (publie, plus_ancien)

    # Passage 1 : enregistre une tentative en echec sur plus_ancien, AVANT que
    # plus_recent n'existe.
    link_duplicates(db_session, arbitre=arbitre, cache_chemin=chemin)
    db_session.refresh(plus_ancien)
    assert plus_ancien.duplicate_of_id is None

    # plus_recent arrive : meme histoire que plus_ancien (0.75 de recouvrement
    # avec lui), mais hors bande douteuse contre l'archive PRISE SEULE (0.4,
    # sous le seuil de 0.65) -- son seul candidat plausible est plus_ancien.
    autre_src = _src(db_session, "media-plus-recent")
    plus_recent = _art(
        db_session, autre_src, n=3,
        title="Apex, le nouvel opus de Boris Brejcha, sort en decembre chez KNTXT",
        hours_ago=1,
    )

    # Passage 2 : plus_ancien est desormais REPORTE. C'est le passage ou
    # l'inversion se produisait avant le correctif.
    link_duplicates(db_session, arbitre=arbitre, cache_chemin=chemin)

    db_session.refresh(plus_ancien)
    db_session.refresh(plus_recent)
    assert plus_ancien.duplicate_of_id is None  # jamais lie au plus RECENT
    assert plus_recent.duplicate_of_id == plus_ancien.id  # le plus ancien gagne
    # plus_ancien reste, lui, un candidat DOUTEUX (jamais lie) contre l'archive :
    # `publie` n'est jamais devenu un doublon, seulement une cible possible.
    assert publie.duplicate_of_id is None


def test_residu_un_cache_illisible_n_efface_pas_les_verdicts_existants(db_session, tmp_path):
    """Residu ferme au tour de correction 2/5 : un fichier de cache PRESENT
    mais illisible ne doit jamais etre confondu avec un cache VIDE par une
    fonction qui va ensuite ECRIRE -- sinon un seul fichier corrompu efface
    tous les verdicts existants, manuels compris."""
    chemin = tmp_path / "cluster_arbitrage.json"
    marquer_manuel(9001, 9002, False, "verdict existant avant la corruption", chemin)
    # Corruption du fichier : plus du JSON valide.
    chemin.write_text("{ceci n'est pas du json valide")

    publie, reprise = _paire_douteuse(db_session)
    arbitre = _arbitre_fixe({(publie.id, reprise.id): (True, "meme sortie")})

    # Le passage ne doit PAS lever (une panne de lecture ne doit jamais casser
    # un run), mais ne doit RIEN ecrire de nouveau non plus : le fichier
    # illisible n'est pas touche, pour qu'un humain puisse encore le reparer.
    link_duplicates(db_session, arbitre=arbitre, cache_chemin=chemin)

    contenu_final = chemin.read_text()
    assert contenu_final == "{ceci n'est pas du json valide"
