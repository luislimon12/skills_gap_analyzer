"""Database layer: migrations, deduplication, lifecycle rules, and 30-day resume deletion."""

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from src.db import jobs_repo, resumes_repo
from src.db.migrate import upgrade
from src.db.schema import analyses, jobs, metadata, resume_deletions, resumes
from tests.helpers import T0, job_id, make_job, statuses, upsert


def count_jobs(engine):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(jobs)).scalar_one()


def make_stale(engine, scan_start, query="Data Analyst"):
    with engine.begin() as conn:
        return jobs_repo.mark_unseen_stale(conn, country_code="ca", query=query,
                                           scan_started_at=scan_start, now=scan_start)


# --------------------------------------------------------------- migrations


def test_alembic_migration_matches_schema_and_is_idempotent(engine):
    inspector = inspect(engine)
    for table in metadata.sorted_tables:
        migrated = {column["name"] for column in inspector.get_columns(table.name)}
        assert migrated == set(table.columns.keys()), table.name
    upgrade(engine)  # second run is a no-op
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0001"


# -------------------------------------------------------------------- dedup


def test_refetch_updates_in_place_and_batches_are_deduplicated(engine):
    first = upsert(engine, [make_job(1), make_job(2), make_job(2)])
    second = upsert(engine, [make_job(1, title="Senior Data Analyst")], now=T0 + timedelta(hours=1))
    assert (first.found, first.inserted) == (2, 2)
    assert (second.inserted, second.updated) == (0, 1)
    assert count_jobs(engine) == 2
    with engine.connect() as conn:
        row = conn.execute(select(jobs.c.title, jobs.c.first_seen_at, jobs.c.last_seen_at)
                           .where(jobs.c.external_id == "1")).one()
    assert row.title == "Senior Data Analyst"
    assert row.last_seen_at > row.first_seen_at


def test_same_external_id_from_two_sources_are_separate_jobs(engine):
    upsert(engine, [make_job(1, source="adzuna"), make_job(1, source="loopcv")])
    assert count_jobs(engine) == 2


def test_database_rejects_duplicate_source_and_external_id(engine):
    upsert(engine, [make_job(1)])
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(jobs.insert().values(
                source="adzuna", external_id="1", title="x", company="", location="", country="",
                country_code="", region="", salary_is_predicted=False, description="",
                description_is_truncated=True, url="", fetched_at=T0, category="",
                first_seen_at=T0, last_seen_at=T0, status="active",
            ))


def test_job_found_by_two_searches_is_listed_under_both_roles(engine):
    upsert(engine, [make_job(1)], query="Data Analyst")
    upsert(engine, [make_job(1)], query="Business Intelligence Analyst")
    with engine.connect() as conn:
        assert len(jobs_repo.load_jobs_for_role(conn, "data analyst")) == 1  # case-insensitive
        assert len(jobs_repo.load_jobs_for_role(conn, "Business Intelligence Analyst")) == 1
        assert jobs_repo.tracked_queries(conn) == ["Business Intelligence Analyst", "Data Analyst"]
    assert count_jobs(engine) == 1


# ---------------------------------------------------------------- lifecycle


def test_full_rescan_marks_only_unseen_jobs_of_that_slice_stale(engine):
    upsert(engine, [make_job(1), make_job(2)], query="Data Analyst")
    upsert(engine, [make_job(3)], query="Electrician")
    scan_start = T0 + timedelta(days=1)
    upsert(engine, [make_job(1)], now=scan_start + timedelta(minutes=5))
    assert make_stale(engine, scan_start) == 1
    assert statuses(engine) == {"1": "active", "2": "stale", "3": "active"}


def test_job_seen_by_another_slice_during_the_scan_stays_active(engine):
    upsert(engine, [make_job(1)], query="Data Analyst")
    scan_start = T0 + timedelta(days=1)
    upsert(engine, [make_job(1)], query="Data Engineer", now=scan_start + timedelta(minutes=1))
    assert make_stale(engine, scan_start) == 0


def test_stale_job_closes_only_after_the_configured_window(engine):
    upsert(engine, [make_job(1)])
    make_stale(engine, T0 + timedelta(days=1))
    with engine.begin() as conn:
        assert jobs_repo.close_stale_jobs(conn, stale_days=3, now=T0 + timedelta(days=3)) == 0
        assert jobs_repo.close_stale_jobs(conn, stale_days=3, now=T0 + timedelta(days=4)) == 1
    assert statuses(engine) == {"1": "closed"}


def test_closed_job_is_reactivated_when_it_reappears(engine):
    upsert(engine, [make_job(1)])
    with engine.begin() as conn:
        jobs_repo.mark_closed(conn, job_id(engine, 1), now=T0 + timedelta(days=2))
    stats = upsert(engine, [make_job(1)], now=T0 + timedelta(days=3))
    assert stats.reactivated == 1
    with engine.connect() as conn:
        row = conn.execute(select(jobs.c.status, jobs.c.closed_at, jobs.c.stale_since)).one()
    assert tuple(row) == ("active", None, None)


def test_closed_jobs_are_hidden_from_results_but_available_with_a_label(engine):
    upsert(engine, [make_job(1), make_job(2)])
    with engine.begin() as conn:
        jobs_repo.mark_closed(conn, job_id(engine, 2), now=T0)
    with engine.connect() as conn:
        listed = jobs_repo.load_jobs_for_role(conn, "Data Analyst")
        everything = jobs_repo.load_jobs_for_role(conn, "Data Analyst", include_closed=True)
        counts = jobs_repo.job_status_counts(conn)
    assert [posting["source_id"] for posting in listed] == ["1"]
    assert {p["source_id"]: p["no_longer_listed"] for p in everything} == {"1": False, "2": True}
    assert counts == {"active": 1, "stale": 0, "closed": 1}


