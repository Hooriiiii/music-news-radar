"""Le creneau est AUTONOME de son article.

Defaut mesure en production le 2026-09-30 : 46 % des creneaux aboutis etaient
manques (37 contre 43 publies), et 25 des 37 ne portaient AUCUNE raison alors
que tous les 25 avaient un post compose -- la composition avait donc reussi, et
c'est apres qu'ils se sont perdus en silence.

La file couvre TROIS jours (`publication_creneaux` = 9, trois par jour) alors
que `retention_days` vaut 2 depuis le 2026-09-21 : un article attribue a un
creneau du troisieme jour est PURGE avant l'heure de sa publication. Or
`creneaux.article_id` et `posts.article_id` sont tous deux
`ON DELETE SET NULL` : la purge vide les deux sans un mot.

Le post, lui, porte deja tout ce qu'il faut pour partir -- textes, visuels
copies en local, credit, palette, actifs -- denormalise exprès pour survivre a
cette purge. Le gel et la publication ne doivent donc pas dependre de
l'article.
"""

import datetime as dt

import pytest

from app.config import settings
from app.models import (
    Article, Creneau, EtatCreneau, FormatCreneau, Post, PostStatus, Source, SourceCategorie, SourceType,
)
from app.schedule.runner import tourner
from app.schedule.slots import prochains_horaires
from app.schedule.store import appliquer_arbitrage, porte_un_post_orphelin, utc

BASE = dt.datetime(2026, 9, 20, 4, 0, tzinfo=dt.timezone.utc)
H0, H1 = prochains_horaires(BASE, 2)
(R0,) = prochains_horaires(BASE, 1, "reel")


@pytest.fixture(autouse=True)
def _carrousels_seuls(monkeypatch):
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)


class Outils:
    """Les memes doubles que les autres fichiers de la file, en plus court."""

    def __init__(self):
        self.prepares, self.prepares_reel, self.publies, self.notifications = [], [], [], []

    def composer(self, article, **_):  # pragma: no cover -- aucun article a composer ici
        raise AssertionError("aucune composition attendue : l'article a ete purge")

    def preparer(self, post, article, dossier):
        self.prepares.append((post.id, article))
        return ["p_01.jpg"]

    def preparer_reel(self, post, article, dossier):
        self.prepares_reel.append((post.id, article))
        return ["r_reel.mp4", "r_cover.jpg"]

    def publier(self, dossier, noms, legende, publier_vraiment, **_crochets):
        self.publies.append((noms, publier_vraiment))
        return "ig42"

    def notifier(self, titre, message):
        self.notifications.append(message)

    def tourner(self, db, maintenant, *, avec_reels=False, horloge=None):
        return tourner(
            db, maintenant, mode="reel", composer=self.composer, preparer=self.preparer,
            publier=self.publier, notifier=self.notifier,
            horloge=horloge or (lambda: maintenant),
            preparer_reel=self.preparer_reel if avec_reels else None,
            publier_reel=self.publier if avec_reels else None,
        )


def _source(db, **kwargs):
    defauts = dict(name="Tsugi", type=SourceType.RSS, url="https://t/feed")
    source = Source(**{**defauts, **kwargs})
    db.add(source)
    db.commit()
    return source


def _article(db, source, **kwargs):
    defauts = dict(
        source_id=source.id, url="https://t/1", title="Actu", raw_hash="h1",
        relevance_score=90, hotness_score=30, published_at=BASE - dt.timedelta(hours=1),
        media=[{"type": "photo", "url": "https://img/1.jpg"}],
    )
    article = Article(**{**defauts, **kwargs})
    db.add(article)
    db.commit()
    return article


def _post(db, article, template="kinda_post", **kwargs):
    defauts = dict(
        article_id=article.id, article_title=article.title, article_url=article.url,
        caption="Legende", template=template, credit="🎥 @saralandry",
        slides=[{"kind": "cover", "image_url": "https://img/1.jpg"}],
        assets=[{"origine": "article", "type": "photo", "url": "https://img/1.jpg"}],
    )
    post = Post(**{**defauts, **kwargs})
    db.add(post)
    db.commit()
    return post


