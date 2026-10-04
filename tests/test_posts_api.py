import datetime as dt

import pytest

from app.content.compose import CoverVariante, PostDraft
from app.content.render import RenduSlide
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, Post, PostStatus, Source, SourceType
from app.schemas import PostOut


def _article(db):
    src = Source(name="Tsugi", type=SourceType.RSS, url="https://tsugi.fr/feed")
    db.add(src)
    db.commit()
    article = Article(
        source_id=src.id,
        url="https://tsugi.fr/a",
        title="Les Vieilles Charrues devoilent leur line-up",
        raw_hash="h1",
        published_at=dt.datetime.now(dt.timezone.utc),
    )
    db.add(article)
    db.commit()
    return article


def _post(db, article, status=PostStatus.DRAFT, variants=None):
    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name="Tsugi",
        status=status,
        caption="Une legende.",
        hashtags=["#electro"],
        slides=[
            {
                "kind": "cover",
                "headline": "**Vieilles Charrues** : le line-up",
                "title": "**Vieilles Charrues**",
                "subtitle": "le line-up **electro**",
                "image_url": None,
            }
        ],
        variants=variants,
    )
    db.add(post)
    db.commit()
    return post


def _quatre_variantes():
    return {
        "variantes": [
            {"headline": f"**H**{i}", "title": f"**T**{i}", "subtitle": f"**S**{i}"}
            for i in range(4)
        ],
        "retenue": None,
    }


def test_creer_un_post_depuis_un_article(client, db_session, monkeypatch):
    """La composition est mockee : on teste le cablage, pas le modele."""
    article = _article(db_session)

    def faux_composeur():
        def composer(_article):
            return PostDraft(
                variantes=[
                    CoverVariante(
                        headline=f"**Vieilles Charrues** : le line-up {i}",
                        title=f"**Titre** {i}",
                        subtitle=f"**Sous-titre** {i}",
                    )
                    for i in range(4)
                ],
                caption="Le line-up est tombe 🎧 ✨",
                hashtags=["#vieillescharrues"],
            )

        return composer

    monkeypatch.setattr("app.api.posts.make_claude_composer", faux_composeur)

    response = client.post(f"/articles/{article.id}/posts")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    # Une seule slide a la creation : la cover (variante 1) ; les trois autres
    # variantes vivent dans `variants`, pas dans `slides`.
    assert len(body["slides"]) == 1
    assert len(body["variants"]["variantes"]) == 4
    assert body["variants"]["retenue"] is None
    assert body["article_title"] == article.title


def test_creer_un_post_sur_un_article_inexistant(client):
    assert client.post("/articles/9999/posts").status_code == 404


def test_creer_un_post_composition_echouee_rend_une_erreur_lisible(client, db_session, monkeypatch):
    """Une composition qui echoue systematiquement doit rendre un 502 exploitable,
    pas un 500 avec stacktrace brute."""
    article = _article(db_session)

    def faux_composeur():
        def composer(_article):
            from app.content.compose import CompositionError

            raise CompositionError("composition impossible pour l'article")

        return composer

    monkeypatch.setattr("app.api.posts.make_claude_composer", faux_composeur)

    response = client.post(f"/articles/{article.id}/posts")

    assert response.status_code == 502
    assert "detail" in response.json()


def test_lister_les_posts_par_statut(client, db_session):
    article = _article(db_session)
    _post(db_session, article, status=PostStatus.DRAFT)
    _post(db_session, article, status=PostStatus.APPROVED)

    assert len(client.get("/posts").json()) == 2
    approuves = client.get("/posts?status=approved").json()
    assert len(approuves) == 1
    assert approuves[0]["status"] == "approved"


