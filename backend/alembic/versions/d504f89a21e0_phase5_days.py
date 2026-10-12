"""Allow thirty-day intentions and the final conversation phase.

Revision ID: d504f89a21e0
Revises: f487c98b8d13
"""

from alembic import op
import sqlalchemy as sa


revision = "d504f89a21e0"
down_revision = "f487c98b8d13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.drop_constraint("ck_games_phase", type_="check")
        batch.create_check_constraint(
            "ck_games_phase", "phase IN ('active', 'final_conversation', 'finished')"
        )
    with op.batch_alter_table("intentions") as batch:
        batch.drop_constraint("ck_intentions_day", type_="check")
        batch.create_check_constraint("ck_intentions_day", "created_day BETWEEN 1 AND 30")


def downgrade() -> None:
    with op.batch_alter_table("intentions") as batch:
        batch.drop_constraint("ck_intentions_day", type_="check")
        batch.create_check_constraint("ck_intentions_day", "created_day BETWEEN 1 AND 7")
    with op.batch_alter_table("games") as batch:
        batch.drop_constraint("ck_games_phase", type_="check")
        batch.create_check_constraint("ck_games_phase", "phase IN ('active', 'finished')")
