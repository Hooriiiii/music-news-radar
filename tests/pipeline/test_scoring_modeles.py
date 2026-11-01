"""Ce que `make_claude_scorer` envoie selon le modele, et ce qu'il fait d'une reponse sans note."""

import json
from types import SimpleNamespace

import anthropic
import httpx
import pydantic
import pytest

from app.config import settings
from app.models import Article, Source, SourceCategorie, SourceType
from app.pipeline import credits_anthropic
from app.pipeline.cluster_arbitrage import VerdictArbitrage, make_claude_arbitre
from app.pipeline.scoring import (
    MAX_TOKENS_NOTATION,
    SYSTEM_PROMPT,
    ArticleScore,
    Category,
    ReponseSansNote,
    make_claude_scorer,
    score_pending,
)


def _note():
    return ArticleScore(
        relevance=70, hotness=40, emergence=10, category=Category.OTHER, summary="Resume."
    )


class _Client:
    def __init__(self, parsed=None, stop_reason="end_turn"):
        self.appels = []
        self.messages = self
        self._parsed = parsed
        self._stop_reason = stop_reason

    def parse(self, **kwargs):
        self.appels.append(kwargs)
        return SimpleNamespace(parsed_output=self._parsed, stop_reason=self._stop_reason, usage=None)


def _article():
    return Article(url="https://x.y/1", title="T", raw_hash="0" * 64)


def test_defauts_le_passage_a_5_5_n_est_pas_decide_par_le_code():
    champs = type(settings).model_fields
    assert champs["scoring_model"].default == "claude-haiku-4-5"
    assert champs["arbitrage_model"].default == "claude-haiku-4-5"
    assert champs["scoring_effort"].default == "low"


def _sdk_reel(*contenus):
    """Le vrai SDK sur un transport local : aucun appel reseau. Chaque requete consomme un
    contenu ; le corps envoye reste lisible dans `.corps`."""
    corps = []
    restants = list(contenus)

    def repondre(requete: httpx.Request) -> httpx.Response:
        corps.append(json.loads(requete.content))
        contenu, raison = restants.pop(0)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": corps[-1]["model"],
                "content": contenu,
                "stop_reason": raison,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    client = anthropic.Anthropic(
        api_key="test",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(repondre)),
    )
    client.corps = corps
    return client


def _texte(texte, stop_reason="end_turn"):
    return ([{"type": "text", "text": texte}], stop_reason)


NOTE_JSON = _note().model_dump_json()
REFLEXION = {"type": "thinking", "thinking": "Je reflechis.", "signature": "sig"}


def test_haiku_4_5_ne_recoit_aucun_effort_qui_le_ferait_planter():
    """Le SDK envoie `output_config` avec `format` seul : c'est `effort` qui ferait planter."""
    client = _sdk_reel(_texte(NOTE_JSON))
    make_claude_scorer(client, modele="claude-haiku-4-5")(_article())
    envoye = client.corps[0]
    assert envoye["model"] == "claude-haiku-4-5"
    assert "format" in envoye["output_config"]
    assert "effort" not in envoye["output_config"]


def test_haiku_5_5_envoie_l_effort_a_cote_du_format(monkeypatch):
    monkeypatch.setattr(settings, "scoring_effort", "low")
    client = _sdk_reel(_texte(NOTE_JSON))
    make_claude_scorer(client, modele="claude-haiku-5-5")(_article())
    assert client.corps[0]["output_config"]["effort"] == "low"
    assert "format" in client.corps[0]["output_config"]


@pytest.mark.parametrize("modele", ["claude-haiku-5-5", "claude-haiku-5-5-20261001"])
def test_haiku_5_recoit_l_effort_regle(modele, monkeypatch):
    monkeypatch.setattr(settings, "scoring_effort", "low")
    client = _Client(parsed=_note())
    make_claude_scorer(client, modele=modele)(_article())
    assert client.appels[0]["output_config"] == {"effort": "low"}
    assert client.appels[0]["output_format"] is ArticleScore


def test_l_effort_suit_le_reglage(monkeypatch):
    monkeypatch.setattr(settings, "scoring_effort", "medium")
    client = _Client(parsed=_note())
    make_claude_scorer(client, modele="claude-haiku-5-5")(_article())
    assert client.appels[0]["output_config"] == {"effort": "medium"}


