"""Programmer : quelle actu peut aller sur quel creneau, en un seul endroit.

POURQUOI UN MODULE A PART. `PATCH /schedule/{id}` refuse, et le selecteur du dashboard
doit griser AVANT le clic, avec la meme raison. Deux copies divergeraient, et le
navigateur n'en porte aucune. `raison_refus` est donc LA regle ; le PATCH et les deux
lectures (`options_de_l_article`, `actus_du_creneau`) l'appellent.

LA LIMITE DE PURGE, et le calcul. L'agent ne compose un creneau qu'a
`publier_a - publication_composition_heures`. La purge efface un article non publie a
`published_at + retention_days` (`retention._predicat_purgeable`), sans regarder la file.
Si la composition tombe apres la purge, `ON DELETE SET NULL` vide le creneau avant qu'on
ait compose : l'article n'existe plus, et un post n'existera jamais. L'option est donc
refusee, SAUF si un brouillon valide existe deja : le creneau le porte des le geste
(`post_a_reprendre`), et un post survit a son article. La retention n'est pas touchee.

Rien ici n'appelle Claude, Meta ni le reseau, et aucune lecture n'ecrit. Les listes se
lisent d'un coup (`lire`) : aucune requete par ligne.
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau, Post
from app.pipeline.comptes_artistes import IndexComptes, construire_index
from app.publish.mode import mode_effectif
from app.schedule.publication_incertaine import publication_incertaine
from app.schedule.runner import _brouillon_perime
from app.schedule.store import (
    MODIFIABLES,
    TEMPLATES,
    comptes_cites,
    posts_a_reprendre,
    posts_en_base,
    raison_ineligible_reel,
    raison_reel_propre,
    raison_sans_visuel,
    sources_des_actifs,
    utc,
)

REFUS_CRENEAU = "Créneau gelé ou passé : il ne se modifie plus"
DEJA_ICI = "C'est déjà son créneau."
# Le selecteur est une liste a lire, pas une archive : au-dela, on dit combien restent.
ACTUS_AFFICHEES = 100

ANNONCES_MODE = {
    "arret": "Mode arrêt : rien ne sera composé ni publié tant que le mode ne change pas.",
    "apercu": "Mode aperçu : composé et préparé chez Meta, mais rien n'est publié.",
}
# Ce qui peut empecher la main, dans l'ordre ou on le dit. PAS `store.VERROUILLES`, qui
# dit qu'un CRENEAU ne bouge plus, pas qu'un ARTICLE ne peut pas repartir. Un MANQUE n'y
# est pas : rien n'est parti (`_marquer_manques` ne lit jamais un creneau reclame, et
# le MANQUE d'apres reclamation exige qu'`encore_publier` ait refuse), et le robot le reprend.
RAISONS_DU_VERROU = {
    EtatCreneau.PUBLIE: "Article déjà publié",
    EtatCreneau.APERCU: "Article déjà passé en aperçu",
    EtatCreneau.ECHEC: "Publication peut-être en ligne : vérifie le compte, puis confirme qu'elle n'est pas partie",
    EtatCreneau.GELE: "Article déjà gelé sur un autre créneau",
}
PLACES = frozenset(RAISONS_DU_VERROU) | MODIFIABLES


@dataclass
class Lecture:
    """Ce qu'il faut pour juger une liste d'articles, lu une fois."""

    db: Session
    maintenant: dt.datetime
    index: IndexComptes
    engages: dict[int, dict[int, EtatCreneau]]
    places: dict[int, Creneau]
    brouillons: dict[tuple[int, str], Post]
    sources: tuple
    posts_reel: list | None


def verrouille(creneau: Creneau) -> bool:
    """Ce creneau empeche-t-il de programmer son article a la main ? Un ECHEC seulement
    s'il porte une trace de doute : meme regle que la ceinture du robot
    (`store._articles_publiables`), et la confirmation humaine l'efface."""
    if creneau.etat == EtatCreneau.ECHEC:
        return publication_incertaine(creneau.erreur)
    return creneau.etat in RAISONS_DU_VERROU


def _creneaux_des_articles(db: Session, ids: list[int]) -> list[Creneau]:
    if not ids:
        return []
    return list(db.scalars(select(Creneau).where(Creneau.article_id.in_(ids), Creneau.etat.in_(PLACES))))


