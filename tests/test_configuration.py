"""Startup configuration checks never connect to environment databases."""

from pathlib import Path
import runpy
import socket
from unittest.mock import Mock

from flask import Flask
import psycopg
import pytest

import access
import database


SOURCE = Path(__file__).resolve().parents[1] / "app.py"


@pytest.mark.parametrize("value", [None, "", "   ", "sqlite:///devflow.db"])
def test_startup_rejects_missing_or_sqlite_configuration(monkeypatch, value):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DEVFLOW_DATABASE", "unused.db")
    if value is not None:
        monkeypatch.setenv("DATABASE_URL", value)
    with pytest.raises(database.DatabaseConfigurationError, match="DATABASE_URL"):
        runpy.run_path(str(SOURCE))


def test_import_does_not_connect_create_or_seed(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/devflow_test")
    connect = Mock(side_effect=AssertionError("Import must not connect"))
    monkeypatch.setattr(database.psycopg, "connect", connect)
    loaded = runpy.run_path(str(SOURCE))
    app = loaded["app"]
    assert not app.debug
    root_response = app.test_client().get("/")
    assert root_response.status_code == 302
    assert root_response.headers["Location"].endswith("/dashboard")
    assert app.test_client().get("/dashboard").status_code == 200
    assert "init_db" not in loaded
    connect.assert_not_called()


def test_render_import_fails_closed_without_access_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/devflow_test")
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("DEVFLOW_ACCESS_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("DEVFLOW_SESSION_SECRET", raising=False)
    connect = Mock(side_effect=AssertionError("Configuration must fail before connecting"))
    monkeypatch.setattr(database.psycopg, "connect", connect)

    with pytest.raises(access.AccessConfigurationError, match="PASSWORD_HASH"):
        runpy.run_path(str(SOURCE))

    connect.assert_not_called()


def test_direct_startup_does_not_force_debug(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/devflow_test")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    calls = []
    monkeypatch.setattr(Flask, "run", lambda app, **kwargs: calls.append((app.debug, kwargs)))
    runpy.run_path(str(SOURCE), run_name="__main__")
    assert calls == [(False, {})]


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/tasks", None), ("POST", "/api/tasks", {"name": "New"}),
    ("PATCH", "/api/tasks/1", {}), ("DELETE", "/api/tasks/1", None),
])
def test_connection_failure_returns_generic_json(monkeypatch, method, path, payload):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/devflow_test")
    app = runpy.run_path(str(SOURCE))["app"]
    monkeypatch.setattr(database, "connect_database", Mock(
        side_effect=psycopg.OperationalError("private connection details")))
    response = app.test_client().open(path, method=method, json=payload)
    assert response.status_code == 500
    assert response.get_json() == {"error": "Database operation failed"}
    assert b"private" not in response.data


def test_real_refused_local_connection_returns_generic_json(monkeypatch):
    # Reserve a local port without listening; this cannot reach any service.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        monkeypatch.setenv("DATABASE_URL", f"postgresql://127.0.0.1:{port}/devflow_test?sslmode=disable")
        app = runpy.run_path(str(SOURCE))["app"]
        response = app.test_client().get("/api/tasks")
    assert response.status_code == 500
    assert response.get_json() == {"error": "Database operation failed"}
