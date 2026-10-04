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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.content.legende import HASHTAGS_MAX, LEGENDE_MARGE, credit_de_compte, normaliser_hashtags, texte_legende
from app.content.visuels import fond_de_cover
from app.db import get_db
from app.models import Article, Creneau, EtatCreneau, Post
from app.publish.preparation import candidats_editables, liste_explicite
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

    # Ce qui empeche d'editer, dit en clair.
    #
    # UN SEUL CAS DEPUIS LE 2026-10-04, la publication EN VOL. Un creneau GELE
    # etait auparavant « a moitie editable » (legende oui, cover non, il fallait
    # cliquer « Rouvrir ») : le re-gel est desormais AUTOMATIQUE, donc un creneau
    # gele se modifie entierement et le dire autrement enverrait l'utilisateur
    # chercher un bouton qui n'existe plus. Ce qui PEUT encore refuser une edition
    # de cover -- trop pres du depart -- se lit dans `raison_non_reouvrable`, parce
    # que la legende, elle, reste modifiable jusqu'au bout.
    #
    # Le francais s'ecrit avec ses accents, y compris dans un message d'API : ces
    # phrases-ci s'affichent telles quelles au dashboard (lecon du 2026-09-19 sur
    # les legendes publiees en ASCII).
    raison_edition = "publication en cours chez Meta : plus rien ne se modifie" if en_vol else None

    if en_vol:
        raison_reouverture = raison_edition
    elif not gele:
        raison_reouverture = "le créneau n'est pas gelé : tout se modifie déjà"
    elif not regel_possible(publier_a, maintenant):
        raison_reouverture = (
            "trop près de l'heure de départ pour refaire les fichiers figés : "
            "modifier la cover ou les éléments maintenant ferait manquer la publication"
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
        # Le manifeste du gel, tel qu'il a ete ECRIT -- jamais recalcule. C'est
        # toute la difference : un recalcul ne retombe pas sur ce qui est parti
        # (27 des 48 carrousels geles mesurables, le 2026-10-04), et il ne le peut
        # plus du tout une fois l'article purge.
        elements=creneau.elements,
        candidats=candidats_editables(post, article),
        liste_arretee=liste_explicite(post),
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


# Ce qu'un fichier fige peut etre, et rien d'autre : le gel n'ecrit que des JPEG
# et des MP4 (`preparation.py`, `preparation_reel.py`).
TYPES_FIGES = {".jpg": "image/jpeg", ".mp4": "video/mp4"}


@router.get("/relecture/{creneau_id}/fichiers/{nom}")
def servir_fichier_fige(creneau_id: int, nom: str, db: Session = Depends(get_db)):
    """Le fichier fige lui-meme, tel que Meta le recevra.

    POURQUOI CET ENDPOINT. « Je veux voir ce qui va etre poste EXACTEMENT. » Tout
    le reste de la relecture decrit ce qui part ; ceci le MONTRE -- la cover rendue
    par Chromium, les photos recadrees en 1080x1350, les videos transcodees. Le
    logo Wikipedia du post 164 etait coupe au point que les lettres manquaient :
    aucune description ne l'aurait dit, l'image si.

    AUCUNE TRAVERSEE DE CHEMIN POSSIBLE : `nom` n'est pas construit, il est
    CHERCHE dans `creneaux.fichiers` -- la liste que le gel a ecrite lui-meme. Un
    nom absent de cette liste rend 404 sans jamais toucher au disque, donc ni
    `..`, ni chemin absolu, ni lien symbolique n'ouvrent quoi que ce soit.

    Lire ne retient RIEN : aucune ecriture, aucun verrou, aucun retard. Le dossier
    est de toute facon efface des que le creneau est termine (`runner._menage`).
    """
    creneau = db.get(Creneau, creneau_id)
    if creneau is None:
        raise HTTPException(status_code=404, detail="Creneau introuvable")
    if nom not in (creneau.fichiers or []):
        raise HTTPException(status_code=404, detail="Ce fichier n'est pas fige sur ce creneau")
    chemin = settings.publication_dir / str(creneau.id) / nom
    if not chemin.is_file():
        # Le gel a eu lieu mais le dossier a ete efface (menage, reouverture) : le
        # dire, plutot qu'une image cassee sans explication.
        raise HTTPException(status_code=404, detail="Fichier fige absent du disque")
    return FileResponse(chemin, media_type=TYPES_FIGES.get(chemin.suffix, "application/octet-stream"))


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
