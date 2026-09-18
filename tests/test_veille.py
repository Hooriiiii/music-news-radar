"""La veille rapide : douze comptes relus toutes les cinq minutes.

Mesure du 2026-09-18 (`docs/superpowers/specs/2026-09-18-etre-une-source-pas-un-
intermediaire.md`) : Kinda apprend l'actu d'un artiste par la presse 39 h avant de
la voir chez l'artiste. La cause est entierement la notre -- l'espacement de 3 h
des comptes Instagram. Ces tests tiennent les deux bouts : la cadence courte sur
une liste courte, et les garde-fous qui empechent cette cadence d'epuiser le
quota Graph dont la PUBLICATION depend aussi.

Aucun appel reseau ici : les adapters sont injectes.
"""

import datetime as dt

import pytest

from app.config import settings
from app.models import Article, Source, SourceCategorie, SourceType
from app.pipeline.ingest import IngestStats
from app.pipeline.veille import (
    EtatVeille,
    RapportVeille,
    chemin_etat,
    enregistrer_etat,
    lire_etat,
    run_veille,
    source_surveillee,
    sources_surveillees,
)
from app.sources.base import RawItem
from app.sources.instagram import (
    InstagramAdapter,
    QuotaGraphAtteint,
    compte_surveille,
    comptes_surveilles,
)

