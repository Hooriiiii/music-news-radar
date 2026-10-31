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
from app.schedule.pause_visuels import articles_en_pause
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


def test_raison_sans_visuel(db_session, fred, guettapen, monkeypatch):
    """DESSERRE le 2026-10-05 : la recherche web est le dernier barreau de
    l'echelle et peut illustrer a peu pres n'importe quelle actu, donc un article
    sans image EN BASE n'est plus refuse. Mesure du meme jour : 7 des 48 articles
    publiables (14 %) etaient refuses de la file -- arbitrage ET assignation
    manuelle -- par ce seul motif.

    Le reglage est rallume explicitement : la suite coupe la recherche web en
    ambiant (`conftest._pas_de_recherche_web_ambiante`), et c'est justement ce
    reglage qui decide du desserrage."""
    monkeypatch.setattr(settings, "visuels_web_enabled", True)
    sans = _article(db_session, guettapen, "Actu sans artiste",
                    media=[entree("photo", "https://cdn.ig/g.jpg")])
    avec = _article(db_session, guettapen, media=[entree("photo", "https://cdn.ig/g.jpg")],
                    mentioned=["Fred again.."])
    propre = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    assert raison_sans_visuel(sans) is None
    assert raison_sans_visuel(avec) is None
    assert raison_sans_visuel(propre) is None
    assert [c.article_id for c in candidats_eligibles(db_session, BASE)] == [
        sans.id, avec.id, propre.id
    ]


def test_un_article_dont_rien_ne_donne_de_requete_web_reste_refuse(
    db_session, guettapen, monkeypatch
):
    """Le desserrage n'est pas un blanc-seing : il reste deux refus, tous deux
    DETERMINISTES et sans reseau. Ici, rien dans l'article ne donne de requete --
    le barreau web ne peut rien, et le laisser passer ne ferait que lui faire
    sauter des creneaux."""
    monkeypatch.setattr(settings, "visuels_web_enabled", True)
    muet = _article(db_session, guettapen, "???", media=[entree("photo", "https://cdn.ig/g.jpg")])
    raison = raison_sans_visuel(muet)
    assert raison is not None
    assert "compte media Instagram interdits" in raison
    assert "requete web" in raison
    assert [c.article_id for c in candidats_eligibles(db_session, BASE)] == []


def test_la_recherche_web_coupee_rend_le_filtre_d_avant(db_session, guettapen, monkeypatch):
    """`VISUELS_WEB_ENABLED=false` : le barreau n'existe plus, donc le filtre
    reprend exactement sa severite d'avant le desserrage."""
    monkeypatch.setattr(settings, "visuels_web_enabled", False)
    sans = _article(db_session, guettapen, "Actu sans artiste",
                    media=[entree("photo", "https://cdn.ig/g.jpg")])
    raison = raison_sans_visuel(sans)
    assert raison is not None and "recherche web coupee" in raison
    assert [c.article_id for c in candidats_eligibles(db_session, BASE)] == []


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


def test_un_article_dont_le_web_ne_trouve_rien_n_est_JAMAIS_exclu(db_session, guettapen):
    """LA propriete qui rend sur le desserrage de `raison_sans_visuel`, et sa
    violation est SILENCIEUSE : l'article disparait de la file sans que rien ne
    le dise.

    Avant le 2026-10-05, ce chemin levait `AucunVisuel`, donc `runner._exclure`
    posait `file_exclu_raison` et l'article ne revenait PLUS JAMAIS. Desserrer le
    filtre sans toucher a ca aurait donc ete pire que le refus : au lieu d'etre
    ecartes proprement, ces articles auraient ete brules un par un.

    PREUVE PAR MUTATION : remettre `raise AucunVisuel(...)` a la place de
    `raise VisuelIntrouvable(...)` dans `choix_visuels._choisir_visuels`, ou
    remplacer `_sauter(...)` par `_exclure(...)` dans `runner._composer`, fait
    rougir ce test et lui seul."""
    article = _article(db_session, guettapen, "Actu sans artiste",
                       media=[entree("photo", "https://cdn.ig/g.jpg")])
    creneau = _epingler(db_session, article)
    outils = Outils()
    outils.tourner(db_session)
    db_session.refresh(article)
    db_session.refresh(creneau)
    assert outils.composes == []
    # Pas d'exclusion : l'article reste eligible et repartira des qu'un barreau
    # donnera mieux (nouveau post du compte source, cache web expire).
    assert article.file_exclu_raison is None
    # Le creneau SAUTE, et sa raison le dit.
    assert (creneau.article_id, creneau.etat) == (None, EtatCreneau.LIBRE)
    assert creneau.erreur.startswith("visuel introuvable :")
    # Mis a l'ecart TEMPORAIREMENT, sinon il reprendrait le creneau qu'il vient
    # de liberer a chaque passage, jusqu'a MANQUE.
    assert articles_en_pause(BASE) == {article.id}


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
    # Plus d'exclusion depuis le 2026-10-05 : le web a regarde pour rien, donc
    # c'est un SAUT -- l'article peut avoir une image demain.
    assert article.file_exclu_raison is None
    assert articles_en_pause(BASE) == {article.id}


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
    assert post.variants["fond"] == {"origine": ORIGINE_COMPTE, "a_agrandir": False}
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


