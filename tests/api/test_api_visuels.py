"""Le dashboard dit d'ou vient le visuel d'une actu, et pourquoi il manque."""

import datetime as dt
import itertools
from pathlib import Path

from app.config import settings
from app.content.media import entree
from app.models import Article, Artist, Creneau, Source, SourceCategorie, SourceType

UTC = dt.timezone.utc
DASHBOARD = Path(__file__).resolve().parents[2] / "app" / "static" / "dashboard.html"
_numeros = itertools.count()


def _source(db, nom, handle=None, categorie=SourceCategorie.ARTISTE, type_=SourceType.INSTAGRAM):
    url = f"ig:{handle}" if type_ == SourceType.INSTAGRAM else f"https://{nom}.example/feed"
    source = Source(name=nom, type=type_, url=url, categorie=categorie)
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, media=None, mentioned=None):
    n = next(_numeros)
    article = Article(source_id=source.id, url=f"https://a.example/{n}", title=titre, raw_hash=f"d{n:063d}",
                      relevance_score=70, published_at=dt.datetime.now(UTC), media=media,
                      mentioned_artists=mentioned)
    db.add(article)
    db.commit()
    return article


def test_les_articles_disent_l_origine_de_leur_visuel(client, db_session):
    fred = _source(db_session, "Fred again.. (IG)", "fredagain")
    guettapen = _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)
    billboard = _source(db_session, "Billboard (RSS)", categorie=SourceCategorie.MEDIA, type_=SourceType.RSS)
    presse = _article(db_session, billboard, "Presse", media=[entree("photo", "https://b/og.jpg")])
    propre = _article(db_session, fred, "Post", media=[entree("photo", "https://cdn.ig/f.jpg")])
    relaye = _article(db_session, guettapen, "Relais", media=[entree("photo", "https://cdn.ig/g.jpg")],
                      mentioned=["Fred again.."])
    orphelin = _article(db_session, guettapen, "Orphelin", media=[entree("photo", "https://cdn.ig/o.jpg")])

    corps = {a["id"]: a for a in client.get("/articles").json()}

    assert (corps[presse.id]["visuel"], corps[presse.id]["visuels_interdits"]) == ("presse", False)
    assert corps[propre.id]["visuel"] == "@fredagain"
    assert (corps[relaye.id]["visuel"], corps[relaye.id]["visuels_interdits"]) == ("artistes : @fredagain", True)
    assert corps[orphelin.id]["visuel"] is None
    assert corps[orphelin.id]["raison_sans_visuel"].startswith("visuels d'un compte media Instagram interdits")
    assert client.get(f"/articles/{orphelin.id}").json()["raison_sans_visuel"] is not None


def test_lier_une_fiche_artiste_a_un_compte_suivi(client, db_session):
    fred = _source(db_session, "Fred again.. (IG)", "fredagain")
    media = _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)
    maintenant = dt.datetime.now(UTC)
    artist = Artist(name="Fred Again", slug="fred again", first_seen_at=maintenant, last_seen_at=maintenant)
    db_session.add(artist)
    db_session.commit()

    corps = client.patch(f"/artists/{artist.id}", json={"source_instagram_id": fred.id}).json()
    assert corps["source_instagram_id"] == fred.id
    assert client.patch(f"/artists/{artist.id}", json={"source_instagram_id": media.id}).status_code == 422
    # Un PATCH qui ne nomme PAS le champ ne l'efface pas (`model_fields_set`).
    assert client.patch(f"/artists/{artist.id}", json={}).json()["source_instagram_id"] == fred.id
    assert client.patch(f"/artists/{artist.id}", json={"source_instagram_id": None}).json()["source_instagram_id"] is None


def test_un_article_sans_visuel_est_refuse_sur_un_creneau_carrousel(client, db_session):
    guettapen = _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)
    article = _article(db_session, guettapen, "Orphelin", media=[entree("photo", "https://cdn.ig/o.jpg")])
    creneau = Creneau(publier_a=dt.datetime.now(UTC) + dt.timedelta(hours=5))
    db_session.add(creneau)
    db_session.commit()
    reponse = client.patch(f"/schedule/{creneau.id}", json={"article_id": article.id})
    assert reponse.status_code == 409
    assert reponse.json()["detail"].startswith("Article sans visuel autorisé :")
    assert "recherche web coupee" in reponse.json()["detail"]


def test_la_recherche_web_allumee_laisse_assigner_le_meme_article(
    client, db_session, monkeypatch
):
    """L'ACTION REFUSEE dont l'utilisateur s'est plaint le 2026-10-05. Le filtre
    ignorait le dernier barreau de l'echelle : il concluait « pas de visuel » la
    ou il fallait conclure « pas de visuel EN BASE ». Avec la recherche web
    allumee -- le reglage de production --, l'assignation manuelle passe."""
    monkeypatch.setattr(settings, "visuels_web_enabled", True)
    guettapen = _source(db_session, "Guettapen (IG)", "guettapen", SourceCategorie.MEDIA)
    article = _article(db_session, guettapen, "Orphelin", media=[entree("photo", "https://cdn.ig/o.jpg")])
    creneau = Creneau(publier_a=dt.datetime.now(UTC) + dt.timedelta(hours=5))
    db_session.add(creneau)
    db_session.commit()

    reponse = client.patch(f"/schedule/{creneau.id}", json={"article_id": article.id})

    assert reponse.status_code == 200, reponse.json()
    db_session.refresh(creneau)
    assert creneau.article_id == article.id


def test_le_dashboard_montre_l_origine_des_visuels():
    """Sentinelles : pastille d'origine, provenance des visuels, comptes medias.

    La section « Visuels retenus » a ete FUSIONNEE dans la liste unique « Ce que
    Meta recevra » (2026-10-05) : l'origine, le compte, la date du post et le
    lien « voir le post » vivent desormais sur la ligne de l'element. Ce test
    visait la PROVENANCE affichee, pas le titre de la section -- il verifie donc
    la ligne, et le mot qui doit rester en PLACE : une sentinelle qui se
    contenterait du titre resterait verte sur un simple commentaire qui le cite.
    """
    html = DASHBOARD.read_text()
    code = html[html.index("function renderPostDetail"):]
    assert 'class="badge visuel"' in html
    assert 'class="badge sans-visuel"' in html
    # La provenance d'un visuel, sur la ligne de la liste unique.
    assert "infos.permalink" in code and "voir le post" in code
    assert 'ORIGINES_ELEMENT[e.origine] || e.origine || "origine inconnue"' in code
    # La regle du 2026-09-17 : jamais le visuel d'un compte media.
    assert "const interdit = !!(article && article.visuels_interdits);" in code
    assert "visuel d'un compte média : interdit" in code
