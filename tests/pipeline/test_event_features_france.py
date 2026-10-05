"""Pertinence France (Phase 2C, Task 3) -- le signal deterministe et gratuit.

Les cas verrouilles ici sont, pour la plupart, des TITRES REELS de la base de
production (2026-09-30), pas des exemples inventes : c'est sur eux que le
signal a ete mesure, c'est sur eux qu'il doit rester vrai.
"""

import ast
import datetime as dt
import pathlib

import app
from app.pipeline.event_features import (
    FRANCE_MARQUEUR_TITRE,
    FRANCE_SOURCE_PRIMAIRE,
    calculer_features,
    marqueurs_france,
    pertinence_france,
    source_primaire_francaise,
)

UTC = dt.timezone.utc


class TestMarqueurDeTitre:
    def test_une_ville_francaise_suffit(self):
        assert pertinence_france(titre="ARTMS Announces Cancellation Of Lille Concert") == 100.0

    def test_un_festival_francais_meme_ecrit_en_anglais(self):
        assert marqueurs_france("SLS Paris 2026 takes over Rex Club") == frozenset(
            {"paris", "rex club"}
        )

    def test_le_marqueur_survit_a_la_casse_aux_accents_et_au_hashtag(self):
        """« #RexClub » n'a pas d'espace, « FRANCE » est en capitales et
        « Châtelet » porte un accent -- trois formes reelles du feed."""
        assert marqueurs_france("Cette semaine au #RexClub:") == frozenset({"rexclub"})
        assert "france" in marqueurs_france("FAIRE TREMBLER L'EST DE LA FRANCE")
        assert "chatelet" in marqueurs_france("au Théâtre du Châtelet ce week-end !")

    def test_un_artiste_francais_a_l_etranger_reste_de_l_actu_francaise(self):
        """Mesure : « David Guetta announces biggest London show » est le cas
        le plus discutable des 44 classes, et il est volontairement garde --
        un artiste francais EST de la scene FR, ou qu'il joue."""
        assert pertinence_france(titre="David Guetta announces biggest London show") == 100.0

    def test_les_mots_anglais_courants_ne_sont_pas_des_marqueurs(self):
        """Le prototype perdait six evenements sur « air » (« fair use »,
        « Ca a l'air ») et « justice » est absent du lexique de titre pour la
        meme raison -- il n'est couvert que par le nom de compte.

        « La Discotheque » est un promoteur BRITANNIQUE : un nom a
        consonance francaise n'est pas un marqueur.
        """
        for titre in (
            "US appeals court rejects 'fair use' defense over AI training",
            "Trump's DoJ was biased and wrong about 'fair use'",
            "UK promoter La Discotheque lands eight-figure investment fund",
            "Nancy Pelosi announces retirement",
            "Glastonbury ticket price to increase by nearly 10%",
        ):
            assert marqueurs_france(titre) == frozenset(), titre


