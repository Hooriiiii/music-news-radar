"""Le passage de l'agent avec des creneaux reel, sur des outils factices."""

import datetime as dt

import pytest

from app.config import settings
from app.content.compose import CoverVariante, PostDraft
from app.content.media import entree
from app.content.video import AssemblageIndisponible
from app.models import (
    Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau, Post, Source, SourceCategorie, SourceType,
)
from app.publish.preparation import PreparationDifferee
from app.publish.ytdlp import MISE_A_JOUR, YtdlpEnAttente, YtdlpIndisponible
import app.schedule.runner as runner
from app.schedule.runner import tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import utc

# Reels en opt-in : ce module exerce la chaine reel, il les allume.
pytestmark = pytest.mark.usefixtures("reels_allumes")

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
        # Erreurs consommees une par appel, avant `erreur_preparation_reel`.
        self.erreurs_preparation_reel = []

    def composer(self, article):
        self.composes.append(article.id)
        return _draft()

    def preparer(self, post, article, dossier):
        self.prepares.append(post.id)
        return ["p_01.jpg"]

    def preparer_reel(self, post, article, dossier):
        self.prepares_reel.append(post.id)
        if self.erreurs_preparation_reel:
            erreur = self.erreurs_preparation_reel.pop(0)
            if erreur is not None:
                raise erreur
            return ["r_reel.mp4", "r_cover.jpg"]
        if self.erreur_preparation_reel:
            raise self.erreur_preparation_reel
        return ["r_reel.mp4", "r_cover.jpg"]

    def publier(self, dossier, noms, legende, publier_vraiment, **_crochets):
        self.publies.append((noms, publier_vraiment))
        return "ig42"

    def publier_reel(self, dossier, noms, legende, publier_vraiment, **_crochets):
        self.publies_reel.append((noms, publier_vraiment))
        self.legendes.append(legende)
        return "ig-reel"

    def notifier(self, titre, message):
        self.notifications.append(message)

    def tourner(self, db, maintenant, mode="reel", avec_reels=True, lire_mode=None):
        return tourner(
            db, maintenant, mode=mode, composer=self.composer, preparer=self.preparer,
            publier=self.publier, notifier=self.notifier, horloge=lambda: maintenant,
            preparer_reel=self.preparer_reel if avec_reels else None,
            publier_reel=self.publier_reel if avec_reels else None,
            lire_mode=lire_mode,
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


def test_un_post_au_mauvais_gabarit_est_detache_puis_recompose(db_session, video):
    """Le switch POST/REELS bascule au dashboard : le creneau lache le post, sans
    exclure l'article, et le passage suivant compose au bon gabarit."""
    mauvais = _post(db_session, video, "kinda_post", "@saralandry")
    reel = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.REEL, video, mauvais)
    outils = Outils()
    outils.tourner(db_session, BASE)
    assert (reel.etat, reel.post_id, reel.article_id) == (EtatCreneau.PROGRAMME, None, video.id)
    assert "gabarit" in reel.erreur
    assert outils.prepares_reel == []
    db_session.refresh(video)
    assert video.file_exclu_raison is None

    outils.tourner(db_session, BASE + dt.timedelta(minutes=10))
    assert outils.composes == [video.id]
    assert db_session.get(Post, reel.post_id).template == "kinda_reel"


def test_un_brouillon_carrousel_n_est_pas_repris_sur_un_creneau_reel(db_session, video):
    _post(db_session, video, "kinda_post", "@saralandry")
    reel = Creneau(publier_a=BASE + dt.timedelta(hours=5), format=FormatCreneau.REEL, article_id=video.id,
                   etat=EtatCreneau.PROGRAMME, epingle=True)
    db_session.add(reel)
    db_session.commit()
    outils = Outils()
    outils.tourner(db_session, BASE)
    assert outils.composes == [video.id]
    assert db_session.get(Post, reel.post_id).template == "kinda_reel"


def test_un_article_inapte_au_reel_libere_le_creneau_sans_composer_de_reel(db_session):
    photo = _photo(db_session)
    reel = Creneau(publier_a=BASE + dt.timedelta(hours=5), format=FormatCreneau.REEL, article_id=photo.id,
                   etat=EtatCreneau.PROGRAMME, epingle=True)
    db_session.add(reel)
    db_session.commit()
    Outils().tourner(db_session, BASE)
    assert (reel.etat, reel.article_id, reel.post_id) == (EtatCreneau.LIBRE, None, None)
    assert "reel impossible" in reel.erreur
    assert db_session.query(Post).filter(Post.template == "kinda_reel").count() == 0
    db_session.refresh(photo)
    assert photo.file_exclu_raison is None


