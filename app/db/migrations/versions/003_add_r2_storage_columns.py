"""Add R2 storage columns to videos table.

Revision ID: 003
Revises: 002
Create Date: 2026-02-22
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("r2_video_key", sa.String(512), nullable=True))
    op.add_column("videos", sa.Column("r2_thumbnail_key", sa.String(512), nullable=True))
    op.add_column("videos", sa.Column("r2_project_prefix", sa.String(512), nullable=True))
    op.add_column(
        "videos",
        sa.Column("render_version", sa.Integer, server_default="1", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("videos", "render_version")
    op.drop_column("videos", "r2_project_prefix")
    op.drop_column("videos", "r2_thumbnail_key")
    op.drop_column("videos", "r2_video_key")
