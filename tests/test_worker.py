"""Ingestion worker: budget, backfill, re-scan lifecycle, retries, fallback, lock, housekeeping, CLI."""

import json
import os
import shutil
import time
from datetime import timedelta

import pytest
import yaml
from sqlalchemy import func, select

from src.db import resumes_repo
from src.db.engine import make_engine
from src.db.migrate import upgrade
from src.db.runs_repo import all_tasks, recent_runs
from src.db.schema import api_requests, backfill_tasks, market_stats
from src.sources.base import SearchCriteria, SourceError
from src.sources.fallback import FallbackSource
from src.worker import __main__ as worker_cli
from src.worker import alerts
from src.worker import cycle as cycle_module
from src.worker.budget import RequestBudget
from src.worker.config import ConfigError, load_ingestion_config
from src.worker.cycle import cleanup_resumes, fetch_with_retry, run_cycle, run_housekeeping
from src.worker.link_check import UnsafeDestination, check_public_url, run_link_check
from src.worker.lock import SingleInstanceLock, WorkerAlreadyRunning
from src.worker.raw_store import prune_raw_responses, save_raw_response
from tests.helpers import T0, FakeSource, make_job, pages_of, statuses, worker_config

QUERY = ("ca", "Data Analyst")
PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731 - resolver stub: every host is public


@pytest.fixture(autouse=True)
def alerts_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(cycle_module, "send_alert", lambda subject, body, **kw: sent.append(subject))
    return sent


def run(engine, source, now, cfg=None):
    return run_cycle(engine, cfg or worker_config(), source=source, now=now, sleep=lambda s: None)


def tasks(engine):
    with engine.connect() as conn:
        return all_tasks(conn)


def last_run(engine):
    with engine.connect() as conn:
        return recent_runs(conn, 1)[0]


def count(engine, table):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


class FlakySource(FakeSource):
    def __init__(self, pages, failures):
        super().__init__(pages)
        self.failures = failures
        self.attempts = 0

    def fetch(self, criteria):
        self.attempts += 1
        if self.failures:
            self.failures -= 1
            raise SourceError("HTTP 503")
        return super().fetch(criteria)


# ------------------------------------------------------------------ backfill


def test_backfill_pages_until_a_short_page_and_logs_the_run(engine):
    source = FakeSource({QUERY: pages_of(1, 2, 3)})
    summary = run(engine, source, T0)
    assert summary.status == "success"
    assert [c.page for c in source.calls] == [1, 2]
    assert all(c.sort_by == "date" and c.max_days_old == 30 for c in source.calls)
    task = tasks(engine)[0]
    assert (task["status"], task["mode"], task["last_full_scan_at"] is not None) == ("complete", "incremental", True)
    row = last_run(engine)
    assert (row["status"], row["source"], row["jobs_found"], row["jobs_inserted"]) == ("success", "fake", 3, 3)
    assert row["requests_used"] == row["backfill_requests"] == 2
    assert count(engine, api_requests) == 2  # every request is in the budget ledger


def test_slices_cover_each_country_and_term_highest_value_first(engine):
    cfg = worker_config(queries=["Data Analyst", "Electrician"], sources={"adzuna": {"countries": ["ca", "us"]}})
    run(engine, FakeSource(), T0, cfg)
    assert [(t["query"], t["country"]) for t in tasks(engine)] == [
        ("Data Analyst", "ca"), ("Data Analyst", "us"), ("Electrician", "ca"), ("Electrician", "us")]


def test_slices_removed_from_config_stop_running(engine):
    source = FakeSource()
    run(engine, source, T0, worker_config(sources={"adzuna": {"countries": ["ca", "us"]}}))
    source.calls.clear()
    run(engine, source, T0 + timedelta(days=2), worker_config(sources={"adzuna": {"countries": ["ca"]}}))
    assert source.calls and all(call.country == "ca" for call in source.calls)
    with engine.connect() as conn:
        assert {row.country: row.status for row in conn.execute(select(backfill_tasks))}["us"] == "inactive"


