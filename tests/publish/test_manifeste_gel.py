"""Le manifeste du gel : ce qui part REELLEMENT, ecrit au lieu d'etre recalcule.

LE DEFAUT MESURE. `posts.slides` est le brouillon, `preparation.
medias_du_carrousel` y ajoute au gel jusqu'a neuf medias de l'actu, et rien ne
persistait le resultat : le post 164 (Tomorrowland) affichait 1 slide dans la
fiche quand Instagram en recevait 3 -- dont un logo Wikipedia recadre, repere
APRES publication.

Mesure du 2026-10-04 sur la base vivante : 86 creneaux portent des fichiers
figes ; parmi les 48 carrousels dont les candidats sont encore recalculables,
27 (56 %) ne retombent pas sur le nombre de fichiers reellement figes -- et un
creneau (le 3) annonce 5 fichiers pour 1 seul candidat recalculable, son article
ayant ete purge. Le recalcul ne dit pas ce qui est parti, et ne le pourra jamais.
"""

import datetime as dt
import io

import pytest
from PIL import Image

from app.content.media import PHOTO, VIDEO
from app.content.render import RenduSlide
from app.content.visuels import ORIGINE_ARTICLE, ORIGINE_ARTISTE, actif
from app.models import (
    Article,
    Creneau,
    EtatCreneau,
    Post,
    Source,
    SourceCategorie,
    SourceType,
)
from app.publish.manifeste import Figement, normaliser, nombre_publies
from app.publish.preparation import (
    CARROUSEL_MAX,
    candidats_editables,
    liste_explicite,
    medias_du_carrousel,
    preparer_fichiers,
)

MAINTENANT = dt.datetime.now(dt.timezone.utc)

# Une cover COMPLETE : `PostOut`/`CoverIn` exigent les trois textes, et c'est
# volontaire -- une cover sans texte echouerait plus tard chez Chromium.
COVER_EN_BASE = {"kind": "cover", "headline": "**A**", "title": "**T**",
                 "subtitle": "**S**", "image_url": "https://img/f.jpg"}


def _png(largeur=1200, hauteur=1500):
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), (10, 20, 30)).save(tampon, "PNG")
    return tampon.getvalue()


def _rendre(slides, template, palette_=None):
    return [RenduSlide(png=_png(1080, 1350), scale=1.0, lines={}, overflow=False)]


def _telecharger(octets=None):
    def telecharger(url):
        return (octets if octets is not None else _png()), None

    return telecharger


def _post(slides=None, assets=None, variants=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        slides=slides or [{"kind": "cover", "image_url": "https://img/fond.jpg"}],
        assets=assets,
        template="kinda_post",
        variants=variants if variants is not None else {"fond": {"origine": "article"}},
        credit=None,
    )


def _article(*urls, categorie=SourceCategorie.ARTISTE):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=1,
        source=SimpleNamespace(type=SourceType.RSS, categorie=categorie),
        media=[{"type": PHOTO, "url": url} for url in urls],
    )


# --- Task 1 : la liste est ECRITE ------------------------------------------


def test_le_manifeste_suit_les_fichiers_figes_element_par_element(tmp_path):
    """L'invariant central : les entrees NON ecartees sont, dans l'ordre, exactement
    les fichiers figes. Si les deux divergeaient, le manifeste mentirait sur ce qui
    part -- le defaut meme que ce chantier corrige."""
    figement = preparer_fichiers(
        _post(), _article("https://img/1.jpg", "https://img/2.jpg"), tmp_path,
        rendre=_rendre, telecharger=_telecharger(),
    )
    assert isinstance(figement, Figement)
    assert len(figement.noms) == 3
    partants = [e for e in figement.elements if not e["ecarte"]]
    assert [e["fichier"] for e in partants] == figement.noms
    # La cover d'abord, puis les medias de l'actu : l'ordre exact du carrousel.
    assert [e["kind"] for e in partants] == ["cover", "media", "media"]
    assert partants[0]["source_url"] == "https://img/fond.jpg"
    assert partants[0]["origine"] == "article"
    assert nombre_publies(figement.elements) == 3


