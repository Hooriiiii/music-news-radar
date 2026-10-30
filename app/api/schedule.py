"""La file de publication, pilotable depuis le dashboard.

Seuls les creneaux LIBRE ou PROGRAMME et encore a venir se modifient : un creneau
gele a deja ses fichiers prets, et changer son article a T-2h publierait autre
chose que ce qui a ete fige. Toute action manuelle EPINGLE le creneau, sinon
l'arbitrage suivant la defait dans les 10 minutes.

Une seule exception, « Ne pas publier » (`sauter`) : RETIRER ne publie rien d'autre,
donc ce geste vaut aussi sur un GELE, et apres l'heure tant que le robot peut partir.

Un creneau reel n'accepte qu'un article eligible en reel, et decaler n'echange
qu'avec un creneau du meme format. `/publication/reels` coupe ou rallume les reels
automatiques ; yt-dlp seul se coupe dans `.env`.
"""

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import settings
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau
from app.publish.mode import enregistrer_mode, enregistrer_reels, mode_effectif, reels_actifs, source_du_mode
from app.schedule.reouverture import ReouvertureRefusee, ne_pas_publier, rouvrir
from app.schedule.store import (
    MODIFIABLES,
    TEMPLATES,
    VERROUILLES,
    appliquer_arbitrage,
    offre_reels,
    post_a_reprendre,
    raison_ineligible_reel,
    raison_sans_visuel,
    utc,
)
from app.schemas import CreneauOut, CreneauUpdate, ModeOut, ModeUpdate, OffreReelsOut, ReelsOut, ReelsUpdate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["schedule"])

# Passer en `reel` publie sur le vrai compte Instagram, publiquement et de
# facon irreversible : on exige que l'utilisateur tape ce mot en confirmation,
# pas une simple case a cocher qu'un clic malheureux activerait.
CONFIRMATION_REEL = "PUBLIER"


def _maintenant() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _vue(db: Session, creneau: Creneau) -> CreneauOut:
    article = db.get(Article, creneau.article_id) if creneau.article_id else None
    return CreneauOut(
        id=creneau.id,
        publier_a=utc(creneau.publier_a),
        format=creneau.format,
        etat=creneau.etat,
        epingle=creneau.epingle,
        article_id=creneau.article_id,
        article_title=article.title if article else None,
        article_url=article.url if article else None,
        relevance=article.relevance_score if article else None,
        post_id=creneau.post_id,
        ig_media_id=creneau.ig_media_id,
        erreur=creneau.erreur,
    )


def _liste(db: Session, maintenant: dt.datetime) -> list[CreneauOut]:
    depuis = maintenant - dt.timedelta(hours=24)
    creneaux = db.scalars(select(Creneau).where(Creneau.publier_a >= depuis).order_by(Creneau.publier_a))
    return [_vue(db, creneau) for creneau in creneaux]


def _reprogrammer(creneau: Creneau) -> None:
    creneau.etat = EtatCreneau.PROGRAMME if creneau.article_id else EtatCreneau.LIBRE
    creneau.epingle = creneau.article_id is not None


@router.get("/schedule", response_model=list[CreneauOut])
def lister_file(db: Session = Depends(get_db)):
    return _liste(db, _maintenant())


@router.get("/schedule/offre-reels", response_model=OffreReelsOut)
def lire_offre_reels(db: Session = Depends(get_db)):
    return OffreReelsOut(**offre_reels(db, _maintenant()))


@router.get("/publication/mode", response_model=ModeOut)
def lire_mode():
    return ModeOut(mode=mode_effectif(), source=source_du_mode())


@router.put("/publication/mode", response_model=ModeOut)
def changer_mode(payload: ModeUpdate):
    if payload.mode == "reel" and payload.confirmation != CONFIRMATION_REEL:
        raise HTTPException(
            status_code=409,
            detail=f"passer en reel exige confirmation=\"{CONFIRMATION_REEL}\" : publication publique et irreversible",
        )
    enregistrer_mode(payload.mode, _maintenant())
    return ModeOut(mode=mode_effectif(), source=source_du_mode())


def _etat_reels() -> ReelsOut:
    return ReelsOut(actifs=reels_actifs(), ytdlp=settings.publication_reel_ytdlp)


@router.get("/publication/reels", response_model=ReelsOut)
def lire_reels():
    return _etat_reels()


