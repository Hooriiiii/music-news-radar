"""L'arbitrage Claude elargi aux paires FRAICHES 0.30-0.65 (Phase 2B, Task 3).

Le seuil de 0.65 lie sans rien demander ; sous 0.30, la mesure du 2026-09-24
ne trouve aucun vrai positif (0 sur 18 paires). Entre les deux, ni un seuil ni
un autre ne separe les vrais rapprochements des faux -- la courbe est PLATE
entre 0.33 et 0.57 -- donc c'est le SENS qui tranche, avec la machinerie qui
existe deja (cache par paire, verdicts manuels prioritaires, plafonds,
coupe-circuit).

`db_session.autoflush = False` dans chaque test du perimetre base : c'est le
reglage de PRODUCTION (`app/db.py`), et un test en autoflush par defaut a deja
masque deux fois un defaut de production dans ce depot.
"""

import datetime as dt

import pytest

from app.config import settings
from app.models import Article, Source, SourceType
from app.pipeline.cluster import (
    RAISON_DOUBLON_PREFIXE,
    est_gabarit_de_source,
    link_duplicates,
    significant_tokens,
)
from app.pipeline.cluster_arbitrage import EntreeArbitrage, cle_paire, ecrire_cache, Cache
from app.pipeline.cluster_arbitrage import marquer_manuel
from app.pipeline.retention import purge_old_articles

UTC = dt.timezone.utc

# Deux titres a 0.33 de recouvrement : le rate purement SEMANTIQUE mesure le
# 2026-09-24 (une annonce et le compte a rebours qui la precede).
SEMANTIQUE_A = "Taylor Swift devoile un compte a rebours sur son site officiel"
SEMANTIQUE_B = "Taylor Swift annonce le single Patient Zero"

# Un GABARIT de source : la meme phrase, un laureat different a chaque fois
# (0.56 de recouvrement deux a deux -- en plein dans la bande).
GABARIT = [
    "Le laureat du prix Revelation Scene aux Trophees Independants 2026 est Maxime Duverger",
    "Le laureat du prix Album Studio aux Trophees Independants 2026 est Clarisse Fontaine",
    "Le laureat du prix Concert Annee aux Trophees Independants 2026 est Bastien Morel",
]

# Deux articles d'une MEME source qui couvrent vraiment la meme actu (0.60) :
# le cas que le remede naif (« ecarter les paires de meme source ») tuerait.
MEME_SOURCE_A = "Holly Humberstone annule sa tournee europeenne"
MEME_SOURCE_B = "Holly Humberstone reporte sa tournee allemande"
MEME_SOURCE_AUTRE = "Bret Michaels repousse ses dates americaines"


def _source(db, nom, url):
    source = Source(name=nom, type=SourceType.RSS, url=url)
    db.add(source)
    db.commit()
    return source


def _articles(db, source, titres, depart_heures=6):
    """Des articles frais, dates sur l'heure REELLE : `link_duplicates` lit
    `dt.datetime.now`, jamais une date figee -- une fenetre calee sur une
    constante ne serait jamais examinee (piege deja paye au tour 2/5 de la
    Task 7)."""
    maintenant = dt.datetime.now(UTC)
    crees = []
    for rang, titre in enumerate(titres):
        article = Article(
            source_id=source.id,
            url=f"https://exemple/{source.id}/{rang}",
            title=titre,
            raw_hash=f"{source.id}-{rang}",
            published_at=maintenant - dt.timedelta(hours=depart_heures - rang),
        )
        db.add(article)
        crees.append(article)
    db.commit()
    return crees


class _Arbitre:
    """Un arbitre injecte : enregistre les paires vues, rend un verdict fixe."""

    def __init__(self, verdict):
        self.verdict = verdict
        self.appels = []

    def __call__(self, publie, nouveau):
        self.appels.append(cle_paire(publie.id, nouveau.id))
        return self.verdict


# -- le critere anti-gabarit, isole ----------------------------------------


def test_le_gabarit_est_reconnu_quand_les_mots_partages_reviennent_ailleurs():
    jetons = [significant_tokens(titre) for titre in GABARIT]
    partage = jetons[0] & jetons[1]
    assert est_gabarit_de_source(partage, jetons, 3) is True


