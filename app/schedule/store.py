"""Le lien entre l'arbitrage, en Python pur, et la base.

Trois gestes, pour chaque format : tenir a jour les creneaux futurs, lister les
articles publiables, et ecrire le resultat de l'arbitrage. Toute date lue en base
passe par `utc()` : SQLite rend des datetime naifs, et comparer un naif a un
aware leve une erreur.

Deux formats, une regle : un article n'occupe JAMAIS deux creneaux. Les reels
sont arbitres d'abord -- la matiere video est rare, la matiere photo abondante --
et les carrousels piochent dans le reste.
"""

import datetime as dt

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, object_session, selectinload

from app.config import settings
from app.content.actifs_artistes import classer, duree_min_reel_ms, posts_en_base
from app.content.credit import credit_video
from app.content.media import video_recuperable, videos_exploitables
from app.content.visuels import (
    ORIGINE_ARTICLE,
    ORIGINE_ARTISTE,
    a_une_image_autorisee,
    origine_interdite,
    raison_sans_artiste,
)
from app.models import (
    Article, ArticleStatus, Creneau, EtatCreneau, FormatCreneau, Post, PostStatus, Source, SourceType,
)
from app.pipeline.comptes_artistes import CompteArtiste, IndexComptes, construire_index
from app.sources.instagram import compte_depuis_url
from app.publish.mode import reels_actifs
from app.schedule.pause_visuels import articles_en_pause
from app.schedule.queue import Candidat, Place, arbitrer
from app.schedule.slots import FenetresInvalides, prochains_horaires

# Un creneau dans l'un de ces etats ne bouge plus.
VERROUILLES = frozenset({
    EtatCreneau.GELE, EtatCreneau.PUBLIE, EtatCreneau.APERCU, EtatCreneau.ECHEC, EtatCreneau.MANQUE,
})
MODIFIABLES = frozenset({EtatCreneau.LIBRE, EtatCreneau.PROGRAMME})

TEMPLATES = {FormatCreneau.CARROUSEL: "kinda_post", FormatCreneau.REEL: "kinda_reel"}
ORDRE_ARBITRAGE = (FormatCreneau.REEL, FormatCreneau.CARROUSEL)
# Les videos sont rares dans le haut du classement : on lit plus loin avant le
# filtre Python pour ne pas rater un reel classe 70e.
LIMITE_CARROUSEL = 60
LIMITE_REEL = 300


def utc(moment: dt.datetime | None) -> dt.datetime | None:
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.timezone.utc)


def nombre_de_creneaux(format_: FormatCreneau) -> int:
    if format_ == FormatCreneau.REEL:
        # Reels coupes au dashboard : plus aucun creneau reel a venir n'est
        # programme (les epingles et les geles restent, voir le runner).
        return settings.publication_creneaux_reel if reels_actifs() else 0
    return settings.publication_creneaux


def _futurs(format_: FormatCreneau, maintenant: dt.datetime):
    return select(Creneau).where(Creneau.publier_a > maintenant, Creneau.format == format_)


def synchroniser_creneaux(
    db: Session, maintenant: dt.datetime, format_: FormatCreneau = FormatCreneau.CARROUSEL
) -> list[Creneau]:
    attendus = prochains_horaires(maintenant, nombre_de_creneaux(format_), format_.value)
    existants = {utc(c.publier_a): c for c in db.scalars(_futurs(format_, maintenant))}
    for heure in attendus:
        if heure not in existants:
            db.add(Creneau(publier_a=heure, format=format_))
    # Un creneau futur qui n'est plus dans la grille (fenetres changees en
    # config, reels desactives) disparait, sauf s'il est epingle ou deja engage.
    for heure, creneau in existants.items():
        if heure not in attendus and creneau.etat in MODIFIABLES and not creneau.epingle:
            db.delete(creneau)
    db.commit()
    return list(db.scalars(_futurs(format_, maintenant).order_by(Creneau.publier_a)))


def a_une_image(article: Article) -> bool:
    # Sans image AUTORISEE, la cover partirait sur le fond de secours : pas
    # publiable. Un compte media Instagram n'en fournit jamais. DELEGUE au seul
    # predicat de `app.content.visuels` : la file et le choix des visuels
    # (`choix_visuels._propres`) ne doivent jamais diverger.
    return a_une_image_autorisee(article)