@router.put("/publication/reels", response_model=ReelsOut)
def changer_reels(payload: ReelsUpdate, db: Session = Depends(get_db)):
    # Pas de confirmation : couper ne publie rien, et rallumer reste soumis au
    # mode (arret/apercu/reel), qui a la sienne.
    maintenant = _maintenant()
    enregistrer_reels(payload.actifs, maintenant)
    # La file reflete le choix tout de suite, sans attendre l'agent : les
    # creneaux reel a venir non epingles disparaissent (ou reviennent).
    appliquer_arbitrage(db, maintenant)
    return _etat_reels()


@router.post("/schedule/rebuild", response_model=list[CreneauOut])
def recalculer_file(db: Session = Depends(get_db)):
    maintenant = _maintenant()
    appliquer_arbitrage(db, maintenant)
    return _liste(db, maintenant)


@router.patch("/schedule/{creneau_id}", response_model=CreneauOut)
def modifier_creneau(creneau_id: int, payload: CreneauUpdate, db: Session = Depends(get_db)):
    creneau = db.get(Creneau, creneau_id)
    if creneau is None:
        raise HTTPException(status_code=404, detail="Creneau introuvable")
    maintenant = _maintenant()
    actions = [payload.epingle is not None, payload.article_id is not None, payload.sauter, payload.decaler]
    if sum(actions) != 1:
        raise HTTPException(status_code=422, detail="Une seule action a la fois : epingle, article_id, sauter ou decaler")
    if payload.sauter:
        return _ne_pas_publier(db, creneau, maintenant)
    if creneau.etat not in MODIFIABLES or utc(creneau.publier_a) <= maintenant:
        raise HTTPException(status_code=409, detail="Creneau gele ou passe : il ne se modifie plus")

    if payload.epingle is not None:
        if payload.epingle and creneau.article_id is None:
            raise HTTPException(status_code=409, detail="rien a epingler")
        creneau.epingle = payload.epingle
    elif payload.article_id is not None:
        article = db.get(Article, payload.article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="Article introuvable")
        if article.status == ArticleStatus.USED:
            raise HTTPException(status_code=409, detail="Article deja publie")
        if article.duplicate_of_id is not None:
            raise HTTPException(status_code=409, detail="Article duplique")
        if article.file_exclu_raison is not None:
            # Choisi a la main, un article exclu reste refuse : la securite
            # prime, l'exclusion se leve separement si besoin.
            raise HTTPException(status_code=409, detail="Article exclu de la file")
        if creneau.format == FormatCreneau.REEL:
            # Meme regle que l'arbitrage : la securite prime sur le choix manuel
            # (credit obligatoire, jamais un compte media).
            raison = raison_ineligible_reel(article)
            if raison is not None:
                raise HTTPException(status_code=409, detail=f"Article inutilisable en reel : {raison}")
        else:
            raison = raison_sans_visuel(article)
            if raison is not None:
                raise HTTPException(status_code=409, detail=f"Article sans visuel autorise : {raison}")
        if db.scalar(
            select(Creneau.id).where(
                Creneau.article_id == article.id,
                Creneau.id != creneau.id,
                Creneau.etat.in_(VERROUILLES),
            )
        ):
            raise HTTPException(status_code=409, detail="Article deja engage sur un autre creneau")
        # Un article n'occupe qu'un creneau a la fois : les autres creneaux
        # modifiables qui le portaient sont liberes, passes ou futurs.
        for autre in db.scalars(
            select(Creneau).where(
                Creneau.article_id == article.id,
                Creneau.id != creneau.id,
                Creneau.etat.in_(MODIFIABLES),
            )
        ):
            autre.article_id = None
            autre.post_id = None
            _reprogrammer(autre)
        creneau.article_id = article.id
        # Le brouillon repris doit etre du gabarit du creneau : jamais un
        # carrousel accroche a un reel, ni l'inverse.
        creneau.post_id = post_a_reprendre(db, article.id, TEMPLATES[creneau.format])
        _reprogrammer(creneau)
    else:
        suivant = db.scalar(
            select(Creneau)
            # Un echange ne change jamais le format d'un article : un reel ne
            # glisse pas sur un creneau carrousel.
            .where(
                Creneau.publier_a > creneau.publier_a,
                Creneau.format == creneau.format,
                Creneau.etat.in_(MODIFIABLES),
            )
            .order_by(Creneau.publier_a)
            .limit(1)
        )
        if suivant is None:
            raise HTTPException(status_code=409, detail="Aucun creneau modifiable apres celui-ci")
        creneau.article_id, suivant.article_id = suivant.article_id, creneau.article_id
        creneau.post_id, suivant.post_id = suivant.post_id, creneau.post_id
        _reprogrammer(creneau)
        _reprogrammer(suivant)

    db.commit()
    return _vue(db, creneau)


