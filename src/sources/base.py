"""The JobSource interface and the normalised Job record every source returns.

Every job board adapter (Adzuna, LoopCV, ...) implements ``JobSource.fetch``
and returns ``Job`` objects, so the worker, the database layer and the web app
never deal with a board's raw response shape.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone


class SourceError(Exception):
    """A source request failed. ``permanent`` errors should not be retried.

    Messages must never contain API keys or other credentials.
    """

    def __init__(self, message: str, *, permanent: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.permanent = permanent
        self.status_code = status_code


class QuotaExceededError(SourceError):
    """The source reported that the request quota is used up (HTTP 429 after retries)."""


@dataclass(frozen=True)
class SearchCriteria:
    """One page of one search: what to look for, where, and how far back."""

    query: str
    country: str = "ca"            # source country code, e.g. "ca", "us"
    page: int = 1
    results_per_page: int = 50
    max_days_old: int | None = None
    where: str | None = None
    sort_by: str = "date"          # newest first, so backfill reaches recent jobs first


@dataclass
class Job:
    """A normalised job posting.

    ``source`` + ``external_id`` identify a posting uniquely; the database has a
    unique constraint on that pair so re-fetches update instead of duplicating.
    """

    source: str
    external_id: str
    title: str
    company: str = ""
    location: str = ""
    country: str = ""              # display name, e.g. "Canada"
    country_code: str = ""         # source country code, e.g. "ca"
    region: str = ""               # province / state display name
    salary_min: float | None = None
    salary_max: float | None = None
    salary_is_predicted: bool = False
    description: str = ""          # summary text (Adzuna returns ~500-character excerpts)
    description_is_truncated: bool = True
    url: str = ""
    posted_date: date | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    contract_time: str | None = None
    category: str = ""

    def __post_init__(self) -> None:
        self.external_id = str(self.external_id).strip()
        if not self.source or not self.external_id:
            raise ValueError("Job needs both source and external_id")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FetchResult:
    """What one ``fetch`` call returned, plus bookkeeping for the worker."""

    jobs: list[Job]
    source: str                    # name of the source that actually answered
    requests_used: int = 1
    total_available: int | None = None   # e.g. Adzuna's ``count``
    mean_salary: float | None = None     # e.g. Adzuna's ``mean``
    raw: dict | list | None = None       # raw response, kept briefly for debugging
    page_size: int = 0                   # number of raw results on the page (before filters)
    used_fallback: bool = False


class JobSource(ABC):
    """Interface every job board adapter implements."""

    #: short, stable identifier stored in ``jobs.source`` (e.g. "adzuna")
    name: str = ""

    def is_configured(self) -> bool:
        """True when credentials/settings needed to call the source are present."""
        return True

    @abstractmethod
    def fetch(self, criteria: SearchCriteria) -> FetchResult:
        """Fetch one page of results for ``criteria`` and return normalised jobs.

        Raises ``SourceError`` on failure (``permanent=True`` for errors that a
        retry will not fix, such as rejected credentials).
        """
