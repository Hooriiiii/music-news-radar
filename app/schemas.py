import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.content import layout
from app.content.highlight import validate_headline
from app.models import (
    ArticleStatus,
    EtatCreneau,
    FormatCreneau,
    PostStatus,
    SourceCategorie,
    SourceRole,
    SourceType,
)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: SourceType
    url: str
    genre: str | None
    categorie: SourceCategorie | None
    active: bool


class SourceCreate(BaseModel):
    name: str
    type: SourceType
    url: str
    genre: str | None = None
    categorie: SourceCategorie | None = None


class SourceUpdate(BaseModel):
    name: str | None = None
    genre: str | None = None
    categorie: SourceCategorie | None = None
    active: bool | None = None


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    url: str
    title: str
    summary: str | None
    published_at: dt.datetime | None
    fetched_at: dt.datetime
    relevance_score: int | None
    hotness_score: int | None
    emergence_score: int | None
    category: str | None
    imprint: str | None
    genre: str | None
    status: ArticleStatus
    media: list[dict] | None

    # Calcules par l'API (pas des colonnes) : l'origine du visuel a venir, la
    # raison de son absence, et si les medias propres sont interdits (compte
    # media Instagram).
    visuel: str | None = None
    raison_sans_visuel: str | None = None
    visuels_interdits: bool = False


class ArtistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    mentions_count: int
    max_emergence: int | None
    tracked: bool
    source_instagram_id: int | None = None


class ArtistMentionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    artist_id: int
    article_id: int | None
    source_id: int | None
    mentioned_at: dt.datetime
    relevance_score: int | None
    emergence_score: int | None
    title: str
    url: str


class DigestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    sent_at: dt.datetime | None
    article_ids: list[int]


class CoverIn(BaseModel):
    """La slide 1 : le seul contenu RENDU par Chromium.

    Elle porte les trois textes -- l'accroche du gabarit post et le couple
    titre/sous-titre du gabarit reel -- pour que la bascule POST/REELS n'ait rien a
    redemander a Claude. Une union permissive (un seul modele avec des champs
    optionnels) accepterait une cover sans texte et ferait echouer le rendu plus
    tard chez Chromium -- ici l'echec arrive a la frontiere de l'API, en 422.
    """

    kind: Literal["cover"] = "cover"
    headline: str
    title: str
    subtitle: str
    image_url: str | None = None

    @field_validator("headline", "title", "subtitle")
    @classmethod
    def _balisage_valide(cls, value: str) -> str:
        validate_headline(value)
        return value


class MediaIn(BaseModel):
    """Une photo ou une video de l'actu, publiee TELLE QUELLE.

    Elle ne passe pas par le gabarit : le renderer l'ignore, l'export la telecharge.
    Aucun balisage a valider, il n'y a pas de texte.
    """

    kind: Literal["media"]
    # Vocabulaire ferme sur celui de `app/content/media.py` : `export.py`
    # interpole `type` dans un nom d'entree de zip, un `str` libre y laisserait
    # passer une forme de zip-slip (ex. "../../x") -- attaquable seulement par
    # l'utilisateur local lui-meme, d'ou Minor, mais autant fermer la porte.
    type: Literal["photo", "video", "gif"]
    url: str
    preview_url: str | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None


# `kind` discrimine : Pydantic route chaque entree vers CoverIn ou MediaIn selon
# cette cle, au lieu de tenter les deux modeles dans l'ordre -- une entree dont le
# `kind` ne vaut ni "cover" ni "media" est donc rejetee en 422, a la frontiere de
# l'API. Le defaut "cover" sur CoverIn.kind documente l'intention -- une slide sans
# `kind` EST une cover -- mais une union DISCRIMINEE exige la cle presente pour
# choisir le modele : verifie a la main, une entree qui omet `kind` entierement est
# rejetee en 422 ("Unable to extract tag"), le defaut ne joue pas. C'est le
# comportement voulu ici, a l'ECRITURE : mieux vaut echouer tot que deviner. La
# migration 0011 backfille deja `kind` sur toutes les lignes existantes ; le
# defaut documente seulement l'intention pour un futur lecteur cote renderer.
SlideIn = Annotated[CoverIn | MediaIn, Field(discriminator="kind")]


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int | None
    article_title: str
    article_url: str
    source_name: str | None
    # Les visuels retenus (origine, compte, permalink, copie locale) et le credit
    # qui en decoule. NULL = post anterieur a la regle des visuels (2026-09-17).
    assets: list[dict] | None = None
    credit: str | None = None
    status: PostStatus
    template: str
    caption: str
    hashtags: list[str] | None
    slides: list[SlideIn]
    # Les quatre variantes + l'indice retenu : l'API ne cache pas ce qui est en
    # base, meme si `slides`/`SlideIn` restent la vue etroite en attendant la
    # tache qui expose la selection et le type de gabarit.
    variants: dict | None = None
    render_report: dict | None = None
    created_at: dt.datetime
    published_at: dt.datetime | None