def test_une_image_que_le_cadrage_abimerait_figure_AVEC_SA_RAISON(tmp_path):
    """C'est precisement ce que l'utilisateur veut voir, et qui etait seulement
    JOURNALISE : une image trop petite est ecartee en silence. Le logo Wikipedia du
    post 164 est parti faute de ce signal (il est passe, lui -- mais personne ne
    voyait non plus ce qui passait)."""
    figement = preparer_fichiers(
        _post(), _article("https://img/minuscule.jpg"), tmp_path,
        rendre=_rendre, telecharger=_telecharger(_png(120, 150)),
    )
    assert figement.noms == [figement.noms[0]]  # la cover seule est figee
    ecartes = [e for e in figement.elements if e["ecarte"]]
    assert len(ecartes) == 1
    assert ecartes[0]["source_url"] == "https://img/minuscule.jpg"
    assert ecartes[0]["raison"]  # une raison en clair, jamais une disparition
    assert ecartes[0]["fichier"] is None


def test_le_carrousel_plein_dit_ce_qu_il_a_laisse_dehors(tmp_path):
    """Meta plafonne a dix elements. Le surplus etait `break`-e : rien ne disait
    qu'il existait. Il est desormais ECARTE avec sa raison -- aucun fichier de plus
    n'est fige pour autant."""
    urls = [f"https://img/{i}.jpg" for i in range(1, 13)]
    figement = preparer_fichiers(
        _post(), _article(*urls), tmp_path, rendre=_rendre, telecharger=_telecharger()
    )
    assert len(figement.noms) == CARROUSEL_MAX
    ecartes = [e for e in figement.elements if e["ecarte"]]
    assert len(ecartes) == 3
    assert all("carrousel plein" in e["raison"] for e in ecartes)


def test_une_video_retombee_sur_sa_vignette_le_dit(tmp_path):
    """Ce qui arrive chez Meta n'est pas ce que le brouillon annonce : une video
    inutilisable part en PHOTO. La ligne du manifeste porte le type reellement
    publie et la raison du remplacement."""
    from types import SimpleNamespace

    article = SimpleNamespace(
        id=1,
        source=SimpleNamespace(type=SourceType.RSS, categorie=SourceCategorie.ARTISTE),
        media=[{"type": VIDEO, "url": None, "preview_url": "https://img/vignette.jpg"}],
    )
    figement = preparer_fichiers(
        _post(), article, tmp_path, rendre=_rendre, telecharger=_telecharger()
    )
    media = [e for e in figement.elements if e["kind"] == "media"][0]
    assert media["ecarte"] is False
    # Le TYPE reste celui du media d'origine, et c'est voulu : le couple
    # (`type`, `source_url`) doit rester coherent, sinon l'editeur reecrirait une
    # slide `photo` sur une URL de video. Ce qui part a la place se lit dans
    # `raison`.
    assert media["type"] == VIDEO
    assert "vignette" in media["raison"]


def test_le_manifeste_est_denormalise_donc_il_survit_a_la_purge(tmp_path):
    """La liste ne reference AUCUN article : elle ne porte que des URL, des origines
    et des noms de fichiers. C'est la condition pour qu'elle reste lisible apres la
    purge a deux jours -- et un creneau peut etre autonome de son article."""
    figement = preparer_fichiers(
        _post(), _article("https://img/1.jpg"), tmp_path, rendre=_rendre, telecharger=_telecharger()
    )
    attendu = {"kind", "type", "origine", "source_url", "fichier", "compte", "raison", "ecarte"}
    assert all(set(entree) == attendu for entree in figement.elements)
    assert not any("article_id" in entree for entree in figement.elements)


def test_un_preparateur_qui_ne_rend_que_des_noms_vaut_INCONNU_jamais_vide():
    """Regle de la refonte UX : une valeur non lue s'affiche inconnue, jamais verte.
    Un preparateur injecte (tests, `tester_publication`) ne decrit pas ce qui part --
    `elements` vaut alors None, et personne n'invente de liste."""
    figement = normaliser(["a_01.jpg", "a_02.jpg"])
    assert figement.noms == ["a_01.jpg", "a_02.jpg"]
    assert figement.elements is None
    assert nombre_publies(None) is None


# --- Task 1 cote base : le gel ECRIT la colonne ----------------------------


