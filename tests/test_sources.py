"""JobSource adapters: Adzuna (recorded response), LoopCV stub, and the fallback rule."""

from datetime import date

import pytest
import requests

from src.sources import AdzunaSource, FallbackSource, LoopCVSource, build_source
from src.sources.adzuna import parse_adzuna_job
from src.sources.base import FetchResult, Job, JobSource, QuotaExceededError, SearchCriteria, SourceError

# Recorded (trimmed) Adzuna search response.
ADZUNA_RECORDED = {
    "count": 1234,
    "mean": 71234.5,
    "results": [
        {
            "id": "4567890123",
            "title": "Junior <strong>Data</strong> <strong>Analyst</strong>",
            "company": {"display_name": "Maple Analytics Inc."},
            "location": {"display_name": "Toronto, Ontario"},
            "created": "2026-11-10T14:03:00Z",
            "redirect_url": "https://www.adzuna.ca/land/ad/4567890123",
            "description": "We use <strong>SQL</strong> and Python daily...",
            "salary_min": 60000,
            "salary_max": 75000,
            "salary_is_predicted": "1",
            "contract_time": "full_time",
            "category": {"label": "IT Jobs"},
        },
        {"id": "", "title": "Missing id is skipped"},
    ],
}

KEY_ID, KEY_SECRET = "test-id-123", "secret-key-456"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = ADZUNA_RECORDED if payload is None else payload

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.error:
            raise self.error
        return self.response


def adzuna(session):
    return AdzunaSource(app_id=KEY_ID, app_key=KEY_SECRET, session=session)


def test_parse_adzuna_job_normalises_every_field():
    job = parse_adzuna_job(ADZUNA_RECORDED["results"][0], "ca")
    assert (job.source, job.external_id) == ("adzuna", "4567890123")
    assert job.title == "Junior Data Analyst"
    assert job.company == "Maple Analytics Inc."
    assert (job.country, job.region, job.country_code) == ("Canada", "Ontario", "ca")
    assert (job.salary_min, job.salary_max, job.salary_is_predicted) == (60000.0, 75000.0, True)
    assert job.posted_date == date(2026, 11, 10)
    assert job.url.endswith("/4567890123")
    assert "<strong>" not in job.description and job.description_is_truncated
    assert (job.contract_time, job.category) == ("full_time", "IT Jobs")


def test_adzuna_fetch_builds_request_and_returns_jobs_and_market_stats():
    session = FakeSession()
    result = adzuna(session).fetch(SearchCriteria("Data Analyst", "us", page=2, max_days_old=7, where="Boston"))
    assert [job.external_id for job in result.jobs] == ["4567890123"]
    assert result.page_size == 2  # raw results on the page, before bad rows are skipped
    assert (result.total_available, result.mean_salary, result.requests_used) == (1234, 71234.5, 1)
    call = session.calls[0]
    assert call["url"].endswith("/jobs/us/search/2")
    assert call["params"]["what"] == "Data Analyst"
    assert (call["params"]["sort_by"], call["params"]["max_days_old"], call["params"]["where"]) == ("date", 7, "Boston")


@pytest.mark.parametrize(
    ("status", "error_type", "permanent"),
    [(401, SourceError, True), (403, SourceError, True), (400, SourceError, True),
     (429, QuotaExceededError, False), (503, SourceError, False)],
)
def test_adzuna_http_errors_are_classified_and_never_leak_keys(status, error_type, permanent):
    with pytest.raises(error_type) as info:
        adzuna(FakeSession(FakeResponse(status))).fetch(SearchCriteria("Data Analyst"))
    assert info.value.permanent is permanent
    assert info.value.status_code == status
    assert KEY_SECRET not in str(info.value) and KEY_ID not in str(info.value)


@pytest.mark.parametrize(
    "error",
    [requests.ConnectionError(f"failed: https://api/?app_id={KEY_ID}&app_key={KEY_SECRET}"),
     requests.Timeout("read timed out")],
    ids=["connection", "timeout"],
)
def test_adzuna_network_failures_are_transient_and_redacted(error):
    with pytest.raises(SourceError) as info:
        adzuna(FakeSession(error=error)).fetch(SearchCriteria("Data Analyst"))
    assert not info.value.permanent
    assert KEY_SECRET not in str(info.value) and KEY_ID not in str(info.value)


def test_adzuna_without_keys_is_unconfigured_and_refuses_to_call():
    session = FakeSession()
    source = AdzunaSource(session=session)
    assert not source.is_configured()
    with pytest.raises(SourceError) as info:
        source.fetch(SearchCriteria("Data Analyst"))
    assert info.value.permanent and session.calls == []


class StubSource(JobSource):
    def __init__(self, name, result=None, error=None, configured=True):
        self.name, self.result, self.error, self.configured = name, result, error, configured
        self.calls = 0

    def is_configured(self):
        return self.configured

    def fetch(self, criteria):
        self.calls += 1
        if self.error:
            raise self.error
        return FetchResult(jobs=list(self.result or []), source=self.name, page_size=len(self.result or []))


def _job(external_id, source):
    return Job(source=source, external_id=external_id, title="Data Analyst")


@pytest.mark.parametrize(
    "primary",
    [StubSource("loopcv", error=SourceError("timed out")),
     StubSource("loopcv", result=[]),
     StubSource("loopcv", configured=False)],
    ids=["fails-or-times-out", "returns-nothing", "not-configured"],
)
def test_fallback_uses_adzuna_when_loopcv_is_unavailable(primary):
    fallback = StubSource("adzuna", result=[_job("1", "adzuna")])
    result = FallbackSource(primary, fallback).fetch(SearchCriteria("Data Analyst"))
    assert (result.source, result.used_fallback, fallback.calls) == ("adzuna", True, 1)


def test_fallback_not_used_when_primary_answers_or_a_later_page_is_empty():
    fallback = StubSource("adzuna", result=[_job("1", "adzuna")])
    answered = FallbackSource(StubSource("loopcv", result=[_job("9", "loopcv")]), fallback)
    assert answered.fetch(SearchCriteria("Data Analyst")).source == "loopcv"
    exhausted = FallbackSource(StubSource("loopcv", result=[]), fallback)
    assert exhausted.fetch(SearchCriteria("Data Analyst", page=3)).source == "loopcv"
    assert fallback.calls == 0


def test_build_source_runs_on_adzuna_alone_until_loopcv_is_enabled():
    assert isinstance(build_source({}), AdzunaSource)
    chained = build_source({"sources": {"loopcv": {"enabled": True}}})
    assert isinstance(chained, FallbackSource) and chained.name == "loopcv>adzuna"
    stub = LoopCVSource()
    assert not stub.is_configured()
    assert stub.fetch(SearchCriteria("Data Analyst")).jobs == []
    with pytest.raises(ValueError):
        Job(source="adzuna", external_id="  ", title="no id")