class PostUpdate(BaseModel):
    caption: str | None = None
    hashtags: list[str] | None = None
    slides: list[SlideIn] | None = None
    status: PostStatus | None = None
    # `template` : verifiable ici, sans la base -- `layout.GABARITS` est un
    # registre statique. `variante_retenue` ne l'est pas : ses bornes valides
    # dependent du NOMBRE de variantes du post vise, connu seulement en base.
    # Ce controle-la reste donc dans l'endpoint.
    template: str | None = None
    variante_retenue: int | None = None

    @field_validator("template")
    @classmethod
    def _gabarit_connu(cls, value: str) -> str:
        if value not in layout.GABARITS:
            raise ValueError(f"gabarit inconnu : {value!r} (connus : {sorted(layout.GABARITS)})")
        return value


class CoversOut(BaseModel):
    """Les 4 covers rendues, en data URIs -- pas de fichiers sur disque : sur
    localhost, quatre PNG en base64 pesent ~800 Ko de JSON, instantane et sans
    etat a gerer cote serveur.

    `overflow` porte le meme signal que `render_report`, mais PAR VARIANTE :
    `render_covers` le calcule deja pour chacune (`RenduSlide.overflow`) et le
    jetait avant cette tache. Le dashboard peut donc avertir AVANT que
    l'utilisateur choisisse une variante qui ne tient pas -- et n'a plus besoin
    d'un second lancement de Chromium (POST /render) pour le savoir sur celle
    retenue : c'est la meme mesure, sur le meme texte et le meme fond.
    """

    covers: list[str]
    overflow: list[bool]


class CreneauOut(BaseModel):
    id: int
    publier_a: dt.datetime
    format: FormatCreneau
    etat: EtatCreneau
    epingle: bool
    article_id: int | None
    article_title: str | None = None
    relevance: int | None = None
    post_id: int | None
    ig_media_id: str | None
    erreur: str | None


class CreneauUpdate(BaseModel):
    """Une seule action par appel : le routeur refuse en 422 zero ou plusieurs."""

    epingle: bool | None = None
    article_id: int | None = None
    sauter: bool = False
    decaler: bool = False


class OffreReelsOut(BaseModel):
    """La matiere disponible pour les reels sur la fenetre de fraicheur.
    `directes` : videos que l'API donne ; le reste passe par yt-dlp au gel."""

    avec_video: int
    directes: int
    eligibles: int
    fraicheur_jours: int


class ReelsOut(BaseModel):
    """L'interrupteur des reels (dashboard) et celui de yt-dlp (.env, lecture seule)."""

    actifs: bool
    ytdlp: bool


class ReelsUpdate(BaseModel):
    actifs: bool


class EventObservationOut(BaseModel):
    """Une preuve (Phase 2A, Task 4) -- champs denormalises, lisibles meme si
    `article_id` est devenu NULL apres la purge de l'article."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    article_id: int | None
    platform: SourceType
    source_id: int | None
    source_role: SourceRole
    published_at: dt.datetime | None
    observed_at: dt.datetime
    title: str
    url: str
    source_name: str


class EventOut(BaseModel):
    """Un evenement et ses features deterministes (Task 3), calculees a la
    lecture -- rien de tout ca n'est persiste ni ne pese sur l'arbitrage de
    publication, voir `docs/superpowers/plans/2026-09-23-phase-2a-evenements.md`,
    Task 4."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    titre: str
    event_type: str | None
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    source_count: int
    platform_count: int
    independent_source_count: int
    # Calcule a la lecture (app/pipeline/event_features.py), jamais stocke :
    # voir la note de `corroboration` cote dashboard -- elle mesure le bavardage
    # de la presse, pas la pertinence editoriale de Kinda.
    freshness: float
    corroboration: float
    # Pertinence (`relevance_score`) de l'article REPRESENTANT du groupe --
    # Tour de correction 1 : c'est le chiffre editorial qui doit se lire A COTE
    # de `corroboration`, jamais a sa place (voir app/api/events.py). Nullable :
    # le representant peut avoir ete purge sans que l'evenement disparaisse.
    relevance_representant: int | None


class EventDetailOut(EventOut):
    observations: list[EventObservationOut]


class ModeOut(BaseModel):
    mode: Literal["arret", "apercu", "reel"]
    source: Literal["dashboard", "env"]


class ModeUpdate(BaseModel):
    mode: Literal["arret", "apercu", "reel"]
    confirmation: str | None = None
