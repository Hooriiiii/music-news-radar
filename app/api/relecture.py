"""Ce qui va partir, tel qu'il partira -- la fenetre de relecture humaine.

UN ENDPOINT DEDIE et non `PostOut` elargi, pour la raison exacte qui a donne
`GET /supervision` : la question est TRANSVERSALE. « Qu'est-ce qui part, quand, et
est-ce que ca tient ? » croise `creneaux` (l'heure, l'etat, les fichiers figes),
`posts` (les textes, le credit, le fond) et le pipeline de legende
(`app/content/legende.py`), dont le resultat n'est stocke nulle part -- il est
CALCULE a l'instant de publier. Le mettre dans `PostOut` obligerait chaque
lecteur de post a payer ce calcul et laisserait la question sans endroit unique.

CA NE RETIENT RIEN. Lire cet endpoint ne marque rien, ne verrouille rien, ne
retarde rien : l'app reste automatique, et la relecture est une OPTION. Aucune
ecriture ici, sauf la reouverture explicite qui vit dans `app/api/schedule.py`.
"""

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.legende import HASHTAGS_MAX, LEGENDE_MARGE, credit_de_compte, normaliser_hashtags, texte_legende
from app.content.visuels import fond_de_cover
from app.db import get_db
from app.models import Article, Creneau, EtatCreneau, Post
from app.schedule.relecture import RAISON_RELECTURE, regel_possible
from app.schedule.runner import RAISON_RECLAMATION, RECLAMATION_EN_COURS
from app.schedule.store import utc
from app.schemas import DepartOut

router = APIRouter(tags=["relecture"])

# Les etats d'ou un post peut ENCORE partir. PUBLIE, APERCU, MANQUE : c'est fini,
# relire n'y changerait plus rien. ECHEC y est pour un seul cas, la reclamation en
# cours (publication en vol) : la masquer donnerait l'impression que le post a
# disparu pendant les minutes ou il est justement le plus interessant a regarder.
A_VENIR = (EtatCreneau.PROGRAMME, EtatCreneau.GELE, EtatCreneau.ECHEC)


def _en_vol(creneau: Creneau, maintenant: dt.datetime) -> bool:
    """Conteneur en creation chez Meta, ou attente de l'heure affichee.

    Meme lecture que `app/api/posts.py::_publication_figee` et pour la meme
    raison : une vieille reclamation (processus tue) n'est plus une publication
    en vol, c'est un echec editable.
    """
    return (
        creneau.etat == EtatCreneau.ECHEC
        and creneau.erreur == RAISON_RECLAMATION
        and utc(creneau.publier_a) > maintenant - RECLAMATION_EN_COURS
    )


