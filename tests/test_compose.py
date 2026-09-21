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
    probleme_emojis,
)
from app.content.choix_visuels import ChoixVisuels
from app.content.illustration import Lot
from app.content.media import entree
from app.content.visuels import (
    FOND_KINDA,
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    ORIGINE_COMPTE,
    ORIGINE_KINDA,
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


def test_le_plancher_de_resolution_suit_le_gabarit_publie(db_session, monkeypatch):
    """Post 71 (CLAUDE.md) : une seule og:image, 564x345, agrandie 3,91x pour
    remplir 1080x1350 -- trop petite, elle doit descendre d'un barreau vers le
    fond Kinda plutot que d'etre publiee floue."""
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

    assert post.slides[0]["image_url"] == FOND_KINDA
    assert post.variants["fond"] == {"origine": ORIGINE_KINDA}


def test_une_photo_assez_grande_pour_le_post_mais_pas_pour_le_reel(db_session, monkeypatch):
    """Le reel (1080x1920) est plus exigeant que le post (1080x1350) sur la MEME
    photo : la cible depend du gabarit publie, jamais fixe."""
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

    post_reel = create_post(
        db_session, article, _draft(), template="kinda_reel", visuels=_choix(),
        selecteur_fond=lambda candidats: "https://cdn/moyenne.jpg", verdicts_fond=verdicts,
    )
    assert post_reel.slides[0]["image_url"] == FOND_KINDA


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
    assert post.variants["fond"] == {"origine": ORIGINE_COMPTE}
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
    assert post.variants["fond"] == {"origine": ORIGINE_ARTISTE}


def test_une_affiche_de_reserve_ne_devient_jamais_une_slide(db_session):
    """Une affiche de l'ACTU reste une slide honnete ; une affiche pechee dans
    une reserve n'illustre rien -- on ne la publie ni en fond ni en slide."""
    from app.content.illustration import Verdict

    article = _article(db_session)
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/affiche.jpg"), origine="article")],
        "@transmusicales",
        secours=[Lot(ORIGINE_COMPTE, lambda: ["file:///cache/flyer.jpg"])],
    )

    post = create_post(
        db_session, article, _draft(), visuels=choix,
        selecteur_fond=lambda candidats: "https://cdn/affiche.jpg",
        verdicts_fond=lambda urls: dict.fromkeys(
            urls, Verdict(affiche=True, sujet="autre", note=10)
        ),
    )

    assert post.slides[0]["image_url"] == FOND_KINDA
    assert [a["url"] for a in post.assets] == ["https://cdn/affiche.jpg"]


def test_une_affiche_sans_remplacante_part_sur_le_fond_kinda(db_session):
    """« ya toujours l'affiche en fond !! » : plutot l'aplat sombre du gabarit,
    sur lequel l'accroche reste lisible, qu'une affiche de line-up."""
    from app.content.illustration import Verdict

    article = _article(db_session)
    choix = ChoixVisuels(
        [actif(entree("photo", "https://cdn/affiche.jpg"), origine="article")], "@x"
    )

    post = create_post(
        db_session, article, _draft(), visuels=choix,
        selecteur_fond=lambda candidats: "https://cdn/affiche.jpg",
        verdicts_fond=lambda urls: dict.fromkeys(
            urls, Verdict(affiche=True, sujet="autre", note=10)
        ),
    )

    assert post.slides[0]["image_url"] == FOND_KINDA
    assert post.variants["fond"] == {"origine": ORIGINE_KINDA}


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
    assert post.variants["fond"] == {"origine": ORIGINE_ARTICLE}


# --- Une carte d'annonce par carrousel, jamais sept -----------------------
#
# Post du 2026-09-18 13h10 (programme des Trans Musicales) : les slides 2 a 8
# etaient la carte d'annonce du festival, declinee sept fois dans SA charte.
# Le post devenait leur carrousel avec une cover Kinda posee devant.


def _affiches(*urls, autres=()):
    """Un `ChoixVisuels` dont les `urls` sont des affiches, `autres` des photos."""
    return ChoixVisuels(
        [actif(entree("photo", url), origine="article") for url in (*urls, *autres)],
        "@transmusicales",
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
    post = create_post(
        db_session, _article(db_session), _draft(), visuels=_affiches(*cartes),
        selecteur_fond=lambda candidats: cartes[0],
        verdicts_fond=_juge(set(cartes)),
    )

    # La premiere dans l'ordre des candidats : sur un carrousel d'annonce, la
    # carte de tete porte le message principal (« TOUTE LA PROG REVELEE »).
    assert _urls(post) == [cartes[0]]
    # Le fond, lui, n'est jamais une affiche : tout etait une carte, donc l'aplat.
    assert post.slides[0]["image_url"] == FOND_KINDA


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
    lot borne a trois, quatre cartes restaient inconnues, donc gardees."""
    from app.config import settings
    from app.content import illustration

    monkeypatch.setattr(settings, "illustration_cache_path", tmp_path / "verdicts.json")
    monkeypatch.setattr(settings, "illustration_vision_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(illustration, "_octets", lambda url: b"jpeg")
    monkeypatch.setattr(illustration, "reduire", lambda octets, taille=None: octets)
    lots = []

    def juge(images):
        lots.append(len(images))
        return [illustration.Verdict(affiche=True, sujet="autre", note=10)] * len(images)

    monkeypatch.setattr(illustration, "make_claude_juge", lambda: juge)
    cartes = [f"https://cdn/trans-{n}.jpg" for n in range(7)]

    post = create_post(db_session, _article(db_session), _draft(), visuels=_affiches(*cartes))

    assert lots == [7], "un seul appel, et il porte sur les sept cartes"
    assert _urls(post) == [cartes[0]]
    assert post.slides[0]["image_url"] == FOND_KINDA


def test_l_unique_visuel_meme_affiche_garde_son_post_publiable(db_session):
    """« Jamais un post reduit a sa cover » : le plafond ne doit pas vider un
    carrousel qui n'avait qu'une carte -- elle reste, le post part a deux
    elements (cf. `app/publish/preparation.py`)."""
    from app.publish.preparation import medias_du_carrousel

    article = _article(db_session)
    carte = "https://cdn/prog.jpg"
    post = create_post(
        db_session, article, _draft(), visuels=_affiches(carte),
        selecteur_fond=lambda candidats: carte,
        verdicts_fond=_juge({carte}),
    )

    assert _urls(post) == [carte]
    assert post.slides[0]["image_url"] == FOND_KINDA
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
