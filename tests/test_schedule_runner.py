"""Le passage de l'agent, avec une horloge injectee et des outils factices."""

import datetime as dt

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import app.schedule.runner as runner

from app.config import settings
from app.content.compose import CompositionError, CoverVariante, PostDraft
from app.content.render import RenduIndisponible
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, PostStatus, Source, SourceType
from app.publish.hosting import TunnelInjoignable
from app.publish.instagram import ErreurInstagram
from app.publish.preparation import PreparationImpossible
from app.schedule.runner import tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import utc


@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    # Ces tests decrivent la file carrousel seule ; les reels ont leurs propres fichiers.
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)


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

    def publier(self, dossier, noms, legende, publier_vraiment, **_crochets):
        self.publies.append((noms, publier_vraiment))
        if self.erreur_publication:
            raise self.erreur_publication
        return "ig42"

    def notifier(self, titre, message):
        self.notifications.append(message)

    def tourner(self, db, maintenant, mode="reel", horloge=None):
        # Horloge fraiche figee sur `maintenant` : l'horloge reelle ferait
        # basculer ces tests en MANQUE une fois leur date passee.
        return tourner(db, maintenant, mode=mode, composer=self.composer, preparer=self.preparer,
                       publier=self.publier, notifier=self.notifier,
                       horloge=horloge or (lambda: maintenant))


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
    attendus = [h for h in prochains_horaires(maintenant, 9) if h <= maintenant + dt.timedelta(hours=24)]
    assert 0 < len(outils.composes) == len(attendus) < 9


def test_une_publication_interrompue_n_est_jamais_retentee(db_session, article):
    outils = Outils()
    etats_vus = []

    def publier_puis_crash(dossier, noms, legende, publier_vraiment, **_crochets):
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


# --- Le dashboard modifie la file pendant un passage (session perimee) --------------------


def _dashboard(db):
    """Une seconde session sur le meme moteur : ce que voit et ecrit le dashboard."""
    return sessionmaker(bind=db.get_bind())()


def test_un_remplacement_pendant_la_composition_n_ecrase_pas_le_choix_du_dashboard(db_session, article):
    autre = _second_article(db_session, article)
    outils = Outils()
    composer = outils.composer

    def composer_pendant_un_remplacement(art):
        if art.id == article.id:
            with _dashboard(db_session) as dashboard:
                h0, h1 = (_creneau(dashboard, h) for h in (H0, H1))
                h1.article_id, h1.etat = None, EtatCreneau.LIBRE
                h0.article_id, h0.post_id, h0.epingle = autre.id, None, True
                dashboard.commit()
        return composer(art)

    outils.composer = composer_pendant_un_remplacement
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    db_session.expire_all()
    h0 = _creneau_h0(db_session)
    assert (h0.article_id, h0.post_id) == (autre.id, None)
    # Le post compose pour rien reste un brouillon, reutilisable.
    (post,) = db_session.query(Post).all()
    assert (post.article_id, post.status) == (article.id, PostStatus.DRAFT)
    # Le creneau H1, libere par le dashboard, n'a pas ete compose sur une vue perimee.
    assert outils.composes == [article.id]


