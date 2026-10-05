"""La porte de candidature sur le RESUME, et son garde-fou de recurrence.

La dedup rapproche par les mots du TITRE ; mesure du 2026-09-24 sur 48 h
reelles : de vrais memes-evenements n'en partagent AUCUN (le record d'Ella
Langley couvert par quatre articles Billboard, une contrefaçon de vinyles chez
MBW et Mixmag). Ce qui les attrape est le nombre ABSOLU de mots significatifs
partages des RESUMES -- et ce qui empeche de payer pour une publication
RECURRENTE hebdomadaire est l'archive `event_observations`, jamais purgee.

`db_session.autoflush = False` dans chaque test du perimetre base : c'est le
reglage de PRODUCTION (`app/db.py`), et un test en autoflush par defaut a deja
masque deux fois un defaut de production dans ce depot.
"""

import datetime as dt

import pytest

from app.models import (
    Article,
    Event,
    EventObservation,
    Source,
    SourceRole,
    SourceType,
)
from app.pipeline.cluster import link_duplicates
from app.pipeline.cluster_arbitrage import cle_paire, marquer_manuel
from app.pipeline.cluster_resume import (
    ArchiveTitres,
    charger_archive,
    est_publication_recurrente,
    jetons_de_resume,
)

UTC = dt.timezone.utc

# Deux titres qui ne partagent AUCUN mot significatif -- le cas ou la dedup par
# titre est aveugle -- mais dont les resumes racontent la meme condamnation.
TITRE_A = "Vinyl counterfeiter jailed after fake Taylor Swift pressings"
TITRE_B = "Mastermind behind huge operation sentenced to prison"
RESUME_A = (
    "Un contrefacteur britannique condamne a trois ans et demi de prison apres "
    "avoir genere des millions avec de faux disques vinyles de Taylor Swift."
)
RESUME_B = (
    "Le cerveau de la plus grosse operation de contrefacon de disques vinyles "
    "du pays est condamne a la prison apres avoir genere des millions."
)


def _source(db, nom, url, source_id=None):
    source = Source(name=nom, type=SourceType.RSS, url=url)
    if source_id is not None:
        source.id = source_id
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, resume, rang, heures=6):
    """Date sur l'heure REELLE : `link_duplicates` lit `dt.datetime.now`, une
    fenetre calee sur une constante ne serait jamais examinee."""
    article = Article(
        source_id=source.id,
        url=f"https://exemple/{source.id}/{rang}",
        title=titre,
        summary=resume,
        raw_hash=f"{source.id}-{rang}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(hours=heures),
    )
    db.add(article)
    db.commit()
    return article


def _observation(db, source, titre, jour, article_id=None):
    evenement = Event(
        titre=titre,
        first_seen_at=jour,
        last_seen_at=jour,
    )
    db.add(evenement)
    db.commit()
    observation = EventObservation(
        event_id=evenement.id,
        article_id=article_id,
        platform=SourceType.RSS,
        source_id=source.id,
        source_role=SourceRole.SECONDARY,
        published_at=jour,
        observed_at=jour,
        title=titre,
        url=f"https://exemple/obs/{titre[:20]}",
        source_name=source.name,
    )
    db.add(observation)
    db.commit()
    return observation


class _Arbitre:
    def __init__(self, verdict):
        self.verdict = verdict
        self.appels = []

    def __call__(self, reference, nouveau):
        self.appels.append(cle_paire(reference.id, nouveau.id))
        return self.verdict


@pytest.fixture()
def cache(tmp_path):
    return tmp_path / "arbitrage.json"


def _paire(db):
    """Deux articles frais de deux sources, titres sans recouvrement, resumes
    qui partagent largement plus de six mots significatifs."""
    gauche = _source(db, "MBW", "https://mbw/rss")
    droite = _source(db, "Mixmag", "https://mixmag/rss")
    a = _article(db, gauche, TITRE_A, RESUME_A, 1, heures=6)
    b = _article(db, droite, TITRE_B, RESUME_B, 2, heures=5)
    return a, b


