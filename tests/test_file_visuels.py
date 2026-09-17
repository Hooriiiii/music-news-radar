"""Sans visuel autorise, un article reste au dashboard mais n'entre pas dans la
file ; le choix des visuels precede la composition payee."""

import datetime as dt
import itertools
import os

import pytest

import scripts.run_publication as script
from app.config import settings
from app.content.choix_visuels import ChoixVisuels, VisuelsDifferes
from app.content.compose import CoverVariante, PostDraft
from app.content.media import entree
from app.content.visuels import actif, chemin_cache
from app.models import (
    Article,
    Creneau,
    EtatCreneau,
    FormatCreneau,
    Post,
    Source,
    SourceCategorie,
    SourceType,
)
from app.schedule.runner import _menage, tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import (
    actifs_interdits,
    candidats_eligibles,
    raison_ineligible_reel,
    raison_sans_visuel,
)

UTC = dt.timezone.utc
BASE = dt.datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
H0 = prochains_horaires(BASE, 2)[0]
_numeros = itertools.count()


@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)


def _source(db, nom, handle, categorie):
    source = Source(name=nom, type=SourceType.INSTAGRAM, url=f"ig:{handle}", categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre="GTA VI soundtrack : Fred again.. au generique", media=None,
             mentioned=None, url=None, heures=2):
    n = next(_numeros)
    article = Article(
        source_id=source.id, url=url or f"https://www.instagram.com/p/F{n}/", title=titre,
        raw_hash=f"f{n:063d}", relevance_score=85, hotness_score=30,
        published_at=BASE - dt.timedelta(hours=heures), media=media, mentioned_artists=mentioned,
    )
    db.add(article)
    db.commit()
    return article


@pytest.fixture()
def fred(db_session):
    return _source(db_session, "Fred again.. (IG)", "fredagain", SourceCategorie.ARTISTE)


@pytest.fixture()
def guettapen(db_session):
    return _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)


def _draft():
    variante = CoverVariante(headline="**Accroche**", title="**Titre**", subtitle="**Sous**")
    return PostDraft(variantes=[variante] * 4, caption="Legende 🎧 ✨", hashtags=["#electro"])


class Outils:
    def __init__(self):
        self.composes, self.notifications = [], []

    def composer(self, article):
        self.composes.append(article.id)
        return _draft()

    def tourner(self, db, maintenant=BASE, choisir=None):
        return tourner(
            db, maintenant, mode="apercu", composer=self.composer,
            preparer=lambda post, article, dossier: ["p_01.jpg"],
            publier=lambda dossier, noms, legende, vraiment: "ig42",
            notifier=lambda titre, message: self.notifications.append(message),
            horloge=lambda: maintenant, choisir=choisir,
        )


def _epingler(db, article):
    creneau = Creneau(publier_a=H0, article_id=article.id, etat=EtatCreneau.PROGRAMME, epingle=True)
    db.add(creneau)
    db.commit()
    return creneau


def test_raison_sans_visuel(db_session, fred, guettapen):
    sans = _article(db_session, guettapen, "Actu sans artiste",
                    media=[entree("photo", "https://cdn.ig/g.jpg")])
    avec = _article(db_session, guettapen, media=[entree("photo", "https://cdn.ig/g.jpg")],
                    mentioned=["Fred again.."])
    propre = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    assert raison_sans_visuel(sans) == (
        "visuels d'un compte media Instagram interdits, et aucun artiste suivi cite"
    )
    assert raison_sans_visuel(avec) is None
    assert raison_sans_visuel(propre) is None
    assert [c.article_id for c in candidats_eligibles(db_session, BASE)] == [avec.id, propre.id]


def test_un_reel_de_media_devient_eligible_par_la_video_d_un_artiste(db_session, fred, guettapen):
    article = _article(db_session, guettapen, mentioned=["Fred again.."],
                       media=[entree("video", "https://cdn.ig/g.mp4", duration_ms=20_000)])
    assert "categorie media" in raison_ineligible_reel(article, maintenant=BASE)
    _article(db_session, fred, "GTA soundtrack", url="https://www.instagram.com/reel/FRED/",
             media=[entree("video", None, preview_url="https://cdn.ig/v.jpg")], heures=20)
    assert raison_ineligible_reel(article, maintenant=BASE) is None


