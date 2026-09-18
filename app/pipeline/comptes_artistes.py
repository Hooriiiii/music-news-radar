"""Les comptes Instagram d'artistes que Kinda suit, et qui les cite.

Deux usages, une seule definition : la cascade de scoring (un article qui cite un
artiste suivi n'est jamais ecarte par le pre-filtre) et les visuels (on va
chercher l'image chez l'artiste cite, jamais chez le media qui relaie).

Un nom d'un seul mot (« Anyma », « Justice ») n'est jamais cherche dans le texte
libre : il collisionne avec des mots courants. Il cite seulement par la liste
d'artistes d'un modele ou sous la forme `@handle`.
"""

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Artist, Source, SourceCategorie, SourceType
from app.pipeline.cluster import normalize_title
from app.sources.instagram import compte_depuis_url

CLE_MIN = 4
# « Fred again.. (IG) », « DJ Snake (FR) (IG) » : les suffixes de nommage des sources.
_PARENTHESES = re.compile(r"\s*\([^)]*\)")


def cle_artiste(nom: str | None) -> str:
    """La forme comparable d'un nom ou d'un handle : sans suffixe entre
    parentheses, sans @, puis la normalisation des titres de la dedup."""
    if not nom:
        return ""
    return normalize_title(_PARENTHESES.sub("", nom).strip().lstrip("@"))


@dataclass(frozen=True)
class CompteArtiste:
    source_id: int
    handle: str
    nom: str
    # Derniere lecture du compte par le pipeline : sous l'intervalle, la base est
    # a jour et une lecture en direct serait du quota Graph pour rien.
    dernier_appel: dt.datetime | None


def _date(valeur) -> dt.datetime | None:
    if not valeur:
        return None
    try:
        moment = dt.datetime.fromisoformat(valeur)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


@dataclass
class IndexComptes:
    comptes: dict[int, CompteArtiste]
    par_cle: dict[str, CompteArtiste]

    def cites(
        self,
        textes: Iterable[str | None] = (),
        noms: Iterable[str] = (),
        exclure_source_id: int | None = None,
    ) -> list[CompteArtiste]:
        trouves: dict[int, CompteArtiste] = {}
        for nom in noms:
            compte = self.par_cle.get(cle_artiste(nom))
            if compte is not None:
                trouves[compte.source_id] = compte
        for texte in textes:
            if not texte:
                continue
            normalise = f" {normalize_title(texte)} "
            for cle, compte in self.par_cle.items():
                if " " in cle and f" {cle} " in normalise:
                    trouves[compte.source_id] = compte
            for compte in self.comptes.values():
                if re.search(rf"@{re.escape(compte.handle)}(?![\w.])", texte, re.IGNORECASE):
                    trouves[compte.source_id] = compte
        trouves.pop(exclure_source_id, None)
        return [trouves[source_id] for source_id in sorted(trouves)]


def compte_de_source(source: Source) -> CompteArtiste:
    """Le compte Instagram que represente une source, tel que le lecteur Graph
    l'attend. Une seule definition : l'index des artistes cites la partage avec
    le fond de secours (`choix_visuels.photos_du_compte`), qui lit le compte de
    l'article lui-meme et non ceux qu'il cite -- deux lectures voisines du meme
    `sources.state` finiraient par diverger sur `dernier_appel`, et le lecteur
    redemanderait a Graph ce que le pipeline vient d'ingerer."""
    return CompteArtiste(
        source_id=source.id,
        handle=compte_depuis_url(source.url),
        nom=_PARENTHESES.sub("", source.name or "").strip(),
        dernier_appel=_date((source.state or {}).get("dernier_appel")),
    )


def construire_index(db: Session, avec_liens: bool = True) -> IndexComptes:
    sources = db.scalars(
        select(Source)
        .where(
            Source.type == SourceType.INSTAGRAM,
            Source.categorie == SourceCategorie.ARTISTE,
            Source.active.is_(True),
        )
        .order_by(Source.id)
    ).all()
    comptes: dict[int, CompteArtiste] = {}
    par_cle: dict[str, CompteArtiste] = {}
    for source in sources:
        compte = compte_de_source(source)
        handle = compte.handle
        comptes[source.id] = compte
        for cle in (cle_artiste(source.name), cle_artiste(handle)):
            if len(cle) >= CLE_MIN:
                par_cle.setdefault(cle, compte)
    if not avec_liens:
        return IndexComptes(comptes, par_cle)
    # Les liens poses sur les fiches artistes priment : c'est la correction manuelle.
    liens = db.execute(
        select(Artist.name, Artist.source_instagram_id).where(Artist.source_instagram_id.is_not(None))
    )
    for nom, source_id in liens:
        cle = cle_artiste(nom)
        if source_id in comptes and len(cle) >= CLE_MIN:
            par_cle[cle] = comptes[source_id]
    return IndexComptes(comptes, par_cle)


def lier_artistes_comptes(db: Session) -> int:
    """Lie chaque fiche artiste non liee dont le nom correspond a un compte suivi.
    Jamais d'ecrasement d'un lien existant. Idempotent. Les cles viennent des
    SOURCES seules : un lien manuel pose sur une autre fiche du meme nom ne doit
    pas se propager."""
    index = construire_index(db, avec_liens=False)
    lies = 0
    for artist in db.scalars(select(Artist).where(Artist.source_instagram_id.is_(None))):
        compte = index.par_cle.get(cle_artiste(artist.name))
        if compte is not None:
            artist.source_instagram_id = compte.source_id
            lies += 1
    db.commit()
    return lies