T0 = dt.datetime(2026, 9, 18, 10, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _liste_de_veille(monkeypatch):
    monkeypatch.setattr(settings, "veille_comptes", "stmpdrcrds, defectedrecords")
    # Le jeton de sourcing est neutralise par conftest pour toute la suite : la
    # veille le redemande, il faut donc le reposer ici.
    monkeypatch.setattr(settings, "meta_fb_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    monkeypatch.setattr(settings, "veille_intervalle_minutes", 5)
    monkeypatch.setattr(settings, "instagram_intervalle_heures", 1)
    monkeypatch.setattr(settings, "veille_appels_max_par_heure", 200)


# --- 1. Cadence par source -------------------------------------------------


def _adapter(compte="stmpdrcrds", source_id=1, etat=None):
    source = Source(
        id=source_id, name=compte, type=SourceType.INSTAGRAM, url=f"ig:{compte}",
        categorie=SourceCategorie.ARTISTE,
    )
    source.state = etat
    return InstagramAdapter(source)


@pytest.mark.parametrize(
    "compte, attendu",
    [
        ("stmpdrcrds", True),
        ("StmpdRcrds", True),
        ("@defectedrecords", True),
        ("tsugimag", False),
        ("", False),
    ],
)
def test_la_liste_de_veille_ignore_la_casse_et_l_arobase(compte, attendu):
    """Les comptes sont saisis a la main dans `.env` : une majuscule ou un @ ne
    doit pas faire silencieusement retomber un compte a la cadence de 3 h."""
    assert compte_surveille(compte) is attendu


def test_la_liste_de_veille_se_lit_dans_les_reglages():
    assert comptes_surveilles() == frozenset({"stmpdrcrds", "defectedrecords"})


def test_la_liste_par_defaut_tient_le_budget_de_quota():
    """DOUZE comptes, pas un de plus sans refaire l'arithmetique : chaque compte
    ajoute coute 12 appels Graph par heure, sur un quota partage avec la
    publication. La liste est volontairement faite de LABELS et de festivals
    actifs plutot que d'un seul artiste."""
    from app.config import Settings

    defaut = [c.strip() for c in Settings.model_fields["veille_comptes"].default.split(",")]

    assert len(defaut) == 12
    assert "stmpdrcrds" in defaut and "defectedrecords" in defaut
    assert len(set(defaut)) == 12


def test_un_compte_surveille_est_relu_toutes_les_cinq_minutes(monkeypatch):
    monkeypatch.setattr(settings, "meta_fb_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    adapter = _adapter(etat={"dernier_appel": T0.isoformat()})

    assert adapter.a_lire(T0 + dt.timedelta(minutes=2)) is False
    assert adapter.a_lire(T0 + dt.timedelta(minutes=5)) is True


def test_un_compte_ordinaire_garde_l_espacement_en_heures():
    """La veille est une LISTE COURTE : les 111 autres comptes restent a la
    cadence du pipeline horaire, sinon le quota Graph saute."""
    adapter = _adapter(compte="tsugimag", etat={"dernier_appel": T0.isoformat()})

    assert adapter.a_lire(T0 + dt.timedelta(minutes=30)) is False
    assert adapter.a_lire(T0 + dt.timedelta(minutes=50)) is True


def test_l_espacement_de_trois_heures_reste_intact(monkeypatch):
    """Garde-fou de non-regression : la generalisation de `a_lire` aux minutes ne
    doit pas changer le comportement mesure a 3 h (marge d'un quart d'heure)."""
    monkeypatch.setattr(settings, "instagram_intervalle_heures", 3)
    adapter = _adapter(compte="tsugimag", etat={"dernier_appel": T0.isoformat()})

    assert adapter.a_lire(T0 + dt.timedelta(hours=2)) is False
    assert adapter.a_lire(T0 + dt.timedelta(hours=2, minutes=50)) is True


def test_le_pipeline_horaire_ne_relit_pas_un_compte_que_la_veille_vient_de_lire(monkeypatch):
    """L'ESPACEMENT EST PARTAGE : `dernier_appel` est le meme etat pour les deux
    agents. Un compte lu par la veille a T n'est donc pas relu par le passage
    horaire a T+1 min -- la veille ne DOUBLE pas les appels, elle les redistribue.
    """
    monkeypatch.setattr(settings, "meta_fb_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    adapter = _adapter()
    appels = []
    monkeypatch.setattr(adapter, "_appeler", lambda: appels.append(1) or {})

    adapter.fetch(T0)  # passage de veille
    adapter.fetch(T0 + dt.timedelta(minutes=1))  # passage horaire, juste apres

    assert appels == [1]


# --- 2. Selection des sources ----------------------------------------------


def _source(db, compte, type_=SourceType.INSTAGRAM, active=True, url=None):
    source = Source(
        name=compte, type=type_, url=url or f"ig:{compte}", active=active,
        categorie=SourceCategorie.ARTISTE,
    )
    db.add(source)
    db.commit()
    return source


def test_la_veille_ne_retient_que_les_sources_instagram_surveillees(db_session):
    """Trois exclusions a tenir : le compte hors liste, la source inactive, et
    surtout une source NON Instagram qui porterait le meme nom -- la veille
    n'existe que pour `business_discovery`."""
    surveillee = _source(db_session, "stmpdrcrds")
    _source(db_session, "tsugimag")
    _source(db_session, "defectedrecords", active=False)
    _source(db_session, "defectedrecords", type_=SourceType.RSS,
            url="https://defected.example/feed")

    assert [s.id for s in sources_surveillees(db_session)] == [surveillee.id]


def test_source_surveillee_refuse_une_source_rss(db_session):
    rss = _source(db_session, "stmpdrcrds", type_=SourceType.RSS,
                  url="https://exemple.fr/feed")
    assert source_surveillee(rss) is False


# --- 3. Le passage de veille ------------------------------------------------


class FauxAdapter:
    """Un adapter qui rend des items sans reseau, et dit s'il aurait appele Meta."""

    def __init__(self, items=(), a_lire=True, erreur=None):
        self.items = list(items)
        self._a_lire = a_lire
        self.erreur = erreur
        self.appels = 0

    def a_lire(self, maintenant):
        return self._a_lire

    def fetch(self, maintenant=None):
        self.appels += 1
        if self.erreur is not None:
            raise self.erreur
        return self.items


def _item(n=1):
    return RawItem(
        url=f"https://www.instagram.com/p/{n}/",
        title=f"The Resistance is coming {n}",
        summary="un post",
        published_at=T0,
    )


def _run(db, adapters, **kwargs):
    kwargs.setdefault("cluster", lambda _db: 0)
    kwargs.setdefault("score", lambda _db, limit=None: None)
    kwargs.setdefault("record", lambda _db: 0)
    kwargs.setdefault("notify", lambda *_: True)
    return run_veille(db, T0, get_adapter=lambda source: adapters[source.id], **kwargs)


def test_la_veille_ingere_les_comptes_surveilles(db_session):
    source = _source(db_session, "stmpdrcrds")
    adapters = {source.id: FauxAdapter([_item()])}

    rapport = _run(db_session, adapters)

    assert isinstance(rapport, RapportVeille)
    assert [s.new for s in rapport.ingest] == [1]
    assert db_session.query(Article).count() == 1


def test_la_veille_ne_score_QUE_ce_qu_elle_vient_d_ingerer(db_session, monkeypatch):
    """Jamais `score_pending` sans limite : l'arriere-plan peut porter des
    centaines d'articles non scores, et la veille tourne 288 fois par jour."""
    monkeypatch.setattr(settings, "anthropic_api_key", "cle")
    monkeypatch.setattr(settings, "veille_scoring_max", 7)
    source = _source(db_session, "stmpdrcrds")
    limites = []

    _run(
        db_session,
        {source.id: FauxAdapter([_item()])},
        score=lambda _db, limit=None: limites.append(limit),
    )

    assert limites == [7]


def test_la_veille_ne_score_rien_quand_rien_n_est_arrive(db_session, monkeypatch):
    """288 passages par jour : un passage a vide ne doit rien couter, ni en appel
    Claude ni en requete de scoring."""
    monkeypatch.setattr(settings, "anthropic_api_key", "cle")
    source = _source(db_session, "stmpdrcrds")
    appels = []

    rapport = _run(
        db_session,
        {source.id: FauxAdapter([])},
        score=lambda _db, limit=None: appels.append(limit),
        cluster=lambda _db: appels.append("cluster"),
    )

    assert appels == []
    assert rapport.scoring is None


def test_la_veille_n_illustre_ni_ne_purge():
    """Le pipeline horaire garde l'illustration (requetes reseau bornees) et la
    purge. Les melanger a un passage de 5 minutes multiplierait par 12 le cout
    d'une etape concue pour tourner une fois par heure."""
    import ast
    import inspect

    import app.pipeline.veille as module

    # Les IMPORTS, pas le texte : le docstring du module explique justement
    # pourquoi l'illustration et la purge n'y sont pas.
    importes = {
        noeud.module
        for noeud in ast.walk(ast.parse(inspect.getsource(module)))
        if isinstance(noeud, ast.ImportFrom) and noeud.module
    }

    assert "app.pipeline.illustrations" not in importes
    assert "app.pipeline.retention" not in importes


def test_une_source_en_erreur_n_arrete_pas_la_veille(db_session):
    a = _source(db_session, "stmpdrcrds")
    b = _source(db_session, "defectedrecords")
    adapters = {a.id: FauxAdapter(erreur=RuntimeError("Meta HTTP 500")), b.id: FauxAdapter([_item()])}

    rapport = _run(db_session, adapters)

    erreurs = [s.error for s in rapport.ingest if s.error]
    assert erreurs and "500" in erreurs[0]
    assert db_session.query(Article).count() == 1


# --- 4. Garde-fous de quota -------------------------------------------------


def test_le_plafond_horaire_borne_le_nombre_d_appels(db_session, monkeypatch):
    """Le quota Graph est PARTAGE avec la publication : la veille ne doit jamais
    pouvoir le manger entierement, quelle que soit la longueur de la liste."""
    monkeypatch.setattr(settings, "veille_appels_max_par_heure", 1)
    monkeypatch.setattr(settings, "veille_comptes", "stmpdrcrds,defectedrecords")
    a = _source(db_session, "stmpdrcrds")
    b = _source(db_session, "defectedrecords")
    adapters = {a.id: FauxAdapter([_item(1)]), b.id: FauxAdapter([_item(2)])}

    rapport = _run(db_session, adapters)

    assert adapters[a.id].appels + adapters[b.id].appels == 1
    assert rapport.appels == 1
    assert rapport.plafond_atteint is True


def test_le_compteur_d_appels_repart_a_la_nouvelle_heure(db_session, monkeypatch):
    monkeypatch.setattr(settings, "veille_appels_max_par_heure", 1)
    source = _source(db_session, "stmpdrcrds")
    enregistrer_etat(chemin_etat(), EtatVeille(heure=T0 - dt.timedelta(hours=1), appels=99))

    rapport = _run(db_session, {source.id: FauxAdapter([_item()])})

    assert rapport.appels == 1
    assert lire_etat(chemin_etat()).appels == 1


def test_un_compte_non_du_ne_consomme_pas_de_budget(db_session):
    """Le budget compte les APPELS, pas les sources : un compte dont l'espacement
    n'est pas echu n'appelle pas Meta et ne doit rien consommer."""
    source = _source(db_session, "stmpdrcrds")
    adapter = FauxAdapter([_item()], a_lire=False)

    rapport = _run(db_session, {source.id: adapter})

    assert adapter.appels == 0
    assert rapport.appels == 0


def test_une_erreur_de_quota_met_la_veille_en_pause_et_notifie_une_fois(db_session, monkeypatch):
    monkeypatch.setattr(settings, "veille_pause_quota_minutes", 30)
    a = _source(db_session, "stmpdrcrds")
    b = _source(db_session, "defectedrecords")
    adapters = {
        a.id: FauxAdapter(erreur=QuotaGraphAtteint("Application request limit reached")),
        b.id: FauxAdapter([_item()]),
    }
    notifications = []

    rapport = _run(db_session, adapters, notify=lambda *args: notifications.append(args))

    assert rapport.en_pause is True
    # On s'arrete AU PREMIER code 4 : insister ne ferait qu'aggraver le depassement.
    assert adapters[b.id].appels == 0
    assert len(notifications) == 1
    etat = lire_etat(chemin_etat())
    assert etat.pause_jusqu_a == T0 + dt.timedelta(minutes=30)

    # Deuxieme passage pendant la pause : rien, et pas de seconde notification.
    rapport2 = run_veille(
        db_session, T0 + dt.timedelta(minutes=5),
        get_adapter=lambda s: adapters[s.id], notify=lambda *args: notifications.append(args),
        cluster=lambda _db: 0, score=lambda _db, limit=None: None, record=lambda _db: 0,
    )
    assert rapport2.en_pause is True
    assert rapport2.ingest == []
    assert len(notifications) == 1


def test_la_pause_est_levee_a_son_terme(db_session):
    source = _source(db_session, "stmpdrcrds")
    enregistrer_etat(chemin_etat(), EtatVeille(pause_jusqu_a=T0 - dt.timedelta(minutes=1)))
    adapter = FauxAdapter([_item()])

    rapport = _run(db_session, {source.id: adapter})

    assert rapport.en_pause is False
    assert adapter.appels == 1


def test_la_pause_de_la_veille_ne_touche_pas_le_pipeline_horaire(db_session, monkeypatch):
    """La pause vit dans le fichier d'etat de la veille, que `run_ingest` ne lit
    jamais : un code 4 sur la liste courte ne doit pas eteindre l'ingestion des
    123 autres sources, ni la publication."""
    monkeypatch.setattr(settings, "meta_fb_access_token", "TOK")
    monkeypatch.setattr(settings, "meta_ig_user_id", "1")
    enregistrer_etat(chemin_etat(), EtatVeille(pause_jusqu_a=T0 + dt.timedelta(minutes=30)))
    adapter = _adapter()
    appels = []
    monkeypatch.setattr(adapter, "_appeler", lambda: appels.append(1) or {})

    adapter.fetch(T0)

    assert appels == [1]


def test_un_etat_illisible_ne_fait_pas_planter_la_veille(db_session):
    chemin = chemin_etat()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("{ ceci n'est pas du json")
    source = _source(db_session, "stmpdrcrds")
    adapter = FauxAdapter([_item()])

    rapport = _run(db_session, {source.id: adapter})

    assert rapport.en_pause is False
    assert adapter.appels == 1


def test_le_rapport_dit_ce_qui_s_est_passe(db_session, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "cle")
    source = _source(db_session, "stmpdrcrds")

    rapport = _run(
        db_session, {source.id: FauxAdapter([_item()])},
        cluster=lambda _db: 3, record=lambda _db: 2,
        score=lambda _db, limit=None: "STATS",
    )

    assert rapport.doublons == 3
    assert rapport.mentions == 2
    assert rapport.scoring == "STATS"
    assert rapport.nouveaux == 1
    assert isinstance(rapport.ingest[0], IngestStats)


def test_sans_jeton_la_veille_ne_consomme_pas_son_budget(db_session, monkeypatch):
    """L'adapter rend zero item SANS appeler Meta quand le jeton manque :
    compter ces passages comme des appels ferait sauter le plafond horaire en
    vingt minutes, et pour rien."""
    monkeypatch.setattr(settings, "meta_fb_access_token", None)
    source = _source(db_session, "stmpdrcrds")
    adapter = FauxAdapter([_item()])

    rapport = _run(db_session, {source.id: adapter})

    assert adapter.appels == 0
    assert rapport.appels == 0
    assert lire_etat(chemin_etat()).appels == 0