def test_le_gel_ecrit_le_manifeste_dans_la_meme_ecriture_que_les_fichiers(db_session, tmp_path, monkeypatch):
    """Les deux decrivent le MEME gel : une ecriture conditionnelle unique, pour
    qu'ils ne puissent jamais se desynchroniser."""
    from app.config import settings
    from app.models import FormatCreneau
    from app.schedule.runner import RapportPublication, _geler

    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db_session.add(source)
    db_session.commit()
    article = Article(
        source_id=source.id, url="https://t/1", title="Actu", raw_hash="h1",
        relevance_score=90, hotness_score=30, published_at=MAINTENANT - dt.timedelta(hours=1),
        media=[{"type": PHOTO, "url": "https://img/1.jpg"}],
    )
    db_session.add(article)
    db_session.commit()
    post = Post(
        article_id=article.id, article_title=article.title, article_url=article.url,
        caption="L", template="kinda_post",
        # Un actif qui PORTE le fond : sans lui, `_brouillon_perime` relache le post
        # (aucun fond extractible des actifs) et le gel n'a jamais lieu.
        assets=[actif({"type": PHOTO, "url": "https://img/fond.jpg"}, origine=ORIGINE_ARTICLE)],
        slides=[{"kind": "cover", "image_url": "https://img/fond.jpg"}],
    )
    db_session.add(post)
    db_session.commit()
    creneau = Creneau(
        publier_a=MAINTENANT + dt.timedelta(hours=1), article_id=article.id, post_id=post.id,
        etat=EtatCreneau.PROGRAMME, epingle=True,
    )
    db_session.add(creneau)
    db_session.commit()

    manifeste = [{"kind": "cover", "type": PHOTO, "fichier": "p_01.jpg", "ecarte": False,
                  "origine": "article", "source_url": "https://img/fond.jpg", "compte": None,
                  "raison": None}]

    def preparer(post_, article_, dossier):
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "p_01.jpg").write_bytes(b"x")
        return Figement(noms=["p_01.jpg"], elements=manifeste)

    # `_geler` directement, et non `tourner` : ce test porte sur l'ECRITURE du gel.
    # Passer par `tourner` ferait d'abord tourner l'arbitrage, qui recalcule la
    # grille d'heures -- un creneau pose a une heure arbitraire n'y survit pas, et
    # le test mesurerait alors l'arbitrage.
    _geler(
        db_session, MAINTENANT, {FormatCreneau.CARROUSEL: preparer},
        lambda *a: None, RapportPublication(),
    )
    db_session.refresh(creneau)
    assert (creneau.etat, creneau.erreur) == (EtatCreneau.GELE, None)
    assert creneau.fichiers == ["p_01.jpg"]
    assert creneau.elements == manifeste


# --- Task 2 : la liste devient EDITABLE ------------------------------------


def test_sans_marqueur_le_gel_reprend_les_actifs_comme_avant():
    """Le comportement par defaut ne bouge PAS : un post qu'on n'a pas edite voit
    toujours ses actifs ajoutes apres les slides."""
    post = _post(assets=[actif({"type": PHOTO, "url": "https://img/a.jpg"}, origine=ORIGINE_ARTISTE, compte="@x")])
    assert liste_explicite(post) is False
    assert [m["url"] for m in medias_du_carrousel(post, _article())] == ["https://img/a.jpg"]


def test_la_liste_ARRETEE_ne_reprend_plus_ni_actifs_ni_medias_de_l_actu():
    """Sans ce marqueur, retirer un element n'aurait AUCUN effet : le gel suivant le
    reprendrait depuis `posts.assets` ou depuis l'article, et l'edition serait
    annulee en silence."""
    post = _post(
        slides=[
            {"kind": "cover", "image_url": "https://img/fond.jpg"},
            {"kind": "media", "type": PHOTO, "url": "https://img/garde.jpg"},
        ],
        assets=[actif({"type": PHOTO, "url": "https://img/a.jpg"}, origine=ORIGINE_ARTISTE, compte="@x")],
        variants={"fond": {"origine": "article"}, "carrousel": {"explicite": True}},
    )
    assert liste_explicite(post) is True
    article = _article("https://img/autre.jpg")
    assert [m["url"] for m in medias_du_carrousel(post, article)] == ["https://img/garde.jpg"]