# -- le nettoyage du resume ------------------------------------------------


def test_le_bandeau_de_metriques_instagram_ne_fabrique_pas_de_mots_partages():
    """Une caption Instagram est suivie d'un bandeau (`_summary_with_metrics`)
    dont les mots se ressemblent d'un post a l'autre sans rien dire de l'actu :
    le laisser entrer ferait franchir le seuil a des paires vides."""
    caption = "Charlotte de Witte annonce une tournee mondiale"
    avec_bandeau = f"{caption}\n\n[Instagram : 12000 likes, 340 commentaires]"
    assert jetons_de_resume(_faux_article(avec_bandeau)) == jetons_de_resume(
        _faux_article(caption)
    )


def test_les_balises_et_les_entites_html_d_une_description_rss_sont_retirees():
    brut = "<p>Fred again.. &amp; Skrillex annoncent une r&eacute;sidence</p>"
    jetons = jetons_de_resume(_faux_article(brut))
    assert "residence" in jetons
    assert not any(jeton.startswith("amp") for jeton in jetons)


def test_un_resume_absent_ne_leve_pas():
    assert jetons_de_resume(_faux_article(None)) == set()


def _faux_article(resume):
    return Article(url="https://x", title="t", raw_hash="h", summary=resume)


# -- le critere de RECURRENCE ----------------------------------------------


SERIE = [
    "Billboard Hot 100 Top 10 Countdown for Sept. 12, 2026 | Billboard News",
    "Billboard Hot 100 Top 10 Countdown for Sept. 19, 2026 | Billboard News",
    "Billboard Hot 100 Top 10 Countdown for Sept. 05, 2026 | Billboard News",
    "Billboard Hot 100 Top 10 Countdown for Aug. 29, 2026 | Billboard News",
]
CANDIDAT_SERIE = "Billboard Hot 100 Top 10 Countdown for Sept. 26, 2026 | Billboard News"

# Quatre articles du MEME jour sur le MEME evenement : une rafale, pas une
# serie -- c'est exactement le cas que le critere doit epargner.
RAFALE = [
    "Ella Langley Breaks Hot 100 Record for Most Weeks at No. 1",
    "Ella Langley Toasts Choosin Texas Setting All-Time Hot 100 High",
    "How Do We Feel About Ella Langley Choosin Texas Setting the Record",
    "Ella Langley Choosin Texas and All the Hot 100 Hits That Set Records",
]
CANDIDAT_RAFALE = "Ella Langley Choosin Texas Reigns Alone With 23 Weeks at No. 1"
# Volontairement COURT : « ella » et « langley » y pesent la moitie du titre,
# donc la PART est franchie et seul l'etalement sur plusieurs jours peut
# encore epargner cette couverture. C'est ce qui rend le test suivant
# sensible a `JOURS_MIN`, et non a un autre reglage.
CANDIDAT_RAFALE_COURT = "Ella Langley Reigns Alone"


def _archive(db, source, titres, jours):
    base = dt.datetime.now(UTC) - dt.timedelta(days=10)
    for rang, titre in enumerate(titres):
        _observation(db, source, titre, base + dt.timedelta(days=jours[rang]))
    return charger_archive(db, 30)


def test_une_publication_recurrente_est_reconnue(db_session):
    db_session.autoflush = False
    source = _source(db_session, "Billboard (RSS)", "https://billboard/rss")
    archive = _archive(db_session, source, SERIE, [0, 7, 14, 21])
    candidat = _article(db_session, source, CANDIDAT_SERIE, "resume", 9)
    assert est_publication_recurrente(candidat, archive, 4, 0.4) is True