def test_un_saut_pendant_le_gel_n_est_pas_ecrase(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    preparer = outils.preparer

    def preparer_pendant_un_saut(post, art, dossier):
        with _dashboard(db_session) as dashboard:
            creneau = _creneau(dashboard, H0)
            creneau.article_id, creneau.post_id, creneau.etat = None, None, EtatCreneau.LIBRE
            dashboard.commit()
        return preparer(post, art, dossier)

    outils.preparer = preparer_pendant_un_saut
    rapport = outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    db_session.expire_all()
    creneau = _creneau_h0(db_session)
    assert (creneau.etat, creneau.fichiers, rapport.geles) == (EtatCreneau.LIBRE, None, 0)


def _l_autre(creneau, article, autre):
    # L'arbitrage choisit lui-meme quel article va sur H0.
    return autre.id if creneau.article_id == article.id else article.id


def test_un_post_d_un_autre_article_n_est_ni_gele_ni_publie(db_session, article):
    autre = _second_article(db_session, article)
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    creneau = _creneau_h0(db_session)
    with _dashboard(db_session) as dashboard:
        # Epingle : sinon l'arbitrage reattribue le creneau a l'autre article.
        dashboard.get(Creneau, creneau.id).epingle = True
        dashboard.get(Post, creneau.post_id).article_id = _l_autre(creneau, article, autre)
        dashboard.commit()
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    db_session.expire_all()
    creneau = _creneau_h0(db_session)
    assert outils.prepares == [] and creneau.etat == EtatCreneau.PROGRAMME and creneau.erreur
    assert {db_session.get(Article, a.id).file_exclu_raison for a in (article, autre)} == {None}


def test_un_post_change_apres_le_gel_n_est_pas_publie(db_session, article):
    autre = _second_article(db_session, article)
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    creneau = _creneau_h0(db_session)
    assert creneau.etat == EtatCreneau.GELE
    with _dashboard(db_session) as dashboard:
        dashboard.get(Post, creneau.post_id).article_id = _l_autre(creneau, article, autre)
        dashboard.commit()
    outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    db_session.expire_all()
    creneau = _creneau_h0(db_session)
    assert outils.publies == []
    assert creneau.etat == EtatCreneau.ECHEC and creneau.erreur
    assert {db_session.get(Article, a.id).file_exclu_raison for a in (article, autre)} == {None}


# --- Reclamation atomique et articles deja publies --------------------------------------


def test_un_creneau_reclame_par_un_autre_passage_n_est_pas_publie(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    maintenant = H0 + dt.timedelta(minutes=20)

    def horloge_puis_reclamation():
        # Un second passage concurrent reclame le creneau juste avant nous.
        with _dashboard(db_session) as autre_passage:
            creneau = _creneau(autre_passage, H0)
            if creneau.etat == EtatCreneau.GELE:
                creneau.etat = EtatCreneau.ECHEC
                creneau.erreur = "publication interrompue avant confirmation"
                autre_passage.commit()
        return maintenant

    outils.tourner(db_session, maintenant, horloge=horloge_puis_reclamation)
    assert outils.publies == []


def test_un_article_deja_publie_libere_son_creneau_epingle_sans_composer(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=30))
    creneau = next(c for c in db_session.query(Creneau).all() if c.article_id == article.id)
    # Publie a la main entre deux passages ; le post compose a disparu.
    db_session.delete(db_session.get(Post, creneau.post_id))
    creneau.post_id, creneau.epingle = None, True
    db_session.get(Article, article.id).status = ArticleStatus.USED
    db_session.commit()
    outils.composes = []
    outils.tourner(db_session, utc(creneau.publier_a) - dt.timedelta(hours=3))
    assert outils.composes == []
    assert (creneau.etat, creneau.article_id, creneau.epingle) == (EtatCreneau.LIBRE, None, False)


def test_un_post_publie_a_la_main_n_est_ni_gele_ni_publie(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    creneau = _creneau_h0(db_session)
    creneau.epingle = True
    db_session.get(Post, creneau.post_id).status = PostStatus.PUBLISHED
    db_session.commit()
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    assert (outils.prepares, outils.publies) == ([], [])
    assert creneau.article_id is None


def test_un_post_publie_a_la_main_apres_le_gel_n_est_pas_republie(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    creneau = _creneau_h0(db_session)
    db_session.get(Post, creneau.post_id).status = PostStatus.PUBLISHED
    db_session.commit()
    outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    assert outils.publies == []


# --- Horloge fraiche, dossiers figes, etapes isolees ------------------------------------


def test_un_retard_accumule_pendant_le_passage_devient_un_manque(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    maintenant = H0 + dt.timedelta(minutes=20)
    # Les publications precedentes du passage ont pris 80 minutes.
    rapport = outils.tourner(db_session, maintenant, horloge=lambda: maintenant + dt.timedelta(minutes=80))
    assert outils.publies == []
    assert (_creneau_h0(db_session).etat, rapport.manques) == (EtatCreneau.MANQUE, 1)


def _preparer_sur_disque(outils):
    def preparer(post, art, dossier):
        outils.prepares.append(post.id)
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "p_01.jpg").write_bytes(b"jpeg")
        return ["p_01.jpg"]
    return preparer


@pytest.mark.parametrize("erreur", [None, ErreurInstagram("media refuse")])
def test_le_dossier_fige_disparait_apres_la_publication(db_session, article, tmp_path, erreur):
    outils = Outils()
    outils.preparer = _preparer_sur_disque(outils)
    outils.erreur_publication = erreur
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    dossier = tmp_path / str(_creneau_h0(db_session).id)
    assert dossier.exists()
    outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    assert not dossier.exists()


def test_le_dossier_fige_disparait_pour_un_manque(db_session, article, tmp_path):
    outils = Outils()
    outils.preparer = _preparer_sur_disque(outils)
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    dossier = tmp_path / str(_creneau_h0(db_session).id)
    outils.tourner(db_session, H0 + dt.timedelta(minutes=120))
    assert not dossier.exists()


def test_un_manque_previent(db_session, article):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 + dt.timedelta(minutes=120))
    assert len(outils.notifications) == 1 and "manque" in outils.notifications[0]


def test_une_panne_inattendue_de_composition_ne_bloque_pas_un_creneau_gele(db_session, article, monkeypatch):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))
    assert _creneau_h0(db_session).etat == EtatCreneau.GELE
    _second_article(db_session, article)

    def create_post_en_panne(db, art, draft, **_):
        raise ValueError("bug inattendu")

    monkeypatch.setattr(runner, "create_post", create_post_en_panne)
    rapport = outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    assert _creneau_h0(db_session).etat == EtatCreneau.PUBLIE
    assert any("bug inattendu" in erreur for erreur in rapport.erreurs)
    assert any("bug inattendu" in message for message in outils.notifications)


