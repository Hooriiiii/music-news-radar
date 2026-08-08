import datetime as dt
import io
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.compose import CompositionError, create_post, make_claude_composer
from app.content.render import render_post
from app.db import get_db
from app.models import Article, ArticleStatus, Post, PostStatus
from app.schemas import PostOut, PostUpdate

router = APIRouter(tags=["posts"])


@router.post("/articles/{article_id}/posts", response_model=PostOut, status_code=201)
def creer_post(article_id: int, db: Session = Depends(get_db)):
    """Genere un brouillon de carrousel. Un appel Claude, declenche a la main :
    le pipeline planifie ne cree jamais de post."""
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    composer = make_claude_composer()
    try:
        draft = composer(article)
    except CompositionError as exc:
        # Erreur amont (Claude) : une reponse HTTP lisible, pas un 500 avec trace.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return create_post(db, article, draft)


@router.get("/posts", response_model=list[PostOut])
def lister_posts(
    status: PostStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Post)
    if status is not None:
        stmt = stmt.where(Post.status == status)
    stmt = stmt.order_by(Post.created_at.desc(), Post.id.desc())
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/posts/{post_id}", response_model=PostOut)
def lire_post(post_id: int, db: Session = Depends(get_db)):
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.patch("/posts/{post_id}", response_model=PostOut)
def modifier_post(post_id: int, payload: PostUpdate, db: Session = Depends(get_db)):
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    if payload.caption is not None:
        post.caption = payload.caption
    if payload.hashtags is not None:
        post.hashtags = payload.hashtags
    if payload.slides is not None:
        # Reassignation ENTIERE (liste de dicts simples, pas les modeles Pydantic) :
        # une mutation in place ne serait pas persistee sur la colonne JSON
        post.slides = [slide.model_dump() for slide in payload.slides]
    if payload.status is not None:
        post.status = payload.status
        if payload.status is PostStatus.PUBLISHED:
            post.published_at = dt.datetime.now(dt.timezone.utc)
            # L'article passe en USED : c'est ce qui le protege de la purge et
            # evite qu'il soit repropose plus tard
            article = db.get(Article, post.article_id) if post.article_id else None
            if article is not None:
                article.status = ArticleStatus.USED

    db.commit()
    return post


def _post_ou_404(db: Session, post_id: int) -> Post:
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.get("/posts/{post_id}/slides/{index}.png")
def rendre_slide(post_id: int, index: int, db: Session = Depends(get_db)):
    post = _post_ou_404(db, post_id)
    if index < 1 or index > len(post.slides):
        raise HTTPException(status_code=404, detail="Slide not found")
    rendu = render_post([post.slides[index - 1]], post.template)[0]
    return Response(content=rendu.png, media_type="image/png")


def _texte_legende(post: Post) -> str:
    parties = [post.caption]
    if post.hashtags:
        parties.append(" ".join(post.hashtags))
    parties.append(f"Source : {post.article_url}")
    return "\n\n".join(parties)


@router.get("/posts/{post_id}/export.zip")
def exporter_post(post_id: int, db: Session = Depends(get_db)):
    post = _post_ou_404(db, post_id)
    rendus = render_post(post.slides, post.template)

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for numero, rendu in enumerate(rendus, start=1):
            archive.writestr(f"slide_{numero:02d}.png", rendu.png)
        archive.writestr("caption.txt", _texte_legende(post))

    # En-tete en ASCII pur : un nom de fichier accentue casse l'envoi
    return Response(
        content=tampon.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="kinda_post_{post.id}.zip"'},
    )
