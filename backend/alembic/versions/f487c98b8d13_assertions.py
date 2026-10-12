"""Add communication assertions without rewriting historical events.

Revision ID: f487c98b8d13
Revises: e91a42f708bc
"""

from alembic import op
import sqlalchemy as sa


revision = "f487c98b8d13"
down_revision = "e91a42f708bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assertions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("speaker_actor_id", sa.Uuid(), sa.ForeignKey("actors.id"), nullable=False),
        sa.Column("recipient_actor_id", sa.Uuid(), sa.ForeignKey("actors.id"), nullable=False),
        sa.Column("claim_id", sa.Uuid(), sa.ForeignKey("claims.id"), nullable=False),
        sa.Column("source_claim_id", sa.Uuid(), sa.ForeignKey("claims.id")),
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("corrects_assertion_id", sa.Uuid(), sa.ForeignKey("assertions.id")),
        sa.Column("expressed_stance", sa.String(16), nullable=False),
        sa.Column("expressed_confidence", sa.Double(), nullable=False),
        sa.Column("source_attribution", sa.String(24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("fidelity", sa.JSON(), nullable=False),
        sa.UniqueConstraint("game_id", "id", name="uq_assertions_game_id"),
        sa.UniqueConstraint("game_id", "event_id", name="uq_assertions_game_event"),
        sa.ForeignKeyConstraint(
            ["game_id", "speaker_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_assertions_game_speaker",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "recipient_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_assertions_game_recipient",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "claim_id"], ["claims.game_id", "claims.id"],
            name="fk_assertions_game_claim",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "source_claim_id"], ["claims.game_id", "claims.id"],
            name="fk_assertions_game_source_claim",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "event_id"], ["events.game_id", "events.id"],
            name="fk_assertions_game_event",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "source_event_id"], ["events.game_id", "events.id"],
            name="fk_assertions_game_source_event",
        ),
        sa.ForeignKeyConstraint(
            ["game_id", "corrects_assertion_id"],
            ["assertions.game_id", "assertions.id"],
            name="fk_assertions_game_correction",
        ),
        sa.CheckConstraint("day BETWEEN 1 AND 30", name="ck_assertions_day"),
        sa.CheckConstraint(
            "expressed_stance IN ('believe', 'doubt', 'deny')",
            name="ck_assertions_stance",
        ),
        sa.CheckConstraint(
            "expressed_confidence BETWEEN 0 AND 1",
            name="ck_assertions_confidence",
        ),
        sa.CheckConstraint(
            "source_attribution IN ('direct_observation', 'another_character', "
            "'ai', 'anonymous', 'unknown', 'claimed_authority')",
            name="ck_assertions_attribution",
        ),
    )
    op.create_index("ix_assertions_game_id", "assertions", ["game_id"])
    op.create_index("ix_assertions_game_speaker_day", "assertions", [
        "game_id", "speaker_actor_id", "day",
    ])
    op.create_index("ix_assertions_game_claim", "assertions", ["game_id", "claim_id"])


def downgrade() -> None:
    op.drop_table("assertions")