# --- Tour de correction 1/5 (2026-09-21) ------------------------------------


def _article_score(db, source, titre, relevance, media, heures=2):
    n = next(_numeros)
    article = Article(
        source_id=source.id, url=f"https://www.instagram.com/p/S{n}/", title=titre,
        raw_hash=f"s{n:063d}", relevance_score=relevance, hotness_score=30,
        published_at=BASE - dt.timedelta(hours=heures), media=media,
    )
    db.add(article)
    db.commit()
    return article


def test_c1_un_article_sans_visuel_ne_fait_plus_taire_le_compte_toute_la_journee(
    db_session, fred, monkeypatch
):
    """C-1 (tour de correction 1/5), durci au tour de correction 2/5 : la
    revue rejoue ce scenario avec les VRAIES fenetres de `PUBLICATION_
    FENETRES` (pas de fenetres compressees) et 200 passages -- 4 publications
    sur 4, contre 0 avant le correctif. Sous mutation (retrait de la mise a
    l'ecart), seules les assertions les plus etroites mordaient ; celle qui
    doit tomber a zero est le NOMBRE DE PUBLICATIONS, le defaut mesure a
    l'origine.

    `db_session.autoflush = False` (le reglage de PRODUCTION, `app/db.py`) ;
    l'article fautif n'est JAMAIS epingle -- c'est l'arbitrage, pas un test
    truque, qui doit le laisser de cote."""
    from app.content import illustration

    db_session.autoflush = False
    monkeypatch.setattr(
        illustration, "tailles_de", lambda urls, **k: {"https://cdn.ig/petite.jpg": (200, 150)}
    )

    petit = _article_score(db_session, fred, "Petite photo, gros score", 95,
                           [entree("photo", "https://cdn.ig/petite.jpg")])
    bons = [
        _article_score(db_session, fred, f"Bonne actu {n}", 70 - n,
                       [entree("photo", f"https://cdn.ig/bonne{n}.jpg")])
        for n in range(4)
    ]

    def choisir(db, article, format_, maintenant):
        # Jamais de reserve : la petite photo ne peut etre sauvee par rien --
        # c'est le cas exact que la Tache 5 doit sauter.
        return ChoixVisuels(
            [actif(entree("photo", article.media[0]["url"]), origine="article",
                  compte="@fred", article_id=article.id)],
            "@fred",
        )

    outils = Outils()
    # Demarre une heure avant l'ouverture de la premiere fenetre (08:30 Paris) :
    # BASE (04:00 UTC = 06:00 Paris) - 1h = 05:00 Paris.
    debut = BASE - dt.timedelta(hours=1)
    apercus_totaux, sautes_totaux = 0, 0
    for pas in range(260):  # 260 x 10 min = 43h20 : plus de deux jours de fenetres reelles
        rapport = outils.tourner(db_session, maintenant=debut + dt.timedelta(minutes=10 * pas),
                                 choisir=choisir)
        apercus_totaux += rapport.apercus
        sautes_totaux += rapport.sautes

    db_session.expire_all()
    # Mode apercu : le conteneur est cree chez Meta mais `media_publish` n'est
    # jamais appele (`Post.status` reste DRAFT expres) -- l'etat du CRENEAU
    # (`APERCU`) est ce qui fait avancer la file (`candidats_eligibles`), donc
    # ce qu'il faut lire ici pour dire qu'un article « est parti ».
    partis = {
        c.article_id for c in db_session.query(Creneau).filter(Creneau.etat == EtatCreneau.APERCU)
    }
    petit_frais = db_session.get(Article, petit.id)
    # LE chiffre que la revue mesure : les QUATRE bons articles doivent finir
    # publies -- pas "au moins un", qui laissait passer une mutation partielle.
    assert partis & {b.id for b in bons} == {b.id for b in bons}, (
        f"les 4 bons articles doivent tous etre publies, {len(partis)} l'ont ete"
    )
    assert petit.id not in partis, "l'article sans visuel ne doit jamais partir"
    assert apercus_totaux == 4
    assert sautes_totaux >= 1
    # Jamais exclu, meme apres plusieurs sauts : il reste eligible pour demain.
    assert petit_frais.file_exclu_raison is None


