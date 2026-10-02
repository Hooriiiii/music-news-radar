"""La porte sur le RESUME etendue a l'ARCHIVE (2026-10-02).

Defaut CONSTATE en production ce jour-la : le compte s'appretait a republier
DEUX actus deja publiees, et aucune mesure de titre ne pouvait les voir.

  1. « This is happening.. » (Charlotte de Witte, son propre compte, publie le
     29/09) contre « Charlotte de Witte and KI/KI announce first-ever b2b set at
     Flanders Expo » (DJ Mag, 30/09) : ZERO mot de titre en commun, donc 0,00
     aux deux mesures de titre. Les RESUMES partagent six mots.
  2. « Calvin Harris DJs for 1.6 million people... » (DJ Mag, 21/09) contre
     « Calvin Harris sets Guinness World Record... » (Mixmag, 30/09) :
     similarite d'archive 0,25, recouvrement 0,29, CINQ mots de resume
     partages -- et 8,99 jours d'ecart, hors de l'ancienne fenetre de 7 jours.

Les deux formes sont reproduites ici telles quelles : c'est le critere
d'acceptation du correctif.

`db_session.autoflush = False` dans chaque test du perimetre base : c'est le
reglage de PRODUCTION (`app/db.py`), et un test en autoflush par defaut a deja
masque deux fois un defaut de production dans ce depot.
"""

import datetime as dt

import pytest

from app.config import settings
from app.models import (
    Article,
    ArticleStatus,
    Event,
    EventObservation,
    Source,
    SourceRole,
    SourceType,
)
from app.pipeline.cluster import (
    RAISON_DOUBLON_PREFIXE,
    archive_similarity_from_tokens,
    link_duplicates,
    significant_tokens,
    title_similarity_from_tokens,
)
from app.pipeline.cluster_arbitrage import cle_paire, marquer_manuel
from app.pipeline.cluster_resume import jetons_de_resume

UTC = dt.timezone.utc

# --- Cas 1 : le titre de l'archive ne dit RIEN -----------------------------
TITRE_PUBLIE_1 = "This is happening.."
TITRE_REPRISE_1 = "Charlotte de Witte and KI/KI announce first-ever b2b set at Flanders Expo"
# Les deux resumes partagent EXACTEMENT six mots significatifs, comme la paire
# reelle (belgique, charlotte, expo, flanders, premier, witte) : un jeu d'essai
# plus riche que la production ferait passer ce test pour une marge qu'on n'a pas.
RESUME_PUBLIE_1 = (
    "Charlotte de Witte devoile un premier set b2b avec KI/KI au Flanders Expo "
    "de Gand, en Belgique, en fevrier prochain."
)
RESUME_REPRISE_1 = (
    "Premier b2b entre Charlotte de Witte et KI/KI programme au Flanders Expo, "
    "en Belgique : la techno belge tient son affiche."
)
JETONS_PARTAGES_1 = 6

# --- Cas 2 : neuf jours d'ecart, cinq mots de resume partages --------------
TITRE_PUBLIE_2 = "Calvin Harris DJs for 1.6 million people at record-breaking Sao Paulo show"
TITRE_REPRISE_2 = "Calvin Harris sets Guinness World Record for biggest-ever crowd at a DJ set"
RESUME_PUBLIE_2 = (
    "Calvin Harris a joue devant 1,6 million de personnes a Sao Paulo, un record "
    "d'affluence pour un concert gratuit sur la plage de Copacabana."
)
RESUME_REPRISE_2 = (
    "Record homologue : Calvin Harris entre au livre Guinness avec le plus grand "
    "public jamais reuni par un DJ, 1,6 million a Sao Paulo."
)
# CINQ, la valeur reelle mesuree le 2026-10-02 -- donc SOUS les six de la bande
# fraiche. C'est ce qui justifie un seuil propre a l'archive, et ce chiffre doit
# rester pique ici : un jeu d'essai a six rendrait le test insensible au seuil.
JETONS_PARTAGES_2 = 5


def _source(db, nom):
    source = Source(name=nom, type=SourceType.RSS, url=f"https://{nom}/rss")
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, resume, rang, *, heures, status=ArticleStatus.NEW):
    """Date sur l'heure REELLE : `link_duplicates` lit `dt.datetime.now`, une
    fenetre calee sur une constante ne serait jamais examinee."""
    article = Article(
        source_id=source.id,
        url=f"https://exemple/{source.id}/{rang}",
        title=titre,
        summary=resume,
        raw_hash=f"{source.id}-{rang}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(hours=heures),
        status=status,
    )
    db.add(article)
    db.commit()
    return article


