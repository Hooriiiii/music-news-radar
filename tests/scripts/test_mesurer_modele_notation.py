"""La mesure Haiku 4.5 / 5.5 : ce qu'elle appelle, ce qu'elle n'ecrit jamais."""

import json
from types import SimpleNamespace

import pydantic
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Article, Source, SourceCategorie, SourceType
from app.pipeline import credits_anthropic
from app.pipeline.scoring import ArticleScore, Category
from scripts.diagnostic import mesurer_modele_notation as mesure


@pytest.fixture(autouse=True)
def _effort_stable(monkeypatch):
    monkeypatch.setattr(settings, "scoring_effort", "low")


def _usage(entree=100, sortie=50, lu=0, ecrit=0):
    return SimpleNamespace(
        input_tokens=entree, output_tokens=sortie,
        cache_read_input_tokens=lu, cache_creation_input_tokens=ecrit, server_tool_use=None,
    )


def _reponse(relevance=70, hotness=40, emergence=10, stop_reason="end_turn", parsed=True):
    note = ArticleScore(
        relevance=relevance, hotness=hotness, emergence=emergence,
        category=Category.OTHER, summary="Resume du modele.",
    )
    return SimpleNamespace(
        parsed_output=note if parsed else None, stop_reason=stop_reason, usage=_usage(),
        model="x", _request_id="req",
    )


class _Client:
    """`reponses` : fonction (appel n, kwargs) -> reponse, ou exception a lever."""

    def __init__(self, reponses=lambda n, kw: _reponse()):
        self.messages = self
        self.appels = []
        self._reponses = reponses

    def parse(self, **kwargs):
        self.appels.append(kwargs)
        sortie = self._reponses(len(self.appels), kwargs)
        if isinstance(sortie, Exception):
            raise sortie
        return sortie


def _source(db, nom, categorie=SourceCategorie.MEDIA, genre="electronic"):
    s = Source(name=nom, type=SourceType.RSS, url=f"https://{nom}", categorie=categorie, genre=genre)
    db.add(s)
    db.flush()
    return s


def _article(db, source, n, **autres):
    a = Article(
        source_id=source.id, url=f"https://a/{n}", title=f"Titre {n}", summary=f"Brut {n}",
        raw_hash=str(n).zfill(64), **autres,
    )
    db.add(a)
    db.flush()
    return a


@pytest.fixture()
def base(db_session):
    media = _source(db_session, "M")
    pop = _source(db_session, "P", genre="Pop")
    artiste = _source(db_session, "A", categorie=SourceCategorie.ARTISTE)
    representant = _article(db_session, media, 1)
    for n in (2, 3):
        _article(db_session, media, n)
    _article(db_session, pop, 4)
    _article(db_session, artiste, 5)
    _article(db_session, media, 6, relevance_score=50)  # deja note
    _article(db_session, media, 7, duplicate_of_id=representant.id)  # doublon
    db_session.commit()
    return db_session


def _usine(db):
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False)


def _lancer(db, tmp_path, *autres, client=None):
    client = client if client is not None else _Client()
    code = mesure.main(
        ["--sortie", str(tmp_path), *autres], usine=_usine(db), client=client, attendre=lambda: None
    )
    return code, client


def _json(tmp_path):
    return json.loads(next(tmp_path.glob("mesure_notation-*.json")).read_text())


def test_sans_apply_aucun_appel_aucun_fichier_et_un_cout_estime(base, tmp_path, capsys):
    code, client = _lancer(base, tmp_path)
    sortie = capsys.readouterr().out
    assert code == 0
    assert client.appels == []
    assert list(tmp_path.iterdir()) == []
    assert "pipeline" in sortie.splitlines()[0] and "veille" in sortie.splitlines()[0]
    assert "5 article(s) eligible(s)" in sortie
    assert "15 appels" in sortie
    assert "$" in sortie


def test_seuls_les_articles_que_score_pending_noterait_sont_eligibles(base):
    eligibles = mesure.charger_eligibles(_usine(base))
    assert sorted(a.title for a in eligibles) == [f"Titre {n}" for n in range(1, 6)]


def test_un_post_quasi_vide_n_est_pas_eligible_claude_ne_le_note_jamais(db_session):
    ig = Source(name="IG", type=SourceType.INSTAGRAM, url="ig:x", categorie=SourceCategorie.ARTISTE)
    db_session.add(ig)
    db_session.flush()
    article = _article(db_session, ig, 1)
    article.summary, article.title = None, "Post Instagram"
    db_session.commit()
    assert mesure.charger_eligibles(_usine(db_session)) == []


def test_tirage_reproductible_et_stratifie(base):
    eligibles = mesure.charger_eligibles(_usine(base))
    premier = [a.id for a in mesure.tirer(eligibles, 3, graine=7)]
    assert premier == [a.id for a in mesure.tirer(list(reversed(eligibles)), 3, graine=7)]
    strates = {mesure.strate(a) for a in mesure.tirer(eligibles, 3, graine=7)}
    assert strates == {"media/autre", "media/pop", "artiste/autre"}
    assert len(mesure.tirer(eligibles, 99, graine=7)) == 5


def _etat_articles(db):
    db.expire_all()
    return [
        (a.id, a.summary, a.relevance_score, a.hotness_score, a.emergence_score, a.scored_by)
        for a in db.scalars(select(Article).order_by(Article.id))
    ]


