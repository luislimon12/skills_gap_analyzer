"""Production schema: jobs, lifecycle, fetch runs, backfill queue, resumes, analyses.

Revision ID: 0001
Revises:
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("company", sa.Text, nullable=False),
        sa.Column("location", sa.Text, nullable=False),
        sa.Column("country", sa.String(64), nullable=False),
        sa.Column("country_code", sa.String(8), nullable=False),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("salary_min", sa.Float),
        sa.Column("salary_max", sa.Float),
        sa.Column("salary_is_predicted", sa.Boolean, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("description_is_truncated", sa.Boolean, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("posted_date", sa.Date),
        sa.Column("fetched_at", TZ, nullable=False),
        sa.Column("contract_time", sa.String(32)),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("first_seen_at", TZ, nullable=False),
        sa.Column("last_seen_at", TZ, nullable=False),
        sa.Column("last_checked_at", TZ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stale_since", TZ),
        sa.Column("closed_at", TZ),
        sa.UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_last_seen_at", "jobs", ["last_seen_at"])

    op.create_table(
        "job_queries",
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("country_code", sa.String(8), primary_key=True),
        sa.Column("query", sa.String(200), primary_key=True),
        sa.Column("first_seen_at", TZ, nullable=False),
        sa.Column("last_seen_at", TZ, nullable=False),
    )
    op.create_index("ix_job_queries_slice", "job_queries", ["country_code", "query"])

    op.create_table(
        "fetch_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("finished_at", TZ),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("jobs_found", sa.Integer, nullable=False),
        sa.Column("jobs_inserted", sa.Integer, nullable=False),
        sa.Column("jobs_updated", sa.Integer, nullable=False),
        sa.Column("jobs_reactivated", sa.Integer, nullable=False),
        sa.Column("jobs_marked_stale", sa.Integer, nullable=False),
        sa.Column("jobs_closed", sa.Integer, nullable=False),
        sa.Column("requests_used", sa.Integer, nullable=False),
        sa.Column("backfill_requests", sa.Integer, nullable=False),
        sa.Column("sources_used", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text),
    )
    op.create_index("ix_fetch_runs_started_at", "fetch_runs", ["started_at"])

    op.create_table(
        "backfill_tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("country", sa.String(8), nullable=False),
        sa.Column("query", sa.String(200), nullable=False),
        sa.Column("max_days_old", sa.Integer),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("page", sa.Integer, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scan_started_at", TZ),
        sa.Column("last_run_at", TZ),
        sa.Column("last_full_scan_at", TZ),
        sa.Column("last_incremental_at", TZ),
        sa.Column("last_error", sa.Text),
        sa.UniqueConstraint("source", "country", "query", name="uq_backfill_slice"),
    )

    op.create_table(
        "market_stats",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("query", sa.String(200), nullable=False),
        sa.Column("country", sa.String(8), nullable=False),
        sa.Column("total_count", sa.Integer),
        sa.Column("mean_salary", sa.Float),
        sa.Column("fetched_at", TZ, nullable=False),
    )
    op.create_index("ix_market_stats_query", "market_stats", ["query", "fetched_at"])

    op.create_table(
        "resumes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("skills", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text),
        sa.Column("uploaded_at", TZ, nullable=False),
        sa.Column("delete_after", TZ, nullable=False),
    )
    op.create_index("ix_resumes_session_id", "resumes", ["session_id"])
    op.create_index("ix_resumes_delete_after", "resumes", ["delete_after"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("resume_id", sa.String(36), sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(200), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("fit_score", sa.Float),
        sa.Column("postings_count", sa.Integer, nullable=False),
        sa.Column("results", sa.Text, nullable=False),
    )
    op.create_index("ix_analyses_resume_id", "analyses", ["resume_id"])

    op.create_table(
        "resume_deletions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("resume_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("deleted_at", TZ, nullable=False),
    )


def downgrade() -> None:
    for table in (
        "resume_deletions", "analyses", "resumes", "market_stats",
        "backfill_tasks", "fetch_runs", "job_queries", "jobs",
    ):
        op.drop_table(table)