def test_un_arbitrage_en_panne_ne_bloque_pas_un_creneau_gele(db_session, article, monkeypatch):
    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(hours=3))
    outils.tourner(db_session, H0 - dt.timedelta(hours=1))

    def arbitrage_en_panne(db, maintenant):
        raise RuntimeError("arbitrage casse")

    monkeypatch.setattr(runner, "appliquer_arbitrage", arbitrage_en_panne)
    rapport = outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    assert _creneau_h0(db_session).etat == EtatCreneau.PUBLIE
    assert any("arbitrage casse" in erreur for erreur in rapport.erreurs)


# --- Publication a l'heure affichee ------------------------------------------------------


class Horloge:
    """Horloge factice : `dormir` l'avance au lieu d'attendre."""

    def __init__(self, moment):
        self.moment = moment
        self.sommeils = []

    def __call__(self):
        return self.moment

    def dormir(self, secondes):
        self.sommeils.append(secondes)
        self.moment += dt.timedelta(seconds=secondes) + self.saut
        self.saut = dt.timedelta(0)

    saut = dt.timedelta(0)


class OutilsPonctuels(Outils):
    def __init__(self, horloge):
        super().__init__()
        self.horloge = horloge
        self.mode = "reel"
        self.journal = []
        self.etats_pendant_conteneur = []
        self.duree_conteneur_s = 0
        self.pendant_attente = None

    def publier(self, dossier, noms, legende, publier_vraiment, *, attendre_jusqu_a=None, encore_publier=None):
        with sessionmaker(bind=self.db.get_bind())() as autre:
            self.etats_pendant_conteneur.append(autre.get(Creneau, _creneau_h0(self.db).id).etat)
        self.journal.append("conteneur")
        self.horloge.moment += dt.timedelta(seconds=self.duree_conteneur_s)
        if not publier_vraiment:
            return "conteneur1"
        if attendre_jusqu_a is not None:
            attendre_jusqu_a()
            if self.pendant_attente:
                self.pendant_attente()
        if encore_publier is not None and not encore_publier():
            self.journal.append("abandon")
            return "conteneur1"
        self.journal.append(("media_publish", self.horloge()))
        self.publies.append((noms, publier_vraiment))
        return "ig42"

    def tourner_ponctuel(self, db, mode="reel"):
        self.db = db
        return tourner(db, self.horloge(), mode=mode, composer=self.composer, preparer=self.preparer,
                       publier=self.publier, notifier=self.notifier, horloge=self.horloge,
                       lire_mode=lambda: self.mode, dormir=self.horloge.dormir)


def _gele_h0(db, outils):
    outils.tourner(db, H0 - dt.timedelta(hours=3))
    outils.tourner(db, H0 - dt.timedelta(hours=1))
    creneau = _creneau_h0(db)
    assert creneau.etat == EtatCreneau.GELE
    return creneau


