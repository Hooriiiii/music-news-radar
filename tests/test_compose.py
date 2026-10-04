import datetime as dt

import pytest
from pydantic import ValidationError

from app.content.compose import (
    CAPTION_EMOJIS_MIN,
    SYSTEM_PROMPT,
    CompositionError,
    CoverVariante,
    PostDraft,
    appeler_compositeur,
    build_user_prompt,
    contient_emoji,
    create_post,
    make_claude_composer,
    probleme_accents,
    probleme_cloture_question,
    probleme_de_legende,
    probleme_de_variantes,
    probleme_emojis,
    probleme_enumeration_surlignee,
    resoudre_fond,
)
from app.content.choix_visuels import ChoixVisuels, VisuelIntrouvable, VisuelsDifferes
from app.content.illustration import Lot, Verdict
from app.content.media import entree
from app.content.visuels import (
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    ORIGINE_COMPTE,
    VisuelDiffere,
    actif,
)
from app.models import Article, Source, SourceType


def _article(db):
    src = Source(name="Tsugi", type=SourceType.RSS, url="https://tsugi.fr/feed")
    db.add(src)
    db.commit()
    article = Article(
        source_id=src.id,
        url="https://tsugi.fr/vieilles-charrues",
        title="Les Vieilles Charrues devoilent leur line-up electro",
        summary="Nico Moreno, Mosimann et Cassius sont annonces.",
        raw_hash="h1",
        published_at=dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc),
        media=[{"type": "photo", "url": "https://tsugi.fr/photo.jpg"}],
    )
    db.add(article)
    db.commit()
    return article


def _variante(n):
    return CoverVariante(
        headline=f"**Accroche {n}**", title=f"**Titre {n}**", subtitle=f"**Sous {n}**"
    )


def test_la_legende_refuse_le_balisage_de_surlignage():
    """`**` n'a de sens que dans les textes RENDUS, ou il devient un <mark> rose.

    La legende, elle, part telle quelle dans Instagram : un `**Sensational**` s'y
    affiche avec ses asterisques. Observe en vrai sur une composition Sonnet, d'ou
    ce rejet et pas seulement une consigne de prompt.
    """
    with pytest.raises(ValidationError):
        PostDraft(
            variantes=[_variante(n) for n in range(4)],
            caption="Triste nouvelle : **Sensational** nous quitte.",
            hashtags=["#rip"],
        )


def test_la_legende_accepte_un_asterisque_isole():
    """Un asterisque seul n'est pas du balisage -- ne pas sur-rejeter."""
    draft = PostDraft(
        variantes=[_variante(n) for n in range(4)],
        caption="Line-up complet * sous reserve de modification 🎧 et c'est signe ✨",
        hashtags=["#festival"],
    )
    assert "*" in draft.caption


def test_la_legende_sans_emoji_reste_un_brouillon_valide():
    """Le compte d'emojis n'est plus une regle de SCHEMA : `messages.parse()`
    levait sinon une ValidationError qui jetait tout le brouillon, et trois
    legendes sans emoji faisaient perdre la composition d'un creneau (production,
    2026-09-17). Une legende sans emoji vaut mieux que pas de post : le compte est
    verifie par `probleme_emojis`, que le compositeur utilise pour relancer."""
    draft = PostDraft(
        variantes=[_variante(n) for n in range(4)],
        caption="Une legende parfaitement correcte, mais sans le moindre emoji.",
        hashtags=["#festival"],
    )
    assert draft.caption.endswith("emoji.")


def test_probleme_emojis_decrit_une_legende_sans_emoji():
    probleme = probleme_emojis("Une legende sans le moindre emoji.")
    assert probleme is not None
    assert "0 emoji" in probleme
    assert "caption" in probleme
    assert f"{CAPTION_EMOJIS_MIN}" in probleme
    # Ne jamais pousser le modele a en mettre dans les textes rendus.
    assert "headline" in probleme and "jamais" in probleme


def test_probleme_emojis_decrit_une_avalanche_demojis():
    probleme = probleme_emojis("Trop 🩷 c'est 🎧 vraiment 🪩 beaucoup 🎪 trop 🌱 la 🔊 non ✨")
    assert probleme is not None
    assert "7 emoji" in probleme


def test_probleme_emojis_accepte_une_legende_dans_les_bornes():
    assert probleme_emojis("Line-up complet 🎧 et c'est signe ✨") is None


def test_une_variante_exige_les_trois_textes_balises():
    """Chaque variante porte l'accroche du post ET le couple titre/sous-titre du
    reel : c'est ce qui rend la bascule POST/REELS instantanee, sans redemander a
    Claude. Les trois passent par la meme regle de surlignage."""
    with pytest.raises(ValidationError):
        CoverVariante(headline="sans surlignage", title="**Titre**", subtitle="**Sous**")


def test_detection_demoji_ne_prend_pas_les_accents_ni_le_balisage():
    """La detection ne doit pas se declencher sur ce que les accroches contiennent
    deja legitimement : lettres accentuees, apostrophes, marqueurs `**`."""
    assert not contient_emoji("**Vieilles Charrues** : l'ete electro a Carhaix")
    assert not contient_emoji("Deja 20 000 festivaliers, c'est enorme !")
    assert not contient_emoji("")


def test_detection_demoji_repere_les_pictogrammes():
    for texte in ("Le line-up tombe 🎧", "🩷", "Ambiance 🪩 totale", "Bonjour ✨ Kinda"):
        assert contient_emoji(texte), texte


def test_une_variante_refuse_un_emoji_dans_les_textes_rendus():
    """`headline`, `title` et `subtitle` partent dans Chromium : un emoji y casse la
    direction artistique et fausse la mesure de largeur de ligne."""
    for champ in ("headline", "title", "subtitle"):
        textes = {"headline": "**Accroche**", "title": "**Titre**", "subtitle": "**Sous**"}
        textes[champ] = textes[champ] + " 🎧"
        with pytest.raises(ValidationError):
            CoverVariante(**textes)


def test_une_variante_accepte_accents_et_balisage():
    """Le garde-fou emoji ne doit pas rejeter une variante parfaitement normale."""
    variante = CoverVariante(
        headline="**Vieilles Charrues** : le line-up electro devoile a Carhaix",
        title="**Vieilles Charrues**",
        subtitle="l'ete electro demarre **ce week-end**",
    )
    assert variante.title == "**Vieilles Charrues**"


def test_le_prompt_porte_la_cloture_conditionnelle_et_lanti_contradiction():
    """La regle 4 disait « termine par une question ou une invitation a reagir »
    sans exception : sur une liquidation judiciaire, cela produisait « Y etiez-vous
    cette annee ? »."""
    assert "liquidation judiciaire" in SYSTEM_PROMPT
    assert "hommage" in SYSTEM_PROMPT
    assert "ACTU TERMINALE" in SYSTEM_PROMPT
    assert "que l'article contredit" in SYSTEM_PROMPT


def test_le_prompt_demande_des_paragraphes_courts_separes_par_une_ligne_vide():
    assert "2 a 4 paragraphes courts" in SYSTEM_PROMPT
    assert "ligne vide" in SYSTEM_PROMPT


def test_le_prompt_porte_la_regle_emojis():
    assert "EMOJIS" in SYSTEM_PROMPT
    assert "AUCUN emoji dans `headline`, `title` ou `subtitle`" in SYSTEM_PROMPT
    for emoji in ("🩷", "🎧", "🪩", "🎪", "✨", "🌱", "🔊"):
        assert emoji in SYSTEM_PROMPT


