"""Persistance des artistes repérés dans le flux — le moteur A&R de Kinda.

Le scoring extrait déjà `mentioned_artists` sur chaque article. Mais l'article vit
2 jours et le radar n'agrège que 7 jours glissants : sans cette couche, Kinda oublie
tout. Or ce qui intéresse un label, ce n'est pas « qui est cité aujourd'hui », c'est
« depuis quand, à quelle fréquence, et est-ce que ça accélère ».

On matérialise donc l'artiste (`Artist`) et chaque citation datée (`ArtistMention`),
avec les champs dénormalisés nécessaires pour rester lisibles après la purge des
articles.

DEUX DÉFAUTS MESURÉS LE 2026-10-03, et ce que chacun coûtait
------------------------------------------------------------
`rising_artists(db, limit=12)` rendait, dans l'ordre : Stray Kids, aespa,
Charli XCX, BTS, Taylor Swift, Ed Sheeran, Madonna — les plus grosses stars du
monde. Un panneau A&R construit là-dessus proposait de signer Taylor Swift.

1. **L'ATTRIBUTION.** La note d'émergence de l'ARTICLE était recopiée
   identiquement sur CHAQUE artiste cité. Mesuré : « Dance Up-And-Comer of the
   Month: Tatyana Jane … Gotten Lessons From Skrillex » donnait 85 à Skrillex,
   « How Latin Mafia Is Breaking Barriers … Up & Comer » donnait 45 à
   Fred again.., « On debut album "P ★ K I", Ahadadream … » donnait 60 à
   Skrillex. Les notes étaient JUSTES — ces articles parlent bien d'artistes
   émergents — mais elles ne valaient pas pour le parrain. Remède :
   `est_citation_principale`, et seules les citations principales font monter
   `Artist.max_emergence`.

   Ce que la mesure dit de la TAILLE de ce défaut, et il faut le dire :
   155 citations sur 5 178 (3 %) de la fenêtre de 30 jours sont détectées
   secondaires, et corriger l'attribution ne change qu'UNE ligne du top 15.
   La raison est structurelle : la purge à 2 jours a déjà effacé l'ordre de
   citation de 4 544 des 5 178 citations de la fenêtre. C'est précisément
   pourquoi le rang doit être copié MAINTENANT, à l'enregistrement : c'est un
   correctif de qualité d'ARCHIVE, qui portera sur les mois à venir, pas un
   correctif de classement.

2. **LE CLASSEMENT**, qui faisait tout le dégât. `rising_artists` triait par
   VOLUME de citations, ce qui remonte mécaniquement les artistes les plus
   couverts — l'inverse de « on commence à en parler ». Et il départageait sur
   `max_emergence`, un cliquet : un seul article de parrainage à 75 marquait
   l'artiste à vie (mesuré : 250 artistes à 60 ou plus, dont 15 avec une
   moyenne sous 20 — BTS, 24 citations, max 75, moyenne 3,1).

   Remède : on classe sur la MOYENNE d'émergence des citations principales de
   la fenêtre. Mesuré sur les vraies données, la tête de liste devient
   Surusinghe, FRÈJUS, G2G, UNIIQU3, Ahadadream, Jennifer Loveless, Eoin DJ —
   zéro star installée.

3. **« DÉJÀ SUIVI » N'EST PAS UNE DÉCOUVERTE.** `Artist.source_instagram_id`
   dit que Kinda suit déjà le compte Instagram de cet artiste (posé par
   `comptes_artistes.lier_artistes_comptes` depuis l'`IndexComptes` — les 88
   comptes actifs de catégorie artiste, ce que le pipeline appelle « artiste
   suivi », et non `Artist.tracked`, qui n'est lu par personne). La question
   « cet artiste mérite-t-il d'être suivi par Kinda ? » est alors DÉJÀ
   RÉPONDUE : ce n'est pas un jugement de notoriété, c'est un fait. Les 91
   fiches liées sont écartées par défaut de la shortlist de découverte
   (`exclure_suivis`). Mesuré : des 409 citations à 60 ou plus, 18 vont à un
   compte déjà suivi, et c'est exactement la population que `min_emergence=60`
   remontait (Skrillex 85, David Guetta 75, Lady Gaga 60, Tiësto 60).

CE QUE LA MESURE A RÉFUTÉ, et qu'il ne faut pas réintroduire : diluer la note
par le NOMBRE d'artistes cités. Les deux articles les plus peuplés de la base
sont « Les dates de nos artistes en octobre ! » (15 artistes, émergence 65) et
une sélection Fip (11 artistes, 45) — des line-ups d'artistes tous réellement
émergents, c'est-à-dire la meilleure matière A&R de la base. Diviser leur note
par 15 enterrerait précisément les découvertes qu'on cherche. Le premier jet
d'`est_citation_principale` faisait ce dégât sans le vouloir, par le RANG
plutôt que par le compte ; la troisième règle de cette fonction est née de
cette mesure.
"""

