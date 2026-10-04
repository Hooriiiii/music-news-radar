import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# JSONB en prod (Postgres), JSON générique pour les tests SQLite.
# none_as_null : sans lui, un None Python devient la VALEUR json 'null'
# (pas un NULL SQL) et les filtres IS NOT NULL ne filtrent plus rien
JSONVariant = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class SourceType(str, enum.Enum):
    RSS = "rss"
    REDDIT_RSS = "reddit_rss"
    BLUESKY = "bluesky"
    X = "x"
    INSTAGRAM = "instagram"


class SourceCategorie(str, enum.Enum):
    """Qui PRODUIT le contenu d'une source -- pas son format (c'est `SourceType`).

    Le scoring s'en sert : un post d'artiste est une actu primaire, un post de
    média est déjà un repackaging que Kinda ne peut pas reposter sans plagier.
    NULL = inconnu (sources X, ou pas encore classée).
    """

    ARTISTE = "artiste"
    MEDIA = "media"
    FESTIVAL = "festival"
    LABEL = "label"
    CLUB = "club"
    AUTRE = "autre"


class ArticleStatus(str, enum.Enum):
    NEW = "new"
    REVIEWED = "reviewed"
    USED = "used"


class ScoredBy(str, enum.Enum):
    """Quel étage de la cascade a produit les notes d'un article.

    Sans cette trace, impossible de savoir a posteriori quels articles n'ont eu
    que le traitement au rabais du modèle local — et le dashboard ne peut pas les
    distinguer de ceux jugés par Claude.
    """

    CLAUDE = "claude"
    LOCAL = "local"


class PostStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"


class EtatCreneau(str, enum.Enum):
    """Ou en est un creneau de publication.

    LIBRE et PROGRAMME bougent encore au gre de l'arbitrage. Tous les autres sont
    definitifs : GELE (fichiers prets, plus aucun remplacement), PUBLIE, APERCU
    (conteneur cree chez Meta mais non publie, mode apercu), ECHEC (la
    publication a ete tentee et refusee) et MANQUE (l'heure est passee de plus de
    90 minutes sans publication, typiquement le Mac endormi).
    """

    LIBRE = "libre"
    PROGRAMME = "programme"
    GELE = "gele"
    PUBLIE = "publie"
    APERCU = "apercu"
    ECHEC = "echec"
    MANQUE = "manque"


class SourceRole(str, enum.Enum):
    """Le role d'une source dans une observation d'evenement (Phase 2A).

    Calcule au moment de l'observation depuis `sources.categorie` : PRIMARY si le
    compte est artiste/festival/label/club (l'annonce vient de la source du fait),
    SECONDARY si c'est un media (repackaging), UNKNOWN sinon (categorie NULL, ou
    source X/Reddit non classee).
    """

    PRIMARY = "primary"
    SECONDARY = "secondary"
    UNKNOWN = "unknown"


class FormatCreneau(str, enum.Enum):
    """Ce qu'un creneau publie : un carrousel d'images ou un reel video.

    Le cycle de vie (LIBRE -> ... -> PUBLIE) est le meme ; seuls changent le
    gabarit compose, le preparateur du gel et le publieur.
    """

    CARROUSEL = "carrousel"
    REEL = "reel"


