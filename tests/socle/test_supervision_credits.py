"""Le compte a l'arret faute de credits doit se lire sur le bandeau.

CONSTAT DU 2026-10-31 : plus de credits Anthropic, `pipeline.log` ecrit « crédits API
épuisés — le scoring est arrêté » et 1246 erreurs, pendant que le voyant Composition
dit en ATTENTION « scoring interrompu (crédits, clé API ou réseau) ». Le signal ne
vivait que dans le rapport du run : rien ne le consignait pour le bandeau, qui ne
peut payer aucun appel a l'affichage. Le voici consigne (`credits_anthropic.json`).

Quatre proprietes negatives, donc une preuve par mutation a la fin : un refus reseau
ne vaut pas credits epuises, le premier succes eteint, absent ne vaut pas vert, et lire
n'ecrit rien.
"""

import datetime as dt
import json
import sys
import types
from pathlib import Path

import pytest

import app.pipeline.credits_anthropic as credits
from app import supervision
from app.config import settings
from app.models import Article, Creneau, EtatCreneau, FormatCreneau, Source, SourceType
from app.pipeline.scoring import score_pending
from app.schedule.runner import RapportPublication, _composer
from app.supervision import ATTENTION, INCONNU, OK, PANNE, voyant_composition

MAINTENANT = dt.datetime(2026, 10, 31, 12, 0, tzinfo=dt.timezone.utc)
REFUS_API = "Error code: 400 - Your credit balance is too low to access the Anthropic API."


def _chemin() -> Path:
    return settings.meta_token_path.parent / credits.FICHIER_ETAT


def _ecrire(**champs):
    _chemin().parent.mkdir(parents=True, exist_ok=True)
    _chemin().write_text(json.dumps(champs))


def _lire() -> dict:
    return json.loads(_chemin().read_text())


def _article(db, nom="a"):
    source = db.query(Source).first()
    if source is None:
        source = Source(name="s", type=SourceType.RSS, url="http://s")
        db.add(source)
        db.commit()
    article = Article(source_id=source.id, url=f"http://s/{nom}", title=nom, raw_hash=nom,
                      fetched_at=MAINTENANT - dt.timedelta(hours=5))
    db.add(article)
    db.commit()
    return article


def _note(_article):
    from app.pipeline.scoring import ArticleScore

    return ArticleScore(relevance=70, hotness=10, emergence=10, category="other", imprint=None,
                        summary="s", artists=[])


# --- Ce que le scoring consigne ------------------------------------------------


def test_un_refus_de_credits_au_scoring_se_consigne(db_session):
    _article(db_session)

    def scorer(_article):
        raise RuntimeError(REFUS_API)

    stats = score_pending(db_session, scorer=scorer, prefilter=None)
    assert stats.errors == 1
    assert credits.lire_etat(_lire()).epuises is True


def test_une_panne_reseau_ne_se_consigne_pas_en_credits_epuises(db_session):
    """Le coeur du « ne crie pas au loup » : une coupure ne dit RIEN des credits."""
    _article(db_session)

    def scorer(_article):
        raise ConnectionError("Connection error.")

    score_pending(db_session, scorer=scorer, prefilter=None)
    assert not _chemin().exists()


def test_une_panne_reseau_n_efface_pas_non_plus_un_refus_consigne(db_session):
    _ecrire(refus_le=(MAINTENANT - dt.timedelta(hours=1)).isoformat())
    _article(db_session)

    def scorer(_article):
        raise ConnectionError("Connection error.")

    score_pending(db_session, scorer=scorer, prefilter=None)
    assert credits.lire_etat(_lire()).epuises is True


def test_le_premier_succes_eteint_le_refus(db_session):
    """Le solde recharge : rien a cliquer, le passage suivant le constate."""
    _ecrire(refus_le=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat())
    _article(db_session)
    stats = score_pending(db_session, scorer=_note, prefilter=None)
    assert stats.scored == 1
    assert credits.lire_etat(_lire()).epuises is False


def test_une_note_locale_ne_prouve_rien_sur_les_credits(db_session, monkeypatch):
    """Le pre-filtre ecarte sans appeler Claude : ce n'est pas un succes de l'API, et
    un refus consigne doit rester."""
    _ecrire(refus_le=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat())
    _article(db_session)
    monkeypatch.setattr(settings, "local_prefilter_threshold", 45)
    basse = _note(None).model_copy(update={"relevance": 10})

    def jamais(_article):
        raise AssertionError("Claude ne devait pas etre appele")

    stats = score_pending(db_session, scorer=jamais, prefilter=lambda _a: basse)
    assert stats.prefiltered == 1
    assert credits.lire_etat(_lire()).epuises is True