class _Arbitre:
    def __init__(self, verdict=(True, "meme actu")):
        self.verdict = verdict
        self.appels = []

    def __call__(self, reference, nouveau):
        self.appels.append(cle_paire(reference.id, nouveau.id))
        return self.verdict


@pytest.fixture()
def cache(tmp_path):
    return tmp_path / "arbitrage.json"


def _cas(db, *, heures_archive, titres, resumes):
    """Un article PUBLIE (USED) hors fenetre fraiche, et sa reprise fraiche."""
    publie = _article(
        db, _source(db, "deja-publie"), titres[0], resumes[0], 1,
        heures=heures_archive, status=ArticleStatus.USED,
    )
    reprise = _article(db, _source(db, "reprise"), titres[1], resumes[1], 2, heures=2)
    return publie, reprise


def _cas_1(db, heures_archive=72):
    return _cas(db, heures_archive=heures_archive,
                titres=(TITRE_PUBLIE_1, TITRE_REPRISE_1),
                resumes=(RESUME_PUBLIE_1, RESUME_REPRISE_1))


def _cas_2(db, heures_archive=9 * 24):
    return _cas(db, heures_archive=heures_archive,
                titres=(TITRE_PUBLIE_2, TITRE_REPRISE_2),
                resumes=(RESUME_PUBLIE_2, RESUME_REPRISE_2))


def _lier(db, cache, arbitre, **kwargs):
    options = dict(
        arbitre=arbitre,
        arbitrage_resume_archive_min=settings.cluster_arbitrage_resume_archive_seuil,
        cache_chemin=cache,
    )
    options.update(kwargs)
    return link_duplicates(db, **options)


# -- la mesure des deux cas reels, avant toute machinerie -------------------


@pytest.mark.parametrize(
    "resumes, attendu",
    [
        ((RESUME_PUBLIE_1, RESUME_REPRISE_1), JETONS_PARTAGES_1),
        ((RESUME_PUBLIE_2, RESUME_REPRISE_2), JETONS_PARTAGES_2),
    ],
)
def test_les_jeux_d_essai_partagent_autant_de_mots_que_la_production(resumes, attendu):
    """Un jeu d'essai plus bavard que la realite rendrait vert un seuil qui
    reperd le cas reel -- c'est exactement ce qu'une preuve par mutation sur
    `cluster_arbitrage_resume_archive_seuil` a montre en cours de route."""
    def jetons(resume):
        return jetons_de_resume(
            Article(url="https://x", title="t", raw_hash="h", summary=resume)
        )

    assert len(jetons(resumes[0]) & jetons(resumes[1])) == attendu


@pytest.mark.parametrize(
    "gauche, droite",
    [(TITRE_PUBLIE_1, TITRE_REPRISE_1), (TITRE_PUBLIE_2, TITRE_REPRISE_2)],
)
def test_aucune_mesure_de_titre_ne_voit_ces_deux_paires(gauche, droite):
    """Le point de depart : si une mesure de titre les voyait, il n'y aurait
    rien a corriger. Ce test echoue si quelqu'un croit avoir regle le defaut
    en touchant aux seuils de titre."""
    a, b = significant_tokens(gauche), significant_tokens(droite)
    assert archive_similarity_from_tokens(a, b) < settings.cluster_archive_seuil
    assert title_similarity_from_tokens(a, b) < 0.65


# -- le critere d'acceptation ----------------------------------------------


def test_le_cas_charlotte_de_witte_est_rattache_a_l_actu_deja_publiee(db_session, cache):
    db_session.autoflush = False
    publie, reprise = _cas_1(db_session)
    arbitre = _Arbitre()

    stats = _lier(db_session, cache, arbitre)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id == publie.id
    assert arbitre.appels == [cle_paire(publie.id, reprise.id)]
    assert stats.archive == 1
    assert stats.resumes_archive_arbitres == 1
    # Comptee comme ARCHIVE, jamais comme un lien frais : c'est la seule bande
    # dont un faux positif enterre une actu neuve pour de bon.
    assert stats.frais == 0
    assert stats.resumes_arbitres == 0


def test_le_cas_calvin_harris_a_neuf_jours_d_ecart_est_rattache(db_session, cache):
    """Huit jours et 23 heures : hors de l'ancienne fenetre de 7 jours, dans
    celle de 10 -- et CINQ mots de resume partages, sous les six de la bande
    fraiche. Les deux reglages sont necessaires, ce test tombe si l'un saute."""
    db_session.autoflush = False
    publie, reprise = _cas_2(db_session)

    stats = _lier(db_session, cache, _Arbitre())

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id == publie.id
    assert stats.resumes_archive_arbitres == 1


