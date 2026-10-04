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
    # La SANTE, tiree de `sources.state` (ecrit a chaque ingestion par
    # `app/pipeline/health.py::marquer_sante`) : elle existait depuis le debut et
    # n'etait exposee par rien -- ni l'API, ni l'ecran. Une source morte depuis
    # trois semaines etait donc indetectable depuis le dashboard, alors que la
    # trace etait en base. Ce ne sont pas des colonnes : l'endpoint les extrait
    # (`app/api/sources.py::vue`), d'ou des defauts a None.
    last_error: str | None = None
    last_error_at: dt.datetime | None = None
    last_ok_at: dt.datetime | None = None


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
    # L'EXCLUSION DE LA FILE, telle que l'ecrivent le dashboard (« saute »), le
    # gel, la composition et la dedup d'archive. Elle etait en base depuis le
    # debut et exposee par rien : la grille ne pouvait donc ni montrer qu'un
    # article est hors file, ni dire pourquoi -- alors que c'est le champ qui
    # refuse l'article PARTOUT (`store._articles_publiables`).
    file_exclu_raison: str | None = None

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
    # Pas de `tracked` : la colonne est tombee avec la migration 0021 (ecrite
    # par l'API, lue par personne -- voir `app/models.py`, classe `Artist`).
    source_instagram_id: int | None = None


class SignalArtisteOut(BaseModel):
    """Un artiste de la shortlist A&R, avec les chiffres qui justifient son rang.

    Le classement ne tient plus au VOLUME de citations (il remontait les stars)
    ni a `max_emergence` seul (un cliquet qu'une seule note de parrainage
    remplissait) : il tient a l'emergence MOYENNE des citations principales de
    la fenetre. Les trois mesures sont rendues pour qu'un panneau puisse trier
    autrement sans redemander la base.
    """

    model_config = ConfigDict(from_attributes=True)

    artist: ArtistOut
    citations: int
    citations_principales: int
    emergence_moyenne: float | None
    emergence_max_principale: int | None
    velocite: float | None
    acceleration: float | None
    deja_suivi: bool


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
    # `None` = inconnu (citation anterieure a la migration 0020), lu comme
    # PRINCIPAL partout : la regle du doute.
    principal: bool | None = None


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
    # La PROVENANCE, portee par la slide depuis que la liste publiee est editable
    # (`PostUpdate.carrousel_explicite`). Sans ces deux champs, une liste reecrite
    # a la main perdrait l'origine (`article` | `artiste`) et le compte credite de
    # chaque visuel : le manifeste du gel annoncerait alors « photo de l'actu » sur
    # une photo pechee chez un artiste suivi. Facultatifs -- les slides posees
    # avant cette tache n'en ont pas, et « inconnu » est une reponse honnete.
    origine: str | None = None
    compte: str | None = None


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
    # L'utilisateur a ARRETE la liste des elements : a partir de la, le gel ne
    # reprend plus ni `posts.assets` ni les medias de l'article -- `slides` fait
    # foi (`preparation.liste_explicite`). Sans ce marqueur, retirer un element de
    # la liste finale n'aurait aucun effet : le gel suivant le reprendrait, et
    # l'edition serait annulee en silence.
    #
    # Un BOOLEEN et non un simple effet de bord de `slides` : les cases a cocher
    # « Medias de l'actu » ecrivent `slides` elles aussi, mais sur le BROUILLON --
    # leur donner le sens « liste definitive » changerait leur comportement sans
    # que personne ne l'ait demande.
    carrousel_explicite: bool | None = None

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
    # L'URL de l'actu, pour que « article #123 » soit un LIEN et non un
    # identifiant a recopier. Zero cout : `_vue` charge deja l'article pour en
    # tirer le titre et la pertinence. `None` quand l'article a ete purge --
    # le creneau est autonome de son article (2026-09-30), le renvoi redevient
    # alors du texte inerte plutot qu'un lien mort.
    article_url: str | None = None
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
    # Le post Kinda tire de cet article, s'il existe : une observation disait
    # ce que la presse a publie, jamais ce que Kinda en a fait. Rempli par
    # `GET /events/{id}` en UNE requete groupee (jamais une par observation),
    # et `None` quand rien n'a ete compose -- ou quand l'article est purge.
    post_id: int | None = None


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
    # Phase 2C -- toutes NULLABLES, et un `null` ne veut JAMAIS dire zero : il
    # veut dire « pas assez de matiere pour mesurer ». 85 % des evenements
    # n'ont qu'une preuve, donc pas de velocite ; 92 % en ont moins de trois,
    # donc pas d'acceleration. Les confondre avec 0 est exactement ce qui
    # rendrait faux le classement de 2D sans qu'on le voie.
    velocity: float | None
    acceleration: float | None
    nouveaute: float | None
    # 100 = marqueur France dans le titre, 70 = compte primaire francais,
    # 0 = aucun marqueur TROUVE (jamais « prouve etranger » : le rappel mesure
    # est d'environ 2/3). Voir `event_features.pertinence_france`.
    pertinence_france: float | None
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


class VoyantOut(BaseModel):
    """Un voyant du bandeau de supervision.

    `niveau` vaut `ok`, `attention`, `panne` ou `inconnu` -- et `inconnu` ne se
    degrade JAMAIS en `ok` : une valeur qu'on n'a pas pu lire peut cacher une
    panne (voir `app/supervision.py`). `raison` est un texte affichable a cote
    du voyant, jamais seulement dans une infobulle.
    """

    model_config = ConfigDict(from_attributes=True)

    cle: str
    libelle: str
    niveau: Literal["ok", "attention", "panne", "inconnu"]
    valeur: str
    raison: str | None = None
    details: list[str] = []


class SupervisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    niveau: Literal["ok", "attention", "panne", "inconnu"]
    releve_le: dt.datetime
    voyants: list[VoyantOut]


class ReglagesOut(BaseModel):
    """Les reglages que l'INTERFACE doit dire a l'utilisateur.

    Trois textes du dashboard annoncaient des valeurs ecrites en dur, et deux
    etaient devenus faux : « toutes les 2 h » (l'agent est horaire depuis le
    2026-08-15) et « 14 jours de retention » (`retention_days` vaut 2 depuis le
    2026-09-21, donc on envoyait chercher un article purge douze jours plus
    tot). Un texte d'interface qui recopie un reglage ne reste vrai que par
    chance : il est desormais DERIVE de la configuration.

    Rien ici ne coute un appel reseau ni une requete : ce sont les champs de
    `settings`, deja charges en memoire.
    """

    model_config = ConfigDict(from_attributes=True)

    retention_days: int
    pipeline_intervalle_s: int


class DepartOut(BaseModel):
    """CE QUI VA REELLEMENT PARTIR sur un creneau, et non ce qui est en base.

    L'audit UX du 2026-10-02 l'avait releve et personne ne l'avait comble : la
    legende EDITEE n'est pas la legende PUBLIEE. `app/content/legende.py::
    texte_legende` -- le seul goulot que traversent la publication automatique,
    l'export zip et `tester_publication` -- aere les paragraphes, remet le diese
    sur les hashtags, les deduplique, les plafonne a 30, ajoute la ligne de
    credit obligatoire et tronque l'ensemble a 2150 points de code. Et
    `posts.credit` n'etait affiche NULLE PART. Un utilisateur qui relit avant
    publication relit donc ce champ-ci, pas `posts.caption`.

    Regle du bandeau de supervision, appliquee telle quelle : une valeur qu'on
    n'a pas pu lire vaut INCONNUE, jamais « tout va bien ». `deborde` est
    `None` tant qu'aucun rendu n'a mesure la cover -- un `false` par defaut
    serait un faux feu vert sur le seul signal qui doit empecher une
    publication en defaut.
    """

    model_config = ConfigDict(from_attributes=True)

    creneau_id: int
    publier_a: dt.datetime
    format: FormatCreneau
    etat: EtatCreneau
    post_id: int
    accroche: str
    article_title: str | None = None
    article_url: str | None = None
    # Le texte exact, apres `texte_legende` : c'est lui qui part chez Meta.
    legende_publiee: str
    # Mesures contre les limites de Meta, pour qu'un depassement se VOIE avant
    # la troncature silencieuse plutot qu'apres coup sur le compte.
    legende_longueur: int
    legende_maximum: int
    hashtags_publies: list[str]
    hashtags_maximum: int
    # Obligatoire sur un reel, jamais coupe de la legende. `None` = aucun compte
    # a crediter (photo de presse, image du web).
    credit: str | None = None
    # Le fond de la cover et son origine (`variants.fond.origine`), figes a la
    # composition : une fois l'URL dans la slide, plus rien ne dirait d'ou elle
    # vient.
    fond: str | None = None
    fond_origine: str | None = None
    # Nombre d'elements du carrousel une fois geles (cover comprise). `None`
    # avant le gel : on ne devine pas ce que le gel va ecarter.
    elements_figes: int | None = None
    # LE MANIFESTE du gel (`creneaux.elements`, migration 0022) : la liste
    # ordonnee de ce qui part -- cover comprise, medias de l'actu compris --, plus
    # les elements ECARTES avec leur raison. C'est la reponse a « qu'est-ce qui va
    # etre poste exactement », que personne ne pouvait obtenir : le post 164
    # affichait 1 slide quand Instagram en recevait 3.
    #
    # `None` = pas encore gele, ou creneau gele avant la migration : inconnu,
    # jamais une liste reconstituee. Un recalcul ne retomberait pas dessus (27 des
    # 48 carrousels geles mesures le 2026-10-04) et le pourrait d'autant moins que
    # l'article est purge a deux jours.
    elements: list[dict] | None = None
    # Ce que l'editeur propose en remplacement ou en ajout : tout visuel
    # disponible et AUTORISE pour ce post (actifs retenus a la composition +
    # medias de l'actu quand elle est encore en base). Le dashboard n'a ainsi pas
    # a recroiser deux reponses pour savoir ce qu'il peut offrir.
    candidats: list[dict] = Field(default_factory=list)
    # La liste a-t-elle ete ARRETEE a la main ? Si oui, le gel ne reprend plus
    # ni les actifs ni les medias de l'actu : les slides font foi.
    liste_arretee: bool = False
    # La cover deborde-t-elle ? `None` = aucun rendu ne l'a mesure (inconnu).
    deborde: bool | None = None
    # Editable maintenant ? Et, sinon, POURQUOI -- un controle desactive
    # imprime toujours sa raison (regle de la refonte du 2026-10-02).
    editable: bool
    raison_non_editable: str | None = None
    # Rouvrir un creneau gele est possible tant qu'il reste de quoi re-geler.
    reouvrable: bool
    raison_non_reouvrable: str | None = None
    minutes_restantes: int