def _ne_pas_publier(db: Session, creneau: Creneau, maintenant: dt.datetime) -> CreneauOut:
    try:
        ne_pas_publier(db, creneau)
    except ReouvertureRefusee as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    vue = _vue(db, creneau)
    # Le creneau libere se remplit tout de suite avec le candidat suivant ; a defaut
    # l'agent le fera au passage suivant. Le retrait est committe : un 500 ici dirait
    # a l'utilisateur qu'il a echoue.
    try:
        appliquer_arbitrage(db, maintenant)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("arbitrage impossible apres « Ne pas publier » du creneau %s", vue.id)
    return vue


@router.post("/schedule/{creneau_id}/rouvrir", response_model=CreneauOut)
def rouvrir_creneau(creneau_id: int, db: Session = Depends(get_db)):
    """Rend un creneau GELE a l'edition, en garantissant le re-gel.

    POURQUOI LE 409 EXISTAIT, ET POURQUOI ON NE LE LEVE PAS SANS CA. Le gel a
    rendu la cover par Chromium, telecharge et recadre les photos, transcode les
    videos : `creneaux.fichiers` est la liste de ce qui va PARTIR. Editer la cover
    ou les slides sans toucher a ces fichiers produirait l'ecart le plus cher
    qu'une interface puisse produire -- on verrait une chose, Meta en recevrait
    une autre. On ne rouvre donc pas l'edition : on INVALIDE le gel, et le gel
    normal (`runner._geler`, chaque passage de l'agent) refait les fichiers.

    CE QUI EST GRATUIT N'EST PAS ICI. La legende et les hashtags ne sont PAS
    figes : `runner._publier` appelle `texte_legende(post)` juste avant de
    publier, donc une edition de legende s'applique telle quelle sans rien
    refaire. Ils sont restes editables sur un creneau gele
    (`app/api/posts.py`) ; seuls la cover, les slides et le gabarit -- ce dont
    les fichiers figes dependent -- passent par ici.

    TROIS REFUS, chacun avec sa raison EN CLAIR (jamais un bouton gris et muet) :
    pas gele (rien a rouvrir, tout se modifie deja), publication EN VOL (le
    conteneur existe chez Meta, l'etat est ECHEC avec la raison de reclamation),
    et trop pres du depart pour re-geler.

    AUCUNE DOUBLE PUBLICATION POSSIBLE. L'ecriture est CONDITIONNELLE sur
    (etat, article, post), comme la reclamation de l'agent : un creneau deja
    reclame est en ECHEC, donc hors de la condition `etat == GELE`, et si l'agent
    reclame entre notre lecture et notre ecriture, zero ligne est touchee et on
    rend 409. Rien n'est envoye a Meta ici, et apres cet appel le creneau est
    PROGRAMME -- or `_publier` ne selectionne que `etat == GELE`, donc aucun
    chemin ne mene a `media_publish` avant un nouveau gel.

    EPINGLE, et c'est necessaire. Un creneau PROGRAMME non epingle est repris par
    `appliquer_arbitrage` au passage suivant (10 min) : le post que l'utilisateur
    vient de rouvrir pour le corriger serait remplace par le meilleur candidat du
    moment. Convention deja ecrite en tete de ce module -- toute action manuelle
    epingle.
    """
    creneau = db.get(Creneau, creneau_id)
    if creneau is None:
        raise HTTPException(status_code=404, detail="Creneau introuvable")
    # Le geste lui-meme vit dans `app/schedule/reouverture.py` : depuis que
    # `PATCH /posts/{id}` rouvre TOUT SEUL (re-gel automatique, demande de
    # l'utilisateur), deux copies de cette ecriture conditionnelle finiraient par
    # divergeer -- et c'est l'ecriture la plus delicate du depot, celle qui croise
    # la reclamation de l'agent.
    try:
        rouvrir(db, creneau, _maintenant())
    except ReouvertureRefusee as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _vue(db, creneau)