def _purger(db, article, *lignes):
    """Ce que fait la purge en PRODUCTION : le DELETE, plus les deux
    `ON DELETE SET NULL` que Postgres declenche.

    SQLite n'applique pas les cles etrangeres sans `PRAGMA foreign_keys=ON`
    (jamais pose par `tests/conftest.py`) : les mettre a NULL a la main est la
    seule facon de reproduire fidelement la purge ici. Le test-sentinelle
    `test_les_deux_cles_de_l_article_sont_bien_en_set_null` garantit que cette
    simulation reste fidele au schema.
    """
    for ligne in lignes:
        ligne.article_id = None
    db.delete(article)
    db.commit()


def test_les_deux_cles_de_l_article_sont_bien_en_set_null():
    """La simulation de purge de ce fichier repose sur ces deux `SET NULL` :
    si l'un devenait `CASCADE` ou `RESTRICT`, tous les tests ci-dessous
    decriraient un scenario impossible."""
    for table, colonne in ((Creneau, "article_id"), (Post, "article_id")):
        (contrainte,) = table.__table__.c[colonne].foreign_keys
        assert contrainte.ondelete == "SET NULL", (table.__tablename__, colonne)


# --- La propriete centrale -------------------------------------------------


def test_un_creneau_dont_l_article_a_disparu_publie_quand_meme(db_session, tmp_path, monkeypatch):
    """LA propriete : un creneau dont l'article a ete purge apres la
    composition est gele puis PUBLIE. Avant, il finissait en MANQUE sans
    raison."""
    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    source = _source(db_session)
    article = _article(db_session, source)
    post = _post(db_session, article)
    creneau = Creneau(publier_a=H0, article_id=article.id, post_id=post.id, etat=EtatCreneau.PROGRAMME)
    db_session.add(creneau)
    db_session.commit()
    _purger(db_session, article, creneau, post)

    outils = Outils()
    outils.tourner(db_session, H0 - dt.timedelta(minutes=60))
    db_session.refresh(creneau)
    assert (creneau.etat, creneau.fichiers) == (EtatCreneau.GELE, ["p_01.jpg"])
    # Le preparateur a bien recu `None` en article, et s'en est accommode.
    assert outils.prepares == [(post.id, None)]

    rapport = outils.tourner(db_session, H0 + dt.timedelta(minutes=20))
    db_session.refresh(creneau)
    db_session.refresh(post)
    assert (creneau.etat, creneau.ig_media_id) == (EtatCreneau.PUBLIE, "ig42")
    assert post.status == PostStatus.PUBLISHED
    assert (rapport.publies, rapport.manques) == (1, 0)


def test_le_passage_used_de_l_article_est_saute_sans_lever(db_session, tmp_path, monkeypatch):
    """`USED` protege l'article de la purge et l'ecarte de la file. Purge, il
    n'y a plus rien a proteger : le basculement n'a plus d'objet et ne doit ni
    lever ni empecher la publication."""
    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    source = _source(db_session)
    article = _article(db_session, source)
    post = _post(db_session, article)
    creneau = Creneau(publier_a=H0, article_id=article.id, post_id=post.id,
                      etat=EtatCreneau.GELE, fichiers=["p_01.jpg"])
    db_session.add(creneau)
    db_session.commit()
    _purger(db_session, article, creneau, post)

    rapport = Outils().tourner(db_session, H0 + dt.timedelta(minutes=20))
    db_session.refresh(creneau)
    assert creneau.etat == EtatCreneau.PUBLIE
    assert rapport.erreurs == []