def vue_depart(db: Session, creneau: Creneau, post: Post, maintenant: dt.datetime) -> DepartOut:
    """Le creneau et son post, vus comme Meta les recevra."""
    article = db.get(Article, post.article_id) if post.article_id else None
    publier_a = utc(creneau.publier_a)
    en_vol = _en_vol(creneau, maintenant)
    gele = creneau.etat == EtatCreneau.GELE

    # Ce qui empeche d'editer, dit en clair. L'ordre compte : en vol est plus
    # strict que gele (en vol, meme la legende est deja lue et partie).
    # Le francais s'ecrit avec ses accents, y compris dans un message d'API :
    # ces phrases-ci s'affichent telles quelles au dashboard (lecon du 2026-09-19
    # sur les legendes publiees en ASCII).
    if en_vol:
        raison_edition = "publication en cours chez Meta : plus rien ne se modifie"
    elif gele:
        raison_edition = "gelé : légende et hashtags modifiables, la cover demande de rouvrir"
    else:
        raison_edition = None

    if en_vol:
        raison_reouverture = raison_edition
    elif not gele:
        raison_reouverture = "le créneau n'est pas gelé : tout se modifie déjà"
    elif not regel_possible(publier_a, maintenant):
        raison_reouverture = (
            "trop près de l'heure de départ pour refaire les fichiers figés : "
            "rouvrir maintenant ferait manquer la publication"
        )
    else:
        raison_reouverture = None

    # `render_report` est la seule mesure du debordement, et elle est effacee des
    # qu'un texte, un gabarit ou une slide change (`api/posts.py`) : absente, on
    # dit INCONNU, jamais « ca tient ».
    rapport = post.render_report or {}
    slides_rendues = rapport.get("slides") if isinstance(rapport, dict) else None
    deborde = None
    if isinstance(slides_rendues, list) and slides_rendues:
        premiere = slides_rendues[0]
        if isinstance(premiere, dict) and isinstance(premiere.get("overflow"), bool):
            deborde = premiere["overflow"]

    cover = (post.slides or [{}])[0] or {}
    return DepartOut(
        creneau_id=creneau.id,
        publier_a=publier_a,
        format=creneau.format,
        etat=creneau.etat,
        post_id=post.id,
        accroche=(cover.get("headline") or cover.get("title") or "").replace("**", ""),
        article_title=article.title if article else None,
        article_url=article.url if article else None,
        legende_publiee=texte_legende(post),
        legende_longueur=len(texte_legende(post)),
        legende_maximum=LEGENDE_MARGE,
        hashtags_publies=normaliser_hashtags(post.hashtags)[:HASHTAGS_MAX],
        hashtags_maximum=HASHTAGS_MAX,
        credit=credit_de_compte(post.credit),
        fond=fond_de_cover(post),
        fond_origine=((post.variants or {}).get("fond") or {}).get("origine"),
        elements_figes=len(creneau.fichiers) if creneau.fichiers else None,
        deborde=deborde,
        editable=raison_edition is None,
        raison_non_editable=raison_edition,
        reouvrable=raison_reouverture is None,
        raison_non_reouvrable=raison_reouverture,
        minutes_restantes=int((publier_a - maintenant).total_seconds() // 60),
    )


@router.get("/relecture", response_model=list[DepartOut])
def lister_departs(db: Session = Depends(get_db)):
    """Tout ce qui porte un post compose et n'est pas encore parti, au plus tot.

    On ne filtre PAS sur la fenetre d'annonce : l'annonce est un rappel, cette
    liste est la verite. Un creneau compose a J-1 doit pouvoir etre relu douze
    heures avant son depart -- c'est la mesure qui dit que c'est la que le temps
    se trouve (mediane 737 min entre composition et depart, contre 105 min entre
    gel et depart).
    """
    maintenant = dt.datetime.now(dt.timezone.utc)
    lignes = db.execute(
        select(Creneau, Post)
        .join(Post, Creneau.post_id == Post.id)
        .where(Creneau.etat.in_(A_VENIR))
        .order_by(Creneau.publier_a)
    ).all()
    return [
        vue_depart(db, creneau, post, maintenant)
        for creneau, post in lignes
        # Un ECHEC qui n'est pas une publication en vol est un echec pour de bon :
        # rien n'en partira, il appartient au tableau de la file, pas a la relecture.
        if creneau.etat != EtatCreneau.ECHEC or _en_vol(creneau, maintenant)
    ]


def creneaux_a_relire(db: Session, maintenant: dt.datetime) -> list[tuple[Creneau, Post]]:
    """Les creneaux qui portent un post et partent dans la fenetre d'annonce.

    Utilise par l'agent (`runner._annoncer_relecture`) et par le bandeau de
    supervision : UN seul endroit qui definit « ca part bientot et c'est pret »,
    sinon les deux divergeraient et le bandeau mentirait sur ce que l'agent
    annonce. Ne lit que la base, aucun appel paye.
    """
    from app.schedule.relecture import dans_la_fenetre

    lignes = db.execute(
        select(Creneau, Post)
        .join(Post, Creneau.post_id == Post.id)
        .where(Creneau.etat.in_([EtatCreneau.PROGRAMME, EtatCreneau.GELE]))
        .order_by(Creneau.publier_a)
    ).all()
    return [
        (creneau, post)
        for creneau, post in lignes
        if dans_la_fenetre(utc(creneau.publier_a), maintenant)
    ]


__all__ = ["A_VENIR", "RAISON_RELECTURE", "creneaux_a_relire", "router", "vue_depart"]
