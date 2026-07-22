import datetime as dt

from app.content.compose import PostDraft, Slide
from app.models import Article, ArticleStatus, Post, PostStatus, Source, SourceType


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


def _post(db, article, status=PostStatus.DRAFT):
    post = Post(
        article_id=article.id,
        article_title=article.title,
        article_url=article.url,
        source_name="Tsugi",
        status=status,
        caption="Une legende.",
        hashtags=["#electro"],
        slides=[{"headline": "**Vieilles Charrues** : le line-up", "image_url": None}],
    )
    db.add(post)
    db.commit()
    return post


def test_creer_un_post_depuis_un_article(client, db_session, monkeypatch):
    """La composition est mockee : on teste le cablage, pas le modele."""
    article = _article(db_session)

    def faux_composeur():
        def composer(_article):
            return PostDraft(
                slides=[
                    Slide(headline="**Vieilles Charrues** : le line-up"),
                    Slide(headline="Rendez-vous **ce week-end**"),
                ],
                caption="Le line-up est tombe.",
                hashtags=["#vieillescharrues"],
            )

        return composer

    monkeypatch.setattr("app.api.posts.make_claude_composer", faux_composeur)

    response = client.post(f"/articles/{article.id}/posts")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert len(body["slides"]) == 2
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
            "slides": [{"headline": "**Nouvelle** accroche", "image_url": "https://x/y.jpg"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["caption"] == "Une meilleure legende."
    assert body["slides"][0]["image_url"] == "https://x/y.jpg"


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
        json={"slides": [{"image_url": "https://x/y.jpg"}]},
    )

    assert response.status_code == 422


def test_editer_une_slide_au_balisage_invalide_est_refuse(client, db_session):
    """Meme invariant qu'a la composition : un `**` orphelin ou une accroche sans
    segment surligne ne doit pas pouvoir s'imprimer tel quel sur le PNG."""
    article = _article(db_session)
    post = _post(db_session, article)

    sans_surlignage = client.patch(
        f"/posts/{post.id}",
        json={"slides": [{"headline": "Une accroche plate sans surlignage"}]},
    )
    assert sans_surlignage.status_code == 422

    marqueurs_impairs = client.patch(
        f"/posts/{post.id}",
        json={"slides": [{"headline": "Une accroche **mal fermee"}]},
    )
    assert marqueurs_impairs.status_code == 422


def test_slide_hors_bornes(client, db_session):
    article = _article(db_session)
    post = _post(db_session, article)
    assert client.get(f"/posts/{post.id}/slides/7.png").status_code == 404


def test_rendre_une_slide(client, db_session, monkeypatch):
    """Rendu mocke : on verifie le cablage (index -> slide, media type), pas Chromium."""
    article = _article(db_session)
    post = _post(db_session, article)

    def faux_rendu(slides, template):
        assert template == post.template
        assert slides == [post.slides[0]]
        return [b"PNG_FACTICE"]

    monkeypatch.setattr("app.api.posts.render_post", faux_rendu)

    response = client.get(f"/posts/{post.id}/slides/1.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"PNG_FACTICE"


def test_rendre_une_slide_post_inexistant(client):
    assert client.get("/posts/9999/slides/1.png").status_code == 404


def test_exporter_un_post(client, db_session, monkeypatch):
    """Export mocke : verifie l'assemblage du zip (slides + legende) et le nom
    de fichier en ASCII pur (cf. CLAUDE.md)."""
    article = _article(db_session)
    post = _post(db_session, article)

    def faux_rendu(slides, template):
        return [b"PNG_1"]

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
        assert "slide_01.png" in noms
        assert "caption.txt" in noms
        assert archive.read("slide_01.png") == b"PNG_1"
        legende = archive.read("caption.txt").decode("utf-8")
        assert "Une legende." in legende
        assert "#electro" in legende
        assert post.article_url in legende


def test_exporter_un_post_inexistant(client):
    assert client.get("/posts/9999/export.zip").status_code == 404
