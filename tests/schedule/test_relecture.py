"""La fenetre de relecture humaine, et l'invariant qu'elle ne doit pas casser.

L'INVARIANT, qui gouverne tout ce fichier : **si personne ne relit, ca publie
quand meme a l'heure.** C'est la propriete dont la violation couterait le plus
cher, et elle est SILENCIEUSE -- un post qui attend une validation ressemble a un
post en cours. Le taux de publication est deja a moins de la moitie des creneaux
aboutis ; une file qui attend un humain l'amenerait a zero les jours ou l'humain
ne regarde pas.

`test_la_relecture_n_est_jamais_une_condition_de_publication` est la preuve par
mutation : il part d'un creneau dont le post n'a JAMAIS ete touche (statut
DRAFT, aucun `render_report`, aucune annonce posee) et exige qu'il parte. Une
garde de relecture ajoutee dans `_publier` -- la forme qu'un contributeur presse
ecrirait, `if post.status is PostStatus.DRAFT: continue` -- le fait rougir.
"""

import datetime as dt

import pytest

from app.config import settings
from app.models import (
    Article,
    Creneau,
    EtatCreneau,
    FormatCreneau,
    Post,
    PostStatus,
    Source,
    SourceCategorie,
    SourceType,
)
from app.schedule import relecture
from app.schedule.runner import RAISON_RECLAMATION, tourner

# RELATIVE A MAINTENANT, et c'est necessaire : `_en_vol` et le 409 de
# `app/api/posts.py` comparent `publier_a` a l'horloge REELLE (une reclamation de
# plus de 20 min n'est plus une publication en vol). Une date figee dans le passe
# rendrait ces tests verts aujourd'hui et rouges demain -- le depot a deja paye
# cette lecon sur les fenetres de retention.
BASE = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)


@pytest.fixture()
def actu(db_session):
    source = Source(
        name="Astropolis (IG)", type=SourceType.INSTAGRAM, url="ig:astropolis",
        categorie=SourceCategorie.FESTIVAL,
    )
    db_session.add(source)
    db_session.commit()
    article = Article(
        source_id=source.id, url="https://a/1", title="Astropolis devoile sa programmation",
        raw_hash="r1", relevance_score=88, hotness_score=40,
        published_at=BASE - dt.timedelta(hours=2),
        media=[{"type": "photo", "url": "https://img/scene.jpg"}],
    )
    db_session.add(article)
    db_session.commit()
    return article


def _post(db, article, **extra):
    post = Post(
        article_id=article.id, article_title=article.title, article_url=article.url,
        caption="Trois jours a Brest fin aout.", hashtags=["Electro", "electro", "brest"],
        slides=[{
            "kind": "cover", "image_url": "https://img/scene.jpg",
            # Les trois textes : `CoverIn` les exige tous (gabarit post ET reel).
            "headline": "**Astropolis** revient", "title": "**Astropolis**", "subtitle": "**revient** a Brest",
        }],
        template="kinda_post", credit="@astropolis",
        # Les quatre variantes, comme `create_post` les pose : `variante_retenue`
        # est borne sur cette liste.
        variants={"variantes": [{
            "headline": "**Astropolis** revient", "title": "**Astropolis**",
            "subtitle": "**revient** a Brest",
        }], "retenue": None, "fond": {"origine": "article"}},
        # Un actif reel : sans fond extractible, `_brouillon_perime` relache le
        # creneau avant meme d'appeler le preparateur.
        assets=[{"type": "photo", "url": "https://img/scene.jpg", "origine": "article"}],
        **extra,
    )
    db.add(post)
    db.commit()
    return post