class TestLesLieuxNeSontPasQueDesMetropoles:
    """Le defaut du 2026-10-04, et sa mesure.

    Post 164 publie le 03/10 : « Les forfaits 7 jours pour Tomorrowland Winter
    sont en vente ». Le festival se tient a l'Alpe d'Huez, en Isere -- la
    legende le dit, l'affiche publiee en slide l'ecrit en capitales -- et
    `marqueurs_france` rendait l'ensemble VIDE. Le lexique connaissait des
    artistes et neuf METROPOLES, pas les stations, les salles ni les festivals.
    """

    def test_le_cas_qui_a_declenche_la_correction(self):
        """Les deux textes REELS du post 164, tels qu'ils sont en base."""
        assert "tomorrowland winter" in marqueurs_france(
            "Les forfaits 7 jours pour Tomorrowland Winter sont en vente"
        )
        assert "alpe d'huez" in marqueurs_france(
            "Alpe d'Huez attend déjà Tomorrowland Winter 2026"
        )

    def test_tomorrowland_nu_reste_belge(self):
        """La marque entiere nomme un EVENEMENT qui se tient en France a chacune
        de ses editions ; « Tomorrowland » seul se tient a Boom, en Belgique.
        C'est la recherche du nom COMPLET qui fait la difference -- si quelqu'un
        raccourcit l'entree, ce test rougit."""
        assert marqueurs_france("Tomorrowland dévoile sa programmation 2027") == frozenset()

    def test_un_festival_francais_hors_des_metropoles(self):
        """Texte reel du post 17 : le Delta Festival est a Marseille, mais
        l'accroche ne le dit pas -- seul son NOM la porte."""
        assert "delta festival" in marqueurs_france(
            "Le Delta Festival ne reviendra pas, liquidation judiciaire prononcée"
        )

    def test_les_faux_positifs_mesures_restent_dehors(self):
        """CHAQUE texte ci-dessous est reel et vient de la base : c'est sur eux
        qu'un candidat a ete essaye puis REJETE. Le precedent de « air » est
        dans le module pour ca -- un detecteur qui crie France partout rendrait
        la mesure de derive inutilisable, ce qui est sa seule fonction.
        """
        for titre in (
            # « angele » -- Angele est belge, et le mot est dans « Los Angeles ».
            "John Summit garde le plus gros show de sa tournée pour Los Angeles",
            # « rone »
            "le Symphony of Unity capté à Vérone",
            "Police in Ibiza are using drones to find illegal villa parties",
            "[Fresh] Drone - What Comes Next (2026)",
            # « crecy » nu
            "Secrecy Announce Debut EP Winter In A&E: Hear “Knife In My Dreams”",
            # « concrete »
            "Stef Chura, “Dancing Alone on the Concrete”",
            # « jacques » -- Jacques Brel est belge
            "Ycare annonce un album hommage à Jacques Brel et une tournée de 50 dates",
            # « francophone » -- un pays francophone n'est pas la France
            "Charlotte de Witte et KI/KI : rendez-vous en Belgique francophone",
            # « air » -- deja retire par le prototype, la base le redit
            "AIR (Part 3 of 3) est enfin disponible, la trilogie de Bicep est bouclée",
            # « bout du monde », « les escales », « le grand mix », « bon entendeur »,
            # « busy p », « petit bain » : de vrais noms de lieux FR, ecartes parce
            # qu'ils se lisent aussi comme des tournures courantes.
            "Un set au bout du monde pour clôturer la tournée",
            "Les escales de la tournée sont déjà complètes",
            "Le grand mix de l'été est en ligne",
            "À bon entendeur, le line-up tombe demain",
            "The label has been busy promoting its new signing",
        ):
            assert marqueurs_france(titre) == frozenset(), titre

    def test_un_nom_a_trait_d_union_est_cherche_en_sous_chaine(self):
        """`_mots` coupe sur le trait d'union : « saint-etienne » ne peut donc
        pas vivre dans `LIEUX_FRANCE`, qui compare des mots entiers. Ce test
        existe pour que la prochaine addition ne se perde pas en silence."""
        assert "saint-etienne" in marqueurs_france("Un festival techno à Saint-Étienne")
        assert "guy-man" in marqueurs_france("Guy-Man (ex-Daft Punk) signe pour GTA VI")

    def test_chaque_marqueur_rendu_est_bien_present_dans_le_texte(self):
        """L'invariant qui rend la mesure relisible : l'ensemble rendu nomme des
        marqueurs qu'on peut RETROUVER dans le texte -- c'est ce qui permet de
        voir le faux positif, et c'est pour ca qu'on ne rend pas un booleen."""
        texte = "Les forfaits pour Tomorrowland Winter à l'Alpe d'Huez, en Isère"
        marqueurs = marqueurs_france(texte)
        assert marqueurs
        normalise = texte.lower().replace("è", "e").replace("à", "a")
        for m in marqueurs:
            assert m in normalise, m


class TestSourcePrimaire:
    def test_un_compte_primaire_francais_sans_marqueur_de_titre(self):
        """« Victory live! » ne dit rien ; le compte qui le poste, si."""
        assert (
            pertinence_france(titre="Victory live!", noms_sources_primaires=["Madeon (FR) (IG)"])
            == 70.0
        )

    def test_le_suffixe_de_canal_tombe_mais_pas_le_suffixe_de_pays(self):
        assert source_primaire_francaise(["Astropolis (IG)"])
        assert source_primaire_francaise(["vladimir cauchemar (FR)"])
        assert not source_primaire_francaise(["Madeon"])

    def test_aucun_media_dans_la_liste_des_comptes_francais(self):
        """LE piege du depot : Tsugi, Guettapen, Konbini, Booska-P et FIP sont
        des medias FRANCAIS qui couvrent l'international. Leur nom ne dit rien
        du pays du sujet, donc il ne classe rien."""
        for media in ("Tsugi", "Tsugi (IG)", "Guettapen (IG)", "Konbini (IG)",
                      "Booska-P (IG)", "FIP (IG)", "Electro News (IG)"):
            assert not source_primaire_francaise([media]), media

    def test_un_marqueur_de_titre_prime_sur_le_compte(self):
        assert (
            pertinence_france(
                titre="Nuits Sonores annonce sa programmation",
                noms_sources_primaires=["Madeon (FR) (IG)"],
            )
            == FRANCE_MARQUEUR_TITRE
        )
        assert FRANCE_MARQUEUR_TITRE > FRANCE_SOURCE_PRIMAIRE