def test_la_liste_arretee_garde_les_regles_du_projet_pas_seulement_les_candidats():
    """Ce que le marqueur change : la SOURCE des candidats. Ce qu'il ne change
    jamais : le fond n'est pas republie, et un visuel interdit reste interdit."""
    post = _post(
        slides=[
            {"kind": "cover", "image_url": "https://img/fond.jpg"},
            {"kind": "media", "type": PHOTO, "url": "https://img/fond.jpg"},
        ],
        variants={"fond": {"origine": "article"}, "carrousel": {"explicite": True}},
    )
    assert medias_du_carrousel(post, _article()) == []


def test_la_reserve_de_l_editeur_ne_propose_jamais_le_visuel_d_un_compte_media():
    """Regle du 2026-09-17, sans exception : une reserve qui proposerait un visuel
    interdit le ferait rentrer par la porte de l'editeur."""
    from types import SimpleNamespace

    article = SimpleNamespace(
        id=1,
        source=SimpleNamespace(type=SourceType.INSTAGRAM, categorie=SourceCategorie.MEDIA),
        media=[{"type": PHOTO, "url": "https://img/guettapen.jpg"}],
    )
    assert candidats_editables(_post(assets=[]), article) == []


def test_la_reserve_offre_les_actifs_ET_les_medias_de_l_actu_sans_doublon():
    """L'editeur doit pouvoir REMETTRE un element que le gel avait ecarte : ni le
    plafond de dix, ni la regle du fond, ni la liste arretee ne bornent la reserve."""
    post = _post(
        assets=[actif({"type": PHOTO, "url": "https://img/a.jpg"}, origine=ORIGINE_ARTISTE, compte="@fredagain")],
        variants={"fond": {"origine": "article"}, "carrousel": {"explicite": True}},
    )
    reserve = candidats_editables(post, _article("https://img/a.jpg", "https://img/b.jpg"))
    assert [c["url"] for c in reserve] == ["https://img/a.jpg", "https://img/b.jpg"]
    assert reserve[0]["compte"] == "@fredagain"
    assert reserve[0]["origine"] == ORIGINE_ARTISTE


@pytest.mark.parametrize("payload", [
    {"carrousel_explicite": True},
    {"slides": [{"kind": "cover", "headline": "**H**", "title": "**T**", "subtitle": "**S**", "image_url": "https://img/autre.jpg"}]},
])
def test_editer_la_liste_rouvre_le_gel_et_garde_le_post(client, db_session, payload):
    """Le re-gel est AUTOMATIQUE, et Claude n'est JAMAIS repaye : le creneau garde
    son `post_id` -- c'est aussi la condition de l'ecriture conditionnelle qui
    interdit la double publication."""
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://t/1", title="Actu", raw_hash="h1",
                      published_at=MAINTENANT)
    db_session.add(article)
    db_session.commit()
    post = Post(article_id=article.id, article_title="Actu", article_url="https://t/1",
                caption="L", template="kinda_post",
                slides=[COVER_EN_BASE])
    db_session.add(post)
    db_session.commit()
    creneau = Creneau(publier_a=MAINTENANT + dt.timedelta(hours=1), article_id=article.id,
                      post_id=post.id, etat=EtatCreneau.GELE, fichiers=["p_01.jpg"],
                      elements=[{"kind": "cover", "fichier": "p_01.jpg", "ecarte": False}])
    db_session.add(creneau)
    db_session.commit()

    assert client.patch(f"/posts/{post.id}", json=payload).status_code == 200
    db_session.refresh(creneau)
    db_session.refresh(post)
    assert (creneau.etat, creneau.fichiers, creneau.elements) == (EtatCreneau.PROGRAMME, None, None)
    assert creneau.post_id == post.id
    if "carrousel_explicite" in payload:
        assert liste_explicite(post) is True


def test_arreter_la_liste_ne_perd_ni_la_palette_ni_l_origine_du_fond(client, db_session):
    """`variants` s'ecrit en FUSION, jamais en bloc : l'ecrire en bloc ferait
    disparaitre la palette et l'origine du fond, figees a la composition parce que
    l'article est purge a deux jours."""
    post = Post(article_id=None, article_title="Actu", article_url="https://t/1", caption="L",
                template="kinda_post", slides=[COVER_EN_BASE],
                variants={"palette": "ecologie", "fond": {"origine": "compte"}, "variantes": []})
    db_session.add(post)
    db_session.commit()

    assert client.patch(f"/posts/{post.id}", json={"carrousel_explicite": True}).status_code == 200
    db_session.refresh(post)
    assert post.variants["palette"] == "ecologie"
    assert post.variants["fond"]["origine"] == "compte"
    assert post.variants["carrousel"] == {"explicite": True}


