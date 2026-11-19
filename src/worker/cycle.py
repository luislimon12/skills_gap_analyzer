"""One ingestion cycle: load pending work, query the source, upsert, log a fetch_runs row.

Order inside a cycle:
  1. Completed slices due for their daily full re-scan are queued again.
  2. Incremental updates: newest postings for completed slices (spread over the day).
  3. Initial backfill, then re-scans, page by page until the backfill budget runs out.
  4. Lifecycle: stale jobs past the configured window are closed.
Housekeeping (link check, resume deletion, raw-response pruning, disk check)
runs separately in ``run_housekeeping`` so it still happens if fetching fails.

Every outbound request, retries included, is checked against the budget and
recorded in ``api_requests`` *before* it is sent, timestamped with the time it
is made, so quotas are never exceeded and a run that crosses midnight charges
each request to the right day.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy.engine import Engine

from src.db import jobs_repo, resumes_repo, runs_repo
from src.db.engine import as_utc, utcnow
from src.pipeline.normalize import title_matches_role
from src.sources import build_source
from src.sources.base import FetchResult, JobSource, QuotaExceededError, SearchCriteria, SourceError
from src.worker.alerts import check_disk_space, send_alert
from src.worker.budget import RequestBudget
from src.worker.config import project_path
from src.worker.link_check import run_link_check
from src.worker.raw_store import prune_raw_responses, save_raw_response

log = logging.getLogger(__name__)

ORPHAN_GRACE = timedelta(hours=1)


class BudgetExhausted(Exception):
    pass


@dataclass
class CycleSummary:
    run_id: int | None = None
    status: str = "running"
    error: str | None = None
    found: int = 0
    inserted: int = 0
    updated: int = 0
    reactivated: int = 0
    marked_stale: int = 0
    closed: int = 0
    requests: int = 0
    backfill_requests: int = 0
    sources_used: set[str] = field(default_factory=set)
    stopped_at: str | None = None
    errors: list[str] = field(default_factory=list)

    def run_fields(self, stats: jobs_repo.UpsertStats | None = None) -> dict:
        """Audit-row fields; ``stats`` adds a page that is about to be committed."""
        extra = stats or jobs_repo.UpsertStats()
        return {
            "jobs_found": self.found + extra.found,
            "jobs_inserted": self.inserted + extra.inserted,
            "jobs_updated": self.updated + extra.updated,
            "jobs_reactivated": self.reactivated + extra.reactivated,
            "jobs_marked_stale": self.marked_stale,
            "jobs_closed": self.closed,
            "requests_used": self.requests,
            "backfill_requests": self.backfill_requests,
            "sources_used": ",".join(sorted(self.sources_used)),
        }

    def add(self, stats: jobs_repo.UpsertStats) -> None:
        self.found += stats.found
        self.inserted += stats.inserted
        self.updated += stats.updated
        self.reactivated += stats.reactivated


def fetch_with_retry(
    source: JobSource,
    criteria: SearchCriteria,
    retry_cfg: dict,
    sleep=time.sleep,
    before_attempt: Callable[[], None] | None = None,
) -> FetchResult:
    """Retry transient errors with exponential backoff.

    ``before_attempt`` runs before every attempt (the worker uses it to check
    and charge the request budget; it raises to stop). Permanent and quota
    errors are raised at once. ``requests_used`` on the result (or on the
    raised error) counts every attempt made.
    """
    attempts = max(1, int(retry_cfg.get("attempts", 3)))
    backoff = float(retry_cfg.get("backoff_seconds", 2))
    failed = 0
    for attempt in range(attempts):
        if before_attempt is not None:
            before_attempt()
        try:
            result = source.fetch(criteria)
            result.requests_used += failed
            result.failed_attempts = failed
            return result
        except SourceError as error:
            failed += 1
            error.requests_used = failed
            if error.permanent or isinstance(error, QuotaExceededError) or attempt == attempts - 1:
                raise
            wait = backoff * (2 ** attempt)
            log.warning("Transient error (%s); retrying in %.0fs", error, wait)
            sleep(wait)
    raise AssertionError("unreachable")


class Cycle:
    def __init__(self, engine: Engine, config: dict, source: JobSource, clock: Callable[[], datetime],
                 sleep=time.sleep):
        self.engine = engine
        self.config = config
        self.source = source
        self.clock = clock
        self.now = clock()
        self.sleep = sleep
        adzuna = config["sources"]["adzuna"]
        self.per_page = int(adzuna.get("results_per_page", 50))
        self.title_filter = bool(adzuna.get("title_filter", True))
        self.delay = float(adzuna.get("request_delay_seconds", 3))
        self.countries = list(adzuna.get("countries") or ["ca"])
        self.window = int(config["backfill"].get("max_days_old") or 0) or None
        self.summary = CycleSummary()
        self.incremental_allowance = 0
        raw_cfg = config.get("raw_responses") or {}
        self.raw_dir = project_path(raw_cfg.get("dir", "data/raw/api")) if raw_cfg.get("keep", True) else None

    # ------------------------------------------------------------ helpers

    def slices(self) -> list[dict]:
        """Every (country, search term) pair; earlier terms and countries get higher priority."""
        result = []
        for q_index, query in enumerate(self.config["queries"]):
            for c_index, country in enumerate(self.countries):
                result.append({
                    "country": country, "query": query, "max_days_old": self.window,
                    "priority": q_index * len(self.countries) + c_index,
                })
        return result

    def budget(self) -> RequestBudget:
        with self.engine.connect() as conn:
            return RequestBudget.load(conn, self.config["quota"], self.clock())

    def charge(self, kind: str, count: int = 1, check: bool = True) -> None:
        """Check the budget, then record ``count`` requests before they are sent."""
        if check:
            if not self.budget().can_spend(kind):
                raise BudgetExhausted
            if kind == "incremental" and self.incremental_allowance <= 0:
                raise BudgetExhausted
        with self.engine.begin() as conn:
            runs_repo.record_request(conn, source=self.source.name, kind=kind,
                                     requested_at=self.clock(), run_id=self.summary.run_id, count=count)
        self.summary.requests += count
        if kind == "backfill":
            self.summary.backfill_requests += count
        else:
            self.incremental_allowance -= count

    def _save_progress(self) -> None:
        with self.engine.begin() as conn:
            runs_repo.update_run(conn, self.summary.run_id, **self.summary.run_fields())

    def fetch_page(self, task: dict, page: int, *, kind: str, max_days_old: int | None) -> FetchResult:
        criteria = SearchCriteria(
            query=task["query"], country=task["country"], page=page,
            results_per_page=self.per_page, max_days_old=max_days_old,
        )
        try:
            result = fetch_with_retry(self.source, criteria, self.config.get("retry") or {}, self.sleep,
                                      before_attempt=lambda: self.charge(kind))
        except SourceError:
            self._save_progress()
            raise
        except BudgetExhausted:
            self._save_progress()
            raise

        # Each attempt was charged one request up front. If the adapter itself made
        # more than one request for this page (e.g. LoopCV, then Adzuna), charge the rest.
        extra = result.requests_used - getattr(result, "failed_attempts", 0) - 1
        if extra > 0:
            self.charge(kind, extra, check=False)
        self.summary.sources_used.add(result.source)

        jobs = result.jobs
        if self.title_filter:
            jobs = [job for job in jobs if title_matches_role(job.title, task["query"])]
        with self.engine.begin() as conn:
            stats = jobs_repo.upsert_jobs(conn, jobs, query=task["query"], country_code=task["country"],
                                          now=self.clock())
            # Only full-window searches describe the whole market; incremental
            # (last few days) counts would understate it.
            if kind == "backfill" and page == 1 and result.total_available is not None:
                jobs_repo.insert_market_stats(
                    conn, query=task["query"], country=task["country"],
                    total_count=result.total_available, mean_salary=result.mean_salary, now=self.clock(),
                )
            runs_repo.update_run(conn, self.summary.run_id, **self.summary.run_fields(stats))
        self.summary.add(stats)  # only after the page is committed

        if self.raw_dir is not None and result.raw is not None:
            try:
                save_raw_response(result.raw, directory=self.raw_dir, source=result.source,
                                  country=task["country"], query=task["query"], page=page, now=self.clock())
            except OSError as error:
                log.warning("Could not archive the raw response (%s); continuing", type(error).__name__)
        return result

    # -------------------------------------------------------------- steps

    def schedule_rescans(self) -> None:
        interval = timedelta(hours=float(self.config["backfill"].get("rescan_interval_hours", 24)))
        with self.engine.begin() as conn:
            for task in runs_repo.completed_tasks(conn, self.source.name):
                last_full = as_utc(task["last_full_scan_at"])
                if last_full is None or self.now - last_full >= interval:
                    runs_repo.update_task(conn, task["id"], status="pending", mode="rescan", page=1,
                                          scan_started_at=self.now)

    def run_incremental(self) -> None:
        interval = timedelta(hours=float(self.config["incremental"].get("interval_hours", 1)))
        max_pages = int(self.config["incremental"].get("max_pages", 2))
        with self.engine.begin() as conn:
            tasks = runs_repo.completed_tasks(conn, self.source.name)
        due = [
            t for t in tasks
            if t["last_incremental_at"] is None or self.now - as_utc(t["last_incremental_at"]) >= interval
        ]
        due.sort(key=lambda t: (as_utc(t["last_incremental_at"]) or datetime.min.replace(tzinfo=self.now.tzinfo),
                                t["priority"]))
        self.incremental_allowance = self.budget().incremental_allowance(self.now)
        for index, task in enumerate(due):
            if self.incremental_allowance <= 0:
                log.info("Incremental allowance for this hour used; %d slice(s) wait for the next run",
                         len(due) - index)
                return
            since = as_utc(task["last_incremental_at"] or task["last_full_scan_at"]) or self.now
            days = max(1, math.ceil((self.now - since).total_seconds() / 86400))
            if self.window:
                days = min(days, self.window)
            finished = False
            for page in range(1, max_pages + 1):
                try:
                    result = self.fetch_page(task, page, kind="incremental", max_days_old=days)
                except BudgetExhausted:
                    log.info("Request budget used up during incremental updates")
                    return
                except SourceError as error:
                    if error.permanent or isinstance(error, QuotaExceededError):
                        raise
                    self.summary.errors.append(f"{task['country']}:{task['query']}: {error}")
                    break
                if result.page_size < self.per_page or page == max_pages:
                    finished = True
                    break
                self.sleep(self.delay)
            # Advance the checkpoint only when every page of the window was fetched,
            # so a failed page is searched again next time with the same window.
            if finished:
                with self.engine.begin() as conn:
                    runs_repo.update_task(conn, task["id"], last_incremental_at=self.now, last_run_at=self.now)
            self.sleep(self.delay)

    def run_backfill(self) -> None:
        max_depth = int(self.config["backfill"].get("max_pages_per_slice", 20))
        with self.engine.begin() as conn:
            tasks = runs_repo.pending_tasks(conn, self.source.name)
        for task in tasks:
            scan_started = as_utc(task["scan_started_at"])
            if scan_started is None:
                scan_started = self.now
                with self.engine.begin() as conn:
                    runs_repo.update_task(conn, task["id"], scan_started_at=scan_started)
            page = int(task["page"] or 1)
            while True:
                try:
                    result = self.fetch_page(task, page, kind="backfill", max_days_old=task["max_days_old"])
                except BudgetExhausted:
                    self.summary.stopped_at = f"{task['mode']} {task['country']} '{task['query']}' page {page}"
                    log.info("Backfill budget used up; stopped at %s", self.summary.stopped_at)
                    return
                except SourceError as error:
                    if error.permanent or isinstance(error, QuotaExceededError):
                        raise
                    self.summary.errors.append(f"{task['country']}:{task['query']} page {page}: {error}")
                    with self.engine.begin() as conn:
                        runs_repo.update_task(conn, task["id"], page=page, last_run_at=self.now,
                                              last_error=str(error)[:500])
                    break

                short_page = result.page_size < self.per_page
                hit_depth = page >= max_depth
                if short_page or hit_depth:
                    self._complete_slice(task, scan_started, complete_scan=short_page)
                    break
                page += 1
                with self.engine.begin() as conn:
                    runs_repo.update_task(conn, task["id"], page=page, last_run_at=self.now, last_error=None)
                self.sleep(self.delay)
            self.sleep(self.delay)

    def _complete_slice(self, task: dict, scan_started: datetime, *, complete_scan: bool) -> None:
        previous = as_utc(task["previous_scan_started_at"])
        with self.engine.begin() as conn:
            marked = 0
            if not complete_scan:
                log.warning(
                    "Slice %s '%s' has more pages than max_pages_per_slice; stale marking skipped. "
                    "Narrow the time window or the search term.", task["country"], task["query"],
                )
            elif previous is not None:
                # Unseen in this scan *and* the previous one: see mark_unseen_stale.
                marked = jobs_repo.mark_unseen_stale(
                    conn, country_code=task["country"], query=task["query"],
                    scan_started_at=scan_started, previous_scan_started_at=previous, now=self.now,
                )
            runs_repo.update_task(
                conn, task["id"], status="complete", mode="incremental", page=1,
                scan_started_at=None, last_full_scan_at=self.now, last_incremental_at=self.now,
                previous_scan_started_at=scan_started if complete_scan else task["previous_scan_started_at"],
                last_run_at=self.now, last_error=None,
            )
        self.summary.marked_stale += marked
        log.info("Slice %s '%s' %s complete (%d marked stale)",
                 task["country"], task["query"], task["mode"], marked)

    def close_stale(self) -> None:
        days = int(self.config["lifecycle"].get("stale_days_before_closed", 3))
        with self.engine.begin() as conn:
            self.summary.closed += jobs_repo.close_stale_jobs(conn, stale_days=days, now=self.now)

    # ---------------------------------------------------------------- run

    def run(self) -> CycleSummary:
        s = self.summary
        with self.engine.begin() as conn:
            s.run_id = runs_repo.start_run(conn, self.source.name, self.now)
            runs_repo.ensure_tasks(conn, source=self.source.name, slices=self.slices())
        try:
            if not self.source.is_configured():
                raise SourceError(f"Source '{self.source.name}' is not configured (missing API keys).",
                                  permanent=True)
            self.schedule_rescans()
            self.run_incremental()
            self.run_backfill()
            s.status = "partial" if s.errors else "success"
            s.error = "; ".join(s.errors)[:2000] or None
        except QuotaExceededError as error:
            s.status, s.error = "partial", str(error)
        except SourceError as error:
            s.status, s.error = "failed", str(error)
        except Exception as error:  # noqa: BLE001 - the run row must always be closed
            log.exception("Worker cycle crashed")
            s.status, s.error = "failed", f"{type(error).__name__}: {error}"
        try:
            self.close_stale()
        except Exception as error:  # noqa: BLE001
            log.exception("Closing stale jobs failed")
            s.status, s.error = "failed", f"lifecycle: {type(error).__name__}: {error}"
        with self.engine.begin() as conn:
            runs_repo.finish_run(conn, s.run_id, status=s.status, error_message=s.error,
                                 now=self.clock(), **s.run_fields())
        log.info(
            "Run %s %s: found %d, inserted %d, reactivated %d, stale %d, closed %d, requests %d (backfill %d)%s",
            s.run_id, s.status, s.found, s.inserted, s.reactivated, s.marked_stale, s.closed,
            s.requests, s.backfill_requests, f"; stopped at {s.stopped_at}" if s.stopped_at else "",
        )
        if s.status == "failed":
            send_alert("Worker run failed", f"Run {s.run_id}: {s.error}", key="run-failure",
                       min_hours_between=float(self.config["alerts"].get("min_hours_between_emails", 6)))
        return s


def run_cycle(
    engine: Engine,
    config: dict,
    *,
    source: JobSource | None = None,
    now: datetime | None = None,
    sleep=time.sleep,
    clock: Callable[[], datetime] | None = None,
) -> CycleSummary:
    """Run one cycle. ``now`` freezes the clock (tests); otherwise real UTC time is used."""
    if clock is None:
        clock = (lambda: now) if now is not None else utcnow
    return Cycle(engine, config, source or build_source(config), clock, sleep).run()


def cleanup_resumes(engine: Engine, config: dict, now: datetime | None = None) -> int:
    """Delete resumes past delete_after (files, analyses and rows) plus orphaned files.

    Orphaned files younger than ORPHAN_GRACE are left alone: the web app writes
    the encrypted file just before committing its row, and the sweep must not
    delete a file that an upload in progress still owns.
    """
    now = now or utcnow()
    storage_dir = project_path((config.get("resumes") or {}).get("storage_dir", "data/private/resumes"))
    with engine.begin() as conn:
        deleted = resumes_repo.delete_expired_resumes(conn, now)
        orphans = resumes_repo.orphaned_files(conn, storage_dir)
    cutoff = time.time() - ORPHAN_GRACE.total_seconds()
    removed = 0
    for path in orphans:
        try:
            if Path(path).stat().st_mtime > cutoff:
                continue
            Path(path).unlink(missing_ok=True)
            removed += 1
        except OSError:
            log.error("Could not delete orphaned resume file %s", Path(path).name)
    if deleted or removed:
        log.info("Resume cleanup: %d expired resume(s), %d orphaned file(s) deleted", deleted, removed)
    return deleted


def run_housekeeping(engine: Engine, config: dict, *, now: datetime | None = None,
                     link_session=None, sleep=time.sleep, resolver=None) -> dict:
    now = now or utcnow()
    result: dict = {}
    steps = (
        ("resumes_deleted", lambda: cleanup_resumes(engine, config, now)),
        ("raw_pruned", lambda: prune_raw_responses(
            project_path((config.get("raw_responses") or {}).get("dir", "data/raw/api")),
            int((config.get("raw_responses") or {}).get("retention_days", 7)),
        )),
        ("link_check", lambda: run_link_check(engine, config, now, session=link_session, sleep=sleep,
                                              resolver=resolver)),
    )
    for name, step in steps:
        try:
            result[name] = step()
        except Exception as error:  # noqa: BLE001 - one failed step must not block the others
            log.exception("Housekeeping step %s failed", name)
            result[name] = f"error: {type(error).__name__}"
            send_alert(f"Housekeeping step {name} failed", f"{type(error).__name__}: {error}",
                       key=f"housekeeping-{name}")
    low_disk = check_disk_space(float(config["alerts"].get("disk_free_min_percent", 10)))
    if low_disk:
        send_alert("Low disk space", low_disk, key="low-disk",
                   min_hours_between=float(config["alerts"].get("min_hours_between_emails", 6)))
    result["low_disk"] = low_disk
    return result
