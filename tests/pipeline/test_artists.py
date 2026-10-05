import datetime as dt

from app.models import (
    Article,
    Artist,
    ArtistMention,
    Source,
    SourceCategorie,
    SourceType,
)
from app.pipeline.artists import (
    est_citation_principale,
    record_mentions,
    rising_artists,
)
from app.pipeline.retention import purge_old_articles

UTC = dt.timezone.utc


def _src(db, name="Feed", genre="electronic"):
    src = Source(name=name, type=SourceType.RSS, url=f"https://{name}.test/f", genre=genre)
    db.add(src)
    db.commit()
    return src


def _scored(db, src, *, n, artists, emergence=60, relevance=50, days_ago=0):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.test/{n}",
        title=f"Titre {n}",
        raw_hash=f"{n:064d}",
        relevance_score=relevance,
        emergence_score=emergence,
        mentioned_artists=artists,
        published_at=dt.datetime.now(UTC) - dt.timedelta(days=days_ago),
    )
    db.add(art)
    db.commit()
    return art


def test_creates_artist_and_mention(db_session):
    src = _src(db_session)
    article = _scored(db_session, src, n=1, artists=["Peggy Gou"])

    assert record_mentions(db_session) == 1
    artist = db_session.query(Artist).one()
    assert artist.name == "Peggy Gou"
    assert artist.mentions_count == 1
    mention = db_session.query(ArtistMention).one()
    assert mention.artist_id == artist.id
    assert mention.article_id == article.id
    assert mention.emergence_score == 60


def test_same_artist_across_articles_is_one_row(db_session):
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Peggy Gou"], days_ago=5)
    _scored(db_session, src, n=2, artists=["peggy gou"])  # casse différente

    assert record_mentions(db_session) == 2
    artist = db_session.query(Artist).one()
    assert artist.mentions_count == 2
    # la première apparition est conservée, la dernière est mise à jour
    assert artist.first_seen_at < artist.last_seen_at


def test_keeps_the_highest_emergence_seen(db_session):
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Nouveau Venu"], emergence=40)
    _scored(db_session, src, n=2, artists=["Nouveau Venu"], emergence=85)

    record_mentions(db_session)
    assert db_session.query(Artist).one().max_emergence == 85


def test_is_idempotent(db_session):
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Peggy Gou"])

    assert record_mentions(db_session) == 1
    assert record_mentions(db_session) == 0
    assert db_session.query(Artist).one().mentions_count == 1


def test_ignores_unscored_articles(db_session):
    src = _src(db_session)
    art = _scored(db_session, src, n=1, artists=["Peggy Gou"])
    art.relevance_score = None
    art.emergence_score = None
    db_session.commit()

    assert record_mentions(db_session) == 0


def test_mentions_survive_the_purge(db_session):
    """Le point de tout l'exercice : l'article est éphémère, l'historique de
    l'artiste est l'actif du label et ne doit jamais être purgé."""
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Peggy Gou"], days_ago=60)
    record_mentions(db_session)

    purge_old_articles(db_session, retention_days=14)

    assert db_session.query(Article).count() == 0
    assert db_session.query(Artist).one().name == "Peggy Gou"
    assert db_session.query(ArtistMention).count() == 1


def test_rising_artists_ne_regarde_que_la_fenetre(db_session):
    src = _src(db_session)
    for n in range(1, 4):
        _scored(db_session, src, n=n, artists=["Montant"], emergence=70, days_ago=n)
    for n in range(4, 7):
        _scored(db_session, src, n=n, artists=["Ancien"], emergence=80, days_ago=90)
    record_mentions(db_session)

    names = [s.artist.name for s in rising_artists(db_session, window_days=30)]
    assert names == ["Montant"]
    assert "Ancien" not in names  # hors fenêtre


def test_rising_artists_can_filter_on_emergence(db_session):
    src = _src(db_session)
    for n in range(1, 4):
        _scored(db_session, src, n=n, artists=["Jeune Pousse"], emergence=80, days_ago=n)
    for n in range(4, 7):
        _scored(db_session, src, n=n, artists=["Star Etablie"], emergence=5, days_ago=n)
    record_mentions(db_session)

    names = [s.artist.name for s in rising_artists(db_session, min_emergence=50)]
    assert names == ["Jeune Pousse"]


# --- Attribution : la note de l'article ne vaut pas pour tous ses cites -------


def _article_titre(db, src, *, n, titre, artists, emergence=75):
    art = Article(
        source_id=src.id,
        url=f"https://{src.name}.test/{n}",
        title=titre,
        raw_hash=f"{n:064d}",
        relevance_score=60,
        emergence_score=emergence,
        mentioned_artists=artists,
        published_at=dt.datetime.now(UTC) - dt.timedelta(hours=n),
    )
    db.add(art)
    db.commit()
    return art