def test_role_results_have_the_shape_the_scoring_code_reads(engine):
    upsert(engine, [make_job(1, description="Python and SQL", posted_date=T0.date(), salary_min=50000.0)])
    with engine.connect() as conn:
        posting = jobs_repo.load_jobs_for_role(conn, "Data Analyst")[0]
    assert posting["full_text"] == "Python and SQL"
    assert posting["posted_date"] == "2026-11-02"
    assert posting["role"] == "Data Analyst"
    assert posting["is_truncated"] is True and posting["salary_min"] == 50000.0
    assert posting["requirement_level"] is None


def test_link_check_selection_is_capped_and_skips_recently_checked_jobs(engine):
    upsert(engine, [make_job(i) for i in range(5)])
    with engine.begin() as conn:
        jobs_repo.mark_checked(conn, job_id(engine, 0), now=T0)
        due = jobs_repo.jobs_due_for_link_check(conn, limit=3, now=T0 + timedelta(hours=1))
        assert jobs_repo.link_checks_since(conn, T0 - timedelta(hours=1)) == 1
    assert len(due) == 3
    assert job_id(engine, 0) not in {job["id"] for job in due}


def test_latest_market_stats_per_country(engine):
    with engine.begin() as conn:
        jobs_repo.insert_market_stats(conn, query="Data Analyst", country="ca", total_count=100,
                                      mean_salary=70000.0, now=T0)
        jobs_repo.insert_market_stats(conn, query="Data Analyst", country="ca", total_count=120,
                                      mean_salary=71000.0, now=T0 + timedelta(hours=1))
        jobs_repo.insert_market_stats(conn, query="Data Analyst", country="us", total_count=900,
                                      mean_salary=90000.0, now=T0)
        latest = {row["country"]: row["total_count"] for row in jobs_repo.latest_market_stats(conn, "data analyst")}
    assert latest == {"ca": 120, "us": 900}


# ----------------------------------------------------- resumes and deletion


def test_resume_is_scheduled_for_deletion_30_days_after_upload(engine):
    with engine.begin() as conn:
        saved = resumes_repo.create_resume(conn, session_id="anon", skills=["SQL", "Python", "SQL"], now=T0)
        stored = resumes_repo.get_resume(conn, saved["id"])
    assert saved["delete_after"] == T0 + timedelta(days=30)
    assert stored["delete_after"] == T0 + timedelta(days=30)
    assert stored["skills"] == ["Python", "SQL"]


def test_expired_resumes_are_deleted_with_their_files_and_analyses(engine, tmp_path):
    stored_file = tmp_path / "resume.bin"
    stored_file.write_bytes(b"encrypted")
    with engine.begin() as conn:
        old = resumes_repo.create_resume(conn, session_id="a", skills=["SQL"], file_path=str(stored_file), now=T0)
        new = resumes_repo.create_resume(conn, session_id="b", skills=["SQL"], now=T0 + timedelta(days=20))
        for resume in (old, new):
            resumes_repo.save_analysis(conn, resume_id=resume["id"], role="Data Analyst", fit_score=50.0,
                                       postings_count=10, skills=[{"skill": "SQL", "has_skill": True}])
    with engine.begin() as conn:
        assert resumes_repo.delete_expired_resumes(conn, now=T0 + timedelta(days=30, minutes=1)) == 1

    assert not stored_file.exists()
    with engine.connect() as conn:
        assert [row[0] for row in conn.execute(select(resumes.c.id))] == [new["id"]]
        assert {row[0] for row in conn.execute(select(analyses.c.resume_id))} == {new["id"]}
        log = conn.execute(select(resume_deletions.c.resume_id, resume_deletions.c.reason)).all()
    assert [tuple(row) for row in log] == [(old["id"], "expired")]


def test_delete_my_resume_removes_everything_and_analyses_hold_no_resume_text(engine):
    with engine.begin() as conn:
        saved = resumes_repo.create_resume(conn, session_id="a", skills=["SQL"], now=T0)
        resumes_repo.save_analysis(
            conn, resume_id=saved["id"], role="Data Analyst", fit_score=80.0, postings_count=3,
            skills=[{"skill": "SQL", "has_skill": True, "demand": 0.5, "weighted_demand": 0.4,
                     "gap_score": 0.0, "rationale": "Jane Doe, 555-0100"}],
        )
        stored = resumes_repo.analyses_for_resume(conn, saved["id"])[0]["results"]
        assert resumes_repo.delete_resume(conn, saved["id"], reason="user_request")
        assert resumes_repo.get_resume(conn, saved["id"]) is None
        assert resumes_repo.analyses_for_resume(conn, saved["id"]) == []
        assert not resumes_repo.delete_resume(conn, saved["id"], reason="user_request")
    assert stored == [{"skill": "SQL", "has_skill": True, "demand": 0.5, "weighted_demand": 0.4, "gap_score": 0.0}]


def test_stored_resume_files_are_encrypted_and_need_the_key(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    with pytest.raises(resumes_repo.EncryptionKeyMissing):
        resumes_repo.store_encrypted_file(b"data", tmp_path)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    path = Path(resumes_repo.store_encrypted_file(b"%PDF-1.7 my resume", tmp_path))
    assert path.parent == tmp_path and len(path.stem) == 32  # random name
    assert b"my resume" not in path.read_bytes()
    assert resumes_repo.read_encrypted_file(str(path)) == b"%PDF-1.7 my resume"
