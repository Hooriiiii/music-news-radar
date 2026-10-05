"""Televerser un visuel depuis le disque : la derniere piece du controle editorial.

L'editeur de relecture ne proposait que ce que le PIPELINE avait trouve. Quand la
bonne image n'y est pas, l'utilisateur veut deposer un fichier de son Mac. Ce
depot produit UNE ENTREE DE PLUS dans `candidats` -- rien d'autre : la mettre dans
la liste publiee passe par `PATCH /posts/{id}`, qui rouvre deja le creneau tout
seul.

LA PROPRIETE LA PLUS FRAGILE D'ICI, et la seule dont la violation serait
SILENCIEUSE et grave : le nom du fichier depose ne doit jamais pouvoir designer un
emplacement hors du cache. Elle est verrouillee par mutation
(`test_aucun_nom_fourni_ne_compose_le_chemin_sur_disque`).
"""

import datetime as dt
import io

import pytest
from PIL import Image

from app.config import settings
from app.models import Article, Creneau, EtatCreneau, FormatCreneau, Post, Source, SourceType
from app.publish import televersement
from app.publish.preparation import candidats_editables
from app.publish.televersement import DepotRefuse, accepter, chemin_depose

MAINTENANT = dt.datetime.now(dt.timezone.utc)

COVER = {"kind": "cover", "headline": "**A**", "title": "**T**", "subtitle": "**S**",
         "image_url": "https://img/fond.jpg"}


def image(largeur=1200, hauteur=1500, format_="PNG", mode="RGB"):
    tampon = io.BytesIO()
    Image.new(mode, (largeur, hauteur), (12, 34, 56)).save(tampon, format_)
    return tampon.getvalue()


def _post(db, *, slides=None, assets=None):
    source = Source(name="Presse", type=SourceType.RSS, url="https://presse/rss")
    db.add(source)
    db.flush()
    article = Article(
        source_id=source.id, url="https://presse/a", title="Une actu", raw_hash="h-depot",
        published_at=MAINTENANT - dt.timedelta(hours=2), fetched_at=MAINTENANT, media=[],
    )
    db.add(article)
    db.flush()
    post = Post(
        article_id=article.id, article_title=article.title, article_url=article.url,
        template="kinda_post", caption="Legende", hashtags=["#techno"],
        slides=slides or [COVER], assets=assets, variants={"fond": {"origine": "article"}},
    )
    db.add(post)
    db.commit()
    return post


# --- Ce qui est accepte, et ce qui est repare plutot que refuse ---------------


def test_une_photo_ordinaire_est_acceptee_et_copiee_dans_le_cache():
    actif = accepter(image(1200, 1500))
    assert actif["type"] == "photo"
    assert actif["origine"] == "depot"
    assert actif["fichier"] == actif["url"]
    chemin = chemin_depose(televersement.normaliser(image(1200, 1500)))
    assert chemin.is_file()
    assert chemin.parent == settings.visuels_cache_dir
    # Les dimensions rendues sont celles du fichier GARDE, pas de la source brute.
    assert (actif["width"], actif["height"]) == (1200, 1500)


def test_le_fichier_garde_est_un_jpeg_a_la_resolution_d_origine():
    """Pas de recadrage au depot : le gel recadre, sur les seuils du moment."""
    jpeg = televersement.normaliser(image(1600, 1000))
    with Image.open(io.BytesIO(jpeg)) as ouverte:
        assert ouverte.format == "JPEG"
        assert ouverte.size == (1600, 1000)


def test_un_png_transparent_est_repare_jamais_refuse():
    """Ce que le pipeline sait reparer n'est pas un motif de refus."""
    actif = accepter(image(1200, 1500, format_="PNG", mode="RGBA"))
    assert actif["type"] == "photo"


def test_un_16_9_de_presse_passe_comme_dans_le_gel():
    """55 % de perte au recadrage : sous le plafond de 60 %, donc accepte --
    le format de presse le plus courant du pipeline ne doit pas etre refuse."""
    assert accepter(image(1600, 900))["width"] == 1600


# --- Un refus dit POURQUOI, et AVANT le gel ----------------------------------


def test_trop_petite_le_message_nomme_le_seuil_qui_a_mordu():
    with pytest.raises(DepotRefuse, match="agrandir"):
        accepter(image(200, 250))


def test_trop_eloignee_du_4_5_le_message_nomme_l_autre_seuil():
    with pytest.raises(DepotRefuse, match="recadrage"):
        accepter(image(4000, 300))