def test_une_vraie_reprise_de_la_meme_source_n_est_pas_un_gabarit():
    """Le piege MESURE : filtrer « meme source des deux cotes » tuerait 3 des
    5 vrais rates. Ici les deux articles viennent de la meme source, mais les
    mots qui les rapprochent ne se retrouvent nulle part ailleurs chez elle."""
    jetons = [
        significant_tokens(MEME_SOURCE_A),
        significant_tokens(MEME_SOURCE_B),
        significant_tokens(MEME_SOURCE_AUTRE),
    ]
    partage = jetons[0] & jetons[1]
    assert est_gabarit_de_source(partage, jetons, 3) is False


def test_un_partage_vide_n_est_jamais_un_gabarit():
    """Ceinture : `set() <= n_importe_quoi` est VRAI, donc un partage vide
    ferait compter TOUS les articles de la source et bloquerait tout."""
    assert est_gabarit_de_source(set(), [{"a"}, {"b"}, {"c"}], 3) is False


# -- la bande fraiche : le lien, sa trace, son compteur ---------------------


def test_une_paire_fraiche_de_la_bande_est_liee_quand_claude_dit_meme_actu(
    db_session, tmp_path
):
    db_session.autoflush = False
    gauche = _source(db_session, "media-a", "https://a/feed")
    droite = _source(db_session, "media-b", "https://b/feed")
    premier, = _articles(db_session, gauche, [SEMANTIQUE_A])
    second, = _articles(db_session, droite, [SEMANTIQUE_B], depart_heures=3)

    arbitre = _Arbitre((True, "le compte a rebours annoncait ce single"))
    stats = link_duplicates(
        db_session,
        arbitre=arbitre,
        cache_chemin=tmp_path / "arbitrage.json",
        arbitrage_frais_min=0.30,
    )

    assert arbitre.appels == [cle_paire(premier.id, second.id)]
    assert stats.frais_arbitres == 1
    assert stats.frais == 1
    assert stats.archive == 0
    db_session.refresh(second)
    assert second.duplicate_of_id == premier.id
    # AUCUNE trace : voir le test suivant, qui dit pourquoi.
    assert second.file_exclu_raison is None


def test_un_lien_frais_ne_survit_pas_a_la_purge_de_son_representant(
    db_session, tmp_path
):
    """Tour de correction 1 : un lien FRAIS doit rester AUTO-REPARANT.

    `articles.duplicate_of_id` est `ON DELETE SET NULL` et la purge n'epargne
    que les `USED` : le representant d'un lien frais disparait a deux jours et
    le doublon lui survit, delie. Si le lien lui avait pose un
    `file_exclu_raison`, il le garderait, et `store._articles_publiables`
    (qui exige `file_exclu_raison IS NULL`) le tiendrait hors de la file POUR
    TOUJOURS -- au nom d'un doublon d'un article qui n'existe plus. Avant la
    Phase 2B, aucune trace n'etait posee sur un lien frais et la purge le
    reparait toute seule : ce test verrouille cette propriete.

    C'est le test de la preuve par mutation du tour 1 : reposer la trace le
    fait echouer."""
    db_session.autoflush = False
    gauche = _source(db_session, "media-a", "https://a/feed")
    droite = _source(db_session, "media-b", "https://b/feed")
    premier, = _articles(db_session, gauche, [SEMANTIQUE_A])
    second, = _articles(db_session, droite, [SEMANTIQUE_B], depart_heures=3)

    arbitre = _Arbitre((True, "meme actu"))
    link_duplicates(
        db_session,
        arbitre=arbitre,
        cache_chemin=tmp_path / "arbitrage.json",
        arbitrage_frais_min=0.30,
    )
    db_session.refresh(second)
    assert second.duplicate_of_id == premier.id

    # La purge emporte le representant (frais, jamais publie) ; Postgres remet
    # `duplicate_of_id` a NULL (`ON DELETE SET NULL`), joue ici a la main --
    # SQLite n'applique pas les actions referentielles sans PRAGMA.
    premier.published_at = dt.datetime.now(UTC) - dt.timedelta(days=10)
    db_session.commit()
    id_second = second.id
    # `expunge_all` avant la purge : elle fait un DELETE en masse dont la
    # synchronisation de session reevalue le predicat EN PYTHON, sur des dates
    # que SQLite rend naives -- rien a voir avec la propriete testee.
    db_session.expunge_all()
    assert purge_old_articles(db_session, retention_days=2) == 1
    second = db_session.get(Article, id_second)
    second.duplicate_of_id = None
    db_session.commit()

    assert second.file_exclu_raison is None  # redevenu publiable, tout seul