def test_un_reel_dont_l_article_a_disparu_gele_au_lieu_d_etre_jete(
    db_session, tmp_path, monkeypatch, reels_allumes
):
    """Le gel relisait l'eligibilite reel et, sans article, rendait
    « article purge » : un reel PRET etait jete pour une information absente.
    Tout ce dont la publication a besoin est sur le post -- credit denormalise,
    actifs et leur video."""
    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    # Le fixture de module coupe les reels : ce test-la en a besoin.
    monkeypatch.setattr(settings, "publication_creneaux_reel", 3)
    source = _source(db_session, name="Sara Landry", type=SourceType.INSTAGRAM,
                     url="ig:saralandry", categorie=SourceCategorie.ARTISTE)
    article = _article(db_session, source, url="https://www.instagram.com/p/AAA/",
                       media=[{"type": "video", "url": "https://cdn/v.mp4",
                               "preview_url": "https://cdn/v.jpg"}])
    post = _post(db_session, article, template="kinda_reel")
    creneau = Creneau(publier_a=R0, format=FormatCreneau.REEL, article_id=article.id,
                      post_id=post.id, etat=EtatCreneau.PROGRAMME)
    db_session.add(creneau)
    db_session.commit()
    _purger(db_session, article, creneau, post)

    outils = Outils()
    outils.tourner(db_session, R0 - dt.timedelta(minutes=60), avec_reels=True)
    db_session.refresh(creneau)
    assert (creneau.etat, creneau.post_id) == (EtatCreneau.GELE, post.id)
    assert outils.prepares_reel == [(post.id, None)]


# --- L'arbitrage ne jette plus le post orphelin ----------------------------


def test_l_arbitrage_garde_le_post_d_un_creneau_dont_l_article_est_purge(db_session):
    """`if article_id != creneau.article_id` ne voyait rien a faire quand les
    deux valaient None : `post_id` survivait, mais `etat` passait a LIBRE. Le
    creneau devenait invisible de la composition (qui exige un article) comme du
    gel (qui exige PROGRAMME), et pourrissait jusqu'a MANQUE."""
    source = _source(db_session)
    article = _article(db_session, source)
    post = _post(db_session, article)
    creneau = Creneau(publier_a=H0, article_id=article.id, post_id=post.id, etat=EtatCreneau.PROGRAMME)
    db_session.add(creneau)
    db_session.commit()
    _purger(db_session, article, creneau, post)
    # Un candidat frais est disponible : le creneau orphelin ne doit PAS le
    # prendre, sinon la composition payee du post orphelin est perdue.
    frais = _article(db_session, source, url="https://t/2", raw_hash="h2", title="Autre actu",
                     relevance_score=95)

    appliquer_arbitrage(db_session, BASE)

    db_session.refresh(creneau)
    assert porte_un_post_orphelin(creneau)
    assert (creneau.etat, creneau.article_id, creneau.post_id) == (
        EtatCreneau.PROGRAMME, None, post.id
    )
    # Le candidat frais prend le creneau suivant, pas celui-la.
    suivant = next(c for c in db_session.query(Creneau).all() if utc(c.publier_a) == H1)
    assert suivant.article_id == frais.id


def test_un_creneau_deja_passe_en_libre_par_l_ancien_defaut_redevient_programme(db_session):
    """Les lignes deja en base (production) sont LIBRE avec un `post_id` : elles
    doivent se remettre en marche toutes seules, sans intervention manuelle."""
    source = _source(db_session)
    article = _article(db_session, source)
    post = _post(db_session, article)
    creneau = Creneau(publier_a=H0, article_id=article.id, post_id=post.id, etat=EtatCreneau.PROGRAMME)
    db_session.add(creneau)
    db_session.commit()
    _purger(db_session, article, creneau, post)
    creneau.etat = EtatCreneau.LIBRE  # l'etat que l'ancien arbitrage laissait
    db_session.commit()

    appliquer_arbitrage(db_session, BASE)

    db_session.refresh(creneau)
    assert (creneau.etat, creneau.post_id) == (EtatCreneau.PROGRAMME, post.id)


def test_un_creneau_publie_sans_article_n_est_jamais_reecrit(db_session):
    """La purge nullifie aussi `article_id` d'un creneau DEJA engage : un etat
    verrouille ne doit pas se faire reecrire au pretexte qu'il porte un post
    orphelin."""
    source = _source(db_session)
    article = _article(db_session, source)
    post = _post(db_session, article)
    creneau = Creneau(publier_a=H0, article_id=article.id, post_id=post.id,
                      etat=EtatCreneau.PUBLIE, ig_media_id="ig1")
    db_session.add(creneau)
    db_session.commit()
    _purger(db_session, article, creneau, post)

    appliquer_arbitrage(db_session, BASE)

    db_session.refresh(creneau)
    assert (creneau.etat, creneau.post_id, creneau.ig_media_id) == (
        EtatCreneau.PUBLIE, post.id, "ig1"
    )


