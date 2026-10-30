import base64
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.choix_visuels import (
    AucunVisuel,
    ChoixVisuels,
    VisuelIntrouvable,
    VisuelsDifferes,
    choisir_visuels,
    compte_du_visuel,
)
from app.content.compose import (
    CompositionError,
    appeler_compositeur,
    create_post,
    make_claude_composer,
    resoudre_fond,
)
from app.content.export import construire_zip
from app.content.lecture_artistes import LecteurComptes
from app.content.legende import texte_legende
from app.content.render import RenduIndisponible, render_covers, render_post
from app.content.teinte import palette_du_post
from app.content.visuels import fond_de_cover
from app.db import get_db
from app.models import Article, Creneau, EtatCreneau, Post, PostStatus
from app.schedule.publication_incertaine import erreur_incertaine
from app.schedule.reouverture import ReouvertureRefusee, rouvrir
from app.schedule.runner import RAISON_RECLAMATION, RECLAMATION_EN_COURS
from app.publish.preparation import candidats_editables
from app.publish.televersement import TAILLE_MAX_OCTETS, DepotRefuse, accepter
from app.schedule.store import utc
from app.schemas import CoversOut, PostOut, PostUpdate

router = APIRouter(tags=["posts"])


@router.post("/articles/{article_id}/posts", response_model=PostOut, status_code=201)
def creer_post(article_id: int, db: Session = Depends(get_db)):
    """Genere un brouillon de carrousel. Un appel Claude, declenche a la main :
    le pipeline planifie ne cree jamais de post."""
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    # Les visuels d'abord, avec le meme budget de lecture que l'agent. Sans visuel
    # autorise, le post est quand meme compose (la regle vise la file) : fond vide,
    # a completer a la main.
    try:
        visuels = choisir_visuels(
            db, article, maintenant=dt.datetime.now(dt.timezone.utc), lecteur=LecteurComptes()
        )
    except (AucunVisuel, VisuelIntrouvable):
        # `VisuelIntrouvable` depuis le 2026-10-05 : les chemins « rien trouve,
        # web compris » de `choisir_visuels` la levent au lieu d'`AucunVisuel`.
        # Les DEUX gardent ici l'echappatoire MANUELLE d'avant (brouillon compose
        # a fond vide, a completer a la main) : c'est la FILE que la regle vise,
        # pas le geste de l'utilisateur.
        visuels = ChoixVisuels()
    except VisuelsDifferes as exc:
        raise HTTPException(
            status_code=503, detail=f"visuels d'artistes momentanement indisponibles : {exc}"
        ) from exc
    # Le fond AVANT l'appel paye (Sonnet), comme le planificateur (Tache 5,
    # 2026-09-21) : `resoudre_fond` leve `VisuelIntrouvable` quand toute
    # l'echelle (actu, compte, artiste, web) n'a rien donne d'assez net --
    # l'aplat Kinda n'est plus une issue publiable. Son resultat est repasse a
    # `create_post` pour ne jamais rouvrir une reserve reseau deux fois.
    try:
        fond_precalcule = resoudre_fond(visuels, "kinda_post")
    except VisuelsDifferes as exc:
        # `resoudre_fond` peut lever la MEME exception que `choisir_visuels`
        # (une reserve n'a pas pu etre consultee) : meme 503 que celui rendu
        # quelques lignes plus haut pour ce cas.
        raise HTTPException(
            status_code=503, detail=f"visuels d'artistes momentanement indisponibles : {exc}"
        ) from exc
    except VisuelIntrouvable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    composer = make_claude_composer()
    try:
        draft = appeler_compositeur(composer, article, compte_du_visuel(visuels))
    except CompositionError as exc:
        # Erreur amont (Claude) : une reponse HTTP lisible, pas un 500 avec trace.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return create_post(db, article, draft, visuels=visuels, fond_precalcule=fond_precalcule)


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


def _creneaux_engages(db: Session, post: Post) -> list[Creneau]:
    return list(
        db.scalars(
            select(Creneau).where(
                Creneau.post_id == post.id, Creneau.etat.in_([EtatCreneau.GELE, EtatCreneau.ECHEC])
            )
        )
    )