def test_title_filter_drops_unrelated_titles(engine):
    source = FakeSource({QUERY: [[make_job(1, source="fake"), make_job(2, source="fake", title="Business Analyst")], []]})
    run(engine, source, T0)
    assert set(statuses(engine)) == {"1"}


def test_backfill_stops_at_its_budget_and_resumes_the_next_day(engine):
    cfg = worker_config(quota={"daily_requests": 10, "backfill_share": 0.5})  # 5 backfill requests a day
    source = FakeSource({QUERY: pages_of(*range(1, 19))})  # 9 full pages, then an empty one
    first = run(engine, source, T0, cfg)
    assert (first.backfill_requests, first.stopped_at) == (5, "backfill ca 'Data Analyst' page 6")
    assert tasks(engine)[0]["page"] == 6
    assert run(engine, source, T0 + timedelta(hours=1), cfg).backfill_requests == 0
    next_day = run(engine, source, T0 + timedelta(days=1), cfg)
    assert [c.page for c in source.calls[-5:]] == [6, 7, 8, 9, 10]
    assert next_day.backfill_requests == 5
    assert tasks(engine)[0]["status"] == "complete"


def test_requests_are_charged_to_the_day_they_are_made(engine):
    """A run that starts at 23:59 and keeps going after midnight charges each day correctly."""
    late = T0.replace(hour=23, minute=59)
    clock = {"now": late}

    class CrossesMidnight(FakeSource):
        def fetch(self, criteria):
            result = super().fetch(criteria)
            clock["now"] = late + timedelta(minutes=2)  # midnight passes after the first page
            return result

    cfg = worker_config(quota={"daily_requests": 4, "backfill_share": 1.0})
    summary = run_cycle(engine, cfg, source=CrossesMidnight({QUERY: pages_of(*range(1, 15))}),
                        clock=lambda: clock["now"], sleep=lambda s: None)
    assert summary.requests == 5  # 1 on the first day + the full 4 on the next
    with engine.connect() as conn:
        days = [row[0] for row in conn.execute(select(api_requests.c.requested_at))]
    assert sorted(day.day for day in days) == [late.day] + [late.day + 1] * 4
    assert summary.stopped_at == "backfill ca 'Data Analyst' page 6"


def test_retries_are_checked_against_the_budget_before_they_are_sent(engine):
    source = FlakySource({QUERY: pages_of(1, 2, 3)}, failures=1)
    summary = run(engine, source, T0, worker_config(quota={"daily_requests": 1, "backfill_share": 1.0}))
    assert source.attempts == 1  # the retry would exceed the quota, so it is never sent
    assert summary.requests == 1 and count(engine, api_requests) == 1


def test_request_budget_caps_daily_use_and_reserves_room_for_updates():
    capped = RequestBudget.from_config({"daily_requests": 10, "safety_margin": 0.9})
    spent = 0
    while capped.can_spend():
        capped.spend(1)
        spent += 1
    assert spent == 9
    shared = RequestBudget.from_config({"daily_requests": 100, "safety_margin": 1.0, "backfill_share": 0.7})
    while shared.can_spend("backfill"):
        shared.spend(1, "backfill")
    assert (shared.backfill_this_run, shared.remaining_incremental()) == (70, 30)
    monthly = RequestBudget.from_config({"daily_requests": 100, "monthly_requests": 50, "safety_margin": 1.0},
                                        used_month=50)
    assert not monthly.can_spend()


def test_rescans_never_starve_a_slice_that_has_not_been_fetched(engine):
    cfg = worker_config(queries=["Data Analyst", "Electrician"],
                        quota={"daily_requests": 2, "backfill_share": 0.5})  # one backfill request a day
    source = FakeSource()
    run(engine, source, T0, cfg)
    assert [c.query for c in source.calls] == ["Data Analyst"]
    run(engine, source, T0 + timedelta(days=1), cfg)  # Data Analyst's re-scan is due too
    assert source.calls[-1].query == "Electrician"


