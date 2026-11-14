"""Hands-off autopilot: batches.hands_off and batch_items.next_attempt_at.

Publish-job retries (incl. waiting for the YouTube quota reset) reuse the
existing publish_jobs.next_retry_at column.

Revision ID: 007
Revises: 006
Create Date: 2026-11-14
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("batches") as batch_op:
        batch_op.add_column(
            sa.Column("hands_off", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    with op.batch_alter_table("batch_items") as batch_op:
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("batch_items") as batch_op:
        batch_op.drop_column("next_attempt_at")
    with op.batch_alter_table("batches") as batch_op:
        batch_op.drop_column("hands_off")