def test_une_attente_sur_un_reel_n_empeche_pas_le_gel_du_suivant(db_session, video):
    premier = _epingle(db_session, BASE + dt.timedelta(minutes=30), FormatCreneau.REEL, video,
                       _post(db_session, video, "kinda_reel", "@saralandry"))
    second = _epingle(db_session, BASE + dt.timedelta(minutes=45), FormatCreneau.REEL, video,
                      _post(db_session, video, "kinda_reel", "@saralandry"))
    outils = Outils()
    outils.erreurs_preparation_reel = [PreparationDifferee("assemblage abandonne : delai depasse"), None]
    outils.tourner(db_session, BASE)
    assert premier.etat == EtatCreneau.PROGRAMME and "differe" in premier.erreur
    assert second.etat == EtatCreneau.GELE
    assert outils.notifications == []


def test_sans_outils_reel_aucun_reel_n_est_compose(db_session, video):
    """Reels coupes : pas d'appel Claude paye pour un reel qui ne partira pas."""
    reel = Creneau(publier_a=BASE + dt.timedelta(hours=5), format=FormatCreneau.REEL, article_id=video.id,
                   etat=EtatCreneau.PROGRAMME, epingle=True)
    db_session.add(reel)
    db_session.commit()
    outils = Outils()
    outils.tourner(db_session, BASE, avec_reels=False)
    assert outils.composes == []
    assert (reel.etat, reel.post_id, reel.article_id) == (EtatCreneau.PROGRAMME, None, video.id)


