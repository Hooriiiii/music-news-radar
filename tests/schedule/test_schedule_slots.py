import datetime as dt
import random
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.schedule.slots import FenetresInvalides, horaires_du_jour, prochains_horaires, verifier_fenetres

PARIS = ZoneInfo("Europe/Paris")


def _local(moment):
    return moment.astimezone(PARIS)


FENETRES_CARROUSEL = ((dt.time(8, 30), dt.time(10, 30)),
                      (dt.time(14, 30), dt.time(16, 30)),
                      (dt.time(19, 0), dt.time(20, 15)))
FENETRES_REEL = ((dt.time(11, 0), dt.time(12, 30)),
                 (dt.time(17, 0), dt.time(18, 30)),
                 (dt.time(21, 0), dt.time(22, 30)))


def test_trois_horaires_par_jour_dans_leurs_fenetres():
    for decalage in range(40):
        jour = dt.date(2026, 9, 20) + dt.timedelta(days=decalage)
        horaires = horaires_du_jour(jour)
        assert len(horaires) == 3
        for heure, (debut, fin) in zip(horaires, FENETRES_CARROUSEL):
            assert _local(heure).date() == jour
            assert debut <= _local(heure).time() <= fin


def test_le_tirage_est_deterministe_pour_une_meme_date():
    """Sinon l'heure serait retiree a chaque passage de l'agent et glisserait :
    un creneau pourrait n'etre jamais atteint."""
    assert horaires_du_jour(dt.date(2026, 9, 20)) == horaires_du_jour(dt.date(2026, 9, 20))


def test_le_tirage_change_d_un_jour_a_l_autre():
    matins = {
        _local(horaires_du_jour(dt.date(2026, 9, 1) + dt.timedelta(days=i))[0]).time()
        for i in range(10)
    }
    assert len(matins) > 1


def test_les_horaires_sont_en_utc():
    assert all(h.tzinfo == dt.timezone.utc for h in horaires_du_jour(dt.date(2026, 9, 20)))


def test_le_jour_du_changement_d_heure_reste_dans_les_fenetres():
    for heure, (debut, fin) in zip(horaires_du_jour(dt.date(2026, 10, 25)), FENETRES_CARROUSEL):
        assert debut <= _local(heure).time() <= fin


def test_prochains_horaires_futurs_et_dans_l_ordre():
    maintenant = dt.datetime(2026, 9, 20, 12, 0, tzinfo=PARIS).astimezone(dt.timezone.utc)
    horaires = prochains_horaires(maintenant, 9)
    assert len(horaires) == 9
    assert horaires == sorted(horaires)
    assert all(h > maintenant for h in horaires)
    # A midi, les fenetres de 14 h 30 et de 19 h sont encore devant.
    assert [_local(h).date() for h in horaires[:3]] == [dt.date(2026, 9, 20)] * 2 + [dt.date(2026, 9, 21)]


def test_apres_la_fenetre_du_soir_on_passe_au_lendemain():
    maintenant = dt.datetime(2026, 9, 20, 20, 30, tzinfo=PARIS).astimezone(dt.timezone.utc)
    assert _local(prochains_horaires(maintenant, 1)[0]).date() == dt.date(2026, 9, 21)