def test_un_creneau_a_cinq_minutes_part_a_l_heure_affichee(db_session, article):
    horloge = Horloge(H0 - dt.timedelta(minutes=5))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)

    rapport = outils.tourner_ponctuel(db_session)

    # Reclame (ECHEC committe) AVANT le conteneur, attente jusqu'a l'heure, puis publication.
    assert outils.etats_pendant_conteneur == [EtatCreneau.ECHEC]
    # Pas courts contre l'horloge murale : un Mac en veille pendant l'attente se voit.
    assert horloge.sommeils == [30] * 10
    assert outils.journal == ["conteneur", ("media_publish", H0)]
    assert (creneau.etat, rapport.publies) == (EtatCreneau.PUBLIE, 1)
    assert utc(creneau.publie_le) == H0
    assert db_session.get(Article, article.id).status == ArticleStatus.USED


def test_repasser_en_apercu_pendant_l_attente_ne_publie_pas(db_session, article):
    horloge = Horloge(H0 - dt.timedelta(minutes=5))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)
    outils.pendant_attente = lambda: setattr(outils, "mode", "apercu")

    rapport = outils.tourner_ponctuel(db_session)

    assert outils.journal == ["conteneur", "abandon"]
    assert (creneau.etat, creneau.publie_le, rapport.apercus, rapport.publies) == (EtatCreneau.APERCU, None, 1, 0)
    assert db_session.get(Post, creneau.post_id).status == PostStatus.DRAFT
    assert db_session.get(Article, article.id).status == ArticleStatus.NEW


def test_repasser_en_arret_pendant_l_attente_rend_le_creneau_gele(db_session, article):
    horloge = Horloge(H0 - dt.timedelta(minutes=5))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)
    outils.pendant_attente = lambda: setattr(outils, "mode", "arret")

    outils.tourner_ponctuel(db_session)

    assert outils.journal == ["conteneur", "abandon"]
    assert creneau.etat == EtatCreneau.GELE
    assert db_session.get(Article, article.id).file_exclu_raison is None

    # Rien n'a ete publie : repasse en reel a temps, il part, une seule fois.
    outils.pendant_attente = None
    outils.mode = "reel"
    horloge.moment = H0 + dt.timedelta(minutes=10)
    outils.tourner_ponctuel(db_session)
    outils.tourner_ponctuel(db_session)
    assert creneau.etat == EtatCreneau.PUBLIE
    assert len(outils.publies) == 1


def test_un_conteneur_fini_apres_l_heure_publie_sans_attendre(db_session, article):
    horloge = Horloge(H0 - dt.timedelta(minutes=5))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)
    outils.duree_conteneur_s = 400

    outils.tourner_ponctuel(db_session)

    assert horloge.sommeils == []
    assert outils.journal == ["conteneur", ("media_publish", H0 + dt.timedelta(seconds=100))]
    assert creneau.etat == EtatCreneau.PUBLIE
    assert utc(creneau.publie_le) == H0 + dt.timedelta(seconds=100)


def test_un_creneau_au_dela_de_la_fenetre_d_avance_n_est_pas_touche(db_session, article):
    horloge = Horloge(H0 - dt.timedelta(seconds=settings.publication_avance_s + 60))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)

    outils.tourner_ponctuel(db_session)

    assert (outils.journal, horloge.sommeils) == ([], [])
    assert creneau.etat == EtatCreneau.GELE


def test_en_apercu_un_creneau_a_venir_attend_son_heure(db_session, article):
    horloge = Horloge(H0 - dt.timedelta(minutes=5))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)
    outils.mode = "apercu"

    outils.tourner_ponctuel(db_session, mode="apercu")
    assert (outils.journal, creneau.etat) == ([], EtatCreneau.GELE)

    horloge.moment = H0 + dt.timedelta(minutes=1)
    outils.tourner_ponctuel(db_session, mode="apercu")
    assert (outils.journal, creneau.etat) == (["conteneur"], EtatCreneau.APERCU)


def test_l_attente_ne_depasse_jamais_l_avance(db_session, article, monkeypatch):
    monkeypatch.setattr(settings, "publication_avance_s", 120)
    horloge = Horloge(H0 - dt.timedelta(seconds=100))
    outils = OutilsPonctuels(horloge)
    _gele_h0(db_session, outils)
    # Le conteneur "remonte le temps" (horloge ajustee) : l'attente reste bornee.
    outils.duree_conteneur_s = -3600

    outils.tourner_ponctuel(db_session)

    assert sum(horloge.sommeils) == 120 and max(horloge.sommeils) <= 30


