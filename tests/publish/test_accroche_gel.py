"""Modifier l'accroche modifie ce que le gel fige (2026-10-31).

LE PIEGE, un post a quatre variantes : le gel rend `slides[0]`, `/covers` et le choix
de variante lisent `variants.variantes`. Ecrire dans `slides` seul donnait une fiche qui
montre l'ancien texte, et un aller-retour de variante qui efface la correction en silence.
Ces tests regardent le JPEG gele lui-meme, pas la base.
"""

import datetime as dt
import io

import pytest
from PIL import Image, ImageChops, ImageStat

from app.api import posts as api_posts
from app.config import settings
from app.content.render import render_post
from app.content.visuels import ORIGINE_ARTICLE, actif
from app.models import Article, Creneau, EtatCreneau, FormatCreneau, Post, Source, SourceType
from app.publish.preparation import _cadrer, preparer_fichiers
from app.schedule.runner import RapportPublication, _geler
from tests.dashboard.test_dashboard_ecritures import chromium_absent

MAINTENANT = dt.datetime.now(dt.timezone.utc)
FOND = "https://img/fond.jpg"


def _variante(n):
    return {"headline": f"**Variante {n}** ancienne", "title": f"**Titre {n}**", "subtitle": f"**Sous-titre {n}**"}


def _png():
    tampon = io.BytesIO()
    Image.new("RGB", (1200, 1500), (10, 20, 30)).save(tampon, "PNG")
    return tampon.getvalue()