def _publication_en_vol(db: Session, post: Post) -> bool:
    """RECLAME recemment par l'agent : le conteneur est en creation chez Meta, ou
    l'agent attend l'heure affichee dans le tunnel ouvert.

    Plus RIEN ne se modifie, legende comprise : `runner._publier` a deja appele
    `texte_legende(post)` et passe le texte a `publier`, donc une edition
    n'arriverait plus a temps et ferait croire le contraire. Une vieille
    reclamation (processus tue) ne fige plus rien : c'est un echec editable.
    """
    maintenant = dt.datetime.now(dt.timezone.utc)
    return any(
        creneau.erreur == RAISON_RECLAMATION
        and utc(creneau.publier_a) > maintenant - RECLAMATION_EN_COURS
        for creneau in _creneaux_engages(db, post)
    )


def _creneaux_geles(db: Session, post: Post) -> list[Creneau]:
    """Les creneaux GELES qui portent ce post : leurs fichiers figes (cover rendue
    par Chromium, photos recadrees, videos transcodees) sont deja sur le disque."""
    return [c for c in _creneaux_engages(db, post) if c.etat == EtatCreneau.GELE]


# Ce dont les FICHIERS FIGES dependent, et rien d'autre. `caption` et `hashtags`
# n'y sont PAS : `runner._publier` calcule `texte_legende(post)` juste avant
# l'appel a Meta, en lisant la base -- la legende n'est donc jamais figee, et le
# 409 qui la bloquait aussi fermait la porte exactement dans la fenetre ou
# l'utilisateur veut corriger (« modifier la description »). C'etait une
# sur-portee, pas une protection : rien dans le gel ne depend de ces deux champs.
#
# `carrousel_explicite` en fait PARTIE : arreter la liste change ce que
# `medias_du_carrousel` rendra, donc les fichiers a figer.
CHAMPS_FIGES = ("slides", "variante_retenue", "template", "carrousel_explicite")


def _regel_automatique(db: Session, post: Post) -> None:
    """Invalide le gel des creneaux qui portent ce post, avant de l'editer.

    LE CHOIX DE L'UTILISATEUR (2026-10-04) : « on modifie, le systeme refait ce
    qui a change et rien d'autre. Aucun bouton a cliquer, et l'etat affiche est
    toujours celui qui partira. » Le 409 d'avant renvoyait vers
    `POST /schedule/{id}/rouvrir` -- une porte que l'utilisateur devait trouver,
    comprendre et cliquer pour obtenir ce qu'il venait justement de demander.

    CE QUI N'EST PAS REFAIT POUR AUTANT : rien d'autre que les fichiers figes.
    Claude n'est JAMAIS repaye, le creneau garde son `post_id` (c'est la
    condition meme de l'ecriture conditionnelle), la legende et les hashtags ne
    passent pas par ici (relus en base juste avant Meta), et le re-gel lui-meme
    est le gel ORDINAIRE du passage suivant -- aucun second chemin.

    REFUSER PLUTOT QUE PUBLIER AUTRE CHOSE : un refus de reouverture (publication
    en vol, moins de 20 min avant le depart, agent passe entre-temps) devient un
    409 qui porte la raison en clair, et AUCUNE edition n'est appliquee. L'ancien
    comportement -- editer le brouillon pendant que les fichiers figes disent
    autre chose -- etait precisement le defaut a supprimer.
    """
    maintenant = dt.datetime.now(dt.timezone.utc)
    for creneau in _creneaux_geles(db, post):
        try:
            rouvrir(db, creneau, maintenant)
        except ReouvertureRefusee as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


# Le statut d'un post n'appartient qu'au robot (`runner._publier`, sans passer par
# ici). « Valider » n'etait lu par personne, et « publie » a la main mentait sur le
# compte et bloquait la publication reelle ; revenir a « brouillon » rendait un post
# publie supprimable, donc l'archive editoriale.
STATUT_REFUSE = "Le statut d'un post est posé par le robot quand il publie : il ne se change pas à la main."