def _creneau(db, article, post, *, publier_a, etat=EtatCreneau.GELE, **extra):
    # EPINGLE par defaut : `synchroniser_creneaux` supprime un creneau MODIFIABLE
    # dont l'heure n'est pas dans la grille calculee, et ces heures-ci sont
    # choisies a la main. Un creneau rouvert pour relecture est de toute facon
    # epingle en production (voir `rouvrir_creneau`).
    extra.setdefault("epingle", True)
    creneau = Creneau(
        publier_a=publier_a, article_id=article.id, post_id=post.id, etat=etat,
        format=FormatCreneau.CARROUSEL, **extra,
    )
    db.add(creneau)
    db.commit()
    return creneau


# --- L'invariant : la relecture n'est jamais une condition ------------------


class Outils:
    """Les outils du passage, factices -- aucun reseau, aucun appel paye."""

    def __init__(self):
        self.publies, self.notifications = [], []

    def composer(self, article):  # pragma: no cover -- aucun test d'ici ne compose
        raise AssertionError("aucune composition attendue")

    def preparer(self, post, article, dossier):
        dossier.mkdir(parents=True, exist_ok=True)
        return ["p_01.jpg"]

    def publier(self, dossier, noms, legende, publier_vraiment, **_crochets):
        self.publies.append((noms, legende, publier_vraiment))
        return "ig-relecture"

    def notifier(self, titre, message):
        self.notifications.append(message)

    def tourner(self, db, maintenant, mode="reel"):
        return tourner(
            db, maintenant, mode=mode, composer=self.composer, preparer=self.preparer,
            publier=self.publier, notifier=self.notifier, horloge=lambda: maintenant,
        )


def test_la_relecture_n_est_jamais_une_condition_de_publication(db_session, actu):
    """PREUVE PAR MUTATION. Le post n'a jamais ete ouvert : statut DRAFT, aucun
    `render_report`, aucune annonce posee. Il doit partir quand meme.

    Mutation qui doit faire rougir ce test (verifiee a la main) : ajouter dans
    `runner._publier`, apres la lecture du post,
        if post.status is PostStatus.DRAFT: continue
    c'est-a-dire « on attend une validation ». Rien d'autre dans la suite ne
    rougit -- d'ou ce test.
    """
    post = _post(db_session, actu)
    assert post.status is PostStatus.DRAFT and post.render_report is None
    creneau = _creneau(db_session, actu, post, publier_a=BASE, fichiers=["p_01.jpg"])
    assert not relecture.deja_annonce(creneau.id, post.id, BASE)

    outils = Outils()
    rapport = outils.tourner(db_session, BASE + dt.timedelta(minutes=2))

    assert rapport.publies == 1, "un post non relu doit partir a son heure"
    assert outils.publies and outils.publies[0][2] is True
    db_session.refresh(creneau)
    assert creneau.etat is EtatCreneau.PUBLIE
    db_session.refresh(post)
    assert post.status is PostStatus.PUBLISHED


def test_une_annonce_de_relecture_en_panne_ne_retient_aucune_publication(db_session, actu, monkeypatch):
    """L'annonce est un canal LATERAL : si elle explose, le post part quand meme.

    `etape` isole chaque etape du passage, et l'annonce est posee comme les
    autres : c'est ce que ce test verrouille, parce qu'une annonce placee hors de
    cette enveloppe emporterait la publication d'un creneau deja gele.
    """
    post = _post(db_session, actu)
    creneau = _creneau(db_session, actu, post, publier_a=BASE, fichiers=["p_01.jpg"])

    def annonce_en_panne(*_args, **_kwargs):
        raise RuntimeError("fichier d'etat illisible")

    monkeypatch.setattr("app.schedule.runner._annoncer_relecture", annonce_en_panne)

    outils = Outils()
    rapport = outils.tourner(db_session, BASE + dt.timedelta(minutes=2))

    assert rapport.publies == 1
    db_session.refresh(creneau)
    assert creneau.etat is EtatCreneau.PUBLIE
    assert any("relecture" in message for message in rapport.erreurs)


# --- L'annonce --------------------------------------------------------------