def lire(db: Session, articles: list[Article], maintenant: dt.datetime, formats: set[FormatCreneau]) -> Lecture:
    ids = [a.id for a in articles]
    engages: dict[int, dict[int, EtatCreneau]] = {}
    places: dict[int, Creneau] = {}
    for creneau in _creneaux_des_articles(db, ids):
        if verrouille(creneau):
            engages.setdefault(creneau.article_id, {})[creneau.id] = creneau.etat
        elif creneau.etat in MODIFIABLES:
            places[creneau.article_id] = creneau
    brouillons = posts_a_reprendre(db, ids)
    index = construire_index(db)
    posts_reel = None
    if FormatCreneau.REEL in formats:
        # Une seule lecture pour l'union des comptes : `raison_ineligible_reel` applique
        # ensuite la borne compte par compte, comme sa requete par article.
        comptes = {}
        for article in articles:
            if raison_reel_propre(article) is not None:
                comptes.update({c.source_id: c for c in comptes_cites(article, index)})
        posts_reel = posts_en_base(db, list(comptes.values()), maintenant, sans_limite=True)
    return Lecture(
        db=db, maintenant=maintenant, index=index, engages=engages, places=places, brouillons=brouillons,
        sources=sources_des_actifs(db, list(brouillons.values())), posts_reel=posts_reel,
    )


def raison_creneau(creneau: Creneau, maintenant: dt.datetime) -> str | None:
    if creneau.etat not in MODIFIABLES or utc(creneau.publier_a) <= maintenant:
        return REFUS_CRENEAU
    return None


def raison_article(article: Article) -> str | None:
    if article.status == ArticleStatus.USED:
        return "Article déjà publié"
    if article.duplicate_of_id is not None:
        return "Article dupliqué"
    if article.file_exclu_raison is not None:
        # Choisi a la main, un article exclu reste refuse : la securite prime,
        # l'exclusion se leve separement si besoin.
        return "Article exclu de la file"
    return None


def _raison_engage(
    article: Article, creneau_id: int | None, engages: dict[int, dict[int, EtatCreneau]]
) -> str | None:
    etats = {etat for identifiant, etat in engages.get(article.id, {}).items() if identifiant != creneau_id}
    return next((raison for etat, raison in RAISONS_DU_VERROU.items() if etat in etats), None)


def _raison_format(article: Article, format_: FormatCreneau, lecture: Lecture) -> str | None:
    if format_ == FormatCreneau.REEL:
        # Meme regle que l'arbitrage (credit obligatoire, jamais un compte media).
        raison = raison_ineligible_reel(
            article, index=lecture.index, maintenant=lecture.maintenant, posts=lecture.posts_reel
        )
        return f"Article inutilisable en reel : {raison}" if raison else None
    raison = raison_sans_visuel(article, lecture.index)
    return f"Article sans visuel autorisé : {raison}" if raison else None


def moment_de_purge(article: Article) -> dt.datetime:
    """Quand `retention._predicat_purgeable` commencera a viser cet article.

    Meme regle, lue a l'envers : la date de post, la date d'ajout pour seul filet, et le
    sursis d'un article qu'aucune machine n'a juge. Verrouille contre la vraie purge par
    `test_le_calcul_de_purge_dit_ce_que_fait_la_purge`.
    """
    jours = settings.retention_days
    if article.relevance_score is None and article.duplicate_of_id is None:
        jours = max(jours, settings.retention_sursis_non_juges_days)
    return utc(article.published_at or article.fetched_at) + dt.timedelta(days=jours)


def composition_prevue(publier_a: dt.datetime, maintenant: dt.datetime) -> tuple[dt.datetime, bool]:
    """(heure ou l'horizon de composition s'ouvre, deja ouvert ?)."""
    heure = utc(publier_a) - dt.timedelta(hours=settings.publication_composition_heures)
    return heure, heure <= maintenant


def _brouillon_valide(article: Article, format_: FormatCreneau, lecture: Lecture) -> Post | None:
    brouillon = lecture.brouillons.get((article.id, TEMPLATES[format_]))
    if brouillon is None or _brouillon_perime(lecture.db, brouillon, article, sources=lecture.sources):
        return None
    return brouillon


def _raison_purge(article: Article, creneau: Creneau, lecture: Lecture) -> str | None:
    if article.status == ArticleStatus.USED or _brouillon_valide(article, creneau.format, lecture):
        return None
    heure, _ = composition_prevue(creneau.publier_a, lecture.maintenant)
    if max(heure, lecture.maintenant) < moment_de_purge(article):
        return None
    return (
        f"Actu purgée avant sa composition (rétention {settings.retention_days} j, composition "
        f"{settings.publication_composition_heures} h avant le créneau), et aucun brouillon valide"
    )