def comptes_cites(article: Article, index: IndexComptes) -> list[CompteArtiste]:
    return index.cites(
        textes=[article.title, article.summary],
        noms=article.mentioned_artists or [],
        exclure_source_id=article.source_id,
    )


def raison_sans_visuel(article: Article, index: IndexComptes | None = None) -> str | None:
    """None si l'article aura un visuel autorise, sinon la raison, affichable.

    Sans reseau : un artiste suivi cite suffit, son visuel sera cherche a la
    composition (en base, puis en direct). Si rien n'est trouve alors, l'article
    sera exclu avec la raison precise.
    """
    if a_une_image(article):
        return None
    if index is None:
        db = object_session(article)
        if db is None:
            return raison_sans_artiste(article)
        index = construire_index(db)
    if comptes_cites(article, index):
        return None
    return raison_sans_artiste(article)


def etiquette_visuel(article: Article, index: IndexComptes) -> str | None:
    """Ce que le dashboard affiche : d'ou viendra le visuel.

    "presse" pour un media qui n'est pas Instagram (og:image, media_content) ;
    le handle du compte pour un visuel propre a l'article (artiste, festival,
    label, club) ; sinon les comptes d'artistes suivis cites, dont le visuel
    sera cherche a la composition. `None` si rien de tout ca n'est possible --
    voir `raison_sans_visuel` pour la raison affichable.
    """
    if a_une_image(article):
        source = article.source
        if source is not None and source.type == SourceType.INSTAGRAM:
            return f"@{compte_depuis_url(source.url)}"
        return "presse"
    comptes = comptes_cites(article, index)
    if comptes:
        return "artistes : " + ", ".join(f"@{c.handle}" for c in comptes)
    return None


def actifs_interdits(db: Session, post: Post) -> list[dict]:
    """Les actifs d'un post que la policy refuse MAINTENANT.

    La source d'un actif d'artiste est RELUE (par l'`article_id` du post
    d'origine, sinon par son handle) : un compte passe en categorie `media`
    APRES la composition ne doit pas voir son visuel publie. `preparation.py`
    tient la meme ceinture pour les medias du representant, mais il n'a pas de
    session pour remonter au compte d'un actif venu d'ailleurs.

    Les actifs d'origine `article` sont relus EUX AUSSI, par membre (revue du
    2026-09-21) : depuis que les visuels d'un carrousel viennent de tout le
    groupe de doublons, un actif `article` porte l'`article_id` du MEMBRE dont
    il vient, pas forcement celui du representant. La ceinture ne relisait que
    les actifs d'artistes, et `preparation.py` que la source du representant :
    un doublon reclasse `media` entre la composition (J-1) et le gel (T-2h)
    passait donc au travers -- exactement le scenario que cette ceinture existe
    pour couvrir. Le handle sert de repli quand le membre a ete purge entre-temps
    (retention de 2 jours) ; une photo de presse RSS n'en a pas, et n'en a pas
    besoin : `origine_interdite` ne vise que les comptes Instagram.
    """
    actifs = getattr(post, "assets", None) or []
    artistes = [
        a for a in actifs if a.get("origine") in (ORIGINE_ARTISTE, ORIGINE_ARTICLE)
    ]
    if not artistes:
        return []
    ids = {a["article_id"] for a in artistes if a.get("article_id")}
    handles = {a["compte"].lstrip("@").lower() for a in artistes if a.get("compte")}
    par_article: dict[int, Source] = {}
    if ids:
        par_article = {
            article_id: source
            for article_id, source in db.execute(
                select(Article.id, Source)
                .join(Source, Source.id == Article.source_id)
                .where(Article.id.in_(ids))
            )
        }
    par_handle: dict[str, Source] = {}
    if handles:
        # Le handle est NORMALISE : une meme source s'ecrit `ig:fredagain`,
        # `ig:FredAgain` ou `https://www.instagram.com/fredagain/`. Comparer les
        # URL brutes laisserait passer un visuel interdit selon l'ecriture.
        par_handle = {
            compte_depuis_url(source.url).lower(): source
            for source in db.scalars(
                select(Source).where(Source.type == SourceType.INSTAGRAM)
            )
            if compte_depuis_url(source.url).lower() in handles
        }
    interdits = []
    for actif_ in artistes:
        source = par_article.get(actif_.get("article_id"))
        if source is None and actif_.get("compte"):
            source = par_handle.get(actif_["compte"].lstrip("@").lower())
        if source is not None and origine_interdite(source):
            interdits.append(actif_)
    return interdits


