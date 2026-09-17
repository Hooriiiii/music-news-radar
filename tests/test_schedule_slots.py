import datetime as dt
from zoneinfo import ZoneInfo

from app.schedule.slots import horaires_du_jour, prochains_horaires

PARIS = ZoneInfo("Europe/Paris")


def _local(moment):
    return moment.astimezone(PARIS)


def test_deux_horaires_par_jour_dans_leurs_fenetres():
    for decalage in range(40):
        jour = dt.date(2026, 9, 20) + dt.timedelta(days=decalage)
        matin, apres_midi = horaires_du_jour(jour)
        assert _local(matin).date() == jour == _local(apres_midi).date()
        assert dt.time(8, 30) <= _local(matin).time() <= dt.time(11, 0)
        assert dt.time(17, 0) <= _local(apres_midi).time() <= dt.time(19, 30)


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
    matin, apres_midi = horaires_du_jour(dt.date(2026, 10, 25))
    assert dt.time(8, 30) <= _local(matin).time() <= dt.time(11, 0)
    assert dt.time(17, 0) <= _local(apres_midi).time() <= dt.time(19, 30)


def test_prochains_horaires_futurs_et_dans_l_ordre():
    maintenant = dt.datetime(2026, 9, 20, 12, 0, tzinfo=PARIS).astimezone(dt.timezone.utc)
    horaires = prochains_horaires(maintenant, 6)
    assert len(horaires) == 6
    assert horaires == sorted(horaires)
    assert all(h > maintenant for h in horaires)
    assert _local(horaires[0]).date() == dt.date(2026, 9, 20)
    assert _local(horaires[1]).date() == dt.date(2026, 9, 21)


def test_apres_la_fenetre_du_soir_on_passe_au_lendemain():
    maintenant = dt.datetime(2026, 9, 20, 20, 0, tzinfo=PARIS).astimezone(dt.timezone.utc)
    assert _local(prochains_horaires(maintenant, 1)[0]).date() == dt.date(2026, 9, 21)
