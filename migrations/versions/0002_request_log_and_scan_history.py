"""Request log for the budget, previous-scan start for stale marking, close reason.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "api_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("requested_at", TZ, nullable=False),
        sa.Column("run_id", sa.Integer),
    )
    op.create_index("ix_api_requests_requested_at", "api_requests", ["requested_at"])
    with op.batch_alter_table("backfill_tasks") as batch:
        batch.add_column(sa.Column("previous_scan_started_at", TZ))
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("closed_reason", sa.String(16)))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("closed_reason")
    with op.batch_alter_table("backfill_tasks") as batch:
        batch.drop_column("previous_scan_started_at")
    op.drop_index("ix_api_requests_requested_at", table_name="api_requests")
    op.drop_table("api_requests")
