"""Sentinelle (Phase 2A, Task 4) : aucun champ d'`EventOut`/`EventObservationOut`
ne doit etre silencieusement absent du panneau Événements du dashboard --
meme philosophie que `test_le_dashboard_propose_la_categorie_ecologie` dans
`tests/test_api.py`, qui verifie deja qu'aucune `Category` n'est invisible a
l'usage."""

import pathlib

from app.schemas import EventDetailOut, EventOut

PAGE = pathlib.Path("app/static/dashboard.html").read_text(encoding="utf-8")

# Champs herites (id) ou volontairement geres autrement qu'un acces direct
# `ev.<champ>` / `o.<champ>` dans le JS -- documente ici plutot que devine.
_EVENT_CHAMPS_IGNORES = {"id"}  # affiche via un gabarit ("événement #${ev.id}")
_OBSERVATION_CHAMPS_IGNORES = {
    "id", "event_id", "observed_at",
    # `source_name` (deja affiche) porte l'information editoriale ; `source_id`
    # n'est qu'une cle etrangere technique, sans valeur ajoutee a la lecture.
    "source_id",
}  # techniques, pas montres a l'editorial


def test_le_panneau_evenements_affiche_tous_les_champs_d_event():
    for champ in EventOut.model_fields:
        if champ in _EVENT_CHAMPS_IGNORES:
            continue
        assert f"ev.{champ}" in PAGE, champ


def test_le_panneau_evenements_affiche_tous_les_champs_d_observation():
    champs_observation = set(EventDetailOut.model_fields) - set(EventOut.model_fields)
    assert "observations" in champs_observation
    from app.schemas import EventObservationOut

    for champ in EventObservationOut.model_fields:
        if champ in _OBSERVATION_CHAMPS_IGNORES:
            continue
        assert f"o.{champ}" in PAGE, champ


def test_le_panneau_evenements_existe():
    assert 'id="events-panel"' in PAGE
    assert 'data-preset="events"' in PAGE
    assert "loadEventsPanel" in PAGE