def test_une_rafale_du_meme_jour_sur_un_evenement_n_est_pas_une_recurrence(db_session):
    """LA propriete du critere, et celle qui se casserait en silence : sans
    l'exigence d'ETALEMENT SUR PLUSIEURS JOURS (`JOURS_MIN`), quatre articles
    du meme jour sur le meme record feraient passer leur propre couverture pour
    un format hebdomadaire -- et les vrais positifs disparaitraient sans que
    rien n'echoue nulle part."""
    db_session.autoflush = False
    source = _source(db_session, "Billboard (RSS)", "https://billboard/rss")
    archive = _archive(db_session, source, RAFALE, [0, 0, 0, 0])
    candidat = _article(db_session, source, CANDIDAT_RAFALE_COURT, "resume", 9)
    assert est_publication_recurrente(candidat, archive, 4, 0.4) is False


def test_un_vrai_positif_etale_sur_deux_jours_reste_epargne(db_session):
    """Meme rafale, etalee sur deux journees : les mots « ella », « langley »,
    « texas », « choosin » reviennent bien, mais ils ne couvrent qu'une petite
    part du titre du candidat -- c'est la PART qui tranche ici, la frequence ne
    suffit pas."""
    db_session.autoflush = False
    source = _source(db_session, "Billboard (RSS)", "https://billboard/rss")
    archive = _archive(db_session, source, RAFALE, [0, 0, 1, 1])
    candidat = _article(db_session, source, CANDIDAT_RAFALE, "resume", 9)
    assert est_publication_recurrente(candidat, archive, 4, 0.4) is False


def test_une_archive_vide_ne_rend_jamais_recurrent():
    article = Article(url="https://x", title="Un titre quelconque", raw_hash="h")
    assert est_publication_recurrente(article, ArchiveTitres(), 4, 0.4) is False


def test_l_archive_ne_se_compte_jamais_elle_meme(db_session):
    """Une observation porte l'article dont elle vient tant qu'il n'est pas
    purge : sans le filtre sur `article_id`, un article serait sa propre preuve
    de recurrence."""
    db_session.autoflush = False
    source = _source(db_session, "Billboard (RSS)", "https://billboard/rss")
    candidat = _article(db_session, source, CANDIDAT_SERIE, "resume", 9)
    base = dt.datetime.now(UTC) - dt.timedelta(days=3)
    _observation(db_session, source, CANDIDAT_SERIE, base, article_id=candidat.id)
    _observation(db_session, source, CANDIDAT_SERIE, base + dt.timedelta(days=1),
                 article_id=candidat.id)
    archive = charger_archive(db_session, 30)
    assert est_publication_recurrente(candidat, archive, 2, 0.4) is False


# -- la porte, de bout en bout --------------------------------------------


def test_deux_titres_sans_recouvrement_sont_lies_quand_leurs_resumes_se_recoupent(
    db_session, cache
):
    db_session.autoflush = False
    a, b = _paire(db_session)
    arbitre = _Arbitre((True, "meme condamnation"))
    stats = link_duplicates(
        db_session, arbitre=arbitre, arbitrage_resume_min=6, cache_chemin=cache
    )
    assert arbitre.appels == [cle_paire(a.id, b.id)]
    assert b.duplicate_of_id == a.id
    assert stats.resumes_arbitres == 1
    assert stats.frais == 1
    # Aucune trace sur l'article : son representant est FRAIS, donc purge a
    # deux jours -- une `file_exclu_raison` lui survivrait et sortirait le
    # doublon de la file pour toujours.
    assert b.file_exclu_raison is None


def test_coupee_la_porte_rend_exactement_les_decisions_d_avant(db_session, cache):
    """Le coupe-circuit est le PARAMETRE lui-meme (`arbitrage_resume_min` a
    `None`, le defaut) : c'est aussi ce que voit `run_veille`, qui ne le passe
    jamais."""
    db_session.autoflush = False
    a, b = _paire(db_session)
    arbitre = _Arbitre((True, "meme condamnation"))
    stats = link_duplicates(db_session, arbitre=arbitre, cache_chemin=cache)
    assert arbitre.appels == []
    assert b.duplicate_of_id is None
    assert stats.resumes_arbitres == 0
    assert stats.total == 0


