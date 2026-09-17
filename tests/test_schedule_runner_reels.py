"""Le passage de l'agent avec des creneaux reel, sur des outils factices."""

import datetime as dt

import pytest

from app.config import settings
from app.content.compose import CoverVariante, PostDraft
from app.content.media import entree
from app.content.video import AssemblageIndisponible
from app.models import (
    Article, Creneau, EtatCreneau, FormatCreneau, Post, Source, SourceCategorie, SourceType,
)
from app.publish.ytdlp import MISE_A_JOUR, YtdlpEnAttente, YtdlpIndisponible
from app.schedule.runner import tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import utc

BASE = dt.datetime(2026, 9, 20, 4, 0, tzinfo=dt.timezone.utc)
(R0,) = prochains_horaires(BASE, 1, "reel")


def _draft():
    variante = CoverVariante(headline="**Accroche**", title="**Titre**", subtitle="**Sous**")
    return PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=["#techno"])


class Outils:
    def __init__(self):
        self.composes, self.prepares, self.prepares_reel = [], [], []
        self.publies, self.publies_reel, self.legendes, self.notifications = [], [], [], []
        self.erreur_preparation_reel = None

    def composer(self, article):
        self.composes.append(article.id)
        return _draft()

    def preparer(self, post, article, dossier):
        self.prepares.append(post.id)
        return ["p_01.jpg"]

    def preparer_reel(self, post, article, dossier):
        self.prepares_reel.append(post.id)
        if self.erreur_preparation_reel:
            raise self.erreur_preparation_reel
        return ["r_reel.mp4", "r_cover.jpg"]

    def publier(self, dossier, noms, legende, publier_vraiment):
        self.publies.append((noms, publier_vraiment))
        return "ig42"

    def publier_reel(self, dossier, noms, legende, publier_vraiment):
        self.publies_reel.append((noms, publier_vraiment))
        self.legendes.append(legende)
        return "ig-reel"

    def notifier(self, titre, message):
        self.notifications.append(message)

    def tourner(self, db, maintenant, mode="reel", avec_reels=True):
        return tourner(
            db, maintenant, mode=mode, composer=self.composer, preparer=self.preparer,
            publier=self.publier, notifier=self.notifier, horloge=lambda: maintenant,
            preparer_reel=self.preparer_reel if avec_reels else None,
            publier_reel=self.publier_reel if avec_reels else None,
        )


@pytest.fixture()
def video(db_session):
    source = Source(name="Sara Landry", type=SourceType.INSTAGRAM, url="ig:saralandry",
                    categorie=SourceCategorie.ARTISTE)
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://www.instagram.com/p/AAA/", title="Le live",
                      raw_hash="v1", relevance_score=90, hotness_score=40,
                      published_at=BASE - dt.timedelta(hours=1),
                      media=[{"type": "video", "url": "https://cdn/v.mp4", "preview_url": "https://cdn/v.jpg"}])
    db_session.add(article)
    db_session.commit()
    return article


def _photo(db):
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed", categorie=SourceCategorie.MEDIA)
    db.add(source)
    db.commit()
    article = Article(source_id=source.id, url="https://t/1", title="Actu", raw_hash="p1", relevance_score=80,
                      hotness_score=20, published_at=BASE - dt.timedelta(hours=1),
                      media=[{"type": "photo", "url": "https://img/1.jpg"}])
    db.add(article)
    db.commit()
    return article


def _post(db, article, template, credit=None):
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                caption="Legende", slides=[{"kind": "cover"}], template=template, credit=credit)
    db.add(post)
    db.commit()
    return post


def _epingle(db, heure, format_, article, post, etat=EtatCreneau.PROGRAMME, fichiers=None):
    creneau = Creneau(publier_a=heure, format=format_, article_id=article.id, post_id=post.id,
                      etat=etat, epingle=True, fichiers=fichiers)
    db.add(creneau)
    db.commit()
    return creneau