def _values(enum_cls):
    return [m.value for m in enum_cls]


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type", native_enum=False, length=20, values_callable=_values)
    )
    url: Mapped[str] = mapped_column(String(1000))
    genre: Mapped[str | None] = mapped_column(String(100))
    # Artiste / média / festival... : pondère le scoring (cf. SYSTEM_PROMPT)
    categorie: Mapped[SourceCategorie | None] = mapped_column(
        Enum(
            SourceCategorie, name="source_categorie", native_enum=False, length=20,
            values_callable=_values,
        )
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # État propre à l'adapter (ex. X/Apify : since, last_run_at) -- réassigner un
    # dict complet pour que SQLAlchemy détecte le changement, pas de mutation in place
    state: Mapped[dict | None] = mapped_column(JSONVariant)
    # Phase 2A (evenements) : deux sources qui partagent ce groupe comptent pour
    # UNE seule source independante dans `independent_source_count` -- l'exemple
    # du blueprint est une depeche AFP reprise par trois sites. Nullable et vide
    # pour l'instant : personne ne la remplit encore, seule la structure existe.
    # Pas de FK : ce n'est pas une reference a une autre source, juste une cle de
    # regroupement partagee entre plusieurs lignes de `sources`.
    source_group_id: Mapped[int | None] = mapped_column(Integer, index=True)

    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    url: Mapped[str] = mapped_column(String(2000))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    relevance_score: Mapped[int | None] = mapped_column(Integer)
    hotness_score: Mapped[int | None] = mapped_column(Integer)
    # Axe A&R : intérêt "jeune artiste à suivre", indépendant de relevance
    emergence_score: Mapped[int | None] = mapped_column(Integer, index=True)
    # Étage de la cascade qui a produit ces notes : "claude" (le juge) ou
    # "local" (article écarté par le pré-filtre, jamais soumis à Claude).
    # Null = pas encore scoré.
    scored_by: Mapped[ScoredBy | None] = mapped_column(
        Enum(
            ScoredBy, name="scored_by", native_enum=False, length=20,
            values_callable=_values,
        )
    )
    category: Mapped[str | None] = mapped_column(String(100))
    imprint: Mapped[str | None] = mapped_column(String(200))
    genre: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[ArticleStatus] = mapped_column(
        Enum(
            ArticleStatus, name="article_status", native_enum=False, length=20,
            values_callable=_values,
        ),
        default=ArticleStatus.NEW,
        server_default="new",
        index=True,
    )
    raw_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Medias TYPES (photo / video / gif) avec URL exploitable, duree et dimensions.
    # Remplace l'ancienne `media_urls: list[str]`, dans laquelle un tweet video
    # stockait sa VIGNETTE -- indistinguable d'une photo, donc le format REELS
    # etait indecidable. Reassigner la liste ENTIERE, jamais de mutation in place.
    media: Mapped[list | None] = mapped_column(JSONVariant)
    # Horodatage de l'alerte Discord envoyée (null = jamais alerté) -- garantit
    # l'idempotence des runs fréquents de détection du hot
    alerted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Artistes mentionnés, extraits par le scoring -- alimentent le radar maison
    mentioned_artists: Mapped[list | None] = mapped_column(JSONVariant)
    # Même actu déjà couverte par une autre source : pointe vers le représentant
    # (l'article vu le plus tôt). Null = c'est lui, le représentant. Jamais de
    # chaîne : un doublon pointe toujours vers un article dont ce champ est null.
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    # Raison pour laquelle l'article ne doit plus etre propose a la file de
    # publication : composition ou gel impossible, ou « sauter » depuis le
    # dashboard. Null = eligible. Sans cette trace, un article en defaut
    # reviendrait a chaque passage de l'agent, toutes les 10 minutes.
    file_exclu_raison: Mapped[str | None] = mapped_column(Text)

    source: Mapped[Source] = relationship(back_populates="articles")


class Artist(Base):
    """Un artiste repéré dans le flux. C'est l'ACTIF DURABLE de Kinda : les articles
    sont purgés à 14 jours, les artistes et leurs mentions ne le sont jamais. La
    courbe de citation d'un artiste dans le temps est le deal-flow du label."""

    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Forme normalisée (minuscules, sans accents) : la clé d'unicité, pour que
    # "Peggy Gou" et "peggy gou" ne fassent pas deux fiches
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    mentions_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Meilleur score d'émergence jamais vu : un artiste repéré une fois comme très
    # prometteur le reste, même si les mentions suivantes sont anodines
    max_emergence: Mapped[int | None] = mapped_column(Integer, index=True)
    # Il n'y a PAS de colonne « suivi manuel » ici, et c'est une decision :
    # `tracked` a vecu de la migration 0007 a la 0021, ecrite par
    # `PATCH /artists/{id}` et lue par PERSONNE. Ce que le pipeline appelle
    # « artiste suivi » -- l'exception au pre-filtre (`scoring.ContexteScoring`)
    # et la reserve de visuels (`content/choix_visuels.py`) -- est
    # `comptes_artistes.IndexComptes`, c'est-a-dire les sources Instagram ACTIVES
    # de categorie artiste, jamais un booleen sur la fiche. La colonne morte a
    # deja coute une fausse alerte relayee a l'utilisateur
    # (`scripts/etat_notion.py` concluait « 0 artiste suivi, les chemins qui en
    # dependent ne se declenchent jamais » alors que 88 comptes tournaient).
    # Le lien UTILE est celui-ci, juste en dessous.
    # Le compte Instagram suivi de cet artiste, quand le nom ne suffit pas a le
    # retrouver (« Guy-Man » et son compte). Pose automatiquement par
    # `lier_artistes_comptes`, corrigeable au dashboard (PATCH /artists/{id}).
    source_instagram_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )

    mentions: Mapped[list["ArtistMention"]] = relationship(back_populates="artist")


