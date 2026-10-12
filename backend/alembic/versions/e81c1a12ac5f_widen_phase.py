"""Fit the final_conversation phase in MariaDB's varchar column.

Revision ID: e81c1a12ac5f
Revises: d504f89a21e0
"""

from alembic import op
import sqlalchemy as sa


revision = "e81c1a12ac5f"
down_revision = "d504f89a21e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.alter_column("phase", existing_type=sa.String(16),
                           type_=sa.String(24), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.alter_column("phase", existing_type=sa.String(24),
                           type_=sa.String(16), existing_nullable=False)
