"""Launcher checks never import app or open an application database."""

import os
from pathlib import Path
import runpy
import sys

import pytest
from unittest.mock import Mock
from werkzeug.security import generate_password_hash

import access
import database as postgres_database


ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD_HASH = generate_password_hash(os.urandom(32).hex())
TEST_SESSION_SECRET = os.urandom(48).hex()


def configure_access(monkeypatch):
    monkeypatch.setenv("DEVFLOW_ACCESS_PASSWORD_HASH", TEST_PASSWORD_HASH)
    monkeypatch.setenv("DEVFLOW_SESSION_SECRET", TEST_SESSION_SECRET)


@pytest.mark.parametrize("arguments", [
    ["--preload"], ["--workers", "9"], ["--threads", "9"],
    ["--config", "untrusted.py"], ["--forwarded-allow-ips=*"], ["other:app"],
])
def test_launcher_rejects_arguments(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["start_production.py", *arguments])
    with pytest.raises(SystemExit, match="accepts no arguments"):
        runpy.run_path(str(ROOT / "start_production.py"), run_name="__main__")


def test_launcher_sanitizes_environment_and_selects_fixed_config(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["start_production.py"])
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/devflow_test")
    monkeypatch.setenv("GUNICORN_CMD_ARGS", "--preload --workers 9 --threads 9 --config untrusted.py")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setenv("WEB_CONCURRENCY", "9")
    monkeypatch.setenv("FLASK_DEBUG", "1")
    configure_access(monkeypatch)
    calls = []
    monkeypatch.setattr(os, "chdir", lambda path: calls.append(path))
    monkeypatch.setattr(os, "execve", lambda *args: calls.append(args))

    runpy.run_path(str(ROOT / "start_production.py"), run_name="__main__")

    executable, arguments, environment = calls[1]
    assert calls[0] == ROOT
    assert executable == sys.executable
    assert arguments == [sys.executable, "-m", "gunicorn", "--config",
                         str(ROOT / "gunicorn.conf.py"), "app:app"]
    assert not {"GUNICORN_CMD_ARGS", "FORWARDED_ALLOW_IPS", "WEB_CONCURRENCY"} & environment.keys()
    assert environment["FLASK_DEBUG"] == "0"
    assert environment["DATABASE_URL"] == "postgresql://localhost/devflow_test"
    assert environment["DEVFLOW_ENV"] == "production"


@pytest.mark.parametrize("port", ["", "0", "65536", "-1", "abc", "10000 --preload"])
def test_launcher_rejects_invalid_port(monkeypatch, port):
    monkeypatch.setattr(sys, "argv", ["start_production.py"])
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PORT", port)
    with pytest.raises(SystemExit, match="PORT must"):
        runpy.run_path(str(ROOT / "start_production.py"), run_name="__main__")


@pytest.mark.parametrize("database", [None, "", "relative.db", "postgresql:///missing-host",
                                     "postgresql://localhost", "sqlite:///old.db",
                                     "postgresql://fixture:do-not-print@localhost/test?invalid_option=1"])
def test_launcher_requires_postgres_url(monkeypatch, database):
    monkeypatch.setattr(sys, "argv", ["start_production.py"])
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if database is not None:
        monkeypatch.setenv("DATABASE_URL", database)
    execute = Mock()
    connect = Mock()
    monkeypatch.setattr(os, "execve", execute)
    monkeypatch.setattr(postgres_database.psycopg, "connect", connect)
    with pytest.raises(SystemExit, match="DATABASE_URL") as caught:
        runpy.run_path(str(ROOT / "start_production.py"), run_name="__main__")
    assert "do-not-print" not in str(caught.value)
    execute.assert_not_called()
    connect.assert_not_called()


@pytest.mark.parametrize("missing", [
    "DEVFLOW_ACCESS_PASSWORD_HASH", "DEVFLOW_SESSION_SECRET",
])
def test_launcher_requires_access_credentials_before_gunicorn(monkeypatch, missing):
    monkeypatch.setattr(sys, "argv", ["start_production.py"])
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/devflow_test")
    configure_access(monkeypatch)
    monkeypatch.delenv(missing)
    execute = Mock()
    monkeypatch.setattr(os, "execve", execute)

    with pytest.raises(SystemExit, match=missing):
        runpy.run_path(str(ROOT / "start_production.py"), run_name="__main__")

    execute.assert_not_called()


def test_pinned_runtime_and_explicit_configuration(monkeypatch):
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setenv("WEB_CONCURRENCY", "9")
    config = runpy.run_path(str(ROOT / "gunicorn.conf.py"))
    assert (ROOT / ".python-version").read_text().strip() == "3.14.10"
    assert config["workers"] == config["threads"] == 1
    assert config["worker_class"] == "sync"
    assert config["preload_app"] is False
    assert config["reload"] is False
    assert config["forwarded_allow_ips"] == ""
    assert config["secure_scheme_headers"] == {"X-FORWARDED-PROTO": "https"}
    assert config["bind"] == "0.0.0.0:10000"
    assert config["accesslog"] == config["errorlog"] == "-"


def test_render_configuration_trusts_only_the_render_scheme_signal(monkeypatch):
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("RENDER", "true")
    config = runpy.run_path(str(ROOT / "gunicorn.conf.py"))
    assert config["forwarded_allow_ips"] == "*"
    assert config["secure_scheme_headers"] == {"X-FORWARDED-PROTO": "https"}