def test_le_prompt_interdit_de_nommer_le_media_source():
    """Retour de l'utilisateur le 2026-09-21 : « supprime les sources des
    descriptions [...] parle de l'actu en elle-meme. »"""
    assert "SANS JAMAIS NOMMER LE MEDIA" in SYSTEM_PROMPT
    assert '"via X"' in SYSTEM_PROMPT
    assert '"selon X"' in SYSTEM_PROMPT
    assert "pas sa source" in SYSTEM_PROMPT


def test_le_prompt_demande_de_varier_la_cloture():
    """Les six derniers posts publies finissaient TOUS par une question : le
    prompt doit donner d'autres formes et demander d'alterner."""
    assert "NE PREND PAS TOUJOURS LA MEME" in SYSTEM_PROMPT
    assert "alterne" in SYSTEM_PROMPT


def test_le_brouillon_exige_exactement_quatre_variantes():
    for nombre in (3, 5):
        with pytest.raises(ValidationError):
            PostDraft(variantes=[_variante(i) for i in range(nombre)], caption="c 🎧 ✨", hashtags=[])

    draft = PostDraft(variantes=[_variante(i) for i in range(4)], caption="c 🎧 ✨", hashtags=[])
    assert len(draft.variantes) == 4


def test_build_user_prompt_contient_le_titre_et_la_source(db_session):
    article = _article(db_session)
    prompt = build_user_prompt(article)
    assert "Vieilles Charrues" in prompt
    assert "Tsugi" in prompt


def _draft():
    return PostDraft(variantes=[_variante(i) for i in range(4)], caption="c 🎧 ✨", hashtags=["#e"])


def test_create_post_pose_les_quatre_variantes_sans_retenue(db_session):
    """La retenue est nulle a la creation : l'utilisateur n'a pas encore choisi, et
    c'est ce qui rend le brouillon reprenable sans repayer un appel Claude."""
    article = _article(db_session)

    post = create_post(db_session, article, _draft())

    assert len(post.variants["variantes"]) == 4
    assert post.variants["retenue"] is None


def test_create_post_pose_une_cover_avec_le_fond_de_larticle(db_session):
    """La slide 1 est une cover, pas un media : c'est la seule que le renderer
    rendra."""
    article = _article(db_session)

    post = create_post(db_session, article, _draft())

    (cover,) = post.slides
    assert cover["kind"] == "cover"
    assert cover["image_url"] == "https://tsugi.fr/photo.jpg"
    # Les trois textes sont poses des la creation, pour que la bascule de gabarit
    # n'ait rien a redemander.
    assert cover["headline"] and cover["title"] and cover["subtitle"]


def test_create_post_prend_la_vignette_dune_video_comme_fond(db_session):
    """Une actu video n'a pas de photo : sa vignette est un fond parfaitement
    valable, et c'est meme le cas normal."""
    article = _article(db_session)
    article.media = [
        {"type": "video", "url": "https://v.mp4", "preview_url": "https://thumb.jpg"}
    ]
    db_session.commit()

    post = create_post(db_session, article, _draft())

    assert post.slides[0]["image_url"] == "https://thumb.jpg"


def test_create_post_retire_les_mentions_de_source_de_la_legende_persistee(db_session):
    """La legende persistee doit deja etre propre : le dashboard affiche
    `post.caption`, elle ne doit pas montrer autre chose que ce qui partira
    reellement (2026-09-21)."""
    article = _article(db_session)
    draft = PostDraft(
        variantes=[_variante(i) for i in range(4)],
        caption="Nico Moreno annonce une date. 🎧 Info via Tsugi. ✨",
        hashtags=["#festival"],
    )

    post = create_post(db_session, article, draft)

    assert "Tsugi" not in post.caption
    assert post.caption == "Nico Moreno annonce une date. 🎧"


def _validation_error() -> ValidationError:
    """Une vraie ValidationError, telle que `messages.parse()` la leve quand la
    sortie du modele viole le schema (ex. la regle de balisage `_balisage_valide`,
    qui n'est verifiee qu'apres coup et n'est donc PAS contrainte cote API)."""
    try:
        PostDraft.model_validate({"variantes": [], "caption": "x", "hashtags": []})
    except ValidationError as exc:
        return exc
    raise AssertionError("attendu une ValidationError")  # pragma: no cover


class _FakeParsedResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class _FakeMessages:
    def __init__(self, effects):
        self._effects = list(effects)
        self.appels = []

    def parse(self, **kwargs):
        self.appels.append(kwargs)
        effect = self._effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class _FakeClient:
    def __init__(self, effects):
        self.messages = _FakeMessages(effects)


def _contenu_utilisateur(appel) -> str:
    return appel["messages"][-1]["content"]


def _draft_legende(caption):
    return PostDraft(variantes=[_variante(i) for i in range(4)], caption=caption, hashtags=[])


def test_composer_relance_une_legende_sans_emoji_en_disant_le_probleme(db_session):
    article = _article(db_session)
    sans = _draft_legende("Une legende sans emoji.")
    avec = _draft_legende("Une legende 🎧 avec emojis ✨")
    client = _FakeClient([_FakeParsedResponse(sans), _FakeParsedResponse(avec)])
    composer = make_claude_composer(client=client, max_attempts=3)

    assert composer(article) is avec
    premier, second = client.messages.appels
    assert "0 emoji" not in _contenu_utilisateur(premier)
    relance = _contenu_utilisateur(second)
    assert "0 emoji" in relance and "caption" in relance
    assert build_user_prompt(article) in relance


def test_composer_accepte_une_legende_sans_emoji_apres_les_tentatives(db_session, caplog):
    """Pas de CompositionError pour un detail de forme : le brouillon part avec
    un avertissement."""
    article = _article(db_session)
    drafts = [_draft_legende(f"Sans emoji {i}.") for i in range(3)]
    client = _FakeClient([_FakeParsedResponse(d) for d in drafts])
    composer = make_claude_composer(client=client, max_attempts=3)

    with caplog.at_level("WARNING"):
        resultat = composer(article)

    assert resultat is drafts[-1]
    assert len(client.messages.appels) == 3
    assert "acceptee malgre" in caplog.text


def test_composer_garde_la_legende_sans_emoji_si_les_relances_echouent(db_session):
    """Les relances ratent la validation stricte : le premier brouillon, qui ne
    pechait que par ses emojis, est rendu plutot que de perdre la composition."""
    article = _article(db_session)
    sans = _draft_legende("Sans emoji.")
    client = _FakeClient([_FakeParsedResponse(sans), _validation_error(), _validation_error()])
    composer = make_claude_composer(client=client, max_attempts=3)

    assert composer(article) is sans


def test_composer_accepte_trop_demojis_apres_les_tentatives(db_session):
    article = _article(db_session)
    trop = _draft_legende("Trop 🩷 c'est 🎧 vraiment 🪩 beaucoup 🎪 trop 🌱 la 🔊 non ✨")
    client = _FakeClient([_FakeParsedResponse(trop)] * 3)
    composer = make_claude_composer(client=client, max_attempts=3)

    assert composer(article) is trop
    assert "7 emoji" in _contenu_utilisateur(client.messages.appels[1])


def test_composer_redonne_l_erreur_de_validation_a_la_relance(db_session):
    article = _article(db_session)
    client = _FakeClient([_validation_error_emoji(), _FakeParsedResponse(_draft())])
    composer = make_claude_composer(client=client, max_attempts=3)

    composer(article)

    relance = _contenu_utilisateur(client.messages.appels[1])
    assert "Emoji interdit" in relance


def test_composer_leve_composition_error_apres_echecs_repetes(db_session):
    """Une composition qui rate systematiquement la validation doit produire une
    erreur exploitable (CompositionError), pas laisser fuiter la ValidationError
    brute jusqu'a l'API (ce qui deviendrait un 500 avec stacktrace)."""
    article = _article(db_session)
    client = _FakeClient([_validation_error(), _validation_error(), _validation_error()])
    composer = make_claude_composer(client=client, max_attempts=3)

    with pytest.raises(CompositionError):
        composer(article)


