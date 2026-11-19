"""Fallback rule: try the primary source, and use the fallback source when the
primary fails, times out, or returns nothing.

"Returns nothing" only triggers the fallback on page 1: an empty later page
just means the primary source has no more results. Once a search has fallen
back on page 1, its later pages also go to the fallback source, so pagination
stays on one source.
"""

from __future__ import annotations

import logging

from src.sources.base import FetchResult, JobSource, SearchCriteria, SourceError

log = logging.getLogger(__name__)


def _search_key(criteria: SearchCriteria) -> tuple:
    return (criteria.query, criteria.country, criteria.max_days_old, criteria.where,
            criteria.sort_by, criteria.results_per_page)


class FallbackSource(JobSource):
    def __init__(self, primary: JobSource, fallback: JobSource):
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}>{fallback.name}"
        self._fell_back: set[tuple] = set()

    def is_configured(self) -> bool:
        return self.primary.is_configured() or self.fallback.is_configured()

    def _use_fallback(self, criteria: SearchCriteria, reason: str, requests_used: int = 0) -> FetchResult:
        log.warning(
            "Source %s %s for '%s' page %d; using %s",
            self.primary.name, reason, criteria.query, criteria.page, self.fallback.name,
        )
        result = self.fallback.fetch(criteria)
        result.used_fallback = True
        result.requests_used += requests_used
        if criteria.page == 1:
            self._fell_back.add(_search_key(criteria))
        log.info("Source used: %s ('%s' page %d)", self.fallback.name, criteria.query, criteria.page)
        return result

    def fetch(self, criteria: SearchCriteria) -> FetchResult:
        key = _search_key(criteria)
        if criteria.page == 1:
            self._fell_back.discard(key)
        elif key in self._fell_back:
            return self._use_fallback(criteria, "was replaced on page 1")

        if not self.primary.is_configured():
            return self._use_fallback(criteria, "is not configured")
        try:
            result = self.primary.fetch(criteria)
        except SourceError as error:
            return self._use_fallback(criteria, f"failed: {error}")
        if result.jobs or criteria.page > 1:
            log.info("Source used: %s ('%s' page %d)", self.primary.name, criteria.query, criteria.page)
            return result
        return self._use_fallback(criteria, "returned no jobs", result.requests_used)