def test_un_lien_d_archive_sur_le_resume_laisse_une_trace_sur_l_article(db_session, cache):
    """Son representant est `USED`, donc jamais purge : la trace decrit une
    situation qui dure, contrairement a celle d'un lien frais."""
    db_session.autoflush = False
    publie, reprise = _cas_1(db_session)

    _lier(db_session, cache, _Arbitre())

    db_session.refresh(reprise)
    assert reprise.file_exclu_raison.startswith(RAISON_DOUBLON_PREFIXE)
    assert f"article {publie.id}" in reprise.file_exclu_raison
    # La mesure n'est PAS une similarite de titre : la trace doit le dire,
    # sinon « similarite 6.00 » se lirait comme un score de recouvrement.
    assert "resumes" in reprise.file_exclu_raison
    assert "similarite" not in reprise.file_exclu_raison


# -- la propriete la plus fragile : AUCUNE bande certaine contre l'archive --


def test_sans_arbitre_aucun_lien_d_archive_n_est_pose_sur_le_seul_resume(db_session, cache):
    """LE garde-fou du correctif. Un lien d'archive est irreversible en
    pratique : l'article sort de la file et le representant `USED` est
    verrouille. Le nombre de mots de resume partages n'est donc JAMAIS une
    raison suffisante a lui seul -- meme tres au-dela du seuil.

    Ce test echoue si quelqu'un ajoute un raccourci « assez de mots partages
    donc meme actu » pour economiser un appel."""
    db_session.autoflush = False
    publie, reprise = _cas_1(db_session)
    # Les deux resumes IDENTIQUES : le maximum de mots partages possible.
    reprise.summary = publie.summary
    db_session.commit()

    stats = _lier(db_session, cache, None)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert stats.archive == 0
    assert stats.resumes_archive_arbitres == 0


def test_la_porte_est_coupee_par_son_parametre_comme_pour_run_veille(db_session, cache):
    """Convention du depot : c'est un PARAMETRE qui ouvre une bande, jamais le
    nom de l'appelant. `run_veille` ne le passe pas et n'arbitre donc jamais."""
    db_session.autoflush = False
    _publie, reprise = _cas_1(db_session)
    arbitre = _Arbitre()

    stats = link_duplicates(db_session, arbitre=arbitre, cache_chemin=cache)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert arbitre.appels == []
    assert stats.resumes_archive_arbitres == 0


def test_une_panne_de_l_arbitre_ne_pose_aucun_lien(db_session, cache):
    """Le doute favorise la publication : mieux vaut une republication qu'une
    actu neuve enterree. C'est aussi l'etat reel pendant que les credits
    Anthropic sont epuises."""
    db_session.autoflush = False
    _publie, reprise = _cas_1(db_session)

    stats = _lier(db_session, cache, _Arbitre(verdict=None))

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert stats.archive == 0
    assert stats.arbitrage_pannes == 1


def test_un_verdict_manuel_different_prime_et_n_appelle_personne(db_session, cache):
    db_session.autoflush = False
    publie, reprise = _cas_1(db_session)
    marquer_manuel(publie.id, reprise.id, meme_actu=False, raison="deux soirees", chemin=cache)
    arbitre = _Arbitre()

    _lier(db_session, cache, arbitre)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert arbitre.appels == []


# -- le garde-fou de CONTENU ----------------------------------------------


def test_un_seul_titre_pauvre_ne_ferme_pas_la_porte(db_session, cache):
    """« This is happening.. » ne porte qu'UN jeton significatif, et c'est
    exactement le cas reel : exiger un titre riche DES DEUX cotes reperdrait
    le defaut qu'on corrige."""
    db_session.autoflush = False
    assert len(significant_tokens(TITRE_PUBLIE_1)) < 3
    _publie, reprise = _cas_1(db_session)

    stats = _lier(db_session, cache, _Arbitre())

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is not None
    assert stats.titres_vides_ecartes == 0


def test_deux_titres_pauvres_sont_ecartes_sans_rien_payer(db_session, cache):
    """Quand AUCUN des deux titres ne dit rien, la porte du resume serait le
    SEUL rempart : l'arbitre n'aurait que deux resumes a comparer. Meme seuil
    que le garde-fou de contenu de la re-ingestion (`TITRE_JETONS_MIN`)."""
    db_session.autoflush = False
    publie, reprise = _cas_1(db_session)
    publie.title = "This is happening.."
    reprise.title = "Post Instagram"
    db_session.commit()
    arbitre = _Arbitre()

    stats = _lier(db_session, cache, arbitre)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert arbitre.appels == []
    assert stats.titres_vides_ecartes == 1