def test_une_publication_recurrente_ecarte_la_paire_sans_rien_payer(db_session, cache):
    db_session.autoflush = False
    a, b = _paire(db_session)
    # `a` devient une publication recurrente de sa source : quatre titres de la
    # meme forme, etales sur des semaines.
    base = dt.datetime.now(UTC) - dt.timedelta(days=21)
    for rang in range(4):
        _observation(db_session, a.source, TITRE_A, base + dt.timedelta(days=7 * rang))
    arbitre = _Arbitre((True, "meme condamnation"))
    stats = link_duplicates(
        db_session,
        arbitre=arbitre,
        arbitrage_resume_min=6,
        recurrence_min_titres=4,
        recurrence_part_min=0.4,
        cache_chemin=cache,
    )
    assert arbitre.appels == []
    assert b.duplicate_of_id is None
    assert stats.recurrences_ecartees == 1
    assert stats.resumes_arbitres == 0


def test_un_resume_trop_court_n_est_jamais_candidat(db_session, cache):
    db_session.autoflush = False
    gauche = _source(db_session, "A", "https://a/rss")
    droite = _source(db_session, "B", "https://b/rss")
    _article(db_session, gauche, TITRE_A, "Rien.", 1, heures=6)
    court = _article(db_session, droite, TITRE_B, "Rien.", 2, heures=5)
    arbitre = _Arbitre((True, "peu importe"))
    link_duplicates(
        db_session, arbitre=arbitre, arbitrage_resume_min=6, cache_chemin=cache
    )
    assert arbitre.appels == []
    assert court.duplicate_of_id is None


def test_le_budget_du_resume_est_separe_de_celui_des_bandes_de_titre(db_session, cache):
    """Un budget commun laisserait la porte du resume affamer la bande archive,
    celle qui protege du repost d'une actu DEJA PUBLIEE."""
    db_session.autoflush = False
    a, b = _paire(db_session)
    arbitre = _Arbitre((True, "meme condamnation"))
    link_duplicates(
        db_session,
        arbitre=arbitre,
        arbitrage_resume_min=6,
        arbitrage_resume_max=0,
        arbitrage_max=20,
        cache_chemin=cache,
    )
    assert arbitre.appels == []
    assert b.duplicate_of_id is None


def test_un_verdict_manuel_different_prime_sur_la_porte(db_session, cache):
    db_session.autoflush = False
    a, b = _paire(db_session)
    marquer_manuel(a.id, b.id, False, "deux affaires distinctes", chemin=cache)
    arbitre = _Arbitre((True, "meme condamnation"))
    link_duplicates(
        db_session, arbitre=arbitre, arbitrage_resume_min=6, cache_chemin=cache
    )
    assert arbitre.appels == []
    assert b.duplicate_of_id is None


def test_une_panne_de_l_arbitre_ne_pose_jamais_de_lien(db_session, cache):
    db_session.autoflush = False
    a, b = _paire(db_session)
    arbitre = _Arbitre(None)
    stats = link_duplicates(
        db_session, arbitre=arbitre, arbitrage_resume_min=6, cache_chemin=cache
    )
    assert arbitre.appels == [cle_paire(a.id, b.id)]
    assert b.duplicate_of_id is None
    assert stats.arbitrage_pannes == 1
    assert stats.resumes_arbitres == 0


def test_sans_arbitre_la_porte_ne_lie_rien(db_session, cache):
    """Le cas de `run_veille` meme si le seuil etait passe : le doute favorise
    toujours la publication."""
    db_session.autoflush = False
    a, b = _paire(db_session)
    stats = link_duplicates(db_session, arbitrage_resume_min=6, cache_chemin=cache)
    assert b.duplicate_of_id is None
    assert stats.resumes_arbitres == 0