# --- Le fichier partage ---------------------------------------------------------


def test_deux_agents_qui_se_croisent_gardent_le_dernier_evenement():
    """Relecture-fusion : le passage horaire et la veille ecrivent le meme fichier.
    C'est la DATE qui tranche, jamais l'ordre d'arrivee a l'ecriture."""
    tot = MAINTENANT - dt.timedelta(hours=2)
    credits.consigner_refus(MAINTENANT)
    credits.consigner_succes(tot)
    donnees = _lire()
    assert donnees["refus_le"] == MAINTENANT.isoformat()
    assert donnees["succes_le"] == tot.isoformat()
    assert credits.lire_etat(donnees).epuises is True
    # Une date plus ancienne ne recule jamais un champ.
    credits.consigner_refus(tot)
    assert _lire()["refus_le"] == MAINTENANT.isoformat()


def test_le_temporaire_est_nomme_par_processus_et_ne_reste_pas(monkeypatch):
    vus = []
    original = credits.os.replace

    def replace(source, cible):
        vus.append(str(source))
        original(source, cible)

    monkeypatch.setattr(credits.os, "replace", replace)
    credits.consigner_succes(MAINTENANT)
    assert str(credits.os.getpid()) in vus[0]
    assert [c.name for c in _chemin().parent.iterdir()] == [credits.FICHIER_ETAT]


def test_une_ecriture_ratee_ne_leve_jamais(monkeypatch):
    """Un disque plein ne fait perdre ni une note, ni une composition."""

    def casse(*_a, **_k):
        raise OSError("disque plein")

    monkeypatch.setattr(credits.os, "replace", casse)
    assert credits.consigner_refus(MAINTENANT) is False


def test_un_fichier_corrompu_est_repare_par_l_ecriture_suivante():
    _chemin().parent.mkdir(parents=True, exist_ok=True)
    _chemin().write_text("{tronque")
    assert credits.consigner_succes(MAINTENANT) is True
    assert credits.lire_etat(_lire()).epuises is False


# --- Le voyant ------------------------------------------------------------------


def test_credits_epuises_en_panne_avec_leur_effet(db_session):
    _ecrire(refus_le=MAINTENANT.isoformat())
    voyant = voyant_composition(db_session, MAINTENANT)
    assert voyant.niveau == PANNE
    assert "crédits Anthropic épuisés" in voyant.raison
    assert "rien ne se note ni ne se compose" in voyant.raison
    assert "plus aucun post ne partira" in voyant.raison


def test_le_refus_prime_sur_l_arriere_de_scoring(db_session):
    """C'est le message exact du constat : « crédits, clé API ou réseau » en ATTENTION."""
    _ecrire(refus_le=MAINTENANT.isoformat())
    _article(db_session)
    voyant = voyant_composition(db_session, MAINTENANT)
    assert voyant.niveau == PANNE
    assert "clé API ou réseau" not in voyant.raison


def test_apres_le_succes_le_voyant_sort_tout_seul(db_session):
    _ecrire(refus_le=(MAINTENANT - dt.timedelta(hours=1)).isoformat(), succes_le=MAINTENANT.isoformat())
    assert voyant_composition(db_session, MAINTENANT).niveau in {OK, ATTENTION}
    assert "crédits Anthropic épuisés" not in (voyant_composition(db_session, MAINTENANT).raison or "")


def test_une_vieille_erreur_de_composition_ne_tient_plus_apres_le_succes(db_session):
    """L'ancienne branche lisait `creneaux.erreur` sur 36 h : un solde recharge restait
    en PANNE un jour et demi."""
    _ecrire(succes_le=MAINTENANT.isoformat())
    db_session.add(Creneau(
        publier_a=MAINTENANT - dt.timedelta(hours=2), format=FormatCreneau.CARROUSEL,
        etat=EtatCreneau.LIBRE, erreur=f"composition interrompue : {REFUS_API}",
    ))
    db_session.commit()
    voyant = voyant_composition(db_session, MAINTENANT)
    assert voyant.niveau != PANNE
    assert "credit balance" not in (voyant.raison or "")


@pytest.mark.parametrize("contenu", [None, "{tronque", "[]", "{}", '{"refus_le": "pas une date"}'])
def test_absent_ou_illisible_vaut_inconnu_jamais_vert(db_session, contenu):
    if contenu is not None:
        _chemin().parent.mkdir(parents=True, exist_ok=True)
        _chemin().write_text(contenu)
    voyant = voyant_composition(db_session, MAINTENANT)
    assert voyant.niveau == INCONNU
    assert "crédits" in voyant.raison


