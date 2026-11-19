"""Fallback rule: try the primary source, and use the fallback source when the
primary fails, times out, or returns nothing.

"Returns nothing" only triggers the fallback on page 1: an empty later page
just means the primary source has no more results.
"""

from __future__ import annotations

import logging

from src.sources.base import FetchResult, JobSource, SearchCriteria, SourceError

log = logging.getLogger(__name__)


class FallbackSource(JobSource):
    def __init__(self, primary: JobSource, fallback: JobSource):
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}>{fallback.name}"

    def is_configured(self) -> bool:
        return self.primary.is_configured() or self.fallback.is_configured()

    def fetch(self, criteria: SearchCriteria) -> FetchResult:
        reason = None
        requests_used = 0
        if self.primary.is_configured():
            try:
                result = self.primary.fetch(criteria)
                requests_used = result.requests_used
                if result.jobs or criteria.page > 1:
                    log.info("Source used: %s ('%s' page %d)", self.primary.name, criteria.query, criteria.page)
                    return result
                reason = "returned no jobs"
            except SourceError as error:
                reason = f"failed: {error}"
        else:
            reason = "is not configured"

        log.warning(
            "Source %s %s for '%s' page %d; falling back to %s",
            self.primary.name, reason, criteria.query, criteria.page, self.fallback.name,
        )
        result = self.fallback.fetch(criteria)
        result.used_fallback = True
        result.requests_used += requests_used
        log.info("Source used: %s ('%s' page %d)", self.fallback.name, criteria.query, criteria.page)
        return result