def test_un_post_qui_part_bientot_est_annonce_une_seule_fois(db_session, actu):
    """Declenchee par l'HORLOGE, pas par le gel : le creneau d'ici est seulement
    PROGRAMME (jamais gele) et il est quand meme annonce. C'est la mesure du
    2026-10-04 qui l'impose -- 14 % des creneaux reellement geles l'ont ete a
    moins de cinq minutes de leur depart."""
    post = _post(db_session, actu)
    depart = BASE + dt.timedelta(minutes=90)
    creneau = _creneau(db_session, actu, post, publier_a=depart, etat=EtatCreneau.PROGRAMME)

    outils = Outils()
    rapport = outils.tourner(db_session, BASE)
    assert rapport.annonces_relecture == 1
    assert any("relecture" in m and "Astropolis" in m for m in outils.notifications)
    # La console s'ouvre sur ce panneau depuis le 2026-10-31.
    assert any("panneau Programmation" in m for m in outils.notifications)
    assert relecture.deja_annonce(creneau.id, post.id, BASE)

    # Dix minutes plus tard, le passage suivant : plus rien. Six annonces par post
    # est la facon la plus sure d'apprendre a ignorer les notifications.
    outils.notifications.clear()
    rapport = outils.tourner(db_session, BASE + dt.timedelta(minutes=10))
    assert rapport.annonces_relecture == 0
    assert not any("relecture" in m for m in outils.notifications)


def test_un_post_hors_de_la_fenetre_n_est_pas_annonce(db_session, actu):
    """Compose a J-1, il n'est pas annonce douze heures d'avance : l'annonce est
    un rappel a portee de depart, la LISTE (`GET /relecture`) est la verite."""
    post = _post(db_session, actu)
    _creneau(
        db_session, actu, post,
        publier_a=BASE + dt.timedelta(hours=12), etat=EtatCreneau.PROGRAMME,
    )
    rapport = Outils().tourner(db_session, BASE)
    assert rapport.annonces_relecture == 0


def test_un_etat_illisible_n_empeche_pas_d_annoncer(db_session, actu):
    """Un fichier d'etat tronque vaut « rien n'a ete annonce » : au pire on
    annonce une fois de trop. L'inverse -- taire une annonce -- est ce qui
    coute, et c'est pour ca qu'on ne leve pas."""
    chemin = settings.meta_token_path.parent / "relecture.json"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("{ ceci n'est pas du JSON")
    post = _post(db_session, actu)
    _creneau(
        db_session, actu, post,
        publier_a=BASE + dt.timedelta(minutes=30), etat=EtatCreneau.PROGRAMME,
    )
    assert Outils().tourner(db_session, BASE).annonces_relecture == 1


def test_les_annonces_echues_sont_purgees_a_l_ecriture(db_session):
    """Sans cette purge, le fichier grossirait indefiniment -- meme defaut que
    les compteurs abandonnes de `pause_visuels`."""
    relecture.marquer_annonce(1, 10, BASE)
    relecture.marquer_annonce(2, 20, BASE + dt.timedelta(days=2))
    assert not relecture.deja_annonce(1, 10, BASE + dt.timedelta(days=2))
    assert relecture.deja_annonce(2, 20, BASE + dt.timedelta(days=2))


# --- GET /relecture : ce qui va PARTIR --------------------------------------