@pytest.mark.parametrize("modele", ["claude-haiku-4-5", "claude-haiku-5-5"])
def test_le_prompt_systeme_part_entier_avec_le_cache(modele):
    client = _Client(parsed=_note())
    make_claude_scorer(client, modele=modele)(_article())
    assert client.appels[0]["system"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert client.appels[0]["max_tokens"] == MAX_TOKENS_NOTATION == 2000


def test_sans_modele_explicite_le_reglage_fait_foi(monkeypatch):
    monkeypatch.setattr(settings, "scoring_model", "claude-haiku-5-5")
    monkeypatch.setattr(settings, "scoring_effort", "low")
    client = _Client(parsed=_note())
    make_claude_scorer(client)(_article())
    assert client.appels[0]["model"] == "claude-haiku-5-5"
    assert client.appels[0]["output_config"] == {"effort": "low"}


@pytest.mark.parametrize(
    ("contenu", "stop_reason"),
    [([], "refusal"), ([REFLEXION], "max_tokens")],
    ids=["contenu_vide", "reflexion_seule"],
)
def test_sans_bloc_texte_le_scorer_leve_reponse_sans_note(contenu, stop_reason):
    """Le seul chemin vers `ReponseSansNote` : `max_tokens` n'y mene que sans bloc texte."""
    client = _sdk_reel((contenu, stop_reason))
    with pytest.raises(ReponseSansNote, match=stop_reason) as erreur:
        make_claude_scorer(client)(_article())
    assert erreur.value.stop_reason == stop_reason


@pytest.mark.parametrize(
    "sortie",
    [_texte(NOTE_JSON[:20], "max_tokens"), _texte("Je ne peux pas noter cet article.", "refusal")],
    ids=["json_tronque_par_max_tokens", "refus_en_texte"],
)
def test_un_bloc_texte_invalide_leve_validation_error_pas_reponse_sans_note(sortie):
    with pytest.raises(pydantic.ValidationError):
        make_claude_scorer(_sdk_reel(sortie))(_article())


def _en_attente(db, nombre=1):
    source = Source(name="S", type=SourceType.RSS, url="https://s", categorie=SourceCategorie.MEDIA)
    db.add(source)
    db.flush()
    articles = [
        Article(
            source_id=source.id, url=f"https://x.y/{n}", title="T", summary="Brut",
            raw_hash=str(n + 1) * 64,
        )
        for n in range(nombre)
    ]
    db.add_all(articles)
    db.commit()
    return articles[0] if nombre == 1 else articles


def test_score_pending_laisse_l_article_refuse_sans_note_et_ne_crie_pas_aux_credits(db_session):
    article = _en_attente(db_session)
    scorer = make_claude_scorer(_Client(parsed=None, stop_reason="refusal"))

    stats = score_pending(db_session, scorer=scorer)

    db_session.refresh(article)
    assert article.relevance_score is None
    assert article.summary == "Brut"
    assert article.file_exclu_raison is None
    assert (stats.scored, stats.errors) == (0, 1)
    assert "refusal" in stats.derniere_erreur
    etat = credits_anthropic.lire_etat(json.loads(credits_anthropic.chemin_etat().read_text()))
    # Une reponse 200 prouve le solde : le voyant ne reste pas sur « epuises ».
    assert etat.refus_le is None
    assert etat.succes_le is not None


def test_validation_error_laisse_l_article_sans_note_et_n_arrete_pas_le_passage(db_session):
    articles = _en_attente(db_session, nombre=2)
    client = _sdk_reel(_texte(NOTE_JSON[:20], "max_tokens"), _texte(NOTE_JSON))

    stats = score_pending(db_session, scorer=make_claude_scorer(client))

    for article in articles:
        db_session.refresh(article)
    assert len(client.corps) == 2
    assert (stats.scored, stats.errors) == (1, 1)
    sans_note = [a for a in articles if a.relevance_score is None]
    assert len(sans_note) == 1
    assert sans_note[0].summary == "Brut"
    assert sans_note[0].file_exclu_raison is None


def test_validation_error_ne_consigne_aucun_succes_de_credits(db_session):
    _en_attente(db_session)
    client = _sdk_reel(_texte("Je ne peux pas noter cet article.", "refusal"))

    stats = score_pending(db_session, scorer=make_claude_scorer(client))

    assert stats.errors == 1
    assert not credits_anthropic.chemin_etat().exists()


def test_l_arbitrage_suit_son_propre_modele_pas_celui_de_la_notation(monkeypatch):
    monkeypatch.setattr(settings, "scoring_model", "claude-haiku-5-5")
    monkeypatch.setattr(settings, "arbitrage_model", "modele-arbitrage")
    client = _Client(parsed=VerdictArbitrage(meme_actu=False, raison="non"))

    make_claude_arbitre(client)(_article(), _article())

    assert client.appels[0]["model"] == "modele-arbitrage"
    assert "effort" not in client.appels[0].get("output_config", {})
