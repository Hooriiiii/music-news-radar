"""reels : le format d'un creneau et le credit d'un post

Les creneaux existants deviennent des carrousels (server_default), sans
backfill. `posts.credit` est nullable : NULL = pas de compte a crediter
(source non Instagram).

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# Convention du projet : VARCHAR + CHECK, jamais d'enum natif. `op.f()` reste
# dans les fonctions (un appel au niveau module leve a l'import, cf. 0015).
FORMATS = ("carrousel", "reel")


def upgrade() -> None:
    op.add_column(
        "creneaux",
        sa.Column("format", sa.String(length=20), server_default="carrousel", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_creneaux_format_creneau"), "creneaux", sa.column("format").in_(FORMATS)
    )
    op.add_column("posts", sa.Column("credit", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("posts", "credit")
    op.drop_constraint(op.f("ck_creneaux_format_creneau"), "creneaux", type_="check")
    op.drop_column("creneaux", "format")