def categories_reel() -> set[str]:
    return {c.strip() for c in settings.publication_reel_categories.split(",") if c.strip()}


def duree_max_reel_ms() -> int:
    return int(settings.publication_reel_duree_max_s * 1000)


def _trop_courte(entree: dict) -> bool:
    # Une duree CONNUE sous le minimum ferait echouer le gel. Le plancher est
    # le meme que pour une video EMPRUNTEE a un artiste cite
    # (`actifs_artistes.duree_min_reel_ms` : le plus haut des deux planchers,
    # technique Meta et editorial). Une duree inconnue passe : le gel la
    # mesurera, et `ReelInadapte` liberera le creneau sans exclure l'article.
    duree = entree.get("duration_ms")
    return duree is not None and duree < duree_min_reel_ms()


def source_video_reel(article: Article) -> str | None:
    """D'ou viendra la video du reel : "api" (URL directe), "ytdlp" (recuperee sur
    le permalink au gel), ou None. L'ordre est la regle : l'API d'abord."""
    if any(not _trop_courte(v) for v in videos_exploitables(article.media, duree_max_reel_ms())):
        return "api"
    source = article.source
    videos = [m for m in article.media or [] if m.get("type") == "video"]
    if (
        settings.publication_reel_ytdlp
        and source is not None
        and source.type == SourceType.INSTAGRAM
        and video_recuperable(article.media, article.url)
        and not any(_trop_courte(v) for v in videos)
    ):
        return "ytdlp"
    return None


def raison_reel_propre(article: Article) -> str | None:
    """L'eligibilite reel par les medias de l'article LUI-MEME : video, categorie
    du compte, credit. None si elle est acquise.

    Extraite pour que le choix des visuels distingue « cet article a sa propre
    video » de « il faut aller la chercher chez un artiste cite »."""
    return _raison_ineligible_reel(article, source_video_reel(article))


def raison_ineligible_reel(
    article: Article, *, index: IndexComptes | None = None, maintenant: dt.datetime | None = None
) -> str | None:
    """None si l'article peut partir en reel, sinon la raison, affichable.

    Le chemin propre d'abord (sa video, son compte). A defaut, la video d'un
    artiste suivi cite, EN BASE seulement : l'arbitrage ne lit jamais le reseau.
    """
    raison = raison_reel_propre(article)
    if raison is None:
        return None
    db = object_session(article)
    if db is None:
        return raison
    index = index if index is not None else construire_index(db)
    comptes = comptes_cites(article, index)
    if not comptes:
        return raison
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    if classer(
        article, posts_en_base(db, comptes, maintenant), format_=FormatCreneau.REEL, maintenant=maintenant
    ):
        return None
    return raison


def _raison_ineligible_reel(article: Article, source_video: str | None) -> str | None:
    if source_video is None:
        return "aucune video exploitable"
    source = article.source
    categorie = source.categorie.value if source is not None and source.categorie is not None else None
    if categorie not in categories_reel():
        autorisees = ", ".join(sorted(categories_reel())) or "aucune"
        return f"compte de categorie {categorie or 'inconnue'} : reels reserves a {autorisees}"
    if credit_video(article) is None:
        return "credit impossible : la video ne vient pas d'un compte Instagram"
    return None


def _articles_publiables(db: Session, maintenant: dt.datetime, limite: int) -> list[Article]:
    depuis = maintenant - dt.timedelta(days=settings.publication_fraicheur_jours)
    requete = (
        select(Article)
        # La source est lue pour chaque article (eligibilite reel) : un seul aller.
        .options(selectinload(Article.source))
        .where(Article.relevance_score >= settings.publication_min_relevance)
        .where(Article.duplicate_of_id.is_(None))
        .where(Article.status != ArticleStatus.USED)
        .where(Article.file_exclu_raison.is_(None))
        # Un article deja parti (pour de vrai ou en apercu), quel que soit le
        # format, fait avancer la file : pas de reel d'un article deja publie en
        # carrousel, et le mode apercu ne reprend pas le meme article.
        .where(
            ~Article.id.in_(
                select(Creneau.article_id).where(
                    Creneau.etat.in_([EtatCreneau.APERCU, EtatCreneau.PUBLIE]),
                    Creneau.article_id.is_not(None),
                )
            )
        )
        .where(func.coalesce(Article.published_at, Article.fetched_at) >= depuis)
    )
    # Un article saute faute de visuel de bonne qualite est mis a l'ecart
    # TEMPORAIREMENT (`pause_visuels.marquer_saute`) -- sans ce filtre, un
    # article au meilleur score reprenait le creneau qu'il venait de liberer
    # a CHAQUE passage, jusqu'a MANQUE, et faisait taire le compte toute une
    # journee. `notin_` sur un ensemble VIDE reste correct en SQL (aucune
    # ligne exclue) : pas besoin de le conditionner.
    en_pause = articles_en_pause(maintenant)
    if en_pause:
        requete = requete.where(Article.id.notin_(en_pause))
    return db.scalars(
        requete.order_by(Article.relevance_score.desc(), Article.id).limit(limite)
    ).all()


