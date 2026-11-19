"""Request budget: stop before the source's daily/weekly/monthly quota is used up.

Backfill and re-scans may use only ``backfill_share`` of the daily quota; the
rest is kept for hourly incremental updates and retries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.engine import Connection

from src.db.runs_repo import requests_used_since


def day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def month_start(now: datetime) -> datetime:
    return day_start(now).replace(day=1)


@dataclass
class RequestBudget:
    daily_limit: int
    weekly_limit: int
    monthly_limit: int
    backfill_limit: int
    used_today: int = 0
    used_week: int = 0
    used_month: int = 0
    backfill_today: int = 0
    used_this_run: int = 0
    backfill_this_run: int = 0

    @classmethod
    def from_config(cls, quota: dict, **used) -> "RequestBudget":
        margin = float(quota.get("safety_margin", 0.95))
        daily = int(quota.get("daily_requests", 250))
        return cls(
            daily_limit=math.floor(daily * margin),
            weekly_limit=math.floor(int(quota.get("weekly_requests", daily * 7)) * margin),
            monthly_limit=math.floor(int(quota.get("monthly_requests", daily * 30)) * margin),
            backfill_limit=math.floor(daily * float(quota.get("backfill_share", 0.7))),
            **used,
        )

    @classmethod
    def load(cls, conn: Connection, quota: dict, now: datetime) -> "RequestBudget":
        used_today, backfill_today = requests_used_since(conn, day_start(now))
        used_week, _ = requests_used_since(conn, now - timedelta(days=7))
        used_month, _ = requests_used_since(conn, month_start(now))
        return cls.from_config(
            quota, used_today=used_today, used_week=used_week,
            used_month=used_month, backfill_today=backfill_today,
        )

    # Totals including requests made during this run (not yet saved to fetch_runs).
    @property
    def today(self) -> int:
        return self.used_today + self.used_this_run

    def remaining(self) -> int:
        return max(0, min(
            self.daily_limit - self.today,
            self.weekly_limit - (self.used_week + self.used_this_run),
            self.monthly_limit - (self.used_month + self.used_this_run),
        ))

    def remaining_backfill(self) -> int:
        return max(0, min(self.remaining(), self.backfill_limit - (self.backfill_today + self.backfill_this_run)))

    def remaining_incremental(self) -> int:
        """Requests left today that are not reserved for backfill."""
        reserved = max(0, self.backfill_limit - (self.backfill_today + self.backfill_this_run))
        return max(0, self.remaining() - reserved)

    def incremental_allowance(self, now: datetime) -> int:
        """Spread incremental requests evenly over the hours left in the UTC day."""
        hours_left = max(1, 24 - now.hour)
        return math.ceil(self.remaining_incremental() / hours_left)

    def can_spend(self, kind: str = "incremental") -> bool:
        return (self.remaining_backfill() if kind == "backfill" else self.remaining()) > 0

    def spend(self, count: int, kind: str = "incremental") -> None:
        self.used_this_run += count
        if kind == "backfill":
            self.backfill_this_run += count
