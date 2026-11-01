"""La consignation de chaque appel Anthropic : forme de la ligne, `null` pour ce
qu'on n'a pas lu, jamais d'exception, et branchement sur les cinq sites."""

import datetime as dt
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pydantic
import pytest

from app.config import settings
from app.content.compose import CompositionError, make_claude_composer
from app.content.illustration import make_claude_juge
from app.content.recherche_web import make_chercheur_claude
from app.models import Article
from app.pipeline import usage_anthropic
from app.pipeline.cluster_arbitrage import make_claude_arbitre
from app.pipeline.scoring import make_claude_scorer
from app.pipeline.usage_anthropic import consigner

CHAMPS = {
    "le", "poste", "modele", "input_tokens", "output_tokens",
    "cache_creation_input_tokens", "cache_read_input_tokens",
    "web_search_requests", "stop_reason", "request_id",
}


def _lignes() -> list[dict]:
    fichiers = sorted(settings.meta_token_path.parent.glob("usage_anthropic-*.jsonl"))
    return [json.loads(ligne) for f in fichiers for ligne in f.read_text().splitlines()]


def _reponse(parsed=None, content=None, recherches=None):
    outil = None if recherches is None else SimpleNamespace(web_search_requests=recherches)
    return SimpleNamespace(
        model="claude-haiku-4-5-20251001",
        stop_reason="end_turn",
        _request_id="req_1",
        parsed_output=parsed,
        content=content or [],
        usage=SimpleNamespace(
            input_tokens=100, output_tokens=20,
            cache_creation_input_tokens=5, cache_read_input_tokens=7,
            server_tool_use=outil,
        ),
    )


def _client(parse=None, create=None):
    return SimpleNamespace(messages=SimpleNamespace(parse=parse, create=create))


def _validation_error() -> pydantic.ValidationError:
    class Schema(pydantic.BaseModel):
        n: int

    try:
        Schema(n="x")
    except pydantic.ValidationError as exc:
        return exc
    raise AssertionError("le schema aurait du refuser")


def _article():
    return Article(id=1, source_id=1, url="https://x/1", title="Titre", raw_hash="1")


# -- la ligne -----------------------------------------------------------------


def test_une_reponse_complete_donne_tous_les_champs():
    assert consigner("notation", _reponse(recherches=3), modele="claude-haiku-4-5") is True

    ligne = _lignes()[0]
    assert set(ligne) == CHAMPS
    assert ligne["poste"] == "notation"
    assert ligne["modele"] == "claude-haiku-4-5"
    assert ligne["input_tokens"] == 100
    assert ligne["output_tokens"] == 20
    assert ligne["cache_creation_input_tokens"] == 5
    assert ligne["cache_read_input_tokens"] == 7
    assert ligne["web_search_requests"] == 3
    assert ligne["stop_reason"] == "end_turn"
    assert ligne["request_id"] == "req_1"
    assert dt.datetime.fromisoformat(ligne["le"]).utcoffset() == dt.timedelta(0)


def test_le_fichier_est_mensuel_et_a_cote_du_jeton():
    consigner("notation", _reponse())
    mois = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    assert (settings.meta_token_path.parent / f"usage_anthropic-{mois}.jsonl").exists()


def test_sans_modele_explicite_on_prend_celui_de_la_reponse():
    consigner("notation", _reponse())
    assert _lignes()[0]["modele"] == "claude-haiku-4-5-20251001"


def test_sans_reponse_tout_est_null_jamais_zero():
    consigner("composition", None, modele="claude-sonnet-5")
    ligne = _lignes()[0]
    assert ligne["poste"] == "composition"
    assert ligne["modele"] == "claude-sonnet-5"
    for champ in CHAMPS - {"le", "poste", "modele"}:
        assert ligne[champ] is None, champ


def test_sans_server_tool_use_la_recherche_est_null():
    consigner("notation", _reponse())
    assert _lignes()[0]["web_search_requests"] is None


def test_une_reponse_magicmock_donne_null_pas_un_faux_nombre():
    consigner("notation", MagicMock())
    ligne = _lignes()[0]
    for champ in CHAMPS - {"le", "poste"}:
        assert ligne[champ] is None, champ


def test_un_booleen_n_est_pas_un_nombre_de_tokens():
    reponse = _reponse()
    reponse.usage.input_tokens = True
    consigner("notation", reponse)
    assert _lignes()[0]["input_tokens"] is None


def test_les_appels_s_ajoutent_sans_s_ecraser():
    consigner("notation", _reponse())
    consigner("arbitrage", _reponse())
    assert [ligne["poste"] for ligne in _lignes()] == ["notation", "arbitrage"]


def test_une_ligne_passe_par_un_seul_write_en_append(monkeypatch):
    """Trois agents ecrivent en parallele : deux `write` pourraient s'entrelacer."""
    drapeaux, ecritures = [], []
    vrai_open, vrai_write = os.open, os.write

    def faux_open(chemin, flags, *a, **k):
        drapeaux.append(flags)
        return vrai_open(chemin, flags, *a, **k)

    def faux_write(fd, octets):
        ecritures.append(octets)
        return vrai_write(fd, octets)

    monkeypatch.setattr(usage_anthropic.os, "open", faux_open)
    monkeypatch.setattr(usage_anthropic.os, "write", faux_write)
    consigner("notation", _reponse())

    assert drapeaux[0] & os.O_APPEND
    assert len(ecritures) == 1
    assert ecritures[0].endswith(b"\n") and ecritures[0].count(b"\n") == 1