class TestLaLangueNEstPasUnSignal:
    """La mesure a tranche : un titre en francais dit QUI RACONTE, pas DE QUI
    ON PARLE. Ces cinq titres sont reels, tous ecrits en francais, tous d'un
    sujet ouvertement etranger -- ils doivent valoir 0, jamais un lot de
    consolation. Si quelqu'un rajoute un jour un etage « langue », ce test
    rougit, et c'est le but.
    """

    def test_le_francais_sur_un_sujet_etranger_ne_vaut_rien(self):
        for titre in (
            "LA BELGE A REGALE SA VILLE NATALE",
            "QUAND HYSTA EMMENE LE HARDCORE EN CROATIE",
            "DJ et producteur argentin, francorossi est connu pour sa techno profonde",
            "LE NEERLANDAIS SORT UNE NOUVELLE FOIS DE SA ZONE DE CONFORT",
            "La DJ et productrice belge Charlotte de Witte a livre un set gratuit a Gand",
        ):
            assert pertinence_france(titre=titre) == 0.0, titre


class TestZeroEtNone:
    def test_zero_veut_dire_mesure_sans_marqueur(self):
        assert pertinence_france(titre="Boys Noize Announces First Album in Five Years") == 0.0

    def test_none_quand_il_n_y_a_rien_a_lire(self):
        """Ni mot exploitable dans le titre, ni compte primaire connu : on n'a
        rien mesure. Rendre 0 ferait lire « regarde, pas francais »."""
        assert pertinence_france(titre="") is None
        assert pertinence_france(titre="   🎧🔥  ") is None
        assert pertinence_france(titre="", noms_sources_primaires=[None, ""]) is None

    def test_un_compte_connu_suffit_a_mesurer_un_titre_muet(self):
        assert pertinence_france(titre=".", noms_sources_primaires=["Laurent Garnier (IG)"]) == 70.0
        assert pertinence_france(titre=".", noms_sources_primaires=["Drumcode (IG)"]) == 0.0


class TestIntegrationDansLesFeatures:
    def test_calculer_features_porte_la_note(self):
        maintenant = dt.datetime(2026, 9, 30, 12, tzinfo=UTC)
        f = calculer_features(
            first_seen_at=maintenant,
            maintenant=maintenant,
            source_count=1,
            platform_count=1,
            independent_source_count=1,
            titre="Les Trans Musicales devoilent leur programmation",
        )
        assert f.pertinence_france == 100.0

    def test_sans_titre_ni_compte_la_feature_reste_none(self):
        """Un appelant de la Phase 2A qui ne fournit rien obtient exactement
        les features de la Phase 2A -- jamais un zero fabrique."""
        maintenant = dt.datetime(2026, 9, 30, 12, tzinfo=UTC)
        f = calculer_features(
            first_seen_at=maintenant,
            maintenant=maintenant,
            source_count=1,
            platform_count=1,
            independent_source_count=1,
        )
        assert f.pertinence_france is None
        assert f.velocity is None and f.acceleration is None and f.nouveaute is None


def test_le_module_de_features_reste_pur():
    """Global Constraint : `event_features` n'importe ni base, ni config, ni
    modeles -- et la Task 3 a ajoute `re`/`unicodedata`, qui n'y changent
    rien. Verifie sur les IMPORTS : c'est ce qui fait echouer un test plutot
    que passer inapercu le jour ou quelqu'un y lira une source."""
    chemin = pathlib.Path(app.__file__).parent / "pipeline" / "event_features.py"
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    modules = {n.module for n in ast.walk(arbre) if isinstance(n, ast.ImportFrom) and n.module}
    modules |= {a.name for n in ast.walk(arbre) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m.startswith("app") or m.startswith("sqlalchemy") for m in modules), modules


def test_aucun_module_de_publication_ne_lit_les_features_2c():
    """« Rien ne change dans le comportement de PUBLICATION » : 2C calcule et
    expose, c'est 2D qui classera.

    Verifie sur les imports plutot que sur le comportement, comme la
    sentinelle de `tests/pipeline/test_quota_graph.py` : le jour ou un
    `from app.pipeline.event_features import ...` apparait dans
    `app/publish` ou `app/schedule`, ce test rougit.
    """
    racine = pathlib.Path(app.__file__).parent
    for chemin in list((racine / "publish").rglob("*.py")) + list(
        (racine / "schedule").rglob("*.py")
    ):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        modules = {n.module for n in ast.walk(arbre) if isinstance(n, ast.ImportFrom) and n.module}
        modules |= {a.name for n in ast.walk(arbre) if isinstance(n, ast.Import) for a in n.names}
        assert not any(
            "event_features" in m or "event_archive" in m for m in modules
        ), chemin
