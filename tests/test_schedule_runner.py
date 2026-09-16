"""Le passage de l'agent, avec une horloge injectee et des outils factices."""

import datetime as dt

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.content.compose import CompositionError, CoverVariante, PostDraft
from app.content.render import RenduIndisponible
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, PostStatus, Source, SourceType
from app.publish.instagram import ErreurInstagram
from app.publish.preparation import PreparationImpossible
from app.schedule.runner import tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import utc

BASE = dt.datetime(2026, 9, 20, 4, 0, tzinfo=dt.timezone.utc)
H0, H1 = prochains_horaires(BASE, 2)


def _draft():
    variante = CoverVariante(headline="**Accroche**", title="**Titre**", subtitle="**Sous**")
    return PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=["#electro"])


class Outils:
    def __init__(self):
        self.composes, self.prepares, self.publies, self.notifications = [], [], [], []
        self.erreur_composition = self.erreur_preparation = self.erreur_publication = None

    def composer(self, article):
        self.composes.append(article.id)
        if self.erreur_composition:
            raise self.erreur_composition
        return _draft()

    def preparer(self, post, article, dossier):
        self.prepares.append(post.id)
        if self.erreur_preparation:
            raise self.erreur_preparation
        return ["p_01.jpg"]

    def publier(self, dossier, noms, legende, publier_vraiment):
        self.publies.append((noms, publier_vraiment))
        if self.erreur_publication:
            raise self.erreur_publication
        return "ig42"

    def notifier(self, titre, message):
        self.notifications.append(message)

    def tourner(self, db, maintenant, mode="reel"):
        return tourner(db, maintenant, mode=mode, composer=self.composer, preparer=self.preparer,
                       publier=self.publier, notifier=self.notifier)


@pytest.fixture()
def article(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://t/1", title="Actu", raw_hash="h1",
                      relevance_score=90, hotness_score=30, published_at=BASE - dt.timedelta(hours=1),
                      media=[{"type": "photo", "url": "https://img/1.jpg"}])
    db_session.add(article)
    db_session.commit()
    return article


def _creneau(db, heure):
    return next(c for c in db.query(Creneau).all() if utc(c.publier_a) == heure)


def _creneau_h0(db):
    return _creneau(db, H0)


def _second_article(db, article):
    autre = Article(source_id=article.source_id, url="https://t/2", title="Autre actu", raw_hash="h2",
                    relevance_score=80, hotness_score=20, published_at=BASE - dt.timedelta(hours=2),
                    media=[{"type": "photo", "url": "https://img/2.jpg"}])
    db.add(autre)
    db.commit()
    return autre


def _jusqu_a_publication(outils, db, heure, mode="reel"):
    for decalage in (-180, -60, 20):
        outils.tourner(db, heure + dt.timedelta(minutes=decalage), mode=mode)


def test_en_arret_l_agent_ne_fait_rien(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3), mode="arret")
    assert db_session.query(Creneau).count() == 0
    assert outils.composes == []


def test_la_chaine_complete_en_mode_reel(db_session, article):
    outils = Outils()

    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    assert outils.composes == [article.id]
    creneau = _creneau_h0(db_session)
    assert (creneau.etat, creneau.post_id is not None) == (EtatCreneau.PROGRAMME, True)
    assert outils.prepares == []

    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    assert (creneau.etat, creneau.fichiers) == (EtatCreneau.GELE, ["p_01.jpg"])

    rapport = outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    assert outils.publies == [(["p_01.jpg"], True)]
    assert (creneau.etat, creneau.ig_media_id, rapport.publies) == (EtatCreneau.PUBLIE, "ig42", 1)
    assert db_session.get(Post, creneau.post_id).status == PostStatus.PUBLISHED
    assert db_session.get(Article, article.id).status == ArticleStatus.USED


def test_en_apercu_rien_n_est_publie(db_session, article):
    outils = Outils()
    for decalage in (-180, -60, 20):
        outils.tourner(db_session, H0 + dt.timedelta(minutes=decalage), mode="apercu")
    creneau = _creneau_h0(db_session)
    assert outils.publies == [(["p_01.jpg"], False)]
    assert creneau.etat == EtatCreneau.APERCU
    assert db_session.get(Post, creneau.post_id).status == PostStatus.DRAFT
    assert db_session.get(Article, article.id).status == ArticleStatus.NEW