def test_un_verdict_different_ne_pose_aucun_lien_et_n_est_jamais_redemande(
    db_session, tmp_path
):
    db_session.autoflush = False
    chemin = tmp_path / "arbitrage.json"
    gauche = _source(db_session, "media-a", "https://a/feed")
    droite = _source(db_session, "media-b", "https://b/feed")
    _articles(db_session, gauche, [SEMANTIQUE_A])
    second, = _articles(db_session, droite, [SEMANTIQUE_B], depart_heures=3)

    arbitre = _Arbitre((False, "deux moments distincts"))
    link_duplicates(
        db_session, arbitre=arbitre, cache_chemin=chemin, arbitrage_frais_min=0.30
    )
    db_session.refresh(second)
    assert second.duplicate_of_id is None
    assert len(arbitre.appels) == 1

    # Deuxieme passage : le verdict memorise fait foi, aucun appel de plus.
    second_arbitre = _Arbitre((True, "ne doit jamais etre appele"))
    link_duplicates(
        db_session, arbitre=second_arbitre, cache_chemin=chemin, arbitrage_frais_min=0.30
    )
    assert second_arbitre.appels == []
    db_session.refresh(second)
    assert second.duplicate_of_id is None


def test_un_verdict_manuel_different_prime_et_ne_coute_aucun_appel(db_session, tmp_path):
    db_session.autoflush = False
    chemin = tmp_path / "arbitrage.json"
    gauche = _source(db_session, "media-a", "https://a/feed")
    droite = _source(db_session, "media-b", "https://b/feed")
    premier, = _articles(db_session, gauche, [SEMANTIQUE_A])
    second, = _articles(db_session, droite, [SEMANTIQUE_B], depart_heures=3)
    marquer_manuel(premier.id, second.id, False, "l'utilisateur a tranche", chemin)

    arbitre = _Arbitre((True, "Claude dirait oui"))
    link_duplicates(
        db_session, arbitre=arbitre, cache_chemin=chemin, arbitrage_frais_min=0.30
    )

    assert arbitre.appels == []
    db_session.refresh(second)
    assert second.duplicate_of_id is None


def test_une_panne_d_arbitrage_ne_pose_jamais_de_lien(db_session, tmp_path):
    """Le doute favorise toujours la publication : une panne n'est pas un
    verdict, rien n'est memorise, rien n'est lie."""
    db_session.autoflush = False
    gauche = _source(db_session, "media-a", "https://a/feed")
    droite = _source(db_session, "media-b", "https://b/feed")
    _articles(db_session, gauche, [SEMANTIQUE_A])
    second, = _articles(db_session, droite, [SEMANTIQUE_B], depart_heures=3)

    arbitre = _Arbitre(None)
    stats = link_duplicates(
        db_session,
        arbitre=arbitre,
        cache_chemin=tmp_path / "arbitrage.json",
        arbitrage_frais_min=0.30,
    )

    assert stats.arbitrage_pannes == 1
    assert stats.frais_arbitres == 0
    db_session.refresh(second)
    assert second.duplicate_of_id is None


def test_sans_arbitre_la_bande_fraiche_ne_lie_rien(db_session, tmp_path):
    """Un verdict deja en cache ne suffit pas : sans arbitre on ne paie rien,
    mais on ne perd pas non plus le verdict deja rendu -- il lie."""
    db_session.autoflush = False
    chemin = tmp_path / "arbitrage.json"
    gauche = _source(db_session, "media-a", "https://a/feed")
    droite = _source(db_session, "media-b", "https://b/feed")
    premier, = _articles(db_session, gauche, [SEMANTIQUE_A])
    second, = _articles(db_session, droite, [SEMANTIQUE_B], depart_heures=3)
    ecrire_cache(
        Cache({cle_paire(premier.id, second.id): EntreeArbitrage(True, "meme actu")}),
        chemin,
    )

    stats = link_duplicates(
        db_session, cache_chemin=chemin, arbitrage_frais_min=0.30
    )

    assert stats.frais_arbitres == 1
    db_session.refresh(second)
    assert second.duplicate_of_id == premier.id