import datetime as dt
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, Artist, ArtistMention
from app.pipeline.cluster import normalize_title
from app.pipeline.event_features import acceleration, velocity

# Deux citations ne sont pas une courbe. Mesuré le 2026-10-03 sur la fenêtre de
# 30 jours : sans plancher, 2 531 artistes sont candidats et les huit premiers
# sont huit artistes cités UNE seule fois, tous à 75 ex æquo (Jordan Peak,
# samwgirling, RIP Magic, oklou_, …) — du bruit pur. À deux citations, la tête
# est encore dominée par des paires (LinLin 74 sur 2, Chippy Nonstop 70 sur 2),
# la pathologie même que `event_features.velocity` documente et corrige par son
# numérateur `n - 1`. À trois, la tête se stabilise et mélange les niveaux et
# les volumes (Ahadadream, 17 citations à 52, entre dans les cinq premiers).
CITATIONS_MIN_DEFAUT = 3


def slugify(name: str) -> str:
    """Clé d'unicité d'un artiste : minuscules, sans accents, espaces normalisés."""
    folded = unicodedata.normalize("NFKD", name.strip().lower())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


def nomme_dans(nom: str, titre_normalise: str) -> bool:
    """Le nom apparaît-il dans ce titre DÉJÀ NORMALISÉ (et entouré d'espaces) ?

    Public parce que le script de rattrapage en a besoin seul, sans les deux
    autres règles : sur une citation dont l'article est purgé, le titre est la
    seule chose qui reste. Comparé avec `normalize_title`, la
    normalisation de la dédup, et non brut : sans elle « Tiësto » ne se
    reconnaît pas dans « tiesto », et la mesure a montré ce raté exact."""
    cle = normalize_title(nom)
    return bool(cle) and f" {cle} " in titre_normalise


def est_citation_principale(
    nom: str,
    titre: str | None,
    rang: int | None,
    autres_noms: Iterable[str] = (),
) -> bool:
    """Cette citation porte-t-elle l'actu, ou l'artiste n'est-il qu'un nom de passage ?

    Trois règles, en UNION. L'objectif est le RAPPEL des vrais sujets : une
    fausse dégradation coûte une découverte perdue, alors qu'un faux sujet ne
    coûte qu'une note un peu haute, que la moyenne de la fenêtre absorbe.

    1. **Le rang** dans `mentioned_artists` : le modèle nomme le sujet en tête.
       Vérifié sur les 16 articles multi-artistes de la base à émergence 45 ou
       plus : juste 6 fois sur 8 quand l'article a un sujet unique
       (['Shimza', 'Tyla'], ['Ahadadream', 'Skrillex'], ['Latin Mafia',
       'Fred again..'], ['Grumpy', 'D.A. Crimson'], ['Tiësto', 'cielll.wav'],
       ['Ahadadream', 'Capo Lee', …]).
    2. **La présence dans le titre**, qui rattrape les deux ratés du rang, tous
       deux des posts Instagram dont la légende nomme le sujet en dernier
       (['boysnoize', 'skin0nskin', 'dj_gigola', 'gavc0re'] pour « Boiler Room:
       DC through the lens of our host gavc0re », ['Fallen Tape', 'Pal
       Parallax', 'Cwtch'] pour « cwtch______ (LIVE) ★ … »).
    3. **AUCUN autre cité n'est nommé dans le titre**, donc l'article ne
       singularise personne : la note vaut pour tous. C'est le garde-fou SANS
       LEQUEL CETTE FONCTION DÉTRUIRAIT CE QU'ELLE EST CENSÉE PROTÉGER, et il a
       été trouvé en mesurant le plan de rattrapage, pas en raisonnant. Sans
       elle, les deux articles les plus peuplés de la base — « Les dates de nos
       artistes en octobre ! » (15 artistes de techno FR, émergence 65) et une
       sélection Fip (11 artistes, 45) — voyaient 14 et 10 de leurs artistes
       dégradés : des line-ups d'artistes tous réellement émergents, la
       meilleure matière A&R de la base, enterrés par le remède. Un titre qui ne
       nomme personne (une légende Instagram, « Les dates de nos artistes »,
       « Entre DJ sets, expositions et conférences ») ne désigne pas de sujet,
       donc ne relègue personne.

    Ce que cette fonction NE résout PAS, et il faut le savoir : un parrain nommé
    DANS le titre reste principal — « Dance Up-And-Comer of the Month: Tatyana
    Jane … Gotten Lessons From Skrillex » laisse Skrillex à 85, et « Slayyyter
    Reacts to Lady Gaga Using Her Song » laisse Lady Gaga à 60. Ces deux cas
    sont des comptes DÉJÀ SUIVIS, que `rising_artists(exclure_suivis=True)`
    écarte. Les deux étages se complètent, aucun ne prétend suffire.

    `rang is None` (article purgé, rang perdu) ne dit rien contre l'artiste, et
    `autres_noms` est alors inconnu : l'appelant doit traiter l'inconnu comme
    principal — voir `principal_de_citation` et
    `scripts/recalculer_attribution_artistes.py`.
    """
    if rang == 0:
        return True
    titre_normalise = f" {normalize_title(titre or '')} "
    if nomme_dans(nom, titre_normalise):
        return True
    return not any(nomme_dans(autre, titre_normalise) for autre in autres_noms)