def test_la_chaine_complete_d_un_reel(db_session, video):
    outils = Outils()
    outils.tourner(db_session, R0 - dt.timedelta(hours=3))
    creneau = next(c for c in db_session.query(Creneau).all() if utc(c.publier_a) == R0)
    assert (creneau.format, creneau.article_id) == (FormatCreneau.REEL, video.id)
    post = db_session.get(Post, creneau.post_id)
    assert (post.template, post.credit) == ("kinda_reel", "@saralandry")
    carrousels = [c for c in db_session.query(Creneau).all() if c.format == FormatCreneau.CARROUSEL]
    assert all(c.article_id != video.id for c in carrousels)

    outils.tourner(db_session, R0 - dt.timedelta(hours=1))
    assert (creneau.etat, creneau.fichiers) == (EtatCreneau.GELE, ["r_reel.mp4", "r_cover.jpg"])
    assert (outils.prepares_reel, outils.prepares) == ([post.id], [])

    rapport = outils.tourner(db_session, R0 + dt.timedelta(minutes=20))
    assert outils.publies_reel == [(["r_reel.mp4", "r_cover.jpg"], True)]
    assert (creneau.etat, creneau.ig_media_id, rapport.publies) == (EtatCreneau.PUBLIE, "ig-reel", 1)
    assert "🎥 via @saralandry" in outils.legendes[0]


def test_un_reel_en_apercu_n_est_pas_publie(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    creneau = _epingle(db_session, BASE - dt.timedelta(minutes=10), FormatCreneau.REEL, video, post,
                       etat=EtatCreneau.GELE, fichiers=["r_reel.mp4", "r_cover.jpg"])
    outils = Outils()
    outils.tourner(db_session, BASE, mode="apercu")
    assert outils.publies_reel == [(["r_reel.mp4", "r_cover.jpg"], False)]
    assert creneau.etat == EtatCreneau.APERCU


def test_ffmpeg_absent_n_exclut_rien_et_n_empeche_pas_le_gel_des_carrousels(db_session, video):
    photo = _photo(db_session)
    carrousel = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.CARROUSEL, photo,
                         _post(db_session, photo, "kinda_post"))
    reel = _epingle(db_session, BASE + dt.timedelta(hours=1, minutes=30), FormatCreneau.REEL, video,
                    _post(db_session, video, "kinda_reel", "@saralandry"))
    outils = Outils()
    outils.erreur_preparation_reel = AssemblageIndisponible("ffmpeg est introuvable")
    outils.tourner(db_session, BASE)
    assert carrousel.etat == EtatCreneau.GELE
    assert reel.etat == EtatCreneau.PROGRAMME and "ffmpeg" in reel.erreur
    db_session.refresh(video)
    assert video.file_exclu_raison is None
    assert len(outils.notifications) == 1


def test_un_reel_d_artiste_sans_media_url_est_programme_en_reel(db_session):
    """Le cas qui justifie yt-dlp : Meta ne donne pas la video, le reel part quand meme."""
    source = Source(name="Anyma", type=SourceType.INSTAGRAM, url="ig:anyma", categorie=SourceCategorie.ARTISTE)
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://www.instagram.com/reel/ZZZ/", title="Le show",
                      raw_hash="z1", relevance_score=90, hotness_score=40,
                      published_at=BASE - dt.timedelta(hours=1),
                      media=[entree("video", None, preview_url="https://cdn/z.jpg")])
    db_session.add(article)
    db_session.commit()
    Outils().tourner(db_session, R0 - dt.timedelta(hours=3))
    creneau = next(c for c in db_session.query(Creneau).all() if utc(c.publier_a) == R0)
    assert (creneau.format, creneau.article_id) == (FormatCreneau.REEL, article.id)
    post = db_session.get(Post, creneau.post_id)
    assert (post.template, post.credit) == ("kinda_reel", "@anyma")


def test_une_attente_yt_dlp_ne_notifie_ni_n_exclut_ni_ne_bloque(db_session, video):
    """Pause ou budget : une raison sur le creneau, et c'est tout -- 6 passages
    par heure ne doivent pas donner 6 notifications."""
    photo = _photo(db_session)
    carrousel = _epingle(db_session, BASE + dt.timedelta(hours=1, minutes=30), FormatCreneau.CARROUSEL, photo,
                         _post(db_session, photo, "kinda_post"))
    reel = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.REEL, video,
                    _post(db_session, video, "kinda_reel", "@saralandry"))
    outils = Outils()
    outils.erreur_preparation_reel = YtdlpEnAttente("yt-dlp en pause jusqu'a 11:00 UTC apres une panne")
    outils.tourner(db_session, BASE)
    outils.tourner(db_session, BASE + dt.timedelta(minutes=10))
    assert reel.etat == EtatCreneau.PROGRAMME and "yt-dlp en pause" in reel.erreur
    assert carrousel.etat == EtatCreneau.GELE
    assert outils.notifications == []
    db_session.refresh(video)
    assert video.file_exclu_raison is None