def test_au_dela_de_90_minutes_de_retard_on_ne_publie_pas(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    rapport = outils.tourner(db_session, H0 + dt.timedelta(minutes=120))
    assert outils.publies == []
    assert (_creneau_h0(db_session).etat, rapport.manques) == (EtatCreneau.MANQUE, 1)


def test_un_mac_reveille_en_retard_gele_et_publie_dans_le_meme_passage(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 + dt.timedelta(minutes=30))
    assert (len(outils.prepares), len(outils.publies)) == (1, 1)
    assert _creneau_h0(db_session).etat == EtatCreneau.PUBLIE


def test_une_publication_refusee_previent(db_session, article):
    outils = Outils()
    outils.erreur_publication = ErreurInstagram("jeton expire")
    for decalage in (-180, -60, 20):
        outils.tourner(db_session, H0 + dt.timedelta(minutes=decalage))
    creneau = _creneau_h0(db_session)
    assert creneau.etat == EtatCreneau.ECHEC
    assert "jeton expire" in creneau.erreur
    assert len(outils.notifications) == 1 and "jeton expire" in outils.notifications[0]


def test_une_composition_impossible_exclut_l_article(db_session, article):
    outils = Outils()
    outils.erreur_composition = CompositionError("sortie invalide")
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    assert "composition" in db_session.get(Article, article.id).file_exclu_raison
    creneau = _creneau_h0(db_session)
    assert (creneau.etat, creneau.article_id) == (EtatCreneau.LIBRE, None)


def test_une_panne_de_credits_n_exclut_pas_l_article(db_session, article):
    outils = Outils()
    outils.erreur_composition = RuntimeError("Your credit balance is too low")
    rapport = outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    assert db_session.get(Article, article.id).file_exclu_raison is None
    assert "credit" in _creneau_h0(db_session).erreur
    assert rapport.erreurs


def test_un_gel_impossible_exclut_l_article_et_libere_le_creneau(db_session, article):
    outils = Outils()
    outils.erreur_preparation = PreparationImpossible("accroche trop longue : la cover deborde")
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    assert "deborde" in db_session.get(Article, article.id).file_exclu_raison
    assert _creneau_h0(db_session).etat == EtatCreneau.LIBRE


def test_on_ne_compose_que_les_creneaux_a_moins_de_24_heures(db_session, article):
    for n in range(2, 9):
        db_session.add(Article(source_id=article.source_id, url=f"https://t/{n}", title=f"Actu {n}",
                               raw_hash=f"h{n}", relevance_score=80, published_at=BASE,
                               media=[{"type": "photo", "url": f"https://img/{n}.jpg"}]))
    db_session.commit()
    outils = Outils()
    maintenant = H0 - dt.timedelta(hours=3)
    outils.tourner(db_session, maintenant)
    attendus = [h for h in prochains_horaires(maintenant, 6) if h <= maintenant + dt.timedelta(hours=24)]
    assert 0 < len(outils.composes) == len(attendus) < 6


def test_une_publication_interrompue_n_est_jamais_retentee(db_session, article):
    outils = Outils()
    etats_vus = []

    def publier_puis_crash(dossier, noms, legende, publier_vraiment):
        outils.publies.append((noms, publier_vraiment))
        # Une autre session voit ce qui est COMMITTE au moment de l'appel.
        with sessionmaker(bind=db_session.get_bind())() as autre:
            etats_vus.append(autre.get(Creneau, _creneau_h0(db_session).id).etat)
        raise KeyboardInterrupt

    outils.publier = publier_puis_crash
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    with pytest.raises(KeyboardInterrupt):
        outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    assert etats_vus == [EtatCreneau.ECHEC]

    db_session.rollback()
    db_session.expire_all()
    for decalage in (30, 40):
        outils.tourner(db_session, H0 + dt.timedelta(minutes=decalage))
    _jusqu_a_publication(outils, db_session, H1)
    assert len(outils.publies) == 1
    creneau = _creneau_h0(db_session)
    assert (creneau.etat, creneau.erreur) == (EtatCreneau.ECHEC, "publication interrompue avant confirmation")


def test_un_article_en_echec_de_publication_n_est_pas_remis_en_file(db_session, article):
    outils = Outils()
    outils.erreur_publication = ErreurInstagram("media refuse")
    _jusqu_a_publication(outils, db_session, H0)
    assert "echec de publication : media refuse" == db_session.get(Article, article.id).file_exclu_raison
    _jusqu_a_publication(outils, db_session, H1)
    assert len(outils.publies) == 1
    assert _creneau(db_session, H1).article_id is None


def test_un_gel_en_panne_systemique_n_exclut_pas_l_article(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.erreur_preparation = OSError("disque plein")
    rapport = outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    creneau = _creneau_h0(db_session)
    assert db_session.get(Article, article.id).file_exclu_raison is None
    assert (creneau.etat, creneau.article_id) == (EtatCreneau.PROGRAMME, article.id)
    assert "disque plein" in creneau.erreur and rapport.erreurs
    assert len(outils.notifications) == 1

    outils.erreur_preparation = None
    outils.tourner(db_session, H0 - dt.timedelta(minutes=50))
    assert (creneau.etat, len(outils.prepares)) == (EtatCreneau.GELE, 2)


def test_un_gel_en_panne_systemique_s_arrete_au_premier_creneau(db_session, article, monkeypatch):
    _second_article(db_session, article)
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    assert len(outils.composes) == 2
    # Fenetre de gel elargie : les deux creneaux du jour sont a geler dans ce passage.
    monkeypatch.setattr(settings, "publication_gel_heures", 12)
    outils.erreur_preparation = OSError("disque plein")
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    assert len(outils.prepares) == 1
    assert len(outils.notifications) == 1


def test_en_apercu_la_file_avance(db_session, article):
    autre = _second_article(db_session, article)
    outils = Outils()
    _jusqu_a_publication(outils, db_session, H0, mode="apercu")
    creneau = _creneau_h0(db_session)
    assert (creneau.etat, creneau.publie_le, creneau.ig_media_id) == (EtatCreneau.APERCU, None, "ig42")
    _jusqu_a_publication(outils, db_session, H1, mode="apercu")
    assert _creneau(db_session, H1).article_id == autre.id
    assert _creneau(db_session, H1).etat == EtatCreneau.APERCU
    assert outils.composes == [article.id, autre.id]


def test_un_second_passage_apres_publication_ne_republie_pas(db_session, article):
    outils = Outils()
    _jusqu_a_publication(outils, db_session, H0)
    for decalage in (30, 60, 120):
        outils.tourner(db_session, H0 + dt.timedelta(minutes=decalage))
    assert len(outils.publies) == 1


@pytest.mark.parametrize(("retard", "publie"), [(89, True), (91, False)])
def test_la_borne_de_retard(db_session, article, retard, publie):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 + dt.timedelta(minutes=retard))
    assert bool(outils.publies) is publie
    assert _creneau_h0(db_session).etat == (EtatCreneau.PUBLIE if publie else EtatCreneau.MANQUE)


def test_une_panne_systemique_de_composition_previent(db_session, article):
    outils = Outils()
    outils.erreur_composition = RuntimeError("Your credit balance is too low")
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    assert len(outils.notifications) == 1 and "credit" in outils.notifications[0]


def test_une_notification_en_panne_n_interrompt_pas_le_passage(db_session, article):
    outils = Outils()
    outils.erreur_publication = ErreurInstagram("jeton expire")

    def notifier_en_panne(titre, message):
        raise RuntimeError("osascript absent")

    outils.notifier = notifier_en_panne
    _jusqu_a_publication(outils, db_session, H0)
    assert _creneau_h0(db_session).etat == EtatCreneau.ECHEC


def test_chromium_absent_au_gel_n_exclut_pas_l_article(db_session, article, monkeypatch):
    _second_article(db_session, article)
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    monkeypatch.setattr(settings, "publication_gel_heures", 12)
    outils.erreur_preparation = RenduIndisponible("Chromium Playwright est absent")
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    creneau = _creneau_h0(db_session)
    assert db_session.get(Article, article.id).file_exclu_raison is None
    assert (creneau.etat, creneau.article_id) == (EtatCreneau.PROGRAMME, article.id)
    assert "Chromium" in creneau.erreur
    assert len(outils.notifications) == 1
    assert len(outils.prepares) == 1