def test_un_verdict_deja_en_cache_relie_sans_repayer(db_session, cache):
    db_session.autoflush = False
    a, b = _paire(db_session)
    arbitre = _Arbitre((True, "meme condamnation"))
    link_duplicates(
        db_session, arbitre=arbitre, arbitrage_resume_min=6, cache_chemin=cache
    )
    assert len(arbitre.appels) == 1
    b.duplicate_of_id = None
    db_session.commit()
    second = _Arbitre((True, "ne devrait pas etre appele"))
    stats = link_duplicates(
        db_session, arbitre=second, arbitrage_resume_min=6, cache_chemin=cache
    )
    assert second.appels == []
    assert b.duplicate_of_id == a.id
    assert stats.resumes_arbitres == 1


# -- les pieds de page structurels des flux WordPress -----------------------


def test_le_pied_wordpress_ne_fabrique_pas_de_mots_partages():
    """« The post ... appeared first on Stereogum. » est un artefact de FLUX,
    reconnu a sa FORME -- rien a voir avec le filtre de frequence sur les mots
    du resume, que la mesure interdit (il tue les vrais positifs Billboard).
    Sans ce retrait, « appeared », « first », « post » et le nom du site
    suffisaient a rapprocher deux articles sans aucun rapport."""
    corps = "Bicep annonce un live au Zenith de Paris pour janvier prochain."
    pied = f"{corps} The post Bicep annonce un live appeared first on Stereogum."
    assert jetons_de_resume(_faux_article(pied)) == jetons_de_resume(
        _faux_article(corps)
    )


def test_la_coupure_continue_reading_est_retiree():
    corps = "Le festival Dour devoile sa programmation complete pour 2027."
    coupe = f"{corps}... Continue reading Le festival Dour devoile sa programmation"
    assert jetons_de_resume(_faux_article(coupe)) == jetons_de_resume(
        _faux_article(corps)
    )


# -- la garde de pertinence -------------------------------------------------


def _noter(db, article, note):
    article.relevance_score = note
    db.commit()


def test_deux_articles_sous_le_seuil_ne_sont_jamais_arbitres(db_session, cache):
    """Le lien ne protegerait AUCUNE publication -- et il ferait pire : un
    evenement a DEUX observations est PERMANENT (`purger_evenements_sans_valeur`),
    donc deux posts vides qui se ressemblent deviendraient du bruit que plus
    rien ne purgerait jamais."""
    db_session.autoflush = False
    a, b = _paire(db_session)
    _noter(db_session, a, 20)
    _noter(db_session, b, 25)
    arbitre = _Arbitre((True, "meme condamnation"))
    link_duplicates(
        db_session, arbitre=arbitre, arbitrage_resume_min=6, cache_chemin=cache
    )
    assert arbitre.appels == []
    assert b.duplicate_of_id is None


def test_un_seul_article_au_dessus_du_seuil_suffit_a_garder_la_paire(db_session, cache):
    """La corroboration a de la valeur pour celui qui, lui, peut etre publie."""
    db_session.autoflush = False
    a, b = _paire(db_session)
    _noter(db_session, a, 75)
    _noter(db_session, b, 20)
    arbitre = _Arbitre((True, "meme condamnation"))
    link_duplicates(
        db_session, arbitre=arbitre, arbitrage_resume_min=6, cache_chemin=cache
    )
    assert arbitre.appels == [cle_paire(a.id, b.id)]
    assert b.duplicate_of_id == a.id


def test_une_note_absente_vaut_au_dessus_du_seuil(db_session, cache):
    """`link_duplicates` tourne AVANT le scoring : un article frais n'a pas de
    note. On ne sait pas, donc on ne jette pas -- la regle du doute du depot.
    La purge d'evenements prend la convention INVERSE sur le meme champ, et
    c'est voulu : chaque regle penche du cote ou le doute coute le moins cher."""
    db_session.autoflush = False
    a, b = _paire(db_session)
    assert a.relevance_score is None and b.relevance_score is None
    arbitre = _Arbitre((True, "meme condamnation"))
    link_duplicates(
        db_session, arbitre=arbitre, arbitrage_resume_min=6, cache_chemin=cache
    )
    assert arbitre.appels == [cle_paire(a.id, b.id)]