def _poser(db, *, etat=EtatCreneau.GELE, depart=dt.timedelta(hours=1), retenue=1, template="kinda_post"):
    source = Source(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    db.add(source)
    db.commit()
    article = Article(source_id=source.id, url="https://t/1", title="Actu", raw_hash="h1",
                      relevance_score=90, published_at=MAINTENANT)
    db.add(article)
    db.commit()
    variantes = [_variante(n) for n in range(4)]
    indice = retenue if retenue is not None else 0
    post = Post(article_id=article.id, article_title="Actu", article_url=article.url, caption="L",
                template=template,
                assets=[actif({"type": "photo", "url": FOND}, origine=ORIGINE_ARTICLE)],
                slides=[{"kind": "cover", **variantes[indice], "image_url": FOND},
                        {"kind": "media", "type": "photo", "url": "https://img/m.jpg"}],
                variants={"variantes": variantes, "retenue": retenue, "palette": "verte",
                          "fond": {"origine": "article"}})
    db.add(post)
    db.commit()
    creneau = Creneau(publier_a=MAINTENANT + depart, article_id=article.id, post_id=post.id, etat=etat,
                      format=FormatCreneau.CARROUSEL,
                      fichiers=["p_01.jpg"] if etat == EtatCreneau.GELE else None)
    db.add(creneau)
    db.commit()
    return post, creneau


def _corps(post, **textes):
    cover = {**post.slides[0], **textes}
    return {"slides": [cover, *post.slides[1:]]}


NOUVELLE = "**Une accroche** corrigée à la main"


def test_l_ecriture_suit_la_variante_retenue_sans_toucher_aux_autres(client, db_session):
    post, _ = _poser(db_session)
    assert client.patch(f"/posts/{post.id}", json=_corps(post, headline=NOUVELLE)).status_code == 200
    db_session.refresh(post)
    assert post.slides[0]["headline"] == NOUVELLE
    variantes = post.variants["variantes"]
    assert variantes[1]["headline"] == NOUVELLE
    assert [variantes[i]["headline"] for i in (0, 2, 3)] == [_variante(i)["headline"] for i in (0, 2, 3)]
    # FUSION : ni la palette ni l'origine du fond ne partent, et rien n'est « retenu » a la place.
    assert post.variants["palette"] == "verte"
    assert post.variants["fond"] == {"origine": "article"}
    assert post.variants["retenue"] == 1


def test_sans_variante_retenue_c_est_la_premiere_qui_est_a_l_ecran(client, db_session):
    post, _ = _poser(db_session, retenue=None)
    client.patch(f"/posts/{post.id}", json=_corps(post, headline=NOUVELLE))
    db_session.refresh(post)
    assert post.variants["variantes"][0]["headline"] == NOUVELLE
    assert post.variants["retenue"] is None


def test_un_aller_retour_de_variante_garde_la_correction(client, db_session):
    post, _ = _poser(db_session)
    client.patch(f"/posts/{post.id}", json=_corps(post, headline=NOUVELLE))
    client.patch(f"/posts/{post.id}", json={"variante_retenue": 0})
    assert client.patch(f"/posts/{post.id}", json={"variante_retenue": 1}).status_code == 200
    db_session.refresh(post)
    assert post.slides[0]["headline"] == NOUVELLE
    assert post.slides[1]["url"] == "https://img/m.jpg"


def test_sans_alignement_la_correction_se_perd_preuve_par_mutation(client, db_session, monkeypatch):
    """Le defaut exact : l'edition passe en 200, puis disparait sans un mot."""
    post, _ = _poser(db_session)
    monkeypatch.setattr(api_posts, "_aligner_variante_sur_la_cover", lambda _post: None)
    client.patch(f"/posts/{post.id}", json=_corps(post, headline=NOUVELLE))
    client.patch(f"/posts/{post.id}", json={"variante_retenue": 0})
    client.patch(f"/posts/{post.id}", json={"variante_retenue": 1})
    db_session.refresh(post)
    assert post.slides[0]["headline"] != NOUVELLE


def test_changer_le_fond_ou_la_liste_ne_touche_pas_aux_variantes(client, db_session):
    post, _ = _poser(db_session)
    avant = post.variants
    client.patch(f"/posts/{post.id}", json=_corps(post, image_url="https://img/autre.jpg"))
    db_session.refresh(post)
    assert post.variants == avant


def test_sur_un_gel_l_edition_rouvre_le_creneau_et_garde_le_post(client, db_session):
    post, creneau = _poser(db_session)
    assert client.patch(f"/posts/{post.id}", json=_corps(post, headline=NOUVELLE)).status_code == 200
    db_session.refresh(creneau)
    assert (creneau.etat, creneau.fichiers, creneau.post_id) == (EtatCreneau.PROGRAMME, None, post.id)
    assert creneau.epingle is True


def test_sur_un_creneau_programme_l_edition_epingle(client, db_session):
    post, creneau = _poser(db_session, etat=EtatCreneau.PROGRAMME)
    assert client.patch(f"/posts/{post.id}", json=_corps(post, headline=NOUVELLE)).status_code == 200
    db_session.refresh(creneau)
    assert (creneau.etat, creneau.epingle) == (EtatCreneau.PROGRAMME, True)


def test_a_moins_de_vingt_minutes_le_refus_dit_sa_raison_et_ne_touche_a_rien(client, db_session):
    post, creneau = _poser(db_session, depart=dt.timedelta(minutes=5))
    avant = (post.slides, post.variants)
    reponse = client.patch(f"/posts/{post.id}", json=_corps(post, headline=NOUVELLE))
    assert reponse.status_code == 409
    assert "Trop pres du depart" in reponse.json()["detail"]
    db_session.refresh(post)
    db_session.refresh(creneau)
    assert (post.slides, post.variants) == avant
    assert (creneau.etat, creneau.fichiers) == (EtatCreneau.GELE, ["p_01.jpg"])


@pytest.mark.parametrize("texte", ["sans surlignage", "**pas ferme"])
def test_un_balisage_invalide_est_refuse_sans_rouvrir_le_gel(client, db_session, texte):
    post, creneau = _poser(db_session)
    assert client.patch(f"/posts/{post.id}", json=_corps(post, headline=texte)).status_code == 422
    db_session.refresh(creneau)
    assert (creneau.etat, creneau.fichiers) == (EtatCreneau.GELE, ["p_01.jpg"])


def _ecart(a: bytes, b: bytes) -> float:
    image_a, image_b = (Image.open(io.BytesIO(o)).convert("RGB") for o in (a, b))
    return sum(ImageStat.Stat(ImageChops.difference(image_a, image_b)).mean)


@chromium_absent
def test_le_jpeg_gele_porte_l_accroche_corrigee(client, db_session, tmp_path, monkeypatch):
    """De bout en bout, avec le VRAI rendu : PATCH, re-gel ordinaire, puis le fichier."""
    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    # Un fond plat, sans reseau : seul le texte differe d'un rendu a l'autre.
    monkeypatch.setattr("app.content.render.fetch_image_data_uri", lambda _url: None)
    post, creneau = _poser(db_session)
    ancienne = post.slides[0]
    assert client.patch(f"/posts/{post.id}", json=_corps(post, headline=NOUVELLE)).status_code == 200

    def preparer(post_, article_, dossier):
        return preparer_fichiers(post_, article_, dossier, telecharger=lambda _url: (_png(), None))

    db_session.expire_all()
    rapport = RapportPublication()
    _geler(db_session, MAINTENANT, {FormatCreneau.CARROUSEL: preparer}, lambda *a: None, rapport)
    db_session.refresh(creneau)
    assert creneau.etat == EtatCreneau.GELE, (creneau.erreur, rapport.erreurs)
    gele = next(tmp_path.rglob(creneau.fichiers[0])).read_bytes()

    attendu = _cadrer(render_post([{**ancienne, "headline": NOUVELLE}], "kinda_post",
                                  _palette(post))[0].png)
    avant = _cadrer(render_post([ancienne], "kinda_post", _palette(post))[0].png)
    assert _ecart(gele, attendu) < 0.5
    assert _ecart(gele, avant) > _ecart(gele, attendu) + 0.5


def _palette(post):
    from app.content.teinte import palette_du_post

    return palette_du_post(post)
