"""evenements phase 2A : events, event_observations, sources.source_group_id

Rien n'est encore alimente (Task 1 du plan
docs/superpowers/plans/2026-09-23-phase-2a-evenements.md) : cette migration ne
fait que poser la structure. `event_observations` reprend la philosophie
d'`artist_mentions` (0007) -- champs denormalises (titre, url, nom de source) et
FK vers `article_id` en ON DELETE SET NULL -- pour que la purge a 2 jours casse
le lien vers l'article sans detruire la preuve.

NON APPLIQUEE sur la base de production : verifiee sur un cluster jetable
seulement. Voir le rapport de tache pour la commande a passer.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# Convention du projet : enums stockes en VARCHAR + CHECK (`native_enum=False`),
# jamais d'enum natif Postgres. `op.f()` n'est appele QUE dans les fonctions
# upgrade/downgrade : au niveau module il leve un NameError (le proxy Operations
# n'est pas encore etabli), invisible a l'usage courant mais fatal a
# `alembic history` / `alembic check` -- voir tests/socle/test_migrations.py.
VALEURS_PLATEFORME = ("rss", "reddit_rss", "bluesky", "x", "instagram")
VALEURS_ROLE = ("primary", "secondary", "unknown")


def upgrade() -> None:
    # --- sources.source_group_id ---
    # Nullable, sans backfill : personne ne la remplit encore, seule la
    # structure doit exister (voir app/models.py::Source).
    op.add_column("sources", sa.Column("source_group_id", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_sources_source_group_id"), "sources", ["source_group_id"]
    )

    # --- events ---
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("titre", sa.Text(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("platform_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "independent_source_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(op.f("ix_events_first_seen_at"), "events", ["first_seen_at"])
    op.create_index(op.f("ix_events_last_seen_at"), "events", ["last_seen_at"])

    # --- event_observations ---
    op.create_table(
        "event_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("source_role", sa.String(length=20), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_observations")),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_event_observations_events_event_id"),
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name=op.f("fk_event_observations_articles_article_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_event_observations_sources_source_id"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "event_id",
            "article_id",
            name=op.f("uq_event_observations_event_id_article_id"),
        ),
    )
    op.create_index(op.f("ix_event_observations_event_id"), "event_observations", ["event_id"])
    op.create_index(op.f("ix_event_observations_article_id"), "event_observations", ["article_id"])
    op.create_index(op.f("ix_event_observations_source_id"), "event_observations", ["source_id"])
    op.create_index(
        op.f("ix_event_observations_published_at"), "event_observations", ["published_at"]
    )
    op.create_check_constraint(
        op.f("ck_event_observations_platform"),
        "event_observations",
        sa.column("platform").in_(VALEURS_PLATEFORME),
    )
    op.create_check_constraint(
        op.f("ck_event_observations_source_role"),
        "event_observations",
        sa.column("source_role").in_(VALEURS_ROLE),
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_event_observations_source_role"), "event_observations", type_="check")
    op.drop_constraint(op.f("ck_event_observations_platform"), "event_observations", type_="check")
    op.drop_index(op.f("ix_event_observations_published_at"), table_name="event_observations")
    op.drop_index(op.f("ix_event_observations_source_id"), table_name="event_observations")
    op.drop_index(op.f("ix_event_observations_article_id"), table_name="event_observations")
    op.drop_index(op.f("ix_event_observations_event_id"), table_name="event_observations")
    op.drop_table("event_observations")
    op.drop_index(op.f("ix_events_last_seen_at"), table_name="events")
    op.drop_index(op.f("ix_events_first_seen_at"), table_name="events")
    op.drop_table("events")
    op.drop_index(op.f("ix_sources_source_group_id"), table_name="sources")
    op.drop_column("sources", "source_group_id")