def test_la_liste_montre_le_texte_publie_et_non_la_legende_en_base(client, db_session, actu):
    """Le defaut que cet endpoint existe pour supprimer : la legende EDITEE n'est
    pas la legende PUBLIEE. `texte_legende` remet le diese sur les hashtags, les
    deduplique et ajoute la ligne de credit -- rien de tout ca n'etait visible."""
    post = _post(db_session, actu)
    _creneau(db_session, actu, post, publier_a=BASE + dt.timedelta(hours=3), etat=EtatCreneau.PROGRAMME)

    lignes = client.get("/relecture").json()
    assert len(lignes) == 1
    depart = lignes[0]
    assert depart["post_id"] == post.id
    # Le diese est pose, le doublon de casse est tombe (« electro » apres
    # « Electro »), et le credit est la -- trois transformations invisibles
    # jusqu'ici dans la zone de texte de la fiche.
    assert "#Electro" in depart["legende_publiee"]
    assert depart["hashtags_publies"] == ["#Electro", "#brest"]
    assert "📸 @astropolis" in depart["legende_publiee"]
    assert depart["credit"] == "@astropolis"
    assert depart["legende_longueur"] == len(depart["legende_publiee"])
    assert depart["legende_maximum"] == 2150


def test_la_relecture_ne_rend_plus_le_signal_mort_de_debordement(client, db_session, actu):
    """Decision du 2026-10-30 : `render_report` n'est ecrit par aucun bouton, donc
    ce champ valait « inconnu » presque toujours, a cote de la mesure vivante de
    `/covers`. Son intention tient toujours : aucune affirmation sur une cover que
    cet endpoint n'a pas mesuree -- ni « inconnu », ni « elle tient »."""
    post = _post(db_session, actu)
    post.render_report = {"slides": [{"overflow": True, "scale": 0.6, "lines": 3}]}
    db_session.commit()
    _creneau(db_session, actu, post, publier_a=BASE + dt.timedelta(hours=3), etat=EtatCreneau.PROGRAMME)
    assert "deborde" not in client.get("/relecture").json()[0]


def test_une_publication_en_vol_est_visible_et_dit_pourquoi_rien_ne_bouge(client, db_session, actu):
    """Pendant les minutes ou le conteneur existe chez Meta, le creneau est en
    ECHEC avec la raison de reclamation. Le masquer donnerait l'impression que le
    post a disparu au moment ou il est le plus interessant a regarder."""
    post = _post(db_session, actu)
    _creneau(
        db_session, actu, post, publier_a=BASE + dt.timedelta(minutes=5),
        etat=EtatCreneau.ECHEC, erreur=RAISON_RECLAMATION,
    )
    depart = client.get("/relecture").json()[0]
    assert depart["editable"] is False
    assert depart["reouvrable"] is False
    assert "publication en cours" in depart["raison_non_editable"]


def test_un_echec_pour_de_bon_n_est_pas_une_relecture(client, db_session, actu):
    """Rien n'en partira : il appartient au tableau de la file, pas a la
    relecture."""
    post = _post(db_session, actu)
    _creneau(
        db_session, actu, post, publier_a=BASE - dt.timedelta(hours=5),
        etat=EtatCreneau.ECHEC, erreur="echec de publication : Meta a refuse",
    )
    assert client.get("/relecture").json() == []


# --- L'edition pendant la fenetre ------------------------------------------


def test_la_legende_reste_modifiable_sur_un_creneau_gele(client, db_session, actu):
    """LE correctif du chantier. `runner._publier` appelle `texte_legende(post)`
    juste avant l'appel a Meta, en lisant la base : la legende n'est donc JAMAIS
    figee. Le 409 qui la bloquait fermait la porte exactement dans la fenetre ou
    l'utilisateur veut corriger -- une sur-portee, pas une protection."""
    post = _post(db_session, actu)
    _creneau(db_session, actu, post, publier_a=BASE + dt.timedelta(hours=1), fichiers=["p_01.jpg"])

    reponse = client.patch(
        f"/posts/{post.id}", json={"caption": "Programmation devoilee a Brest.", "hashtags": ["astropolis"]}
    )
    assert reponse.status_code == 200, reponse.text
    db_session.refresh(post)
    assert post.caption == "Programmation devoilee a Brest."