class ArtistMention(Base):
    """Une citation d'un artiste dans un article, à une date donnée.

    Les champs sont DÉNORMALISÉS (titre, url, scores) exprès : l'article sera purgé,
    la mention doit rester lisible sans lui.
    """

    __tablename__ = "artist_mentions"

    id: Mapped[int] = mapped_column(primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id"), index=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    mentioned_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    relevance_score: Mapped[int | None] = mapped_column(Integer)
    emergence_score: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(2000))
    # Cette citation porte-t-elle l'actu, ou l'artiste n'est-il qu'un nom de
    # passage ? La note d'emergence de l'article ne vaut pour l'artiste que dans
    # le premier cas (voir `artists.est_citation_principale`). NULL = inconnu
    # (citations anterieures a la migration 0020) et vaut PRINCIPAL a la lecture :
    # la regle du doute, une vraie decouverte ne doit pas etre degradee a tort.
    principal: Mapped[bool | None] = mapped_column(Boolean)

    artist: Mapped[Artist] = relationship(back_populates="mentions")


class Event(Base):
    """Une actualite reelle, telle que racontee par plusieurs preuves (Phase 2A).

    Le systeme raisonnait POST par POST : la meme annonce vue chez l'artiste, chez
    son label, dans la presse et sur Reddit produisait des doublons jetes par la
    dedup. `Event` est la couche qui rend ce regroupement explicite et mesurable --
    voir `docs/superpowers/plans/2026-09-23-phase-2a-evenements.md`. Cette table
    n'est encore alimentee par rien (Task 2) : la structure existe seule ici.

    Les compteurs sont DENORMALISES mais toujours RECALCULABLES depuis les
    `EventObservation` -- jamais une valeur devinee ou incrementee a l'aveugle.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    titre: Mapped[str] = mapped_column(Text)
    # Taxonomie a part (release_announcement, line_up...) -- NULL tant qu'elle
    # n'est pas ecrite, voir la note du plan Phase 2A.
    event_type: Mapped[str | None] = mapped_column(String(100))
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    platform_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Respecte `sources.source_group_id` : deux sources du meme groupe ne comptent
    # que pour une seule source independante (depeche AFP reprise trois fois).
    independent_source_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    observations: Mapped[list["EventObservation"]] = relationship(back_populates="event")


class EventObservation(Base):
    """Une preuve : un article rattache a un `Event` (Phase 2A).

    Meme philosophie qu'`ArtistMention` : les champs utiles aux features sont
    DENORMALISES (titre, url, nom de source) et le FK vers l'article est
    `ON DELETE SET NULL`, pour que la purge (2 jours, par date de post) casse le
    lien vers l'article sans detruire la preuve elle-meme -- c'est cette archive
    qui rend l'evenement mesurable au-dela de la fenetre de retention.
    """

    __tablename__ = "event_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    # `sources.type` fige au moment de l'observation (pas relu depuis `sources`,
    # qui peut changer ou disparaitre) -- meme enum que `SourceType`.
    platform: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="platform", native_enum=False, length=20, values_callable=_values)
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    # PRIMARY si la source est artiste/festival/label/club, SECONDARY si media,
    # UNKNOWN sinon (categorie NULL, ou source X/Reddit non classee).
    source_role: Mapped[SourceRole] = mapped_column(
        Enum(SourceRole, name="source_role", native_enum=False, length=20, values_callable=_values)
    )
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    observed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Denormalise : l'article peut etre purge, la preuve doit rester lisible.
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(2000))
    source_name: Mapped[str] = mapped_column(String(200))

    event: Mapped[Event] = relationship(back_populates="observations")

    __table_args__ = (
        # Idempotence du rattachement (Task 2) : rejouer un passage ne doit
        # jamais dupliquer l'observation d'un meme article sur un meme evenement.
        UniqueConstraint("event_id", "article_id", name="uq_event_observations_event_id_article_id"),
    )


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, unique=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    article_ids: Mapped[list] = mapped_column(JSONVariant, default=list)


class Post(Base):
    """Un post Instagram genere a partir d'un article.

    Table separee et non colonnes sur `articles` pour deux raisons : un article
    peut donner plusieurs tentatives de post, et surtout les articles sont purges
    a 14 jours alors qu'un post publie est une archive permanente. Meme motif que
    `artist_mentions` : FK en SET NULL et champs DENORMALISES, pour que le post
    reste lisible apres la purge de son article.
    """

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    # Denormalise : survit a la purge de l'article
    article_title: Mapped[str] = mapped_column(Text)
    article_url: Mapped[str] = mapped_column(String(2000))
    source_name: Mapped[str | None] = mapped_column(String(200))
    # Le compte a crediter en legende (`@compte`), pose a la composition quand la
    # source est Instagram. Denormalise comme `source_name` : le post survit a la
    # purge de l'article, son credit aussi. Obligatoire pour un reel.
    credit: Mapped[str | None] = mapped_column(String(200))
    # Les visuels retenus a la composition, dans l'ordre (le premier fait le fond
    # de cover) : entrees medias completes + origine (article | artiste), compte,
    # permalink, article_id, publie_le, fichier (copie locale). NULL = post
    # anterieur a la regle des visuels (2026-09-17) : on relit alors les medias de
    # l'article, filtres. Reassigner la liste ENTIERE, jamais de mutation in place.
    assets: Mapped[list | None] = mapped_column(JSONVariant)
    status: Mapped[PostStatus] = mapped_column(
        Enum(
            PostStatus, name="post_status", native_enum=False, length=20,
            values_callable=_values,
        ),
        default=PostStatus.DRAFT,
        server_default="draft",
        index=True,
    )
    template: Mapped[str] = mapped_column(
        String(100), default="kinda_post", server_default="kinda_post"
    )
    caption: Mapped[str] = mapped_column(Text)
    hashtags: Mapped[list | None] = mapped_column(JSONVariant)
    # Liste ordonnee : [{"kind": "cover", "headline": ..., "title": ..., "subtitle":
    # ..., "image_url": ...}, ...]. "cover" est rendue par Chromium ; "media" (a
    # venir) passe telle quelle a l'export -- c'est une vraie photo/video de
    # l'actu, pas du texte genere. Reassigner la liste ENTIERE, jamais de mutation
    # in place.
    slides: Mapped[list] = mapped_column(JSONVariant, default=list)
    # Les quatre propositions et l'indice de celle que l'utilisateur retient
    # (`retenue: null` tant qu'il n'a pas choisi). Les non retenues sont GARDEES :
    # choisir entre quatre formulations du meme sujet est du signal sur ce qui
    # plait, gratuit a collecter, et exploitable par le tri local a venir.
    # Reassigner le dict ENTIER, jamais de mutation in place.
    variants: Mapped[dict | None] = mapped_column(JSONVariant)
    # Rapport du dernier rendu : par slide, l'echelle retenue, le nombre de lignes
    # et un drapeau de debordement. Nullable : un post jamais rendu n'en a pas.
    # C'est ce qui permet au dashboard de signaler un post en defaut au lieu de
    # laisser publier une image coupee.
    # Reassigner le dict ENTIER, jamais de mutation in place.
    render_report: Mapped[dict | None] = mapped_column(JSONVariant)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Creneau(Base):
    """Une heure de publication, et ce qui doit y partir.

    Table a part et non colonnes sur `posts` : un creneau porte un article bien
    avant qu'un post existe, puisque la composition (appel Claude paye) n'a lieu
    qu'a J-1. `publier_a` est UNIQUE : deux creneaux a la meme heure publieraient
    deux fois.
    """

    __tablename__ = "creneaux"

    id: Mapped[int] = mapped_column(primary_key=True)
    publier_a: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), unique=True, index=True)
    format: Mapped[FormatCreneau] = mapped_column(
        Enum(
            FormatCreneau, name="format_creneau", native_enum=False, length=20,
            values_callable=_values,
        ),
        default=FormatCreneau.CARROUSEL,
        server_default="carrousel",
    )
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id", ondelete="SET NULL"))
    etat: Mapped[EtatCreneau] = mapped_column(
        Enum(
            EtatCreneau, name="etat_creneau", native_enum=False, length=20,
            values_callable=_values,
        ),
        default=EtatCreneau.LIBRE,
        server_default="libre",
        index=True,
    )
    # Epingle depuis le dashboard : l'arbitrage n'y touche plus.
    epingle: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Noms des JPEG figes, dans l'ordre du carrousel. Reassigner la liste ENTIERE.
    fichiers: Mapped[list | None] = mapped_column(JSONVariant)
    ig_media_id: Mapped[str | None] = mapped_column(String(100))
    erreur: Mapped[str | None] = mapped_column(Text)
    gele_le: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    publie_le: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cree_le: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