def test_les_seuils_sont_ceux_de_la_configuration_pas_des_copies(monkeypatch):
    """Meme verdict que le gel, parce que c'est la MEME fonction et les MEMES
    reglages : abaisser le plafond d'agrandissement doit refuser au depot ce que
    le gel ecarterait."""
    petite = image(600, 750)
    assert accepter(petite)["width"] == 600
    monkeypatch.setattr(settings, "publication_crop_upscale_max", 1.1)
    with pytest.raises(DepotRefuse, match="agrandir"):
        accepter(petite)


@pytest.mark.parametrize(
    ("octets", "motif"),
    [
        (b"%PDF-1.7\nblabla", "PDF"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "SVG"),
        (b"PK\x03\x04" + b"\x00" * 40, "archive"),
        (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 40, "HEIC"),
        (b"", "vide"),
        (b"ceci n'est pas une image", "illisible"),
    ],
)
def test_ce_qui_n_est_pas_une_image_est_nomme_pas_renvoye_en_illisible(octets, motif):
    """Un refus sur lequel l'utilisateur peut AGIR. Le HEIC est le premier
    candidat sur un Mac (format par defaut de l'iPhone) : « illisible » l'aurait
    laisse sans remede."""
    with pytest.raises(DepotRefuse, match=motif):
        accepter(octets)


def test_le_type_reel_prime_sur_tout_ce_qu_on_pourrait_declarer():
    """Le format vient du DECODAGE. Un PDF renomme en .jpg, annonce
    `image/jpeg`, reste un PDF : ni l'extension ni l'en-tete ne sont lus."""
    with pytest.raises(DepotRefuse, match="PDF"):
        accepter(b"%PDF-1.7\n" + b"\xff\xd8\xff" * 100)


def test_une_bombe_de_decompression_est_refusee_sur_l_EN_TETE(monkeypatch):
    """25 Mo de PNG peuvent decoder en plusieurs gigaoctets : la borne de pixels
    est lue dans l'en-tete, AVANT tout decodage."""
    monkeypatch.setattr(televersement, "PIXELS_MAX", 1000)
    with pytest.raises(DepotRefuse, match="Mpx|trop grande"):
        accepter(image(1200, 1500))


def test_un_fichier_trop_lourd_est_refuse_meme_hors_HTTP(monkeypatch):
    """Ceinture du module : l'endpoint borne le flux, mais un script ou un test
    qui appelle `accepter` directement doit rencontrer la meme limite."""
    monkeypatch.setattr(televersement, "TAILLE_MAX_OCTETS", 10)
    with pytest.raises(DepotRefuse, match="trop lourd"):
        accepter(image(1200, 1500))


# --- La garde de chemin, prouvee par mutation --------------------------------


@pytest.mark.parametrize(
    "nom",
    ["../../../../tmp/evade.jpg", "/tmp/absolu.jpg", "..%2F..%2Fevade.jpg", "..\\..\\evade.jpg"],
)
def test_aucun_nom_fourni_ne_compose_le_chemin_sur_disque(client, db_session, nom, tmp_path):
    """LA propriete dont la violation serait silencieuse ET grave.

    On envoie le nom par TOUS les canaux qu'un client peut emprunter -- chaine de
    requete, `Content-Disposition`, en-tete maison -- et le fichier doit atterrir
    dans `visuels_cache_dir` sous un nom derive du CONTENU.

    MUTATION VERIFIEE : remplacer dans `televersement.chemin_depose` le nom
    content-adresse par un nom tire de l'appelant (ce qu'un contributeur presse
    ecrirait pour « garder le nom d'origine ») fait rougir ce test, et lui seul.
    """
    post = _post(db_session)
    octets = image(1200, 1500)
    reponse = client.post(
        f"/posts/{post.id}/visuels?nom={nom}",
        content=octets,
        headers={
            "Content-Type": "image/jpeg",
            "Content-Disposition": f'attachment; filename="{nom}"',
            "X-Filename": nom,
        },
    )
    assert reponse.status_code == 201, reponse.text
    attendu = chemin_depose(televersement.normaliser(octets))
    fichiers = sorted(p.name for p in settings.visuels_cache_dir.iterdir())
    assert fichiers == [attendu.name]
    # Le nom sur disque est le SHA-256 du contenu, pas un fragment du nom recu.
    assert attendu.stem.isalnum() and len(attendu.stem) == 32
    assert reponse.json()["url"] == attendu.resolve().as_uri()
    assert not (tmp_path / "evade.jpg").exists()


