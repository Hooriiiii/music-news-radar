import datetime as dt
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Article, Digest
from app.pipeline.scoring import is_hot

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=True,
)


def select_digest_articles(
    db: Session,
    since: dt.datetime,
    exclude_ids: list[int] | None = None,
    limit: int | None = None,
) -> list[Article]:
    stmt = (
        select(Article)
        .where(Article.relevance_score >= settings.digest_relevance_threshold)
        .where(Article.fetched_at > since)
        # Garde-fou back-fill : une source nouvellement ajoutée peut ingérer de
        # vieux articles -- le digest est de l'actu, pas de l'archive
        .where(or_(Article.published_at.is_(None), Article.published_at >= since))
        .order_by(
            Article.relevance_score.desc(),
            Article.hotness_score.desc().nulls_last(),
            Article.id,
        )
    )
    if exclude_ids:
        stmt = stmt.where(Article.id.not_in(exclude_ids))

    # Plafond par source appliqué en Python : on parcourt par pertinence
    # décroissante et on saute les articles d'une source déjà pleine
    limit = limit or settings.digest_max_articles
    picked: list[Article] = []
    per_source: dict[int, int] = {}
    for article in db.scalars(stmt.limit(limit * 10)):
        if per_source.get(article.source_id, 0) >= settings.digest_max_per_source:
            continue
        picked.append(article)
        per_source[article.source_id] = per_source.get(article.source_id, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def build_digest(
    db: Session, for_date: dt.date | None = None, persist: bool = True
) -> tuple[Digest, list[Article], bool]:
    """Construit le digest du jour. Idempotent : si un digest existe déjà pour
    cette date, il est retourné tel quel (created=False).

    Fenêtre de sélection : depuis le digest précédent (minuit de sa date, ses
    articles exclus), ou les dernières 24h s'il n'y a jamais eu de digest.
    """
    for_date = for_date or dt.date.today()

    existing = db.scalar(select(Digest).where(Digest.date == for_date))
    if existing is not None:
        return existing, _articles_by_ids(db, existing.article_ids), False

    previous = db.scalar(
        select(Digest).where(Digest.date < for_date).order_by(Digest.date.desc()).limit(1)
    )
    if previous is not None:
        since = dt.datetime.combine(previous.date, dt.time.min, tzinfo=dt.timezone.utc)
        exclude_ids = list(previous.article_ids or [])
    else:
        since = dt.datetime.combine(
            for_date, dt.time.min, tzinfo=dt.timezone.utc
        ) - dt.timedelta(hours=24)
        exclude_ids = []

    articles = select_digest_articles(db, since=since, exclude_ids=exclude_ids)
    digest = Digest(date=for_date, article_ids=[a.id for a in articles])
    if persist:
        db.add(digest)
        db.commit()
    return digest, articles, True


def render_digest(for_date: dt.date, articles: list[Article]) -> str:
    hot = [a for a in articles if is_hot(a)]
    others = [a for a in articles if not is_hot(a)]
    template = _env.get_template("digest.html.j2")
    return template.render(
        date_label=for_date.strftime("%d/%m/%Y"),
        hot_articles=hot,
        other_articles=others,
        total=len(articles),
        threshold=settings.digest_relevance_threshold,
    )


def render_digest_text(for_date: dt.date, articles: list[Article]) -> str:
    """Version texte brut (fallback multipart du mail)."""
    lines = [f"Music News Radar — digest du {for_date:%d/%m/%Y} ({len(articles)} actus)", ""]
    for article in articles:
        flag = "[HOT] " if is_hot(article) else ""
        lines.append(f"- {flag}{article.title}")
        if article.summary:
            lines.append(f"  {article.summary}")
        lines.append(f"  {article.url}")
        lines.append("")
    return "\n".join(lines)


def _articles_by_ids(db: Session, ids: list[int]) -> list[Article]:
    if not ids:
        return []
    articles = db.scalars(select(Article).where(Article.id.in_(ids))).all()
    order = {article_id: position for position, article_id in enumerate(ids)}
    return sorted(articles, key=lambda a: order.get(a.id, len(order)))