def raison_refus(creneau: Creneau, article: Article, lecture: Lecture) -> str | None:
    """LA regle : pourquoi cet article ne peut pas aller sur ce creneau, ou None.

    Le seuil de pertinence et la fraicheur n'y sont pas, exprès : l'humain prime
    (`notes_de_l_article`).
    """
    return (
        raison_creneau(creneau, lecture.maintenant)
        or raison_article(article)
        or _raison_engage(article, creneau.id, lecture.engages)
        or _raison_format(article, creneau.format, lecture)
        or _raison_purge(article, creneau, lecture)
    )


def notes_de_l_article(article: Article) -> list[str]:
    """Ce qui se dit sans interdire. Une note absente n'est ni « 0 » ni « sous le seuil »."""
    if article.relevance_score is None:
        return ["pas encore notée, choix manuel"]
    seuil = settings.publication_min_relevance
    return [f"sous le seuil de {seuil}, choix manuel"] if article.relevance_score < seuil else []


def annonces_du_depart(publier_a: dt.datetime, maintenant: dt.datetime) -> list[str]:
    """Permis, mais a dire. Les valeurs viennent de la configuration."""
    minutes = (utc(publier_a) - maintenant).total_seconds() / 60
    annonces = []
    if minutes < settings.publication_relecture_avance_minutes:
        annonces.append(
            f"partira sans fenêtre de relecture (moins de {settings.publication_relecture_avance_minutes} min)"
        )
    if minutes < settings.publication_relecture_regel_minutes:
        annonces.append(
            f"moins de {settings.publication_relecture_regel_minutes} min : une fois gelé, la cover et les "
            "éléments ne se corrigent plus, la légende et les hashtags si"
        )
    return annonces


