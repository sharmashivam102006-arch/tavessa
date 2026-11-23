"""Alembic environment — Tavessa.

- URL comes from settings (DATABASE_URL), never from alembic.ini.
- target_metadata is Base.metadata; model modules must be imported in
  app/models/__init__.py for autogenerate to see them.
- No migration may backfill money columns with a guessed value
  (see docs/database-design.md § 10).
"""
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base
from app.db.session import normalise_database_url

# Import models so they register on Base.metadata (none yet in Stage 1).
import app.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = normalise_database_url(settings.database_url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = normalise_database_url(settings.database_url)
    connectable = engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
