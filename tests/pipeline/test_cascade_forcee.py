"""Sous le seuil du pre-filtre, deux cas partent quand meme chez Claude : une actu
couverte par au moins trois sources, et un article qui cite un artiste suivi.
Cas reel du 2026-09-17 : Billboard 15, Pitchfork 25 sur la BO de GTA VI."""

import itertools

from app.models import Article, ScoredBy, Source, SourceCategorie, SourceType
from app.pipeline.scoring import ArticleScore, Category, score_pending

_numeros = itertools.count()


def note(relevance, artists=()):
    return ArticleScore(relevance=relevance, hotness=relevance, emergence=10,
                        category=Category.ANNOUNCEMENT, summary="Resume.", artists=list(artists))


def _source(db, nom, type_=SourceType.RSS, categorie=SourceCategorie.MEDIA, handle=None):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{nom}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, summary=None, rep=None, relevance=None):
    n = next(_numeros)
    article = Article(source_id=source.id, url=f"https://a.example/{n}", title=titre, summary=summary,
                      raw_hash=f"c{n:063d}", duplicate_of_id=rep.id if rep else None, relevance_score=relevance)
    db.add(article)
    db.commit()
    return article


class Claude:
    def __init__(self):
        self.appels = []

    def __call__(self, article):
        self.appels.append(article.id)
        return note(70)


def test_un_groupe_de_trois_sources_passe_chez_claude(db_session):
    rep = _article(db_session, _source(db_session, "billboard"), "GTA VI The Album will feature 34 tracks")
    _article(db_session, _source(db_session, "pitchfork"), "GTA VI tracks", rep=rep, relevance=40)
    _article(db_session, _source(db_session, "stereogum"), "GTA VI songs", rep=rep, relevance=40)
    claude = Claude()

    stats = score_pending(db_session, scorer=claude, prefilter=lambda a: note(15))

    assert claude.appels == [rep.id]
    assert (stats.scored, stats.forces_cluster, stats.prefiltered) == (1, 1, 0)
    assert rep.scored_by == ScoredBy.CLAUDE


def test_trois_articles_de_deux_sources_ne_suffisent_pas(db_session):
    billboard = _source(db_session, "billboard")
    rep = _article(db_session, billboard, "Actu")
    _article(db_session, billboard, "Actu bis", rep=rep, relevance=40)
    _article(db_session, _source(db_session, "pitchfork"), "Actu ter", rep=rep, relevance=40)
    claude = Claude()
    stats = score_pending(db_session, scorer=claude, prefilter=lambda a: note(15))
    assert claude.appels == []
    assert stats.prefiltered == 1


def test_un_titre_qui_cite_un_artiste_suivi_passe_chez_claude(db_session):
    _source(db_session, "Fred again.. (IG)", SourceType.INSTAGRAM, SourceCategorie.ARTISTE,
            "fredagainagainagainagainagain")
    article = _article(db_session, _source(db_session, "djmag"),
                       "GTA VI full soundtrack announced featuring Fred again.., PinkPantheress, more")
    claude = Claude()
    stats = score_pending(db_session, scorer=claude, prefilter=lambda a: note(25))
    assert claude.appels == [article.id]
    assert stats.forces_artiste == 1


def test_un_nom_d_un_mot_rendu_par_le_prefiltre_suffit(db_session):
    _source(db_session, "Anyma (IG)", SourceType.INSTAGRAM, SourceCategorie.ARTISTE, "anyma")
    article = _article(db_session, _source(db_session, "presse"), "Anyma au Sphere pour dix dates")
    claude = Claude()
    score_pending(db_session, scorer=claude, prefilter=lambda a: note(25, artists=["Anyma"]))
    assert claude.appels == [article.id]


def test_sans_artiste_suivi_ni_groupe_le_prefiltre_ecarte(db_session):
    _source(db_session, "Anyma (IG)", SourceType.INSTAGRAM, SourceCategorie.ARTISTE, "anyma")
    _article(db_session, _source(db_session, "presse"), "Anyma au Sphere pour dix dates")
    claude = Claude()
    stats = score_pending(db_session, scorer=claude, prefilter=lambda a: note(25))
    assert claude.appels == []
    assert (stats.prefiltered, stats.forces_artiste) == (1, 0)


def test_le_propre_post_de_l_artiste_ne_force_pas(db_session):
    fred = _source(db_session, "Fred again.. (IG)", SourceType.INSTAGRAM, SourceCategorie.ARTISTE, "fredagain")
    _article(db_session, fred, "Fred again.. merci Paris",
             summary="Fred again.. merci Paris pour ces deux soirees incroyables a Bercy\n\n[Instagram : 1 likes]")
    claude = Claude()
    score_pending(db_session, scorer=claude, prefilter=lambda a: note(20, artists=["Fred again.."]))
    assert claude.appels == []


def test_un_post_quasi_vide_qui_cite_un_artiste_reste_local(db_session):
    _source(db_session, "Fred again.. (IG)", SourceType.INSTAGRAM, SourceCategorie.ARTISTE, "fredagain")
    club = _source(db_session, "Club (IG)", SourceType.INSTAGRAM, SourceCategorie.CLUB, "club")
    article = _article(db_session, club, "@fredagain 🔥", summary="@fredagain 🔥\n\n[Instagram : 3 likes]")
    claude = Claude()
    stats = score_pending(db_session, scorer=claude, prefilter=lambda a: note(20))
    assert claude.appels == []
    assert stats.quasi_vides == 1
    assert article.scored_by == ScoredBy.LOCAL


def test_au_dessus_du_seuil_rien_n_est_compte_comme_force(db_session):
    rep = _article(db_session, _source(db_session, "a"), "Actu")
    _article(db_session, _source(db_session, "b"), "Actu", rep=rep, relevance=40)
    _article(db_session, _source(db_session, "c"), "Actu", rep=rep, relevance=40)
    stats = score_pending(db_session, scorer=Claude(), prefilter=lambda a: note(60))
    assert (stats.scored, stats.forces_cluster) == (1, 0)