def test_une_edition_refusee_ne_touche_a_RIEN(client, db_session, monkeypatch):
    """Trop pres du depart : la reouverture est refusee, donc AUCUNE edition n'est
    appliquee. L'ancien comportement -- editer le brouillon pendant que les fichiers
    figes disent autre chose -- etait precisement le defaut a supprimer."""
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://t/1", title="Actu", raw_hash="h1",
                      published_at=MAINTENANT)
    db_session.add(article)
    db_session.commit()
    post = Post(article_id=article.id, article_title="Actu", article_url="https://t/1", caption="L",
                template="kinda_post", slides=[COVER_EN_BASE])
    db_session.add(post)
    db_session.commit()
    creneau = Creneau(publier_a=MAINTENANT + dt.timedelta(minutes=5), article_id=article.id,
                      post_id=post.id, etat=EtatCreneau.GELE, fichiers=["p_01.jpg"])
    db_session.add(creneau)
    db_session.commit()

    reponse = client.patch(f"/posts/{post.id}", json={"carrousel_explicite": True})
    assert reponse.status_code == 409
    assert "Trop pres du depart" in reponse.json()["detail"]
    db_session.refresh(creneau)
    db_session.refresh(post)
    # Le gel tient toujours, et le post n'a pas bouge : le creneau partira comme
    # prevu plutot que de manquer.
    assert (creneau.etat, creneau.fichiers) == (EtatCreneau.GELE, ["p_01.jpg"])
    assert liste_explicite(post) is False


def test_rien_ne_se_rouvre_pendant_une_publication_en_vol(client, db_session):
    """« Pas d'edition quand la publication est en vol. » Le creneau est en ECHEC de
    reclamation (GELE -> ECHEC committe AVANT l'appel a Meta) : il n'est donc meme
    pas dans l'ensemble des creneaux GELES, et le refus tombe avant."""
    from app.schedule.runner import RAISON_RECLAMATION

    post = Post(article_id=None, article_title="Actu", article_url="https://t/1", caption="L",
                template="kinda_post", slides=[COVER_EN_BASE])
    db_session.add(post)
    db_session.commit()
    db_session.add(Creneau(
        publier_a=MAINTENANT + dt.timedelta(minutes=5), post_id=post.id,
        etat=EtatCreneau.ECHEC, erreur=RAISON_RECLAMATION,
    ))
    db_session.commit()

    reponse = client.patch(f"/posts/{post.id}", json={"carrousel_explicite": True})
    assert reponse.status_code == 409
    assert "Publication en cours" in reponse.json()["detail"]
    db_session.refresh(post)
    assert liste_explicite(post) is False


def test_la_relecture_expose_le_manifeste_et_la_reserve(client, db_session):
    """`GET /relecture` rend le manifeste TEL QU'IL A ETE ECRIT, jamais recalcule --
    et la reserve de l'editeur, pour que le dashboard n'ait pas a recroiser deux
    reponses."""
    source = Source(name="Astropolis", type=SourceType.INSTAGRAM, url="ig:astropolis",
                    categorie=SourceCategorie.FESTIVAL)
    db_session.add(source)
    db_session.commit()
    article = Article(source_id=source.id, url="https://t/1", title="Actu", raw_hash="h1",
                      published_at=MAINTENANT, media=[{"type": PHOTO, "url": "https://img/1.jpg"}])
    db_session.add(article)
    db_session.commit()
    post = Post(article_id=article.id, article_title="Actu", article_url="https://t/1", caption="L",
                template="kinda_post", slides=[COVER_EN_BASE],
                assets=[])
    db_session.add(post)
    db_session.commit()
    manifeste = [
        {"kind": "cover", "type": PHOTO, "fichier": "p_01.jpg", "ecarte": False,
         "origine": "article", "source_url": "https://img/f.jpg", "compte": None, "raison": None},
        {"kind": "media", "type": PHOTO, "fichier": None, "ecarte": True, "origine": "article",
         "source_url": "https://img/logo.jpg", "compte": None,
         "raison": "image trop petite pour le cadrage 4:5"},
    ]
    db_session.add(Creneau(
        publier_a=MAINTENANT + dt.timedelta(hours=1), article_id=article.id, post_id=post.id,
        etat=EtatCreneau.GELE, fichiers=["p_01.jpg"], elements=manifeste,
    ))
    db_session.commit()

    ligne = client.get("/relecture").json()[0]
    assert ligne["elements"] == manifeste
    assert [c["url"] for c in ligne["candidats"]] == ["https://img/1.jpg"]
    assert ligne["liste_arretee"] is False