def test_les_carrousels_gardent_exactement_leurs_heures():
    """Changer la graine des carrousels deplacerait toutes les heures deja
    programmees en base."""
    jour = dt.date(2026, 9, 20)
    attendus = []
    for rang, (debut, fin) in enumerate(FENETRES_CARROUSEL):
        ouverture = dt.datetime.combine(jour, debut, tzinfo=PARIS)
        minutes = int((dt.datetime.combine(jour, fin, tzinfo=PARIS) - ouverture).total_seconds() // 60)
        tirage = random.Random(f"kinda-{jour.isoformat()}-{rang}").randint(0, minutes)
        attendus.append((ouverture + dt.timedelta(minutes=tirage)).astimezone(dt.timezone.utc))
    assert horaires_du_jour(jour) == attendus


def test_trois_reels_par_jour_dans_leurs_fenetres():
    for decalage in range(40):
        jour = dt.date(2026, 9, 20) + dt.timedelta(days=decalage)
        horaires = horaires_du_jour(jour, "reel")
        assert len(horaires) == 3
        for heure, (debut, fin) in zip(horaires, FENETRES_REEL):
            assert debut <= _local(heure).time() <= fin


def test_les_six_fenetres_par_defaut_ne_se_chevauchent_pas():
    """Six publications par jour, deux formats : `publier_a` est unique tous
    formats confondus, donc la grille livree doit etre disjointe."""
    verifier_fenetres()
    verifier_fenetres("reel")


def test_les_fenetres_carrousel_sont_une_liste_configurable(monkeypatch):
    """Le nombre de posts par jour est une ligne de `.env`, pas une ligne de code."""
    monkeypatch.setattr(settings, "publication_fenetres", "07:00-07:30,12:00-12:30,15:00-15:30,23:00-23:30")
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
    horaires = horaires_du_jour(dt.date(2026, 9, 20))
    assert len(horaires) == 4
    assert dt.time(7, 0) <= _local(horaires[0]).time() <= dt.time(7, 30)
    assert dt.time(23, 0) <= _local(horaires[3]).time() <= dt.time(23, 30)


def test_un_env_d_avant_la_generalisation_reste_compris(monkeypatch):
    """Repli : `PUBLICATION_FENETRES` vide rend la main a l'ancien couple
    matin / apres-midi, pour qu'un `.env` d'avant ne soit pas ignore en silence."""
    monkeypatch.setattr(settings, "publication_fenetres", "")
    monkeypatch.setattr(settings, "publication_fenetre_matin", "08:30-11:00")
    monkeypatch.setattr(settings, "publication_fenetre_apres_midi", "17:00-19:30")
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
    matin, apres_midi = horaires_du_jour(dt.date(2026, 9, 20))
    assert dt.time(8, 30) <= _local(matin).time() <= dt.time(11, 0)
    assert dt.time(17, 0) <= _local(apres_midi).time() <= dt.time(19, 30)


def test_sans_fenetre_du_tout_la_file_carrousel_est_vide(monkeypatch):
    monkeypatch.setattr(settings, "publication_fenetres", "")
    assert prochains_horaires(dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc), 9) == []


def test_le_tirage_reel_est_deterministe():
    assert horaires_du_jour(dt.date(2026, 9, 20), "reel") == horaires_du_jour(dt.date(2026, 9, 20), "reel")


def test_prochains_horaires_reel():
    maintenant = dt.datetime(2026, 9, 20, 13, 59, tzinfo=PARIS).astimezone(dt.timezone.utc)
    horaires = prochains_horaires(maintenant, 9, "reel")
    assert len(horaires) == 9 and horaires == sorted(horaires)
    assert all(h > maintenant for h in horaires)


def test_zero_creneau_reel_ne_boucle_pas(monkeypatch):
    assert prochains_horaires(dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc), 0, "reel") == []
    monkeypatch.setattr(settings, "publication_reel_fenetres", "")
    assert prochains_horaires(dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc), 9, "reel") == []


def test_une_fenetre_reel_qui_chevauche_un_carrousel_est_refusee(monkeypatch):
    """Sinon deux creneaux pourraient tomber a la meme minute : publier_a est
    unique, l'arbitrage leverait une IntegrityError obscure."""
    monkeypatch.setattr(settings, "publication_reel_fenetres", "10:00-12:00,21:00-22:30")
    with pytest.raises(FenetresInvalides, match="10:00-12:00"):
        horaires_du_jour(dt.date(2026, 9, 20), "reel")


def test_deux_fenetres_qui_se_touchent_sont_refusees(monkeypatch):
    """Bornes incluses : le tirage peut tomber sur la minute de fin."""
    monkeypatch.setattr(settings, "publication_reel_fenetres", "10:30-14:30")
    with pytest.raises(FenetresInvalides):
        horaires_du_jour(dt.date(2026, 9, 20), "reel")


# --- M5 : seuls les formats actifs sont verifies ---------------------------------------


def test_des_fenetres_reel_fautives_ne_cassent_pas_les_carrousels(monkeypatch):
    monkeypatch.setattr(settings, "publication_reel_fenetres", "10:00-12:00")
    jour = dt.date(2026, 9, 20)
    assert len(horaires_du_jour(jour)) == 3
    with pytest.raises(FenetresInvalides):
        horaires_du_jour(jour, "reel")


def test_des_fenetres_reel_illisibles_ne_cassent_pas_les_carrousels(monkeypatch):
    monkeypatch.setattr(settings, "publication_reel_fenetres", "midi")
    assert len(horaires_du_jour(dt.date(2026, 9, 20))) == 3


def test_sans_creneau_reel_les_fenetres_reel_ne_sont_pas_verifiees(monkeypatch):
    monkeypatch.setattr(settings, "publication_reel_fenetres", "10:00-12:00")
    monkeypatch.setattr(settings, "publication_creneaux_reel", 0)
    verifier_fenetres()
    verifier_fenetres("reel")


def test_avec_des_creneaux_reel_toutes_les_fenetres_sont_verifiees(monkeypatch):
    monkeypatch.setattr(settings, "publication_reel_fenetres", "10:00-12:00")
    with pytest.raises(FenetresInvalides):
        verifier_fenetres()
