"""Rattache aux evenements (Phase 2A) les groupes de doublons formes AVANT
`events.attacher` (Task 2).

Pourquoi : `events.attacher` n'est appelee que depuis `cluster.lier()`, donc
UNIQUEMENT quand un NOUVEAU lien de doublon est pose. Mesure sur la base de
production le 2026-09-23 : 66 articles portent deja un `duplicate_of_id`, et
seuls 2 sont observes -- les groupes formes avant la mise en service de ce
module (Task 2) n'ont jamais eu l'occasion de rejoindre un evenement, puisque
rien dans les arguments d'un appel a `lier()` ne les mentionne s'ils ne sont
pas eux-memes touches par CE lien. Et la retention est a 2 jours : la plupart
de ces groupes disparaitront avant qu'un nouveau membre ne les fasse repasser
par `attacher`. Sans rattrapage, la Phase 2A n'observe que ce qui arrive a
partir de maintenant.

Ce script REUTILISE la machinerie de `app/pipeline/events.py`
(`events.attacher`) plutot que de reecrire le rattachement : elle a deja subi
deux tours de correction et une revue qui a monte un Postgres reel pour la
valider (fusion de plusieurs evenements preexistants, garde sur une migration
non jouee, calcul des compteurs). Pour chaque groupe -- defini par une valeur
de `Article.duplicate_of_id`, representant compris --, on appelle `attacher`
une fois : elle relit elle-meme le groupe ENTIER, cree l'evenement s'il
n'existe pas encore, le COMPLETE (sans en creer un second) si un passage
horaire posterieur a la migration lui a deja donne un evenement partiel, et
fusionne si plusieurs evenements preexistants se rejoignent. Les articles USED
(deja publies, jamais purges) sont traites comme n'importe quel membre : ce
sont eux qui portent l'histoire la plus longue.

Mode PLAN fidele a l'execution reelle : `attacher` ecrit via `db.add`/
`db.flush()` (jamais `db.commit()`), donc le plan appelle exactement le meme
code que l'application puis ANNULE la transaction (`db.rollback()`) au lieu de
la valider -- le compte annonce est donc garanti identique a ce que `--apply`
ecrirait, sans reimplementer le calcul des compteurs a part.

Idempotent : une observation deja posee n'est jamais dupliquee
(`events._ajouter_observation` verifie son existence avant d'ecrire) -- un
second passage sur un groupe deja rattrape ne compte aucune observation
nouvelle.

Ne touche ni `duplicate_of_id`, ni `file_exclu_raison`, ni aucun champ
d'article : `attacher` ne lit que `Article`, n'ecrit que `Event` et
`EventObservation`. Rien n'est appele depuis `cluster.py` (donc jamais
`cluster.lier`) : la dedup elle-meme n'est pas rejouee.

Usage :
    uv run python -m scripts.rattraper_evenements            # affiche le plan
    uv run python -m scripts.rattraper_evenements --apply
"""

import argparse
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, attendre_base
from app.models import Article, EventObservation
from app.pipeline import events


@dataclass
class Rapport:
    tables_absentes: bool = False
    groupes: int = 0
    groupes_crees: int = 0
    groupes_completes: int = 0
    groupes_deja_a_jour: int = 0
    membres: int = 0
    observations_ajoutees: int = 0
    exemples: list[str] = field(default_factory=list)


def _groupes_de_doublons(db: Session) -> dict[int, tuple[Article, list[Article]]]:
    """Un groupe par valeur DISTINCTE de `duplicate_of_id` : le representant
    (l'article vise) et tous les membres qui pointent vers lui. USED compris --
    la purge ne les exclut jamais (`ArticleStatus.USED` echappe a
    `retention.purge_old_articles`), et ce sont justement les representants
    d'archive qui portent l'historique le plus long."""
    representant_ids = db.scalars(
        select(Article.duplicate_of_id).where(Article.duplicate_of_id.is_not(None)).distinct()
    ).all()
    groupes: dict[int, tuple[Article, list[Article]]] = {}
    for rep_id in representant_ids:
        representant = db.get(Article, rep_id)
        if representant is None:
            # `duplicate_of_id` est en `ON DELETE SET NULL` : ne devrait jamais
            # survivre a une lecture juste apres, mais un script de
            # maintenance ne doit pas tomber dessus si c'est le cas.
            continue
        membres = list(db.scalars(select(Article).where(Article.duplicate_of_id == rep_id)))
        if not membres:
            continue
        groupes[rep_id] = (representant, membres)
    return groupes


