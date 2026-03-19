"""Add social_accounts and publish_jobs tables.

Revision ID: 005
Revises: 004
Create Date: 2026-03-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- social_accounts table ---
    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(50), server_default="active", nullable=False),
        sa.Column("provider_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "provider_user_id", name="uq_social_account"),
    )
    op.create_index("ix_social_accounts_user_id", "social_accounts", ["user_id"])

    # --- publish_jobs table ---
    op.create_table(
        "publish_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("videos.id"), nullable=False),
        sa.Column("social_account_id", sa.Integer(), sa.ForeignKey("social_accounts.id"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("mode", sa.String(50), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(50), server_default="queued", nullable=False),
        sa.Column("provider_status", sa.String(100), nullable=True),
        sa.Column("platform_media_id", sa.String(255), nullable=True),
        sa.Column("platform_post_url", sa.String(512), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default="3", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_publish_jobs_user_id", "publish_jobs", ["user_id"])
    op.create_index("ix_publish_jobs_video_id", "publish_jobs", ["video_id"])


def downgrade() -> None:
    op.drop_table("publish_jobs")
    op.drop_table("social_accounts")
