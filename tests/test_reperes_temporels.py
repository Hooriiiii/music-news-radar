"""Un post ne doit pas annoncer comme a venir ce qui est deja sorti.

Cas reel qui ouvre ce fichier -- post 153, publie le 2026-10-04 a 10h21 : la cover
disait « **This Dream Of You**, le nouveau son de **Martin Garrix**, sort a minuit »
et la legende « sort ce soir a minuit », alors que le brouillon avait ete compose le
02/10 a 12h30 (45,9 h plus tot) et que le morceau etait sorti dans la nuit du 2 au 3.

Toutes les dates de ce fichier sont RELATIVES A MAINTENANT : un repere temporel est
precisement ce qu'une date figee dans une fixture fait mentir.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.content.compose import build_user_prompt, probleme_repere_relatif
from app.content.reperes import (
    ANCRE_JOUR,
    ANCRE_JOUR_NOMME,
    ANCRE_MOIS,
    ANCRE_SEMAINE,
    JOURS,
    repere_perime,
    repere_relatif,
)
from app.models import Article, Source, SourceType

PARIS = dt.timezone(dt.timedelta(hours=2))


def _a(jour: dt.date, heure: int = 12) -> dt.datetime:
    return dt.datetime(jour.year, jour.month, jour.day, heure, tzinfo=PARIS)


def _lundi_passe() -> dt.date:
    """Le lundi de la semaine en cours, pour ancrer les tests de jour nomme."""
    aujourd_hui = dt.datetime.now(PARIS).date()
    return aujourd_hui - dt.timedelta(days=aujourd_hui.weekday())


# --- Ce qui est un repere relatif, et ce qui n'en est pas -------------------


@pytest.mark.parametrize(
    "texte, attendu, ancre",
    [
        ("**This Dream Of You** sort a minuit", "minuit", ANCRE_JOUR),
        ("Annonce a **Tomorrowland**, le titre sort ce soir", "ce soir", ANCRE_JOUR),
        ("Le single tombe **demain a 18h**", "demain", ANCRE_JOUR),
        ("L'EP debarque apres-demain", "apres-demain", ANCRE_JOUR),
        ("L'annonce tombe **aujourd'hui**", "aujourd'hui", ANCRE_JOUR),
        ("Des posts mysterieux reperes hier", "hier", ANCRE_JOUR),
        ("Le show le plus dingue a **LA** ce week-end", "ce week-end", ANCRE_SEMAINE),
        ("**4 sorties** a suivre cette semaine", "cette semaine", ANCRE_SEMAINE),
        ("L'album arrive la semaine prochaine", "la semaine prochaine", ANCRE_SEMAINE),
        ("**plus une place** disponible ce mois-ci", "ce mois-ci", ANCRE_MOIS),
        ("L'EP de **Curbi** debarque ce vendredi", "ce vendredi", ANCRE_JOUR_NOMME),
        ("**Martin Garrix** balance le titre vendredi prochain", "vendredi prochain", ANCRE_JOUR_NOMME),
    ],
)
def test_les_reperes_releves_en_production_sont_reconnus(texte, attendu, ancre):
    repere = repere_relatif(texte)
    assert repere is not None and repere.expression.lower() == attendu
    assert repere.ancre == ancre


@pytest.mark.parametrize(
    "texte",
    [
        # Mesure : 15 covers et 11 legendes en base. « Dispo des maintenant »
        # decrit une sortie DEJA faite, qui le restera -- jamais dementi par deux
        # jours de retard.
        "**DC4 Vol. 6** est **dispo maintenant** chez Drumcode",
        '"Run", **Amelie Lens** x **Angele** est dispo **partout** des maintenant',
        # Vague par nature : rien a redater.
        "le vinyle live de **TWICE** arrive bientot",
        "Bientot sur **YouTube** : la performance complete",
        # LE piege : un rappel du PASSE, pas une annonce.
        "Qui etaient aux platines de l'**Ushuaia** ce soir-la",
        # « soir » n'est pas « ce soir » : la soiree Trash n'est pas un repere.
        "La soiree **Trash** revient a **Paris**",
        # Une date ABSOLUE est exactement ce qu'on demande.
        "Ne le **9 octobre** a Ibiza, **NIRVANA** debarque",
        "**This Dream Of You** sort le **3 octobre** a minuit".replace("minuit", "20h"),
    ],
)
def test_ce_qui_ne_vieillit_pas_n_est_pas_signale(texte):
    assert repere_relatif(texte) is None


def test_aujourd_hui_est_ecarte_de_la_prose_et_garde_sur_la_cover():
    """Mesure : dans une LEGENDE, 8 des 10 « aujourd'hui » de la base sont le sens
    ATEMPOREL (« de nos jours »), qui ne vieillit pas ; dans une COVER, les 6
    occurrences sont datees. Meme precedent que `probleme_accents` contre
    `probleme_accents_de_cover` : deux textes de nature differente, deux criteres."""
    atemporel = (
        "Le morceau, ne a Bruxelles avec One Track Brain, ressurgit aujourd'hui "
        "sous une forme nouvelle."
    )
    assert repere_relatif(atemporel, prose=True) is None
    assert repere_relatif(atemporel) is not None
    # Les autres reperes restent signales dans la prose.
    assert repere_relatif("Le titre sort demain sur toutes les plateformes", prose=True) is not None


def test_le_repere_rapporte_est_le_plus_a_gauche():
    """Sinon l'ordre de la table deciderait, et le message de relance pointerait un
    mot que le lecteur du texte ne voit pas en premier."""
    repere = repere_relatif("Ce week-end, le festival ferme ses portes demain soir")
    assert repere is not None and repere.expression.lower() == "ce week-end"


# --- Quand un repere devient FAUX ------------------------------------------


def test_un_repere_du_jour_tient_la_journee_et_pas_un_jour_de_plus():
    lundi = _lundi_passe()
    repere = repere_relatif("le titre sort ce soir")
    assert repere is not None
    assert repere_perime(repere, _a(lundi, 8), _a(lundi, 23)) is False
    assert repere_perime(repere, _a(lundi, 23), _a(lundi + dt.timedelta(days=1), 7)) is True


def test_le_cas_du_post_153_minuit_lu_deux_jours_plus_tard():
    """45,9 h d'ecart, un morceau sorti depuis deux jours : perime."""
    compose = dt.datetime.now(PARIS) - dt.timedelta(hours=45, minutes=54)
    repere = repere_relatif("**This Dream Of You** sort a minuit")
    assert repere is not None
    assert repere_perime(repere, compose, dt.datetime.now(PARIS)) is True


