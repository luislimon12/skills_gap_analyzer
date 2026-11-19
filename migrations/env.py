"""Alembic environment. The database URL comes from DATABASE_URL (see src/db/engine.py)."""

from alembic import context

from src.db.engine import make_engine
from src.db.schema import metadata

config = context.config
target_metadata = metadata


def _url() -> str | None:
    # A URL passed programmatically (src/db/migrate.py, tests) wins over the environment.
    return config.attributes.get("database_url")


def run_migrations_offline() -> None:
    from src.db.engine import database_url

    context.configure(
        url=database_url(_url()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = make_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