def test_le_menage_garde_un_creneau_programme_a_venir(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    avenir = _epingle(db_session, BASE + dt.timedelta(hours=30), FormatCreneau.REEL, video, post)
    passe = _epingle(db_session, BASE - dt.timedelta(minutes=5), FormatCreneau.REEL, video, post)
    for creneau in (avenir, passe):
        (settings.publication_dir / str(creneau.id)).mkdir(parents=True)
    runner._menage(db_session, BASE)
    restants = sorted(p.name for p in settings.publication_dir.iterdir())
    assert restants == [str(avenir.id)]


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


# --- Vague de correction finale ---------------------------------------------------------


def _changer_de_mode_pendant_le_gel(outils, nouveau):
    """Le mode lu par le passage change des que le gel a eu lieu : c'est le
    dashboard qui passe d'un mode a l'autre pendant un long passage."""
    modes = ["reel"]
    preparer = outils.preparer_reel

    def preparer_puis_changer(post, article, dossier):
        noms = preparer(post, article, dossier)
        modes.append(nouveau)
        return noms

    outils.preparer_reel = preparer_puis_changer
    return lambda: modes[-1]


def test_repasser_en_apercu_pendant_le_passage_ne_publie_rien(db_session, video):
    """I1 : le passage demarre en reel, l'utilisateur repasse en apercu entre le
    gel et la publication : media_publish ne doit jamais partir."""
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    creneau = _epingle(db_session, BASE, FormatCreneau.REEL, video, post)
    outils = Outils()
    lire_mode = _changer_de_mode_pendant_le_gel(outils, "apercu")
    rapport = outils.tourner(db_session, BASE, mode="reel", lire_mode=lire_mode)
    assert outils.publies_reel == [(["r_reel.mp4", "r_cover.jpg"], False)]
    assert (creneau.etat, rapport.publies, rapport.apercus) == (EtatCreneau.APERCU, 0, 1)
    db_session.refresh(video)
    assert video.status != ArticleStatus.USED


def test_passer_en_arret_pendant_le_passage_ne_reclame_rien(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    creneau = _epingle(db_session, BASE, FormatCreneau.REEL, video, post)
    outils = Outils()
    lire_mode = _changer_de_mode_pendant_le_gel(outils, "arret")
    outils.tourner(db_session, BASE, mode="reel", lire_mode=lire_mode)
    assert outils.publies_reel == []
    assert creneau.etat == EtatCreneau.GELE
    db_session.refresh(video)
    assert video.file_exclu_raison is None


def test_un_mode_illisible_pendant_le_passage_ne_reclame_rien(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    creneau = _epingle(db_session, BASE - dt.timedelta(minutes=5), FormatCreneau.REEL, video, post,
                       etat=EtatCreneau.GELE, fichiers=["r_reel.mp4", "r_cover.jpg"])

    def en_panne():
        raise OSError("disque")

    outils = Outils()
    outils.tourner(db_session, BASE, mode="reel", lire_mode=en_panne)
    assert (outils.publies_reel, creneau.etat) == ([], EtatCreneau.GELE)


def test_le_menage_garde_un_creneau_gele_dont_l_heure_est_passee(db_session, video):
    """I2 : GELE et publier_a <= maintenant (dans le delai de retard) : ses
    fichiers sont ceux qui vont partir, le menage n'y touche pas."""
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    gele = _epingle(db_session, BASE - dt.timedelta(minutes=5), FormatCreneau.REEL, video, post,
                    etat=EtatCreneau.GELE, fichiers=["r_reel.mp4", "r_cover.jpg"])
    (settings.publication_dir / str(gele.id)).mkdir(parents=True)
    runner._menage(db_session, BASE)
    assert [p.name for p in settings.publication_dir.iterdir()] == [str(gele.id)]


def test_un_brouillon_reel_sans_credit_est_complete_a_la_reprise(db_session, video):
    """M1 : un brouillon kinda_reel d'avant le credit n'exclut pas l'article."""
    brouillon = _post(db_session, video, "kinda_reel", credit=None)
    reel = Creneau(publier_a=BASE + dt.timedelta(hours=5), format=FormatCreneau.REEL, article_id=video.id,
                   etat=EtatCreneau.PROGRAMME, epingle=True)
    db_session.add(reel)
    db_session.commit()
    outils = Outils()
    outils.tourner(db_session, BASE)
    assert (outils.composes, reel.post_id) == ([], brouillon.id)
    db_session.refresh(brouillon)
    assert brouillon.credit == "@saralandry"


def test_un_brouillon_reel_sans_credit_deja_accroche_est_complete_au_gel(db_session, video):
    brouillon = _post(db_session, video, "kinda_reel", credit=None)
    creneau = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.REEL, video, brouillon)
    credits = []
    outils = Outils()
    preparer = outils.preparer_reel

    def preparer_en_notant(post, article, dossier):
        credits.append(post.credit)
        return preparer(post, article, dossier)

    outils.preparer_reel = preparer_en_notant
    outils.tourner(db_session, BASE)
    assert (credits, creneau.etat) == (["@saralandry"], EtatCreneau.GELE)
    db_session.refresh(video)
    assert video.file_exclu_raison is None


def test_un_article_devenu_inapte_au_reel_libere_le_creneau_au_gel(db_session, video):
    """M3 : l'article a perdu sa video (ou sa source a change de categorie) entre
    la composition et le gel : on libere, sans exclure."""
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    creneau = _epingle(db_session, BASE + dt.timedelta(hours=1), FormatCreneau.REEL, video, post)
    video.source.categorie = SourceCategorie.MEDIA
    db_session.commit()
    outils = Outils()
    outils.tourner(db_session, BASE)
    assert outils.prepares_reel == []
    assert (creneau.etat, creneau.article_id, creneau.post_id) == (EtatCreneau.LIBRE, None, None)
    assert "reel impossible" in creneau.erreur
    db_session.refresh(video)
    assert video.file_exclu_raison is None


def test_un_reel_gele_manque_parce_que_les_reels_sont_coupes_le_dit(db_session, video):
    """M6 : pas de « Mac en veille ? » quand c'est la case Reels auto qui a coupe."""
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    _epingle(db_session, BASE - dt.timedelta(hours=2), FormatCreneau.REEL, video, post,
             etat=EtatCreneau.GELE, fichiers=["r_reel.mp4", "r_cover.jpg"])
    outils = Outils()
    rapport = outils.tourner(db_session, BASE, avec_reels=False)
    assert rapport.manques == 1
    (message,) = [n for n in outils.notifications if "manque" in n]
    assert "reels coupés" in message
    assert "veille" not in message


def test_un_reel_manque_reels_actifs_garde_le_message_de_veille(db_session, video):
    post = _post(db_session, video, "kinda_reel", "@saralandry")
    _epingle(db_session, BASE - dt.timedelta(hours=2), FormatCreneau.REEL, video, post,
             etat=EtatCreneau.GELE, fichiers=["r_reel.mp4", "r_cover.jpg"])
    outils = Outils()
    outils.tourner(db_session, BASE)
    (message,) = [n for n in outils.notifications if "manque" in n]
    assert "veille" in message and "reels coupés" not in message
