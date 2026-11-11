"""PostgreSQL connections shared by Flask requests and explicit database tools.

Importing this module does not connect, create tables, or seed data.
Call init_app() to validate configuration and register Flask teardown.
"""

import os

from flask import current_app, g
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


class DatabaseConfigurationError(RuntimeError):
    """Invalid database configuration, reported without connection secrets."""


def require_database_url(value):
    """Accept an explicit PostgreSQL URL only; never use libpq/SQLite defaults."""
    if not isinstance(value, str) or not value.strip():
        raise DatabaseConfigurationError("DATABASE_URL is required")
    if not value.startswith(("postgresql://", "postgres://")):
        raise DatabaseConfigurationError("DATABASE_URL must be a PostgreSQL URL")
    try:
        settings = conninfo_to_dict(value)
    except psycopg.Error:
        raise DatabaseConfigurationError("DATABASE_URL is invalid") from None
    if not settings.get("host") or not settings.get("dbname"):
        raise DatabaseConfigurationError("DATABASE_URL must specify a host and database")
    return value


def connect_database(database_url):
    """Open a bounded-time connection; callers own and close it.

    Reads use autocommit to avoid idle transactions. Mutations must use
    `with connection.transaction():` so multi-statement writes remain atomic.
    URL TLS options are retained. Disable automatic prepared statements for
    compatibility with transaction-pooling proxies.
    """
    return psycopg.connect(
        require_database_url(database_url),
        row_factory=dict_row,
        autocommit=True,
        connect_timeout=10,
        prepare_threshold=None,
    )


def get_db():
    """Reuse one PostgreSQL connection within the current Flask context."""
    if "postgres_db" not in g:
        g.postgres_db = connect_database(current_app.config["DATABASE_URL"])
    return g.postgres_db


def close_db(error=None):
    connection = g.pop("postgres_db", None)
    if connection is not None:
        connection.close()


def init_app(app):
    """Validate configuration at startup without opening a database connection."""
    app.config["DATABASE_URL"] = require_database_url(
        app.config.get("DATABASE_URL", os.environ.get("DATABASE_URL"))
    )
    app.teardown_appcontext(close_db)
