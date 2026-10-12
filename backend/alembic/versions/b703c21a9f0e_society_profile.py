"""Add the decision profile and directed relationship basis.

Revision ID: b703c21a9f0e
Revises: a601cf30cdbd
"""

from alembic import op
import sqlalchemy as sa


revision = "b703c21a9f0e"
down_revision = "a601cf30cdbd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("decision_profile", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'")),
    )
    op.add_column(
        "relationships",
        sa.Column("basis", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("relationships", "basis")
    op.drop_column("characters", "decision_profile")
