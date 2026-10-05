"""Le preambule reseau des points d'entree planifies.

Motif concret : l'agent launchd `digest` echouait chaque jour depuis juillet sur
un « failed to resolve host ... .neon.tech ». Il part a 8h30, souvent juste apres
le reveil du Mac, avant que la resolution DNS reponde. L'agent d'ingestion aura
RunAtLoad et partira lui aussi au reveil.

Aucun test ici ne touche la base : `connecter` et `dormir` sont injectes.
"""

import pytest
from sqlalchemy.exc import OperationalError

from app.db import BaseInjoignable, attendre_base


def _echoue(fois: int):
    """Rend un `connecter` qui echoue les `fois` premiers appels puis reussit."""
    restant = {"n": fois}

    def connecter() -> None:
        if restant["n"] > 0:
            restant["n"] -= 1
            raise OperationalError("SELECT 1", {}, Exception("nom d'hote non resolu"))

    return connecter


def test_rend_la_main_immediatement_si_la_base_repond():
    sommeils: list[float] = []

    attendre_base(connecter=_echoue(0), dormir=sommeils.append)

    assert sommeils == []


def test_reessaie_puis_reussit():
    """Le cas du reveil : les premiers essais echouent, le reseau arrive."""
    sommeils: list[float] = []

    attendre_base(essais=5, delai=6.0, connecter=_echoue(2), dormir=sommeils.append)

    # Deux echecs -> deux attentes, pas une de plus : on ne dort pas apres le
    # succes, ni apres le dernier essai.
    assert sommeils == [6.0, 6.0]


def test_leve_apres_le_dernier_essai_sans_dormir_une_fois_de_trop():
    sommeils: list[float] = []

    with pytest.raises(BaseInjoignable, match="3 essais"):
        attendre_base(essais=3, delai=1.0, connecter=_echoue(99), dormir=sommeils.append)

    assert sommeils == [1.0, 1.0]


def test_conserve_la_cause_dorigine():
    """Un « nom d'hote non resolu » et un « mot de passe refuse » ne se
    depannent pas pareil : la cause doit remonter."""
    with pytest.raises(BaseInjoignable) as info:
        attendre_base(essais=1, connecter=_echoue(99), dormir=lambda _: None)

    assert isinstance(info.value.__cause__, OperationalError)
