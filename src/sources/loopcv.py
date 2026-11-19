"""LoopCV adapter - stub until the API details are confirmed.

Open questions (production plan, section 1):
  * Which endpoint returns jobs directly, or does everything go through "loops"?
  * Does the API send removal/update events, or filter by last-modified date?
  * Request limits and terms of use.

Until then ``fetch`` returns no jobs, which makes the fallback rule hand the
request to Adzuna. ``is_configured`` is False unless ``LOOPCV_API_KEY`` is set,
so the worker skips LoopCV entirely by default.
"""

from __future__ import annotations

import os

from src.sources.base import FetchResult, JobSource, SearchCriteria


class LoopCVSource(JobSource):
    name = "loopcv"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key

    @property
    def api_key(self) -> str | None:
        return self._api_key or os.getenv("LOOPCV_API_KEY")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def fetch(self, criteria: SearchCriteria) -> FetchResult:
        # TODO: call the LoopCV API once the endpoint and response shape are confirmed.
        return FetchResult(jobs=[], source=self.name, requests_used=0, page_size=0)