def test_trois_passes_par_article_et_rien_d_ecrit_en_base(base, tmp_path):
    avant = _etat_articles(base)
    commits = []
    usine = _usine(base)
    client = _Client()

    def usine_espionne():
        session = usine()
        vrai_commit = session.commit
        session.commit = lambda: (commits.append(1), vrai_commit())
        return session

    code = mesure.main(
        ["--sortie", str(tmp_path), "--apply", "--taille", "2"],
        usine=usine_espionne, client=client, attendre=lambda: None,
    )

    assert code == 0
    assert [c["model"] for c in client.appels] == [
        "claude-haiku-4-5", "claude-haiku-4-5", "claude-haiku-5-5",
    ] * 2
    assert "output_config" not in client.appels[0]
    assert client.appels[2]["output_config"] == {"effort": "low"}
    assert commits == []
    assert _etat_articles(base) == avant


def test_consigne_le_poste_mesure_et_ne_touche_pas_aux_credits(base, tmp_path):
    _lancer(base, tmp_path, "--apply", "--taille", "1")
    fichiers = list(settings.meta_token_path.parent.glob("usage_anthropic-*.jsonl"))
    lignes = [json.loads(ligne) for f in fichiers for ligne in f.read_text().splitlines()]
    assert {ligne["poste"] for ligne in lignes} == {"mesure_notation"}
    assert len(lignes) == 3
    assert not credits_anthropic.chemin_etat().exists()


def test_arret_au_premier_refus_de_credits(base, tmp_path, capsys):
    client = _Client(
        lambda n, kw: _reponse() if n == 1 else RuntimeError("Your credit balance is too low")
    )
    code, client = _lancer(base, tmp_path, "--apply", client=client)
    sortie = capsys.readouterr().out
    assert code == 1
    assert len(client.appels) == 2
    assert "credits epuises" in sortie.lower()
    assert not credits_anthropic.chemin_etat().exists()
    assert _json(tmp_path)["arret"]


def _validation_error():
    class Schema(pydantic.BaseModel):
        n: int

    try:
        Schema(n="x")
    except pydantic.ValidationError as exc:
        return exc


def test_refus_vides_invalides_et_erreurs_comptes_sans_planter(base, tmp_path, capsys):
    pannes = {
        3: _reponse(parsed=False, stop_reason="refusal"),
        2: _reponse(parsed=False, stop_reason="max_tokens"),
        4: _validation_error(),
        5: RuntimeError("reseau"),
    }
    client = _Client(lambda n, kw: pannes.get(n, _reponse()))

    code, _ = _lancer(base, tmp_path, "--apply", "--taille", "2", client=client)

    assert code == 0
    donnees = _json(tmp_path)
    assert donnees["statuts"]["claude-haiku-5-5"]["refus"] == 1
    assert donnees["statuts"]["claude-haiku-4-5"]["vide"] == 1
    assert donnees["statuts"]["claude-haiku-4-5"]["invalide"] == 1
    assert donnees["statuts"]["claude-haiku-4-5"]["erreur"] == 1
    # La sortie invalide est facturee sans qu'on sache combien : INCONNU, jamais 0.
    assert "INCONNU" in capsys.readouterr().out


def test_deltas_et_franchissements_dans_les_deux_comparaisons(base, tmp_path, capsys):
    notes = [58, 62, 61]
    client = _Client(lambda n, kw: _reponse(relevance=notes[n - 1]))
    _lancer(base, tmp_path, "--apply", "--taille", "1", client=client)
    sortie = capsys.readouterr().out
    donnees = _json(tmp_path)

    bruit = donnees["comparaisons"]["bruit_4_5_contre_4_5"]["global"]["relevance"]
    modele = donnees["comparaisons"]["modele_4_5_contre_5_5"]["global"]["relevance"]
    assert (bruit["delta_moyen"], bruit["delta_absolu"], bruit["franchissements"]) == (4, 4, 1)
    assert (modele["delta_moyen"], modele["delta_absolu"], modele["franchissements"]) == (3, 3, 1)
    assert len(donnees["franchissements"]) == 2
    assert "Titre" in sortie
    assert "verdict" not in sortie.lower()


def test_cout_reel_par_modele_cache_lu_inclus(base, tmp_path):
    def reponse(n, kw):
        r = _reponse()
        r.usage = _usage(entree=1000, sortie=100, lu=1000)
        return r

    _lancer(base, tmp_path, "--apply", "--taille", "1", client=_Client(reponse))
    consommation = _json(tmp_path)["consommation"]
    # 2 appels a 1 $/5 $ : (1000 + 500 + 100) / 1e6 chacun ; 1 appel a 0,10 $/0,50 $.
    assert consommation["claude-haiku-4-5"]["cout"] == pytest.approx(0.0032)
    assert consommation["claude-haiku-5-5"]["cout"] == pytest.approx(0.00016)
    assert consommation["claude-haiku-4-5"]["cache_read_input_tokens"] == 2000


def test_sans_article_eligible_le_dire_sans_appel(db_session, tmp_path, capsys):
    code, client = _lancer(db_session, tmp_path, "--apply")
    assert code == 0
    assert client.appels == []
    assert "Aucun article eligible" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