# --- Un creneau manque porte TOUJOURS une raison ---------------------------


def test_un_creneau_manque_porte_toujours_une_raison(db_session, tmp_path, monkeypatch):
    """25 manques sur 37 n'en avaient aucune : c'est ce qui a rendu le defaut
    invisible pendant une semaine."""
    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    source = _source(db_session)
    article = _article(db_session, source)
    post = _post(db_session, article)
    creneau = Creneau(publier_a=BASE - dt.timedelta(hours=5), article_id=article.id,
                      post_id=post.id, etat=EtatCreneau.PROGRAMME)
    db_session.add(creneau)
    db_session.commit()
    _purger(db_session, article, creneau, post)

    rapport = Outils().tourner(db_session, BASE)

    db_session.refresh(creneau)
    assert creneau.etat == EtatCreneau.MANQUE
    assert creneau.erreur and "manque" in creneau.erreur
    # Et il est COMPTE : un manque qui porte un post compose est une vraie
    # publication manquee, meme sans article. `_compter_manque` ne regardait que
    # l'article, donc ceux-la n'apparaissaient ni au rapport ni a la notification.
    assert rapport.manques == 1


def test_un_creneau_gele_manque_dit_qu_il_etait_gele(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    source = _source(db_session)
    article = _article(db_session, source)
    post = _post(db_session, article)
    creneau = Creneau(publier_a=BASE - dt.timedelta(hours=5), article_id=article.id, post_id=post.id,
                      etat=EtatCreneau.GELE, fichiers=["p_01.jpg"])
    db_session.add(creneau)
    db_session.commit()

    Outils().tourner(db_session, BASE)

    db_session.refresh(creneau)
    assert creneau.etat == EtatCreneau.MANQUE
    assert "gele mais jamais publie" in creneau.erreur


def test_un_creneau_vraiment_vide_porte_une_raison_sans_etre_compte(db_session, tmp_path, monkeypatch):
    """Un creneau sans article NI post n'attendait rien : il garde sa raison
    (aucune panne muette) mais ne gonfle pas le compteur des manques, qui
    declenche la notification « Mac en veille ? »."""
    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    creneau = Creneau(publier_a=BASE - dt.timedelta(hours=5), etat=EtatCreneau.LIBRE)
    db_session.add(creneau)
    db_session.commit()

    rapport = Outils().tourner(db_session, BASE)

    db_session.refresh(creneau)
    assert creneau.etat == EtatCreneau.MANQUE
    assert "aucun post compose" in creneau.erreur
    assert rapport.manques == 0


def test_le_retard_relu_a_la_publication_porte_aussi_sa_raison(db_session, tmp_path, monkeypatch):
    """Second chemin vers MANQUE : le retard relu sur l'horloge FRAICHE dans
    `_publier` (les publications precedentes du passage ont pu prendre des
    minutes). Il passait le creneau en MANQUE sans rien inscrire non plus."""
    monkeypatch.setattr(settings, "publication_dir", tmp_path)
    source = _source(db_session)
    article = _article(db_session, source)
    post = _post(db_session, article)
    creneau = Creneau(publier_a=H0, article_id=article.id, post_id=post.id,
                      etat=EtatCreneau.GELE, fichiers=["p_01.jpg"])
    db_session.add(creneau)
    db_session.commit()

    outils = Outils()
    # `maintenant` laisse le creneau dans les 90 min ; l'horloge fraiche, non.
    rapport = outils.tourner(db_session, H0, horloge=lambda: H0 + dt.timedelta(hours=2))

    db_session.refresh(creneau)
    assert (creneau.etat, outils.publies) == (EtatCreneau.MANQUE, [])
    assert "gele mais jamais publie" in creneau.erreur
    assert rapport.manques == 1
