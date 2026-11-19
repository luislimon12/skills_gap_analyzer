"""fetch_runs (ingestion audit log) and backfill_tasks (the slice queue)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from src.db.engine import utcnow
from src.db.schema import backfill_tasks, fetch_runs

# ---------------------------------------------------------------- fetch_runs


def start_run(conn: Connection, source: str, now: datetime | None = None) -> int:
    return conn.execute(fetch_runs.insert().values(
        started_at=now or utcnow(), source=source, status="running",
        jobs_found=0, jobs_inserted=0, jobs_updated=0, jobs_reactivated=0,
        jobs_marked_stale=0, jobs_closed=0, requests_used=0, backfill_requests=0,
        sources_used="",
    )).inserted_primary_key[0]


def finish_run(conn: Connection, run_id: int, *, status: str, now: datetime | None = None, **fields) -> None:
    if fields.get("error_message"):
        fields["error_message"] = str(fields["error_message"])[:2000]
    conn.execute(
        update(fetch_runs).where(fetch_runs.c.id == run_id)
        .values(status=status, finished_at=now or utcnow(), **fields)
    )


def update_run(conn: Connection, run_id: int, **fields) -> None:
    """Save running totals mid-cycle so request counts survive a crash."""
    conn.execute(update(fetch_runs).where(fetch_runs.c.id == run_id).values(**fields))


def requests_used_since(conn: Connection, since: datetime, source: str | None = None) -> tuple[int, int]:
    """(all requests, backfill/re-scan requests) used by runs started at or after ``since``."""
    query = select(
        func.coalesce(func.sum(fetch_runs.c.requests_used), 0),
        func.coalesce(func.sum(fetch_runs.c.backfill_requests), 0),
    ).where(fetch_runs.c.started_at >= since)
    if source:
        query = query.where(fetch_runs.c.source == source)
    total, backfill = conn.execute(query).one()
    return int(total), int(backfill)


def recent_runs(conn: Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        select(fetch_runs).order_by(fetch_runs.c.started_at.desc(), fetch_runs.c.id.desc()).limit(limit)
    ).mappings().all()
    return [dict(row) for row in rows]


def failed_runs_since(conn: Connection, since: datetime) -> list[dict]:
    rows = conn.execute(
        select(fetch_runs)
        .where(fetch_runs.c.started_at >= since, fetch_runs.c.status.in_(("failed", "partial")))
        .order_by(fetch_runs.c.started_at)
    ).mappings().all()
    return [dict(row) for row in rows]


# ------------------------------------------------------------ backfill_tasks


def ensure_tasks(
    conn: Connection,
    *,
    source: str,
    slices: list[dict],
) -> int:
    """Create missing slices. Each slice: {country, query, priority, max_days_old}.

    Existing slices keep their progress; their priority and window are refreshed
    from config. Returns the number of new slices.
    """
    created = 0
    for item in slices:
        existing = conn.execute(
            select(backfill_tasks.c.id).where(
                backfill_tasks.c.source == source,
                backfill_tasks.c.country == item["country"],
                backfill_tasks.c.query == item["query"],
            )
        ).first()
        if existing is None:
            conn.execute(backfill_tasks.insert().values(
                source=source, country=item["country"], query=item["query"],
                priority=item.get("priority", 100), max_days_old=item.get("max_days_old"),
                page=1, mode="backfill", status="pending",
            ))
            created += 1
        else:
            conn.execute(
                update(backfill_tasks).where(backfill_tasks.c.id == existing[0])
                .values(priority=item.get("priority", 100), max_days_old=item.get("max_days_old"))
            )
    return created


def pending_tasks(conn: Connection) -> list[dict]:
    """Slices with pages left to fetch (backfill or re-scan), highest priority first."""
    rows = conn.execute(
        select(backfill_tasks)
        .where(backfill_tasks.c.status == "pending")
        .order_by(backfill_tasks.c.priority, backfill_tasks.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def completed_tasks(conn: Connection) -> list[dict]:
    rows = conn.execute(
        select(backfill_tasks)
        .where(backfill_tasks.c.status == "complete")
        .order_by(backfill_tasks.c.priority, backfill_tasks.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def update_task(conn: Connection, task_id: int, **fields) -> None:
    conn.execute(update(backfill_tasks).where(backfill_tasks.c.id == task_id).values(**fields))


def all_tasks(conn: Connection) -> list[dict]:
    rows = conn.execute(select(backfill_tasks).order_by(backfill_tasks.c.priority, backfill_tasks.c.id))
    return [dict(row) for row in rows.mappings().all()]