@router.patch("/posts/{post_id}", response_model=PostOut)
def modifier_post(post_id: int, payload: PostUpdate, db: Session = Depends(get_db)):
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if payload.status is not None:
        # AVANT tout le reste : un refus n'applique rien et ne rouvre aucun gel.
        raise HTTPException(status_code=409, detail=STATUT_REFUSE)

    edite_le_contenu = any(
        valeur is not None
        for valeur in (
            payload.slides, payload.variante_retenue, payload.caption, payload.hashtags,
            payload.template, payload.carrousel_explicite,
        )
    )
    if edite_le_contenu and _publication_en_vol(db, post):
        # Le conteneur est parti chez Meta avec son texte : plus rien a changer.
        raise HTTPException(
            status_code=409,
            detail="Publication en cours : le conteneur est cree chez Meta, le post ne se modifie plus",
        )
    # `variante_retenue` est VALIDE ICI, avant tout re-gel : sortir des bornes est
    # la seule faute que cet endpoint peut encore refuser, et la refuser APRES
    # avoir jete des fichiers figes couterait un gel pour rien.
    variante = None
    if payload.variante_retenue is not None:
        variantes = (post.variants or {}).get("variantes") or []
        if not (0 <= payload.variante_retenue < len(variantes)):
            # 422 a la frontiere plutot qu'un IndexError plus tard dans le renderer.
            raise HTTPException(status_code=422, detail="variante_retenue hors bornes")
        variante = variantes[payload.variante_retenue]
    if any(getattr(payload, champ) is not None for champ in CHAMPS_FIGES):
        # Les fichiers figes ne portent pas cette edition : on les invalide, et le
        # gel ordinaire du passage suivant les refait. Automatique, jamais un
        # bouton a cliquer -- et si la reouverture est refusee (publication en vol,
        # trop pres du depart), rien n'est edite du tout.
        _regel_automatique(db, post)

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
    if payload.template is not None:
        # `layout.GABARITS` deja verifie par le schema : ici on ne fait que
        # persister. La cover (slides[0]) porte deja les trois textes -- la
        # bascule est donc instantanee, aucun rendu ni appel Claude requis.
        post.template = payload.template
        # Un gabarit different change ce qui sera rendu (zones, tailles) :
        # meme raison que pour `slides` plus haut, un ancien rapport ne parle
        # plus du rendu a venir.
        post.render_report = None
    if payload.carrousel_explicite is not None:
        # La liste des elements est ARRETEE : le gel ne reprend plus ni
        # `posts.assets` ni les medias de l'article, `slides` fait foi
        # (`preparation.liste_explicite`). Reassignation ENTIERE et FUSION du dict
        # JSON -- l'ecrire en bloc ferait disparaitre la palette et l'origine du
        # fond, figees a la composition.
        post.variants = {
            **(post.variants or {}),
            "carrousel": {"explicite": bool(payload.carrousel_explicite)},
        }
    if payload.variante_retenue is not None:
        # Bornes deja verifiees plus haut, AVANT le re-gel : `variante` est prete.
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


def _trop_lourd() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"fichier trop lourd (maximum {TAILLE_MAX_OCTETS // 1024 // 1024} Mo)",
    )


async def _lire_sous_plafond(request: Request) -> bytes:
    """Le corps de la requete, borne AVANT d'etre tenu en memoire.

    Deux gardes, et la premiere ne lit pas un octet : un `Content-Length` annonce
    au-dela du plafond est refuse tout de suite. La seconde lit le flux morceau
    par morceau et s'arrete au premier depassement -- un en-tete peut mentir, et
    un envoi en `chunked` n'en porte pas. `await request.body()` aurait tout
    accumule avant qu'on puisse dire non.
    """
    annonce = request.headers.get("content-length")
    if annonce and annonce.isdigit() and int(annonce) > TAILLE_MAX_OCTETS:
        raise _trop_lourd()
    octets = bytearray()
    async for morceau in request.stream():
        octets += morceau
        if len(octets) > TAILLE_MAX_OCTETS:
            raise _trop_lourd()
    return bytes(octets)