def test_editer_la_legende_et_les_slides(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)

    response = client.patch(
        f"/posts/{post.id}",
        json={
            "caption": "Une meilleure legende.",
            "slides": [
                {
                    "kind": "cover",
                    "headline": "**Nouvelle** accroche",
                    "title": "**Nouveau titre**",
                    "subtitle": "**Nouveau** sous-titre",
                    "image_url": "https://x/y.jpg",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["caption"] == "Une meilleure legende."
    assert body["slides"][0]["image_url"] == "https://x/y.jpg"


def test_editer_une_cover_round_trip_les_trois_textes_et_le_kind(client, db_session):
    """C'est le test qui aurait attrape le bug : PATCH persistait `slides` via
    `SlideIn` (headline + image_url seulement), donc `title`/`subtitle`/`kind`
    disparaissaient de la ligne en base -- silencieusement, puisque Pydantic ignore
    les cles inconnues en entree ET ne re-ecrit que ce que le modele declare en
    sortie. On relit la ligne EN BASE, pas la reponse HTTP : une reponse pourrait
    par accident sembler correcte si elle echouait a d'autres endroits."""
    article = _article(db_session)
    post = _post(db_session, article)

    response = client.patch(
        f"/posts/{post.id}",
        json={
            "slides": [
                {
                    "kind": "cover",
                    "headline": "**CORTIS** toujours **2e**",
                    "title": "**CORTIS**",
                    "subtitle": "13 semaines au **Top 2**",
                    "image_url": "https://x/cover.jpg",
                }
            ],
        },
    )

    assert response.status_code == 200

    db_session.expire_all()
    relu = db_session.get(Post, post.id)
    (slide,) = relu.slides
    assert slide["kind"] == "cover"
    assert slide["headline"] == "**CORTIS** toujours **2e**"
    assert slide["title"] == "**CORTIS**"
    assert slide["subtitle"] == "13 semaines au **Top 2**"
    assert slide["image_url"] == "https://x/cover.jpg"


def test_editer_un_media_ne_requiert_pas_de_headline(client, db_session):
    """Un media (photo/video reelle de l'actu) n'est pas rendu par le gabarit : il
    n'a pas de texte a valider, seulement une provenance."""
    article = _article(db_session)
    post = _post(db_session, article)

    response = client.patch(
        f"/posts/{post.id}",
        json={
            "slides": [
                {"kind": "media", "type": "photo", "url": "https://x/photo.jpg"},
            ],
        },
    )

    assert response.status_code == 200
    db_session.expire_all()
    relu = db_session.get(Post, post.id)
    (slide,) = relu.slides
    assert slide["kind"] == "media"
    assert slide["type"] == "photo"
    assert slide["url"] == "https://x/photo.jpg"


def test_editer_une_cover_sans_titre_ou_sous_titre_est_refuse(client, db_session):
    """Une union permissive laisserait passer une cover sans texte et ferait
    echouer le rendu plus tard chez Chromium -- ici l'echec doit arriver a la
    frontiere de l'API, en 422."""
    article = _article(db_session)
    post = _post(db_session, article)

    sans_titre = client.patch(
        f"/posts/{post.id}",
        json={"slides": [{"kind": "cover", "headline": "**A**", "subtitle": "**B**"}]},
    )
    assert sans_titre.status_code == 422

    sans_sous_titre = client.patch(
        f"/posts/{post.id}",
        json={"slides": [{"kind": "cover", "headline": "**A**", "title": "**B**"}]},
    )
    assert sans_sous_titre.status_code == 422


def test_editer_une_slide_au_kind_inconnu_est_refuse(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)

    response = client.patch(
        f"/posts/{post.id}",
        json={"slides": [{"kind": "carrousel", "headline": "**A**"}]},
    )

    assert response.status_code == 422


def test_publier_un_post_marque_l_article_comme_poste(client, db_session):
    """C'est ce qui protege l'article de la purge : USED n'est jamais purge."""
    article = _article(db_session)
    post = _post(db_session, article)

    response = client.patch(f"/posts/{post.id}", json={"status": "published"})

    assert response.status_code == 200
    assert response.json()["published_at"] is not None
    db_session.refresh(article)
    assert article.status == ArticleStatus.USED


def test_editer_un_post_inexistant(client):
    assert client.patch("/posts/9999", json={"caption": "x"}).status_code == 404


def test_editer_une_slide_sans_headline_est_refuse(client, db_session):
    """`render_post` fait `slide["headline"]` sans filet : une slide sans headline
    ne doit jamais atteindre la base."""
    article = _article(db_session)
    post = _post(db_session, article)

    response = client.patch(
        f"/posts/{post.id}",
        json={
            "slides": [
                {"kind": "cover", "title": "**T**", "subtitle": "**S**",
                 "image_url": "https://x/y.jpg"}
            ]
        },
    )

    assert response.status_code == 422


def test_editer_une_slide_au_balisage_invalide_est_refuse(client, db_session):
    """Meme invariant qu'a la composition : un `**` orphelin ou une accroche sans
    segment surligne ne doit pas pouvoir s'imprimer tel quel sur le PNG."""
    article = _article(db_session)
    post = _post(db_session, article)

    sans_surlignage = client.patch(
        f"/posts/{post.id}",
        json={
            "slides": [
                {"kind": "cover", "headline": "Une accroche plate sans surlignage",
                 "title": "**T**", "subtitle": "**S**"}
            ]
        },
    )
    assert sans_surlignage.status_code == 422

    marqueurs_impairs = client.patch(
        f"/posts/{post.id}",
        json={
            "slides": [
                {"kind": "cover", "headline": "Une accroche **mal fermee",
                 "title": "**T**", "subtitle": "**S**"}
            ]
        },
    )
    assert marqueurs_impairs.status_code == 422


def test_exporter_un_post(client, db_session, monkeypatch):
    """Export mocke : verifie l'assemblage du zip (slides + legende) et le nom
    de fichier en ASCII pur (cf. CLAUDE.md)."""
    article = _article(db_session)
    post = _post(db_session, article)

    def faux_rendu(slides, template, palette_=None):
        return [RenduSlide(png=b"PNG_1", scale=1.0, lines={"headline": 1}, overflow=False)]

    monkeypatch.setattr("app.api.posts.render_post", faux_rendu)

    response = client.get(f"/posts/{post.id}/export.zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    disposition = response.headers["content-disposition"]
    assert disposition == f'attachment; filename="kinda_post_{post.id}.zip"'
    disposition.encode("ascii")  # ne doit pas lever : en-tete ASCII pur

    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        noms = archive.namelist()
        assert "01_cover.png" in noms
        assert "caption.txt" in noms
        assert archive.read("01_cover.png") == b"PNG_1"
        legende = archive.read("caption.txt").decode("utf-8")
        assert "Une legende." in legende
        assert "#electro" in legende
        assert post.article_url not in legende


def test_exporter_un_post_inexistant(client):
    assert client.get("/posts/9999/export.zip").status_code == 404


def test_render_persiste_le_rapport(client, db_session, monkeypatch):
    """Le but n'est pas de tester le rendu ici -- tests/test_render.py le fait --
    mais que le RAPPORT arrive bien en base. C'est lui qui permet au dashboard de
    signaler un post en defaut plutot que de laisser publier une image coupee."""
    article = _article(db_session)
    post = _post(db_session, article)

    def faux_render(slides, template="kinda_post", palette_=None):
        return [
            RenduSlide(png=b"PNG", scale=1.0, lines={"headline": 3}, overflow=False),
            RenduSlide(png=b"PNG", scale=0.7, lines={"headline": 6}, overflow=True),
        ]

    monkeypatch.setattr("app.api.posts.render_post", faux_render)

    response = client.post(f"/posts/{post.id}/render")

    assert response.status_code == 200
    rapport = response.json()
    assert rapport["slides"][0] == {"scale": 1.0, "lines": {"headline": 3}, "overflow": False}
    assert rapport["slides"][1]["overflow"] is True

    db_session.refresh(post)
    assert post.render_report == rapport


def test_render_sur_un_post_inconnu_rend_404(client):
    assert client.post("/posts/999/render").status_code == 404


def test_editer_les_slides_invalide_le_rapport_de_rendu(client, db_session):
    """Le rapport parle d'un texte precis. L'editeur de carrousel EST une
    surface d'edition d'accroche : une fois les slides changees, un ancien
    rapport (overflow ou pas) ne veut plus rien dire -- et un vieux "clean"
    serait un faux feu vert sur le signal qui doit empecher une publication en
    defaut."""
    article = _article(db_session)
    post = _post(db_session, article)
    post.render_report = {"slides": [{"scale": 1.0, "lines": {"headline": 3}, "overflow": False}]}
    db_session.commit()

    response = client.patch(
        f"/posts/{post.id}",
        json={
            "slides": [
                {
                    "kind": "cover",
                    "headline": "Une **nouvelle** accroche",
                    "title": "**Titre**",
                    "subtitle": "**Sous-titre**",
                    "image_url": None,
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["render_report"] is None
    db_session.refresh(post)
    assert post.render_report is None


def test_changer_de_gabarit_invalide_le_rapport_de_rendu(client, db_session):
    """Meme raison que pour `slides` : un gabarit different change les zones et
    les tailles, donc ce que Chromium mesurerait -- un vieux rapport "clean"
    serait un faux feu vert."""
    article = _article(db_session)
    post = _post(db_session, article)
    post.render_report = {"slides": [{"scale": 1.0, "lines": {"headline": 3}, "overflow": False}]}
    db_session.commit()

    response = client.patch(f"/posts/{post.id}", json={"template": "kinda_reel"})

    assert response.status_code == 200
    assert response.json()["render_report"] is None
    db_session.refresh(post)
    assert post.render_report is None


def test_retenir_une_variante_invalide_le_rapport_de_rendu(client, db_session):
    """Meme raison : une autre variante retenue veut dire un autre texte sur la
    cover, donc un rapport perime."""
    article = _article(db_session)
    post = _post(db_session, article, variants=_quatre_variantes())
    post.render_report = {"slides": [{"scale": 1.0, "lines": {"headline": 3}, "overflow": False}]}
    db_session.commit()

    response = client.patch(f"/posts/{post.id}", json={"variante_retenue": 1})

    assert response.status_code == 200
    assert response.json()["render_report"] is None
    db_session.refresh(post)
    assert post.render_report is None


def test_retenir_une_variante_ne_perd_pas_la_teinte(client, db_session):
    """`variants` porte AUSSI la palette du post depuis le 2026-09-18 : l'ecrire
    en bloc plutot qu'en fusion la ferait disparaitre au premier clic, et la
    cover repasserait en rose sur une actu ecologie."""
    article = _article(db_session)
    post = _post(db_session, article, variants={**_quatre_variantes(), "palette": "verte"})

    assert client.patch(f"/posts/{post.id}", json={"variante_retenue": 1}).status_code == 200

    db_session.refresh(post)
    assert post.variants["palette"] == "verte"


def test_covers_rend_quatre_data_uris(client, db_session, monkeypatch):
    """Deux endpoints distincts et non un seul : la generation coute un appel
    Claude, le rendu coute un Chromium. Recharger la page ne doit pas repayer
    Claude."""
    article = _article(db_session)
    post = _post(db_session, article, variants=_quatre_variantes())

    def faux_render_covers(variantes, image_url, template, palette_=None):
        assert len(variantes) == 4
        assert template == post.template
        # Overflow different par variante, expres : ce n'est pas un simple
        # applati de True/False identiques -- le test doit prouver que chaque
        # RenduSlide.overflow arrive individuellement dans la reponse.
        return [
            RenduSlide(png=b"PNG_FACTICE", scale=1.0, lines={}, overflow=(i == 1))
            for i, _ in enumerate(variantes)
        ]

    monkeypatch.setattr("app.api.posts.render_covers", faux_render_covers)

    reponse = client.post(f"/posts/{post.id}/covers")

    assert reponse.status_code == 200
    corps = reponse.json()
    assert len(corps["covers"]) == 4
    assert all(u.startswith("data:image/png;base64,") for u in corps["covers"])
    # `render_covers` calculait deja `overflow` par variante et le jetait :
    # CoversOut doit maintenant le porter, pour que le dashboard avertisse
    # AVANT que l'utilisateur choisisse une variante qui ne tient pas, et
    # pour eviter un second lancement de Chromium (POST /render) rien que
    # pour re-obtenir ce meme signal sur la variante retenue.
    assert corps["overflow"] == [False, True, False, False]


def test_covers_sur_un_post_sans_variantes_est_refuse(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)

    assert client.post(f"/posts/{post.id}/covers").status_code == 409


def test_covers_sur_un_post_inexistant(client):
    assert client.post("/posts/9999/covers").status_code == 404


def test_retenir_une_variante_reecrit_la_cover(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article, variants=_quatre_variantes())

    reponse = client.patch(f"/posts/{post.id}", json={"variante_retenue": 2})

    assert reponse.status_code == 200

    db_session.expire_all()
    relu = db_session.get(Post, post.id)
    assert relu.variants["retenue"] == 2
    assert relu.slides[0]["headline"] == relu.variants["variantes"][2]["headline"]
    assert relu.slides[0]["title"] == relu.variants["variantes"][2]["title"]
    # Le fond est le choix de l'utilisateur : il doit survivre au changement
    # de variante retenue, meme si la fixture le pose a None ici.
    assert relu.slides[0]["image_url"] is None


def test_retenir_une_variante_sur_des_slides_vides_ne_leve_pas(client, db_session):
    """`slides: []` est permis par le schema (PostUpdate.slides) : un IndexError
    sur `post.slides[0]` serait un 500, pas la reponse d'un cas legal."""
    article = _article(db_session)
    post = _post(db_session, article, variants=_quatre_variantes())
    post.slides = []
    db_session.commit()

    reponse = client.patch(f"/posts/{post.id}", json={"variante_retenue": 1})

    assert reponse.status_code == 200
    db_session.expire_all()
    relu = db_session.get(Post, post.id)
    assert relu.slides[0]["headline"] == relu.variants["variantes"][1]["headline"]


def test_un_indice_de_variante_hors_bornes_est_refuse(client, db_session):
    """422 a la frontiere plutot qu'un IndexError dans le renderer."""
    article = _article(db_session)
    post = _post(db_session, article, variants=_quatre_variantes())

    assert client.patch(f"/posts/{post.id}", json={"variante_retenue": 9}).status_code == 422


def test_changer_de_gabarit_conserve_la_cover_et_ses_trois_textes(client, db_session):
    """La bascule est instantanee parce que la cover porte deja les trois textes."""
    article = _article(db_session)
    post = _post(db_session, article)

    reponse = client.patch(f"/posts/{post.id}", json={"template": "kinda_reel"})

    assert reponse.status_code == 200
    db_session.refresh(post)
    assert post.template == "kinda_reel"
    assert post.slides[0]["title"] and post.slides[0]["subtitle"]


def test_un_gabarit_inconnu_est_refuse(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)

    assert client.patch(f"/posts/{post.id}", json={"template": "kinda_carre"}).status_code == 422


def test_un_brouillon_se_supprime(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)

    assert client.delete(f"/posts/{post.id}").status_code == 204
    assert db_session.get(Post, post.id) is None


def test_un_post_publie_ne_se_supprime_pas(client, db_session):
    """La regle "jamais purge" protege l'ARCHIVE EDITORIALE : savoir ce qui a ete
    publie, et ne pas le reposter. La frontiere est donc la publication."""
    article = _article(db_session)
    post = _post(db_session, article, status=PostStatus.PUBLISHED)

    assert client.delete(f"/posts/{post.id}").status_code == 409
    assert db_session.get(Post, post.id) is not None


def test_un_post_approuve_ne_se_supprime_pas(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article, status=PostStatus.APPROVED)

    assert client.delete(f"/posts/{post.id}").status_code == 409
    assert db_session.get(Post, post.id) is not None


def test_supprimer_un_post_inexistant(client):
    assert client.delete("/posts/9999").status_code == 404


def _transforme_slide_legacy_comme_la_migration_0011(entree: dict) -> dict:
    """Reproduit en Python le SQL de la migration `0011` (backfill `kind`, puis
    `title`/`subtitle` depuis `headline`) : `UPDATE ... SET slides = entree ||
    '{"kind": "cover"}' || jsonb_build_object('title', COALESCE(...))`.

    Ce n'est PAS un remplacement de la verification sur cluster jetable (faite a
    la main, cf. le rapport de cette tache) : Postgres/JSONB n'existe pas sous
    SQLite, donc les tests ne peuvent pas executer la vraie migration. Ce test
    verifie la partie qu'un test SQLite PEUT verifier -- que le RESULTAT de la
    transformation satisfait `PostOut`, le schema qui a fait 500 la file
    entiere en production avant ce correctif.
    """
    return {
        **entree,
        "kind": "cover",
        "title": entree.get("title", entree.get("headline")),
        "subtitle": entree.get("subtitle", entree.get("headline")),
    }


def test_une_slide_legacy_migree_valide_postout(client, db_session):
    """Avant le correctif, la migration ne backfillait QUE `kind` : une slide
    legacy n'avait ni `title` ni `subtitle`, que `CoverIn` rend obligatoires --
    `GET /posts` (qui valide chaque ligne via `list[PostOut]`) rendait donc 500
    sur la file ENTIERE des qu'un seul post pre-0011 trainait en base. Verifie
    a la main sur la base de l'utilisateur (lecture seule, 2026-08-09) : il y en
    a trois, a la revision 0009 -- ce n'etait donc pas hypothetique."""
    article = _article(db_session)
    slide_legacy = {
        "headline": "**Vieilles Charrues** : le line-up",
        "image_url": "https://example.com/fond.jpg",
    }
    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name="Tsugi",
        caption="Une legende.",
        hashtags=["#electro"],
        slides=[_transforme_slide_legacy_comme_la_migration_0011(slide_legacy)],
        variants=None,
    )
    db_session.add(post)
    db_session.commit()

    # C'est EXACTEMENT le chemin qui 500ait : la validation de sortie de
    # `GET /posts`/`GET /posts/{id}` (response_model=list[PostOut] / PostOut).
    valide = PostOut.model_validate(post)

    assert valide.slides[0].kind == "cover"
    assert valide.slides[0].title == "**Vieilles Charrues** : le line-up"
    assert valide.slides[0].subtitle == "**Vieilles Charrues** : le line-up"

    reponse = client.get(f"/posts/{post.id}")
    assert reponse.status_code == 200
    assert reponse.json()["slides"][0]["title"] == valide.slides[0].title

    reponse_liste = client.get("/posts")
    assert reponse_liste.status_code == 200


def test_l_api_expose_les_visuels_et_le_credit(client, db_session):
    """Le dashboard doit pouvoir montrer d'ou vient chaque visuel, et qui est
    credite en legende."""
    post = _post(db_session, _article(db_session))
    post.credit = "@fredagain"
    post.assets = [{"type": "photo", "url": "https://cdn.ig/f.jpg", "origine": "artiste",
                    "compte": "@fredagain", "fichier": "file:///cache/f.jpg"}]
    db_session.commit()

    corps = client.get(f"/posts/{post.id}").json()

    assert corps["credit"] == "@fredagain"
    assert corps["assets"][0]["compte"] == "@fredagain"


def _post_sur_un_creneau(db, etat, erreur=None, dans=dt.timedelta(hours=1)):
    article = _article(db)
    post = _post(db, article, variants=_quatre_variantes())
    db.add(Creneau(
        publier_a=dt.datetime.now(dt.timezone.utc) + dans,
        article_id=article.id, post_id=post.id, etat=EtatCreneau(etat), erreur=erreur,
    ))
    db.commit()
    return post


@pytest.mark.parametrize("modification", [
    {"variante_retenue": 1},
    {"slides": [{"kind": "cover", "headline": "**H**", "title": "**T**", "subtitle": "**S**"}]},
    {"template": "kinda_reel"},
])
def test_la_cover_d_un_post_gele_ne_se_modifie_pas_sans_rouvrir(client, db_session, modification):
    """`creneaux.fichiers` PORTE la cover rendue et les medias telecharges : les
    changer sans refaire ces fichiers publierait autre chose que ce qu'on voit.

    Le refus nomme le remede (`POST /schedule/{id}/rouvrir`, 2026-10-04) plutot
    que de laisser l'utilisateur devant une porte fermee -- c'est exactement la
    fenetre ou il veut corriger."""
    post = _post_sur_un_creneau(db_session, "gele")
    reponse = client.patch(f"/posts/{post.id}", json=modification)
    assert reponse.status_code == 409
    assert "rouvrir" in reponse.json()["detail"]
    db_session.refresh(post)
    assert post.template == "kinda_post"


@pytest.mark.parametrize("modification", [
    {"caption": "Autre legende"},
    {"hashtags": ["#pop"]},
])
def test_la_legende_d_un_post_gele_se_modifie_encore(client, db_session, modification):
    """CHANGEMENT DELIBERE du 2026-10-04. Ces deux champs ne sont PAS figes :
    `runner._publier` appelle `texte_legende(post)` juste avant l'appel a Meta, en
    lisant la base. Le 409 qui les bloquait etait une SUR-PORTEE -- il fermait la
    porte exactement dans les deux heures ou l'utilisateur veut relire et corriger
    (« modifier la description »), alors que rien dans le gel n'en depend.

    Ce qui reste refuse pendant une publication EN VOL est teste juste en dessous :
    la legende a alors deja ete lue et est partie avec le conteneur."""
    post = _post_sur_un_creneau(db_session, "gele")
    assert client.patch(f"/posts/{post.id}", json=modification).status_code == 200
    db_session.refresh(post)
    for champ, valeur in modification.items():
        assert getattr(post, champ) == valeur


def test_un_post_programme_non_gele_se_modifie(client, db_session):
    post = _post_sur_un_creneau(db_session, "programme")
    assert client.patch(f"/posts/{post.id}", json={"caption": "Autre"}).status_code == 200


RECLAMATION = "publication interrompue avant confirmation"


@pytest.mark.parametrize(("erreur", "dans", "statut"), [
    # Reclame par l'agent, conteneur en cours ou attente de l'heure : fige.
    (RECLAMATION, dt.timedelta(minutes=5), 409),
    (RECLAMATION, dt.timedelta(minutes=-5), 409),
    # Une vieille reclamation (crash) ou un vrai echec : editable.
    (RECLAMATION, dt.timedelta(minutes=-30), 200),
    ("media refuse", dt.timedelta(minutes=5), 200),
])
def test_un_post_en_cours_de_publication_ne_se_modifie_plus(client, db_session, erreur, dans, statut):
    post = _post_sur_un_creneau(db_session, "echec", erreur=erreur, dans=dans)
    assert client.patch(f"/posts/{post.id}", json={"caption": "Autre"}).status_code == statut
