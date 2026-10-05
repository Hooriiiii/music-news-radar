"""La fraicheur du TEXTE, relue au gel -- et jamais au prix du creneau.

Le post 153 (publie le 2026-10-04 a 10h21, compose le 02/10 a 12h30) annoncait
« sort ce soir a minuit » pour un morceau sorti deux jours plus tot. Le gel (T-2h)
est le dernier moment ou l'on peut encore agir : il lache le brouillon perime, et
la composition du passage suivant en refait un.

Dates RELATIVES A MAINTENANT : une fixture datee ferait mentir ce qu'on mesure ici.
"""

import datetime as dt
import itertools

import pytest

from app.config import settings
from app.content.choix_visuels import ChoixVisuels
from app.content.compose import CompositionError, CoverVariante, PostDraft
from app.content.media import entree
from app.content.visuels import actif
from app.models import (
    Article,
    Creneau,
    EtatCreneau,
    Post,
    Source,
    SourceCategorie,
    SourceType,
)
from app.schedule.runner import tourner

UTC = dt.timezone.utc
_numeros = itertools.count()

# Le texte fautif, mot pour mot, tel qu'il est parti en ligne.
COVER_153 = "**This Dream Of You**, le nouveau son de **Martin Garrix**, sort a minuit"
LEGENDE_153 = (
    "**Martin Garrix** tient enfin sa promesse. This Dream Of You sort ce soir a "
    "minuit. 🎧 ✨"
)


@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)


def _article(db, maintenant):
    n = next(_numeros)
    source = Source(
        name="Martin Garrix (IG)", type=SourceType.INSTAGRAM, url=f"ig:garrix{n}",
        categorie=SourceCategorie.ARTISTE,
    )
    db.add(source)
    db.commit()
    article = Article(
        source_id=source.id, url=f"https://www.instagram.com/p/G{n}/",
        title="Martin Garrix sort This Dream Of You", raw_hash=f"g{n:063d}",
        relevance_score=90, hotness_score=40,
        published_at=maintenant - dt.timedelta(hours=20),
        media=[entree("photo", "https://cdn.ig/garrix.jpg")],
    )
    db.add(article)
    db.commit()
    return article


def _brouillon(db, article, maintenant, *, heures=46, cover=COVER_153, legende=LEGENDE_153):
    """Un brouillon PUBLIABLE (visuels, palette, assets intacts) mais vieux de
    `heures` -- seul son texte a vieilli."""
    post = Post(
        article_id=article.id, article_title=article.title, article_url=article.url,
        caption=legende, template="kinda_post",
        slides=[{
            "kind": "cover", "headline": cover, "title": "**Martin Garrix**",
            "subtitle": "le nouveau son arrive", "image_url": "https://cdn.ig/garrix.jpg",
        }],
        assets=[actif(entree("photo", "https://cdn.ig/garrix.jpg"), origine="article")],
        variants={"fond": {"origine": "article"}, "palette": "rose"},
        created_at=maintenant - dt.timedelta(hours=heures),
    )
    db.add(post)
    db.commit()
    return post


def _creneau(db, maintenant, article=None, post=None):
    creneau = Creneau(
        publier_a=maintenant + dt.timedelta(hours=1),
        article_id=article.id if article is not None else None,
        post_id=post.id if post is not None else None,
        etat=EtatCreneau.PROGRAMME, epingle=True,
    )
    db.add(creneau)
    db.commit()
    return creneau


def _draft():
    variante = CoverVariante(
        headline="**This Dream Of You** sort le **3 octobre**",
        title="**Martin Garrix**", subtitle="le titre arrive le **3 octobre**",
    )
    return PostDraft(variantes=[variante] * 4, caption="Une legende datee 🎧 ✨", hashtags=["#electro"])


class Outils:
    def __init__(self, composer=None):
        self.composes, self.notifications, self.geles = [], [], []
        self._composer = composer

    def composer(self, article):
        self.composes.append(article.id)
        if self._composer is not None:
            return self._composer(article)
        return _draft()

    def preparer(self, post, article, dossier):
        self.geles.append(post.id)
        return ["p_01.jpg"]

    def tourner(self, db, maintenant):
        choisi = actif(entree("photo", "https://cdn.ig/garrix.jpg"), origine="article")
        return tourner(
            db, maintenant, mode="apercu", composer=self.composer, preparer=self.preparer,
            publier=lambda dossier, noms, legende, vraiment: "ig42",
            notifier=lambda titre, message: self.notifications.append(message),
            horloge=lambda: maintenant,
            choisir=lambda db_, a, f, m: ChoixVisuels([choisi], None),
        )


def test_le_gel_lache_un_brouillon_dont_le_texte_a_vieilli(db_session):
    """Le cas du post 153 : 46 h apres sa composition, « sort a minuit » et « sort
    ce soir » ne disent plus la verite. Le creneau lache le post, sans exclure."""
    maintenant = dt.datetime.now(UTC)
    article = _article(db_session, maintenant)
    post = _brouillon(db_session, article, maintenant)
    creneau = _creneau(db_session, maintenant, article, post)

    outils = Outils()
    outils.tourner(db_session, maintenant)
    db_session.refresh(creneau)
    db_session.refresh(article)

    assert creneau.post_id is None
    assert creneau.etat is EtatCreneau.PROGRAMME
    assert outils.geles == []  # rien n'a ete gele avec le texte perime
    assert "minuit" in creneau.erreur and "n'est plus vrai" in creneau.erreur
    assert article.file_exclu_raison is None  # jamais une exclusion : le texte, pas l'article


