"""Ingestion worker command line. Run from the project root:

    python -m src.worker run        # one cycle (the scheduler calls this every hour)
    python -m src.worker cleanup    # delete resumes past their 30-day deletion date
    python -m src.worker status     # jobs, slice progress, today's request use, recent runs
    python -m src.worker report --days 1 [--email]   # failed runs (daily check)

Only one ``run`` can be active at a time (data/worker.lock).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from logging.handlers import RotatingFileHandler

from src.db import jobs_repo, runs_repo
from src.db.engine import PROJECT_ROOT, get_engine, utcnow
from src.db.migrate import upgrade
from src.worker.alerts import send_alert
from src.worker.budget import RequestBudget
from src.worker.config import load_ingestion_config
from src.worker.cycle import cleanup_resumes, run_cycle, run_housekeeping
from src.worker.lock import SingleInstanceLock, WorkerAlreadyRunning

LOCK_PATH = PROJECT_ROOT / "data" / "worker.lock"
LOG_PATH = PROJECT_ROOT / "data" / "logs" / "worker.log"


def setup_logging(verbose: bool = False) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=5, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    # urllib3 debug logs include full request URLs (with API keys); keep them quiet.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_run(args) -> int:
    config = load_ingestion_config()
    try:
        with SingleInstanceLock(LOCK_PATH):
            engine = get_engine()
            upgrade(engine)
            summary = run_cycle(engine, config)
            if not args.skip_housekeeping:
                run_housekeeping(engine, config)
    except WorkerAlreadyRunning:
        logging.getLogger(__name__).warning("Another worker run is still active; skipping this one.")
        return 0
    return 1 if summary.status == "failed" else 0


def cmd_cleanup(_args) -> int:
    config = load_ingestion_config()
    engine = get_engine()
    upgrade(engine)
    deleted = cleanup_resumes(engine, config)
    print(f"Deleted {deleted} expired resume(s).")
    return 0


def cmd_status(_args) -> int:
    config = load_ingestion_config()
    engine = get_engine()
    upgrade(engine)
    now = utcnow()
    with engine.connect() as conn:
        counts = jobs_repo.job_status_counts(conn)
        tasks = runs_repo.all_tasks(conn)
        budget = RequestBudget.load(conn, config["quota"], now)
        runs = runs_repo.recent_runs(conn, 10)
    print(f"Jobs: {counts['active']} active, {counts['stale']} stale, {counts['closed']} closed")
    print(f"Requests today: {budget.used_today} of {budget.daily_limit} allowed "
          f"(backfill {budget.backfill_today} of {budget.backfill_limit}); this month {budget.used_month}")
    done = sum(t["status"] == "complete" for t in tasks)
    print(f"Slices: {done} of {len(tasks)} complete")
    for task in tasks:
        if task["status"] != "complete":
            print(f"  {task['mode']:<11} {task['country']} {task['query']!r}: next page {task['page']}"
                  + (f" (last error: {task['last_error']})" if task["last_error"] else ""))
    print("Recent runs:")
    for run in runs:
        print(f"  #{run['id']} {run['started_at']} {run['status']:<8} found {run['jobs_found']}, "
              f"new {run['jobs_inserted']}, requests {run['requests_used']}"
              + (f" - {run['error_message']}" if run["error_message"] else ""))
    return 0


def cmd_report(args) -> int:
    engine = get_engine()
    upgrade(engine)
    since = utcnow() - timedelta(days=args.days)
    with engine.connect() as conn:
        failures = runs_repo.failed_runs_since(conn, since)
    if not failures:
        print(f"No failed or partial runs in the last {args.days} day(s).")
        return 0
    lines = [f"#{r['id']} {r['started_at']} {r['status']}: {r['error_message']}" for r in failures]
    print("\n".join(lines))
    if args.email:
        send_alert(f"{len(failures)} problem run(s) in the last {args.days} day(s)", "\n".join(lines),
                   min_hours_between=0)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.worker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one ingestion cycle")
    run.add_argument("--skip-housekeeping", action="store_true",
                     help="skip link check, resume cleanup and raw-response pruning")
    run.set_defaults(func=cmd_run)
    sub.add_parser("cleanup", help="delete resumes past their deletion date").set_defaults(func=cmd_cleanup)
    sub.add_parser("status", help="show ingestion progress").set_defaults(func=cmd_status)
    report = sub.add_parser("report", help="list failed runs")
    report.add_argument("--days", type=int, default=1)
    report.add_argument("--email", action="store_true", help="also email the list if there are failures")
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
