"""Job board adapters behind one JobSource interface."""

from __future__ import annotations

from src.sources.adzuna import AdzunaSource
from src.sources.base import FetchResult, Job, JobSource, QuotaExceededError, SearchCriteria, SourceError
from src.sources.fallback import FallbackSource
from src.sources.loopcv import LoopCVSource

__all__ = [
    "AdzunaSource", "FallbackSource", "FetchResult", "Job", "JobSource", "LoopCVSource",
    "QuotaExceededError", "SearchCriteria", "SourceError", "build_source",
]


def build_source(config: dict | None = None) -> JobSource:
    """Build the source the worker uses.

    With LoopCV enabled (and its key set) LoopCV is primary and Adzuna is the
    fallback; otherwise the app runs on Adzuna alone.
    """
    config = config or {}
    sources_cfg = config.get("sources") or config  # full ingestion config or its "sources" block
    adzuna_cfg = sources_cfg.get("adzuna") or {}
    adzuna = AdzunaSource(timeout=float(adzuna_cfg.get("timeout_seconds", 15)))
    loopcv_cfg = sources_cfg.get("loopcv") or {}
    if loopcv_cfg.get("enabled", False):
        return FallbackSource(LoopCVSource(), adzuna)
    return adzuna
