"""visuels d'artistes : posts.assets et artists.source_instagram_id

`posts.assets` NULL = post anterieur a la regle des visuels : lecture historique.
`artists.source_instagram_id` NULL = fiche non liee a un compte suivi.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Meme forme que `posts.variants` (0011) : JSONB en Postgres.
    op.add_column("posts", sa.Column("assets", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("artists", sa.Column("source_instagram_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_artists_sources_source_instagram_id"),
        "artists",
        "sources",
        ["source_instagram_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_artists_source_instagram_id"), "artists", ["source_instagram_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_artists_source_instagram_id"), table_name="artists")
    op.drop_constraint(op.f("fk_artists_sources_source_instagram_id"), "artists", type_="foreignkey")
    op.drop_column("artists", "source_instagram_id")
    op.drop_column("posts", "assets")