# -- l'anti-gabarit dans le vrai parcours ----------------------------------


def test_un_gabarit_de_source_est_ecarte_sans_aucun_appel(db_session, tmp_path):
    db_session.autoflush = False
    source = _source(db_session, "AIM UK (IG)", "ig:aimuk")
    articles = _articles(db_session, source, GABARIT)

    arbitre = _Arbitre((True, "ne doit jamais etre appele"))
    stats = link_duplicates(
        db_session,
        arbitre=arbitre,
        cache_chemin=tmp_path / "arbitrage.json",
        arbitrage_frais_min=0.30,
    )

    assert arbitre.appels == []
    assert stats.gabarits_ecartes == 3  # les trois paires du trio
    assert stats.frais == 0
    for article in articles:
        db_session.refresh(article)
        assert article.duplicate_of_id is None


def test_deux_articles_de_la_meme_source_sur_la_meme_actu_sont_bien_arbitres(
    db_session, tmp_path
):
    """Le remede naif est un piege mesure. Trois articles de la MEME source :
    les deux premiers couvrent la meme actu, le troisieme parle d'autre chose
    -- les mots partages ne se retrouvent donc pas ailleurs, et la paire est
    bien soumise a l'arbitrage."""
    db_session.autoflush = False
    source = _source(db_session, "Billboard (RSS)", "https://billboard/feed")
    premier, second, _autre = _articles(
        db_session, source, [MEME_SOURCE_A, MEME_SOURCE_B, MEME_SOURCE_AUTRE]
    )

    arbitre = _Arbitre((True, "la meme tournee annulee"))
    stats = link_duplicates(
        db_session,
        arbitre=arbitre,
        cache_chemin=tmp_path / "arbitrage.json",
        arbitrage_frais_min=0.30,
    )

    assert arbitre.appels == [cle_paire(premier.id, second.id)]
    assert stats.gabarits_ecartes == 0
    assert stats.frais_arbitres == 1


# -- les bornes de la bande -------------------------------------------------


def test_sous_le_plancher_aucune_paire_n_est_arbitree(db_session, tmp_path):
    db_session.autoflush = False
    gauche = _source(db_session, "media-a", "https://a/feed")
    droite = _source(db_session, "media-b", "https://b/feed")
    _articles(db_session, gauche, [SEMANTIQUE_A])
    _articles(db_session, droite, [SEMANTIQUE_B], depart_heures=3)

    arbitre = _Arbitre((True, "hors bande"))
    link_duplicates(
        db_session,
        arbitre=arbitre,
        cache_chemin=tmp_path / "arbitrage.json",
        arbitrage_frais_min=0.50,  # la paire est a 0.33
    )

    assert arbitre.appels == []


def test_le_plafond_par_passage_borne_les_appels(db_session, tmp_path):
    db_session.autoflush = False
    # Trois SOURCES distinctes : l'anti-gabarit ne s'applique pas, donc seul
    # le plafond peut expliquer qu'il ne reste qu'un appel.
    for rang, titre in enumerate(
        [
            SEMANTIQUE_A,
            SEMANTIQUE_B,
            "Taylor Swift devoile un extrait inedit avant sa sortie mondiale",
        ]
    ):
        source = _source(db_session, f"media-{rang}", f"https://m{rang}/feed")
        _articles(db_session, source, [titre], depart_heures=6 - rang)

    arbitre = _Arbitre((False, "non"))
    link_duplicates(
        db_session,
        arbitre=arbitre,
        cache_chemin=tmp_path / "arbitrage.json",
        arbitrage_frais_min=0.30,
        arbitrage_max=1,
    )

    assert len(arbitre.appels) == 1


# -- LE coupe-circuit : la contrainte globale du chantier -------------------


