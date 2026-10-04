"""Invalider le gel d'un creneau, en un seul endroit.

POURQUOI UN MODULE A PART. Depuis le 2026-10-04 il y a DEUX portes vers le meme
geste : le bouton « Rouvrir » (`POST /schedule/{id}/rouvrir`) et, desormais,
toute edition de contenu fige faite depuis l'editeur de relecture
(`PATCH /posts/{id}`) -- l'utilisateur a demande que le re-gel soit AUTOMATIQUE,
« on modifie, le systeme refait ce qui a change et rien d'autre, aucun bouton a
cliquer ». Deux copies de cette ecriture divergeraient, et c'est l'ecriture la
plus delicate du depot : elle touche un creneau que l'agent peut reclamer a la
meme seconde.

CE QUE REOUVRIR VEUT DIRE, exactement : GELE -> PROGRAMME, `fichiers` et
`elements` oublies, creneau EPINGLE, `erreur` = `RAISON_RELECTURE`, dossier fige
efface. Rien de plus. Le gel normal (`runner._geler`, chaque passage de l'agent)
refait les fichiers ; `RAISON_RELECTURE` fait qu'un gel IMPOSSIBLE apres une
edition manuelle LIBERE le creneau au lieu d'exclure l'article a vie -- « une
edition ne doit jamais coûter une actu ».

AUCUNE DOUBLE PUBLICATION POSSIBLE, et ca se demontre en trois pas.
1. L'agent RECLAME un creneau par GELE -> ECHEC, committe AVANT tout appel a
   Meta. Notre ecriture est CONDITIONNELLE sur `etat == GELE` et sur le meme
   `post_id` : si l'agent a reclame avant nous, zero ligne est touchee et on
   refuse.
2. Rien n'est envoye a Meta ici. Le conteneur n'est cree qu'a l'interieur du
   `with heberger(...)` de `runner._publier`, donc apres la reclamation.
3. Apres cet appel, le creneau est PROGRAMME -- or `_publier` ne selectionne que
   `etat == GELE`. Aucun chemin ne mene a `media_publish` avant un nouveau gel.

EPINGLER est necessaire, pas decoratif : un creneau PROGRAMME non epingle est
repris par `appliquer_arbitrage` au passage suivant (10 min), et le post qu'on
vient d'ouvrir pour le corriger serait remplace par le meilleur candidat du
moment.
"""

import datetime as dt
import shutil

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Creneau, EtatCreneau
from app.schedule.relecture import RAISON_RELECTURE, regel_possible
from app.schedule.runner import RAISON_RECLAMATION, RECLAMATION_EN_COURS
from app.schedule.store import utc


class ReouvertureRefusee(RuntimeError):
    """Le gel ne peut pas etre invalide, et la raison est AFFICHABLE.

    Jamais un refus muet : chaque appelant imprime `str(exc)` tel quel (regle de
    la refonte du 2026-10-02, « un controle desactive imprime sa raison »)."""


def publication_en_vol(creneau: Creneau, maintenant: dt.datetime) -> bool:
    """Conteneur cree chez Meta, ou agent en attente de l'heure dans le tunnel.

    Meme lecture que `api/posts.py::_publication_en_vol` et
    `api/relecture.py::_en_vol`, et pour la meme raison : une vieille reclamation
    (processus tue) n'est plus une publication en vol, c'est un echec editable.
    """
    return (
        creneau.etat == EtatCreneau.ECHEC
        and creneau.erreur == RAISON_RECLAMATION
        and utc(creneau.publier_a) > maintenant - RECLAMATION_EN_COURS
    )


def rouvrir(db: Session, creneau: Creneau, maintenant: dt.datetime) -> None:
    """Invalide le gel de ce creneau, ou leve `ReouvertureRefusee`.

    COMMITTE : la reouverture doit etre durable avant que l'appelant n'ecrive
    quoi que ce soit sur le post -- sinon une edition persistee cohabiterait avec
    des fichiers figes qui ne la portent pas, c'est-a-dire l'ecart exact que tout
    ce chantier supprime (on verrait une chose, Meta en recevrait une autre).
    """
    if publication_en_vol(creneau, maintenant):
        raise ReouvertureRefusee(
            "Publication en cours : le conteneur est cree chez Meta, plus rien ne se modifie"
        )
    if creneau.etat != EtatCreneau.GELE:
        raise ReouvertureRefusee("Creneau non gele : il se modifie deja sans rien rouvrir")
    if creneau.post_id is None:
        raise ReouvertureRefusee("Creneau sans post : rien a rouvrir")
    if not regel_possible(utc(creneau.publier_a), maintenant):
        raise ReouvertureRefusee(
            f"Trop pres du depart : il faut au moins "
            f"{settings.publication_relecture_regel_minutes} min pour refaire les fichiers figes"
        )
    touchees = db.execute(
        update(Creneau)
        .where(
            Creneau.id == creneau.id,
            Creneau.etat == EtatCreneau.GELE,
            Creneau.post_id == creneau.post_id,
        )
        .values(
            etat=EtatCreneau.PROGRAMME,
            # Les fichiers figes ne decrivent plus ce qui partira : les oublier
            # EST l'invalidation. Le manifeste part avec eux -- le garder
            # annoncerait une liste qui n'existe plus sur le disque, pire qu'un
            # « inconnu ». Le gel suivant reecrit les deux d'un seul tenant.
            fichiers=None,
            elements=None,
            epingle=True,
            erreur=RAISON_RELECTURE,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if touchees != 1:
        db.rollback()
        raise ReouvertureRefusee(
            "Creneau modifie entre-temps par l'agent : rien n'a ete rouvert"
        )
    db.commit()
    db.refresh(creneau)
    # Le dossier fige est efface : des fichiers orphelins seraient republies tels
    # quels si quelqu'un remettait l'etat a GELE a la main, et `_menage` ne les
    # ramasse qu'une fois le creneau termine.
    shutil.rmtree(settings.publication_dir / str(creneau.id), ignore_errors=True)


__all__ = ["ReouvertureRefusee", "publication_en_vol", "rouvrir"]
