"""Test helpers: fake job source, job builders, and worker config."""

from datetime import datetime, timezone

from sqlalchemy import select

from src.db import jobs_repo
from src.db.schema import jobs
from src.sources.base import FetchResult, Job, JobSource, SearchCriteria
from src.worker.config import DEFAULTS, _merge

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
PER_PAGE = 2


def make_job(external_id, source="adzuna", title="Data Analyst", **kwargs) -> Job:
    kwargs.setdefault("company", "Acme")
    kwargs.setdefault("country", "Canada")
    kwargs.setdefault("country_code", "ca")
    kwargs.setdefault("url", f"https://example.test/{external_id}")
    return Job(source=source, external_id=str(external_id), title=title, **kwargs)


def pages_of(*ids, source="fake"):
    """Jobs split into pages of PER_PAGE."""
    items = [make_job(i, source=source) for i in ids]
    return [items[i:i + PER_PAGE] for i in range(0, len(items), PER_PAGE)]


class FakeSource(JobSource):
    """Serves fixed pages per (country, query) and records every request."""

    name = "fake"

    def __init__(self, pages=None, name=None):
        self.pages = pages if pages is not None else {}
        self.calls: list[SearchCriteria] = []
        if name:
            self.name = name

    def fetch(self, criteria):
        self.calls.append(criteria)
        pages = self.pages.get((criteria.country, criteria.query), [])
        found = pages[criteria.page - 1] if criteria.page <= len(pages) else []
        return FetchResult(jobs=list(found), source=self.name, page_size=len(found),
                           total_available=sum(len(p) for p in pages), raw={"results": len(found)})


def worker_config(**overrides) -> dict:
    base = _merge(DEFAULTS, {
        "queries": ["Data Analyst"],
        "sources": {"adzuna": {"countries": ["ca"], "results_per_page": PER_PAGE,
                               "request_delay_seconds": 0}},
        "quota": {"daily_requests": 100, "weekly_requests": 1000, "monthly_requests": 3000,
                  "safety_margin": 1.0, "backfill_share": 0.7},
        "backfill": {"max_days_old": 30, "max_pages_per_slice": 20, "rescan_interval_hours": 24},
        "lifecycle": {"stale_days_before_closed": 3},
        "link_check": {"enabled": False},
        "raw_responses": {"keep": False},
        "retry": {"attempts": 3, "backoff_seconds": 1},
    })
    return _merge(base, overrides)


def upsert(engine, found, now=T0, query="Data Analyst", country="ca"):
    with engine.begin() as conn:
        return jobs_repo.upsert_jobs(conn, found, query=query, country_code=country, now=now)


def statuses(engine) -> dict[str, str]:
    with engine.connect() as conn:
        return dict(conn.execute(select(jobs.c.external_id, jobs.c.status)).all())


def job_id(engine, external_id, source="adzuna") -> int:
    with engine.connect() as conn:
        return conn.execute(
            select(jobs.c.id).where(jobs.c.external_id == str(external_id), jobs.c.source == source)
        ).scalar_one()