@pytest.mark.parametrize("jeu", [GABARIT, [SEMANTIQUE_A, SEMANTIQUE_B]])
def test_la_bande_fraiche_coupee_rend_exactement_les_decisions_d_avant(
    db_session, tmp_path, jeu
):
    """Global Constraint du plan : « un test doit prouver que le pipeline
    produit les memes decisions sur les memes donnees quand l'arbitrage
    elargi est coupe ». Coupe = `arbitrage_frais_min` a None, qui est le
    DEFAUT du parametre : aucun appel, aucun lien, aucun compteur -- et cela
    vaut meme si un verdict « meme actu » traine deja dans le cache."""
    db_session.autoflush = False
    chemin = tmp_path / "arbitrage.json"
    source = _source(db_session, "media-a", "https://a/feed")
    articles = _articles(db_session, source, jeu)
    ecrire_cache(
        Cache(
            {
                cle_paire(articles[0].id, articles[1].id): EntreeArbitrage(
                    True, "verdict deja rendu"
                )
            }
        ),
        chemin,
    )

    arbitre = _Arbitre((True, "ne doit jamais etre appele"))
    stats = link_duplicates(db_session, arbitre=arbitre, cache_chemin=chemin)

    assert arbitre.appels == []
    assert stats.frais == 0
    assert stats.frais_arbitres == 0
    assert stats.gabarits_ecartes == 0
    for article in articles:
        db_session.refresh(article)
        assert article.duplicate_of_id is None
        assert article.file_exclu_raison is None