def test_un_mac_endormi_pendant_l_attente_donne_un_manque_sans_publication(tmp_path, db_session, article):
    horloge = Horloge(H0 - dt.timedelta(minutes=5))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)
    _dossier_de(creneau).mkdir(parents=True, exist_ok=True)
    # Le Mac s'endort pendant l'attente et se reveille trois heures plus tard.
    horloge.saut = dt.timedelta(hours=3)

    rapport = outils.tourner_ponctuel(db_session)

    assert outils.journal == ["conteneur", "abandon"]
    assert (creneau.etat, rapport.manques, rapport.publies) == (EtatCreneau.MANQUE, 1, 0)
    assert db_session.get(Article, article.id).file_exclu_raison is None
    assert not _dossier_de(creneau).exists()
    assert outils.notifications


def test_un_creneau_du_relit_mode_et_retard_juste_avant_media_publish(db_session, article):
    """Hors attente aussi : des enfants video peuvent retenir le conteneur longtemps."""
    horloge = Horloge(H0 + dt.timedelta(minutes=5))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)
    outils.duree_conteneur_s = 100 * 60

    outils.tourner_ponctuel(db_session)

    assert outils.journal == ["conteneur", "abandon"]
    assert creneau.etat == EtatCreneau.MANQUE


def test_un_creneau_du_repasse_en_apercu_pendant_le_conteneur_ne_publie_pas(db_session, article):
    horloge = Horloge(H0 + dt.timedelta(minutes=5))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)
    outils.duree_conteneur_s = 1
    original = outils.publier

    def publier(*args, **kwargs):
        outils.mode = "apercu"
        return original(*args, **kwargs)

    outils.publier = publier
    outils.tourner_ponctuel(db_session)
    assert outils.publies == []
    assert creneau.etat == EtatCreneau.APERCU


def test_le_retour_en_gele_ne_passe_pas_sur_un_changement_du_dashboard(db_session, article):
    horloge = Horloge(H0 - dt.timedelta(minutes=5))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)

    def arret_et_dashboard():
        outils.mode = "arret"
        # Le dashboard libere le creneau pendant l'attente : son choix prime.
        with _dashboard(db_session) as dashboard:
            libere = _creneau(dashboard, H0)
            libere.etat, libere.article_id, libere.post_id = EtatCreneau.LIBRE, None, None
            dashboard.commit()

    outils.pendant_attente = arret_et_dashboard
    outils.tourner_ponctuel(db_session)

    db_session.expire_all()
    assert (creneau.etat, creneau.post_id) == (EtatCreneau.LIBRE, None)
    assert outils.publies == []


def _dossier_de(creneau):
    return settings.publication_dir / str(creneau.id)


# --- La session pendant l'attente et l'appel a Meta ---------------------------------------


class OutilsSansTransaction(OutilsPonctuels):
    """Publieur qui note si la session de l'agent a une transaction ouverte.

    Aucune requete de son cote (contrairement a `OutilsPonctuels.publier`, qui
    interroge la base pendant le conteneur) : ce qui est observe est donc bien
    l'etat laisse par le runner.
    """

    def __init__(self, horloge):
        super().__init__(horloge)
        self.transactions = []

    def publier(self, dossier, noms, legende, publier_vraiment, *, attendre_jusqu_a=None,
                encore_publier=None):
        self.transactions.append(("conteneur", self.db.in_transaction()))
        if attendre_jusqu_a is not None:
            attendre_jusqu_a()
        if encore_publier is not None and not encore_publier():
            return "conteneur1"
        self.publies.append((noms, publier_vraiment))
        return "ig42"

    def dormir(self, secondes):
        self.transactions.append(("attente", self.db.in_transaction()))
        self.horloge.dormir(secondes)

    def tourner_ponctuel(self, db, mode="reel"):
        self.db = db
        return tourner(db, self.horloge(), mode=mode, composer=self.composer, preparer=self.preparer,
                       publier=self.publier, notifier=self.notifier, horloge=self.horloge,
                       lire_mode=lambda: self.mode, dormir=self.dormir)


def test_aucune_transaction_n_est_ouverte_pendant_l_attente_ni_l_appel_a_meta(db_session, article):
    """Neon coupe une session restee « idle in transaction » : l'ecriture d'apres
    publication echouait alors, et le creneau restait ECHEC alors que le reel
    etait bel et bien en ligne (17/09, article 17974)."""
    horloge = Horloge(H0 - dt.timedelta(minutes=5))
    outils = OutilsSansTransaction(horloge)
    creneau = _gele_h0(db_session, outils)

    outils.tourner_ponctuel(db_session)

    assert outils.transactions, "le publieur n'a pas ete appele"
    assert all(ouverte is False for _, ouverte in outils.transactions), outils.transactions
    assert creneau.etat == EtatCreneau.PUBLIE


