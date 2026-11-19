"""Email alerts for worker failures and low disk space.

Configure SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, ALERT_EMAIL_FROM
and ALERT_EMAIL_TO in .env. Without them alerts are only logged. Emails are
throttled so a broken worker doesn't send one every hour.
"""

from __future__ import annotations

import logging
import os
import shutil
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

from src.db.engine import PROJECT_ROOT

log = logging.getLogger(__name__)

THROTTLE_FILE = PROJECT_ROOT / "data" / ".last_alert"


def _throttled(min_hours: float, marker: Path) -> bool:
    try:
        return time.time() - marker.stat().st_mtime < min_hours * 3600
    except OSError:
        return False


def send_alert(subject: str, body: str, *, min_hours_between: float = 6, marker: Path = THROTTLE_FILE) -> bool:
    """Log the alert and email it if SMTP is configured. Returns True if an email was sent."""
    log.error("ALERT: %s - %s", subject, body)
    host, recipient = os.getenv("SMTP_HOST"), os.getenv("ALERT_EMAIL_TO")
    if not host or not recipient:
        return False
    if _throttled(min_hours_between, marker):
        log.info("Alert email throttled (sent one in the last %s hours)", min_hours_between)
        return False
    message = EmailMessage()
    message["Subject"] = f"[Skills Gap Analyzer] {subject}"
    message["From"] = os.getenv("ALERT_EMAIL_FROM") or recipient
    message["To"] = recipient
    message.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT") or 587), timeout=20) as smtp:
            smtp.starttls()
            if os.getenv("SMTP_USERNAME"):
                smtp.login(os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD") or "")
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        log.error("Could not send alert email: %s", type(error).__name__)
        return False
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return True


def disk_free_percent(path: Path = PROJECT_ROOT) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / usage.total * 100


def check_disk_space(min_free_percent: float, path: Path = PROJECT_ROOT) -> str | None:
    free = disk_free_percent(path)
    if free < min_free_percent:
        return f"Only {free:.1f}% disk space free on the volume holding {path}."
    return None
