"""Alembic environment for OpsPilot.

Reads ``DATABASE_URL`` from ``opspilot.settings`` and points ``target_metadata``
at the persistence models' metadata, so autogenerate sees the real schema.

This file is *configuration*, not application logic, and it is omitted from the
coverage number (see ``pyproject.toml``). It runs both offline (``--sql``) and
online; the online path does not force a single connection, so a migration may
manage its own transactions -- which matters for the initial migration's
``CREATE EXTENSION IF NOT EXISTS vector`` (see ``docs/data-model.md`` §5).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from opspilot.settings import get_settings

# Alembic's Config object provides access to the values in alembic.ini.
config = context.config

# Python logging is configured only when a config file is present (it is not
# when env.py is invoked programmatically by a test).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The database URL has one source of truth: `opspilot.settings`, which reads the
# environment (and .env). `%` is escaped because configparser interpolates.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

# target_metadata is what autogenerate diffs the database against. The models
# module exposes it so the import lives in one place; M0 declares no tables yet,
# so this is wired now and starts producing diffs in M1.
try:
    from opspilot.adapters.persistence.models import get_metadata

    target_metadata = get_metadata()
except (ImportError, NotImplementedError):
    # M0: the declarative models are not declared yet. `alembic upgrade head`
    # still works for the (empty) baseline; autogenerate has nothing to diff.
    target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout, no DBAPI)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