def test_editer_la_cover_d_un_creneau_gele_rouvre_le_gel_sans_rien_demander(
    client, db_session, actu
):
    """CHANGEMENT DELIBERE du 2026-10-04 : le re-gel est AUTOMATIQUE.

    Les fichiers figes PORTENT la cover rendue, donc la changer SANS les refaire
    publierait autre chose que ce qu'on voit -- c'est ce que le 409 protegeait. Mais
    il demandait a l'utilisateur de cliquer « Rouvrir » pour obtenir ce qu'il venait
    justement de demander : « aucun bouton a cliquer, et l'etat affiche est toujours
    celui qui partira ». L'edition passe donc, et c'est le GEL qui est invalide --
    fichiers ET manifeste oublies, creneau epingle, marqueur de relecture pose."""
    post = _post(db_session, actu)
    creneau = _creneau(
        db_session, actu, post, publier_a=BASE + dt.timedelta(hours=1),
        fichiers=["p_01.jpg"], elements=[{"kind": "cover", "fichier": "p_01.jpg", "ecarte": False}],
    )

    assert client.patch(f"/posts/{post.id}", json={"variante_retenue": 0}).status_code == 200

    db_session.refresh(creneau)
    assert creneau.etat == EtatCreneau.PROGRAMME
    assert (creneau.fichiers, creneau.elements) == (None, None)
    assert creneau.erreur == relecture.RAISON_RELECTURE
    assert creneau.post_id == post.id


def test_rien_ne_se_modifie_pendant_une_publication_en_vol(client, db_session, actu):
    """Le conteneur est parti chez Meta avec son texte : meme la legende est hors
    de portee, parce qu'elle a DEJA ete lue."""
    post = _post(db_session, actu)
    _creneau(
        db_session, actu, post, publier_a=BASE + dt.timedelta(minutes=5),
        etat=EtatCreneau.ECHEC, erreur=RAISON_RECLAMATION,
    )
    assert client.patch(f"/posts/{post.id}", json={"caption": "trop tard"}).status_code == 409
    assert client.patch(f"/posts/{post.id}", json={"variante_retenue": 0}).status_code == 409


# --- Rouvrir, et re-geler --------------------------------------------------


def test_rouvrir_invalide_le_gel_epingle_et_laisse_le_gel_refaire(client, db_session, actu, tmp_path):
    post = _post(db_session, actu)
    creneau = _creneau(db_session, actu, post, publier_a=BASE + dt.timedelta(hours=1), fichiers=["p_01.jpg"])
    dossier = settings.publication_dir / str(creneau.id)
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "p_01.jpg").write_bytes(b"ancien")

    reponse = client.post(f"/schedule/{creneau.id}/rouvrir")
    assert reponse.status_code == 200, reponse.text
    db_session.refresh(creneau)
    assert creneau.etat is EtatCreneau.PROGRAMME
    assert creneau.fichiers is None, "les fichiers figes ne decrivent plus ce qui partira"
    # EPINGLE : sans ca, `appliquer_arbitrage` remplacerait dans les 10 minutes le
    # post que l'utilisateur vient de rouvrir pour le corriger.
    assert creneau.epingle is True
    assert relecture.RAISON_RELECTURE in creneau.erreur
    assert not dossier.exists(), "des fichiers orphelins seraient republies tels quels"

    # La cover redevient modifiable, et le gel du passage suivant refait les fichiers.
    assert client.patch(f"/posts/{post.id}", json={"variante_retenue": 0}).status_code == 200
    Outils().tourner(db_session, BASE)
    db_session.refresh(creneau)
    assert creneau.etat is EtatCreneau.GELE and creneau.fichiers == ["p_01.jpg"]