def test_avant_le_gel_le_manifeste_est_inconnu_jamais_une_liste_inventee(client, db_session):
    """Un creneau PROGRAMME n'a rien de fige : on ne devine pas ce que le gel va
    ecarter. `None`, et le dashboard affiche « inconnu »."""
    post = Post(article_id=None, article_title="Actu", article_url="https://t/1", caption="L",
                template="kinda_post", slides=[COVER_EN_BASE])
    db_session.add(post)
    db_session.commit()
    db_session.add(Creneau(
        publier_a=MAINTENANT + dt.timedelta(hours=3), post_id=post.id, etat=EtatCreneau.PROGRAMME,
    ))
    db_session.commit()

    ligne = client.get("/relecture").json()[0]
    assert ligne["elements"] is None
    assert ligne["elements_figes"] is None


# --- Voir EXACTEMENT ce qui part : les fichiers figes eux-memes --------------


def _creneau_gele_avec_fichier(db, tmp_path, monkeypatch, nom="p_01.jpg"):
    from app.config import settings

    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    post = Post(article_id=None, article_title="Actu", article_url="https://t/1", caption="L",
                template="kinda_post", slides=[COVER_EN_BASE])
    db.add(post)
    db.commit()
    creneau = Creneau(publier_a=MAINTENANT + dt.timedelta(hours=1), post_id=post.id,
                      etat=EtatCreneau.GELE, fichiers=[nom])
    db.add(creneau)
    db.commit()
    dossier = tmp_path / str(creneau.id)
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / nom).write_bytes(_png(1080, 1350))
    return creneau


def test_le_fichier_fige_se_sert_tel_quel(client, db_session, tmp_path, monkeypatch):
    """« Je veux voir ce qui va etre poste EXACTEMENT. » Tout le reste DECRIT ce qui
    part ; ceci le MONTRE -- la cover rendue, les photos recadrees en 1080x1350. Le
    logo Wikipedia du post 164 etait coupe au point que les lettres manquaient :
    aucune description ne l'aurait dit."""
    creneau = _creneau_gele_avec_fichier(db_session, tmp_path, monkeypatch)
    reponse = client.get(f"/relecture/{creneau.id}/fichiers/p_01.jpg")
    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("nom", [
    "../../../etc/passwd",
    "..%2F..%2Fsecret.jpg",
    "autre.jpg",
])
def test_aucun_fichier_hors_du_manifeste_ne_se_sert(client, db_session, tmp_path, monkeypatch, nom):
    """Le nom n'est jamais CONSTRUIT, il est CHERCHE dans `creneaux.fichiers` -- la
    liste que le gel a ecrite lui-meme. Un nom absent rend 404 sans toucher au
    disque : ni `..`, ni chemin absolu, ni lien symbolique n'ouvrent quoi que ce
    soit."""
    creneau = _creneau_gele_avec_fichier(db_session, tmp_path, monkeypatch)
    (tmp_path / "secret.jpg").write_bytes(b"secret")
    assert client.get(f"/relecture/{creneau.id}/fichiers/{nom}").status_code == 404


def test_un_creneau_gele_est_desormais_EDITABLE(client, db_session):
    """CHANGEMENT DELIBERE du 2026-10-04. Un creneau GELE etait « a moitie
    editable » : la legende oui, la cover non, et il fallait cliquer « Rouvrir ».
    Le re-gel etant automatique, il se modifie entierement -- dire le contraire
    enverrait chercher un bouton qui n'existe plus.

    Ce qui peut encore refuser une edition de COVER se lit dans
    `raison_non_reouvrable` (trop pres du depart), jamais dans `editable` : la
    legende, elle, reste modifiable jusqu'au bout."""
    post = Post(article_id=None, article_title="Actu", article_url="https://t/1", caption="L",
                template="kinda_post", slides=[COVER_EN_BASE])
    db_session.add(post)
    db_session.commit()
    db_session.add(Creneau(publier_a=MAINTENANT + dt.timedelta(hours=1), post_id=post.id,
                           etat=EtatCreneau.GELE, fichiers=["p_01.jpg"]))
    db_session.commit()

    ligne = client.get("/relecture").json()[0]
    assert ligne["editable"] is True
    assert ligne["raison_non_editable"] is None
    assert ligne["reouvrable"] is True


