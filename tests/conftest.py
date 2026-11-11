"""PostgreSQL tests require an explicitly selected local test database.

Each test owns a random schema; cleanup never touches public or existing tables.
No test reads DATABASE_URL to choose a server.
"""

import os
import importlib

import psycopg
from psycopg.rows import tuple_row
import pytest

from database import connect_database
from migrate import migrate
from postgres_support import isolated_postgres


@pytest.fixture
def postgres_url():
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is required for real local PostgreSQL tests")
    try:
        with isolated_postgres(value) as isolated:
            yield isolated
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)


@pytest.fixture
def migrated_url(postgres_url):
    migrate(postgres_url)
    return postgres_url


@pytest.fixture
def test_context(migrated_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", migrated_url)

    def postgres_connect(url):
        connection = connect_database(url)
        connection.row_factory = tuple_row
        return connection

    app_module = importlib.import_module("app")
    monkeypatch.setitem(app_module.app.config, "TESTING", True)
    monkeypatch.setitem(app_module.app.config, "DATABASE_URL", migrated_url)
    monkeypatch.setitem(app_module.app.config, "RATE_LIMIT_ENABLED", False)
    # Explicit fixture records only; application startup and migrations never seed.
    with postgres_connect(migrated_url) as connection:
        with connection.transaction():
            for name, status in [("Learn Python", "completed"),
                                 ("Understand APIs", "pending"), ("Learn SQL", "pending")]:
                connection.execute("INSERT INTO tasks (name, status) VALUES (%s, %s)", (name, status))
    yield app_module.app.test_client(), migrated_url, postgres_connect
