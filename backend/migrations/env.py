import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Import our models so Alembic can see the metadata
from logsaga.models import Base

# Alembic Config object
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_db_url() -> str:
    """Return the SQLite URL.

    Priority:
    1. LOGSAGA_DB env var (full SQLAlchemy URL, e.g. sqlite:///C:/path/db)
    2. LOGSAGA_DB_PATH env var (bare file path — matches what main.py uses)
    3. alembic.ini sqlalchemy.url
    """
    env_url = os.environ.get("LOGSAGA_DB")
    if env_url:
        return env_url
    db_path = os.environ.get("LOGSAGA_DB_PATH")
    if db_path:
        return f"sqlite:///{db_path}"
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection required)."""
    url = _get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER TABLE support
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (live DB connection)."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_db_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER TABLE support
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
