"""La mesure de derive editoriale lit l'accroche ET la legende.

POURQUOI CE FICHIER. `scripts/diagnostic/revue_posts.py` alerte quand moins de 20 % des
posts publies portent un marqueur France -- le compte se veut francais NATIONAL
(CLAUDE.md). Mesure du 2026-10-04 sur les 55 posts reellement publies :
l'accroche seule porte un marqueur 8 fois (14,5 %), l'accroche PLUS la legende
13 fois (23,6 %). L'alerte se declenchait donc A TORT, parce que le LIEU est
presque toujours dans la legende et presque jamais dans l'accroche :

    post 164 : accroche « Tomorrowland Winter », legende « les sommets
               d'Alpe d'Huez »
    post 17  : accroche « le Delta Festival », legende « la plage du Prado
               a Marseille »

Les fixtures sont RELATIVES A MAINTENANT : des dates figees ont pourri deux
fois ce mois-ci.
"""

import datetime as dt

from app.models import Article, Post, PostStatus, Source, SourceCategorie, SourceType
from scripts.diagnostic.revue_posts import examiner

UTC = dt.timezone.utc


def _maintenant() -> dt.datetime:
    return dt.datetime.now(UTC)


def _source(db, nom: str, categorie) -> Source:
    source = Source(name=nom, type=SourceType.INSTAGRAM, url=f"ig:{nom}", categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _post(db, *, accroche: str, legende: str, source: Source | None = None) -> Post:
    article = None
    if source is not None:
        article = Article(
            source_id=source.id,
            title=accroche,
            url=f"https://exemple.test/{accroche[:20]}",
            raw_hash=accroche[:40],
            published_at=_maintenant() - dt.timedelta(hours=3),
            fetched_at=_maintenant(),
        )
        db.add(article)
        db.commit()
    post = Post(
        article_id=article.id if article else None,
        article_title=accroche,
        article_url="https://exemple.test/x",
        source_name=source.name if source else None,
        template="kinda_post",
        status=PostStatus.PUBLISHED,
        caption=legende,
        slides=[{"kind": "cover", "image_url": "https://exemple.test/f.jpg"}],
        variants={"variantes": [{"headline": accroche}], "retenue": 0},
    )
    db.add(post)
    db.commit()
    return post


def test_le_lieu_est_dans_la_legende_et_compte_quand_meme(db_session):
    """Les textes REELS du post 164, le cas qui a declenche la correction."""
    _post(
        db_session,
        accroche="Les forfaits 7 jours pour Tomorrowland Winter sont en vente",
        legende=(
            "Les forfaits 7 jours pour Tomorrowland Winter 2026 sont enfin en vente. "
            "Direction les sommets d'Alpe d'Huez, où le géant belge installe à nouveau "
            "son édition hivernale."
        ),
    )
    (ligne,) = examiner(db_session, 10)
    assert ligne["france"] == 100.0
    # Et on VOIT dans quel champ : le lieu n'est pas sur la cover.
    assert ligne["marqueurs"] == ["tomorrowland winter"]
    assert ligne["marqueurs_legende"] == ["alpe d'huez"]


def test_une_legende_sans_marqueur_ne_fabrique_rien(db_session):
    """Un post etranger de bout en bout reste a zero -- l'elargissement du champ
    ne doit pas devenir un lot de consolation."""
    _post(
        db_session,
        accroche="Sept ans plus tard, Noisia se reforme pour un show a Amsterdam",
        legende=(
            "Le trio neerlandais Noisia annonce sa reformation apres sept ans. "
            "Leur premier concert se tiendra le 4 decembre a Amsterdam."
        ),
    )
    (ligne,) = examiner(db_session, 10)
    assert ligne["france"] == 0.0
    assert (ligne["marqueurs"], ligne["marqueurs_legende"]) == ([], [])


def test_un_compte_primaire_francais_compte_sans_marqueur_de_texte(db_session):
    """Le troisieme levier : une source PRIMAIRE francaise est un signal que le
    texte seul ne porte pas. Il ne rapporte rien sur les posts deja publies
    (mesure du 2026-10-04 : 0 post gagne), mais il est gratuit et juste."""
    source = _source(db_session, "Astropolis (IG)", SourceCategorie.FESTIVAL)
    _post(db_session, accroche="La billetterie ouvre demain", legende="Rendez-vous demain midi.",
          source=source)
    (ligne,) = examiner(db_session, 10)
    assert ligne["france"] == 70.0
    assert ligne["source_primaire"] == "Astropolis (IG)"


def test_le_nom_d_un_media_francais_ne_classe_rien(db_session):
    """LE piege du depot : Tsugi et Guettapen sont des medias FRANCAIS qui
    couvrent l'international. Leur nom ne dit rien du pays du sujet -- et
    `_sources_primaires` ne doit donc jamais les faire remonter."""
    source = _source(db_session, "Guettapen (IG)", SourceCategorie.MEDIA)
    _post(db_session, accroche="Un nouveau titre arrive demain",
          legende="Sortie prevue demain sur les plateformes.", source=source)
    (ligne,) = examiner(db_session, 10)
    assert ligne["france"] == 0.0
    assert ligne["source_primaire"] is None


def test_un_article_purge_ne_vaut_pas_une_source_etrangere(db_session):
    """L'article est purge a 2 jours alors que les posts ne le sont jamais : la
    source est alors INCONNUE, ce qui n'est pas « etrangere »."""
    _post(db_session, accroche="Une annonce", legende="Rien de plus.")
    (ligne,) = examiner(db_session, 10)
    assert ligne["source_primaire"] is None