def test_une_preuve_en_base_reste_une_panne_meme_sans_fichier(db_session):
    db_session.add(Creneau(
        publier_a=MAINTENANT - dt.timedelta(hours=2), format=FormatCreneau.CARROUSEL,
        etat=EtatCreneau.LIBRE, erreur=f"composition interrompue : {REFUS_API}",
    ))
    db_session.commit()
    assert voyant_composition(db_session, MAINTENANT).niveau == PANNE


def test_lire_le_voyant_n_ecrit_rien(db_session):
    voyant_composition(db_session, MAINTENANT)
    assert not _chemin().exists()
    _ecrire(succes_le=MAINTENANT.isoformat())
    avant = (_chemin().read_text(), _chemin().stat().st_mtime_ns)
    voyant_composition(db_session, MAINTENANT)
    assert (_chemin().read_text(), _chemin().stat().st_mtime_ns) == avant


# --- La composition, l'autre appelant de Claude ----------------------------------


def _creneau_a_composer(db):
    article = _article(db, "compo")
    article.media = [{"type": "photo", "url": "https://img/1.jpg"}]
    creneau = Creneau(publier_a=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
                      format=FormatCreneau.CARROUSEL, etat=EtatCreneau.PROGRAMME, article_id=article.id)
    db.add(creneau)
    db.commit()
    return article, creneau


def _lancer_composition(db, monkeypatch, composer):
    from app.schedule import runner

    monkeypatch.setattr(runner, "resoudre_fond", lambda visuels, template: None)
    return _composer(db, dt.datetime.now(dt.timezone.utc), composer, lambda *a: None, RapportPublication(),
                     {FormatCreneau.CARROUSEL}, lambda *a: [])


def test_un_refus_a_la_composition_se_consigne_et_n_exclut_pas_l_article(db_session, monkeypatch):
    article, creneau = _creneau_a_composer(db_session)

    def composer(*_a, **_k):
        raise RuntimeError(REFUS_API)

    _lancer_composition(db_session, monkeypatch, composer)
    assert credits.lire_etat(_lire()).epuises is True
    db_session.refresh(article)
    db_session.refresh(creneau)
    assert article.file_exclu_raison is None
    assert creneau.article_id == article.id


def test_une_panne_reseau_a_la_composition_ne_consigne_rien(db_session, monkeypatch):
    article, _creneau = _creneau_a_composer(db_session)

    def composer(*_a, **_k):
        raise ConnectionError("Connection error.")

    _lancer_composition(db_session, monkeypatch, composer)
    assert not _chemin().exists()
    db_session.refresh(article)
    assert article.file_exclu_raison is None


# --- Preuve negative, par mutation ---------------------------------------------


def _supervision_mutee(monkeypatch, avant, apres):
    source = Path(supervision.__file__).read_text(encoding="utf-8")
    assert avant in source, "la mutation ne trouve plus sa cible : le code a change de forme"
    module = types.ModuleType("supervision_credits_mutee")
    monkeypatch.setitem(sys.modules, "supervision_credits_mutee", module)
    exec(compile(source.replace(avant, apres), "supervision_credits_mutee", "exec"), module.__dict__)
    return module


def test_un_fichier_absent_peint_en_vert_rougit_sous_mutation(db_session, monkeypatch):
    mute = _supervision_mutee(monkeypatch, "    if credits_inconnus is not None:\n", "    if False:\n")
    assert voyant_composition(db_session, MAINTENANT).niveau == INCONNU
    assert mute.voyant_composition(db_session, MAINTENANT).niveau != INCONNU


def test_le_succes_qui_n_eteint_pas_rougit_sous_mutation(db_session, monkeypatch):
    _ecrire(succes_le=MAINTENANT.isoformat())
    db_session.add(Creneau(
        publier_a=MAINTENANT - dt.timedelta(hours=2), format=FormatCreneau.CARROUSEL,
        etat=EtatCreneau.LIBRE, erreur=f"composition interrompue : {REFUS_API}",
    ))
    db_session.commit()
    mute = _supervision_mutee(monkeypatch, "    resolu = etat_credits is not None and not etat_credits.epuises\n",
                              "    resolu = False\n")
    assert mute.voyant_composition(db_session, MAINTENANT).niveau == PANNE


def test_une_panne_reseau_prise_pour_un_refus_rougit_sous_mutation(db_session, monkeypatch):
    """Si `est_refus_de_credits` acceptait tout, la coupure consignerait « épuisés »."""
    monkeypatch.setattr(credits, "est_refus_de_credits", lambda _e: True)
    _article(db_session)

    def scorer(_article):
        raise ConnectionError("Connection error.")

    score_pending(db_session, scorer=scorer, prefilter=None)
    assert credits.lire_etat(_lire()).epuises is True
