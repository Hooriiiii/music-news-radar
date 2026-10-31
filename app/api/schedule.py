"""La file de publication, pilotable depuis le dashboard.

Seuls les creneaux LIBRE ou PROGRAMME et encore a venir se modifient : un creneau
gele a deja ses fichiers prets, et changer son article a T-2h publierait autre
chose que ce qui a ete fige. Toute action manuelle EPINGLE le creneau, sinon
l'arbitrage suivant la defait dans les 10 minutes.

Une seule exception, « Ne pas publier » (`sauter`) : RETIRER ne publie rien d'autre,
donc ce geste vaut aussi sur un GELE, et apres l'heure tant que le robot peut partir.

Un creneau reel n'accepte qu'un article eligible en reel, et un echange ne change
jamais le format d'un post (`programmation.raison_echange`). `/publication/reels` coupe ou rallume les reels
automatiques ; yt-dlp seul se coupe dans `.env`.
"""

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import settings
from app.models import Article, Creneau, EtatCreneau
from app.publish.mode import enregistrer_mode, enregistrer_reels, mode_effectif, reels_actifs, source_du_mode
from app.schedule import programmation
from app.schedule.relecture import RAISON_RELECTURE
from app.schedule.reouverture import OCCUPANT_CHANGE, ReouvertureRefusee, ne_pas_publier, rouvrir
from app.schedule.store import (
    MODIFIABLES,
    TEMPLATES,
    appliquer_arbitrage,
    ecrire_si_inchange,
    lecture,
    offre_reels,
    post_a_reprendre,
    utc,
)
from app.schemas import (
    ActusPossiblesOut,
    CreneauOut,
    CreneauUpdate,
    CreneauxPossiblesOut,
    EchangesPossiblesOut,
    ModeOut,
    ModeUpdate,
    OffreReelsOut,
    ReelsOut,
    ReelsUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["schedule"])

# Passer en `reel` publie sur le vrai compte Instagram, publiquement et de
# facon irreversible : on exige que l'utilisateur tape ce mot en confirmation,
# pas une simple case a cocher qu'un clic malheureux activerait.
CONFIRMATION_REEL = "PUBLIER"
VU_OBLIGATOIRE = "occupant_attendu et post_attendu sont obligatoires : ce geste vise ce que l'écran montre"
MODIFIE_ENTRE_TEMPS = "Creneau modifie entre-temps (l'agent vient peut-etre de le geler) : rien n'a ete change"


