"""La file de publication, pilotable depuis le dashboard.

Seuls les creneaux LIBRE ou PROGRAMME et encore a venir se modifient : un creneau
gele a deja ses fichiers prets, et changer son article a T-2h publierait autre
chose que ce qui a ete fige. Toute action manuelle EPINGLE le creneau, sinon
l'arbitrage suivant la defait dans les 10 minutes.
"""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Article, ArticleStatus, Creneau, EtatCreneau
from app.schedule.store import MODIFIABLES, VERROUILLES, appliquer_arbitrage, post_a_reprendre, utc
from app.schemas import CreneauOut, CreneauUpdate

router = APIRouter(tags=["schedule"])


def _maintenant() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _vue(db: Session, creneau: Creneau) -> CreneauOut:
    article = db.get(Article, creneau.article_id) if creneau.article_id else None
    return CreneauOut(
        id=creneau.id,
        publier_a=utc(creneau.publier_a),
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
        creneau.post_id = post_a_reprendre(db, article.id)
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
            .where(Creneau.publier_a > creneau.publier_a, Creneau.etat.in_(MODIFIABLES))
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
