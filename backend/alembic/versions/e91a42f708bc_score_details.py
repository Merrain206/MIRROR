"""Freeze score explanations alongside numeric snapshots.

Revision ID: e91a42f708bc
Revises: c824e15a770d
"""

from alembic import op
import sqlalchemy as sa


revision = "e91a42f708bc"
down_revision = "c824e15a770d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "score_snapshots",
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("score_snapshots", "details")
