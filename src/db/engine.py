"""Database connection setup.

``DATABASE_URL`` selects the database:
  * empty / unset  -> local SQLite file from ``database_path`` in config/settings.yaml
                      (default data/skills_gap.db), shared by the worker and the dashboard
  * postgresql+psycopg://user:pass@127.0.0.1:5432/skills_gap  (production on the VM)

The URL is never logged, since it can contain the database password.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "skills_gap.db"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def sqlite_path(settings_path: Path | None = None) -> Path:
    """The local SQLite file: ``database_path`` from config/settings.yaml.

    The worker and the dashboard both use this, so they always share one file.
    """
    configured = None
    try:
        with open(settings_path or SETTINGS_PATH, encoding="utf-8") as handle:
            configured = (yaml.safe_load(handle) or {}).get("database_path")
    except OSError:
        pass
    if not isinstance(configured, str) or not configured.strip():
        return DEFAULT_SQLITE_PATH
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


def database_url(url: str | None = None) -> str:
    url = url or os.getenv("DATABASE_URL") or ""
    if not url:
        path = sqlite_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.as_posix()}"
    # Accept the plain "postgresql://" form and use the psycopg 3 driver.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    return url


def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(url: str | None = None) -> Engine:
    url = database_url(url)
    if url.startswith("sqlite"):
        engine = create_engine(url, future=True, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        return engine
    return create_engine(url, future=True, pool_pre_ping=True)


@lru_cache(maxsize=8)
def get_engine(url: str | None = None) -> Engine:
    """Cached engine per URL (one connection pool per process)."""
    return make_engine(url)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC so comparisons work."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
