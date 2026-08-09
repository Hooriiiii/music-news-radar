import base64
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.compose import CompositionError, create_post, make_claude_composer
from app.content.export import construire_zip
from app.content.render import RenduIndisponible, render_covers, render_post
from app.db import get_db
from app.models import Article, ArticleStatus, Post, PostStatus
from app.schemas import CoversOut, PostOut, PostUpdate

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
        # Un rapport de rendu parle d'un texte precis : une fois les slides
        # changees (l'editeur de carrousel EST une surface d'edition
        # d'accroche), un ancien rapport "overflow" ou "clean" ne veut plus rien
        # dire -- et un ancien "clean" perime serait un faux feu vert sur le
        # signal qui doit justement empecher une publication en defaut.
        post.render_report = None
    if payload.status is not None:
        post.status = payload.status
        if payload.status is PostStatus.PUBLISHED:
            post.published_at = dt.datetime.now(dt.timezone.utc)
            # L'article passe en USED : c'est ce qui le protege de la purge et
            # evite qu'il soit repropose plus tard
            article = db.get(Article, post.article_id) if post.article_id else None
            if article is not None:
                article.status = ArticleStatus.USED
    if payload.template is not None:
        # `layout.GABARITS` deja verifie par le schema : ici on ne fait que
        # persister. La cover (slides[0]) porte deja les trois textes -- la
        # bascule est donc instantanee, aucun rendu ni appel Claude requis.
        post.template = payload.template
        # Un gabarit different change ce qui sera rendu (zones, tailles) :
        # meme raison que pour `slides` plus haut, un ancien rapport ne parle
        # plus du rendu a venir.
        post.render_report = None
    if payload.variante_retenue is not None:
        variantes = (post.variants or {}).get("variantes") or []
        if not (0 <= payload.variante_retenue < len(variantes)):
            # 422 a la frontiere plutot qu'un IndexError plus tard dans le renderer.
            raise HTTPException(status_code=422, detail="variante_retenue hors bornes")
        variante = variantes[payload.variante_retenue]
        # Reassignation ENTIERE du dict JSON, jamais de mutation in place.
        post.variants = {**post.variants, "retenue": payload.variante_retenue}
        # La cover est REECRITE avec les trois textes de la variante retenue,
        # mais le fond (image_url) est le choix de l'utilisateur et doit
        # survivre a un changement d'avis sur la variante. `post.slides` peut
        # etre vide (schema le permet) : pas de slide[0] a fusionner, on part
        # de la variante seule.
        cover_existante = post.slides[0] if post.slides else {"kind": "cover"}
        reste = post.slides[1:] if post.slides else []
        post.slides = [{**cover_existante, **variante}, *reste]
        # Un autre texte pour la meme cover : meme raison que pour `slides` et
        # `template` ci-dessus.
        post.render_report = None

    db.commit()
    return post


def _post_ou_404(db: Session, post_id: int) -> Post:
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.delete("/posts/{post_id}", status_code=204)
def supprimer_post(post_id: int, db: Session = Depends(get_db)):
    """Supprime un brouillon.

    La frontiere n'est PAS "un brouillon sans variante retenue" (le libelle de
    la spec) mais la PUBLICATION : la regle "jamais purge" protege l'archive
    editoriale -- savoir ce qui a ete publie, ne pas le reposter -- pas le
    choix d'une variante. Un brouillon dont l'utilisateur a retenu une
    variante puis change d'avis doit rester supprimable ; seuls APPROVED et
    PUBLISHED repondent 409.
    """
    post = _post_ou_404(db, post_id)
    if post.status is not PostStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Seul un brouillon peut etre supprime")
    db.delete(post)
    db.commit()
    return Response(status_code=204)


@router.post("/posts/{post_id}/covers", response_model=CoversOut)
def rendre_covers(post_id: int, db: Session = Depends(get_db)):
    """Rend les 4 variantes sur le MEME fond, en data URIs.

    Deux endpoints et non un seul : `POST /articles/{id}/posts` paie un appel
    Claude (la generation des 4 variantes), celui-ci paie un lancement
    Chromium (le rendu). Les separer permet a un rechargement de page, ou un
    changement de l'URL de fond (PATCH sur `slides`), de re-rendre SANS
    repayer Claude -- c'est le genre de decoupage qu'on est tente de
    "simplifier" en un seul appel plus tard ; ne pas le faire.

    Reponse en data URIs et pas en fichiers sur disque : quatre PNG en base64
    pesent ~800 Ko de JSON, instantane sur localhost et sans etat cote
    serveur. Le prix est qu'un rechargement de page re-rend -- exactement ce
    que cet endpoint, appelable seul, est fait pour absorber.
    """
    post = _post_ou_404(db, post_id)
    variantes = (post.variants or {}).get("variantes") or []
    if not variantes:
        raise HTTPException(status_code=409, detail="Post sans variantes a rendre")
    image_url = post.slides[0].get("image_url") if post.slides else None
    try:
        rendus = render_covers(variantes, image_url, post.template)
    except RenduIndisponible as exc:
        # 503 et pas 500 : la cause est une dependance locale manquante, pas un bug.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "covers": [
            "data:image/png;base64," + base64.b64encode(rendu.png).decode("ascii")
            for rendu in rendus
        ],
        "overflow": [rendu.overflow for rendu in rendus],
    }


@router.post("/posts/{post_id}/render")
def rendre_post(post_id: int, db: Session = Depends(get_db)):
    """Rend toutes les slides et PERSISTE le rapport.

    En POST et pas en GET : c'est une ecriture. Les endpoints de rendu en GET
    restent purs -- ils servent l'image, ils ne consignent rien.
    """
    post = _post_ou_404(db, post_id)
    try:
        rendus = render_post(post.slides, post.template)
    except RenduIndisponible as exc:
        # 503 et pas 500 : la cause est une dependance locale manquante, pas un bug.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    rapport = {
        "slides": [
            {"scale": rendu.scale, "lines": rendu.lines, "overflow": rendu.overflow}
            for rendu in rendus
        ]
    }
    # Reassignation ENTIERE : une mutation in place ne serait pas persistee.
    post.render_report = rapport
    db.commit()
    return rapport


def _texte_legende(post: Post) -> str:
    parties = [post.caption]
    if post.hashtags:
        parties.append(" ".join(post.hashtags))
    parties.append(f"Source : {post.article_url}")
    return "\n\n".join(parties)


@router.get("/posts/{post_id}/export.zip")
def exporter_post(post_id: int, db: Session = Depends(get_db)):
    post = _post_ou_404(db, post_id)
    try:
        # `render_post` ne rend plus que les covers (kind: "cover") -- les
        # medias de l'actu (kind: "media") sont a telecharger tels quels,
        # role que `construire_zip` prend en charge.
        rendus = render_post(post.slides, post.template)
    except RenduIndisponible as exc:
        # 503 et pas 500 : la cause est une dependance locale manquante, pas un bug.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    zip_octets = construire_zip(post, [rendu.png for rendu in rendus], _texte_legende(post))

    # En-tete en ASCII pur : un nom de fichier accentue casse l'envoi
    return Response(
        content=zip_octets,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="kinda_post_{post.id}.zip"'},
    )
