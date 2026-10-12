"""Add the seven-day scenario and pending intentions.

Revision ID: c824e15a770d
Revises: b703c21a9f0e
"""

from alembic import op
import sqlalchemy as sa


revision = "c824e15a770d"
down_revision = "b703c21a9f0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "games", sa.Column("scenario", sa.String(16), nullable=False,
                            server_default="society")
    )
    op.create_table(
        "intentions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("actors.id"), nullable=False),
        sa.Column("intended_action_type", sa.String(80), nullable=False),
        sa.Column("target_actor_id", sa.Uuid(), sa.ForeignKey("actors.id")),
        sa.Column("claim_id", sa.Uuid(), sa.ForeignKey("claims.id")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("motivation_factors", sa.JSON(), nullable=False),
        sa.Column("created_day", sa.Integer(), nullable=False),
        sa.Column("urgency", sa.Double(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_event_id", sa.Uuid(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("resolved_event_id", sa.Uuid(), sa.ForeignKey("events.id")),
        sa.ForeignKeyConstraint(
            ["game_id", "actor_id"], ["actors.game_id", "actors.id"],
            name="fk_intentions_game_actor",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "target_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_intentions_game_target",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "claim_id"], ["claims.game_id", "claims.id"],
            name="fk_intentions_game_claim",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "created_event_id"], ["events.game_id", "events.id"],
            name="fk_intentions_game_created_event_id",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "resolved_event_id"], ["events.game_id", "events.id"],
            name="fk_intentions_game_resolved_event_id",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'abandoned')",
            name="ck_intentions_status",
        ),
        sa.CheckConstraint("created_day BETWEEN 1 AND 7", name="ck_intentions_day"),
        sa.CheckConstraint("urgency BETWEEN 0 AND 1", name="ck_intentions_urgency"),
    )
    op.create_index("ix_intentions_game_id", "intentions", ["game_id"])


def downgrade() -> None:
    op.drop_index("ix_intentions_game_id", table_name="intentions")
    op.drop_table("intentions")
    op.drop_column("games", "scenario")
