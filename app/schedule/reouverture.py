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

« NE PAS PUBLIER » (2026-10-30) vit ici pour la meme raison : c'est la meme ecriture
contre la meme reclamation, seule la destination change (LIBRE au lieu de PROGRAMME).
Voir `ne_pas_publier`.
"""

import datetime as dt
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, Creneau, EtatCreneau
from app.pipeline.retrait_humain import RAISON_NE_PAS_PUBLIER, reporter_sur_le_representant
from app.schedule.publication_incertaine import publication_incertaine
from app.schedule.relecture import RAISON_RELECTURE, regel_possible
from app.schedule.runner import (
    RAISON_RECLAMATION,
    RECLAMATION_EN_COURS,
    _attendu,
    _effacer_dossier,
    _reclamer,
)
from app.schedule.store import utc


PUBLICATION_EN_COURS = "Publication en cours : le conteneur est cree chez Meta, plus rien ne se modifie"
PUBLICATION_A_VERIFIER = (
    "Publication réclamée sans confirmation, peut-être partie : il n'y a plus rien à retirer. "
    "Vérifie le compte, puis confirme depuis la carte de l'article qu'elle n'est pas partie."
)


OCCUPANT_CHANGE = "L'occupant de ce créneau a changé depuis l'affichage : rien n'a été changé, recharge le choix."


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


def _invalider_le_gel(
    db: Session, creneau: Creneau, en_meme_temps: Callable[[], None] | None = None,
    attendu: tuple | None = None, **valeurs,
) -> bool:
    """L'ecriture conditionnelle sur (etat, article, post), en un seul endroit.

    Faux si l'agent est passe : zero ligne touchee, rien n'est ecrit, ni ici ni par
    `en_meme_temps`, qui part dans la MEME transaction. Les fichiers figes ne
    decrivent plus ce qui partira : les oublier EST l'invalidation. Le manifeste part
    avec eux -- le garder annoncerait une liste qui n'existe plus sur le disque, pire
    qu'un « inconnu ».
    """
    etait_gele = creneau.etat == EtatCreneau.GELE
    if not _reclamer(db, creneau, attendu or _attendu(creneau), fichiers=None, elements=None, **valeurs):
        return False
    if en_meme_temps is not None:
        en_meme_temps()
    db.commit()
    db.refresh(creneau)
    # Le dossier fige est efface : des fichiers orphelins seraient republies tels
    # quels si quelqu'un remettait l'etat a GELE a la main, et `_menage` ne les
    # ramasse qu'une fois le creneau termine. Seulement apres un GELE : un PROGRAMME
    # n'a de dossier que pendant un gel en cours, qui perdra sa propre ecriture.
    if etait_gele:
        _effacer_dossier(creneau)
    return True


def ne_pas_publier(
    db: Session, creneau: Creneau, vu: tuple[int | None, int | None], maintenant: dt.datetime | None = None
) -> None:
    """Retire ce creneau du programme, ou leve `ReouvertureRefusee`.

    `vu` : (article, post) que l'ecran montrait. LA CONDITION DE L'ECRITURE, pas une
    comparaison avant elle. Une liste restee ouverte depuis le matin affichait A pendant
    que l'arbitrage mettait B : le retrait excluait B et laissait partir A
    (`relecture-et-controle-editorial.md`, « Ne pas publier »).

    Le creneau redevient LIBRE, desepingle, sans post ni erreur ; l'article sort de
    la file dans la MEME transaction (decision humaine, reversible par « Remettre
    dans la file »). Le brouillon reste en base.

    PAS DE PLANCHER DE 20 MINUTES, contrairement a `rouvrir` : il protege un re-gel,
    et ici rien n'est refait. A T-2 minutes, ou apres l'heure tant que le robot peut
    encore partir, c'est justement la que ce geste compte.

    AUCUN CHEMIN VERS `media_publish`. Si l'agent a reclame (GELE -> ECHEC, committe
    avant l'appel a Meta), zero ligne est touchee et on refuse. Si nous passons les
    premiers, `_publier` ne selectionne que `etat == GELE`, et son propre UPDATE
    conditionnel echoue s'il avait lu le creneau juste avant.
    """
    if publication_en_vol(creneau, maintenant or dt.datetime.now(dt.timezone.utc)):
        raise ReouvertureRefusee(PUBLICATION_EN_COURS)
    if creneau.etat == EtatCreneau.ECHEC and publication_incertaine(creneau.erreur):
        raise ReouvertureRefusee(PUBLICATION_A_VERIFIER)
    if creneau.etat not in (EtatCreneau.LIBRE, EtatCreneau.PROGRAMME, EtatCreneau.GELE):
        raise ReouvertureRefusee("Creneau termine : il n'y a plus rien a retirer")
    article_id = vu[0]

    def ecarter_l_article() -> None:
        article = db.get(Article, article_id, populate_existing=True) if article_id else None
        # Une raison deja posee (dedup d'archive) dit ce que `reintegrer` doit refuser.
        if article is not None and article.file_exclu_raison is None:
            article.file_exclu_raison = RAISON_NE_PAS_PUBLIER
            # Programme puis rattache a un autre : le geste vise l'actu, pas la ligne.
            representant = db.get(Article, article.duplicate_of_id) if article.duplicate_of_id else None
            if representant is not None:
                reporter_sur_le_representant(representant, RAISON_NE_PAS_PUBLIER)

    ecrit = _invalider_le_gel(
        db, creneau, ecarter_l_article, attendu=(creneau.etat, *vu),
        etat=EtatCreneau.LIBRE, article_id=None, post_id=None, epingle=False, erreur=None,
    )
    if not ecrit:
        # `_reclamer` a relu le creneau : on dit ce qui s'est reellement passe.
        if creneau.etat == EtatCreneau.ECHEC and creneau.erreur == RAISON_RECLAMATION:
            raise ReouvertureRefusee(PUBLICATION_EN_COURS)
        if (creneau.article_id, creneau.post_id) != tuple(vu):
            raise ReouvertureRefusee(OCCUPANT_CHANGE)
        raise ReouvertureRefusee("Creneau modifie entre-temps par l'agent : rien n'a ete retire")


def rouvrir(db: Session, creneau: Creneau, maintenant: dt.datetime) -> None:
    """Invalide le gel de ce creneau, ou leve `ReouvertureRefusee`.

    COMMITTE : la reouverture doit etre durable avant que l'appelant n'ecrive
    quoi que ce soit sur le post -- sinon une edition persistee cohabiterait avec
    des fichiers figes qui ne la portent pas, c'est-a-dire l'ecart exact que tout
    ce chantier supprime (on verrait une chose, Meta en recevrait une autre).
    """
    if publication_en_vol(creneau, maintenant):
        raise ReouvertureRefusee(PUBLICATION_EN_COURS)
    if creneau.etat != EtatCreneau.GELE:
        raise ReouvertureRefusee("Creneau non gele : il se modifie deja sans rien rouvrir")
    if creneau.post_id is None:
        raise ReouvertureRefusee("Creneau sans post : rien a rouvrir")
    if not regel_possible(utc(creneau.publier_a), maintenant):
        raise ReouvertureRefusee(
            f"Trop pres du depart : il faut au moins "
            f"{settings.publication_relecture_regel_minutes} min pour refaire les fichiers figes"
        )
    # Le gel suivant reecrit fichiers et manifeste d'un seul tenant.
    if not _invalider_le_gel(
        db, creneau, etat=EtatCreneau.PROGRAMME, epingle=True, erreur=RAISON_RELECTURE
    ):
        raise ReouvertureRefusee(
            "Creneau modifie entre-temps par l'agent : rien n'a ete rouvert"
        )


__all__ = [
    "OCCUPANT_CHANGE", "RAISON_NE_PAS_PUBLIER", "ReouvertureRefusee", "ne_pas_publier", "publication_en_vol", "rouvrir",
]