def test_aucun_seuil_en_heures_ne_separe_les_deux_cas_mesures():
    """La demonstration qui justifie un critere de CALENDRIER et non d'heures :
    le post 101 etait FAUX a 10,8 h d'ecart (« demain », minuit franchi) quand le
    post 115 restait VRAI a 19,5 h (« vendredi prochain », meme semaine)."""
    lundi = _lundi_passe()
    mercredi, jeudi = lundi + dt.timedelta(days=2), lundi + dt.timedelta(days=3)
    vendredi, samedi = lundi + dt.timedelta(days=4), lundi + dt.timedelta(days=5)

    cent_un = repere_relatif("le titre arrive demain a 18h")
    assert cent_un is not None
    assert repere_perime(cent_un, _a(mercredi, 20), _a(jeudi, 7)) is True  # 11 h, FAUX

    cent_quinze = repere_relatif("**Martin Garrix** balance le titre vendredi prochain")
    assert cent_quinze is not None
    assert repere_perime(cent_quinze, _a(vendredi, 20), _a(samedi, 16)) is False  # 20 h, VRAI


def test_un_jour_nomme_meurt_quand_sa_date_est_passee_meme_dans_la_semaine():
    """« ce vendredi » ecrit un jeudi est encore vrai le vendredi, plus le samedi --
    et la semaine ISO n'a pas change entre les deux."""
    lundi = _lundi_passe()
    jeudi, vendredi, samedi = (lundi + dt.timedelta(days=n) for n in (3, 4, 5))
    repere = repere_relatif("l'EP debarque ce vendredi")
    assert repere is not None
    assert repere_perime(repere, _a(jeudi), _a(vendredi)) is False
    assert repere_perime(repere, _a(jeudi), _a(samedi)) is True


