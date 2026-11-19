"""Apply Alembic migrations programmatically.

    python -m src.db.migrate          # upgrade the DATABASE_URL database to the latest schema
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from src.db.engine import PROJECT_ROOT, get_engine

ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


def upgrade(engine: Engine | None = None) -> None:
    """Bring the database up to the latest migration (safe to call repeatedly)."""
    engine = engine or get_engine()
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


if __name__ == "__main__":
    upgrade()
    print("Database schema is up to date.")