def test_un_media_sans_visuel_n_est_jamais_programme(db_session, guettapen):
    _article(db_session, guettapen, "Actu sans artiste", media=[entree("photo", "https://cdn.ig/g.jpg")])
    outils = Outils()
    outils.tourner(db_session)
    assert outils.composes == []
    assert all(c.article_id is None for c in db_session.query(Creneau).all())


def test_un_article_epingle_sans_visuel_est_exclu_sans_composition(db_session, guettapen):
    article = _article(db_session, guettapen, "Actu sans artiste",
                       media=[entree("photo", "https://cdn.ig/g.jpg")])
    creneau = _epingler(db_session, article)
    outils = Outils()
    outils.tourner(db_session)
    db_session.refresh(article)
    db_session.refresh(creneau)
    assert outils.composes == []
    assert article.file_exclu_raison.startswith(
        "composition impossible : visuels d'un compte media Instagram"
    )
    assert (creneau.article_id, creneau.etat) == (None, EtatCreneau.LIBRE)


def test_des_visuels_differes_attendent_sans_exclure(db_session, fred):
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    creneau = _epingler(db_session, article)

    def differer(db, article, format_, maintenant):
        raise VisuelsDifferes("quota Graph atteint")

    outils = Outils()
    outils.tourner(db_session, choisir=differer)
    db_session.refresh(article)
    db_session.refresh(creneau)
    assert outils.composes == [] and outils.notifications == []
    assert article.file_exclu_raison is None
    assert creneau.erreur == "composition differee : quota Graph atteint"
    assert creneau.article_id == article.id


def test_les_visuels_choisis_sont_gardes_sur_le_post(db_session, fred):
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    _epingler(db_session, article)
    choisi = actif(entree("photo", "https://cdn.ig/x.jpg"), origine="artiste", compte="@fredagain")
    formats = []

    def choisir(db, article, format_, maintenant):
        formats.append(format_)
        return ChoixVisuels([choisi], "@fredagain")

    Outils().tourner(db_session, choisir=choisir)
    (post,) = db_session.query(Post).all()
    assert (post.assets, post.credit, formats) == ([choisi], "@fredagain", [FormatCreneau.CARROUSEL])


def test_un_brouillon_anterieur_au_fond_interdit_n_est_pas_repris(db_session, fred, guettapen):
    article = _article(db_session, guettapen, media=[entree("photo", "https://cdn.ig/g.jpg")],
                       mentioned=["Fred again.."])
    ancien = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                  caption="L", slides=[{"kind": "cover", "image_url": "https://cdn.ig/g.jpg"}],
                  template="kinda_post")
    db_session.add(ancien)
    db_session.commit()
    creneau = _epingler(db_session, article)
    choisi = actif(entree("photo", "https://cdn.ig/fred.jpg"), origine="artiste", compte="@fredagain")
    outils = Outils()
    outils.tourner(db_session, choisir=lambda db, a, f, m: ChoixVisuels([choisi], "@fredagain"))
    db_session.refresh(creneau)
    assert outils.composes == [article.id]
    assert creneau.post_id not in (None, ancien.id)


def test_un_brouillon_dont_le_compte_est_passe_media_n_est_pas_gele(db_session, fred, guettapen):
    """Recategorisation APRES la composition : la source de l'actif est relue par
    son `article_id`, le creneau lache le post au lieu de publier un visuel
    interdit."""
    poste = _article(db_session, fred, "Fred again.. en studio",
                     media=[entree("photo", "https://cdn.ig/f.jpg")], heures=20)
    article = _article(db_session, guettapen, media=[entree("photo", "https://cdn.ig/g.jpg")],
                       mentioned=["Fred again.."])
    choisi = actif(entree("photo", "https://cdn.ig/f.jpg"), origine="artiste",
                   compte="@fredagain", article_id=poste.id)
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                caption="L", slides=[{"kind": "cover", "image_url": "https://cdn.ig/f.jpg"}],
                template="kinda_post", assets=[choisi], credit="@fredagain")
    db_session.add(post)
    db_session.commit()
    # Dans l'heure : le creneau est a portee de gel (2 h).
    creneau = Creneau(publier_a=BASE + dt.timedelta(hours=1), article_id=article.id,
                      post_id=post.id, etat=EtatCreneau.PROGRAMME, epingle=True)
    db_session.add(creneau)
    fred.categorie = SourceCategorie.MEDIA
    db_session.commit()

    outils = Outils()
    outils.tourner(db_session)
    db_session.refresh(creneau)
    db_session.refresh(article)
    assert creneau.etat == EtatCreneau.PROGRAMME
    assert creneau.post_id != post.id
    assert creneau.erreur == "gel refuse : le post n'a pas de visuel publiable"
    # L'article n'est pas en faute : il peut repartir avec d'autres visuels.
    assert article.file_exclu_raison is None