def test_une_panne_yt_dlp_notifie_la_commande_de_mise_a_jour(db_session, video):
    reel = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.REEL, video,
                    _post(db_session, video, "kinda_reel", "@saralandry"))
    outils = Outils()
    outils.erreur_preparation_reel = YtdlpIndisponible(
        f"yt-dlp en panne (login required) : pause de 60 minutes. Si ca persiste, mettre yt-dlp a jour : {MISE_A_JOUR}"
    )
    outils.tourner(db_session, BASE)
    assert reel.etat == EtatCreneau.PROGRAMME
    assert len(outils.notifications) == 1 and MISE_A_JOUR in outils.notifications[0]
    db_session.refresh(video)
    assert video.file_exclu_raison is None


def test_un_post_au_mauvais_gabarit_n_est_pas_gele_ni_exclu(db_session, video):
    reel = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.REEL, video,
                    _post(db_session, video, "kinda_post", "@saralandry"))
    outils = Outils()
    outils.tourner(db_session, BASE)
    assert reel.etat == EtatCreneau.PROGRAMME and "gabarit" in reel.erreur
    assert outils.prepares_reel == []
    db_session.refresh(video)
    assert video.file_exclu_raison is None


def test_sans_outils_reel_un_reel_gele_attend_sans_planter(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    creneau = _epingle(db_session, BASE - dt.timedelta(minutes=10), FormatCreneau.REEL, video, post,
                       etat=EtatCreneau.GELE, fichiers=["r_reel.mp4", "r_cover.jpg"])
    outils = Outils()
    outils.tourner(db_session, BASE, avec_reels=False)
    assert creneau.etat == EtatCreneau.GELE and "publieur" in creneau.erreur


def test_le_menage_efface_les_dossiers_des_creneaux_termines(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    termine = _epingle(db_session, BASE - dt.timedelta(hours=5), FormatCreneau.REEL, video, post,
                       etat=EtatCreneau.ECHEC)
    gele = _epingle(db_session, BASE + dt.timedelta(hours=5), FormatCreneau.REEL, video, post,
                    etat=EtatCreneau.GELE, fichiers=["r_reel.mp4", "r_cover.jpg"])
    for dossier in (str(termine.id), str(gele.id), "essais", "999999"):
        (settings.publication_dir / dossier).mkdir(parents=True)
    Outils().tourner(db_session, BASE, mode="apercu")
    restants = sorted(p.name for p in settings.publication_dir.iterdir())
    assert restants == sorted([str(gele.id), "essais"])


def test_une_panne_systemique_reel_suspend_les_reels_seulement(db_session, video):
    """Reel en panne AVANT le carrousel dans la fenetre : le second reel n'est pas
    tente (panne de machine, inutile d'insister), le carrousel suivant gele."""
    photo = _photo(db_session)
    reel = _epingle(db_session, BASE + dt.timedelta(minutes=30), FormatCreneau.REEL, video,
                    _post(db_session, video, "kinda_reel", "@saralandry"))
    autre_reel = _epingle(db_session, BASE + dt.timedelta(minutes=45), FormatCreneau.REEL, video,
                          _post(db_session, video, "kinda_reel", "@saralandry"))
    carrousel = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.CARROUSEL, photo,
                         _post(db_session, photo, "kinda_post"))
    outils = Outils()
    outils.erreur_preparation_reel = AssemblageIndisponible("ffmpeg est introuvable")
    outils.tourner(db_session, BASE)
    assert len(outils.prepares_reel) == 1
    assert (reel.etat, autre_reel.etat) == (EtatCreneau.PROGRAMME, EtatCreneau.PROGRAMME)
    assert carrousel.etat == EtatCreneau.GELE
    assert len(outils.notifications) == 1


def test_un_reel_gele_n_est_jamais_regele(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    creneau = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.REEL, video, post)
    outils = Outils()
    outils.tourner(db_session, BASE)
    outils.tourner(db_session, BASE + dt.timedelta(minutes=10))
    outils.tourner(db_session, BASE + dt.timedelta(minutes=20))
    assert creneau.etat == EtatCreneau.GELE
    assert outils.prepares_reel == [post.id]