def test_le_depot_ne_prend_qu_un_bytes_aucun_nom_a_porter():
    """Par CONSTRUCTION et non par vigilance : la fonction qui nomme le fichier
    n'a qu'un seul parametre, les octets. Un nom ne peut pas composer un chemin
    parce qu'il n'y en a pas."""
    import inspect

    assert list(inspect.signature(chemin_depose).parameters) == ["jpeg"]
    assert list(inspect.signature(accepter).parameters) == ["octets"]


def test_la_copie_locale_n_est_relue_que_dans_le_cache():
    """La ceinture d'en face : meme si un `file://` hors du cache arrivait dans
    une slide, le gel ne le lirait pas (`visuels.lire_fichier_cache`)."""
    from app.content.visuels import lire_fichier_cache

    actif = accepter(image(1200, 1500))
    assert lire_fichier_cache(actif["url"]) is not None
    assert lire_fichier_cache("file:///etc/hosts") is None
    assert lire_fichier_cache(
        settings.visuels_cache_dir.resolve().as_uri() + "/../../../etc/hosts"
    ) is None


# --- Le visuel depose devient un candidat, et rien de plus -------------------


def test_le_depot_ajoute_un_candidat_et_ne_touche_pas_la_liste(client, db_session):
    post = _post(db_session)
    reponse = client.post(f"/posts/{post.id}/visuels", content=image(1200, 1500))
    assert reponse.status_code == 201
    candidat = reponse.json()
    db_session.refresh(post)
    # Un actif de plus...
    assert [a["origine"] for a in post.assets] == ["depot"]
    # ... donc un candidat de plus, de la MEME forme que les autres.
    article = db_session.get(Article, post.article_id)
    reserve = candidats_editables(post, article)
    assert candidat in reserve
    assert set(candidat) == set(reserve[0])
    # ... et la liste publiee est INCHANGEE : l'ajouter est le geste suivant.
    assert post.slides == [COVER]
    assert not (post.variants or {}).get("carrousel")


def test_le_candidat_depose_est_consommable_tel_quel_par_le_gel(client, db_session):
    """Le bout en bout qui compte : ce que le depot rend, mis dans `slides` par
    `PATCH /posts/{id}`, doit se retrouver dans `medias_du_carrousel` -- sinon le
    visuel serait accepte puis invisible."""
    from app.publish.preparation import medias_du_carrousel

    post = _post(db_session)
    candidat = client.post(f"/posts/{post.id}/visuels", content=image(1200, 1500)).json()
    patch = client.patch(
        f"/posts/{post.id}",
        json={"slides": [COVER, candidat], "carrousel_explicite": True},
    )
    assert patch.status_code == 200, patch.text
    db_session.refresh(post)
    medias = medias_du_carrousel(post, db_session.get(Article, post.article_id))
    assert [m["url"] for m in medias] == [candidat["url"]]


def test_un_depot_identique_ne_cree_pas_un_second_fichier(client, db_session):
    """Content-adresse : redeposer la meme image reecrit le meme fichier -- et
    rafraichit son `mtime`, ce qui la met a l'abri de la purge du cache."""
    post = _post(db_session)
    octets = image(1200, 1500)
    une = client.post(f"/posts/{post.id}/visuels", content=octets).json()
    deux = client.post(f"/posts/{post.id}/visuels", content=octets).json()
    assert une["url"] == deux["url"]
    assert len(list(settings.visuels_cache_dir.iterdir())) == 1


def test_le_fichier_depose_survit_a_la_purge_du_cache():
    """La purge tourne a CHAQUE passage de l'agent de publication, donc entre le
    depot et le gel (T-2h). Elle supprime sur le `mtime` : un depot frais est
    intact, et c'est la propriete qui empeche un visuel de disparaitre sous son
    creneau."""
    from app.content.visuels import purger_cache

    actif = accepter(image(1200, 1500))
    assert purger_cache(MAINTENANT) == 0
    assert chemin_depose(televersement.normaliser(image(1200, 1500))).is_file()
    # ... et il part bien une fois perime, comme tous les autres.
    assert purger_cache(MAINTENANT + dt.timedelta(days=settings.visuels_cache_jours + 1)) == 1
    assert actif["url"].endswith(".jpg")


