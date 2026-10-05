"""Tout ce qu'il faut pour examiner les posts publies, en un passage.

    uv run python -m scripts.diagnostic.revue_posts [--limite N] [--brut]

Lecture seule. Aucune ecriture, aucun appel reseau, aucun appel paye.

POURQUOI. L'utilisateur fait des retours reguliers sur le compte, et veut qu'on
examine DEUX axes : la PERTINENCE de ce qui a ete choisi, et la MISE EN FORME
(visuel, textes). Les defauts les plus couteux de ce projet ont tous ete
trouves en regardant les posts REELS -- fonds flous, affiche de line-up en
fond, Travis Scott publie sans sa tete, legendes qui citaient le media,
cloture en question six fois sur six. Aucun n'etait visible dans les tests.

Ce script sort les FAITS ; le jugement reste humain. Il ne remplace pas de
regarder les covers a l'oeil : la base ne dit ni la nettete, ni le cadrage, ni
le texte incruste dans l'image.

LE PIEGE QU'IL DESAMORCE. La date qui compte est celle de la COMPOSITION, pas
de la publication : la file couvre trois jours, donc un post publie aujourd'hui
peut avoir ete compose l'avant-veille et ne refleter aucun correctif recent.
Le 2026-10-01, cette confusion a fait croire qu'un seul post etait posterieur a
une mise a jour alors qu'ils l'etaient tous les huit.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter

from sqlalchemy import select

from app.db import SessionLocal, attendre_base
from app.models import Article, Creneau, Post, PostStatus, Source, SourceCategorie
from app.pipeline.event_features import marqueurs_france, pertinence_france

SEGMENT = re.compile(r"\*\*(.+?)\*\*")
# Une enumeration : « A, B et C ». On ne cherche pas a la parser finement --
# juste a reperer le cas ou plusieurs elements de meme nature sont traites
# differemment, defaut constate le 2026-10-01 sur « **Angele**, **Caroline
# Polachek** et SebastiAn ».
ENUMERATION = re.compile(r"\*\*[^*]+\*\*\s*,\s*\*\*[^*]+\*\*\s+et\s+(?!\*\*)")


def _variante(post: Post) -> dict:
    variants = post.variants or {}
    variantes = variants.get("variantes") or [{}]
    retenue = variants.get("retenue") or 0
    return variantes[retenue] if retenue < len(variantes) else variantes[0]


def _accroche(post: Post) -> str:
    v = _variante(post)
    return (v.get("headline") or v.get("title") or "").strip()


def _sources_primaires(db, article: Article | None) -> list[str]:
    """Le nom de la source de l'article, SEULEMENT si ce n'est pas un media.

    Meme doctrine que `event_features.source_primaire_francaise` : pour un
    compte d'artiste, de festival, de label ou de club, le compte EST le sujet
    -- un post d'Astropolis parle d'Astropolis. Le nom d'un MEDIA, lui, ne dit
    rien du pays de son sujet (Tsugi est francais et couvre l'international),
    donc il n'entre jamais. Liste vide quand l'article est purge (2 jours) :
    la source est alors inconnue, pas « etrangere ».
    """
    if article is None or article.source_id is None:
        return []
    source = db.get(Source, article.source_id)
    if source is None or source.categorie == SourceCategorie.MEDIA:
        return []
    return [source.name]


def examiner(db, limite: int) -> list[dict]:
    posts = db.scalars(
        select(Post).where(Post.status == PostStatus.PUBLISHED).order_by(Post.id.desc()).limit(limite)
    ).all()
    lignes = []
    for post in posts:
        creneau = db.scalars(select(Creneau).where(Creneau.post_id == post.id)).first()
        article = db.get(Article, post.article_id) if post.article_id else None
        accroche = _accroche(post)
        nu = accroche.replace("**", "")
        legende = post.caption or ""
        fond = (post.variants or {}).get("fond") or {}
        # LE CHAMP, autant que la liste. Mesure du 2026-10-04 sur les 55 posts
        # publies : l'accroche seule porte un marqueur France 8 fois, l'accroche
        # PLUS la legende 12 fois. Le lieu est presque toujours dans la legende
        # et presque jamais dans l'accroche -- post 164, « Tomorrowland Winter »
        # en accroche, « les sommets d'Alpe d'Huez » en legende ; post 17, « le
        # Delta Festival » en accroche, « la plage du Prado a Marseille » en
        # legende. Mesurer sur l'accroche seule sous-estimait donc la part FR du
        # feed, c'est-a-dire declenchait l'alerte de derive a tort.
        #
        # On garde les deux ensembles separes a l'affichage : un marqueur qui
        # n'est que dans la legende ne se VOIT pas sur la cover, et c'est une
        # information editoriale en soi.
        primaires = _sources_primaires(db, article)
        marq_accroche = sorted(marqueurs_france(nu))
        marq_legende = sorted(set(marqueurs_france(legende)) - set(marq_accroche))
        lignes.append(
            {
                "id": post.id,
                "gabarit": post.template,
                "compose": post.created_at,
                "publie": creneau.publie_le if creneau else None,
                "accroche": nu,
                "legende": legende,
                "hashtags": post.hashtags or [],
                "credit": post.credit,
                "slides": len(post.slides or []),
                "fond": fond.get("origine"),
                "palette": (post.variants or {}).get("palette"),
                "france": pertinence_france(
                    titre=f"{nu}\n{legende}", noms_sources_primaires=primaires
                ),
                "marqueurs": marq_accroche,
                "marqueurs_legende": marq_legende,
                "source_primaire": primaires[0] if primaires else None,
                "segments": len(SEGMENT.findall(accroche)),
                "enumeration_bancale": bool(ENUMERATION.search(accroche)),
                "relevance": article.relevance_score if article else None,
                "article_vivant": article is not None,
            }
        )
    return lignes


def _alertes(lignes: list[dict]) -> list[str]:
    """Ce qui merite un coup d'oeil. Chaque alerte vient d'un defaut REEL deja
    constate sur le compte, jamais d'une regle inventee."""
    alertes = []
    n = len(lignes)
    if not n:
        return alertes

    fr = sum(1 for ligne in lignes if (ligne["france"] or 0) >= 70)
    if fr * 100 / n < 20:
        alertes.append(
            f"PERTINENCE : {fr}/{n} posts portent un marqueur France. Le compte se veut "
            "francais NATIONAL (voir CLAUDE.md) -- compter, car la derive ne se voit pas "
            "post par post."
        )
    web = sum(1 for ligne in lignes if ligne["fond"] == "web")
    if web * 100 / n > 30:
        alertes.append(
            f"VISUEL : {web}/{n} fonds viennent de la RECHERCHE WEB, le dernier barreau de "
            "l'echelle. Les visuels de l'actu elle-meme manquent souvent."
        )
    bancales = [ligne["id"] for ligne in lignes if ligne["enumeration_bancale"]]
    if bancales:
        alertes.append(
            f"TEXTE : surlignage incoherent dans une enumeration, posts {bancales}. "
            "Plusieurs elements de meme nature, certains surlignes et d'autres non."
        )
    sans_credit = [ligne["id"] for ligne in lignes if not ligne["credit"] and ligne["fond"] != "web"]
    if sans_credit:
        alertes.append(
            f"CREDIT : posts {sans_credit} sans credit alors que le fond ne vient pas du web "
            "(un fond web n'en porte volontairement plus depuis le 2026-09-21)."
        )
    return alertes


def afficher(lignes: list[dict], brut: bool) -> None:
    for ligne in lignes:
        print("=" * 78)
        compose = f"{ligne['compose']:%d/%m %H:%M}" if ligne["compose"] else "?"
        # Un post PUBLISHED dont on ne retrouve pas le creneau a bien ete publie :
        # dire « non publie » serait faux (creneau libere, ou publication manuelle).
        publie = f"{ligne['publie']:%d/%m %H:%M}" if ligne["publie"] else "creneau inconnu"
        print(f"POST {ligne['id']} | {ligne['gabarit']} | compose {compose} -> publie {publie}")
        france = "-" if ligne["france"] is None else f"{ligne['france']:.0f}"
        marq = f" {ligne['marqueurs']}" if ligne["marqueurs"] else ""
        # Un marqueur qui n'est que dans la legende ne se voit pas sur la cover.
        if ligne["marqueurs_legende"]:
            marq += f" (legende: {ligne['marqueurs_legende']})"
        if not ligne["marqueurs"] and not ligne["marqueurs_legende"] and ligne["source_primaire"]:
            marq += f" (compte: {ligne['source_primaire']})"
        print(
            f"  fond {str(ligne['fond']):8s} | {ligne['slides']} slide(s) | palette "
            f"{ligne['palette']} | France {france}{marq} | surlignages {ligne['segments']}"
        )
        print(f"  credit  : {ligne['credit']!r}")
        print(f"  accroche: {ligne['accroche']}")
        if brut:
            for paragraphe in ligne["legende"].split("\n"):
                print(f"      {paragraphe}")
            print(f"  hashtags: {ligne['hashtags']}")

    n = len(lignes)
    if not n:
        print("Aucun post publie.")
        return
    print("=" * 78)
    print(f"{n} posts examines.")
    print(f"  gabarits : {dict(Counter(ligne['gabarit'] for ligne in lignes))}")
    print(f"  fonds    : {dict(Counter(str(ligne['fond']) for ligne in lignes))}")
    print(f"  slides   : {dict(Counter(ligne['slides'] for ligne in lignes))}")
    fr = sum(1 for ligne in lignes if (ligne["france"] or 0) >= 70)
    print(f"  France   : {fr}/{n} portent un marqueur")

    alertes = _alertes(lignes)
    if alertes:
        print("\nA REGARDER :")
        for a in alertes:
            print(f"  - {a}")
    print(
        "\nCe script ne voit pas la NETTETE, le CADRAGE ni le TEXTE INCRUSTE dans une "
        "image : ouvrir le compte et regarder les covers."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limite", type=int, default=10, help="nombre de posts (defaut 10)")
    parser.add_argument("--brut", action="store_true", help="afficher legendes et hashtags")
    options = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        lignes = examiner(db, options.limite)
        # Une session qui a LU garde sa transaction ouverte : la clore
        # explicitement plutot que de laisser « idle in transaction » chez Neon.
        db.rollback()
    afficher(lignes, options.brut)


if __name__ == "__main__":
    main()