def test_le_menage_retire_les_vieilles_copies(db_session):
    vieille, recente = chemin_cache("https://cdn/vieille.jpg"), chemin_cache("https://cdn/recente.jpg")
    vieille.parent.mkdir(parents=True)
    vieille.write_bytes(b"x")
    recente.write_bytes(b"x")
    ancien = (BASE - dt.timedelta(days=8)).timestamp()
    os.utime(vieille, (ancien, ancien))
    recent = (BASE - dt.timedelta(days=1)).timestamp()
    os.utime(recente, (recent, recent))
    _menage(db_session, BASE)
    assert (vieille.exists(), recente.exists()) == (False, True)


def test_un_lecteur_neuf_par_passage(monkeypatch):
    crees = []

    class FauxLecteur:
        def __init__(self):
            crees.append(self)

    monkeypatch.setattr(script, "LecteurComptes", FauxLecteur)
    premier, second = script.choix_du_passage(), script.choix_du_passage()
    assert callable(premier) and callable(second)
    assert len(crees) == 2


def test_le_dashboard_compose_un_post_sans_visuel_autorise(client, db_session, guettapen, monkeypatch):
    article = _article(db_session, guettapen, "Actu sans artiste",
                       media=[entree("photo", "https://cdn.ig/g.jpg")])
    monkeypatch.setattr("app.api.posts.make_claude_composer", lambda: lambda a: _draft())
    reponse = client.post(f"/articles/{article.id}/posts")
    assert reponse.status_code == 201
    corps = reponse.json()
    assert (corps["assets"], corps["credit"], corps["slides"][0]["image_url"]) == ([], None, None)


def test_le_dashboard_signale_des_visuels_differes(client, db_session, fred, monkeypatch):
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])

    def differer(*args, **kwargs):
        raise VisuelsDifferes("quota Graph atteint")

    monkeypatch.setattr("app.api.posts.choisir_visuels", differer)
    monkeypatch.setattr("app.api.posts.make_claude_composer", lambda: lambda a: _draft())
    reponse = client.post(f"/articles/{article.id}/posts")
    assert reponse.status_code == 503
    assert "quota" in reponse.json()["detail"]


def test_un_brouillon_sans_aucun_visuel_n_est_pas_gele(db_session, guettapen):
    """Compose a la main au dashboard sans visuel autorise (`assets == []`) :
    la file ne doit pas le publier avec le fond de secours."""
    article = _article(db_session, guettapen, "Actu sans artiste",
                       media=[entree("photo", "https://cdn.ig/g.jpg")])
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                caption="L", slides=[{"kind": "cover", "image_url": None}],
                template="kinda_post", assets=[])
    db_session.add(post)
    db_session.commit()
    creneau = Creneau(publier_a=BASE + dt.timedelta(hours=1), article_id=article.id,
                      post_id=post.id, etat=EtatCreneau.PROGRAMME, epingle=True)
    db_session.add(creneau)
    db_session.commit()

    outils = Outils()
    outils.tourner(db_session)
    db_session.refresh(creneau)
    db_session.refresh(article)
    # Le gel refuse le brouillon vide et lache le post ; rien n'est publie.
    assert creneau.post_id is None
    assert creneau.erreur == "gel refuse : le post n'a pas de visuel publiable"
    assert article.file_exclu_raison is None
    # Au passage suivant, la composition ne reprend pas ce brouillon : sans visuel
    # autorise, l'article sort de la file par le chemin normal.
    outils.tourner(db_session)
    db_session.refresh(article)
    assert outils.composes == []
    assert article.file_exclu_raison.startswith("composition impossible :")