def test_rouvrir_est_refuse_trop_pres_du_depart_avec_sa_raison(client, db_session, actu):
    """Rouvrir jette un post PRET ; sous le delai de re-gel, ce serait l'echanger
    contre un creneau manque. Le refus DIT pourquoi -- jamais un bouton gris."""
    post = _post(db_session, actu)
    creneau = _creneau(db_session, actu, post, publier_a=BASE, fichiers=["p_01.jpg"])
    # `publier_a` est a l'instant : il reste zero minute, loin des 20 exigees.
    reponse = client.post(f"/schedule/{creneau.id}/rouvrir")
    assert reponse.status_code == 409
    assert str(settings.publication_relecture_regel_minutes) in reponse.json()["detail"]
    db_session.refresh(creneau)
    assert creneau.etat is EtatCreneau.GELE and creneau.fichiers == ["p_01.jpg"]


def test_rouvrir_refuse_une_publication_en_vol(client, db_session, actu):
    """Le garde-fou anti-doublon : un creneau reclame est en ECHEC, donc hors de
    la condition `etat == GELE` de l'ecriture -- et on le dit explicitement."""
    post = _post(db_session, actu)
    creneau = _creneau(
        db_session, actu, post, publier_a=BASE + dt.timedelta(minutes=5),
        etat=EtatCreneau.ECHEC, erreur=RAISON_RECLAMATION,
    )
    reponse = client.post(f"/schedule/{creneau.id}/rouvrir")
    assert reponse.status_code == 409
    assert "Publication en cours" in reponse.json()["detail"]
    db_session.refresh(creneau)
    assert creneau.etat is EtatCreneau.ECHEC


def test_un_regel_impossible_apres_relecture_ne_brule_pas_l_article(db_session, actu):
    """« Ne perds pas le post. » Une accroche rallongee d'un caractere de trop
    fait deborder la cover (`PreparationImpossible`), et sans ce traitement
    l'article serait exclu A VIE pour une faute de frappe de l'utilisateur. Le
    creneau se libere, l'article reste eligible, le post survit."""
    from app.publish.preparation import PreparationImpossible

    post = _post(db_session, actu)
    creneau = _creneau(
        db_session, actu, post, publier_a=BASE + dt.timedelta(hours=1),
        etat=EtatCreneau.PROGRAMME, erreur=relecture.RAISON_RELECTURE,
    )

    outils = Outils()

    def gel_refuse(post_, article_, dossier):
        raise PreparationImpossible("accroche trop longue : la cover deborde")

    outils.preparer = gel_refuse
    creneau_id = creneau.id
    outils.tourner(db_session, BASE)

    db_session.refresh(actu)
    assert actu.file_exclu_raison is None, "l'article ne doit pas etre exclu a vie"
    assert db_session.get(Post, post.id) is not None, "le post survit, l'utilisateur le reprend"
    # Le creneau a LACHE le post. Il peut aussi avoir disparu : libere, il
    # redevient MODIFIABLE et `synchroniser_creneaux` balaie une heure qui n'est
    # pas dans la grille calculee -- ce qui n'arrive pas en production, ou les
    # heures viennent justement de cette grille. Ce qui compte ici est que ni
    # l'article ni le post n'aient ete perdus.
    restant = db_session.get(Creneau, creneau_id)
    assert restant is None or (restant.etat is EtatCreneau.LIBRE and restant.post_id is None)


def test_sans_relecture_un_gel_impossible_exclut_toujours(db_session, actu):
    """La contrepartie, et c'est la protection d'avant : hors reouverture
    manuelle, un gel impossible exclut l'article comme il l'a toujours fait. Le
    traitement d'au-dessus ne doit pas s'etre repandu a tous les gels."""
    from app.publish.preparation import PreparationImpossible

    post = _post(db_session, actu)
    _creneau(
        db_session, actu, post, publier_a=BASE + dt.timedelta(hours=1), etat=EtatCreneau.PROGRAMME
    )
    outils = Outils()

    def gel_refuse(post_, article_, dossier):
        raise PreparationImpossible("cover inexploitable")

    outils.preparer = gel_refuse
    outils.tourner(db_session, BASE)
    db_session.refresh(actu)
    assert actu.file_exclu_raison is not None


