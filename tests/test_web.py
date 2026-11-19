"""Web app: upload checks, rate limiting, database-only reads, and the rendered pages."""

import io
import zipfile
from pathlib import Path

import docx
import pytest
from streamlit.testing.v1 import AppTest

from src.dashboard.security import RateLimiter, client_ip
from src.dashboard.utils import (
    analysis_rows_for_export,
    database_job_counts,
    load_role_postings,
    load_settings,
    salary_summaries_by_country,
)
from src.resume.upload_validation import UploadRejected, validate_upload
from tests.helpers import T0, make_job, upsert

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP = str(PROJECT_ROOT / "src" / "dashboard" / "app.py")
PRIVACY_PAGE = str(PROJECT_ROOT / "src" / "dashboard" / "pages" / "1_Privacy_Policy.py")


def docx_bytes(text="Data analyst with SQL and Python experience.") -> bytes:
    document = docx.Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pdf_bytes() -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    page.drawString(72, 720, "Data analyst with SQL and Python experience.")
    page.save()
    return buffer.getvalue()


def macro_docx_bytes() -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(docx_bytes())) as original, zipfile.ZipFile(target, "w") as patched:
        for item in original.infolist():
            patched.writestr(item, original.read(item.filename))
        patched.writestr("word/vbaProject.bin", b"macro")
    return target.getvalue()


def plain_zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", "hello")
    return buffer.getvalue()


# ------------------------------------------------------------- upload checks


def test_pdf_and_docx_are_recognised_by_their_content():
    assert validate_upload(pdf_bytes()) == "pdf"
    assert validate_upload(docx_bytes()) == "docx"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"plain text renamed to resume.pdf", "Only PDF and Word"),
        (b"MZ\x90\x00 an executable", "Only PDF and Word"),
        (plain_zip_bytes(), "Only PDF and Word"),
        (macro_docx_bytes(), "macros"),
        (b"%PDF-" + b"0" * (5 * 1024 * 1024), "larger than 5 MB"),
        (b"", "empty"),
    ],
    ids=["text", "exe", "plain-zip", "macro-docx", "too-big", "empty"],
)
def test_unsafe_or_unsupported_uploads_are_rejected(data, message):
    with pytest.raises(UploadRejected, match=message):
        validate_upload(data)


# ------------------------------------------------------------ rate limiting


def test_rate_limit_applies_per_ip_and_per_session_and_resets():
    now = [0.0]
    limiter = RateLimiter({"upload": (2, 60)}, clock=lambda: now[0])
    assert limiter.allow("upload", "ip:1", "session:a")
    assert limiter.allow("upload", "ip:1", "session:a")
    assert not limiter.allow("upload", "ip:1", "session:b")  # same IP, new session
    assert limiter.allow("upload", "ip:2", "session:c")       # another visitor
    now[0] = 61
    assert limiter.allow("upload", "ip:1", "session:a")


def test_rejected_attempts_are_not_charged_and_cloudflare_ip_is_used():
    limiter = RateLimiter({"analysis": (1, 60)}, clock=lambda: 0.0)
    assert limiter.allow("analysis", "ip:1", "session:a")
    assert not limiter.allow("analysis", "ip:9", "session:a")  # session over its limit
    assert limiter.allow("analysis", "ip:9", "session:z")       # ip:9 was not charged
    assert client_ip({"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "10.0.0.1"}) == "203.0.113.7"
    assert client_ip({"X-Forwarded-For": "198.51.100.2, 10.0.0.1"}) == "198.51.100.2"
    assert client_ip({}) == ""


# --------------------------------------------------------- dashboard helpers


def test_dashboard_reads_jobs_from_the_database_only(tmp_path):
    database = tmp_path / "nested" / "app.db"
    assert load_role_postings("Data Analyst", database) == []  # creates and migrates the file
    assert database.exists()
    from src.dashboard.utils import database_engine

    upsert(database_engine(database), [make_job(1), make_job(2, country="United States", country_code="us",
                                                          salary_min=90000.0, salary_max=110000.0)])
    postings = load_role_postings("Data Analyst", database)
    assert {p["source_id"] for p in postings} == {"1", "2"}
    assert database_job_counts(database) == {"active": 2, "stale": 0, "closed": 0}
    by_country = salary_summaries_by_country(postings)
    assert by_country["Canada"] is None and by_country["United States"]["median"] == 100000.0


def test_settings_and_csv_export_helpers():
    settings = load_settings()
    assert settings["target_roles"] and "privacy_contact" in settings
    rows = analysis_rows_for_export([
        {"skill": "SQL", "category": "Data", "has_skill": False, "count": 3, "sample_size": 4,
         "demand": 0.75, "confidence_low": 0.3, "confidence_high": 0.95, "weighted_demand": 0.7,
         "gap_score": 0.7, "required_count": 2, "preferred_count": 1, "experience_years": 0},
        {"skill": "Python", "category": "Data", "has_skill": True, "count": 4, "sample_size": 4,
         "demand": 1.0, "confidence_low": 0.5, "confidence_high": 1.0, "weighted_demand": 1.0,
         "gap_score": 0.0, "required_count": 4, "preferred_count": 0, "experience_years": 2},
    ])
    assert [row["skill"] for row in rows] == ["SQL", "Python"]  # biggest gap first


# ------------------------------------------------------------ rendered pages


@pytest.fixture
def app_database(engine_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", engine_url)
    return engine_url


def test_app_shows_a_clear_message_when_no_jobs_are_collected_yet(app_database):
    app = AppTest.from_file(APP, default_timeout=120).run()
    assert not app.exception
    assert any("No job postings have been collected yet" in info.value for info in app.info)
    assert not any("Fetch" in button.label for button in app.button)


def test_app_analyses_stored_jobs_without_calling_any_api(engine, app_database):
    upsert(engine, [
        make_job(i, description="Requirements:\nPython and SQL.\nNice to have:\nTableau.",
                 posted_date=T0.date(), region="Ontario", location="Toronto, Ontario")
        for i in range(1, 4)
    ])
    app = AppTest.from_file(APP, default_timeout=120).run()
    assert not app.exception
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Postings in the sample"] == "3"
    assert not any("Fetch" in button.label for button in app.button)


def test_privacy_policy_page_explains_retention_and_deletion():
    page = AppTest.from_file(PRIVACY_PAGE, default_timeout=60).run()
    assert not page.exception
    text = " ".join(block.value for block in page.markdown)
    assert "30 days" in text
    assert "Delete my resume" in text
    assert "never saved to the database" in text