def _validation_error_emoji() -> ValidationError:
    """L'erreur que `messages.parse()` leve quand le modele glisse un emoji dans
    l'accroche : le garde-fou est un validateur Pydantic, donc verifie APRES coup,
    exactement comme la regle de balisage."""
    try:
        PostDraft.model_validate(
            {
                "variantes": [
                    {
                        "headline": "**Vieilles Charrues** : le line-up tombe 🎧",
                        "title": "**Vieilles Charrues**",
                        "subtitle": "le line-up electro **ce week-end**",
                    }
                ]
                * 4,
                "caption": "x",
                "hashtags": [],
            }
        )
    except ValidationError as exc:
        return exc
    raise AssertionError("attendu une ValidationError")  # pragma: no cover


def test_composer_reessaie_quand_le_modele_met_un_emoji_dans_laccroche(db_session):
    """Le prompt ne suffit pas : quand le modele glisse malgre tout un emoji dans un
    texte rendu par Chromium, la boucle de tentatives existante relance l'appel et
    c'est la SECONDE reponse qui est retournee."""
    article = _article(db_session)
    bon_draft = _draft()
    client = _FakeClient([_validation_error_emoji(), _FakeParsedResponse(bon_draft)])
    composer = make_claude_composer(client=client, max_attempts=3)

    assert composer(article) is bon_draft


def test_composer_reessaie_puis_reussit(db_session):
    """La boucle de tentatives sert a quelque chose : un premier echec suivi d'un
    succes doit bien aboutir a un brouillon, pas a une erreur."""
    article = _article(db_session)
    bon_draft = PostDraft(
        variantes=[_variante(i) for i in range(4)],
        caption="c 🎧 ✨",
        hashtags=[],
    )
    client = _FakeClient([_validation_error(), _FakeParsedResponse(bon_draft)])
    composer = make_claude_composer(client=client, max_attempts=3)

    draft = composer(article)

    assert draft is bon_draft


def test_create_post_prend_la_vignette_d_un_reel_sans_media_url(db_session):
    article = _article(db_session)
    article.media = [entree("video", None, preview_url="https://thumb.jpg")]
    db_session.commit()

    post = create_post(db_session, article, _draft())

    assert post.slides[0]["image_url"] == "https://thumb.jpg"


def test_une_sortie_vide_est_retentee_comme_une_erreur_de_validation(db_session):
    """max_tokens ou refus : `parsed_output` vaut None. Jamais d'AttributeError."""
    article = _article(db_session)
    bon = _draft()
    client = _FakeClient([_FakeParsedResponse(None), _FakeParsedResponse(bon)])
    assert make_claude_composer(client=client, max_attempts=3)(article) is bon
    assert "vide" in _contenu_utilisateur(client.messages.appels[1])


def test_des_sorties_vides_repetees_donnent_une_composition_error(db_session):
    article = _article(db_session)
    client = _FakeClient([_FakeParsedResponse(None)] * 3)
    with pytest.raises(CompositionError):
        make_claude_composer(client=client, max_attempts=3)(article)


def _actif_artiste():
    return actif(
        entree("photo", "https://cdn.ig/fred.jpg"),
        origine="artiste",
        compte="@fredagain",
        permalink="https://www.instagram.com/p/F/",
        fichier="file:///cache/fred.jpg",
    )


def test_create_post_garde_les_visuels_choisis(db_session):
    """Les actifs choisis AVANT l'appel paye sont ceux que le post publiera : le
    fond de cover est la copie locale, et le credit en decoule."""
    article = _article(db_session)
    choisi = _actif_artiste()

    post = create_post(db_session, article, _draft(), visuels=ChoixVisuels([choisi], "@fredagain"))

    assert post.assets == [choisi]
    assert post.credit == "@fredagain"
    assert post.slides[0]["image_url"] == "file:///cache/fred.jpg"


def test_create_post_sans_visuel_choisi_laisse_la_cover_sans_fond(db_session):
    """Un choix VIDE est un choix : les medias de l'article ne reviennent pas par
    la fenetre servir de fond."""
    article = _article(db_session)
    article.media = [entree("photo", "https://tsugi.fr/photo.jpg")]
    db_session.commit()

    post = create_post(db_session, article, _draft(), visuels=ChoixVisuels())

    assert (post.assets, post.credit, post.slides[0]["image_url"]) == ([], None, None)


def test_create_post_sans_choix_reste_historique(db_session):
    """Sans `visuels` (composition manuelle, tests) : `assets` reste NULL et le
    fond vient des medias autorises de l'article."""
    post = create_post(db_session, _article(db_session), _draft())

    assert post.assets is None
    assert post.slides[0]["image_url"] == "https://tsugi.fr/photo.jpg"


def test_create_post_fige_la_teinte_de_larticle(db_session):
    """L'article sera purge a 14 jours : la teinte doit survivre sur le post,
    comme le titre et l'URL denormalises."""
    article = _article(db_session)
    article.category = "ecology"
    db_session.commit()

    post = create_post(db_session, article, _draft())

    assert post.variants["palette"] == "verte"


def test_create_post_laisse_la_charte_aux_autres_actus(db_session):
    article = _article(db_session)
    article.category = "event_festival"
    db_session.commit()

    assert create_post(db_session, article, _draft()).variants["palette"] == "rose"


def test_create_post_ecarte_laffiche_du_fond_de_cover(db_session):
    """Le juge visuel ne choisit pas la premiere image mais la moins « affiche ».
    Ici : l'affiche de line-up arrive en tete, la photo de scene la remplace."""
    article = _article(db_session)
    article.media = [
        {"type": "photo", "url": "https://tsugi.fr/affiche.jpg"},
        {"type": "photo", "url": "https://tsugi.fr/scene.jpg"},
    ]
    db_session.commit()
    vus = []

    def selecteur(candidats):
        vus.append(list(candidats))
        return "https://tsugi.fr/scene.jpg"

    post = create_post(db_session, article, _draft(), selecteur_fond=selecteur)

    assert vus == [["https://tsugi.fr/affiche.jpg", "https://tsugi.fr/scene.jpg"]]
    assert post.slides[0]["image_url"] == "https://tsugi.fr/scene.jpg"


def test_la_cover_reel_suit_la_meme_regle(db_session):
    """Un seul chemin de composition pour les deux gabarits : l'utilisateur n'a
    pas demande d'exception pour les reels."""
    article = _article(db_session)
    choix = ChoixVisuels(
        [
            actif(entree("photo", "https://cdn/affiche.jpg"), origine="article"),
            actif(entree("photo", "https://cdn/live.jpg"), origine="article"),
        ],
        "@festival",
    )

    post = create_post(
        db_session, article, _draft(), template="kinda_reel", visuels=choix,
        selecteur_fond=lambda candidats: "https://cdn/live.jpg",
    )

    assert post.slides[0]["image_url"] == "https://cdn/live.jpg"


