"""Initial users and videos tables.

Revision ID: 001
Revises: None
Create Date: 2026-02-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("google_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("picture_url", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_google_id", "users", ["google_id"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "videos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("input_type", sa.String(20), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("script_text", sa.Text(), nullable=True),
        sa.Column("narration_style", sa.String(50), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("orientation", sa.String(20), nullable=False),
        sa.Column("output_filename", sa.String(512), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("thumbnail_filename", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_videos_user_id", "videos", ["user_id"])


def downgrade() -> None:
    op.drop_table("videos")
    op.drop_table("users")
