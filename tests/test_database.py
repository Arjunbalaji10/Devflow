"""Connection lifecycle checks use doubles, never environment database URLs."""

from unittest.mock import Mock

from flask import Flask
import psycopg
import pytest

import database


# Inert fixture URL; no test connects to this hostname.
URL = "postgresql://test_user:fixture_secret@database.invalid/devflow_test?sslmode=require"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_url_fails_without_connecting(monkeypatch, value):
    connect = Mock()
    monkeypatch.setattr(database.psycopg, "connect", connect)
    with pytest.raises(database.DatabaseConfigurationError, match="DATABASE_URL is required"):
        database.connect_database(value)
    connect.assert_not_called()


@pytest.mark.parametrize("value", [
    "devflow.db", "sqlite:///devflow.db", "host=localhost dbname=test",
    "postgresql:///test", "postgresql://localhost",
    "postgresql://user:fixture_secret@localhost/test?unknown_parameter=value",
])
def test_invalid_configuration_does_not_expose_url(value):
    with pytest.raises(database.DatabaseConfigurationError) as caught:
        database.require_database_url(value)
    assert value not in str(caught.value)
    assert "fixture_secret" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_connection_options_preserve_tls_and_bound_connection_time(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(database.psycopg, "connect", connect)
    assert database.connect_database(URL) is connect.return_value
    connect.assert_called_once_with(
        URL, row_factory=database.dict_row, autocommit=True,
        connect_timeout=10, prepare_threshold=None,
    )


def test_startup_validation_never_opens_connection(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(database.psycopg, "connect", connect)
    monkeypatch.setenv("DATABASE_URL", URL)
    app = Flask(__name__)
    database.init_app(app)
    assert app.config["DATABASE_URL"] == URL
    connect.assert_not_called()


def test_startup_requires_configuration(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(database.DatabaseConfigurationError, match="DATABASE_URL is required"):
        database.init_app(Flask(__name__))


@pytest.mark.parametrize("raise_error", [False, True])
def test_request_connection_reused_and_closed_even_on_error(monkeypatch, raise_error):
    app = Flask(__name__)
    app.config["DATABASE_URL"] = URL
    database.init_app(app)
    connection = Mock()
    connect = Mock(return_value=connection)
    monkeypatch.setattr(database, "connect_database", connect)
    try:
        with app.test_request_context():
            assert database.get_db() is connection
            assert database.get_db() is connection
            connection.close.assert_not_called()
            if raise_error:
                raise RuntimeError("request failed")
    except RuntimeError:
        assert raise_error
    connect.assert_called_once_with(URL)
    connection.close.assert_called_once_with()
    with app.test_request_context():
        database.get_db()
    assert connect.call_count == 2


def test_unused_context_does_not_connect(monkeypatch):
    app = Flask(__name__)
    app.config["DATABASE_URL"] = URL
    database.init_app(app)
    connect = Mock()
    monkeypatch.setattr(database, "connect_database", connect)
    with app.app_context():
        pass
    connect.assert_not_called()


def test_failed_connection_is_not_cached_or_retried(monkeypatch):
    app = Flask(__name__)
    app.config["DATABASE_URL"] = URL
    database.init_app(app)
    connect = Mock(side_effect=psycopg.OperationalError("connection unavailable"))
    monkeypatch.setattr(database, "connect_database", connect)
    with app.app_context():
        with pytest.raises(psycopg.OperationalError):
            database.get_db()
        assert "postgres_db" not in database.g
    connect.assert_called_once_with(URL)