def test_dedupliquer_avec_arbitrage_ouvre_la_bande_fraiche_selon_le_reglage(monkeypatch):
    """Le coupe-circuit de `.env` passe par un PARAMETRE, jamais par une
    devinette sur l'appelant -- meme principe que l'arbitrage d'archive."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.pipeline.run import dedupliquer_avec_arbitrage

    monkeypatch.setattr(settings, "cluster_arbitrage_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr("app.pipeline.run.make_claude_arbitre", lambda: (lambda a, b: None))
    monkeypatch.setattr(settings, "cluster_arbitrage_frais_seuil", 0.31)

    vus = []
    monkeypatch.setattr(
        "app.pipeline.run.link_duplicates",
        lambda db, **kwargs: vus.append(kwargs.get("arbitrage_frais_min")),
    )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        monkeypatch.setattr(settings, "cluster_arbitrage_frais_enabled", True)
        dedupliquer_avec_arbitrage(db)
        monkeypatch.setattr(settings, "cluster_arbitrage_frais_enabled", False)
        dedupliquer_avec_arbitrage(db)

    assert vus == [0.31, None]


def test_run_veille_n_arbitre_jamais_la_bande_fraiche(db_session, tmp_path):
    """`run_veille` passe toutes les 5 minutes : si elle ouvrait la bande
    fraiche, le cout serait multiplie par douze sans qu'aucune erreur ne le
    signale. Deux choses la protegent, et le test verifie les DEUX : son
    `cluster` par defaut est bien `link_duplicates` (jamais
    `dedupliquer_avec_arbitrage`, qui lui ouvrirait la bande), et
    `link_duplicates` appele comme elle l'appelle -- la session, rien d'autre
    -- ne touche jamais a la bande fraiche, meme si un arbitre traine."""
    import inspect

    from app.pipeline import veille

    defaut_cluster = inspect.signature(veille.run_veille).parameters["cluster"].default
    assert defaut_cluster is link_duplicates

    db_session.autoflush = False
    gauche = _source(db_session, "media-a", "https://a/feed")
    droite = _source(db_session, "media-b", "https://b/feed")
    _articles(db_session, gauche, [SEMANTIQUE_A])
    second, = _articles(db_session, droite, [SEMANTIQUE_B], depart_heures=3)

    # `cache_chemin` seulement pour rester hermetique (ne jamais lire le
    # cache reel de la machine) : il ne change rien a la demonstration.
    stats = defaut_cluster(db_session, cache_chemin=tmp_path / "arbitrage.json")

    assert stats.frais_arbitres == 0
    db_session.refresh(second)
    assert second.duplicate_of_id is None


# -- l'election ne garde pas une trace perimee ------------------------------


def test_un_doublon_promu_representant_perd_sa_trace_de_doublon(db_session, tmp_path):
    """La SEULE exception assumee a la contrainte « rien ne change dans le
    comportement de publication » (tour de correction 1, 2026-09-24).

    Le cas existe independamment de la bande fraiche, et meme avec
    `CLUSTER_ARBITRAGE_FRAIS_ENABLED=false` : `scripts.marquer_doublon_manuel
    --meme-actu` sur deux articles FRAIS passe par la branche « archive » de
    `appliquer` (l'origine `manuel` n'est ni `frais` ni `arbitre_frais`) et
    pose donc `RAISON_DOUBLON_PREFIXE` sur un doublon dont le representant
    n'est ni `USED` ni engage. L'election peut ensuite promouvoir ce doublon
    en representant : garder sa raison le sortirait de la file de publication
    pour un lien qui n'existe plus, exactement a l'envers. L'election efface
    donc cette raison quand elle delie l'elu -- et uniquement celle-la : une
    raison posee ailleurs (« gel impossible : ... ») reste."""
    db_session.autoflush = False
    from app.pipeline.comptes_artistes import construire_index
    from app.pipeline.election import elire_representants

    gauche = _source(db_session, "media-a", "https://a/feed")
    droite = _source(db_session, "media-b", "https://b/feed")
    # Le representant naturel (vu en premier) porte le titre le MOINS
    # informatif, pour que l'election promeuve l'autre.
    premier, = _articles(db_session, gauche, ["Taylor Swift teasing"])
    second, = _articles(
        db_session,
        droite,
        ["Taylor Swift teasing un compte a rebours avant Patient Zero"],
        depart_heures=3,
    )
    second.duplicate_of_id = premier.id
    second.file_exclu_raison = (
        f"{RAISON_DOUBLON_PREFIXE} (article {premier.id}, verdict manuel)"
    )
    db_session.commit()

    elire_representants(db_session, construire_index(db_session))

    db_session.refresh(second)
    assert second.duplicate_of_id is None
    assert second.file_exclu_raison is None


def test_une_raison_etrangere_a_la_dedup_survit_a_l_election(db_session):
    """L'autre moitie de la regle : l'election n'efface QUE la raison posee
    par la dedup. Un « gel impossible » dit une vraie raison de ne pas
    publier, sans rapport avec un doublon -- l'effacer remettrait dans la file
    un article que la publication avait ecarte."""
    db_session.autoflush = False
    from app.pipeline.comptes_artistes import construire_index
    from app.pipeline.election import elire_representants

    gauche = _source(db_session, "media-a", "https://a/feed")
    droite = _source(db_session, "media-b", "https://b/feed")
    premier, = _articles(db_session, gauche, ["Taylor Swift teasing"])
    second, = _articles(
        db_session,
        droite,
        ["Taylor Swift teasing un compte a rebours avant Patient Zero"],
        depart_heures=3,
    )
    second.duplicate_of_id = premier.id
    second.file_exclu_raison = "gel impossible : le post n'a pas de visuel publiable"
    db_session.commit()

    elire_representants(db_session, construire_index(db_session))

    db_session.refresh(second)
    assert second.duplicate_of_id is None
    assert second.file_exclu_raison == "gel impossible : le post n'a pas de visuel publiable"


# -- le prompt dit la verite sur l'article de reference ---------------------


def test_le_prompt_annonce_un_article_publie_seulement_s_il_l_est():
    """Tour de correction 1 : depuis que l'arbitrage s'applique aussi aux
    paires fraiches, la reference n'est plus forcement un article publie.
    L'en-tete le lit sur l'article (`ArticleStatus.USED`), et ne le recoit pas
    en parametre -- une source de verite, pas deux."""
    from app.models import ArticleStatus
    from app.pipeline.cluster_arbitrage import build_user_prompt

    reference = Article(
        id=1, source_id=1, url="https://a/1", title=SEMANTIQUE_A, raw_hash="1"
    )
    nouveau = Article(
        id=2, source_id=2, url="https://b/1", title=SEMANTIQUE_B, raw_hash="2"
    )

    frais = build_user_prompt(reference, nouveau)
    assert "DEJA PUBLIE" not in frais
    assert "pas encore publie" in frais

    reference.status = ArticleStatus.USED
    assert "DEJA PUBLIE PAR KINDA" in build_user_prompt(reference, nouveau)


def test_reformuler_le_prompt_n_invalide_aucun_verdict_memorise():
    """La cle d'un verdict est la PAIRE D'IDS, jamais le texte du prompt :
    changer la formulation ne desarme rien, et ne justifie donc jamais de
    toucher a `CACHE_VERSION` (qui, elle, desarmerait les verdicts manuels)."""
    from app.pipeline.cluster_arbitrage import CACHE_VERSION

    assert cle_paire(12, 40) == "12:40"
    assert CACHE_VERSION == 1