def test_2_5_un_report_persistant_ne_rejoue_pas_c1(db_session, fred):
    """Tour de correction 2/5, point 1 -- mesure : le meme scenario que C-1
    mais avec une reserve qui leve `VisuelDiffere` a CHAQUE passage (au lieu
    d'un article sans image) donnait 1 publication, 3 MANQUE, 0 saute --
    `runner._composer` faisait `continue` sur `VisuelsDifferes` sans jamais
    liberer le creneau ni mettre l'article a l'ecart : il campait dessus
    jusqu'a MANQUE. Meme correctif que C-1 (`signaler_report` bascule un
    report qui ne guerit jamais dans la meme mise a l'ecart), meme scenario,
    memes VRAIES fenetres : doit rendre les 4 publications, pas 1."""
    db_session.autoflush = False

    petit = _article_score(db_session, fred, "Toujours en panne de reserve", 95,
                           [entree("photo", "https://cdn.ig/petite.jpg")])
    bons = [
        _article_score(db_session, fred, f"Bonne actu {n}", 70 - n,
                       [entree("photo", f"https://cdn.ig/bonne{n}.jpg")])
        for n in range(4)
    ]

    def choisir(db, article, format_, maintenant):
        if article.id == petit.id:
            raise VisuelsDifferes("quota Graph atteint")
        return ChoixVisuels(
            [actif(entree("photo", article.media[0]["url"]), origine="article",
                  compte="@fred", article_id=article.id)],
            "@fred",
        )

    outils = Outils()
    debut = BASE - dt.timedelta(hours=1)
    apercus_totaux, sautes_totaux, reportes_totaux = 0, 0, 0
    for pas in range(260):
        rapport = outils.tourner(db_session, maintenant=debut + dt.timedelta(minutes=10 * pas),
                                 choisir=choisir)
        apercus_totaux += rapport.apercus
        sautes_totaux += rapport.sautes
        reportes_totaux += rapport.reportes

    db_session.expire_all()
    partis = {
        c.article_id for c in db_session.query(Creneau).filter(Creneau.etat == EtatCreneau.APERCU)
    }
    petit_frais = db_session.get(Article, petit.id)
    assert partis & {b.id for b in bons} == {b.id for b in bons}, (
        f"les 4 bons articles doivent tous etre publies, {len(partis)} l'ont ete"
    )
    assert petit.id not in partis
    assert apercus_totaux == 4
    assert reportes_totaux > 0  # le compteur distinct de `sautes` (I-2)
    assert sautes_totaux >= 1  # le report a fini par basculer en pause
    # Un report, meme devenu une pause, n'est jamais une exclusion.
    assert petit_frais.file_exclu_raison is None