@router.post("/posts/{post_id}/visuels", status_code=201)
async def televerser_visuel(post_id: int, request: Request, db: Session = Depends(get_db)):
    """Depose un visuel du disque et le rend comme UN CANDIDAT DE PLUS.

    Tout ce que fait cet endpoint : verifier le fichier, le copier dans
    `visuels_cache_dir`, l'ajouter a `posts.assets`. Il ne touche NI `slides` NI
    les fichiers figes -- mettre le visuel dans la liste publiee est le geste
    suivant, et il passe par `PATCH /posts/{id}` (`slides` +
    `carrousel_explicite`), qui rouvre deja le creneau tout seul. Un seul chemin
    d'ecriture pour la liste, un seul endroit ou le re-gel se decide, et rien ici
    qui approche Meta.

    LE CORPS EST LE FICHIER BRUT, pas un `multipart/form-data` : aucun nom de
    fichier n'arrive donc jusqu'ici (voir `app/publish/televersement.py` : le nom
    sur disque se derive du CONTENU), et le depot n'ajoute aucune dependance
    Python -- `python-multipart` n'est pas installe, et un formulaire
    n'apporterait rien d'autre qu'un nom qu'on refuserait d'utiliser.

    `Content-Type` n'est pas lu : le type REEL vient du decodage de l'image.
    """
    post = _post_ou_404(db, post_id)
    if _publication_en_vol(db, post):
        # Le conteneur est parti chez Meta avec son contenu : accepter un visuel
        # laisserait croire qu'il peut encore entrer dans ce post.
        raise HTTPException(
            status_code=409,
            detail="Publication en cours : le conteneur est cree chez Meta, le post ne se modifie plus",
        )
    octets = await _lire_sous_plafond(request)
    try:
        nouvel_actif = accepter(octets)
    except DepotRefuse as exc:
        # 422 et non 400 : la requete est bien formee, c'est son CONTENU qui ne
        # peut pas etre publie -- et le message est fait pour etre affiche tel quel.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Reassignation ENTIERE : une mutation in place ne serait pas persistee sur la
    # colonne JSON.
    post.assets = [*(post.assets or []), nouvel_actif]
    db.commit()
    # On rend le candidat tel que `GET /relecture` le rendra -- meme fonction,
    # donc meme forme : le dashboard l'ajoute a la liste sans recroiser deux
    # reponses ni deviner la forme attendue par `PATCH /posts/{id}`.
    article = db.get(Article, post.article_id) if post.article_id else None
    nouveau = next(
        (c for c in candidats_editables(post, article) if c.get("url") == nouvel_actif["url"]),
        None,
    )
    if nouveau is None:  # pragma: no cover -- invariant de construction
        raise HTTPException(status_code=500, detail="visuel depose mais absent de la reserve")
    return nouveau


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

    Un post encore porte par un creneau non plus (2026-10-30) : la suppression
    vidait `post_id` (ON DELETE SET NULL), et le creneau repayait Claude pour le
    recomposer. Son retrait, c'est « Ne pas publier ». Un post peut-etre en ligne
    (ECHEC de reclamation, meme ancienne) non plus : un retard jusqu'a la limite peut
    encore etre en vol apres `_publication_en_vol`, et ce brouillon est la seule trace
    de ce qui est parti.
    """
    post = _post_ou_404(db, post_id)
    if _publication_en_vol(db, post):
        raise HTTPException(
            status_code=409,
            detail="Publication en cours : le conteneur est cree chez Meta, le post ne se modifie plus",
        )
    if post.status is not PostStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Seul un brouillon peut etre supprime")
    if db.scalar(
        select(Creneau.id)
        .where(
            Creneau.post_id == post.id,
            Creneau.etat.in_([EtatCreneau.LIBRE, EtatCreneau.PROGRAMME, EtatCreneau.GELE]),
        )
        .limit(1)
    ):
        raise HTTPException(
            status_code=409,
            detail="Ce post est au programme : utilise « Ne pas publier » pour le retirer, le brouillon pourra se supprimer ensuite.",
        )
    if db.scalar(
        select(Creneau.id)
        .where(
            Creneau.post_id == post.id,
            Creneau.etat == EtatCreneau.ECHEC,
            erreur_incertaine(Creneau.erreur),
        )
        .limit(1)
    ):
        raise HTTPException(
            status_code=409,
            detail="Ce post est peut-être en ligne : sa publication s'est interrompue sans confirmation. Vérifie le compte depuis la carte de l'article.",
        )
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
    # `fond_de_cover` et non une lecture directe : meme normalisation que la file
    # et le gel (un `str(None)` vaut un fond absent, cf. app.content.visuels).
    try:
        rendus = render_covers(
            variantes, fond_de_cover(post), post.template, palette_du_post(post)
        )
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
        rendus = render_post(post.slides, post.template, palette_du_post(post))
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


@router.get("/posts/{post_id}/export.zip")
def exporter_post(post_id: int, db: Session = Depends(get_db)):
    post = _post_ou_404(db, post_id)
    try:
        # `render_post` ne rend plus que les covers (kind: "cover") -- les
        # medias de l'actu (kind: "media") sont a telecharger tels quels,
        # role que `construire_zip` prend en charge.
        rendus = render_post(post.slides, post.template, palette_du_post(post))
    except RenduIndisponible as exc:
        # 503 et pas 500 : la cause est une dependance locale manquante, pas un bug.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # L'article peut avoir ete purge (14 jours de retention) : le lien casse
    # (`article_id` -> NULL) et l'export continue sans policy applicable.
    article = db.get(Article, post.article_id) if post.article_id else None
    zip_octets = construire_zip(post, [rendu.png for rendu in rendus], texte_legende(post), article=article)

    # En-tete en ASCII pur : un nom de fichier accentue casse l'envoi
    return Response(
        content=zip_octets,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="kinda_post_{post.id}.zip"'},
    )
