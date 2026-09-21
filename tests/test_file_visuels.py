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
from app.content.visuels import FOND_KINDA, actif, chemin_cache
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


def test_un_doublon_reclasse_media_apres_la_composition_ne_prete_plus_son_visuel(
    db_session, fred
):
    """Revue du 2026-09-21 (I-2) : les visuels d'un carrousel viennent de TOUT le
    groupe de doublons, et chaque actif porte l'`article_id` du MEMBRE dont il
    vient. Si la source de ce membre passe `media` entre la composition (J-1)
    et le gel (T-2h), la ceinture du gel doit le voir -- elle ne relisait que
    la source du representant, et celle des seuls actifs d'artistes."""
    presse = Source(name="Tsugi (RSS)", type=SourceType.RSS, url="https://tsugi.fr/feed",
                    categorie=SourceCategorie.MEDIA)
    compte = _source(db_session, "Nuits Sonores (IG)", "nuits_sonores", SourceCategorie.FESTIVAL)
    db_session.add(presse)
    db_session.commit()
    representant = _article(db_session, presse, media=[entree("photo", "https://tsugi.fr/a.jpg")],
                            url="https://tsugi.fr/a")
    membre = _article(db_session, compte, media=[entree("photo", "https://cdn.ig/ns.jpg")])
    membre.duplicate_of_id = representant.id
    db_session.commit()
    post = Post(
        article_id=representant.id, article_title="t", article_url=representant.url,
        caption="L", template="kinda_post",
        slides=[{"kind": "cover", "image_url": "https://tsugi.fr/a.jpg"}],
        assets=[
            actif(entree("photo", "https://tsugi.fr/a.jpg"), origine="article",
                  article_id=representant.id),
            actif(entree("photo", "https://cdn.ig/ns.jpg"), origine="article",
                  compte="@nuits_sonores", article_id=membre.id),
        ],
    )
    db_session.add(post)
    db_session.commit()
    assert actifs_interdits(db_session, post) == []

    compte.categorie = SourceCategorie.MEDIA
    db_session.commit()

    interdits = actifs_interdits(db_session, post)
    assert [a["article_id"] for a in interdits] == [membre.id]


def test_un_membre_purge_est_retrouve_par_son_compte(db_session):
    """La retention est de 2 jours : le membre peut avoir ete purge entre la
    composition et le gel. Son compte (`@handle`) suffit alors a relire la
    source, comme pour un actif d'artiste."""
    source = _source(db_session, "Nuits Sonores (IG)", "nuits_sonores", SourceCategorie.MEDIA)
    post = Post(
        article_title="t", article_url="https://x/1", caption="L", template="kinda_post",
        slides=[{"kind": "cover", "image_url": "https://tsugi.fr/a.jpg"}],
        assets=[actif(entree("photo", "https://cdn.ig/ns.jpg"), origine="article",
                      compte="@nuits_sonores", article_id=999_999)],
    )
    db_session.add(post)
    db_session.commit()

    assert len(actifs_interdits(db_session, post)) == 1
    assert source.categorie == SourceCategorie.MEDIA


# --- Tache 5 (2026-09-21) : l'echelle du fond va jusqu'au bout, sinon on saute -


def test_un_visuel_trop_petit_sans_secours_saute_le_creneau_sans_exclure(
    db_session, fred, monkeypatch
):
    """L'echelle est allee jusqu'au bout (ici : rien que la photo de l'actu, pas
    de reserve) sans rien trouver d'assez net : le creneau SAUTE -- il se libere
    (`_liberer`) au lieu d'exclure (`_exclure`). L'article n'est PAS en defaut,
    il peut avoir une photo demain ; c'est la retention de 2 jours qui conclura
    s'il n'en trouve jamais. Compte a part dans `RapportPublication.sautes`, et
    jamais l'appel paye a Sonnet pour lui (`outils.composes` ne le contient pas).

    Le creneau libere est repris DANS LE MEME PASSAGE par le candidat suivant
    (`exclusions=True` relance l'arbitrage) : ici, l'unique autre article
    eligible du compte -- la preuve textuelle du « le creneau se libere et
    prend le candidat suivant » du plan."""
    from app.content import illustration

    petit = _article(db_session, fred, "Petite photo",
                     media=[entree("photo", "https://cdn.ig/petite.jpg")], heures=3)
    bon = _article(db_session, fred, "Bonne photo",
                   media=[entree("photo", "https://cdn.ig/bonne.jpg")], heures=2)
    creneau = _epingler(db_session, petit)
    # 200x150 -> 5,4x/9x d'agrandissement pour 1080x1350 : tres au-dela du
    # plancher (`cover_fond_upscale_max`, 2,2x).
    monkeypatch.setattr(
        illustration, "tailles_de", lambda urls, **k: {"https://cdn.ig/petite.jpg": (200, 150)}
    )

    def choisir(db, a, format_, maintenant):
        if a.id == petit.id:
            return ChoixVisuels(
                [actif(entree("photo", "https://cdn.ig/petite.jpg"), origine="article",
                       compte="@fred", article_id=petit.id)],
                "@fred",
            )  # aucune reserve pour remplacer
        return ChoixVisuels(
            [actif(entree("photo", "https://cdn.ig/bonne.jpg"), origine="article",
                   compte="@fred", article_id=bon.id)],
            "@fred",
        )

    outils = Outils()
    rapport = outils.tourner(db_session, choisir=choisir)
    db_session.refresh(petit)
    db_session.refresh(creneau)

    # Seul "bon" est compose : "petit" a saute AVANT l'appel paye a Sonnet.
    assert outils.composes == [bon.id]
    (post,) = db_session.query(Post).all()
    assert post.article_id == bon.id
    assert petit.file_exclu_raison is None
    assert rapport.sautes == 1
    # Le creneau, libere par le saut, a ete repris par le candidat suivant --
    # jamais laisse vide alors qu'un autre article pouvait le remplir.
    assert creneau.article_id == bon.id


