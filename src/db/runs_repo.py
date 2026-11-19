"""fetch_runs (ingestion audit log), api_requests (budget ledger) and backfill_tasks (slice queue)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.engine import Connection

from src.db.engine import utcnow
from src.db.schema import api_requests, backfill_tasks, fetch_runs

# ---------------------------------------------------------------- fetch_runs


def start_run(conn: Connection, source: str, now: datetime | None = None) -> int:
    return conn.execute(fetch_runs.insert().values(
        started_at=now or utcnow(), source=source, status="running",
        jobs_found=0, jobs_inserted=0, jobs_updated=0, jobs_reactivated=0,
        jobs_marked_stale=0, jobs_closed=0, requests_used=0, backfill_requests=0,
        sources_used="",
    )).inserted_primary_key[0]


def update_run(conn: Connection, run_id: int, **fields) -> None:
    """Save running totals mid-cycle so the audit row survives a crash."""
    conn.execute(update(fetch_runs).where(fetch_runs.c.id == run_id).values(**fields))


def finish_run(conn: Connection, run_id: int, *, status: str, now: datetime | None = None, **fields) -> None:
    if fields.get("error_message"):
        fields["error_message"] = str(fields["error_message"])[:2000]
    conn.execute(
        update(fetch_runs).where(fetch_runs.c.id == run_id)
        .values(status=status, finished_at=now or utcnow(), **fields)
    )


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


# -------------------------------------------------------------- api_requests


def record_request(conn: Connection, *, source: str, kind: str, requested_at: datetime,
                   run_id: int | None = None, count: int = 1) -> None:
    """Charge ``count`` requests to the budget. Called before the request is sent."""
    for _ in range(max(0, count)):
        conn.execute(api_requests.insert().values(
            source=source, kind=kind, requested_at=requested_at, run_id=run_id,
        ))


def requests_used_since(conn: Connection, since: datetime) -> tuple[int, int]:
    """(all requests, backfill/re-scan requests) made at or after ``since``."""
    total, backfill = conn.execute(
        select(
            func.count(api_requests.c.id),
            func.coalesce(func.sum(case((api_requests.c.kind == "backfill", 1), else_=0)), 0),
        ).where(api_requests.c.requested_at >= since)
    ).one()
    return int(total), int(backfill)


# ------------------------------------------------------------ backfill_tasks


def ensure_tasks(conn: Connection, *, source: str, slices: list[dict]) -> int:
    """Sync the queue with the configured slices. Each slice: {country, query, priority, max_days_old}.

    * Missing slices are created.
    * Configured slices keep their progress; priority and window are refreshed.
      A slice that was inactive is queued again (a re-scan if it had finished one).
    * Slices of another source, or no longer configured, become ``inactive``
      (their page and scan history are kept) and are never processed.
    Returns the number of new slices.
    """
    created = 0
    configured = {(item["country"], item["query"]) for item in slices}
    for item in slices:
        existing = conn.execute(
            select(backfill_tasks.c.id, backfill_tasks.c.status, backfill_tasks.c.last_full_scan_at).where(
                backfill_tasks.c.source == source,
                backfill_tasks.c.country == item["country"],
                backfill_tasks.c.query == item["query"],
            )
        ).first()
        values = {"priority": item.get("priority", 100), "max_days_old": item.get("max_days_old")}
        if existing is None:
            conn.execute(backfill_tasks.insert().values(
                source=source, country=item["country"], query=item["query"],
                page=1, mode="backfill", status="pending", **values,
            ))
            created += 1
            continue
        task_id, status, last_full_scan_at = existing
        if status == "inactive":
            values.update(status="pending", mode="rescan" if last_full_scan_at else "backfill")
        conn.execute(update(backfill_tasks).where(backfill_tasks.c.id == task_id).values(**values))

    for row in conn.execute(
        select(backfill_tasks.c.id, backfill_tasks.c.source, backfill_tasks.c.country, backfill_tasks.c.query)
        .where(backfill_tasks.c.status != "inactive")
    ):
        if row.source != source or (row.country, row.query) not in configured:
            conn.execute(update(backfill_tasks).where(backfill_tasks.c.id == row.id).values(status="inactive"))
    return created


def _active(source: str):
    return and_(backfill_tasks.c.source == source, backfill_tasks.c.status != "inactive")


def pending_tasks(conn: Connection, source: str) -> list[dict]:
    """Slices with pages left to fetch.

    Initial backfill goes first (by priority) so a frequently re-scanned
    high-priority slice can never starve a slice that hasn't been fetched yet;
    re-scans follow, oldest scan first.
    """
    rows = conn.execute(
        select(backfill_tasks)
        .where(_active(source), backfill_tasks.c.status == "pending")
        .order_by(
            case((backfill_tasks.c.mode == "backfill", 0), else_=1),
            case((backfill_tasks.c.mode == "backfill", backfill_tasks.c.priority), else_=0),
            case((backfill_tasks.c.last_full_scan_at.is_(None), 0), else_=1),
            backfill_tasks.c.last_full_scan_at,
            backfill_tasks.c.priority,
            backfill_tasks.c.id,
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def completed_tasks(conn: Connection, source: str) -> list[dict]:
    rows = conn.execute(
        select(backfill_tasks)
        .where(_active(source), backfill_tasks.c.status == "complete")
        .order_by(backfill_tasks.c.priority, backfill_tasks.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def update_task(conn: Connection, task_id: int, **fields) -> None:
    conn.execute(update(backfill_tasks).where(backfill_tasks.c.id == task_id).values(**fields))


def all_tasks(conn: Connection, include_inactive: bool = False) -> list[dict]:
    query = select(backfill_tasks).order_by(backfill_tasks.c.priority, backfill_tasks.c.id)
    if not include_inactive:
        query = query.where(or_(backfill_tasks.c.status != "inactive"))
    return [dict(row) for row in conn.execute(query).mappings().all()]