# ----------------------------------------------------------------- lifecycle


def test_job_goes_stale_after_two_missed_scans_then_closes_and_reactivates(engine):
    pages = {QUERY: pages_of(1, 2, 3)}
    source = FakeSource(pages)
    run(engine, source, T0)
    pages[QUERY] = pages_of(1, 2)  # job 3 disappears
    assert run(engine, source, T0 + timedelta(hours=25)).marked_stale == 0  # one miss could be a page shift
    assert run(engine, source, T0 + timedelta(hours=50)).marked_stale == 1
    assert statuses(engine)["3"] == "stale"
    assert run(engine, source, T0 + timedelta(days=6)).closed == 1
    assert statuses(engine)["3"] == "closed"
    pages[QUERY] = pages_of(1, 2, 3)  # it comes back
    assert run(engine, source, T0 + timedelta(days=7)).reactivated == 1
    assert statuses(engine) == {"1": "active", "2": "active", "3": "active"}


def test_incremental_update_fetches_new_postings_without_touching_market_stats(engine):
    pages = {QUERY: pages_of(1, 2, 3)}
    source = FakeSource(pages)
    run(engine, source, T0)
    assert count(engine, market_stats) == 1
    pages[QUERY] = [[make_job(4, source="fake")]]
    summary = run(engine, source, T0 + timedelta(hours=1))
    assert (source.calls[-1].page, source.calls[-1].max_days_old) == (1, 1)
    assert (summary.inserted, summary.backfill_requests) == (1, 0)
    assert count(engine, market_stats) == 1  # a 1-day count must not replace the full-window count


def test_failed_incremental_page_keeps_the_checkpoint(engine):
    cfg = worker_config(backfill={"rescan_interval_hours": 1000}, retry={"attempts": 1})
    run(engine, FakeSource({QUERY: pages_of(1)}), T0, cfg)
    before = tasks(engine)[0]["last_incremental_at"]
    summary = run(engine, FlakySource({}, failures=5), T0 + timedelta(hours=2), cfg)
    assert summary.status == "partial"
    assert tasks(engine)[0]["last_incremental_at"] == before


def test_scan_cut_short_by_page_depth_never_marks_jobs_stale(engine):
    cfg = worker_config(backfill={"max_pages_per_slice": 1})
    pages = {QUERY: pages_of(1, 2, 3, 4)}
    source = FakeSource(pages)
    run(engine, source, T0, cfg)
    pages[QUERY] = pages_of(3, 4)
    for hours in (25, 50, 75):
        assert run(engine, source, T0 + timedelta(hours=hours), cfg).marked_stale == 0


# ----------------------------------------------------------- errors, retries


def test_transient_errors_are_retried_with_exponential_backoff():
    waits = []
    source = FlakySource({QUERY: pages_of(1)}, failures=2)
    result = fetch_with_retry(source, SearchCriteria("Data Analyst"), {"attempts": 3, "backoff_seconds": 2},
                              sleep=waits.append)
    assert waits == [2, 4]
    assert (result.requests_used, result.failed_attempts) == (3, 2)


def test_rejected_keys_fail_the_run_cleanly_and_send_an_alert(engine, alerts_sent):
    class Rejected(FakeSource):
        def fetch(self, criteria):
            raise SourceError("Adzuna rejected the API keys (HTTP 401).", permanent=True)

    assert run(engine, Rejected(), T0).status == "failed"
    row = last_run(engine)
    assert row["status"] == "failed" and "401" in row["error_message"] and row["finished_at"] is not None
    assert alerts_sent == ["Worker run failed"]