def test_resoudre_fond_reporte_au_lieu_de_sauter_sur_une_panne_de_reserve(db_session, monkeypatch):
    """Tour de correction 1/5 (I-1) : quand `eviter_l_affiche` rend
    `Fond.differe=True` (au moins une reserve n'a pas pu etre consultee),
    `resoudre_fond` doit REPORTER (`VisuelsDifferes`), jamais sauter
    (`VisuelIntrouvable`) -- les deux issues ont des consequences tres
    differentes au planificateur (report : le creneau garde l'article ; saut :
    l'article se met en pause)."""
    from app.content import illustration

    monkeypatch.setattr(
        illustration, "tailles_de", lambda urls, **k: {"https://cdn/petite.jpg": (564, 345)}
    )
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/petite.jpg"), origine="article")], "@source",
        secours=[Lot(ORIGINE_COMPTE, lambda: (_ for _ in ()).throw(VisuelDiffere("quota")))],
    )

    with pytest.raises(VisuelsDifferes):
        resoudre_fond(
            choix, "kinda_post",
            selecteur_fond=lambda candidats: "https://cdn/petite.jpg",
            verdicts_fond=lambda urls: {
                "https://cdn/petite.jpg": Verdict(affiche=False, sujet="scene", note=70)
            },
        )


def test_le_plancher_de_resolution_suit_le_gabarit_publie(db_session, monkeypatch):
    """Post 71 (CLAUDE.md) : une seule og:image, 564x345, agrandie 3,91x pour
    remplir 1080x1350 -- trop petite pour partir telle quelle, mais assez
    grande (Tache 6, 2026-09-23) pour que le GEL tente l'agrandissement : la
    composition n'exclut plus ce candidat, elle le marque `a_agrandir`."""
    from app.content import illustration
    from app.content.illustration import Verdict

    monkeypatch.setattr(
        illustration, "tailles_de", lambda urls, **k: {"https://cdn/petite.jpg": (564, 345)}
    )
    article = _article(db_session)
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/petite.jpg"), origine="article")], "@source",
    )

    post = create_post(
        db_session, article, _draft(), visuels=choix,
        selecteur_fond=lambda candidats: "https://cdn/petite.jpg",
        verdicts_fond=lambda urls: {
            "https://cdn/petite.jpg": Verdict(affiche=False, sujet="scene", note=70)
        },
    )

    assert post.slides[0]["image_url"] == "https://cdn/petite.jpg"
    assert post.variants["fond"]["a_agrandir"] is True


def test_un_fond_trop_petit_meme_pour_etre_agrandi_saute_toujours(db_session, monkeypatch):
    """Sous `visuels_upscale_source_min_px` (300 px), l'agrandissement ne
    sauverait de toute facon pas le post : la composition saute encore,
    exactement comme la Tache 5."""
    from app.content import illustration
    from app.content.illustration import Verdict

    monkeypatch.setattr(
        illustration, "tailles_de", lambda urls, **k: {"https://cdn/minuscule.jpg": (200, 150)}
    )
    article = _article(db_session)
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/minuscule.jpg"), origine="article")], "@source",
    )

    with pytest.raises(VisuelIntrouvable):
        create_post(
            db_session, article, _draft(), visuels=choix,
            selecteur_fond=lambda candidats: "https://cdn/minuscule.jpg",
            verdicts_fond=lambda urls: {
                "https://cdn/minuscule.jpg": Verdict(affiche=False, sujet="scene", note=70)
            },
        )


def test_une_photo_assez_grande_pour_le_post_mais_pas_pour_le_reel(db_session, monkeypatch):
    """Le reel (1080x1920) est plus exigeant que le post (1080x1350) sur la MEME
    photo : la cible depend du gabarit publie, jamais fixe. Assez grande pour
    etre agrandie (Tache 6), la composition en reel ne saute plus non plus --
    elle marque le brouillon, le gel tranchera."""
    from app.content import illustration
    from app.content.illustration import Verdict

    monkeypatch.setattr(
        illustration,
        "tailles_de",
        lambda urls, **k: {"https://cdn/moyenne.jpg": (1080, 800)},
    )
    article = _article(db_session)

    def _choix():
        return ChoixVisuels(
            [actif(entree("photo", "https://cdn/moyenne.jpg"), origine="article")], "@source",
        )

    verdicts = lambda urls: {  # noqa: E731
        "https://cdn/moyenne.jpg": Verdict(affiche=False, sujet="scene", note=70)
    }

    post_carrousel = create_post(
        db_session, article, _draft(), visuels=_choix(),
        selecteur_fond=lambda candidats: "https://cdn/moyenne.jpg", verdicts_fond=verdicts,
    )
    assert post_carrousel.slides[0]["image_url"] == "https://cdn/moyenne.jpg"
    assert post_carrousel.variants["fond"]["a_agrandir"] is False

    post_reel = create_post(
        db_session, article, _draft(), template="kinda_reel", visuels=_choix(),
        selecteur_fond=lambda candidats: "https://cdn/moyenne.jpg", verdicts_fond=verdicts,
    )
    assert post_reel.slides[0]["image_url"] == "https://cdn/moyenne.jpg"
    assert post_reel.variants["fond"]["a_agrandir"] is True


def test_un_gabarit_inconnu_desactive_le_plancher_sans_lever(db_session, caplog):
    """Un plancher de resolution est un confort, jamais une garantie : un nom de
    gabarit qu'on ne connait pas ne doit pas faire echouer la composition."""
    from app.content.illustration import Verdict

    article = _article(db_session)
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/petite.jpg"), origine="article")], "@source",
    )

    post = create_post(
        db_session, article, _draft(), template="gabarit-fantome", visuels=choix,
        selecteur_fond=lambda candidats: "https://cdn/petite.jpg",
        verdicts_fond=lambda urls: {
            "https://cdn/petite.jpg": Verdict(affiche=False, sujet="scene", note=70)
        },
    )

    assert post.slides[0]["image_url"] == "https://cdn/petite.jpg"


def test_un_juge_muet_garde_le_premier_candidat(db_session):
    """Degradation : None veut dire « je n'ai rien a dire », pas « pas de fond »."""
    article = _article(db_session)

    post = create_post(db_session, article, _draft(), selecteur_fond=lambda candidats: None)

    assert post.slides[0]["image_url"] == "https://tsugi.fr/photo.jpg"


def test_une_affiche_cede_la_place_a_une_photo_du_compte_source(db_session):
    """Le post des Transmusicales (2026-09-18) : les CINQ candidats etaient des
    affiches de line-up, le juge rendait donc la moins mauvaise. La composition
    va alors chercher une photo sur le compte qui a publie l'actu."""
    from app.content.illustration import Verdict

    article = _article(db_session)
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/affiche.jpg"), origine="article")],
        "@transmusicales",
        secours=[Lot(ORIGINE_COMPTE, lambda: ["file:///cache/live.jpg"])],
    )

    post = create_post(
        db_session, article, _draft(), visuels=choix,
        selecteur_fond=lambda candidats: "https://cdn/affiche.jpg",
        verdicts_fond=lambda urls: dict.fromkeys(
            urls, Verdict(affiche=True, sujet="autre", note=10)
        ) | {"file:///cache/live.jpg": Verdict(affiche=False, sujet="scene", note=80)},
    )

    assert post.slides[0]["image_url"] == "file:///cache/live.jpg"
    assert post.variants["fond"] == {"origine": ORIGINE_COMPTE, "a_agrandir": False}
    # La photo de la reserve devient AUSSI un actif (2026-09-18, post Noisia) :
    # un carrousel qui n'aurait que l'affiche ne montre jamais les artistes, et
    # une reserve deja payee (lecture, telechargement, jetons) n'a aucune raison
    # de ne servir qu'au fond.
    assert [a.get("fichier") or a["url"] for a in post.assets] == [
        "https://cdn/affiche.jpg", "file:///cache/live.jpg",
    ]


