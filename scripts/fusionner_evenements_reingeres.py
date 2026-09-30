"""Fusionne les evenements qui ne sont que des RE-INGESTIONS l'un de l'autre.

    uv run python -m scripts.fusionner_evenements_reingeres [--apply]

Sans `--apply` : le plan, aucune ecriture. Idempotent : le relancer ne trouve
plus rien.

POURQUOI. Mesure du 2026-09-30 : un article purge a `retention_days` (2 j) est
encore liste dans le flux RSS de son editeur, revient comme neuf, et creait un
SECOND evenement permanent -- ecarts consecutifs de 59 h en mediane, la
signature exacte de la retention. La dedup par hash ne peut rien y voir : elle
compare aux articles PRESENTS en base, et celui-la n'y est plus.

`events._evenement_deja_observe` empeche desormais ces naissances, mais ne
repare pas l'existant. Ce script repare.

LE CRITERE EST CELUI DE LA PREVENTION, PAS UN AUTRE. Il importe
`TITRE_JETONS_MIN`, `TITRE_FENETRE_HEURES` et `cluster_archive_jours` depuis
leur source, et reutilise `normalize_title` / `significant_tokens` /
`_fusionner`. Une troisieme mesure de l'identite finirait par contredire les
deux premieres : ce depot a deja paye ce genre de divergence.

LE CHAINAGE, et non l'etendue totale. Cinq re-ingestions etalees sur dix jours
sont bien un seul evenement si chacune tombe dans la fenetre de la
PRECEDENTE -- c'est exactement ce que fait la prevention, qui regarde en
arriere a chaque ingestion. Tester l'etendue du groupe entier refuserait de
reparer ces chaines.

CE QU'IL NE FAIT PAS. Il ne touche pas aux evenements dont les observations ont
perdu leur article : il n'a pas besoin de l'article, il travaille sur les
champs DENORMALISES de `event_observations` (titre, url, source), qui survivent
a la purge. C'est precisement ce que cette denormalisation permet.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, attendre_base
from app.models import Event, EventObservation
from app.pipeline.cluster import normalize_title, significant_tokens
from app.pipeline.events import TITRE_FENETRE_HEURES, TITRE_JETONS_MIN, _fusionner, _recalculer


class Composantes:
    """Union-find : un evenement peut etre absorbable par l'URL ET par le
    titre, et les deux chaines peuvent se croiser. Sans composantes connexes,
    on fusionnerait deux fois le meme evenement ou on en oublierait un."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def racine(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def unir(self, a: int, b: int) -> None:
        ra, rb = self.racine(a), self.racine(b)
        if ra != rb:
            # La plus petite racine gagne : `attacher` elit deja le plus petit
            # id comme cible, les deux chemins doivent s'accorder.
            self.parent[max(ra, rb)] = min(ra, rb)

    def groupes(self) -> dict[int, list[int]]:
        par_racine: dict[int, list[int]] = defaultdict(list)
        for x in self.parent:
            par_racine[self.racine(x)].append(x)
        return {r: sorted(v) for r, v in par_racine.items() if len(v) > 1}


def _chainer(observations: list[EventObservation], fenetre_heures: float, cc: Composantes) -> None:
    """Unit les evenements de `observations` tant que chaque observation tombe
    dans la fenetre de la PRECEDENTE (voir « LE CHAINAGE » en tete)."""
    datees = sorted((o for o in observations if o.observed_at), key=lambda o: o.observed_at)
    for precedente, suivante in zip(datees, datees[1:]):
        ecart = (suivante.observed_at - precedente.observed_at).total_seconds() / 3600
        if ecart <= fenetre_heures:
            cc.unir(precedente.event_id, suivante.event_id)


def composer_les_groupes(db: Session) -> dict[int, list[int]]:
    """Les groupes d'evenements a fusionner, cible en tete (plus petit id)."""
    observations = list(db.scalars(select(EventObservation)))
    par_url: dict[str, list[EventObservation]] = defaultdict(list)
    par_titre: dict[tuple[str, int], list[EventObservation]] = defaultdict(list)
    for o in observations:
        if o.url:
            par_url[o.url].append(o)
        titre = normalize_title(o.title or "")
        # Le garde-fou de CONTENU de la prevention : un titre qui ne porte rien
        # ne peut pas servir d'identite (« Post Instagram » vaut 2 jetons, et
        # six posts promo d'un compte fabriqueraient une velocite).
        if titre and o.source_id and len(significant_tokens(o.title or "")) >= TITRE_JETONS_MIN:
            par_titre[(titre, o.source_id)].append(o)

    cc = Composantes()
    for lot in par_url.values():
        _chainer(lot, settings.cluster_archive_jours * 24, cc)
    for lot in par_titre.values():
        _chainer(lot, TITRE_FENETRE_HEURES, cc)
    return cc.groupes()


def fusionner(db: Session, *, appliquer: bool = False) -> tuple[int, int]:
    """Rend (evenements absorbes, groupes traites)."""
    groupes = composer_les_groupes(db)
    absorbes = 0
    for cible_id, membres in sorted(groupes.items()):
        cible = db.get(Event, cible_id)
        if cible is None:
            continue
        autres = [e for e in (db.get(Event, i) for i in membres if i != cible_id) if e is not None]
        if not autres:
            continue
        print(f"  evenement {cible_id} absorbe {', '.join(str(e.id) for e in autres)}")
        print(f"      « {(cible.titre or '')[:72]} »")
        absorbes += len(autres)
        if appliquer:
            for source in autres:
                _fusionner(db, source, cible)
            db.flush()
            _recalculer(db, cible)
    if appliquer and absorbes:
        db.commit()
    else:
        # Une session qui a LU garde sa transaction ouverte : la clore
        # explicitement plutot que de laisser « idle in transaction » chez Neon.
        db.rollback()
    return absorbes, len(groupes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="applique reellement")
    options = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        absorbes, groupes = fusionner(db, appliquer=options.apply)

    if not absorbes:
        print("\nAucun evenement a fusionner.")
        return
    if options.apply:
        print(f"\n{absorbes} evenement(s) absorbe(s) dans {groupes} groupe(s).")
    else:
        print(
            f"\n{absorbes} evenement(s) absorbable(s) dans {groupes} groupe(s). "
            "Relancer avec --apply pour appliquer."
        )


if __name__ == "__main__":
    main()
