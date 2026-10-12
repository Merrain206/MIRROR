"""Record paid model usage without prompts or credentials.

Revision ID: b94f7a61d25c
Revises: e81c1a12ac5f
"""

from alembic import op
import sqlalchemy as sa

revision = "b94f7a61d25c"
down_revision = "e81c1a12ac5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("game_id", sa.Uuid(), nullable=True),
        sa.Column("character_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("context_chars", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("fallback", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_llm_calls_game_id", "llm_calls", ["game_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_game_id", table_name="llm_calls")
    op.drop_table("llm_calls")