def test_les_autres_photos_de_la_reserve_deviennent_des_slides(db_session):
    """« il n'y a pas de slide qui montre les artistes en question » (utilisateur,
    2026-09-18, post Noisia). La meilleure photo fait le fond, les suivantes
    remplissent le carrousel -- elles sont deja copiees en local et deja jugees."""
    from app.content.illustration import Verdict

    article = _article(db_session)
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/affiche.jpg"), origine="article")],
        "@transmusicales",
        secours=[Lot(ORIGINE_ARTISTE, lambda: ["file:///cache/a.jpg", "file:///cache/b.jpg"])],
    )

    post = create_post(
        db_session, article, _draft(), visuels=choix,
        selecteur_fond=lambda candidats: "https://cdn/affiche.jpg",
        verdicts_fond=lambda urls: {
            "https://cdn/affiche.jpg": Verdict(affiche=True, sujet="autre", note=10),
            "file:///cache/a.jpg": Verdict(affiche=False, sujet="autre", note=60),
            "file:///cache/b.jpg": Verdict(affiche=False, sujet="scene", note=80),
        },
    )

    assert post.slides[0]["image_url"] == "file:///cache/b.jpg"
    assert [a.get("fichier") or a["url"] for a in post.assets] == [
        "https://cdn/affiche.jpg", "file:///cache/b.jpg", "file:///cache/a.jpg",
    ]
    assert post.variants["fond"] == {"origine": ORIGINE_ARTISTE, "a_agrandir": False}


def test_une_affiche_de_reserve_ne_devient_jamais_une_slide(db_session):
    """Une affiche de l'ACTU reste une slide honnete ; une affiche pechee dans
    une reserve n'illustre rien -- on ne la publie ni en fond ni en slide. Toute
    l'echelle donnant des affiches, le post SAUTE (Tache 5)."""
    from app.content.illustration import Verdict

    article = _article(db_session)
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/affiche.jpg"), origine="article")],
        "@transmusicales",
        secours=[Lot(ORIGINE_COMPTE, lambda: ["file:///cache/flyer.jpg"])],
    )

    with pytest.raises(VisuelIntrouvable):
        create_post(
            db_session, article, _draft(), visuels=choix,
            selecteur_fond=lambda candidats: "https://cdn/affiche.jpg",
            verdicts_fond=lambda urls: dict.fromkeys(
                urls, Verdict(affiche=True, sujet="autre", note=10)
            ),
        )


def test_une_affiche_sans_remplacante_fait_sauter_le_post(db_session):
    """Avant la Tache 5 (2026-09-21) : « ya toujours l'affiche en fond !! »
    faisait partir la cover sur l'aplat Kinda. Desormais l'aplat n'est plus une
    issue publiable : sans aucune reserve pour remplacer l'affiche, le post
    SAUTE -- l'article reste eligible, il n'est pas exclu."""
    from app.content.illustration import Verdict

    article = _article(db_session)
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/affiche.jpg"), origine="article")], "@x"
    )

    with pytest.raises(VisuelIntrouvable):
        create_post(
            db_session, article, _draft(), visuels=choix,
            selecteur_fond=lambda candidats: "https://cdn/affiche.jpg",
            verdicts_fond=lambda urls: dict.fromkeys(
                urls, Verdict(affiche=True, sujet="autre", note=10)
            ),
        )


def test_sans_secours_le_fond_reste_celui_du_juge(db_session):
    """Un juge muet (vision coupee, pas de cle) ne coute rien et ne change rien :
    le fond retenu reste celui de l'actu."""
    article = _article(db_session)
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/affiche.jpg"), origine="article")], "@x"
    )

    post = create_post(
        db_session, article, _draft(), visuels=choix,
        selecteur_fond=lambda candidats: "https://cdn/affiche.jpg",
        verdicts_fond=lambda urls: {},
    )

    assert post.slides[0]["image_url"] == "https://cdn/affiche.jpg"
    assert post.variants["fond"] == {"origine": ORIGINE_ARTICLE, "a_agrandir": False}


# --- Une carte d'annonce par carrousel, jamais sept -----------------------
#
# Post du 2026-09-18 13h10 (programme des Trans Musicales) : les slides 2 a 8
# etaient la carte d'annonce du festival, declinee sept fois dans SA charte.
# Le post devenait leur carrousel avec une cover Kinda posee devant.


def _affiches(*urls, autres=(), secours=()):
    """Un `ChoixVisuels` dont les `urls` sont des affiches, `autres` des photos.

    `secours` (Tache 5, 2026-09-21) : sans reserve valide, un post dont TOUS les
    candidats sont des affiches SAUTE desormais au lieu de partir sur le fond
    Kinda -- voir `test_une_affiche_sans_remplacante_fait_sauter_le_post`. Les
    tests d'ici qui persistent bel et bien un post en fournissent une."""
    return ChoixVisuels(
        [actif(entree("photo", url), origine="article") for url in (*urls, *autres)],
        "@transmusicales",
        secours=list(secours),
    )


def _juge(affiches):
    def verdicts(urls):
        from app.content.illustration import Verdict

        return {
            url: Verdict(affiche=url in affiches, sujet="autre" if url in affiches else "scene",
                         note=10 if url in affiches else 80)
            for url in urls
        }

    return verdicts


def _urls(post):
    return [a.get("fichier") or a["url"] for a in post.assets]


def test_sept_cartes_d_annonce_n_en_laissent_qu_une(db_session):
    cartes = [f"https://cdn/prog-{n}.jpg" for n in range(7)]
    # Tache 5 : sans reserve, sept affiches et rien d'autre feraient sauter le
    # post -- une photo de compte le sauve, et devient le fond a la place de
    # l'aplat Kinda (`_juge` la juge non-affiche : elle n'est pas dans `cartes`).
    secours = [Lot(ORIGINE_COMPTE, lambda: ["file:///cache/live.jpg"])]
    post = create_post(
        db_session, _article(db_session), _draft(),
        visuels=_affiches(*cartes, secours=secours),
        selecteur_fond=lambda candidats: cartes[0],
        verdicts_fond=_juge(set(cartes)),
    )

    # La premiere carte dans l'ordre des candidats reste en slide : sur un
    # carrousel d'annonce, la carte de tete porte le message principal
    # (« TOUTE LA PROG REVELEE »). La photo de la reserve devient AUSSI une
    # slide (`compose.actifs_de_secours`), en plus de faire le fond.
    assert _urls(post) == [cartes[0], "file:///cache/live.jpg"]
    # Le fond, lui, n'est jamais une affiche : tout etait une carte, la reserve
    # le remplace.
    assert post.slides[0]["image_url"] == "file:///cache/live.jpg"
    assert post.variants["fond"] == {"origine": ORIGINE_COMPTE, "a_agrandir": False}


def test_les_photos_restent_toutes_seule_l_affiche_est_plafonnee(db_session):
    """Le plafond ne vise que les cartes de texte : une photo n'est jamais jetee."""
    cartes = ["https://cdn/prog-1.jpg", "https://cdn/prog-2.jpg"]
    photos = ["https://cdn/scene.jpg", "https://cdn/foule.jpg"]
    post = create_post(
        db_session, _article(db_session), _draft(),
        visuels=_affiches(*cartes, autres=photos),
        selecteur_fond=lambda candidats: photos[0],
        verdicts_fond=_juge(set(cartes)),
    )

    assert _urls(post) == [cartes[0], *photos]


def test_un_visuel_sans_verdict_n_est_jamais_jete(db_session):
    """Dans le doute on garde : une photo jetee par erreur appauvrit le post,
    une carte de trop ne fait que l'alourdir. Et la couverture des verdicts est
    partielle -- le juge du fond n'en note qu'un nombre borne."""
    cartes = [f"https://cdn/prog-{n}.jpg" for n in range(3)]
    post = create_post(
        db_session, _article(db_session), _draft(), visuels=_affiches(*cartes),
        selecteur_fond=lambda candidats: cartes[0],
        # Seule la premiere a ete jugee : les deux autres restent, faute de
        # verdict -- c'est ce qui arrive a ce qui tombe hors de la borne du lot.
        verdicts_fond=lambda urls: _juge(set(cartes))([cartes[0]]),
    )

    assert _urls(post) == cartes


