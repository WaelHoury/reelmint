"""Add user_tts_voices table and TTS columns to videos.

Revision ID: 004
Revises: 003
Create Date: 2026-03-17
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- user_tts_voices table ---
    op.create_table(
        "user_tts_voices",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("fish_voice_id", sa.String(255), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), server_default="processing", nullable=False),
        sa.Column("is_preset", sa.Boolean, server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # --- TTS columns on videos ---
    op.add_column("videos", sa.Column("tts_provider", sa.String(50), nullable=True))
    op.add_column("videos", sa.Column("tts_voice_key", sa.String(255), nullable=True))
    op.add_column("videos", sa.Column("tts_voice_label", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "tts_voice_label")
    op.drop_column("videos", "tts_voice_key")
    op.drop_column("videos", "tts_provider")
    op.drop_table("user_tts_voices")