def test_un_repere_de_semaine_tient_la_semaine():
    lundi = _lundi_passe()
    repere = repere_relatif("le show le plus dingue ce week-end")
    assert repere is not None
    assert repere_perime(repere, _a(lundi), _a(lundi + dt.timedelta(days=6))) is False
    assert repere_perime(repere, _a(lundi), _a(lundi + dt.timedelta(days=7))) is True


def test_un_repere_de_mois_tient_le_mois():
    repere = repere_relatif("une sortie prevue le mois prochain")
    assert repere is not None
    assert repere_perime(repere, _a(dt.date(2026, 9, 1)), _a(dt.date(2026, 9, 30))) is False
    assert repere_perime(repere, _a(dt.date(2026, 9, 30)), _a(dt.date(2026, 10, 1))) is True


def test_une_horloge_qui_recule_ne_perime_rien():
    """Le doute profite a la publication : on ne recompose pas sur une date qu'on
    ne comprend pas."""
    repere = repere_relatif("le titre sort ce soir")
    assert repere is not None
    maintenant = dt.datetime.now(PARIS)
    assert repere_perime(repere, maintenant, maintenant - dt.timedelta(days=2)) is False


def test_un_horodatage_naif_ne_leve_jamais():
    """Le gel lit ce module ; une exception y couterait un creneau."""
    repere = repere_relatif("le titre sort ce soir")
    assert repere is not None
    naif = dt.datetime(2026, 10, 2, 12, 30)
    assert repere_perime(repere, naif, naif + dt.timedelta(days=2)) is True


def test_le_jour_est_celui_de_paris_pas_celui_d_utc():
    """Un post compose a 23h30 heure de Paris et relu a 00h30 a change de jour pour
    son lecteur, meme si les deux instants tombent le meme jour UTC."""
    repere = repere_relatif("le titre sort ce soir")
    assert repere is not None
    paris = dt.timezone(dt.timedelta(hours=2))
    compose = dt.datetime(2026, 7, 10, 23, 30, tzinfo=paris)  # 21h30 UTC le 10
    relu = dt.datetime(2026, 7, 11, 0, 30, tzinfo=paris)      # 22h30 UTC le 10
    assert compose.astimezone(dt.timezone.utc).date() == relu.astimezone(dt.timezone.utc).date()
    assert repere_perime(repere, compose, relu) is True


# --- Le controle de composition --------------------------------------------


def test_le_controle_decrit_le_repere_et_demande_une_date_absolue():
    probleme = probleme_repere_relatif("**This Dream Of You** sort ce soir")
    assert probleme is not None
    assert "ce soir" in probleme and "date ABSOLUE" in probleme


def test_le_controle_laisse_passer_une_date_absolue():
    assert probleme_repere_relatif("**This Dream Of You** sort le **3 octobre**") is None


def test_le_prompt_donne_la_date_du_jour_en_francais(db_session):
    """Sans elle, la regle 9 est insatisfiable : le modele ne peut ni convertir le
    « demain » de l'article, ni voir que cet article date de la veille (post 156)."""
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://tsugi.fr/feed")
    db_session.add(source)
    db_session.commit()
    article = Article(
        source_id=source.id, url="https://tsugi.fr/a", title="Un titre", raw_hash="z" * 64,
        published_at=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
    )
    db_session.add(article)
    db_session.commit()
    prompt = build_user_prompt(article, aujourd_hui=dt.date(2026, 10, 2))
    assert prompt.startswith("Date du jour : vendredi 02/10/2026")
    assert "Publie le : 01/10/2026" in prompt
    # Et sans argument : la date du jour a l'heure de publication.
    attendue = dt.datetime.now(ZoneInfo(settings.publication_fuseau)).date()
    assert f"Date du jour : {JOURS[attendue.weekday()]} {attendue:%d/%m/%Y}" in build_user_prompt(
        article
    )
