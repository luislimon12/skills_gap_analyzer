"""Shared fixtures.

* Every test runs offline: real HTTP requests raise, and API keys / SMTP settings
  are removed from the environment, so no test can call Adzuna or send email.
* ``engine`` is a fresh, migrated database per test: SQLite by default, or the
  PostgreSQL server in TEST_DATABASE_URL (the cloud CI job uses this to mimic the
  Oracle VM setup).
"""

import os

import pytest
import requests

from src.db.engine import make_engine
from src.db.migrate import upgrade

_SCRUBBED_ENV = (
    "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "LOOPCV_API_KEY", "DATABASE_URL", "ENCRYPTION_KEY",
    "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM",
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise RuntimeError("Network access is disabled in tests")

    monkeypatch.setattr(requests.sessions.Session, "send", blocked)
    for name in _SCRUBBED_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def engine(tmp_path):
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        engine = make_engine(url)
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA public CASCADE")
            conn.exec_driver_sql("CREATE SCHEMA public")
    else:
        engine = make_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    upgrade(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def engine_url(engine):
    """The engine's URL including the password (for code that reads DATABASE_URL)."""
    return engine.url.render_as_string(hide_password=False)
