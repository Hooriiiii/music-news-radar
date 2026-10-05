"""La LECTURE de l'archive d'observations (Phase 2C, Tasks 1 et 2).

`app/pipeline/event_archive.py` est l'autre moitie de `event_features.py` :
celle qui touche la base. Ces tests couvrent ce que le module pur ne peut pas
prouver -- et d'abord LE CHOIX DU CHAMP DE DATE, qui ne ferait echouer aucun
calcul s'il basculait sur `observed_at` : il rendrait simplement toute la
velocite fausse, en mesurant notre cadence d'ingestion a la place de la
propagation de l'actu.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.models import Article, Event, EventObservation, Source, SourceRole, SourceType
from app.pipeline.event_archive import (
    ArchiveEvenements,
    charger_archive,
    dates_de_publication,
)
from app.pipeline.event_features import velocity

UTC = dt.timezone.utc


def _source(db, name="Feed"):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.test/f")
    db.add(src)
    db.commit()
    return src


def _observation(db, event, source, *, n, titre, publie_le, observe_le=None):
    art = Article(
        source_id=source.id,
        url=f"https://{source.name}.test/{n}",
        title=titre,
        raw_hash=f"{n:064d}",
        published_at=publie_le,
    )
    db.add(art)
    db.commit()
    obs = EventObservation(
        event_id=event.id,
        article_id=art.id,
        platform=SourceType.RSS,
        source_id=source.id,
        source_role=SourceRole.SECONDARY,
        published_at=publie_le,
        title=titre,
        url=art.url,
        source_name=source.name,
    )
    if observe_le is not None:
        obs.observed_at = observe_le
    db.add(obs)
    db.commit()
    return obs


def _event(db, titre, quand):
    ev = Event(titre=titre, first_seen_at=quand, last_seen_at=quand)
    db.add(ev)
    db.commit()
    return ev


class TestDatesDePublication:
    """LE test-sentinelle de la Phase 2C.

    Mesure du plan : l'etalement des dates d'INGESTION a une mediane de 1,68 h
    (44 % des evenements sous une heure -- notre propre cadence horaire) ;
    celui des dates de PUBLICATION, 10,69 h. Un module qui basculerait sur
    `observed_at` continuerait de rendre des nombres plausibles. Ce test est
    ce qui rend ce basculement rouge.
    """

    def test_le_champ_lu_est_published_at_et_jamais_observed_at(self, db_session):
        src = _source(db_session)
        ev = _event(db_session, "Une actu", dt.datetime(2026, 9, 25, tzinfo=UTC))
        # Publiees a 12 h d'ecart, mais ingerees a 5 minutes d'ecart -- le
        # profil EXACT mesure en production.
        a = _observation(
            db_session,
            ev,
            src,
            n=1,
            titre="Une actu",
            publie_le=dt.datetime(2026, 9, 25, 8, 0, tzinfo=UTC),
            observe_le=dt.datetime(2026, 9, 25, 20, 0, tzinfo=UTC),
        )
        b = _observation(
            db_session,
            ev,
            src,
            n=2,
            titre="Une actu reprise",
            publie_le=dt.datetime(2026, 9, 25, 20, 0, tzinfo=UTC),
            observe_le=dt.datetime(2026, 9, 25, 20, 5, tzinfo=UTC),
        )
        dates = dates_de_publication([a, b])
        assert dates == [
            dt.datetime(2026, 9, 25, 8, 0, tzinfo=UTC),
            dt.datetime(2026, 9, 25, 20, 0, tzinfo=UTC),
        ]
        # Et le taux qui en decoule est celui de la PROPAGATION (1 preuve sur
        # 12 h), pas celui de notre pipeline (1 preuve sur 5 minutes = 12/h).
        assert velocity(dates) == pytest.approx(1 / 12)

    def test_une_observation_sans_date_de_publication_est_ecartee(self, db_session):
        src = _source(db_session)
        ev = _event(db_session, "Une actu", dt.datetime(2026, 9, 25, tzinfo=UTC))
        a = _observation(
            db_session,
            ev,
            src,
            n=1,
            titre="Une actu",
            publie_le=dt.datetime(2026, 9, 25, 8, 0, tzinfo=UTC),
        )
        b = _observation(db_session, ev, src, n=2, titre="Une reprise", publie_le=None)
        # Jamais remplacee par son `observed_at`, qui est toujours renseigne :
        # ce repli reintroduirait notre cadence dans la mesure.
        assert dates_de_publication([a, b]) == [dt.datetime(2026, 9, 25, 8, 0, tzinfo=UTC)]
        assert velocity(dates_de_publication([a, b])) is None


class TestChargerArchive:
    def test_sans_les_tables_l_archive_est_vide_et_ne_leve_pas(self, monkeypatch, db_session):
        from app.pipeline import events

        monkeypatch.setattr(events, "_tables_disponibles", lambda db: False)
        archive = charger_archive(db_session)
        assert archive.entrees == ()
        assert archive.debut is None
        assert archive.profondeur_heures(dt.datetime(2026, 9, 30, tzinfo=UTC)) is None

    def test_le_debut_n_est_pas_borne_par_la_fenetre_de_chargement(self, db_session):
        # La profondeur doit dire depuis quand on ARCHIVE, pas depuis quand on
        # vient de regarder : sinon toute archive paraitrait profonde.
        src = _source(db_session)
        vieux = dt.datetime.now(UTC) - dt.timedelta(days=30)
        recent = dt.datetime.now(UTC) - dt.timedelta(hours=2)
        ev = _event(db_session, "Une actu", recent)
        _observation(db_session, ev, src, n=1, titre="Un vieux titre", publie_le=vieux)
        _observation(db_session, ev, src, n=2, titre="Une actu", publie_le=recent)
        archive = charger_archive(db_session, jours=7)
        # Le vieux titre est HORS de la fenetre de chargement (7 j) : il ne
        # sert pas a juger une reprise...
        assert [quand for (_, quand, _) in archive.entrees] != []
        assert len(archive.entrees) == 1
        # ... mais il fixe bien le debut de l'archive. (SQLite rend les
        # datetimes naives la ou Postgres garde le fuseau : on compare donc
        # sans tzinfo, l'objet du test est la BORNE, pas le fuseau.)
        assert archive.debut.replace(tzinfo=None) == vieux.replace(tzinfo=None)
        assert (recent.replace(tzinfo=None) - archive.debut.replace(tzinfo=None)) == pytest.approx(
            dt.timedelta(days=30, hours=-2), abs=dt.timedelta(minutes=5)
        )


class TestSimilariteMax:
    def _archive(self, *entrees):
        from app.pipeline.cluster import significant_tokens

        return ArchiveEvenements(
            entrees=tuple(
                (eid, quand, frozenset(significant_tokens(titre))) for eid, quand, titre in entrees
            ),
            debut=min(quand for _, quand, _ in entrees),
        )

    def test_une_reprise_a_l_identique_ressort_a_un(self):
        # Cas reel : le meme article Mixmag re-emis 51 h plus tard.
        titre = "Erol Alkan to publish book documenting his legendary London club night"
        archive = self._archive((1, dt.datetime(2026, 9, 25, tzinfo=UTC), titre))
        assert archive.similarite_max(
            event_id=2, titre=titre, avant=dt.datetime(2026, 9, 27, tzinfo=UTC)
        ) == pytest.approx(1.0)

    def test_ses_propres_observations_ne_font_pas_une_reprise(self):
        titre = "Boys Noize Announces First Album in Five Years and World Tour"
        archive = self._archive((7, dt.datetime(2026, 9, 25, tzinfo=UTC), titre))
        assert (
            archive.similarite_max(
                event_id=7, titre=titre, avant=dt.datetime(2026, 9, 27, tzinfo=UTC)
            )
            == 0.0
        )

    def test_un_titre_posterieur_ne_compte_pas(self):
        titre = "Boys Noize Announces First Album in Five Years and World Tour"
        archive = self._archive((7, dt.datetime(2026, 9, 28, tzinfo=UTC), titre))
        assert (
            archive.similarite_max(
                event_id=9, titre=titre, avant=dt.datetime(2026, 9, 27, tzinfo=UTC)
            )
            == 0.0
        )

    def test_deux_actus_differentes_du_meme_artiste_ne_sont_pas_une_reprise(self):
        # La paire qui a impose `archive_similarity_from_tokens` a la dedup le
        # 2026-09-21 : le premier titre est CONTENU dans le second.
        archive = self._archive(
            (1, dt.datetime(2026, 9, 25, tzinfo=UTC), "Charlotte de Witte annonce un nouvel EP")
        )
        score = archive.similarite_max(
            event_id=2,
            titre="Charlotte de Witte annonce une tournee mondiale",
            avant=dt.datetime(2026, 9, 27, tzinfo=UTC),
        )
        assert score < 0.8

    def test_un_titre_sans_mot_significatif_rend_none_et_non_zero(self):
        # « Post Instagram » : la comparaison n'a PAS eu lieu, elle n'a pas
        # conclu a la difference -- et `nouveaute` doit pouvoir faire la
        # difference entre les deux.
        archive = self._archive((1, dt.datetime(2026, 9, 25, tzinfo=UTC), "Un titre quelconque"))
        assert (
            archive.similarite_max(
                event_id=2, titre="", avant=dt.datetime(2026, 9, 27, tzinfo=UTC)
            )
            is None
        )


class TestRienNeChangeDansLaPublication:
    """Global Constraint de la Phase 2C : « 2C calcule et expose ; c'est 2D
    qui classera. Un test doit le prouver. »

    Meme motif que le test-sentinelle du quota Graph
    (`tests/pipeline/test_quota_graph.py`) : on interdit l'IMPORT, pas seulement
    l'usage -- une feature lue par l'arbitrage changerait ce qui part en
    ligne, et personne ne s'en apercevrait avant de le voir publie.
    """

    def test_aucun_module_de_publication_ne_lit_les_features_d_evenement(self):
        import pathlib

        racine = pathlib.Path(__file__).resolve().parents[2]
        coupables = []
        for dossier in ("app/publish", "app/schedule"):
            for fichier in (racine / dossier).rglob("*.py"):
                texte = fichier.read_text(encoding="utf-8")
                if "event_features" in texte or "event_archive" in texte:
                    coupables.append(str(fichier.relative_to(racine)))
        assert coupables == [], (
            "2C observe, elle ne decide rien : ces modules de publication lisent "
            f"les features d'evenement -> {coupables}"
        )


class TestUneObservationNeMentJamaisSaDate:
    """Phase 2C, tour de correction 1.

    `EventObservation.published_at` porte une date de PUBLICATION ou rien.
    Y ecrire la date d'ingestion (ce que faisait `events._moment_fait`
    jusqu'ici) faisait mesurer a la velocite notre propre cadence horaire --
    14 % des observations mesurables le 2026-09-30, et indetectable apres
    coup puisque l'article est purge a deux jours.
    """

    def _rattacher(self, db, titre, publie_le):
        from app.models import Article
        from app.pipeline import events

        src = _source(db, "mixmag")
        art = Article(
            source_id=src.id,
            url="https://mixmag.test/1",
            title=titre,
            raw_hash="1" * 64,
            published_at=publie_le,
        )
        art.fetched_at = dt.datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
        db.add(art)
        db.commit()
        ev, _ = events._creer_evenement(db, art)
        events._ajouter_observation(db, ev, art)
        db.commit()
        obs = db.scalars(
            select(EventObservation).where(EventObservation.event_id == ev.id)
        ).all()
        return ev, obs

    def test_un_article_sans_date_de_publication_donne_une_observation_a_null(self, db_session):
        ev, obs = self._rattacher(db_session, "Une actu sans date", publie_le=None)
        assert [o.published_at for o in obs] == [None]
        # Et la velocite n'a alors AUCUNE date a se mettre sous la dent,
        # plutot qu'une date d'ingestion deguisee.
        assert dates_de_publication(obs) == []

    def test_l_evenement_reste_date_malgre_l_observation_sans_date(self, db_session):
        from app.pipeline import events

        ev, _ = self._rattacher(db_session, "Une actu sans date", publie_le=None)
        # Un evenement DOIT porter une date : `first_seen_at` est NOT NULL et
        # la fraicheur se calcule dessus. Le repli d'ingestion vit la, et la
        # seulement -- il ne redescend jamais dans l'observation.
        assert ev.first_seen_at is not None
        assert ev.first_seen_at.replace(tzinfo=None) == dt.datetime(2026, 9, 25, 9, 0)
        # Et il tient au recalcul : sinon l'evenement se ferait redater a
        # l'heure du RATTACHEMENT des le premier membre ajoute.
        events._recalculer(db_session, ev)
        db_session.commit()
        assert ev.first_seen_at.replace(tzinfo=None) == dt.datetime(2026, 9, 25, 9, 0)

    def test_un_article_date_donne_bien_sa_date_de_publication(self, db_session):
        publie = dt.datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
        _, obs = self._rattacher(db_session, "Une actu datee", publie_le=publie)
        assert [o.published_at.replace(tzinfo=None) for o in obs] == [publie.replace(tzinfo=None)]


class TestScriptDeRattrapage:
    """`scripts/reparations/nettoyer_dates_observations.py` : le plan par defaut,
    l'ecriture sous `--apply`, idempotent, et surtout PRUDENT -- il ne touche
    jamais une observation dont l'article a ete purge, ou une date de
    publication legitime."""

    def _observation(self, db, *, n, article_publie_le, observation_publie_le, purge=False):
        from app.models import Article

        src = _source(db, f"src{n}")
        art = Article(
            source_id=src.id,
            url=f"https://src{n}.test/{n}",
            title=f"Titre {n}",
            raw_hash=f"{n:064d}",
            published_at=article_publie_le,
        )
        db.add(art)
        db.commit()
        ev = _event(db, f"Titre {n}", dt.datetime(2026, 9, 25, tzinfo=UTC))
        obs = EventObservation(
            event_id=ev.id,
            article_id=None if purge else art.id,
            platform=SourceType.RSS,
            source_id=src.id,
            source_role=SourceRole.SECONDARY,
            published_at=observation_publie_le,
            title=art.title,
            url=art.url,
            source_name=src.name,
        )
        db.add(obs)
        db.commit()
        return obs

    def test_repare_seulement_la_date_d_ingestion_deguisee(self, db_session):
        from scripts.reparations.nettoyer_dates_observations import nettoyer_dates

        ingestion = dt.datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
        publication = dt.datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
        deguisee = self._observation(
            db_session, n=1, article_publie_le=None, observation_publie_le=ingestion
        )
        legitime = self._observation(
            db_session, n=2, article_publie_le=publication, observation_publie_le=publication
        )
        # Article purge : indecidable, on n'y touche pas plutot que de
        # detruire une vraie mesure.
        purgee = self._observation(
            db_session, n=3, article_publie_le=None, observation_publie_le=ingestion, purge=True
        )

        plan = nettoyer_dates(db_session)
        assert plan.corrigees == 1
        assert plan.article_purge == 1
        # Sans --apply, rien n'a bouge.
        assert deguisee.published_at is not None

        rapport = nettoyer_dates(db_session, appliquer=True)
        assert rapport.corrigees == 1
        db_session.refresh(deguisee)
        db_session.refresh(legitime)
        db_session.refresh(purgee)
        assert deguisee.published_at is None
        assert legitime.published_at is not None
        assert purgee.published_at is not None

    def test_idempotent(self, db_session):
        from scripts.reparations.nettoyer_dates_observations import nettoyer_dates

        self._observation(
            db_session,
            n=1,
            article_publie_le=None,
            observation_publie_le=dt.datetime(2026, 9, 25, 9, 0, tzinfo=UTC),
        )
        assert nettoyer_dates(db_session, appliquer=True).corrigees == 1
        assert nettoyer_dates(db_session, appliquer=True).corrigees == 0