# --- Les refus de l'endpoint -------------------------------------------------


def test_un_refus_rend_422_avec_la_raison_affichable(client, db_session):
    post = _post(db_session)
    reponse = client.post(f"/posts/{post.id}/visuels", content=image(200, 250))
    assert reponse.status_code == 422
    assert "agrandir" in reponse.json()["detail"]
    db_session.refresh(post)
    # Rien n'est ecrit sur un refus.
    assert not post.assets
    assert not settings.visuels_cache_dir.exists() or not list(settings.visuels_cache_dir.iterdir())


def test_un_corps_trop_lourd_est_refuse_avant_d_etre_lu(client, db_session, monkeypatch):
    """413 sur le `Content-Length` annonce : la borne tombe AVANT la lecture en
    memoire, pas apres."""
    monkeypatch.setattr("app.api.posts.TAILLE_MAX_OCTETS", 1024)
    post = _post(db_session)
    reponse = client.post(f"/posts/{post.id}/visuels", content=b"x" * 5000)
    assert reponse.status_code == 413
    assert "trop lourd" in reponse.json()["detail"]


def test_une_video_est_refusee_avec_SA_raison(client, db_session):
    """Le gel ne sait telecharger une video que depuis une URL http(s)
    (`preparation_reel.telecharger_video` refuse tout autre schema) : une video
    deposee serait acceptee ici puis ECARTEE au gel sans rien montrer."""
    post = _post(db_session)
    faux_mp4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64
    reponse = client.post(f"/posts/{post.id}/visuels", content=faux_mp4)
    assert reponse.status_code == 422
    assert "vidéo" in reponse.json()["detail"] or "video" in reponse.json()["detail"]


def test_un_post_inconnu_rend_404(client, db_session):
    assert client.post("/posts/9999/visuels", content=image()).status_code == 404


def test_pendant_une_publication_en_vol_le_depot_est_refuse(client, db_session):
    """« Pas d'edition quand la publication est en vol » : le conteneur est parti
    chez Meta avec son contenu, accepter un visuel laisserait croire le
    contraire."""
    from app.schedule.runner import RAISON_RECLAMATION

    post = _post(db_session)
    db_session.add(
        Creneau(
            publier_a=MAINTENANT + dt.timedelta(minutes=2), etat=EtatCreneau.ECHEC,
            format=FormatCreneau.CARROUSEL, article_id=post.article_id, post_id=post.id,
            erreur=RAISON_RECLAMATION,
        )
    )
    db_session.commit()
    reponse = client.post(f"/posts/{post.id}/visuels", content=image(1200, 1500))
    assert reponse.status_code == 409
    assert "Publication en cours" in reponse.json()["detail"]


def test_le_depot_n_appelle_jamais_meta_ni_ne_touche_un_creneau(client, db_session):
    """Anti-doublon : rien de ce chemin n'approche `media_publish`, et le creneau
    GELE n'est pas touche -- c'est `PATCH /posts/{id}` qui rouvre, quand et si la
    liste change."""
    post = _post(db_session)
    creneau = Creneau(
        publier_a=MAINTENANT + dt.timedelta(hours=2), etat=EtatCreneau.GELE,
        format=FormatCreneau.CARROUSEL, article_id=post.article_id, post_id=post.id,
        fichiers=["abc_01.jpg"],
    )
    db_session.add(creneau)
    db_session.commit()
    assert client.post(f"/posts/{post.id}/visuels", content=image(1200, 1500)).status_code == 201
    db_session.refresh(creneau)
    assert creneau.etat == EtatCreneau.GELE
    assert creneau.fichiers == ["abc_01.jpg"]


def test_le_visuel_depose_n_est_jamais_une_source_a_relire(client, db_session):
    """`store.actifs_interdits` relit la source des actifs `article` et `artiste`
    pour verifier qu'elle n'est pas passee en compte media. Un fichier du disque
    n'a pas de source : une origine A PART l'empeche d'etre cherche en base."""
    from app.schedule.store import actifs_interdits

    post = _post(db_session)
    client.post(f"/posts/{post.id}/visuels", content=image(1200, 1500))
    db_session.refresh(post)
    assert actifs_interdits(db_session, post) == []


def test_le_visuel_depose_ne_porte_aucun_credit(client, db_session):
    """Un fichier du disque n'a pas de compte a crediter : le credit obligatoire
    des visuels d'artistes ne doit pas se mettre a citer un `@` fantome."""
    from app.content.visuels import credit_des_actifs

    post = _post(db_session)
    client.post(f"/posts/{post.id}/visuels", content=image(1200, 1500))
    db_session.refresh(post)
    assert credit_des_actifs(post.assets) is None


