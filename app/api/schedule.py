"""La file de publication, pilotable depuis le dashboard.

Seuls les creneaux LIBRE ou PROGRAMME et encore a venir se modifient : un creneau
gele a deja ses fichiers prets, et changer son article a T-2h publierait autre
chose que ce qui a ete fige. Toute action manuelle EPINGLE le creneau, sinon
l'arbitrage suivant la defait dans les 10 minutes.

Un creneau reel n'accepte qu'un article eligible en reel, et decaler n'echange
qu'avec un creneau du meme format. `/publication/reels` coupe ou rallume les reels
automatiques ; yt-dlp seul se coupe dans `.env`.
"""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import settings
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau
from app.publish.mode import enregistrer_mode, enregistrer_reels, mode_effectif, reels_actifs, source_du_mode
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
    if creneau.etat not in MODIFIABLES or utc(creneau.publier_a) <= maintenant:
        raise HTTPException(status_code=409, detail="Creneau gele ou passe : il ne se modifie plus")
    actions = [payload.epingle is not None, payload.article_id is not None, payload.sauter, payload.decaler]
    if sum(actions) != 1:
        raise HTTPException(status_code=422, detail="Une seule action a la fois : epingle, article_id, sauter ou decaler")

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
    elif payload.sauter:
        article = db.get(Article, creneau.article_id) if creneau.article_id else None
        if article is not None:
            article.file_exclu_raison = "saute depuis le dashboard"
        creneau.article_id = None
        creneau.post_id = None
        creneau.etat = EtatCreneau.LIBRE
        creneau.epingle = False
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
    vue = _vue(db, creneau)
    if payload.sauter:
        # Le creneau libere se remplit tout de suite avec le candidat suivant.
        appliquer_arbitrage(db, maintenant)
    return vue
