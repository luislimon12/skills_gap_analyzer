"""Capped daily link check: postings whose URL returns 404 or 410 are closed.

URLs come from the job feed, so every request (and every redirect hop) is
limited to public http(s) destinations: hosts that resolve to private,
loopback, link-local, multicast or reserved addresses are never contacted.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from datetime import datetime
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import requests
from sqlalchemy.engine import Engine

from src.db.engine import utcnow
from src.db.jobs_repo import jobs_due_for_link_check, link_checks_since, mark_checked, mark_closed
from src.worker.budget import day_start

log = logging.getLogger(__name__)

GONE_STATUSES = {404, 410}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
USER_AGENT = "SkillsGapAnalyzer-LinkCheck/1.0"

Resolver = Callable[[str], Iterable[str]]


class UnsafeDestination(Exception):
    pass


def resolve_host(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def check_public_url(url: str, resolver: Resolver = resolve_host) -> None:
    """Raise UnsafeDestination unless ``url`` is http(s) to a public address."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeDestination(f"not an http(s) URL: {parts.scheme or '?'}")
    try:
        addresses = list(resolver(parts.hostname))
    except OSError as error:
        raise UnsafeDestination(f"could not resolve host ({type(error).__name__})") from None
    if not addresses:
        raise UnsafeDestination("host has no addresses")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeDestination("host resolves to a non-public address")


def _request(session, method: str, url: str, timeout: float, resolver: Resolver):
    """Follow redirects manually, validating every hop."""
    for _ in range(MAX_REDIRECTS + 1):
        check_public_url(url, resolver)
        response = session.request(method, url, allow_redirects=False, timeout=timeout, stream=True,
                                   headers={"User-Agent": USER_AGENT})
        try:
            response.close()
        except Exception:  # noqa: BLE001 - nothing to release
            pass
        location = getattr(response, "headers", {}).get("Location") if hasattr(response, "headers") else None
        if response.status_code in REDIRECT_STATUSES and location:
            url = urljoin(url, location)
            continue
        return response
    raise UnsafeDestination("too many redirects")


def url_is_gone(url: str, session, timeout: float, resolver: Resolver = resolve_host) -> bool | None:
    """True for 404/410 (confirmed with GET), False for any other answer, None if unknown."""
    try:
        response = _request(session, "HEAD", url, timeout, resolver)
        if response.status_code in GONE_STATUSES or response.status_code in (403, 405, 501):
            # Some sites answer HEAD with 404/405 but serve the page on GET: confirm first.
            response = _request(session, "GET", url, timeout, resolver)
    except UnsafeDestination as error:
        log.warning("Link check skipped an unsafe destination: %s", error)
        return None
    except requests.RequestException:
        return None
    return response.status_code in GONE_STATUSES


def run_link_check(
    engine: Engine,
    config: dict,
    now: datetime | None = None,
    session=None,
    sleep=time.sleep,
    resolver: Resolver | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict:
    cfg = config.get("link_check") or {}
    if not cfg.get("enabled", True):
        return {"checked": 0, "closed": 0}
    if clock is None:
        clock = (lambda: now) if now is not None else utcnow
    session = session or requests.Session()
    resolver = resolver or resolve_host
    daily_cap = int(cfg.get("daily_cap", 100))
    timeout = float(cfg.get("timeout_seconds", 10))

    current = clock()
    with engine.begin() as conn:
        due = jobs_due_for_link_check(conn, limit=daily_cap - link_checks_since(conn, day_start(current)),
                                      now=current)
    checked = closed = 0
    for index, job in enumerate(due):
        if index:
            sleep(float(cfg.get("delay_seconds", 1)))
        current = clock()
        # The cap is per UTC day: recount before each check in case the day rolled over.
        with engine.connect() as conn:
            if link_checks_since(conn, day_start(current)) >= daily_cap:
                break
        gone = url_is_gone(job["url"], session, timeout, resolver)
        with engine.begin() as conn:
            if gone:
                mark_closed(conn, job["id"], current, reason="link_dead")
                closed += 1
                log.info("Link check closed %s:%s (404/410)", job["source"], job["external_id"])
            else:
                mark_checked(conn, job["id"], current)
        checked += 1
    return {"checked": checked, "closed": closed}
