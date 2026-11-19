"""Read/write job postings and their lifecycle (active -> stale -> closed)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.engine import Connection

from src.db.engine import utcnow
from src.db.schema import job_queries, jobs, market_stats
from src.sources.base import Job

log = logging.getLogger(__name__)

_UPDATABLE = (
    "title", "company", "location", "country", "country_code", "region",
    "salary_min", "salary_max", "salary_is_predicted", "description",
    "description_is_truncated", "url", "posted_date", "fetched_at",
    "contract_time", "category",
)


@dataclass
class UpsertStats:
    found: int = 0
    inserted: int = 0
    updated: int = 0
    reactivated: int = 0

    def add(self, other: "UpsertStats") -> None:
        self.found += other.found
        self.inserted += other.inserted
        self.updated += other.updated
        self.reactivated += other.reactivated


def _job_values(job: Job) -> dict:
    values = {name: getattr(job, name) for name in _UPDATABLE}
    for text_field in ("title", "company", "location", "country", "country_code", "region",
                       "description", "url", "category"):
        values[text_field] = values[text_field] or ""
    return values


def upsert_jobs(
    conn: Connection,
    found_jobs: list[Job],
    *,
    query: str,
    country_code: str,
    now: datetime | None = None,
) -> UpsertStats:
    """Insert new jobs, refresh known ones, and reactivate any that had closed.

    Every job returned by a fetch gets ``last_seen_at = now``. Duplicates are
    impossible because of the unique (source, external_id) constraint.
    """
    now = now or utcnow()
    stats = UpsertStats()
    seen_in_batch: set[tuple[str, str]] = set()
    for job in found_jobs:
        key = (job.source, job.external_id)
        if key in seen_in_batch:
            continue
        seen_in_batch.add(key)
        stats.found += 1

        existing = conn.execute(
            select(jobs.c.id, jobs.c.status).where(
                jobs.c.source == job.source, jobs.c.external_id == job.external_id
            )
        ).first()
        values = _job_values(job)
        if existing is None:
            job_id = conn.execute(
                jobs.insert().values(
                    source=job.source,
                    external_id=job.external_id,
                    first_seen_at=now,
                    last_seen_at=now,
                    status="active",
                    **values,
                )
            ).inserted_primary_key[0]
            stats.inserted += 1
        else:
            job_id, old_status = existing
            values.update(last_seen_at=now)
            if old_status != "active":
                values.update(status="active", stale_since=None, closed_at=None)
                stats.reactivated += 1
                log.info("Job %s:%s reactivated (was %s)", job.source, job.external_id, old_status)
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(**values))
            stats.updated += 1

        link = conn.execute(
            select(job_queries.c.job_id).where(
                job_queries.c.job_id == job_id,
                job_queries.c.country_code == country_code,
                job_queries.c.query == query,
            )
        ).first()
        if link is None:
            conn.execute(job_queries.insert().values(
                job_id=job_id, country_code=country_code, query=query,
                first_seen_at=now, last_seen_at=now,
            ))
        else:
            conn.execute(
                update(job_queries)
                .where(
                    job_queries.c.job_id == job_id,
                    job_queries.c.country_code == country_code,
                    job_queries.c.query == query,
                )
                .values(last_seen_at=now)
            )
    return stats


def mark_unseen_stale(
    conn: Connection,
    *,
    country_code: str,
    query: str,
    scan_started_at: datetime,
    now: datetime | None = None,
) -> int:
    """After a full re-scan of a slice, mark its active jobs that were not seen as stale.

    A job also found by another slice during the scan has a newer
    ``last_seen_at`` and is left alone.
    """
    now = now or utcnow()
    slice_job_ids = select(job_queries.c.job_id).where(
        job_queries.c.country_code == country_code, job_queries.c.query == query
    )
    result = conn.execute(
        update(jobs)
        .where(
            jobs.c.status == "active",
            jobs.c.last_seen_at < scan_started_at,
            jobs.c.id.in_(slice_job_ids),
        )
        .values(status="stale", stale_since=now)
    )
    return result.rowcount or 0


def close_stale_jobs(conn: Connection, *, stale_days: int, now: datetime | None = None) -> int:
    """Close jobs that have been stale for at least ``stale_days`` days."""
    now = now or utcnow()
    cutoff = now - timedelta(days=stale_days)
    result = conn.execute(
        update(jobs)
        .where(jobs.c.status == "stale", jobs.c.stale_since <= cutoff)
        .values(status="closed", closed_at=now)
    )
    return result.rowcount or 0


def mark_closed(conn: Connection, job_id: int, now: datetime | None = None) -> None:
    now = now or utcnow()
    conn.execute(
        update(jobs).where(jobs.c.id == job_id)
        .values(status="closed", closed_at=now, last_checked_at=now)
    )


def mark_checked(conn: Connection, job_id: int, now: datetime | None = None) -> None:
    conn.execute(update(jobs).where(jobs.c.id == job_id).values(last_checked_at=now or utcnow()))


def link_checks_since(conn: Connection, since: datetime) -> int:
    return conn.execute(
        select(func.count()).select_from(jobs).where(jobs.c.last_checked_at >= since)
    ).scalar_one()


def jobs_due_for_link_check(conn: Connection, *, limit: int, now: datetime | None = None) -> list[dict]:
    """Active jobs not checked in the last day, never-checked first."""
    if limit <= 0:
        return []
    now = now or utcnow()
    rows = conn.execute(
        select(jobs.c.id, jobs.c.url, jobs.c.source, jobs.c.external_id)
        .where(
            jobs.c.status == "active",
            jobs.c.url != "",
            or_(jobs.c.last_checked_at.is_(None), jobs.c.last_checked_at < now - timedelta(days=1)),
        )
        .order_by(case((jobs.c.last_checked_at.is_(None), 0), else_=1), jobs.c.last_checked_at, jobs.c.id)
        .limit(limit)
    ).mappings().all()
    return [dict(row) for row in rows]


def _posting_dict(row) -> dict:
    """Map a jobs row to the posting shape the scoring code reads."""
    posted = row["posted_date"]
    return {
        "id": row["id"],
        "source": row["source"],
        "source_id": row["external_id"],
        "role": row["query"],
        "title": row["title"],
        "company": row["company"],
        "location": row["location"],
        "country": row["country"],
        "country_code": row["country_code"],
        "region": row["region"],
        "posted_date": posted.isoformat() if hasattr(posted, "isoformat") else posted,
        "url": row["url"],
        "full_text": row["description"],
        "is_truncated": bool(row["description_is_truncated"]),
        "requirement_level": None,
        "salary_min": row["salary_min"],
        "salary_max": row["salary_max"],
        "salary_is_predicted": bool(row["salary_is_predicted"]),
        "contract_time": row["contract_time"],
        "category": row["category"],
        "status": row["status"],
        "no_longer_listed": row["status"] == "closed",
        "last_seen_at": row["last_seen_at"],
    }


def load_jobs_for_role(conn: Connection, role: str, *, include_closed: bool = False) -> list[dict]:
    """Jobs found by searches for ``role`` (any country). Closed jobs are hidden by default."""
    query = (
        select(jobs, job_queries.c.query)
        .join(job_queries, job_queries.c.job_id == jobs.c.id)
        .where(func.lower(job_queries.c.query) == role.strip().lower())
        .order_by(jobs.c.posted_date.desc(), jobs.c.id)
    )
    if not include_closed:
        query = query.where(jobs.c.status != "closed")
    postings, seen = [], set()
    for row in conn.execute(query).mappings():
        if row["id"] in seen:  # same job found in more than one country slice
            continue
        seen.add(row["id"])
        postings.append(_posting_dict(row))
    return postings


def tracked_queries(conn: Connection) -> list[str]:
    return [row[0] for row in conn.execute(select(job_queries.c.query).distinct().order_by(job_queries.c.query))]


def job_status_counts(conn: Connection) -> dict[str, int]:
    counts = {status: 0 for status in ("active", "stale", "closed")}
    for status, count in conn.execute(select(jobs.c.status, func.count()).group_by(jobs.c.status)):
        counts[status] = count
    return counts


def insert_market_stats(
    conn: Connection, *, query: str, country: str, total_count: int | None,
    mean_salary: float | None, now: datetime | None = None,
) -> None:
    conn.execute(market_stats.insert().values(
        query=query, country=country, total_count=total_count,
        mean_salary=mean_salary, fetched_at=now or utcnow(),
    ))


def latest_market_stats(conn: Connection, query: str) -> list[dict]:
    """Latest Adzuna count / mean salary for ``query``, one entry per country."""
    latest = (
        select(market_stats.c.country, func.max(market_stats.c.fetched_at).label("fetched_at"))
        .where(func.lower(market_stats.c.query) == query.strip().lower())
        .group_by(market_stats.c.country)
        .subquery()
    )
    rows = conn.execute(
        select(market_stats).join(
            latest,
            and_(market_stats.c.country == latest.c.country,
                 market_stats.c.fetched_at == latest.c.fetched_at),
        ).where(func.lower(market_stats.c.query) == query.strip().lower())
    ).mappings().all()
    return [dict(row) for row in rows]