def principal_de_citation(mention: ArtistMention) -> bool:
    """La lecture d'une citation : `None` (inconnu) vaut PRINCIPAL.

    Un seul point de lecture pour la règle du doute, partagé par le classement
    et le script de rattrapage — deux copies qui divergeraient feraient
    disparaître des citations de l'un sans l'autre.
    """
    return mention.principal is not False


def record_mentions(db: Session) -> int:
    """Enregistre les citations des articles scorés pas encore traités.

    Renvoie le nombre de citations créées. Idempotent : un article déjà dépouillé
    (présent dans artist_mentions) est ignoré, donc rejouer ne double rien.
    """
    already = select(ArtistMention.article_id).where(ArtistMention.article_id.is_not(None))
    articles = db.scalars(
        select(Article)
        .where(Article.mentioned_artists.is_not(None))
        .where(Article.relevance_score.is_not(None))
        .where(Article.id.not_in(already))
        .order_by(Article.id)
    ).all()

    created = 0
    for article in articles:
        seen_at = article.published_at or article.fetched_at or dt.datetime.now(dt.timezone.utc)
        for rang, raw_name in enumerate(article.mentioned_artists or []):
            name = raw_name.strip()
            slug = slugify(name)
            if not slug:
                continue
            principal = est_citation_principale(
                name,
                article.title,
                rang,
                [autre for i, autre in enumerate(article.mentioned_artists or []) if i != rang],
            )
            artist = db.scalar(select(Artist).where(Artist.slug == slug))
            if artist is None:
                artist = Artist(
                    name=name, slug=slug, first_seen_at=seen_at, last_seen_at=seen_at,
                    mentions_count=0,
                    max_emergence=article.emergence_score if principal else None,
                )
                db.add(artist)
                db.flush()
            artist.mentions_count += 1
            artist.first_seen_at = min(artist.first_seen_at, seen_at)
            artist.last_seen_at = max(artist.last_seen_at, seen_at)
            # Seule une citation PRINCIPALE fait monter le cliquet : c'est tout
            # le correctif d'attribution. Un parrain cité en passant garde sa
            # citation datée — l'archive reste complète — mais pas la note.
            if principal and article.emergence_score is not None:
                artist.max_emergence = max(artist.max_emergence or 0, article.emergence_score)

            db.add(
                ArtistMention(
                    artist_id=artist.id,
                    article_id=article.id,
                    source_id=article.source_id,
                    mentioned_at=seen_at,
                    relevance_score=article.relevance_score,
                    emergence_score=article.emergence_score,
                    title=article.title,
                    url=article.url,
                    principal=principal,
                )
            )
            created += 1
    db.commit()
    return created