def test_les_sept_cartes_des_trans_ne_laissent_qu_une_slide(db_session, monkeypatch, tmp_path):
    """LE cas de production, joue de bout en bout avec le vrai chemin du juge :
    sept cartes d'annonce, aucune photo. Les sept partent dans UN SEUL appel --
    c'est le lot qui s'elargit, pas un second juge --, et une seule survit.

    C'est ce test qui separe « defaut eteint » de « defaut attenue » : avec un
    lot borne a trois, quatre cartes restaient inconnues, donc gardees.

    Tache 5 (2026-09-21) : sept affiches sans reserve feraient desormais SAUTER
    le post -- une reserve (compte source) est donc ajoutee, et son vrai contenu
    doit ETRE distingue des cartes par le juge mocke (via `_octets`, qui encode
    l'URL) pour que le test verifie encore le bon comportement : une SEULE carte
    survit en slide, et la photo de reserve devient le fond a la place de
    l'aplat Kinda."""
    from app.config import settings
    from app.content import illustration

    monkeypatch.setattr(settings, "illustration_cache_path", tmp_path / "verdicts.json")
    monkeypatch.setattr(settings, "illustration_vision_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(illustration, "_octets", lambda url: url.encode())
    monkeypatch.setattr(illustration, "reduire", lambda octets, taille=None: octets)
    lots = []

    def juge(images):
        lots.append(len(images))
        return [
            illustration.Verdict(
                affiche=b"trans-" in image,
                sujet="autre" if b"trans-" in image else "scene",
                note=10 if b"trans-" in image else 80,
            )
            for image in images
        ]

    monkeypatch.setattr(illustration, "make_claude_juge", lambda: juge)
    cartes = [f"https://cdn/trans-{n}.jpg" for n in range(7)]
    secours = [Lot(ORIGINE_COMPTE, lambda: ["file:///cache/live.jpg"])]

    post = create_post(
        db_session, _article(db_session), _draft(), visuels=_affiches(*cartes, secours=secours)
    )

    assert lots == [7, 1], "les sept cartes en un appel, la reserve dans un second"
    assert _urls(post) == [cartes[0], "file:///cache/live.jpg"]
    assert post.slides[0]["image_url"] == "file:///cache/live.jpg"
    assert post.variants["fond"] == {"origine": ORIGINE_COMPTE, "a_agrandir": False}


def test_l_unique_affiche_sans_secours_fait_desormais_sauter_le_post(db_session):
    """Avant la Tache 5 (2026-09-21) : une seule carte, une seule affiche, la
    cover partait sur le fond Kinda et le post restait publiable a deux
    elements. L'aplat Kinda n'etant plus une issue publiable, ce cas SAUTE
    desormais -- il n'y a ni reserve ni autre candidat pour le sauver."""
    article = _article(db_session)
    carte = "https://cdn/prog.jpg"

    with pytest.raises(VisuelIntrouvable):
        create_post(
            db_session, article, _draft(), visuels=_affiches(carte),
            selecteur_fond=lambda candidats: carte,
            verdicts_fond=_juge({carte}),
        )


def test_l_unique_visuel_meme_affiche_garde_son_post_publiable_avec_secours(db_session):
    """« Jamais un post reduit a sa cover » : le plafond ne doit pas vider un
    carrousel qui n'avait qu'une carte -- elle reste, le post part a deux
    elements (cf. `app/publish/preparation.py`). Depuis la Tache 5, ce n'est
    publiable que parce qu'une reserve donne un fond de remplacement : sans
    elle, ce cas sauterait desormais (cf. le test precedent)."""
    from app.publish.preparation import medias_du_carrousel

    article = _article(db_session)
    carte = "https://cdn/prog.jpg"
    secours = [Lot(ORIGINE_COMPTE, lambda: ["file:///cache/live.jpg"])]
    post = create_post(
        db_session, article, _draft(), visuels=_affiches(carte, secours=secours),
        selecteur_fond=lambda candidats: carte,
        verdicts_fond=_juge({carte}),
    )

    assert _urls(post) == [carte, "file:///cache/live.jpg"]
    assert post.slides[0]["image_url"] == "file:///cache/live.jpg"
    assert [m["url"] for m in medias_du_carrousel(post, article)] == [carte]


_SANS_ACCENT = (
    "Le festival devoile un line-up pense pour la bande son du week-end, avec un "
    "plateau electro intitule Nuit Blanche et une jauge qui depasse celle de l'an "
    "dernier. On y retrouvera les habitues du lieu comme les nouvelles tetes de la "
    "scene francaise, entre deux sets tardifs. Le programme complet arrive lundi."
)
_AVEC_ACCENT = _SANS_ACCENT.replace("devoile", "dévoile").replace("pense", "pensé")


def test_probleme_accents_repere_une_longue_legende_en_ascii():
    """Deux des quatre posts du 2026-09-18 sont partis sans le moindre accent :
    « un titre pense pour la bande son », « intitule », « depasse »."""
    probleme = probleme_accents(_SANS_ACCENT)
    assert probleme is not None
    assert "accent" in probleme and "caption" in probleme


def test_probleme_accents_laisse_passer_une_legende_courte():
    """Une accroche de quelques mots peut legitimement n'avoir aucun accent."""
    assert probleme_accents("Le line-up tombe ce week-end 🎧 on y va ? ✨") is None


def test_probleme_accents_accepte_un_seul_accent():
    assert probleme_accents(_AVEC_ACCENT) is None


def test_composer_relance_une_legende_sans_accents(db_session):
    """Le modele rend parfois de l'ASCII force : on le lui dit et on relance."""
    article = _article(db_session)
    sans = _draft_legende(_SANS_ACCENT + " 🎧 ✨")
    avec = _draft_legende(_AVEC_ACCENT + " 🎧 ✨")
    client = _FakeClient([_FakeParsedResponse(sans), _FakeParsedResponse(avec)])
    composer = make_claude_composer(client=client, max_attempts=3)

    assert composer(article) is avec
    relance = _contenu_utilisateur(client.messages.appels[1])
    assert "accent" in relance


def test_composer_accepte_une_legende_sans_accents_apres_les_tentatives(db_session, caplog):
    """Jamais une composition perdue pour un defaut de forme : le brouillon part
    avec un avertissement, comme pour les emojis."""
    article = _article(db_session)
    drafts = [_draft_legende(f"{_SANS_ACCENT} 🎧 ✨ ({i})") for i in range(3)]
    client = _FakeClient([_FakeParsedResponse(d) for d in drafts])
    composer = make_claude_composer(client=client, max_attempts=3)

    with caplog.at_level("WARNING"):
        resultat = composer(article)

    assert resultat is drafts[-1]
    assert len(client.messages.appels) == 3
    assert "acceptee malgre" in caplog.text


def test_le_prompt_exige_le_francais_accentue():
    assert "ACCENTUE" in SYSTEM_PROMPT and "intitulé" in SYSTEM_PROMPT


# --- La cloture n'est plus systematiquement une question (2026-09-21) -------
#
# Les SIX derniers posts publies finissaient tous par une question rhetorique
# (« Qu'en pensez-vous ? », « Vous suivez CORTIS depuis leurs debuts ? »...).


def test_probleme_cloture_question_repere_une_legende_qui_finit_par_l_interrogation():
    probleme = probleme_cloture_question("Le festival revient en octobre. Qu'en pensez-vous ?")
    assert probleme is not None
    assert "question" in probleme


def test_probleme_cloture_question_ignore_les_emojis_de_fin():
    """« Qui tente le deplacement ? ✨ » (post 62) finit bien par une question,
    l'emoji de ponctuation ne doit pas le masquer."""
    assert probleme_cloture_question("Qui tente le deplacement ? ✨") is not None


def test_probleme_cloture_question_laisse_passer_une_cloture_variee():
    assert probleme_cloture_question("La billetterie ouvre vendredi a midi.") is None


def test_composer_relance_une_legende_qui_finit_par_une_question(db_session):
    article = _article(db_session)
    question = _draft_legende("Le festival revient en octobre 🎧. Qu'en pensez-vous ✨?")
    variee = _draft_legende("Le festival revient en octobre 🎧. La billetterie ouvre vendredi ✨")
    client = _FakeClient([_FakeParsedResponse(question), _FakeParsedResponse(variee)])
    composer = make_claude_composer(client=client, max_attempts=3)

    assert composer(article) is variee
    relance = _contenu_utilisateur(client.messages.appels[1])
    assert "question" in relance and "caption" in relance


def test_composer_accepte_une_cloture_en_question_apres_les_tentatives(db_session, caplog):
    """Jamais une composition perdue pour un defaut de forme : une legende qui
    finit encore par une question part avec un avertissement."""
    article = _article(db_session)
    drafts = [
        _draft_legende(f"Le festival revient en octobre ({i}) 🎧. On y va ✨?") for i in range(3)
    ]
    client = _FakeClient([_FakeParsedResponse(d) for d in drafts])
    composer = make_claude_composer(client=client, max_attempts=3)

    with caplog.at_level("WARNING"):
        resultat = composer(article)

    assert resultat is drafts[-1]
    assert len(client.messages.appels) == 3
    assert "acceptee malgre" in caplog.text


def test_le_prompt_dit_de_quel_compte_vient_le_visuel(db_session):
    """Le compositeur ne recoit que l'article : sans cette ligne, il ne sait pas
    que la video vient de LE SSERAFIM et titre le premier nom du line-up (le reel
    « BTS » du 2026-09-18)."""
    article = _article(db_session)

    assert "Compte du visuel publie : @le_sserafim" in build_user_prompt(article, "@le_sserafim")


def test_le_prompt_tait_le_compte_du_visuel_quand_il_est_inconnu(db_session):
    """Une composition a la main n'a pas cette information : pas de ligne vide."""
    article = _article(db_session)

    assert "Compte du visuel" not in build_user_prompt(article)


def test_le_prompt_exige_de_nommer_l_artiste_de_la_video():
    assert "Compte du visuel publie" in SYSTEM_PROMPT
    assert "title" in SYSTEM_PROMPT and "AUCUN" in SYSTEM_PROMPT


def test_le_compositeur_par_defaut_transmet_le_compte_du_visuel(db_session):
    article = _article(db_session)
    client = _FakeClient([_FakeParsedResponse(_draft())])

    make_claude_composer(client=client, max_attempts=3)(article, compte_visuel="@le_sserafim")

    assert "@le_sserafim" in _contenu_utilisateur(client.messages.appels[0])


def test_un_compositeur_qui_ignore_le_compte_du_visuel_marche_toujours(db_session):
    """Le crochet est injecte (run_publication, api/posts, des doubles de test) :
    un compositeur ecrit avant cette regle ne doit pas planter -- une exception
    ici passerait pour une panne systemique et poserait une erreur sur le creneau."""
    article = _article(db_session)
    bon = _draft()
    recus = []

    def ancien_compositeur(art):
        recus.append(art)
        return bon

    assert appeler_compositeur(ancien_compositeur, article, "@le_sserafim") is bon
    assert recus == [article]


def test_un_compositeur_qui_accepte_le_compte_du_visuel_le_recoit(db_session):
    article = _article(db_session)
    bon = _draft()
    recus = []

    def compositeur(art, *, compte_visuel=None):
        recus.append(compte_visuel)
        return bon

    assert appeler_compositeur(compositeur, article, "@le_sserafim") is bon
    # Et sans compte connu, l'appel reste celui d'avant.
    appeler_compositeur(compositeur, article)
    assert recus == ["@le_sserafim", None]


def test_la_grande_photo_d_un_doublon_devient_le_fond_quand_celle_de_l_actu_est_trop_petite(
    db_session, monkeypatch
):
    """Revue du 2026-09-21 (I-4), le cas qui justifiait les taches 2 et 4
    ensemble : une capture de 564 px chez le representant, la photo de presse
    2156 px du meme evenement chez un doublon. Le juge les dit toutes deux des
    photos ; le plancher de nettete ecarte la premiere -- la seconde doit
    devenir le fond, pas rester en slide pendant que la cover part sur l'aplat
    Kinda. Et elle n'est pas republiee en slide derriere (regle du 2026-09-19)."""
    from app.content import illustration
    from app.content.illustration import Verdict

    petite, grande = "https://cdn/capture.jpg", "https://presse.example/grande.jpg"
    monkeypatch.setattr(
        illustration, "tailles_de",
        lambda urls, **k: {petite: (564, 345), grande: (2156, 1320)},
    )
    ouverts = []
    choix = ChoixVisuels(
        [
            actif(entree("photo", petite), origine="article", article_id=1),
            actif(entree("photo", grande), origine="article", article_id=2),
        ],
        "@source",
        secours=[Lot(ORIGINE_COMPTE, lambda: ouverts.append("compte") or [])],
    )

    post = create_post(
        db_session, _article(db_session), _draft(), visuels=choix,
        selecteur_fond=lambda candidats: petite,
        verdicts_fond=lambda urls: {
            petite: Verdict(affiche=False, sujet="portrait", note=80),
            grande: Verdict(affiche=False, sujet="scene", note=60),
        },
    )

    assert post.slides[0]["image_url"] == grande
    assert post.variants["fond"] == {"origine": ORIGINE_ARTICLE, "a_agrandir": False}
    assert ouverts == []


# --- Prompt et controle de cloture coherents (revue du 2026-09-21, I-5) ----
#
# Le prompt proposait encore « une question » parmi les clotures possibles, et
# le controle refusait TOUTE cloture en `?` : sur un historique ou 6 posts sur 6
# finissaient par une question, une relance quasi systematique -- jusqu'a trois
# appels `composer_model` par composition. Tranche : la question sort des
# clotures proposees. Un controle sans memoire ne sait pas faire « de temps en
# temps », il ne sait faire que « jamais » ; c'est donc le prompt qui s'aligne.


def test_le_prompt_ne_propose_plus_la_question_comme_cloture():
    assert "termine par une \\\nquestion" not in SYSTEM_PROMPT
    assert "termine par une question" not in SYSTEM_PROMPT.replace("\n", " ")
    assert "JAMAIS par une question" in SYSTEM_PROMPT


def test_la_relance_ne_dit_plus_qu_une_question_reste_possible():
    probleme = probleme_cloture_question("Le festival revient. On y va ?")
    assert "reste possible" not in probleme


def test_le_filtre_des_sources_passe_avant_le_controle_de_cloture(db_session):
    """Applique APRES la validation, `sans_mentions_de_source` pouvait retirer la
    derniere phrase et laisser en fin de legende la question que le controle
    venait d'ecarter : « ... Vous y serez ? Info via Tsugi. » passait le
    controle, puis partait en ligne sur « Vous y serez ? »."""
    article = _article(db_session)
    cachee = _draft_legende("Le festival revient en octobre 🎧. Vous y serez ✨? Info via Tsugi.")
    variee = _draft_legende("Le festival revient en octobre 🎧. La billetterie ouvre vendredi ✨")
    client = _FakeClient([_FakeParsedResponse(cachee), _FakeParsedResponse(variee)])
    composer = make_claude_composer(client=client, max_attempts=3)

    assert composer(article) is variee
    assert len(client.messages.appels) == 2


def test_le_brouillon_rendu_porte_deja_la_legende_filtree(db_session):
    article = _article(db_session)
    draft = _draft_legende(
        "Le festival revient en octobre 🎧. La billetterie ouvre vendredi ✨. Info via Tsugi."
    )
    client = _FakeClient([_FakeParsedResponse(draft)])

    resultat = make_claude_composer(client=client, max_attempts=3)(article)

    assert "Tsugi" not in resultat.caption
    assert resultat.caption.endswith("vendredi ✨.")


# --- Une enumeration se surligne en entier, ou pas du tout -----------------


def _draft_accroche(headline):
    return PostDraft(
        variantes=[
            CoverVariante(headline=headline, title="**Titre**", subtitle="**Sous**"),
            *[_variante(i) for i in range(3)],
        ],
        caption="Une legende 🎧 correcte ✨",
        hashtags=[],
    )


def test_probleme_enumeration_decrit_l_oubli_pour_la_relance():
    probleme = probleme_enumeration_surlignee(
        "**Angele**, **Caroline Polachek** et SebastiAn s'unissent"
    )

    assert "SebastiAn" in probleme
    assert "TOUS les elements ou AUCUN" in probleme


def test_probleme_enumeration_laisse_passer_le_double_surlignage_legitime():
    assert probleme_enumeration_surlignee("**Bicep** referme le triptyque **AIR**") is None


def test_composer_relance_une_enumeration_a_moitie_surlignee(db_session):
    """Meme mecanique que les emojis et la cloture : on relance avec le probleme
    decrit, on ne jette pas le brouillon."""
    article = _article(db_session)
    bancale = _draft_accroche("**Angele**, **Caroline Polachek** et SebastiAn s'unissent")
    propre = _draft_accroche("**Angele**, **Caroline Polachek** et **SebastiAn** s'unissent")
    client = _FakeClient([_FakeParsedResponse(bancale), _FakeParsedResponse(propre)])
    composer = make_claude_composer(client=client, max_attempts=3)

    assert composer(article) is propre
    assert len(client.messages.appels) == 2
    assert "SebastiAn" in _contenu_utilisateur(client.messages.appels[1])


def test_composer_accepte_une_enumeration_bancale_plutot_que_de_perdre_le_creneau(db_session):
    """Un controle de forme ne fait JAMAIS perdre une composition -- la lecon des
    legendes sans emoji (articles 17930 et 18078)."""
    article = _article(db_session)
    bancale = _draft_accroche("**Angele**, **Caroline Polachek** et SebastiAn s'unissent")
    client = _FakeClient([_FakeParsedResponse(bancale)] * 3)
    composer = make_claude_composer(client=client, max_attempts=3)

    assert composer(article) is bancale
    assert len(client.messages.appels) == 3


def test_le_prompt_demande_une_enumeration_coherente():
    assert "TOUS ou \\\nAUCUN" in SYSTEM_PROMPT or "TOUS ou AUCUN" in SYSTEM_PROMPT.replace("\n", " ")


# --- Le post 168 : la cover desaccentuee que la legende cachait ------------
#
# Publie le 2026-10-03 a 21h01. Bande de titre du reel : « Troye Sivan /
# invite surprise de Charli XCX a l'ACL » -- « invite » pour « invité », « a »
# pour « à », dans le texte le plus visible du post. Sa legende, elle, etait
# parfaitement accentuee : `probleme_accents` ne voyant qu'elle, aucune
# relance n'a ete declenchee et la cover est partie telle quelle.

_LEGENDE_168 = (
    "Troye Sivan a fait une apparition surprise pendant le set de Charli XCX à "
    "l'Austin City Limits, pour interpréter deux titres tirés de son prochain "
    "album. 🎧\n\nCes morceaux sont extraits de She's The Best, qui sort la "
    "semaine prochaine. Le duo sur scène confirme une complicité qui dépasse le "
    "simple featuring. ✨\n\nOn attend déjà l'album, avec en tête ces deux "
    "premiers extraits joués live à Austin."
)
# Les quatre variantes telles qu'elles sont en base.
_VARIANTES_168 = [
    (
        "**Troye Sivan** surprise **Charli XCX** sur scene a l'ACL pour deux titres",
        "**Troye Sivan**",
        "invite surprise de **Charli XCX** a l'ACL",
    ),
    (
        "Deux titres de **She's The Best** en live avant la sortie de l'album",
        "**She's The Best**",
        "deux titres joues en avant-premiere a l'**ACL**",
    ),
    (
        "**Austin City Limits** accueille un duo inattendu avec **Charli XCX**",
        "**Austin City Limits**",
        "**Troye Sivan** monte sur scene avec Charli XCX",
    ),
    (
        "L'album de **Troye Sivan** arrive, un avant-gout livre a l'**ACL**",
        "**Troye Sivan**",
        "un avant-gout de son album, **une semaine avant** la sortie",
    ),
]


def _draft_168(textes=None):
    return PostDraft(
        variantes=[
            CoverVariante(headline=h, title=t, subtitle=s)
            for h, t, s in (textes or _VARIANTES_168)
        ],
        caption=_LEGENDE_168,
        hashtags=[],
    )


def test_la_legende_du_post_168_passe_tous_les_controles_de_legende():
    """Le point exact du trou : rien dans la LEGENDE n'etait a redire."""
    assert probleme_de_legende(_LEGENDE_168) is None


def test_le_post_168_est_desormais_signale_par_ses_textes_de_cover():
    probleme = probleme_de_variantes(_draft_168())
    assert probleme is not None
    assert "scene" in probleme and "scène" in probleme


def test_un_brouillon_bien_accentue_ne_declenche_rien():
    """Les memes textes, accentues : aucun signalement, aucune relance."""
    corriges = [
        (
            "**Troye Sivan** surprise **Charli XCX** sur scène à l'ACL pour deux titres",
            "**Troye Sivan**",
            "invité surprise de **Charli XCX** à l'ACL",
        ),
        (
            "Deux titres de **She's The Best** en live avant la sortie de l'album",
            "**She's The Best**",
            "deux titres joués en avant-première à l'**ACL**",
        ),
        (
            "**Austin City Limits** accueille un duo inattendu avec **Charli XCX**",
            "**Austin City Limits**",
            "**Troye Sivan** monte sur scène avec Charli XCX",
        ),
        (
            "L'album de **Troye Sivan** arrive, un avant-goût livré à l'**ACL**",
            "**Troye Sivan**",
            "un avant-goût de son album, **une semaine avant** la sortie",
        ),
    ]
    assert probleme_de_variantes(_draft_168(corriges)) is None


def test_composer_relance_une_cover_desaccentuee_puis_accepte(db_session):
    """Non bloquant, comme les emojis et la cloture : on relance une fois, et si
    le modele ne fait pas mieux on accepte avec un avertissement plutot que de
    perdre la composition d'un creneau."""
    article = _article(db_session)
    client = _FakeClient([_FakeParsedResponse(_draft_168())] * 3)

    composer = make_claude_composer(client=client)
    draft = composer(article)

    assert len(client.messages.appels) == 3  # relance, relance, puis acceptation
    relance = _contenu_utilisateur(client.messages.appels[1])
    assert "scène" in relance
    assert draft.caption == _LEGENDE_168


def test_la_relance_nomme_les_textes_rendus_et_non_la_legende():
    """Le message doit dire OU corriger : trois posts du 2026-09-18 avaient deja
    montre qu'une consigne qui vise `caption` ne deplace pas les covers."""
    probleme = probleme_de_variantes(_draft_168())
    assert "headline" in probleme and "title" in probleme and "subtitle" in probleme
