"""Le pré-filtre local : un modèle gratuit qui dit « ça ne vaut pas un appel Claude ».

Aucun test ne touche le réseau : tout passe par un transport httpx simulé.
"""

import json

import httpx
import pytest

from app.models import Article, Source, SourceType
from app.pipeline.prefilter import make_local_scorer


def make_article():
    article = Article(
        source_id=1,
        url="https://example.com/a",
        title="Aphex Twin annonce un nouvel album",
        summary="Extrait du flux RSS.",
        raw_hash="x" * 64,
    )
    article.source = Source(id=1, name="Mixmag", type=SourceType.RSS, url="https://mixmag.net")
    return article


SCORE_VALIDE = {
    "relevance": 30,
    "hotness": 10,
    "emergence": 20,
    "category": "other",
    "imprint": None,
    "summary": "Un résumé.",
    "artists": [],
}


def client_qui_repond(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def reponse_chat(contenu: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={"choices": [{"message": {"content": contenu}}]},
    )


# --- Le cas nominal ---


def test_note_un_article():
    scorer = make_local_scorer(
        client=client_qui_repond(lambda r: reponse_chat(json.dumps(SCORE_VALIDE)))
    )
    score = scorer(make_article())
    assert score is not None
    assert score.relevance == 30
    assert score.category.value == "other"


def test_la_requete_porte_les_garde_fous_qwen():
    vues = {}

    def handler(request: httpx.Request) -> httpx.Response:
        vues["payload"] = json.loads(request.content)
        vues["url"] = str(request.url)
        return reponse_chat(json.dumps(SCORE_VALIDE))

    scorer = make_local_scorer(client=client_qui_repond(handler), model="qwen/qwen3-4b")
    scorer(make_article())

    payload = vues["payload"]
    assert payload["model"] == "qwen/qwen3-4b"
    assert payload["temperature"] == pytest.approx(0.2)
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    # /no_think : les Qwen3 raisonnent à voix haute par défaut, ce monologue
    # casse le JSON même quand le template est censé l'avoir désactivé
    assert payload["messages"][-1]["content"].rstrip().endswith("/no_think")
    # le prompt système est celui du scoring, pas une copie
    from app.pipeline.scoring import SYSTEM_PROMPT

    systeme = payload["messages"][0]["content"]
    assert systeme.startswith(SYSTEM_PROMPT)
    # la seule addition : le garde-fou qui empêche le pré-filtre d'être plus
    # sévère que Claude sur les comptes médias
    from app.pipeline.prefilter import CONSIGNE_PREFILTRE

    assert systeme == SYSTEM_PROMPT + CONSIGNE_PREFILTRE
    assert "45" in CONSIGNE_PREFILTRE


def test_reutilise_le_schema_de_article_score():
    vues = {}

    def handler(request: httpx.Request) -> httpx.Response:
        vues["payload"] = json.loads(request.content)
        return reponse_chat(json.dumps(SCORE_VALIDE))

    make_local_scorer(client=client_qui_repond(handler))(make_article())
    schema = vues["payload"]["response_format"]["json_schema"]["schema"]
    assert "relevance" in schema["properties"]
    assert "emergence" in schema["properties"]


# --- Tous les échecs valent None, jamais une exception ---


def test_erreur_http_rend_none():
    scorer = make_local_scorer(
        client=client_qui_repond(lambda r: httpx.Response(500, text="boom"))
    )
    assert scorer(make_article()) is None


def test_json_illisible_rend_none():
    scorer = make_local_scorer(client=client_qui_repond(lambda r: reponse_chat("pas du json")))
    assert scorer(make_article()) is None


def test_corps_de_reponse_non_json_rend_none():
    scorer = make_local_scorer(
        client=client_qui_repond(lambda r: httpx.Response(200, text="<html>LM Studio</html>"))
    )
    assert scorer(make_article()) is None


def test_json_hors_schema_rend_none():
    hors_schema = dict(SCORE_VALIDE, relevance=500)
    scorer = make_local_scorer(
        client=client_qui_repond(lambda r: reponse_chat(json.dumps(hors_schema)))
    )
    assert scorer(make_article()) is None


def test_reponse_sans_choix_rend_none():
    scorer = make_local_scorer(
        client=client_qui_repond(lambda r: httpx.Response(200, json={"error": "no model loaded"}))
    )
    assert scorer(make_article()) is None


def test_lm_studio_eteint_rend_none():
    """LM Studio fermé = connexion refusée : le pré-filtre se tait, il ne casse rien."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    assert make_local_scorer(client=client_qui_repond(handler))(make_article()) is None


def test_timeout_rend_none():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("trop lent", request=request)

    assert make_local_scorer(client=client_qui_repond(handler))(make_article()) is None


def test_monologue_qwen_avant_le_json_est_tolere():
    """Ceinture et bretelles : si un <think> passe malgré tout, on lit le JSON."""
    contenu = "<think>hmm, article moyen</think>\n" + json.dumps(SCORE_VALIDE)
    scorer = make_local_scorer(client=client_qui_repond(lambda r: reponse_chat(contenu)))
    score = scorer(make_article())
    assert score is not None and score.relevance == 30