def test_une_connexion_coupee_pendant_la_publication_est_rattrapee(db_session, article, monkeypatch):
    """Le reel est parti : la base doit le dire, meme si la connexion est morte
    entre-temps. Un post publie enregistre en ECHEC est le pire des etats."""
    horloge = Horloge(H0 - dt.timedelta(minutes=5))
    outils = OutilsPonctuels(horloge)
    creneau = _gele_h0(db_session, outils)
    vrai_commit = db_session.commit
    etat = {"publie": False, "coupures": 0}

    def publier(dossier, noms, legende, publier_vraiment, **crochets):
        identifiant = OutilsPonctuels.publier(outils, dossier, noms, legende, publier_vraiment, **crochets)
        etat["publie"] = True
        return identifiant

    def commit_capricieux():
        # La premiere ecriture d'APRES publication tombe sur la connexion coupee.
        if etat["publie"] and not etat["coupures"]:
            etat["coupures"] += 1
            raise OperationalError(
                "UPDATE articles SET status=%(status)s WHERE articles.id = %(id)s",
                {},
                Exception("terminating connection due to idle-in-transaction timeout"),
            )
        vrai_commit()

    outils.publier = publier
    monkeypatch.setattr(db_session, "commit", commit_capricieux)

    rapport = outils.tourner_ponctuel(db_session)

    monkeypatch.undo()
    db_session.expire_all()
    assert etat["coupures"] == 1
    assert (creneau.etat, creneau.ig_media_id) == (EtatCreneau.PUBLIE, "ig42")
    assert db_session.get(Article, article.id).status == ArticleStatus.USED
    assert db_session.get(Article, article.id).file_exclu_raison is None
    assert db_session.get(Post, creneau.post_id).status == PostStatus.PUBLISHED
    # Une seule publication, comptee une fois : la reprise ne touche que la base.
    assert (rapport.publies, len(outils.publies)) == (1, 1)
    assert not rapport.erreurs


def test_un_tunnel_injoignable_ne_exclut_pas_l_article_et_rend_le_creneau_gele(db_session, article, tmp_path):
    """Un tunnel injoignable est une panne machine : rien n'est parti chez Meta
    (le conteneur n'est cree qu'une fois le tunnel debout), les fichiers figes
    restent valides, le creneau redevient GELE et le passage suivant retente."""
    outils = Outils()
    outils.erreur_publication = TunnelInjoignable("tunnel ouvert mais https://t/c injoignable apres 90 s")
    _jusqu_a_publication(outils, db_session, H0)
    creneau = _creneau_h0(db_session)
    dossier = tmp_path / str(creneau.id)
    dossier.mkdir(exist_ok=True)
    (dossier / "p_01.jpg").write_bytes(b"x")

    assert db_session.get(Article, article.id).file_exclu_raison is None
    assert creneau.etat == EtatCreneau.GELE
    assert "injoignable" in creneau.erreur
    assert creneau.article_id == article.id and creneau.post_id is not None
    assert len(outils.notifications) == 1

    outils.erreur_publication = None
    outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    creneau = _creneau_h0(db_session)
    assert (creneau.etat, creneau.ig_media_id, creneau.erreur) == (EtatCreneau.PUBLIE, "ig42", None)
    assert db_session.get(Article, article.id).status == ArticleStatus.USED
    assert len(outils.publies) == 2
    assert not dossier.exists()


def test_un_tunnel_injoignable_trop_tard_finit_en_manque(db_session, article):
    """Au-dela du retard maximal, le creneau GELE rendu par la panne de tunnel
    suit la regle commune : MANQUE, jamais une publication du soir pour le matin."""
    outils = Outils()
    outils.erreur_publication = TunnelInjoignable("injoignable apres 90 s")
    _jusqu_a_publication(outils, db_session, H0)
    assert _creneau_h0(db_session).etat == EtatCreneau.GELE
    outils.tourner(db_session, H0 + dt.timedelta(minutes=120))
    assert _creneau_h0(db_session).etat == EtatCreneau.MANQUE
    assert db_session.get(Article, article.id).file_exclu_raison is None