def test_ne_leve_pas_quand_le_dossier_est_impossible(tmp_path, monkeypatch):
    bloque = tmp_path / "un-fichier"
    bloque.write_text("pas un dossier")
    monkeypatch.setattr(settings, "meta_token_path", bloque / "jeton.json")
    assert consigner("notation", _reponse()) is False


def test_ne_leve_pas_quand_l_ecriture_echoue(monkeypatch):
    def refuse(*a, **k):
        raise OSError("disque plein")

    monkeypatch.setattr(usage_anthropic.os, "open", refuse)
    assert consigner("notation", _reponse()) is False


# -- les cinq sites -----------------------------------------------------------


def test_notation_consigne_la_reponse():
    client = _client(parse=lambda **k: _reponse(parsed="score"))
    assert make_claude_scorer(client)(_article()) == "score"
    ligne = _lignes()[0]
    assert (ligne["poste"], ligne["modele"]) == ("notation", settings.scoring_model)
    assert ligne["input_tokens"] == 100


def test_notation_consigne_null_sur_reponse_invalide_et_relance():
    def parse(**k):
        raise _validation_error()

    with pytest.raises(pydantic.ValidationError):
        make_claude_scorer(_client(parse=parse))(_article())
    ligne = _lignes()[0]
    assert (ligne["poste"], ligne["input_tokens"]) == ("notation", None)


def test_notation_ne_consigne_pas_une_erreur_d_api():
    """Un refus ou une coupure ne sont pas factures : pas de ligne."""

    def parse(**k):
        raise ConnectionError("reseau")

    with pytest.raises(ConnectionError):
        make_claude_scorer(_client(parse=parse))(_article())
    assert _lignes() == []


def test_arbitrage_consigne_la_reponse():
    sortie = SimpleNamespace(meme_actu=True, raison="meme")
    client = _client(parse=lambda **k: _reponse(parsed=sortie))
    assert make_claude_arbitre(client)(_article(), _article()) == (True, "meme")
    assert _lignes()[0]["poste"] == "arbitrage"
    assert _lignes()[0]["output_tokens"] == 20


def test_arbitrage_sans_sortie_reste_consigne():
    client = _client(parse=lambda **k: _reponse(parsed=None))
    assert make_claude_arbitre(client)(_article(), _article()) is None
    assert _lignes()[0]["input_tokens"] == 100


def test_arbitrage_consigne_null_sur_reponse_invalide():
    def parse(**k):
        raise _validation_error()

    assert make_claude_arbitre(_client(parse=parse))(_article(), _article()) is None
    ligne = _lignes()[0]
    assert (ligne["poste"], ligne["input_tokens"]) == ("arbitrage", None)


def test_arbitrage_ne_consigne_pas_une_erreur_d_api():
    def parse(**k):
        raise ConnectionError("reseau")

    assert make_claude_arbitre(_client(parse=parse))(_article(), _article()) is None
    assert _lignes() == []


def test_composition_consigne_avant_le_traitement_qui_peut_echouer():
    """Sortie vide : la composition echoue, mais chaque appel paye reste compte."""
    client = _client(parse=lambda **k: _reponse(parsed=None))
    with pytest.raises(CompositionError):
        make_claude_composer(client, max_attempts=2)(_article())
    lignes = _lignes()
    assert [ligne["poste"] for ligne in lignes] == ["composition", "composition"]
    assert lignes[0]["input_tokens"] == 100
    assert lignes[0]["modele"] == settings.composer_model


def test_composition_consigne_null_sur_validation_error():
    def parse(**k):
        raise _validation_error()

    with pytest.raises(CompositionError):
        make_claude_composer(_client(parse=parse), max_attempts=2)(_article())
    lignes = _lignes()
    assert len(lignes) == 2
    assert lignes[0]["poste"] == "composition"
    assert lignes[0]["modele"] == settings.composer_model
    assert lignes[0]["input_tokens"] is None
    assert lignes[0]["output_tokens"] is None


def test_composition_ne_consigne_pas_une_erreur_d_api():
    def parse(**k):
        raise ConnectionError("reseau")

    with pytest.raises(ConnectionError):
        make_claude_composer(_client(parse=parse), max_attempts=2)(_article())
    assert _lignes() == []


def test_juge_consigne_meme_si_le_compte_de_verdicts_est_faux():
    client = _client(parse=lambda **k: _reponse(parsed=SimpleNamespace(verdicts=[])))
    with pytest.raises(ValueError):
        make_claude_juge(client)([b"jpeg"])
    ligne = _lignes()[0]
    assert (ligne["poste"], ligne["modele"]) == ("juge_image", settings.visuels_model)
    assert ligne["input_tokens"] == 100


def test_juge_consigne_null_sur_reponse_invalide_et_relance():
    def parse(**k):
        raise _validation_error()

    with pytest.raises(pydantic.ValidationError):
        make_claude_juge(_client(parse=parse))([b"jpeg"])
    ligne = _lignes()[0]
    assert (ligne["poste"], ligne["input_tokens"]) == ("juge_image", None)


def test_recherche_web_consigne_le_nombre_de_recherches():
    client = _client(create=lambda **k: _reponse(recherches=2))
    assert make_chercheur_claude(client)("Daft Punk") == []
    ligne = _lignes()[0]
    assert ligne["poste"] == "recherche_web"
    assert ligne["modele"] == settings.visuels_model
    assert ligne["web_search_requests"] == 2


def test_une_panne_de_consignation_ne_perd_pas_la_note(monkeypatch):
    def refuse(*a, **k):
        raise OSError("disque plein")

    monkeypatch.setattr(usage_anthropic.os, "open", refuse)
    client = _client(parse=lambda **k: _reponse(parsed="score"))
    assert make_claude_scorer(client)(_article()) == "score"