def test_le_premier_cite_est_principal():
    assert est_citation_principale("Ahadadream", "Un titre sans personne", 0, ["Skrillex"]) is True


def test_un_cite_de_passage_absent_du_titre_est_secondaire():
    """Le cas mesure : « On debut album "P K I", Ahadadream makes a bold
    statement of self-love » notait Skrillex a 60 parce qu'il etait cite en
    second. Le titre nomme Ahadadream, pas Skrillex."""
    assert (
        est_citation_principale(
            "Skrillex",
            "On debut album, Ahadadream makes a bold statement",
            1,
            ["Ahadadream"],
        )
        is False
    )


def test_un_line_up_que_le_titre_ne_nomme_pas_reste_entier():
    """LE GARDE-FOU trouve en mesurant le plan de rattrapage, pas en
    raisonnant. « Les dates de nos artistes en octobre ! » cite 15 artistes de
    techno FR a emergence 65 -- la meilleure matiere A&R de la base. Le premier
    jet de la regle, qui ne regardait que le rang et le titre, en degradait 14."""
    line_up = [
        "anti._.logic",
        "cwtch______",
        "electricrescue",
        "kangding_ray",
        "madben_official",
    ]
    titre = "Les dates de nos artistes en octobre !"
    for rang, nom in enumerate(line_up):
        autres = [autre for i, autre in enumerate(line_up) if i != rang]
        assert est_citation_principale(nom, titre, rang, autres) is True, nom


def test_un_titre_qui_nomme_un_sujet_relegue_les_autres():
    """Le pendant du garde-fou : des que le titre nomme QUELQU'UN, les autres
    cites redeviennent des noms de passage."""
    assert est_citation_principale("Tyla", "How Shimza Pacha residency works", 1, ["Shimza"]) is False


def test_un_parrain_nomme_dans_le_titre_reste_principal():
    """Ce que l'attribution ne resout PAS, et qu'il faut savoir : « Dance
    Up-And-Comer of the Month: Tatyana Jane ... Gotten Lessons From Skrillex »
    laisse Skrillex a 85. C'est `exclure_suivis` qui l'ecarte, pas cette
    fonction -- les deux etages se completent."""
    assert (
        est_citation_principale(
            "Skrillex",
            "Dance Up-And-Comer of the Month: Tatyana Jane Has Gotten Lessons From Skrillex",
            1,
            ["Tatyana Jane"],
        )
        is True
    )


def test_le_titre_rattrape_un_sujet_nomme_en_dernier():
    """« Boiler Room: DC through the lens of our host gavc0re » : le modele
    liste le sujet en dernier, le titre le nomme."""
    assert (
        est_citation_principale(
            "gavc0re",
            "Boiler Room: DC through the lens of our host gavc0re",
            3,
            ["boysnoize", "skin0nskin", "dj_gigola"],
        )
        is True
    )


def test_le_titre_est_compare_sans_accents():
    """« Tiesto » dans la legende, « Tiësto » dans la liste : sans la
    normalisation de la dedup, le sujet passait pour un cite de passage."""
    assert (
        est_citation_principale("Tiësto", "Taking a look back at tiesto in Brussels", 1, ["cielll"])
        is True
    )


def test_la_citation_secondaire_ne_fait_pas_monter_le_cliquet(db_session):
    src = _src(db_session)
    _article_titre(
        db_session,
        src,
        n=1,
        titre="Dance Up-And-Comer of the Month: Tatyana Jane got lessons",
        artists=["Tatyana Jane", "Skrillex"],
        emergence=85,
    )
    record_mentions(db_session)

    par_nom = {a.name: a for a in db_session.query(Artist)}
    assert par_nom["Tatyana Jane"].max_emergence == 85
    # Le parrain garde sa citation datee -- l'archive reste complete -- mais pas
    # la note qui ferait de lui une jeune pousse.
    assert par_nom["Skrillex"].max_emergence is None
    citations = {m.title: m for m in db_session.query(ArtistMention)}
    assert len(citations) == 1
    assert db_session.query(ArtistMention).count() == 2


def test_le_rang_est_persiste_a_l_enregistrement(db_session):
    """Le rang n'existe que sur l'article, que la purge efface a 2 jours : il
    doit etre copie au moment de l'enregistrement, comme le titre et les notes."""
    src = _src(db_session)
    _article_titre(
        db_session, src, n=1, titre="Le Sujet revient", artists=["Sujet", "De Passage"]
    )
    record_mentions(db_session)
    purge_old_articles(db_session, retention_days=0)

    par_artiste = {
        m.artist.name: m.principal for m in db_session.query(ArtistMention).all()
    }
    assert par_artiste == {"Sujet": True, "De Passage": False}


