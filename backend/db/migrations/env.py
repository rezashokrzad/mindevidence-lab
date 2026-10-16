from alembic import context
from sqlalchemy import create_engine, pool

from app.config import database_url

config = context.config

# Precedence: explicit sqlalchemy.url (set by tests) > -x url_env=VAR > DATABASE_URL.
url = config.get_main_option("sqlalchemy.url") or database_url(
    context.get_x_argument(as_dictionary=True).get("url_env", "DATABASE_URL")
)


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