def candidats_eligibles(
    db: Session, maintenant: dt.datetime, format_: FormatCreneau = FormatCreneau.CARROUSEL
) -> list[Candidat]:
    reel = format_ == FormatCreneau.REEL
    articles = _articles_publiables(db, maintenant, LIMITE_REEL if reel else LIMITE_CARROUSEL)
    # L'avance « deja compose » ne vaut que pour un brouillon du bon gabarit.
    composes = set(
        db.scalars(
            select(Post.article_id).where(
                Post.status != PostStatus.PUBLISHED,
                Post.article_id.is_not(None),
                Post.template == TEMPLATES[format_],
            )
        )
    )
    # Un seul index pour toute la liste : il fait deux requetes, pas une par article.
    index = construire_index(db)
    if reel:
        # COUT CONNU de l'arbitrage reel, assume tant que les reels sont coupes :
        # la garde appelle `posts_en_base` UNE FOIS PAR ARTICLE sans video propre
        # (jusqu'a LIMITE_REEL requetes par passage). Le remede serait de hisser
        # cette lecture hors de la boucle -- un seul `posts_en_base` pour l'union
        # des comptes cites, puis `classer` en memoire. A faire quand les reels
        # seront allumes, pas avant : `reels_actifs()` est faux par defaut et
        # cette branche ne tourne qu'en test.
        def garde(a):
            return raison_ineligible_reel(a, index=index, maintenant=maintenant) is None
    else:
        def garde(a):
            return raison_sans_visuel(a, index) is None

    return [
        Candidat(a.id, a.relevance_score, a.hotness_score or 0, utc(a.published_at or a.fetched_at), a.id in composes)
        for a in articles
        if garde(a)
    ]


def porte_un_post_orphelin(creneau: Creneau) -> bool:
    """Vrai pour un creneau qui porte un post COMPOSE dont l'article a disparu.

    `creneaux.article_id` ET `posts.article_id` sont tous deux
    `ON DELETE SET NULL` : la purge des articles (`retention_days`, 2 jours
    depuis le 2026-09-21) vide les deux en silence, y compris sur un creneau du
    TROISIEME jour de la file (`publication_creneaux` = 9, soit 3 jours) dont le
    post etait deja compose la veille. Le post, lui, est AUTONOME : textes,
    visuels copies en local (`visuels_cache_dir`), credit, palette et actifs
    sont denormalises exprès pour survivre a cette purge.

    Sans ce predicat, `appliquer_arbitrage` forcait `etat = LIBRE` -- l'article
    est parti -- tout en GARDANT `post_id`, parce que
    `if article_id != creneau.article_id` ne voit rien a faire quand les deux
    valent None. Le creneau devenait alors invisible de la composition comme du
    gel, qui n'examinent que des creneaux PROGRAMME, et pourrissait jusqu'a
    MANQUE. Mesure le 2026-09-30 : 8 des 25 creneaux manques SANS AUCUNE RAISON
    portaient exactement ce profil (post compose, article NULL, jamais gele).

    Un tel creneau est donc traite comme EPINGLE par l'arbitrage : il garde son
    post jusqu'au gel. Si ce post se revele impubliable, le gel le lache
    (`post_id = None`) et le creneau retombe dans le cas ordinaire au passage
    suivant -- la protection se defait toute seule, sans intervention.
    """
    return creneau.article_id is None and creneau.post_id is not None


def post_a_reprendre(db: Session, article_id: int, template: str = "kinda_post") -> int | None:
    return db.scalar(
        select(Post.id)
        .where(Post.article_id == article_id, Post.status != PostStatus.PUBLISHED, Post.template == template)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(1)
    )