# --- Classement : « on commence a en parler », pas « on en parle beaucoup » ---


def test_une_star_installee_ne_remonte_pas_en_tete(db_session):
    """LA PROPRIETE. C'est l'erreur exacte qu'on corrige, et elle est
    silencieuse : une liste plausible mais fausse est pire qu'une liste vide.

    La star est citee DIX fois a emergence 1 (ce que le feed fait d'une
    megastar) ; la jeune pousse TROIS fois a 70. Le classement par VOLUME --
    celui d'avant -- mettait la star en tete."""
    src = _src(db_session)
    for n in range(10):
        _scored(db_session, src, n=n, artists=["Taylor Swift"], emergence=1, days_ago=n % 5)
    for n in range(10, 13):
        _scored(db_session, src, n=n, artists=["Jeune Pousse"], emergence=70, days_ago=n % 3)
    record_mentions(db_session)

    noms = [s.artist.name for s in rising_artists(db_session)]
    assert noms[0] == "Jeune Pousse"
    assert noms.index("Jeune Pousse") < noms.index("Taylor Swift")


def test_une_citation_unique_ne_fait_pas_une_courbe(db_session):
    """Mesure du 2026-10-03 : sans plancher, les huit premiers sont huit
    artistes cites une seule fois, tous a 75 ex aequo."""
    src = _src(db_session)
    _scored(db_session, src, n=1, artists=["Un Seul Coup"], emergence=75)
    for n in range(2, 5):
        _scored(db_session, src, n=n, artists=["Trois Fois"], emergence=50, days_ago=n)
    record_mentions(db_session)

    noms = [s.artist.name for s in rising_artists(db_session)]
    assert noms == ["Trois Fois"]


def test_un_artiste_deja_suivi_sort_de_la_shortlist(db_session):
    """Kinda suit deja son compte : la question « merite-t-il d'etre suivi ? »
    est deja repondue. `inclure_suivis` le ramene pour la supervision."""
    src = _src(db_session)
    compte = Source(
        name="Folamour (IG)",
        type=SourceType.INSTAGRAM,
        url="ig:folamour",
        categorie=SourceCategorie.ARTISTE,
    )
    db_session.add(compte)
    db_session.commit()
    for n in range(1, 4):
        _scored(db_session, src, n=n, artists=["Folamour"], emergence=60, days_ago=n)
    record_mentions(db_session)
    db_session.query(Artist).filter(Artist.name == "Folamour").one().source_instagram_id = compte.id
    db_session.commit()

    assert rising_artists(db_session) == []
    garde = rising_artists(db_session, exclure_suivis=False)
    assert [s.artist.name for s in garde] == ["Folamour"]
    assert garde[0].deja_suivi is True


def test_le_signal_porte_l_acceleration_de_event_features(db_session):
    """On reutilise les formules deja mesurees de la couche evenements plutot
    que d'en reinventer une."""
    src = _src(db_session)
    for n, jours in enumerate((10, 1, 0), start=1):
        _scored(db_session, src, n=n, artists=["Qui Monte"], emergence=55, days_ago=jours)
    record_mentions(db_session)

    signal = rising_artists(db_session)[0]
    assert signal.citations == 3
    assert signal.citations_principales == 3
    assert signal.emergence_moyenne == 55.0
    assert signal.velocite is not None and signal.velocite > 0
    assert signal.acceleration is not None and signal.acceleration > 0


def test_une_citation_inconnue_vaut_principale(db_session):
    """Les 13 310 citations archivees avant la migration 0020 portent
    `principal = NULL` : elles doivent continuer de compter."""
    src = _src(db_session)
    artist = Artist(
        name="Archive",
        slug="archive",
        first_seen_at=dt.datetime.now(UTC) - dt.timedelta(days=3),
        last_seen_at=dt.datetime.now(UTC),
        mentions_count=3,
        max_emergence=60,
    )
    db_session.add(artist)
    db_session.commit()
    for n in range(3):
        db_session.add(
            ArtistMention(
                artist_id=artist.id,
                article_id=None,
                source_id=src.id,
                mentioned_at=dt.datetime.now(UTC) - dt.timedelta(days=n),
                relevance_score=50,
                emergence_score=60,
                title=f"Vieille citation {n}",
                url=f"https://old.test/{n}",
                principal=None,
            )
        )
    db_session.commit()

    signal = rising_artists(db_session)[0]
    assert signal.citations_principales == 3
    assert signal.emergence_moyenne == 60.0