def rattraper(db: Session, *, appliquer: bool = False, limite_exemples: int = 5) -> Rapport:
    """Rattache chaque groupe de doublons existant a son evenement.

    Rend le rapport dans tous les cas ; ecrit reellement seulement si
    `appliquer` est vrai (sinon la transaction est annulee en sortie)."""
    rapport = Rapport()

    if not events._tables_disponibles(db):
        # Migration 0019 non jouee sur cette base : rien a lire ni a ecrire,
        # meme constat qu'`attacher` aurait rendu groupe par groupe -- verifie
        # une seule fois, avant de construire quoi que ce soit.
        rapport.tables_absentes = True
        db.rollback()
        return rapport

    groupes = _groupes_de_doublons(db)

    for rep_id, (representant, membres) in groupes.items():
        ids = {representant.id, *(m.id for m in membres)}
        # Etat AVANT l'appel : quels membres du groupe ont deja leur preuve --
        # c'est ce diff, et lui seul, qu'`attacher` va combler (elle ne
        # touche jamais une observation deja posee).
        deja_observes = set(
            db.scalars(
                select(EventObservation.article_id).where(
                    EventObservation.article_id.in_(ids)
                )
            )
        )
        nouveaux = ids - deja_observes
        avait_deja_un_evenement = bool(deja_observes)

        evenement = events.attacher(db, membres[0], representant)

        if evenement is None:
            # `events._tables_disponibles` a rendu faux : migration 0019 non
            # jouee sur cette base. Rien n'a ete ecrit pour AUCUN groupe --
            # inutile de continuer a essayer un par un.
            rapport.tables_absentes = True
            break

        rapport.groupes += 1
        rapport.membres += len(ids)
        rapport.observations_ajoutees += len(nouveaux)
        if not nouveaux:
            rapport.groupes_deja_a_jour += 1
        elif avait_deja_un_evenement:
            rapport.groupes_completes += 1
        else:
            rapport.groupes_crees += 1

        if len(rapport.exemples) < limite_exemples:
            if not nouveaux:
                detail = "deja a jour"
            elif avait_deja_un_evenement:
                detail = f"complete de {len(nouveaux)} observation(s)"
            else:
                detail = f"cree avec {len(nouveaux)} observation(s)"
            rapport.exemples.append(
                f"representant {representant.id} ({representant.title!r}) : "
                f"{len(ids)} membre(s), {detail}"
            )

    if appliquer:
        db.commit()
    else:
        db.rollback()
    return rapport


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        rapport = rattraper(db, appliquer=args.apply)

        if rapport.tables_absentes:
            print(
                "Tables `events`/`event_observations` absentes (migration 0019 non "
                "jouee) : rien a faire pour l'instant."
            )
            return

        if rapport.groupes == 0:
            print("Aucun groupe de doublons a rattraper : rien a faire.")
            return

        for exemple in rapport.exemples:
            print(f"  {exemple}")

        resume = (
            f"{rapport.groupes} groupe(s) de doublons ({rapport.membres} article(s), "
            f"{rapport.groupes_crees} evenement(s) nouveau(x), "
            f"{rapport.groupes_completes} evenement(s) partiel(s) complete(s), "
            f"{rapport.groupes_deja_a_jour} deja a jour), "
            f"{rapport.observations_ajoutees} observation(s) ajoutee(s)"
        )
        if args.apply:
            print(f"\n{resume} : ecrits.")
        else:
            print(f"\n{resume}. Relancer avec --apply pour appliquer.")


if __name__ == "__main__":
    main()
