"""Ce que `make_claude_scorer` envoie selon le modele, et ce qu'il fait d'une reponse sans note."""

import json
from types import SimpleNamespace

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


def test_haiku_4_5_ne_recoit_pas_d_effort_qui_le_ferait_planter():
    client = _Client(parsed=_note())
    make_claude_scorer(client, modele="claude-haiku-4-5")(_article())
    assert client.appels[0]["model"] == "claude-haiku-4-5"
    assert "output_config" not in client.appels[0]


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


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_une_reponse_sans_note_leve_en_nommant_stop_reason(stop_reason):
    client = _Client(parsed=None, stop_reason=stop_reason)
    with pytest.raises(ReponseSansNote, match=stop_reason) as erreur:
        make_claude_scorer(client)(_article())
    assert erreur.value.stop_reason == stop_reason


def _en_attente(db):
    source = Source(name="S", type=SourceType.RSS, url="https://s", categorie=SourceCategorie.MEDIA)
    db.add(source)
    db.flush()
    article = Article(
        source_id=source.id, url="https://x.y/1", title="T", summary="Brut", raw_hash="1" * 64
    )
    db.add(article)
    db.commit()
    return article


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


def test_l_arbitrage_suit_son_propre_modele_pas_celui_de_la_notation(monkeypatch):
    monkeypatch.setattr(settings, "scoring_model", "claude-haiku-5-5")
    monkeypatch.setattr(settings, "arbitrage_model", "modele-arbitrage")
    client = _Client(parsed=VerdictArbitrage(meme_actu=False, raison="non"))

    make_claude_arbitre(client)(_article(), _article())

    assert client.appels[0]["model"] == "modele-arbitrage"
    assert "output_config" not in client.appels[0]
