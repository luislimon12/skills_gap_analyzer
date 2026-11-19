"""Adzuna adapter for the JobSource interface.

Docs: https://developer.adzuna.com/docs/search
Keys come from ``ADZUNA_APP_ID`` / ``ADZUNA_APP_KEY`` in the environment and
are never written to logs, errors or stored raw responses.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime

import requests

from src.common.text import clean_text, html_to_text
from src.pipeline.geography import normalize_geography
from src.sources.base import FetchResult, Job, JobSource, QuotaExceededError, SearchCriteria, SourceError

API_ROOT = "https://api.adzuna.com/v1/api/jobs"
DEFAULT_TIMEOUT = 15

_KEY_PARAMS = re.compile(r"(app_id|app_key)=[^&\s'\"]+")


def redact(message: str) -> str:
    """requests includes the full URL (query string included) in its error text."""
    return _KEY_PARAMS.sub(r"\1=REDACTED", message)


def _to_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _to_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_adzuna_job(item: dict, country_code: str) -> Job | None:
    """One Adzuna result -> Job. Returns None for results without an id."""
    external_id = str(item.get("id") or "").strip()
    if not external_id:
        return None
    location = clean_text((item.get("location") or {}).get("display_name"))
    country, region = normalize_geography(location, country_hint=country_code)
    return Job(
        source="adzuna",
        external_id=external_id,
        # Adzuna highlights matched search terms with <strong> in titles too.
        title=" ".join(html_to_text(item.get("title")).split()),
        company=clean_text((item.get("company") or {}).get("display_name")),
        location=location,
        country=country,
        country_code=country_code,
        region=region,
        salary_min=_to_float(item.get("salary_min")),
        salary_max=_to_float(item.get("salary_max")),
        salary_is_predicted=str(item.get("salary_is_predicted", "0")) == "1",
        # Adzuna wraps matched terms in <strong>, so strip HTML.
        description=html_to_text(item.get("description")),
        description_is_truncated=True,
        url=item.get("redirect_url") or "",
        posted_date=_to_date(item.get("created")),
        contract_time=item.get("contract_time"),
        category=clean_text((item.get("category") or {}).get("label")),
    )


class AdzunaSource(JobSource):
    name = "adzuna"

    def __init__(
        self,
        app_id: str | None = None,
        app_key: str | None = None,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._app_id = app_id
        self._app_key = app_key
        self._session = session or requests.Session()
        self._timeout = timeout

    @property
    def app_id(self) -> str | None:
        return self._app_id or os.getenv("ADZUNA_APP_ID")

    @property
    def app_key(self) -> str | None:
        return self._app_key or os.getenv("ADZUNA_APP_KEY")

    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_key)

    def build_params(self, criteria: SearchCriteria) -> dict:
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": criteria.query,
            "results_per_page": criteria.results_per_page,
            "content-type": "application/json",
        }
        if criteria.sort_by:
            params["sort_by"] = criteria.sort_by
        if criteria.max_days_old:
            params["max_days_old"] = int(criteria.max_days_old)
        if criteria.where:
            params["where"] = criteria.where
        return params

    def fetch(self, criteria: SearchCriteria) -> FetchResult:
        if not self.is_configured():
            raise SourceError(
                "Adzuna keys are missing: set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env.",
                permanent=True,
            )
        url = f"{API_ROOT}/{criteria.country}/search/{criteria.page}"
        try:
            response = self._session.get(url, params=self.build_params(criteria), timeout=self._timeout)
        except requests.Timeout:
            raise SourceError(f"Adzuna timed out for '{criteria.query}' page {criteria.page}.") from None
        except requests.RequestException as error:
            raise SourceError(f"Could not reach Adzuna: {redact(str(error))}") from None

        status = response.status_code
        if status in (401, 403):
            raise SourceError(
                f"Adzuna rejected the API keys (HTTP {status}). Check ADZUNA_APP_ID and ADZUNA_APP_KEY.",
                permanent=True, status_code=status,
            )
        if status == 429:
            raise QuotaExceededError(
                "Adzuna rate limit or quota reached (HTTP 429).", status_code=status,
            )
        if status in (400, 404):
            raise SourceError(
                f"Adzuna rejected the request for '{criteria.query}' page {criteria.page} (HTTP {status}).",
                permanent=True, status_code=status,
            )
        if not response.ok:
            raise SourceError(
                f"Adzuna returned HTTP {status} for '{criteria.query}' page {criteria.page}.",
                status_code=status,
            )
        try:
            raw = response.json()
        except ValueError:
            raise SourceError("Adzuna returned a response that is not JSON.") from None

        results = raw.get("results") or []
        jobs = [job for job in (parse_adzuna_job(item, criteria.country) for item in results) if job]
        return FetchResult(
            jobs=jobs,
            source=self.name,
            requests_used=1,
            total_available=raw.get("count"),
            mean_salary=_to_float(raw.get("mean")),
            raw=raw,
            page_size=len(results),
        )