@pytest.mark.parametrize("payload", [{"caption": "Autre legende"}, {"hashtags": ["#pop"]}])
def test_editer_la_legende_ne_jette_JAMAIS_les_fichiers_figes(client, db_session, payload):
    """PREUVE PAR MUTATION. Remplacer la condition de `_regel_automatique` par un
    `if True:` -- rouvrir a chaque PATCH -- laisse toute la suite verte sans ce
    test, et coute pourtant un gel entier pour une virgule : a moins de 20 min du
    depart, ce gel est refuse et le post est MANQUE.

    Ce qui justifie la distinction : `runner._publier` appelle `texte_legende(post)`
    en lisant la base JUSTE avant l'appel a Meta. La legende n'est donc jamais
    figee, et rien dans les fichiers n'en depend."""
    post = Post(article_id=None, article_title="Actu", article_url="https://t/1", caption="L",
                template="kinda_post", slides=[COVER_EN_BASE])
    db_session.add(post)
    db_session.commit()
    creneau = Creneau(publier_a=MAINTENANT + dt.timedelta(hours=1), post_id=post.id,
                      etat=EtatCreneau.GELE, fichiers=["p_01.jpg"],
                      elements=[{"kind": "cover", "fichier": "p_01.jpg", "ecarte": False}])
    db_session.add(creneau)
    db_session.commit()

    assert client.patch(f"/posts/{post.id}", json=payload).status_code == 200

    db_session.refresh(creneau)
    assert creneau.etat == EtatCreneau.GELE
    assert creneau.fichiers == ["p_01.jpg"]
    assert creneau.elements is not None
    assert creneau.erreur is None


def test_une_reclamation_de_l_agent_pendant_la_reouverture_fait_echouer_l_ecriture(db_session):
    """PREUVE PAR MUTATION, et c'est la garantie ANTI-DOUBLON.

    L'agent reclame un creneau par GELE -> ECHEC, COMMITTE avant tout appel a
    Meta. Si cette reclamation tombe entre notre lecture et notre ecriture, les
    gardes du debut de `rouvrir` ne la voient pas (elles lisent l'objet en
    memoire) : seule la condition `etat == GELE` de l'UPDATE l'arrete. La retirer
    laisse toute la suite verte -- d'ou ce test : le creneau redeviendrait
    PROGRAMME pendant que son conteneur part chez Meta, et un gel suivant pourrait
    le republier.
    """
    from sqlalchemy import update

    from app.models import Creneau as C
    from app.schedule.reouverture import ReouvertureRefusee, rouvrir

    post = Post(article_id=None, article_title="Actu", article_url="https://t/1", caption="L",
                template="kinda_post", slides=[COVER_EN_BASE])
    db_session.add(post)
    db_session.commit()
    creneau = Creneau(publier_a=MAINTENANT + dt.timedelta(hours=1), post_id=post.id,
                      etat=EtatCreneau.GELE, fichiers=["p_01.jpg"])
    db_session.add(creneau)
    db_session.commit()

    # L'agent reclame le creneau EN BASE, sans que notre objet en memoire le sache
    # -- exactement la course que l'ecriture conditionnelle existe pour perdre.
    db_session.execute(
        update(C).where(C.id == creneau.id).values(etat=EtatCreneau.ECHEC)
        .execution_options(synchronize_session=False)
    )
    db_session.commit()
    assert creneau.etat == EtatCreneau.GELE, "l'objet en memoire doit rester perime"

    with pytest.raises(ReouvertureRefusee, match="modifie entre-temps"):
        rouvrir(db_session, creneau, MAINTENANT)

    db_session.refresh(creneau)
    # La reclamation de l'agent tient : rien n'a ete rouvert, donc rien ne pourra
    # etre regele et republie derriere son conteneur.
    assert creneau.etat == EtatCreneau.ECHEC
    assert creneau.fichiers == ["p_01.jpg"]