def test_c1_un_article_revient_apres_sa_pause(db_session, fred):
    """La mise a l'ecart est TEMPORAIRE : passe la duree de la pause,
    l'article redevient un candidat normal -- ce n'est pas une exclusion."""
    from app.schedule.pause_visuels import articles_en_pause, marquer_saute

    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    fin = marquer_saute(article.id, BASE, duree=dt.timedelta(minutes=30))

    assert article.id in articles_en_pause(BASE + dt.timedelta(minutes=10))
    assert article.id in articles_en_pause(fin - dt.timedelta(seconds=1))
    assert article.id not in articles_en_pause(fin)
    assert article.id not in articles_en_pause(fin + dt.timedelta(hours=1))


# --- Tache 6 (2026-09-23) : le SAUT au GEL suit le meme contrat que le saut
# a la composition (Tache 5) -- pas une exclusion, meme comptage, meme ordre
# liberer-avant-pause. Le vrai `preparer_fichiers` n'est pas invoque ici
# (couvert par `tests/publish/test_publish_preparation.py`) : cette suite verifie
# uniquement le CABLAGE de `runner._geler` autour de `PreparationSautee`.


def test_un_saut_au_gel_libere_le_creneau_sans_exclure(db_session, fred):
    """`PreparationSautee` (agrandissement indisponible ou insuffisant) fait
    LIBERER le creneau, jamais l'exclusion posee par `PreparationImpossible` :
    `file_exclu_raison` doit rester vide, et le compteur `sautes` (pas
    `erreurs` silencieux) doit le dire."""
    from app.publish.preparation import PreparationSautee

    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    creneau = _epingler(db_session, article)
    # Dans la fenetre de gel (`publication_gel_heures`, 2h) : sans ca,
    # `_geler` ignore un creneau trop lointain et ce test ne prouverait rien.
    creneau.publier_a = BASE + dt.timedelta(minutes=30)
    db_session.commit()

    def preparer_sautant(post, article, dossier):
        raise PreparationSautee("fond agrandi toujours sous le plancher de nettete pour ce gabarit")

    rapport = tourner(
        db_session, BASE, mode="apercu", composer=lambda a: _draft(),
        preparer=preparer_sautant,
        publier=lambda dossier, noms, legende, vraiment: "ig42",
        notifier=lambda titre, message: None,
        horloge=lambda: BASE,
    )
    db_session.refresh(article)
    db_session.refresh(creneau)

    assert article.file_exclu_raison is None
    assert rapport.sautes == 1
    assert (creneau.article_id, creneau.etat) == (None, EtatCreneau.LIBRE)


def test_un_saut_au_gel_ne_pose_jamais_la_pause_si_le_creneau_n_a_pas_ete_libere(
    db_session, fred, monkeypatch
):
    """Invariant d'ordre partage avec la composition (Tache 5,
    `app/schedule/pause_visuels.py`) : si un autre agent a deja repris ce
    creneau entre-temps (`_liberer` echoue), la mise a l'ecart n'est jamais
    posee -- sinon un article prendrait une pause pour un creneau qu'on n'a
    meme pas reussi a lui retirer."""
    from app.publish import preparation
    from app.schedule import pause_visuels, runner as runner_mod

    article = _article(db_session, fred, media=[entree("photo", "https://cdn.ig/f.jpg")])
    creneau = _epingler(db_session, article)
    creneau.publier_a = BASE + dt.timedelta(minutes=30)
    db_session.commit()

    appels_pause = []
    monkeypatch.setattr(
        pause_visuels, "marquer_saute",
        lambda *a, **k: appels_pause.append((a, k)) or BASE,
    )
    monkeypatch.setattr(runner_mod, "marquer_saute", pause_visuels.marquer_saute)
    # `_liberer` echoue toujours : simule un creneau deja repris ailleurs.
    monkeypatch.setattr(runner_mod, "_liberer", lambda *a, **k: False)

    def preparer_sautant(post, article, dossier):
        raise preparation.PreparationSautee("agrandissement indisponible")

    rapport = tourner(
        db_session, BASE, mode="apercu", composer=lambda a: _draft(),
        preparer=preparer_sautant,
        publier=lambda dossier, noms, legende, vraiment: "ig42",
        notifier=lambda titre, message: None,
        horloge=lambda: BASE,
    )

    assert appels_pause == []
    assert rapport.sautes == 0