def _maintenant() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _vue(db: Session, creneau: Creneau, mode: str | None = None, articles: dict | None = None) -> CreneauOut:
    if articles is not None:
        article = articles.get(creneau.article_id)
    else:
        article = db.get(Article, creneau.article_id) if creneau.article_id else None
    return CreneauOut(
        **programmation.vue_composition(creneau, _maintenant(), mode or programmation.mode_effectif()),
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
    creneaux = list(db.scalars(select(Creneau).where(Creneau.publier_a >= depuis).order_by(Creneau.publier_a)))
    ids = {c.article_id for c in creneaux if c.article_id}
    articles = {a.id: a for a in db.scalars(select(Article).where(Article.id.in_(ids)))} if ids else {}
    mode = programmation.mode_effectif()
    return [_vue(db, creneau, mode, articles) for creneau in creneaux]


def _reprogramme(occupant: int | None) -> dict:
    """`occupant` : l'article, ou a defaut le post -- un post orphelin occupe un creneau."""
    return {
        "etat": EtatCreneau.PROGRAMME if occupant else EtatCreneau.LIBRE,
        "epingle": occupant is not None,
    }


def _vu(payload: CreneauUpdate) -> tuple[int | None, int | None]:
    """(article, post) que l'ecran montrait. `null` est une valeur : un creneau vu vide."""
    if not {"occupant_attendu", "post_attendu"} <= payload.model_fields_set:
        raise HTTPException(status_code=422, detail=VU_OBLIGATOIRE)
    return payload.occupant_attendu, payload.post_attendu


def _marqueur_du_post(creneau: Creneau) -> str | None:
    """Ce qui suit un post qu'on deplace, de `creneaux.erreur` : seul le marqueur de
    reouverture parle du POST corrige, et il protege son article d'une exclusion au gel.
    Le reste parle du creneau qu'on quitte."""
    return RAISON_RELECTURE if RAISON_RELECTURE in (creneau.erreur or "") else None


def _ecrire(db: Session, creneau: Creneau, lu: tuple, **valeurs) -> None:
    """Ecriture conditionnelle, ou 409 : `lu` a pu etre gele par l'agent depuis la
    lecture, et ecrire dessus publierait les fichiers de l'un avec la legende de
    l'autre (`publication.md`, « L'arbitrage ecrivait sur une lecture perimee »)."""
    if not ecrire_si_inchange(db, creneau, lu, **valeurs):
        db.rollback()
        raise HTTPException(status_code=409, detail=MODIFIE_ENTRE_TEMPS)


@router.get("/schedule", response_model=list[CreneauOut])
def lister_file(db: Session = Depends(get_db)):
    return _liste(db, _maintenant())


@router.get("/schedule/offre-reels", response_model=OffreReelsOut)
def lire_offre_reels(db: Session = Depends(get_db)):
    return OffreReelsOut(**offre_reels(db, _maintenant()))


@router.get("/articles/{article_id}/creneaux-possibles", response_model=CreneauxPossiblesOut)
def creneaux_possibles(article_id: int, db: Session = Depends(get_db)):
    """Pour un article, les creneaux a venir et ce qu'il adviendrait sur chacun. Lit la
    base seule, n'ecrit rien : les memes regles que le PATCH, a un seul endroit."""
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article introuvable")
    return programmation.options_de_l_article(db, article, _maintenant())


@router.get("/schedule/{creneau_id}/actus-possibles", response_model=ActusPossiblesOut)
def actus_possibles(creneau_id: int, db: Session = Depends(get_db)):
    """Le sens inverse : pour un creneau, ses actus possibles. Memes regles."""
    creneau = db.get(Creneau, creneau_id)
    if creneau is None:
        raise HTTPException(status_code=404, detail="Creneau introuvable")
    return programmation.actus_du_creneau(db, creneau, _maintenant())


@router.get("/schedule/{creneau_id}/echanges-possibles", response_model=EchangesPossiblesOut)
def echanges_possibles(creneau_id: int, db: Session = Depends(get_db)):
    """Avec quels creneaux celui-ci peut permuter, et pourquoi pas les autres. Memes regles
    que le PATCH, a un seul endroit ; rien n'est ecrit."""
    creneau = db.get(Creneau, creneau_id)
    if creneau is None:
        raise HTTPException(status_code=404, detail="Creneau introuvable")
    return programmation.echanges_du_creneau(db, creneau, _maintenant())


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
    actions = [payload.epingle is not None, payload.article_id is not None, payload.sauter,
               payload.echanger_avec is not None]
    if sum(actions) != 1:
        raise HTTPException(status_code=422, detail="Une seule action a la fois : epingle, article_id, sauter ou echanger_avec")
    if payload.sauter:
        return _ne_pas_publier(db, creneau, _vu(payload), maintenant)
    if payload.echanger_avec is not None:
        return _echanger(db, creneau.id, payload, maintenant)
    raison = programmation.raison_creneau(creneau, maintenant)
    if raison is not None:
        raise HTTPException(status_code=409, detail=raison)
    lu = lecture(creneau)

    if payload.epingle is not None:
        vu = _vu(payload)
        if payload.epingle and vu[0] is None and creneau.article_id is None:
            raise HTTPException(status_code=409, detail="rien a epingler")
        # Conditionnelle sur ce que l'ecran montrait, pas sur ce qu'on vient de lire.
        if not ecrire_si_inchange(db, creneau, (lu[0], *vu), epingle=payload.epingle):
            db.rollback()
            db.refresh(creneau)
            change = (creneau.article_id, creneau.post_id) != vu
            raise HTTPException(status_code=409, detail=OCCUPANT_CHANGE if change else MODIFIE_ENTRE_TEMPS)
    else:
        article = db.get(Article, payload.article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="Article introuvable")
        if "occupant_attendu" in payload.model_fields_set and payload.occupant_attendu != creneau.article_id:
            raise HTTPException(status_code=409, detail=OCCUPANT_CHANGE)
        # La securite prime sur le choix manuel : les regles sont celles que les
        # lectures d'options annoncent, a un seul endroit (`app/schedule/programmation.py`).
        raison = programmation.raison_refus(
            creneau, article, programmation.lire(db, [article], maintenant, {creneau.format})
        )
        if raison is not None:
            raise HTTPException(status_code=409, detail=raison)
        # Un article n'occupe qu'un creneau a la fois : les autres creneaux
        # modifiables qui le portaient sont liberes, passes ou futurs.
        for autre in db.scalars(
            select(Creneau).where(
                Creneau.article_id == article.id,
                Creneau.id != creneau.id,
                Creneau.etat.in_(MODIFIABLES),
            )
        ):
            _ecrire(db, autre, lecture(autre), article_id=None, post_id=None, erreur=None, **_reprogramme(None))
        # L'erreur parlait de l'article precedent. Survivante, elle mentirait au
        # dashboard, et `runner._geler` la relit : un marqueur de texte perime ferait
        # sauter au nouveau post son controle de texte (`deja_tente`).
        erreur = {} if article.id == creneau.article_id else {"erreur": None}
        _ecrire(
            db, creneau, lu,
            article_id=article.id,
            **erreur,
            # Le brouillon repris doit etre du gabarit du creneau : jamais un
            # carrousel accroche a un reel, ni l'inverse.
            post_id=post_a_reprendre(db, article.id, TEMPLATES[creneau.format]),
            **_reprogramme(article.id),
        )
    db.commit()
    return _vue(db, creneau)


def _echanger(db: Session, creneau_id: int, payload: CreneauUpdate, maintenant: dt.datetime) -> CreneauOut:
    """Permute ce que portent deux creneaux, en une transaction : les deux, ou rien.

    LE VERROU, par id croissant : deux echanges croises (A↔B, B↔A) prendraient sinon leurs
    lignes en sens inverse, et Postgres en tuerait un. Il fait aussi attendre l'agent, dont
    la reclamation et le gel relisent l'etat. L'ECRITURE reste conditionnelle : SQLite n'a
    pas de `FOR UPDATE`, et c'est la meme garantie que partout ailleurs (`publication.md`,
    « L'arbitrage ecrivait sur une lecture perimee »). `_ecrire` annule tout sur un refus,
    la premiere ligne comprise.

    Rien n'est compose : la paire (actu, post) se deplace, et l'agent gele a son passage.
    """
    ids = {creneau_id, payload.echanger_avec}
    lignes = {c.id: c for c in db.scalars(
        select(Creneau).where(Creneau.id.in_(ids)).order_by(Creneau.id).with_for_update()
        .execution_options(populate_existing=True)
    )}
    if len(lignes) != len(ids):
        raise HTTPException(status_code=404, detail="Creneau introuvable")
    ici, la = lignes[creneau_id], lignes[payload.echanger_avec]
    vue = programmation.lire_echange(db, [ici, la], maintenant)
    raison = programmation.raison_echange(ici, la, vue)
    if raison is not None:
        db.rollback()
        raise HTTPException(status_code=409, detail=raison)
    if (
        ("occupant_attendu" in payload.model_fields_set and payload.occupant_attendu != la.article_id)
        or ("occupant_source_attendu" in payload.model_fields_set and payload.occupant_source_attendu != ici.article_id)
    ):
        db.rollback()
        raise HTTPException(status_code=409, detail=OCCUPANT_CHANGE)
    ecritures = [_arrivee(db, ici, la), _arrivee(db, la, ici)]
    for cible, lu, valeurs in sorted(ecritures, key=lambda e: e[0].id):
        _ecrire(db, cible, lu, **valeurs)
    db.commit()
    return _vue(db, ici)


def _arrivee(db: Session, depart: Creneau, arrivee: Creneau) -> tuple[Creneau, tuple, dict]:
    """Ce que `arrivee` recoit de `depart`. Le brouillon n'est repris qu'au gabarit de
    l'arrivee, comme Programmer : c'est lui qui a leve la limite de purge."""
    post_id = depart.post_id
    if post_id is None and depart.article_id is not None:
        post_id = post_a_reprendre(db, depart.article_id, TEMPLATES[arrivee.format])
    return arrivee, lecture(arrivee), {
        "article_id": depart.article_id,
        "post_id": post_id,
        "erreur": _marqueur_du_post(depart),
        **_reprogramme(depart.article_id or depart.post_id),
    }


def _ne_pas_publier(db: Session, creneau: Creneau, vu: tuple, maintenant: dt.datetime) -> CreneauOut:
    try:
        ne_pas_publier(db, creneau, vu, maintenant)
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