# --- Le cablage du dashboard -------------------------------------------------
#
# Mesure au navigateur (Chromium reel, 1440 / 900 / 390 px) faite a la main le
# 2026-10-04 : le controle est visible et la page ne prend aucun scroll
# horizontal aux trois largeurs. Ce qui suit verrouille les proprietes qu'une
# refonte pourrait casser sans que rien d'autre ne rougisse.

from pathlib import Path  # noqa: E402

PAGE = (Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html").read_text(
    encoding="utf-8"
)


def test_le_fichier_part_en_CORPS_BRUT_jamais_en_formulaire():
    """LA propriete de securite, cote navigateur : un `FormData` enverrait le NOM
    du fichier au serveur, et le serveur ne doit jamais en recevoir un. Elle se
    casserait en une ligne, sans qu'aucun test de rendu ne bronche."""
    geste = PAGE[PAGE.index('const depot = detailEl.querySelector("#el-depot")'):]
    geste = geste[: geste.index("\n  }\n")]
    assert 'fetch(`/posts/${post.id}/visuels`, { method: "POST", body: fichier })' in geste
    assert "FormData" not in geste
    assert "filename" not in geste.lower()


def test_le_depot_passe_par_le_chemin_ordinaire_d_ecriture_de_la_liste():
    """`ecrireListe` -> `PATCH /posts/{id}`, celui qui rouvre le creneau tout
    seul. Un second chemin d'ecriture divergerait du re-gel automatique."""
    geste = PAGE[PAGE.index('const depot = detailEl.querySelector("#el-depot")'):]
    geste = geste[: geste.index("\n  }\n")]
    assert "await ecrireListe(" in geste
    assert "[...slidesCourantes(), { kind: \"media\", ...candidat }]" in geste


def test_le_controle_desactive_imprime_toujours_sa_raison():
    """Regle de la refonte du 2026-10-02 : jamais gris et muet."""
    bloc = PAGE[PAGE.index('<div class="depot">'):]
    bloc = bloc[: bloc.index("</div>`;")]
    assert '${editableListe ? "" : "disabled"}' in bloc
    # La raison est calculee une fois, en amont, parce que DEUX controles la
    # partagent desormais (le depot et le selecteur d'ajout) : une seconde copie
    # de cette expression divergerait.
    assert "escapeHtml(raisonNonEditable)" in bloc
    assert "depart.raison_non_editable || depart.raison_non_reouvrable" in PAGE


def test_une_raison_de_refus_porte_la_teinte_d_alerte_jamais_l_accent():
    """Rose et rouge sont voisins : une raison de panne qui vire au rose ne casse
    rien et rend la lecture aveugle. `tests/dashboard/test_dashboard_marque.py` couvre
    desormais cette regle, parce que son selecteur porte le mot « erreur »."""
    assert ".depot-motif.erreur { color: var(--alerte); }" in PAGE
    from tests.dashboard.test_dashboard_marque import regles_de_panne_citant_l_accent

    mutee = PAGE.replace(
        ".depot-motif.erreur { color: var(--alerte); }",
        ".depot-motif.erreur { color: var(--accent); }",
    )
    assert mutee != PAGE
    assert regles_de_panne_citant_l_accent(mutee)


def test_l_origine_du_depot_est_nommee_partout_ou_une_origine_s_affiche():
    """DEUX endroits affichent d'ou vient un visuel depuis la fusion des listes
    (2026-10-05) : la liste finale et la reserve d'ajout.

    Il y en avait trois, et le premier depot a ete annonce « photo de presse »
    dans le troisieme (« Visuels retenus ») -- la seule provenance que
    l'utilisateur choisit lui-meme etait la seule annoncee de travers. Cette
    section a disparu dans la fusion ; le risque, lui, ne disparait pas : le
    libelle doit rester branche aux deux endroits qui restent.
    """
    assert 'depot: "📁 fichier déposé depuis le disque"' in PAGE
    # La liste finale.
    assert "ORIGINES_ELEMENT[e.origine] || e.origine || \"origine inconnue\"" in PAGE
    # La reserve d'ajout et de remplacement.
    assert "c.compte || ORIGINES_ELEMENT[c.origine]" in PAGE