def _tenus_ailleurs(db: Session, maintenant: dt.datetime, format_: FormatCreneau) -> set[int]:
    return set(
        db.scalars(
            select(Creneau.article_id).where(
                Creneau.article_id.is_not(None),
                or_(
                    # Un creneau passe mais encore dans le delai de retard garde
                    # son article, quel que soit son format.
                    and_(
                        Creneau.publier_a <= maintenant,
                        Creneau.etat.in_([EtatCreneau.PROGRAMME, EtatCreneau.GELE]),
                    ),
                    # L'autre format ne cede pas un article epingle ou deja engage.
                    and_(
                        Creneau.format != format_,
                        Creneau.publier_a > maintenant,
                        or_(Creneau.epingle.is_(True), Creneau.etat.in_(VERROUILLES)),
                    ),
                ),
            )
        )
    )


def appliquer_arbitrage(db: Session, maintenant: dt.datetime) -> list[Creneau]:
    tous: list[Creneau] = []
    places_ce_passage: set[int] = set()
    fenetres_fautives: FenetresInvalides | None = None
    for format_ in ORDRE_ARBITRAGE:
        try:
            creneaux = synchroniser_creneaux(db, maintenant, format_)
        except FenetresInvalides as exc:
            # Des fenetres reel fautives ne bloquent pas les carrousels : le
            # format fautif est saute, l'erreur remonte a la fin pour se voir.
            db.rollback()
            fenetres_fautives = exc
            continue
        occupes = _tenus_ailleurs(db, maintenant, format_) | places_ce_passage
        candidats = [c for c in candidats_eligibles(db, maintenant, format_) if c.article_id not in occupes]
        places = [
            Place(
                utc(c.publier_a),
                c.article_id,
                c.epingle or c.etat in VERROUILLES or porte_un_post_orphelin(c),
            )
            for c in creneaux
        ]
        affectation = arbitrer(places, candidats, maintenant)
        for creneau in creneaux:
            if porte_un_post_orphelin(creneau):
                # Le post survit a son article : le creneau le garde jusqu'au
                # gel. Un creneau deja passe en LIBRE par l'ancien defaut
                # redevient PROGRAMME, sinon son post -- pret a partir -- reste
                # invisible du gel, qui ne regarde que des creneaux PROGRAMME.
                # Les etats VERROUILLES (GELE, PUBLIE, ...) ne sont jamais
                # touches : ils passent par ce `continue` sans etre reecrits.
                if creneau.etat == EtatCreneau.LIBRE:
                    creneau.etat = EtatCreneau.PROGRAMME
                continue
            if creneau.epingle or creneau.etat in VERROUILLES:
                continue
            article_id = affectation.get(utc(creneau.publier_a))
            if article_id != creneau.article_id:
                creneau.article_id = article_id
                creneau.post_id = post_a_reprendre(db, article_id, TEMPLATES[format_]) if article_id else None
                if article_id is not None:
                    # Un NOUVEL article prend le creneau -- l'ancienne erreur
                    # (« visuel introuvable : ... », posee sur l'article
                    # precedent) ne le concerne plus et resterait trompeuse au
                    # dashboard tant qu'aucune composition n'a encore eu lieu
                    # pour celui-ci.
                    creneau.erreur = None
            creneau.etat = EtatCreneau.PROGRAMME if article_id else EtatCreneau.LIBRE
        db.commit()
        places_ce_passage |= {c.article_id for c in creneaux if c.article_id is not None}
        tous += creneaux
    if fenetres_fautives is not None:
        raise fenetres_fautives
    return sorted(tous, key=lambda c: utc(c.publier_a))


def offre_reels(db: Session, maintenant: dt.datetime) -> dict:
    """Combien d'articles de la fenetre de fraicheur portent une video, combien
    l'API donne directement (le reste passe par yt-dlp au gel), et combien passent
    toutes les regles reel. C'est la mesure qui dira si deux reels par jour sont
    tenables, et quelle part repose sur yt-dlp."""
    articles = _articles_publiables(db, maintenant, LIMITE_REEL)
    sources = {a.id: source_video_reel(a) for a in articles}
    avec_video = [a for a in articles if sources[a.id] is not None]
    return {
        "avec_video": len(avec_video),
        "directes": sum(1 for a in avec_video if sources[a.id] == "api"),
        "eligibles": sum(1 for a in avec_video if _raison_ineligible_reel(a, sources[a.id]) is None),
        "fraicheur_jours": settings.publication_fraicheur_jours,
    }
