"""Add batches and batch_items tables (batch queue / autopilot).

Revision ID: 006
Revises: 005
Create Date: 2026-11-14
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), server_default="draft", nullable=False),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("publish", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_batches_user_id", "batches", ["user_id"])

    op.create_table(
        "batch_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "batch_id", sa.Integer(),
            sa.ForeignKey("batches.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("input_type", sa.String(20), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), server_default="pending", nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column(
            "video_id", sa.Integer(),
            sa.ForeignKey("videos.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("publish_job_id", sa.Integer(), nullable=True),
        sa.Column("scheduled_publish_at", sa.DateTime(), nullable=True),
        sa.Column("youtube_url", sa.String(512), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_batch_items_batch_id", "batch_items", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_batch_items_batch_id", table_name="batch_items")
    op.drop_table("batch_items")
    op.drop_index("ix_batches_user_id", table_name="batches")
    op.drop_table("batches")