def _age_heures(article: Article, maintenant: dt.datetime) -> int:
    return int((maintenant - utc(article.published_at or article.fetched_at)).total_seconds() // 3600)


def _occupants(db: Session, creneaux: list[Creneau]) -> dict[int, dict]:
    """Ce qui occupe chaque creneau, en deux lectures. Un post orphelin garde son titre :
    il est denormalise pour survivre a la purge."""
    ids_articles = {c.article_id for c in creneaux if c.article_id}
    ids_posts = {c.post_id for c in creneaux if c.post_id and not c.article_id}
    titres: dict[int, str] = {}
    if ids_articles:
        titres = {i: t for i, t in db.execute(select(Article.id, Article.title).where(Article.id.in_(ids_articles)))}
    titres_posts: dict[int, str] = {}
    if ids_posts:
        titres_posts = {i: t for i, t in db.execute(select(Post.id, Post.article_title).where(Post.id.in_(ids_posts)))}
    return {
        c.id: {
            "article_id": c.article_id,
            "titre": titres.get(c.article_id) if c.article_id else titres_posts.get(c.post_id),
            "epingle": c.epingle,
            "compose": c.post_id is not None,
            "post_id": c.post_id,
        }
        for c in creneaux
        if c.article_id or c.post_id
    }


def _composition(article: Article, creneau: Creneau, lecture: Lecture) -> dict:
    if _brouillon_valide(article, creneau.format, lecture):
        return {"brouillon": True, "composition_a": None, "composition_due": False, "composition_suspendue": False}
    heure, due = composition_prevue(creneau.publier_a, lecture.maintenant)
    return {
        "brouillon": False, "composition_a": heure, "composition_due": due,
        "composition_suspendue": mode_effectif() == "arret",
    }


def _mode() -> dict:
    mode = mode_effectif()
    return {"mode": mode, "annonce_mode": ANNONCES_MODE.get(mode)}


def options_de_l_article(db: Session, article: Article, maintenant: dt.datetime) -> dict:
    creneaux = list(db.scalars(select(Creneau).where(Creneau.publier_a > maintenant).order_by(Creneau.publier_a)))
    lecture = lire(db, [article], maintenant, {c.format for c in creneaux})
    occupants = _occupants(db, creneaux)
    options = []
    for creneau in creneaux:
        actuel = creneau.article_id == article.id
        raison = DEJA_ICI if actuel else raison_refus(creneau, article, lecture)
        options.append({
            "creneau_id": creneau.id,
            "publier_a": utc(creneau.publier_a),
            "format": creneau.format,
            "etat": creneau.etat,
            "occupant": occupants.get(creneau.id),
            "actuel": actuel,
            "possible": raison is None,
            "raison": raison,
            "annonces": annonces_du_depart(creneau.publier_a, maintenant),
            **_composition(article, creneau, lecture),
        })
    return {
        "article_id": article.id,
        "titre": article.title,
        "pertinence": article.relevance_score,
        "age_heures": _age_heures(article, maintenant),
        "notes": notes_de_l_article(article),
        "raison_article": raison_article(article) or _raison_engage(article, None, lecture.engages),
        **_mode(),
        "prochain_creneau_id": next((o["creneau_id"] for o in options if o["possible"]), None),
        "options": options,
    }


def actus_du_creneau(db: Session, creneau: Creneau, maintenant: dt.datetime) -> dict:
    # Ni publies ni doublons : jamais programmables, ils noieraient la liste. Les exclus
    # restent, grises avec leur raison : on cherche parfois une actu qu'on a retiree.
    articles = list(db.scalars(
        select(Article)
        .options(selectinload(Article.source))
        .where(Article.duplicate_of_id.is_(None), Article.status != ArticleStatus.USED)
    ))
    lecture = lire(db, articles, maintenant, {creneau.format})
    candidats = []
    for article in articles:
        actuel = creneau.article_id == article.id
        raison = DEJA_ICI if actuel else raison_refus(creneau, article, lecture)
        ailleurs = lecture.places.get(article.id)
        candidats.append({
            "article_id": article.id,
            "titre": article.title,
            "pertinence": article.relevance_score,
            "age_heures": _age_heures(article, maintenant),
            "notes": notes_de_l_article(article),
            "actuel": actuel,
            "creneau_actuel": utc(ailleurs.publier_a) if ailleurs is not None and not actuel else None,
            "possible": raison is None,
            "raison": raison,
            **_composition(article, creneau, lecture),
        })
    candidats.sort(key=lambda c: (not c["possible"], -(c["pertinence"] if c["pertinence"] is not None else -1), -c["article_id"]))
    return {
        "creneau_id": creneau.id,
        "publier_a": utc(creneau.publier_a),
        "format": creneau.format,
        "etat": creneau.etat,
        "occupant": _occupants(db, [creneau]).get(creneau.id),
        "raison_creneau": raison_creneau(creneau, maintenant),
        "annonces": annonces_du_depart(creneau.publier_a, maintenant),
        **_mode(),
        "candidats": candidats[:ACTUS_AFFICHEES],
        "non_affichees": max(0, len(candidats) - ACTUS_AFFICHEES),
    }


def vue_composition(creneau: Creneau, maintenant: dt.datetime, mode: str) -> dict:
    """Quand un creneau programme sans post sera compose. Vide pour tout autre creneau."""
    if creneau.etat != EtatCreneau.PROGRAMME or creneau.post_id is not None or creneau.article_id is None:
        return {"composition_a": None, "composition_due": False, "composition_suspendue": False}
    heure, due = composition_prevue(creneau.publier_a, maintenant)
    return {"composition_a": heure, "composition_due": due, "composition_suspendue": mode == "arret"}


def vues_des_cartes(db: Session, articles: list[Article], maintenant: dt.datetime) -> dict[int, tuple]:
    """Pour chaque article : (son creneau a venir, ce qui l'empeche de se programmer, une
    publication a verifier). Une seule lecture de `creneaux`, et le mode lu une fois."""
    creneaux = _creneaux_des_articles(db, [a.id for a in articles])
    mode = mode_effectif()
    engages: dict[int, dict[int, EtatCreneau]] = {}
    courant: dict[int, Creneau] = {}
    for creneau in creneaux:
        if verrouille(creneau):
            engages.setdefault(creneau.article_id, {})[creneau.id] = creneau.etat
        # Le gel se montre aussi : c'est ce qui va partir.
        if creneau.etat in (EtatCreneau.PROGRAMME, EtatCreneau.GELE) and utc(creneau.publier_a) > maintenant:
            courant[creneau.article_id] = creneau
    vues = {}
    for article in articles:
        creneau = courant.get(article.id)
        vue = None
        if creneau is not None:
            vue = {
                "creneau_id": creneau.id,
                "publier_a": utc(creneau.publier_a),
                "format": creneau.format,
                "etat": creneau.etat,
                "epingle": creneau.epingle,
                "post_id": creneau.post_id,
                **vue_composition(creneau, maintenant, mode),
            }
        a_verifier = EtatCreneau.ECHEC in engages.get(article.id, {}).values()
        vues[article.id] = (vue, raison_article(article) or _raison_engage(article, None, engages), a_verifier)
    return vues
