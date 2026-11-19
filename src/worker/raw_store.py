"""Keep raw API responses for a short time to debug source schema changes.

Responses never contain API keys (those are only in the request URL).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


def save_raw_response(
    raw: dict | list,
    *,
    directory: Path,
    source: str,
    country: str,
    query: str,
    page: int,
    now: datetime,
) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%S")
    path = directory / f"{stamp}_{_slug(source)}_{country}_{_slug(query)}_p{page}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def prune_raw_responses(directory: Path, retention_days: int, now_ts: float | None = None) -> int:
    """Delete raw response files older than ``retention_days``. Returns how many were removed."""
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    cutoff = (now_ts or time.time()) - retention_days * 86400
    removed = 0
    for path in directory.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
