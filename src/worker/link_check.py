"""Capped daily link check: postings whose URL returns 404 or 410 are closed."""

from __future__ import annotations

import logging
import time
from datetime import datetime

import requests
from sqlalchemy.engine import Engine

from src.db.jobs_repo import jobs_due_for_link_check, link_checks_since, mark_checked, mark_closed
from src.worker.budget import day_start

log = logging.getLogger(__name__)

GONE_STATUSES = {404, 410}
USER_AGENT = "SkillsGapAnalyzer-LinkCheck/1.0"


def url_is_gone(url: str, session: requests.Session, timeout: float) -> bool | None:
    """True for 404/410, False for any other answer, None if the check itself failed."""
    try:
        response = session.head(url, allow_redirects=True, timeout=timeout,
                                headers={"User-Agent": USER_AGENT})
        if response.status_code in (403, 405, 501):  # some sites refuse HEAD; try GET
            response = session.get(url, allow_redirects=True, timeout=timeout, stream=True,
                                   headers={"User-Agent": USER_AGENT})
            response.close()
    except requests.RequestException:
        return None
    return response.status_code in GONE_STATUSES


def run_link_check(
    engine: Engine,
    config: dict,
    now: datetime,
    session: requests.Session | None = None,
    sleep=time.sleep,
) -> dict:
    cfg = config.get("link_check") or {}
    if not cfg.get("enabled", True):
        return {"checked": 0, "closed": 0}
    session = session or requests.Session()
    with engine.begin() as conn:
        remaining = int(cfg.get("daily_cap", 100)) - link_checks_since(conn, day_start(now))
        due = jobs_due_for_link_check(conn, limit=remaining, now=now)
    checked = closed = 0
    for index, job in enumerate(due):
        if index:
            sleep(float(cfg.get("delay_seconds", 1)))
        gone = url_is_gone(job["url"], session, float(cfg.get("timeout_seconds", 10)))
        with engine.begin() as conn:
            if gone:
                mark_closed(conn, job["id"], now)
                closed += 1
                log.info("Link check closed %s:%s (404/410)", job["source"], job["external_id"])
            else:
                mark_checked(conn, job["id"], now)
        checked += 1
    return {"checked": checked, "closed": closed}
