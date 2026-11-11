"""Safety checks for the real-server harness and its PostgreSQL isolation."""

import os
from pathlib import Path
import runpy
import sys
from unittest.mock import Mock

import pytest
from psycopg.conninfo import conninfo_to_dict

from database import connect_database
import postgres_support
from postgres_support import isolated_postgres, local_test_url
from verify_production import server_environment, verify_migrations


@pytest.mark.parametrize("value", [None, "", "sqlite:///old.db",
    "postgresql://remote.invalid/devflow_test", "postgresql://127.0.0.1/production",
    "postgresql://127.0.0.1/devflow_test?hostaddr=192.0.2.1",
    "postgresql://127.0.0.1/devflow_test?service=external"])
def test_verification_refuses_unsafe_database_before_connecting(monkeypatch, value):
    connect = Mock()
    monkeypatch.setattr(postgres_support, "connect_database", connect)
    with pytest.raises(ValueError, match="TEST_DATABASE_URL"):
        with isolated_postgres(value):
            pytest.fail("Unsafe database accepted")
    connect.assert_not_called()


def test_loopback_address_overrides_inherited_hostaddr(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    settings = conninfo_to_dict(local_test_url("postgresql://localhost/devflow_test"))
    assert settings["hostaddr"] == "127.0.0.1"


def test_subprocess_environment_never_uses_inherited_database_urls(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "inherited production URL")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "inherited migration URL")
    monkeypatch.setenv("DEVFLOW_DATABASE", "old.db")
    monkeypatch.setenv("DEVFLOW_ACCESS_PASSWORD_HASH", "inherited password hash")
    monkeypatch.setenv("DEVFLOW_SESSION_SECRET", "inherited session secret")
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    environment = server_environment("postgresql://localhost/devflow_test", 12345)
    assert environment["DATABASE_URL"] == "postgresql://localhost/devflow_test"
    assert environment["PORT"] == "12345"
    assert not {"MIGRATION_DATABASE_URL", "DEVFLOW_DATABASE", "PGHOSTADDR"} & environment.keys()
    assert environment["DEVFLOW_ACCESS_PASSWORD_HASH"] != "inherited password hash"
    assert environment["DEVFLOW_SESSION_SECRET"] != "inherited session secret"


def test_exact_runtime_gate_rejects_other_patch_before_connecting(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "version_info", (3, 14, 4))
    monkeypatch.setattr(sys, "argv", ["verify_production.py", "--require-pinned-runtime"])
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/openssl")
    connect = Mock()
    monkeypatch.setattr(postgres_support, "connect_database", connect)
    with pytest.raises(SystemExit, match="Exact Python 3.14.10"):
        runpy.run_path(str(Path(__file__).with_name("verify_production.py")), run_name="__main__")
    connect.assert_not_called()


def test_real_migration_command_and_rollback(postgres_url, tmp_path):
    verify_migrations(tmp_path, postgres_url)


@pytest.mark.parametrize("fail_inside", [False, True])
def test_isolation_cleanup_preserves_existing_schema(postgres_url, fail_inside):
    with connect_database(postgres_url) as observer:
        before = observer.execute("SELECT schema_name FROM information_schema.schemata ORDER BY schema_name").fetchall()
        try:
            with isolated_postgres(postgres_url) as url:
                with connect_database(url) as connection:
                    connection.execute("CREATE TABLE disposable_probe (id INTEGER)")
                if fail_inside:
                    raise RuntimeError("test failure")
        except RuntimeError:
            assert fail_inside
        after = observer.execute("SELECT schema_name FROM information_schema.schemata ORDER BY schema_name").fetchall()
        assert after == before