@dataclass(frozen=True)
class SignalArtiste:
    """Ce qu'on sait d'un artiste sur la fenêtre, et rien de plus.

    Les chiffres de la question A&R du projet — « depuis quand, à quelle
    fréquence, est-ce que ça accélère » — sont exposés plutôt que fondus dans
    une note unique : un panneau de tri ne peut pas reconstituer ce qu'un score
    composite a écrasé. `velocite` et `acceleration` viennent de
    `app/pipeline/event_features.py`, les formules déjà mesurées et calibrées de
    la couche événements, appliquées ici aux dates de citation plutôt qu'aux
    dates de publication des preuves. Rien n'est réinventé.
    """

    artist: Artist
    citations: int
    citations_principales: int
    emergence_moyenne: float | None
    emergence_max_principale: int | None
    velocite: float | None
    acceleration: float | None
    deja_suivi: bool


def rising_artists(
    db: Session,
    window_days: int = 30,
    min_emergence: int | None = None,
    limit: int = 20,
    citations_min: int = CITATIONS_MIN_DEFAUT,
    exclure_suivis: bool = True,
) -> list[SignalArtiste]:
    """La shortlist A&R : « on commence à en parler », jamais « on en parle beaucoup ».

    Classement par ÉMERGENCE MOYENNE des citations principales de la fenêtre,
    puis par accélération, puis par volume. Le volume n'est plus la clé
    primaire : il remontait mécaniquement les artistes les plus couverts, donc
    les plus célèbres (voir l'en-tête du module).

    `exclure_suivis` écarte les artistes dont Kinda suit déjà le compte
    Instagram : la question « mérite-t-il d'être suivi ? » est déjà répondue
    pour eux. `False` les garde, pour une vue de supervision.

    `min_emergence` filtre sur `Artist.max_emergence`, c'est-à-dire la meilleure
    note d'émergence PRINCIPALE jamais vue — inchangé côté API, mais le cliquet
    ne se remplit plus d'une note de parrainage.
    """
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    stmt = (
        select(
            Artist,
            ArtistMention.mentioned_at,
            ArtistMention.emergence_score,
            ArtistMention.principal,
        )
        .join(ArtistMention, ArtistMention.artist_id == Artist.id)
        .where(ArtistMention.mentioned_at >= since)
    )
    if min_emergence is not None:
        stmt = stmt.where(Artist.max_emergence >= min_emergence)
    if exclure_suivis:
        stmt = stmt.where(Artist.source_instagram_id.is_(None))

    artistes: dict[int, Artist] = {}
    dates: dict[int, list[dt.datetime]] = defaultdict(list)
    notes: dict[int, list[int]] = defaultdict(list)
    totaux: dict[int, int] = defaultdict(int)
    principales: dict[int, int] = defaultdict(int)
    for artist, quand, note, principal in db.execute(stmt):
        artistes[artist.id] = artist
        dates[artist.id].append(quand)
        totaux[artist.id] += 1
        if principal is not False:
            principales[artist.id] += 1
            if note is not None:
                notes[artist.id].append(note)

    signaux = [
        SignalArtiste(
            artist=artist,
            citations=totaux[aid],
            citations_principales=principales[aid],
            emergence_moyenne=(sum(notes[aid]) / len(notes[aid])) if notes[aid] else None,
            emergence_max_principale=max(notes[aid]) if notes[aid] else None,
            velocite=velocity(dates[aid]),
            acceleration=acceleration(dates[aid]),
            deja_suivi=artist.source_instagram_id is not None,
        )
        for aid, artist in artistes.items()
        if totaux[aid] >= citations_min
    ]
    # `None` (aucune note d'émergence sur la fenêtre : 17 % des citations de la
    # base n'en portent aucune) passe DERRIÈRE une moyenne mesurée, jamais
    # devant : ne pas savoir n'est pas une promesse.
    signaux.sort(
        key=lambda s: (
            -(s.emergence_moyenne if s.emergence_moyenne is not None else -1.0),
            -(s.acceleration if s.acceleration is not None else -2.0),
            -s.citations,
            s.artist.id,
        )
    )
    return signaux[:limit]