def test_une_reserve_evite_le_saut_quand_le_fond_de_lactu_est_trop_petit(
    db_session, fred, monkeypatch, tmp_path
):
    """Le plancher fait descendre d'un barreau (ici : la reserve du compte
    source) au lieu de sauter directement -- l'echelle continue avant de
    conclure. Bout en bout avec le vrai chemin du juge (comme les tests de la
    Tache 2/4), pour prouver que la reserve est bien ce qui separe le saut de
    la publication."""
    from app.content import illustration
    from app.content.illustration import Lot, Verdict
    from app.content.visuels import ORIGINE_COMPTE

    monkeypatch.setattr(settings, "illustration_cache_path", tmp_path / "verdicts.json")
    monkeypatch.setattr(settings, "illustration_vision_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(illustration, "_octets", lambda url: b"jpeg")
    monkeypatch.setattr(illustration, "reduire", lambda octets, taille=None: octets)
    monkeypatch.setattr(
        illustration, "make_claude_juge",
        lambda: (lambda images: [Verdict(affiche=False, sujet="scene", note=70)] * len(images)),
    )
    # Seule la photo de l'actu est petite ; la reserve, de taille inconnue,
    # passe par la regle du doute (ne pas savoir n'est pas savoir que c'est trop
    # petit).
    monkeypatch.setattr(
        illustration, "tailles_de", lambda urls, **k: {"https://cdn.ig/petite.jpg": (200, 150)}
    )
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/petite.jpg")])
    creneau = _epingler(db_session, article)
    choisi = actif(entree("photo", "https://cdn.ig/petite.jpg"), origine="article",
                   compte="@fred", article_id=article.id)

    def choisir(db, a, format_, maintenant):
        return ChoixVisuels(
            [choisi], "@fred",
            secours=[Lot(ORIGINE_COMPTE, lambda: ["file:///cache/grande.jpg"])],
        )

    outils = Outils()
    rapport = outils.tourner(db_session, choisir=choisir)
    db_session.refresh(creneau)

    assert rapport.sautes == 0
    (post,) = db_session.query(Post).all()
    assert post.slides[0]["image_url"] == "file:///cache/grande.jpg"
    assert post.variants["fond"] == {"origine": ORIGINE_COMPTE}
    assert creneau.post_id == post.id


def test_une_panne_de_transport_reste_un_report_jamais_un_saut(db_session, fred):
    """`VisuelsDifferes` (choix des visuels : quota, reseau) est tranchee AVANT
    que la Tache 5 n'entre en jeu -- une panne de transport ne doit jamais se
    faire passer pour un saut faute d'image de qualite. Non-regression : le
    comportement (deja garanti avant la Tache 5) doit survivre a l'ajout du
    pre-controle du fond."""
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    creneau = _epingler(db_session, article)

    def differer(db, article, format_, maintenant):
        raise VisuelsDifferes("quota Graph atteint")

    outils = Outils()
    rapport = outils.tourner(db_session, choisir=differer)
    db_session.refresh(article)
    db_session.refresh(creneau)

    assert outils.composes == [] and rapport.sautes == 0
    assert article.file_exclu_raison is None
    assert creneau.erreur == "composition differee : quota Graph atteint"
    assert creneau.article_id == article.id  # le creneau garde l'article, il ne saute pas


def test_un_brouillon_anterieur_a_la_tache_5_sur_l_aplat_kinda_est_recompose(db_session, fred):
    """Brouillon compose AVANT la Tache 5 (2026-09-21), fond sur l'aplat Kinda --
    c'etait alors une issue publiable. Elle ne l'est plus : la file le traite
    comme perime (`runner._brouillon_perime`) et le recompose au lieu de le
    reprendre tel quel (meme mecanique que `post_a_reprendre` -- un brouillon
    existant pour l'article, jamais encore attache a ce creneau)."""
    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    # `assets` porte une VRAIE image (comme le ferait `actifs_de_secours`) : ce
    # brouillon n'est perime pour AUCUNE des raisons preexistantes (assets vide,
    # aucun fond extractible) -- seule la Tache 5 (fond == FOND_KINDA) doit le
    # faire recomposer.
    ancien = Post(
        article_id=article.id, article_title=article.title, article_url=article.url,
        caption="L", slides=[{"kind": "cover", "image_url": FOND_KINDA}],
        template="kinda_post",
        assets=[actif(entree("photo", "https://cdn.ig/f.jpg"), origine="article")],
        variants={"fond": {"origine": "kinda"}},
    )
    db_session.add(ancien)
    db_session.commit()
    creneau = _epingler(db_session, article)

    choisi = actif(entree("photo", "https://cdn.ig/fred.jpg"), origine="artiste", compte="@fredagain")
    outils = Outils()
    outils.tourner(db_session, choisir=lambda db, a, f, m: ChoixVisuels([choisi], "@fredagain"))
    db_session.refresh(creneau)

    assert outils.composes == [article.id]
    assert creneau.post_id not in (None, ancien.id)