# -- ce qui NE change PAS, et ce qui change -------------------------------


def test_le_garde_fou_de_recurrence_vaut_aussi_contre_l_archive(db_session, cache):
    """Lier une edition hebdomadaire a la precedente sortirait un article de la
    file au nom d'un format qui reviendra la semaine prochaine -- et contre
    l'archive ce serait DEFINITIF, le representant `USED` etant verrouille."""
    db_session.autoflush = False
    # Le representant d'archive est une actu ORDINAIRE, d'une autre source : la
    # paire doit etre arbitrable par les resumes et INVISIBLE aux mesures de
    # titre, sinon c'est la bande « certaine » qui la lierait et ce test ne
    # dirait plus rien du garde-fou de recurrence.
    _article(
        db_session, _source(db_session, "deja-publie"), TITRE_PUBLIE_1,
        RESUME_PUBLIE_1, 1, heures=72, status=ArticleStatus.USED,
    )
    serie = _source(db_session, "billboard")
    reprise = _article(
        db_session, serie, "Billboard Hot 100 Top 10 Countdown for Sept. 26, 2026",
        RESUME_REPRISE_1, 2, heures=2,
    )
    for rang, jour in enumerate(("2026-09-05", "2026-09-12", "2026-09-19", "2026-08-29"), 1):
        quand = dt.datetime.fromisoformat(jour).replace(tzinfo=UTC)
        evenement = Event(titre=f"edition {rang}", first_seen_at=quand, last_seen_at=quand)
        db_session.add(evenement)
        db_session.commit()
        db_session.add(
            EventObservation(
                event_id=evenement.id,
                platform=SourceType.RSS,
                source_id=serie.id,
                source_role=SourceRole.SECONDARY,
                published_at=quand,
                observed_at=dt.datetime.now(UTC) - dt.timedelta(days=rang),
                title=f"Billboard Hot 100 Top 10 Countdown for {jour}",
                url=f"https://exemple/obs/{rang}",
                source_name=serie.name,
            )
        )
        db_session.commit()
    arbitre = _Arbitre()

    stats = _lier(db_session, cache, arbitre)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert arbitre.appels == []
    assert stats.recurrences_ecartees == 1


def test_deux_notes_sous_le_seuil_n_ecartent_PAS_une_paire_d_archive(db_session, cache):
    """Ce garde-fou de la bande FRAICHE n'a pas d'objet ici : le representant
    est `USED`, donc PUBLIE -- il y a toujours une publication a proteger, et
    son evenement est permanent par construction."""
    db_session.autoflush = False
    publie, reprise = _cas_1(db_session)
    publie.relevance_score = 20
    reprise.relevance_score = 20
    db_session.commit()

    stats = _lier(db_session, cache, _Arbitre())

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id == publie.id
    assert stats.resumes_sous_le_seuil == 0


def test_la_fenetre_d_archive_reste_bornee(db_session, cache):
    """10 jours, pas l'age du projet : au-dela de la duree de vie editoriale
    mesuree (max 8,91 j sur 298 ecarts reels), le risque de faux positif
    depasse le service rendu -- c'est ce qui avait fait ramener 30 a 7."""
    db_session.autoflush = False
    assert settings.cluster_archive_jours == 10
    _publie, reprise = _cas_2(db_session, heures_archive=11 * 24)

    _lier(db_session, cache, _Arbitre())

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None


def test_le_budget_de_l_archive_est_separe_de_celui_de_la_porte_fraiche(db_session, cache):
    """L'archive est la seule bande dont un faux negatif republie une actu deja
    postee : son budget ne doit pas etre mange par la porte fraiche, qui peut
    presenter des dizaines de paires au premier passage."""
    db_session.autoflush = False
    publie, reprise = _cas_1(db_session)
    arbitre = _Arbitre()

    stats = _lier(db_session, cache, arbitre, arbitrage_resume_max=0, arbitrage_resume_min=6)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id == publie.id
    assert stats.resumes_archive_arbitres == 1


def test_le_budget_de_l_archive_epuise_ne_pose_aucun_lien(db_session, cache):
    db_session.autoflush = False
    _publie, reprise = _cas_1(db_session)
    arbitre = _Arbitre()

    _lier(db_session, cache, arbitre, arbitrage_resume_archive_max=0)

    db_session.refresh(reprise)
    assert reprise.duplicate_of_id is None
    assert arbitre.appels == []
