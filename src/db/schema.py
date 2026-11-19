"""Table definitions (SQLAlchemy Core).

Schema changes go through Alembic (migrations/versions). Keep this file and
the latest migration in step: tests/test_db.py checks they match.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

JOB_STATUSES = ("active", "stale", "closed")

# Public job postings. Can always be rebuilt from the source APIs.
jobs = Table(
    "jobs", metadata,
    Column("id", Integer, primary_key=True),
    Column("source", String(32), nullable=False),
    Column("external_id", String(128), nullable=False),
    Column("title", Text, nullable=False, default=""),
    Column("company", Text, nullable=False, default=""),
    Column("location", Text, nullable=False, default=""),
    Column("country", String(64), nullable=False, default=""),
    Column("country_code", String(8), nullable=False, default=""),
    Column("region", String(64), nullable=False, default=""),
    Column("salary_min", Float),
    Column("salary_max", Float),
    Column("salary_is_predicted", Boolean, nullable=False, default=False),
    Column("description", Text, nullable=False, default=""),
    Column("description_is_truncated", Boolean, nullable=False, default=True),
    Column("url", Text, nullable=False, default=""),
    Column("posted_date", Date),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("contract_time", String(32)),
    Column("category", Text, nullable=False, default=""),
    # lifecycle
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_checked_at", DateTime(timezone=True)),
    Column("status", String(16), nullable=False, default="active"),
    Column("stale_since", DateTime(timezone=True)),
    Column("closed_at", DateTime(timezone=True)),
    UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
)
Index("ix_jobs_status", jobs.c.status)
Index("ix_jobs_last_seen_at", jobs.c.last_seen_at)

# Which search (country + query) found each job. Drives role filtering in the
# app and "unseen after a full re-scan" stale marking in the worker.
job_queries = Table(
    "job_queries", metadata,
    Column("job_id", Integer, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
    Column("country_code", String(8), primary_key=True),
    Column("query", String(200), primary_key=True),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
)
Index("ix_job_queries_slice", job_queries.c.country_code, job_queries.c.query)

# One row per worker cycle per source: the ingestion audit log.
fetch_runs = Table(
    "fetch_runs", metadata,
    Column("id", Integer, primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("source", String(64), nullable=False),
    Column("jobs_found", Integer, nullable=False, default=0),
    Column("jobs_inserted", Integer, nullable=False, default=0),
    Column("jobs_updated", Integer, nullable=False, default=0),
    Column("jobs_reactivated", Integer, nullable=False, default=0),
    Column("jobs_marked_stale", Integer, nullable=False, default=0),
    Column("jobs_closed", Integer, nullable=False, default=0),
    Column("requests_used", Integer, nullable=False, default=0),
    Column("backfill_requests", Integer, nullable=False, default=0),
    Column("sources_used", Text, nullable=False, default=""),
    Column("status", String(16), nullable=False, default="running"),  # running|success|partial|failed
    Column("error_message", Text),
)
Index("ix_fetch_runs_started_at", fetch_runs.c.started_at)

# Backfill / re-scan queue: one row per (source, country, query) slice.
backfill_tasks = Table(
    "backfill_tasks", metadata,
    Column("id", Integer, primary_key=True),
    Column("source", String(64), nullable=False),
    Column("country", String(8), nullable=False),
    Column("query", String(200), nullable=False),
    Column("max_days_old", Integer),           # time window for the slice
    Column("priority", Integer, nullable=False, default=100),  # lower = sooner
    Column("page", Integer, nullable=False, default=1),        # next page to fetch
    Column("mode", String(16), nullable=False, default="backfill"),  # backfill|rescan|incremental
    Column("status", String(16), nullable=False, default="pending"),  # pending|complete|failed
    Column("scan_started_at", DateTime(timezone=True)),
    Column("last_run_at", DateTime(timezone=True)),
    Column("last_full_scan_at", DateTime(timezone=True)),
    Column("last_incremental_at", DateTime(timezone=True)),
    Column("last_error", Text),
    UniqueConstraint("source", "country", "query", name="uq_backfill_slice"),
)

# Adzuna's live count / mean salary per search, for the dashboard header.
market_stats = Table(
    "market_stats", metadata,
    Column("id", Integer, primary_key=True),
    Column("query", String(200), nullable=False),
    Column("country", String(8), nullable=False),
    Column("total_count", Integer),
    Column("mean_salary", Float),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
)
Index("ix_market_stats_query", market_stats.c.query, market_stats.c.fetched_at)

# Personal data. Deleted automatically after delete_after (30 days by default).
resumes = Table(
    "resumes", metadata,
    Column("id", String(36), primary_key=True),           # random UUID
    Column("session_id", String(64), nullable=False),     # anonymous session ID
    Column("skills", Text, nullable=False, default="[]"),  # JSON list of extracted skill names
    Column("file_path", Text),                              # encrypted file, only if store_file is on
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    Column("delete_after", DateTime(timezone=True), nullable=False),
)
Index("ix_resumes_session_id", resumes.c.session_id)
Index("ix_resumes_delete_after", resumes.c.delete_after)

analyses = Table(
    "analyses", metadata,
    Column("id", Integer, primary_key=True),
    Column("resume_id", String(36), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False),
    Column("role", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("fit_score", Float),
    Column("postings_count", Integer, nullable=False, default=0),
    Column("results", Text, nullable=False, default="[]"),  # JSON: skill, has_skill, demand, gap_score
)
Index("ix_analyses_resume_id", analyses.c.resume_id)

# Audit trail of deletions. Holds only the random resume ID, never content.
resume_deletions = Table(
    "resume_deletions", metadata,
    Column("id", Integer, primary_key=True),
    Column("resume_id", String(36), nullable=False),
    Column("reason", String(32), nullable=False),  # expired|user_request
    Column("deleted_at", DateTime(timezone=True), nullable=False),
)