def test_sans_le_reglage_le_texte_perime_repart_tel_quel(db_session, monkeypatch):
    """PREUVE PAR MUTATION sur le cas reel : critere retire, « sort ce soir a
    minuit » est gele puis publie tel quel 46 h apres sa composition."""
    monkeypatch.setattr(settings, "publication_reperes_relus", False)
    maintenant = dt.datetime.now(UTC)
    article = _article(db_session, maintenant)
    post = _brouillon(db_session, article, maintenant)
    creneau = _creneau(db_session, maintenant, article, post)

    outils = Outils()
    outils.tourner(db_session, maintenant)
    db_session.refresh(creneau)

    assert creneau.post_id == post.id
    assert creneau.etat is EtatCreneau.GELE
    assert outils.geles == [post.id]
    assert "sort ce soir a minuit" in post.caption


def test_un_brouillon_frais_n_est_jamais_perime_donc_jamais_de_boucle(db_session):
    """La reference est `post.created_at` contre MAINTENANT, jamais contre
    `publier_a` : sinon un brouillon compose ce soir pour un creneau de demain
    serait recompose a chaque passage de l'agent jusqu'a minuit."""
    maintenant = dt.datetime.now(UTC)
    article = _article(db_session, maintenant)
    post = _brouillon(db_session, article, maintenant, heures=0)
    creneau = _creneau(db_session, maintenant, article, post)

    outils = Outils()
    outils.tourner(db_session, maintenant)
    db_session.refresh(creneau)

    assert creneau.post_id == post.id and creneau.etat is EtatCreneau.GELE
    assert outils.composes == []


def test_un_creneau_orphelin_garde_son_texte_perime(db_session):
    """Article purge (`retention_days` = 2 jours) : le creneau est AUTONOME, et
    personne ne pourra recomposer. Lacher le post le rendrait invisible de la
    composition comme du gel, jusqu'a MANQUE -- c'est le cas MEME du post 153,
    dont l'article avait ete purge. On publie son texte perime plutot que rien."""
    maintenant = dt.datetime.now(UTC)
    article = _article(db_session, maintenant)
    post = _brouillon(db_session, article, maintenant)
    post.article_id = None
    creneau = _creneau(db_session, maintenant, None, post)
    db_session.commit()

    outils = Outils()
    outils.tourner(db_session, maintenant)
    db_session.refresh(creneau)

    assert creneau.post_id == post.id and creneau.etat is EtatCreneau.GELE


def test_la_composition_recompose_un_brouillon_perime_detache(db_session):
    maintenant = dt.datetime.now(UTC)
    article = _article(db_session, maintenant)
    ancien = _brouillon(db_session, article, maintenant)
    creneau = _creneau(db_session, maintenant, article)  # post_id NULL : a composer

    outils = Outils()
    outils.tourner(db_session, maintenant)
    db_session.refresh(creneau)

    assert outils.composes == [article.id]
    assert creneau.post_id is not None and creneau.post_id != ancien.id
    neuf = db_session.get(Post, creneau.post_id)
    assert "3 octobre" in neuf.slides[0]["headline"]


def test_une_recomposition_en_echec_rend_son_texte_perime_au_creneau(db_session, caplog):
    """« Une recomposition qui echoue ne doit jamais faire perdre le creneau : on
    publie l'ancien texte plutot que rien, en le journalisant. » Un brouillon
    perime pour son TEXTE est par ailleurs intact, donc publiable tel quel."""
    maintenant = dt.datetime.now(UTC)
    article = _article(db_session, maintenant)
    ancien = _brouillon(db_session, article, maintenant)
    creneau = _creneau(db_session, maintenant, article)

    def echouer(article_):
        raise CompositionError("solde Anthropic epuise")

    outils = Outils(composer=echouer)
    with caplog.at_level("WARNING"):
        outils.tourner(db_session, maintenant)
    db_session.refresh(creneau)
    db_session.refresh(article)

    # Le vieux texte repart : le creneau le reprend, le gel l'accepte (il ne
    # relache pas deux fois le meme post), et rien n'est exclu.
    assert creneau.post_id == ancien.id
    assert creneau.etat is EtatCreneau.GELE
    assert outils.geles == [ancien.id]
    assert article.file_exclu_raison is None  # surtout pas une exclusion
    # Journalise, comme l'exige la regle : on ne publie pas un texte perime en silence.
    assert any(
        "repart avec son texte perime" in m and "solde Anthropic epuise" in m
        for m in caplog.messages
    )


def test_une_recomposition_en_echec_n_est_pas_retentee_a_chaque_passage(db_session):
    """L'agent passe toutes les 10 minutes et le gel court 2 h avant l'heure : sans
    borne, gel et composition se renvoyaient la balle jusqu'a douze fois, a trois
    appels Sonnet chacun. Une tentative par creneau, puis on publie le vieux texte."""
    maintenant = dt.datetime.now(UTC)
    article = _article(db_session, maintenant)
    _brouillon(db_session, article, maintenant)
    _creneau(db_session, maintenant, article)

    def echouer(article_):
        raise CompositionError("solde Anthropic epuise")

    outils = Outils(composer=echouer)
    outils.tourner(db_session, maintenant)
    assert outils.composes == [article.id]
    outils.tourner(db_session, maintenant + dt.timedelta(minutes=10))
    assert outils.composes == [article.id]  # pas un appel de plus