def test_les_actifs_sont_resolus_quelle_que_soit_l_ecriture_du_compte(db_session):
    """`ig:FredAgain` et une URL instagram.com designent le meme compte : la
    relecture de policy ne doit pas dependre de l'ecriture de la source."""
    for url in ("ig:FredAgain", "https://www.instagram.com/fredagain/"):
        source = Source(name="Fred again.. (IG)", type=SourceType.INSTAGRAM, url=url,
                        categorie=SourceCategorie.MEDIA)
        db_session.add(source)
        db_session.commit()
        post = Post(article_title="t", article_url="https://x/1", caption="L",
                    slides=[{"kind": "cover", "image_url": "https://cdn.ig/f.jpg"}],
                    template="kinda_post",
                    assets=[actif(entree("photo", "https://cdn.ig/f.jpg"), origine="artiste",
                                  compte="@fredagain")])
        db_session.add(post)
        db_session.commit()
        assert len(actifs_interdits(db_session, post)) == 1, url
        db_session.delete(source)
        db_session.delete(post)
        db_session.commit()


def test_le_choix_du_passage_rend_une_horloge_aware(monkeypatch):
    vus = []
    monkeypatch.setattr(script, "LecteurComptes", lambda: object())
    monkeypatch.setattr(
        script, "choisir_visuels",
        lambda db, article, *, format_, maintenant, lecteur: vus.append(maintenant),
    )
    script.choix_du_passage()(None, None, FormatCreneau.CARROUSEL, BASE.replace(tzinfo=None))
    assert vus == [BASE]


def test_un_brouillon_sans_fond_extractible_est_relache_pas_exclu(db_session, fred):
    """Un brouillon dont les actifs ne donnent AUCUN fond (video sans vignette) :
    le gel le relache, il n'excluait pas l'article -- une inadequation de filtre
    n'est jamais la faute de l'article."""
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    sans_fond = actif(entree("video", "https://cdn.ig/v.mp4"), origine="article",
                      compte="@fredagain", article_id=article.id)
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                caption="L", slides=[{"kind": "cover", "image_url": None}],
                template="kinda_post", assets=[sans_fond])
    db_session.add(post)
    db_session.commit()
    creneau = Creneau(publier_a=BASE + dt.timedelta(hours=1), article_id=article.id,
                      post_id=post.id, etat=EtatCreneau.PROGRAMME, epingle=True)
    db_session.add(creneau)
    db_session.commit()

    outils = Outils()
    outils.tourner(db_session)
    db_session.refresh(creneau)
    db_session.refresh(article)
    assert creneau.post_id is None
    assert creneau.erreur == "gel refuse : le post n'a pas de visuel publiable"
    assert article.file_exclu_raison is None


def test_un_fond_efface_au_dashboard_relache_le_brouillon_sans_exclure(db_session, fred):
    """Le fond de la cover est editable au dashboard : vide a la main, le post
    partirait sur l'image de secours du gabarit. Le creneau lache le brouillon et
    la composition suivante en refait un -- jamais d'exclusion, l'article a bien
    un visuel."""
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    photo = actif(entree("photo", "https://cdn.ig/f.jpg"), origine="article",
                  compte="@fredagain", article_id=article.id)
    post = Post(article_id=article.id, article_title=article.title, article_url=article.url,
                caption="L", slides=[{"kind": "cover", "image_url": None}],
                template="kinda_post", assets=[photo])
    db_session.add(post)
    db_session.commit()
    creneau = Creneau(publier_a=BASE + dt.timedelta(hours=1), article_id=article.id,
                      post_id=post.id, etat=EtatCreneau.PROGRAMME, epingle=True)
    db_session.add(creneau)
    db_session.commit()

    outils = Outils()
    outils.tourner(db_session)
    db_session.refresh(creneau)
    db_session.refresh(article)
    assert creneau.post_id is None
    assert creneau.erreur == "gel refuse : le post n'a pas de visuel publiable"
    assert article.file_exclu_raison is None
    # Le brouillon n'est pas repris : un nouveau post est compose, avec un fond.
    outils.tourner(db_session, maintenant=BASE + dt.timedelta(minutes=5))
    db_session.refresh(creneau)
    assert outils.composes == [article.id]
    assert creneau.post_id is not None