def test_unconfigured_source_fails_without_making_requests(engine):
    class NoKeys(FakeSource):
        def is_configured(self):
            return False

    source = NoKeys()
    assert run(engine, source, T0).status == "failed"
    assert source.calls == [] and count(engine, api_requests) == 0


def test_repeated_transient_failure_is_partial_and_the_slice_resumes(engine):
    source = FlakySource({QUERY: pages_of(1, 2, 3)}, failures=3)
    assert run(engine, source, T0).status == "partial"
    task = tasks(engine)[0]
    assert (task["status"], task["page"]) == ("pending", 1) and "503" in task["last_error"]
    assert run(engine, source, T0 + timedelta(hours=1)).status == "success"


def test_unwritable_raw_archive_does_not_stop_ingestion(engine, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    cfg = worker_config(raw_responses={"keep": True, "dir": str(blocker / "raw")})
    assert run(engine, FakeSource({QUERY: pages_of(1, 2, 3)}), T0, cfg).status == "success"
    assert tasks(engine)[0]["status"] == "complete"


def test_worker_falls_back_to_adzuna_and_logs_the_source_used(engine):
    class LoopCVDown(FakeSource):
        name = "loopcv"

        def fetch(self, criteria):
            raise SourceError("LoopCV timed out")

    adzuna = FakeSource({QUERY: pages_of(1)}, name="adzuna")
    summary = run(engine, FallbackSource(LoopCVDown(), adzuna), T0, worker_config(retry={"attempts": 1}))
    assert summary.status == "success"
    row = last_run(engine)
    assert (row["source"], row["sources_used"]) == ("loopcv>adzuna", "adzuna")


# ------------------------------------------------------------- lock, full run


def test_only_one_worker_can_hold_the_lock(tmp_path):
    lock_path = tmp_path / "worker.lock"
    with SingleInstanceLock(lock_path):
        with pytest.raises(WorkerAlreadyRunning):
            SingleInstanceLock(lock_path).acquire()
        assert lock_path.read_text() == str(os.getpid())
    with SingleInstanceLock(lock_path):  # released after the first holder exits
        pass


def test_full_worker_run_against_a_copy_of_the_database(tmp_path):
    original_path, copy_path = tmp_path / "original.db", tmp_path / "copy.db"
    original = make_engine(f"sqlite:///{original_path.as_posix()}")
    upgrade(original)
    source = FakeSource({QUERY: pages_of(1, 2, 3)})
    run(original, source, T0)
    original.dispose()

    shutil.copy(original_path, copy_path)
    copy = make_engine(f"sqlite:///{copy_path.as_posix()}")
    source.pages[QUERY] = pages_of(1, 2, 4)
    for hours in (25, 50):
        assert run(copy, source, T0 + timedelta(hours=hours)).status == "success"
    housekeeping = run_housekeeping(
        copy, worker_config(resumes={"storage_dir": str(tmp_path / "resumes")},
                            raw_responses={"dir": str(tmp_path / "raw")}),
        now=T0 + timedelta(hours=50))
    assert housekeeping["resumes_deleted"] == 0
    assert statuses(copy) == {"1": "active", "2": "active", "3": "stale", "4": "active"}
    copy.dispose()

    original = make_engine(f"sqlite:///{original_path.as_posix()}")
    with original.connect() as conn:
        assert len(recent_runs(conn, 10)) == 1  # the original database was not touched
    assert statuses(original) == {"1": "active", "2": "active", "3": "active"}
    original.dispose()


# -------------------------------------------------------------- housekeeping


class FakeHttp:
    """Answers HEAD/GET by URL: an int status, or (status, location) for a redirect."""

    def __init__(self, routes, head_routes=None):
        self.routes = routes
        self.head_routes = head_routes or {}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        answer = (self.head_routes if method == "HEAD" else {}).get(url, self.routes.get(url, 200))
        status, location = answer if isinstance(answer, tuple) else (answer, None)
        headers = {"Location": location} if location else {}
        return type("Response", (), {"status_code": status, "headers": headers, "close": lambda self: None})()


def link_cfg(cap=10):
    return worker_config(link_check={"enabled": True, "daily_cap": cap, "delay_seconds": 0})


def test_link_check_closes_404_and_410_within_the_daily_cap(engine):
    run(engine, FakeSource({QUERY: pages_of(1, 2, 3)}), T0)
    http = FakeHttp({"https://example.test/1": 404, "https://example.test/2": 410})
    assert run_link_check(engine, link_cfg(2), T0, session=http, sleep=lambda s: None,
                          resolver=PUBLIC) == {"checked": 2, "closed": 2}
    assert run_link_check(engine, link_cfg(2), T0 + timedelta(minutes=30), session=http,
                          sleep=lambda s: None, resolver=PUBLIC)["checked"] == 0
    assert statuses(engine) == {"1": "closed", "2": "closed", "3": "active"}


def test_link_check_confirms_a_head_404_with_get(engine):
    run(engine, FakeSource({QUERY: pages_of(1)}), T0)
    http = FakeHttp({"https://example.test/1": 200}, head_routes={"https://example.test/1": 404})
    assert run_link_check(engine, link_cfg(), T0, session=http, sleep=lambda s: None,
                          resolver=PUBLIC)["closed"] == 0
    assert ("GET", "https://example.test/1") in http.calls


def test_link_check_never_contacts_private_addresses(engine):
    with pytest.raises(UnsafeDestination):
        check_public_url("http://example.test/admin", resolver=lambda host: ["10.0.0.5"])
    with pytest.raises(UnsafeDestination):
        check_public_url("file:///etc/passwd", resolver=PUBLIC)
    check_public_url("https://example.test/job", resolver=PUBLIC)

    run(engine, FakeSource({QUERY: pages_of(1)}), T0)
    # The posting redirects to an internal address: the hop is refused, the job stays open.
    http = FakeHttp({"https://example.test/1": (302, "http://internal.test/metadata")})
    resolver = lambda host: ["169.254.169.254"] if host == "internal.test" else ["93.184.216.34"]  # noqa: E731
    assert run_link_check(engine, link_cfg(), T0, session=http, sleep=lambda s: None,
                          resolver=resolver)["closed"] == 0
    assert all("internal.test" not in url for _, url in http.calls)


def test_dead_link_stays_closed_when_the_feed_repeats_it(engine):
    source = FakeSource({QUERY: pages_of(1)})
    run(engine, source, T0)
    run_link_check(engine, link_cfg(), T0, session=FakeHttp({"https://example.test/1": 404}),
                   sleep=lambda s: None, resolver=PUBLIC)
    summary = run(engine, source, T0 + timedelta(hours=1))
    assert summary.reactivated == 0 and statuses(engine)["1"] == "closed"


def test_cleanup_deletes_expired_resumes_and_old_orphans_only(engine, tmp_path):
    storage = tmp_path / "resumes"
    storage.mkdir()
    expired_file, old_orphan, new_orphan, kept_file = (
        storage / "expired.bin", storage / "old.bin", storage / "uploading.bin", storage / "kept.bin")
    for path in (expired_file, old_orphan, new_orphan, kept_file):
        path.write_bytes(b"x")
    two_hours_ago = time.time() - 7200
    os.utime(old_orphan, (two_hours_ago, two_hours_ago))
    with engine.begin() as conn:
        resumes_repo.create_resume(conn, session_id="s", skills=[], file_path=str(expired_file),
                                   now=T0 - timedelta(days=31))
        resumes_repo.create_resume(conn, session_id="t", skills=[], file_path=str(kept_file), now=T0)
    assert cleanup_resumes(engine, worker_config(resumes={"storage_dir": str(storage)}), now=T0) == 1
    assert [p.exists() for p in (expired_file, old_orphan, new_orphan, kept_file)] == [False, False, True, True]


def test_raw_responses_get_unique_names_and_are_pruned(tmp_path):
    first = save_raw_response({"results": []}, directory=tmp_path, source="adzuna", country="ca",
                              query="Data Analyst", page=1, now=T0)
    second = save_raw_response({"results": [1]}, directory=tmp_path, source="adzuna", country="ca",
                               query="Data-Analyst", page=1, now=T0)
    assert first != second and json.loads(first.read_text()) == {"results": []}
    week_old = time.time() - 8 * 86400
    os.utime(first, (week_old, week_old))
    assert prune_raw_responses(tmp_path, retention_days=7) == 1
    assert not first.exists() and second.exists()


def test_config_loader_defaults_fallback_roles_and_validation(tmp_path):
    ingestion = tmp_path / "ingestion.yaml"
    settings = tmp_path / "settings.yaml"
    settings.write_text(yaml.safe_dump({"target_roles": ["Electrician", " "]}), encoding="utf-8")
    ingestion.write_text(yaml.safe_dump({"quota": {"daily_requests": 40}}), encoding="utf-8")
    cfg = load_ingestion_config(ingestion, settings)
    assert cfg["queries"] == ["Electrician"]
    assert (cfg["quota"]["daily_requests"], cfg["quota"]["backfill_share"]) == (40, 0.7)
    assert load_ingestion_config()["queries"][0] == "Data Analyst"  # the checked-in config is valid

    for bad in ({"queries": "Data Analyst"}, {"sources": None}, {"quota": {"daily_requests": "lots"}},
                {"sources": {"adzuna": {"countries": []}}}):
        ingestion.write_text(yaml.safe_dump(bad), encoding="utf-8")
        with pytest.raises(ConfigError):
            load_ingestion_config(ingestion, settings)


def test_alerts_never_raise_and_are_throttled_per_kind(tmp_path, monkeypatch):
    assert alerts.send_alert("test", "body", marker=tmp_path / "m") is False  # no SMTP: log only
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.test")
    monkeypatch.setenv("SMTP_PORT", "not-a-port")
    assert alerts.send_alert("test", "body", marker=tmp_path / "m") is False
    assert alerts.marker_path("run-failure") != alerts.marker_path("daily-report")
    assert alerts.check_disk_space(101, tmp_path) is not None
    assert alerts.check_disk_space(0, tmp_path) is None


def test_cli_run_status_report_cleanup_and_bad_config(engine, tmp_path, monkeypatch, capsys):
    cfg = worker_config(resumes={"storage_dir": str(tmp_path / "resumes")},
                        raw_responses={"dir": str(tmp_path / "raw")})
    monkeypatch.setattr(worker_cli, "get_engine", lambda: engine)
    monkeypatch.setattr(worker_cli, "load_ingestion_config", lambda: cfg)
    monkeypatch.setattr(worker_cli, "LOCK_PATH", tmp_path / "worker.lock")
    monkeypatch.setattr(worker_cli, "LOG_PATH", tmp_path / "logs" / "worker.log")
    monkeypatch.setattr(worker_cli, "send_alert", lambda *a, **k: False)

    assert worker_cli.main(["run"]) == worker_cli.EXIT_RUN_FAILED  # no Adzuna keys in tests
    assert last_run(engine)["status"] == "failed"
    assert worker_cli.main(["status"]) == 0
    assert "Jobs: 0 active, 0 stale, 0 closed" in capsys.readouterr().out
    assert worker_cli.main(["report", "--days", "1"]) == worker_cli.EXIT_FAILURES_FOUND
    assert "failed" in capsys.readouterr().out
    assert worker_cli.main(["cleanup"]) == 0

    def broken():
        raise ConfigError("config/ingestion.yaml: 'queries' must be a list")

    monkeypatch.setattr(worker_cli, "load_ingestion_config", broken)
    assert worker_cli.main(["run"]) == worker_cli.EXIT_CONFIG_ERROR