def test_le_marqueur_de_relecture_survit_a_un_gel_differe(db_session, actu):
    """Sans la conservation du marqueur en TETE, un premier gel differe (pause
    yt-dlp, budget de telechargement) effacerait la protection et le second echec
    exclurait l'article que l'utilisateur vient d'editer."""
    from app.publish.preparation import PreparationDifferee, PreparationImpossible

    post = _post(db_session, actu)
    creneau = _creneau(
        db_session, actu, post, publier_a=BASE + dt.timedelta(hours=1),
        etat=EtatCreneau.PROGRAMME, erreur=relecture.RAISON_RELECTURE,
    )
    outils = Outils()
    outils.preparer = lambda p, a, d: (_ for _ in ()).throw(PreparationDifferee("yt-dlp en pause"))
    outils.tourner(db_session, BASE)
    db_session.refresh(creneau)
    assert relecture.RAISON_RELECTURE in creneau.erreur

    outils.preparer = lambda p, a, d: (_ for _ in ()).throw(PreparationImpossible("la cover deborde"))
    outils.tourner(db_session, BASE + dt.timedelta(minutes=10))
    db_session.refresh(actu)
    assert actu.file_exclu_raison is None


# --- Le voyant --------------------------------------------------------------


def test_le_voyant_de_relecture_ne_crie_pas_au_loup(db_session, actu):
    """Un post qui attend une relecture n'est PAS une alerte : il part quand meme.
    Le bandeau a deja paye cette lecon le 2026-10-02 avec un voyant de
    composition qui criait au loup."""
    from app.supervision import OK, voyant_relecture

    post = _post(db_session, actu)
    _creneau(
        db_session, actu, post, publier_a=BASE + dt.timedelta(minutes=45),
        etat=EtatCreneau.PROGRAMME,
    )
    voyant = voyant_relecture(db_session, BASE)
    assert voyant.niveau == OK
    assert "1 à relire" in voyant.valeur

    # Decision du 2026-10-30 : `render_report` ne fait plus bouger ce voyant. Le
    # gel refuse de toute facon une cover qui deborde (`preparation.py`), donc
    # « partiront ainsi » etait faux ; la vraie mesure est `/covers`, dans la fiche.
    post.render_report = {"slides": [{"overflow": True}]}
    db_session.commit()
    voyant = voyant_relecture(db_session, BASE)
    assert voyant.niveau == OK
    assert not any("deborde" in ligne for ligne in voyant.details)


def test_le_voyant_dit_rien_en_attente_quand_la_fenetre_est_vide(db_session):
    from app.supervision import OK, voyant_relecture

    voyant = voyant_relecture(db_session, BASE)
    assert voyant.niveau == OK and "rien" in voyant.valeur


def test_le_bandeau_porte_le_voyant_de_relecture(client):
    cles = [v["cle"] for v in client.get("/supervision").json()["voyants"]]
    assert "relecture" in cles


def test_la_fenetre_ne_depend_que_de_l_horloge():
    """Pure, et c'est le point : la mesure du 2026-10-04 dit que 14 % des gels se
    font a moins de cinq minutes du depart, donc une fenetre accrochee au gel
    n'existerait pas pour eux."""
    assert relecture.dans_la_fenetre(BASE + dt.timedelta(minutes=30), BASE)
    assert not relecture.dans_la_fenetre(BASE + dt.timedelta(hours=10), BASE)
    assert not relecture.dans_la_fenetre(BASE - dt.timedelta(minutes=1), BASE), "deja passe"


def test_le_delai_de_regel_laisse_deux_passages_de_l_agent():
    assert settings.publication_relecture_regel_minutes >= 20, (
        "il faut au moins deux passages de l'agent (2 x 10 min) : un premier gel "
        "differe ne doit pas etre fatal"
    )
    assert relecture.regel_possible(BASE + dt.timedelta(minutes=25), BASE)
    assert not relecture.regel_possible(BASE + dt.timedelta(minutes=5), BASE)
