"""Email alerts for worker failures and low disk space.

Configure SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, ALERT_EMAIL_FROM
and ALERT_EMAIL_TO in .env. Without them alerts are only logged. Each kind of
alert (``key``) is throttled separately, so a daily report never delays a
run-failure alert. Sending an alert never raises.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

from src.db.engine import PROJECT_ROOT

log = logging.getLogger(__name__)

MARKER_DIR = PROJECT_ROOT / "data"


def marker_path(key: str) -> Path:
    return MARKER_DIR / f".last_alert_{re.sub(r'[^a-z0-9_-]+', '_', key.lower())}"


def _throttled(min_hours: float, marker: Path) -> bool:
    try:
        return time.time() - marker.stat().st_mtime < min_hours * 3600
    except OSError:
        return False


def send_alert(subject: str, body: str, *, key: str = "general", min_hours_between: float = 6,
               marker: Path | None = None) -> bool:
    """Log the alert and email it if SMTP is configured. Returns True if an email was sent."""
    log.error("ALERT: %s - %s", subject, body)
    host, recipient = os.getenv("SMTP_HOST"), os.getenv("ALERT_EMAIL_TO")
    if not host or not recipient:
        return False
    marker = marker or marker_path(key)
    if min_hours_between > 0 and _throttled(min_hours_between, marker):
        log.info("Alert email '%s' throttled (one sent in the last %s hours)", key, min_hours_between)
        return False
    try:
        port = int(os.getenv("SMTP_PORT") or 587)
    except ValueError:
        log.error("SMTP_PORT is not a number; alert email not sent")
        return False

    message = EmailMessage()
    message["Subject"] = f"[Skills Gap Analyzer] {subject}"
    message["From"] = os.getenv("ALERT_EMAIL_FROM") or recipient
    message["To"] = recipient
    message.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            # Verify the server certificate and hostname before sending credentials.
            smtp.starttls(context=ssl.create_default_context())
            if os.getenv("SMTP_USERNAME"):
                smtp.login(os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD") or "")
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as error:
        log.error("Could not send alert email: %s", type(error).__name__)
        return False
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError as error:
        log.warning("Alert sent, but the throttle marker could not be written (%s)", type(error).__name__)
    return True


def disk_free_percent(path: Path = PROJECT_ROOT) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / usage.total * 100


def check_disk_space(min_free_percent: float, path: Path = PROJECT_ROOT) -> str | None:
    free = disk_free_percent(path)
    if free < min_free_percent:
        return f"Only {free:.1f}% disk space free on the volume holding {path}."
    return None
